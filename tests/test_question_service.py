"""Exercise the question use case independently of FastAPI and its lifespan."""

import json
from unittest.mock import Mock

import pytest
from httpx import ReadTimeout

from app.domain.errors import InferenceTimeout, PolicyUnavailable
from app.domain.questions import QuestionResult, QuestionType
from app.services.questions import QuestionService
from app.services.responses import ANSWERS
from app.tools.pdf_search import PdfLookupError


def test_fixed_response_requires_no_http_or_pdf_resources():
    model = Mock(return_value='{"question_type":"out_of_scope"}')
    tool = Mock()
    result = QuestionService(model, tool).answer("Qual a capital dos EUA?")
    assert result == QuestionResult(ANSWERS[QuestionType.OUT_OF_SCOPE],
                                    QuestionType.OUT_OF_SCOPE, False)
    tool.assert_not_called()


def test_mixed_question_passes_tool_evidence_and_preserves_database_notice():
    model = Mock(side_effect=['{"question_type":"database_and_pdf"}', "Política (página 4)."])
    evidence = {"excerpts": [{"page": 4, "text": "Regras de devolução"}]}
    tool = Mock(return_value=evidence)
    result = QuestionService(model, tool).answer("  Preço e devolução?  ")
    tool.assert_called_once_with("Preço e devolução?")
    assert json.loads(model.call_args.args[0])["pdf_tool_result"] == evidence
    assert result.answer == "Política (página 4).\n\n" + ANSWERS[QuestionType.DATABASE]
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
