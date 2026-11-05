"""Exercise the question use case independently of FastAPI and its lifespan."""

import json
import os
from unittest.mock import Mock

import pytest
from httpx import ReadTimeout
from ollama import Client

from app.domain.errors import InferenceTimeout, PolicyUnavailable
from app.domain.questions import QuestionResult, QuestionType
from app.services.questions import QuestionService
from app.services.classification import classify_question
from app.services.responses import ANSWERS
from app.tools.pdf_search import PdfLookupError
from app.domain.errors import InferenceFailure


def test_fixed_response_requires_no_http_or_pdf_resources():
    model = Mock(return_value='{"question_type":"out_of_scope"}')
    tool = Mock()
    database = Mock(return_value={"rows": [{"price_brl": 599.9}]})
    result = QuestionService(model, tool, database).answer(
        "Qual a capital dos EUA?"
    )
    assert result == QuestionResult(ANSWERS[QuestionType.OUT_OF_SCOPE],
                                    QuestionType.OUT_OF_SCOPE, False)
    tool.assert_not_called()


def test_mixed_question_passes_both_tool_results():
    model = Mock(side_effect=[
        '{"question_type":"database_and_pdf"}',
        "Database result.",
        json.dumps({"answer": "Política (página 4).", "source_pages": [4]}),
    ])
    evidence = {"excerpts": [{"page": 4, "text": "Regras de devolução"}]}
    tool = Mock(return_value=evidence)
    database = Mock(return_value={"rows": [{"price_brl": 599.9}]})
    result = QuestionService(model, tool, database).answer("  Preço e devolução?  ")
    tool.assert_called_once_with("Preço e devolução?")
    assert json.loads(model.call_args.args[0])["pdf_tool_result"] == evidence
    payload = json.loads(model.call_args.args[0])
    database_payload = json.loads(model.call_args_list[-2].args[0])
    assert database_payload["database_tool_result"] == database.return_value
    assert "database_tool_result" not in payload
    database.assert_called_once()
    assert "599.9" in model.call_args_list[-2].args[0]
    assert result.requires_lookup


def test_pdf_failure_exposes_application_error_and_preserves_cause():
    cause = PdfLookupError("missing document")
    service = QuestionService(Mock(return_value='{"question_type":"pdf"}'),
                              Mock(side_effect=cause))
    with pytest.raises(PolicyUnavailable) as captured:
        service.answer("Horário?")
    assert captured.value.__cause__ is cause


def test_model_timeout_exposes_application_error_without_http_types():
    cause = ReadTimeout("slow model")
    service = QuestionService(Mock(side_effect=cause))
    with pytest.raises(InferenceTimeout) as captured:
        service.answer("Horário?")
    assert captured.value.__cause__ is cause


def test_invalid_plans_are_not_cached(monkeypatch):
    model = Mock(side_effect=[
        '{"question_type":"database"}',
        '{"entity":"products","filters":[{"field":"invalid","value":1}]}',
        '{"entity":"products","filters":[{"field":"invalid","value":1}]}',
        '{"entity":"unsupported"}',
        "Unsupported query.",
    ])
    service = QuestionService(model)
    with pytest.raises(InferenceFailure):
        service.answer("Unsupported question")
    result = service.answer("Unsupported question")
    assert result.answer == "Unsupported query."
    # Classification was reused; the failed plan was generated again.
    assert model.call_count == 5


@pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_RAG_TESTS") != "1",
    reason="Requires running Ollama and prepared llama3.2:3b weights",
)
@pytest.mark.parametrize("question, expected", [
    ("Vocês vendem cordas?", QuestionType.ACCESSORY),
    ("Vocês têm violão de sete cordas?", QuestionType.DATABASE),
    ("Meu pedido 3 já foi enviado? Qual o código de rastreamento?",
     QuestionType.DATABASE),
    ("Quanto custa a Kalani KAL-700T?", QuestionType.DATABASE),
    ("Quanto custa o Yamaha C40?", QuestionType.DATABASE),
    ("Posso devolver um produto comprado online?", QuestionType.PDF),
    ("Posso devolver o Yamaha C40?", QuestionType.PDF),
    ("Quanto custa o Yamaha C40 e posso devolvê-lo?",
     QuestionType.DATABASE_AND_PDF),
])
def test_live_return_question_routing(question, expected):
    with Client(host="http://127.0.0.1:11434", timeout=120,
                trust_env=False) as client:
        def generate(message, prompt, *, response_format=None):
            result = client.chat(
                model="llama3.2:3b",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": message},
                ],
                format=response_format,
                options={"num_ctx": 2048, "num_predict": 256,
                         "temperature": 0},
                keep_alive=-1,
            )
            return result.message.content

        assert classify_question(question, generate) == expected
