"""Test the FastAPI interface without contacting a real language model."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, ReadTimeout
from app.services.classification import CLASSIFICATION_SCHEMA, CLASSIFICATION_PROMPT
from app.services.responses import ANSWERS
from app.domain.questions import QuestionType
from app.tools.pdf_search import PdfLookupError
from ollama import ResponseError
from app.api.dependencies import get_question_service
from app.domain.questions import QuestionResult


api = importlib.import_module("app.app")
lifecycle = importlib.import_module("app.api.lifespan")
question_service = importlib.import_module("app.services.questions")


async def test_http_interface_accepts_service_without_initializing_model():
    service = MagicMock()
    service.answer.return_value = QuestionResult("Olá!", QuestionType.GREETING, False)
    api.app.dependency_overrides[get_question_service] = lambda: service
    try:
        async with AsyncClient(transport=ASGITransport(app=api.app),
                               base_url="http://testserver") as connection:
            response = await connection.post("/questions", params={"question": "Oi"})
        assert response.status_code == 200
        assert response.json() == {
            "answer": "Olá!", "question_type": "greeting", "requires_lookup": False,
        }
        service.answer.assert_called_once_with("Oi")
    finally:
        del api.app.dependency_overrides[get_question_service]


@pytest.fixture
def runtime(monkeypatch):
    initialize = MagicMock()
    generate = initialize.return_value.__enter__.return_value
    generate.return_value = json.dumps({"question_type": "greeting"})
    monkeypatch.setattr(lifecycle, "configure_logging", lambda: None)
    monkeypatch.setattr(lifecycle, "create_database", MagicMock())
    monkeypatch.setattr(lifecycle, "initialize_llm", initialize)
    pdf = MagicMock()
    monkeypatch.setattr(lifecycle, "pdf_resources", pdf)
    return SimpleNamespace(initialize=initialize, generate=generate, pdf=pdf)


@pytest_asyncio.fixture
async def client(runtime):
    async with api.app.router.lifespan_context(api.app):
        async with AsyncClient(
            transport=ASGITransport(app=api.app), base_url="http://testserver"
        ) as connection:
            yield connection


async def test_two_questions_reuse_one_llm(runtime):
    async with api.app.router.lifespan_context(api.app):
        async with AsyncClient(
            transport=ASGITransport(app=api.app), base_url="http://testserver"
        ) as connection:
            for _ in range(2):
                response = await connection.post(
                    "/questions", params={"question": " Quais produtos vocês vendem? "}
                )
                assert response.status_code == 200
                assert response.json() == {
                    "answer": ANSWERS[QuestionType.GREETING],
                    "question_type": "greeting", "requires_lookup": False,
                }
            runtime.initialize.assert_called_once()
            runtime.initialize.return_value.__exit__.assert_not_called()
            runtime.pdf.return_value.__enter__.assert_called_once()
            runtime.pdf.return_value.__exit__.assert_not_called()
    assert runtime.generate.call_count == 1
    runtime.generate.assert_called_with(
        "Quais produtos vocês vendem?", CLASSIFICATION_PROMPT,
        response_format=CLASSIFICATION_SCHEMA,
    )
    runtime.initialize.return_value.__exit__.assert_called_once()
    runtime.pdf.return_value.__exit__.assert_called_once()
    assert not hasattr(api.app.state, "question_service")


async def test_pdf_startup_failure_releases_llm_and_prevents_serving(runtime):
    runtime.pdf.return_value.__enter__.side_effect = PdfLookupError("cannot build index")
    with pytest.raises(PdfLookupError, match="cannot build index"):
        async with api.app.router.lifespan_context(api.app):
            pytest.fail("API must not serve without its prepared index")
    runtime.initialize.return_value.__exit__.assert_called_once()
    assert not hasattr(api.app.state, "question_service")


@pytest.mark.parametrize(
    "parameters", [{}, {"question": ""}, {"question": " "}]
)
async def test_invalid_questions_never_call_model(client, runtime, parameters):
    response = await client.post("/questions", params=parameters)
    assert response.status_code == 422
    runtime.generate.assert_not_called()


@pytest.mark.parametrize("error, status", [
    (ConnectionError("offline"), 503),
    (ReadTimeout("timeout"), 504),
    (RuntimeError("model failed"), 502),
    (ResponseError("failed", status_code=500), 502),
])
async def test_inference_errors_return_expected_status(client, runtime,
                                                     error, status):
    runtime.generate.side_effect = error
    response = await client.post("/questions", params={"question": "Hello"})
    assert response.status_code == status


def test_swagger_uses_query_parameter():
    schema = api.app.openapi()
    assert set(schema["paths"]) == {"/questions"}
    operation = schema["paths"]["/questions"]["post"]
    assert "requestBody" not in operation
    parameter = operation["parameters"][0]
    assert parameter["name"] == "question"
    assert parameter["in"] == "query"
    assert parameter["required"] is True
    assert api.app.docs_url == "/docs"


async def test_initialization_failure_prevents_serving(runtime):
    runtime.initialize.return_value.__enter__.side_effect = RuntimeError(
        "Model is not installed"
    )
    with pytest.raises(RuntimeError, match="Model is not installed"):
        async with api.app.router.lifespan_context(api.app):
            pass
    runtime.generate.assert_not_called()


@pytest.mark.parametrize("label, lookup", [
    ("greeting", False), ("out_of_scope", False),
])
async def test_routes_return_only_fixed_answers(client, runtime, label, lookup):
    runtime.generate.return_value = json.dumps({"question_type": label})
    response = await client.post("/questions", params={"question": "Mensagem"})
    assert response.status_code == 200
    assert response.json() == {
        "answer": ANSWERS[QuestionType(label)],
        "question_type": label, "requires_lookup": lookup,
    }
    runtime.generate.assert_called_once_with(
        "Mensagem", CLASSIFICATION_PROMPT, response_format=CLASSIFICATION_SCHEMA
    )


async def test_invalid_classification_never_becomes_an_answer(client, runtime):
    runtime.generate.return_value = "Invented product information"
    response = await client.post("/questions", params={"question": "Produtos?"})
    assert response.status_code == 502
    assert "Invented" not in response.text


async def test_capital_question_returns_standard_refusal(client, runtime):
    runtime.generate.return_value = '{"question_type":"out_of_scope"}'
    response = await client.post(
        "/questions", params={"question": "Qual é a capital dos Estados Unidos?"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "answer": ANSWERS[QuestionType.OUT_OF_SCOPE],
        "question_type": "out_of_scope", "requires_lookup": False,
    }


@pytest.mark.parametrize("label", ["pdf", "database_and_pdf"])
async def test_pdf_tool_result_is_used_for_final_answer(client, runtime, monkeypatch, label):
    evidence = {"source": "policies.pdf", "excerpts": [{"page": 2, "text": "09:00 às 18:00"}], "found": True}
    tool = MagicMock(return_value=evidence)
    monkeypatch.setattr(api.app.state.question_service, "_pdf_lookup", tool)
    monkeypatch.setattr(api.app.state.question_service, "_database_lookup",
                        MagicMock(return_value={"rows": [{"price_brl": 599.9}]}))
    runtime.generate.side_effect = [json.dumps({"question_type": label}), "Das 09:00 às 18:00 (página 2)."]
    response = await client.post("/questions", params={"question": "Qual o horário?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"].startswith("Das 09:00 às 18:00 (página 2).")
    assert body["question_type"] == label
    assert body["requires_lookup"] is True

    tool.assert_called_once_with("Qual o horário?")
    prompt, system = runtime.generate.call_args.args
    assert json.loads(prompt)["pdf_tool_result"] == evidence
    expected_prompt = (question_service.MIXED_ANSWER_PROMPT
                       if label == "database_and_pdf"
                       else question_service.PDF_ANSWER_PROMPT)
    assert system == expected_prompt
    assert runtime.generate.call_count == 2


async def test_pdf_failure_returns_503_without_fabricating_answer(client, runtime, monkeypatch):
    runtime.generate.return_value = '{"question_type":"pdf"}'
    monkeypatch.setattr(api.app.state.question_service, "_pdf_lookup", MagicMock(side_effect=PdfLookupError("missing")))
    response = await client.post("/questions", params={"question": "Horários?"})
    assert response.status_code == 503
    assert runtime.generate.call_count == 1


@pytest.mark.parametrize("label", ["greeting", "out_of_scope"])
async def test_other_routes_never_consult_pdf(client, runtime, monkeypatch, label):
    tool = MagicMock()
    monkeypatch.setattr(api.app.state.question_service, "_pdf_lookup", tool)
    runtime.generate.return_value = json.dumps({"question_type": label})
    assert (await client.post("/questions", params={"question": "Mensagem"})).status_code == 200
    tool.assert_not_called()


async def test_pdf_answer_timeout_preserves_error_mapping(client, runtime, monkeypatch):
    monkeypatch.setattr(api.app.state.question_service, "_pdf_lookup", MagicMock(return_value={"excerpts": [], "found": False}))
    runtime.generate.side_effect = ['{"question_type":"pdf"}', ReadTimeout("timeout")]
    response = await client.post("/questions", params={"question": "Horário?"})
    assert response.status_code == 504
