"""Test the FastAPI interface without contacting a real language model."""

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, ReadTimeout


api = importlib.import_module("app.app")


@pytest.fixture
def runtime(monkeypatch):
    initialize = MagicMock()
    generate = initialize.return_value.__enter__.return_value
    generate.return_value = "Washington, D.C."
    monkeypatch.setattr(api, "configure_logging", lambda: None)
    monkeypatch.setattr(api, "initialize_llm", initialize)
    return SimpleNamespace(initialize=initialize, generate=generate)


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
                    "/questions", params={"question": " What is the capital? "}
                )
                assert response.status_code == 200
                assert response.json() == {"answer": "Washington, D.C."}
            runtime.initialize.assert_called_once()
            runtime.initialize.return_value.__exit__.assert_not_called()
    assert runtime.generate.call_count == 2
    runtime.generate.assert_called_with("What is the capital?")
    runtime.initialize.return_value.__exit__.assert_called_once()
    assert not hasattr(api.app.state, "generate_response")


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
    (api.ResponseError("failed", status_code=500), 502),
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

