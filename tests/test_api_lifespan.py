"""Verify startup imports the store before exposing the question endpoint."""

import importlib
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.questions import QuestionType
from app.infrastructure.database.sqlite import create_database
from app.services.questions import QuestionService
from app.tools.database import DatabaseLookupError


api = importlib.import_module("app.app")
lifecycle = importlib.import_module("app.api.lifespan")
services = importlib.import_module("app.services.questions")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    target = tmp_path / "store.db"
    monkeypatch.setenv("STORE_DATABASE_PATH", str(target))
    monkeypatch.setattr(lifecycle, "configure_logging", lambda: None)
    monkeypatch.setattr(lifecycle, "pdf_resources", MagicMock())
    initialize = MagicMock()
    generate = MagicMock()

    def ready_model():
        # Fail if the LLM is initialized before the SQLite import completes.
        assert target.is_file()
        with closing(sqlite3.connect(target)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM products"
            ).fetchone() == (65,)
        return generate

    initialize.return_value.__enter__.side_effect = ready_model
    monkeypatch.setattr(lifecycle, "initialize_llm", initialize)
    return SimpleNamespace(
        path=target, initialize=initialize, generate=generate
    )


async def test_startup_imports_once_per_run_without_duplicates(runtime):
    for _ in range(2):
        async with api.app.router.lifespan_context(api.app):
            assert isinstance(api.app.state.question_service, QuestionService)
            with closing(sqlite3.connect(runtime.path)) as connection:
                assert connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall() == []
                assert connection.execute(
                    "SELECT COUNT(*) FROM order_items"
                ).fetchone() == (22,)
    assert runtime.initialize.call_count == 2


async def test_import_failure_prevents_model_startup(runtime, monkeypatch):
    def missing_csv(**kwargs):
        return create_database(runtime.path.parent / "missing", runtime.path)

    monkeypatch.setattr(lifecycle, "create_database", missing_csv)
    with pytest.raises(FileNotFoundError):
        async with api.app.router.lifespan_context(api.app):
            pytest.fail("Requests must not be accepted after import failure")
    runtime.initialize.assert_not_called()
    assert not hasattr(api.app.state, "question_service")


@pytest.mark.parametrize("kind", ["database", "database_and_pdf"])
async def test_first_request_queries_imported_database(runtime, monkeypatch,
                                                     kind):
    pdf = MagicMock(return_value={
        "found": True, "excerpts": [{"page": 2, "text": "Return policy"}],
    })
    monkeypatch.setattr(services, "consult_pdf", pdf)
    runtime.generate.side_effect = [
        json.dumps({"question_type": kind}),
        json.dumps({"entity": "products", "filters": [
            {"field": "product_id", "value": 81},
        ]}),
        "Yamaha C40: BRL 599.90.",
    ]
    if kind == "database_and_pdf":
        runtime.generate.side_effect = [
            json.dumps({"question_type": kind}),
            json.dumps({"entity": "products", "filters": [
                {"field": "product_id", "value": 81},
            ]}),
            "Yamaha C40: BRL 599.90.",
            json.dumps({"answer": "Return policy (page 2).", "source_pages": [2]}),
        ]
    async with api.app.router.lifespan_context(api.app):
        async with AsyncClient(transport=ASGITransport(app=api.app),
                               base_url="http://testserver") as connection:
            response = await connection.post(
                "/questions", params={"question": "Price of product 81?"}
            )
    assert response.status_code == 200
    body = response.json()
    assert body["question_type"] == kind
    assert body["requires_lookup"] is True
    if kind == "database_and_pdf":
        assert body["answer"] == (
            "Yamaha C40: BRL 599.90.\n\nReturn policy (page 2)."
        )
        evidence = json.loads(runtime.generate.call_args.args[0])
        row = json.loads(runtime.generate.call_args_list[-2].args[0])[
            "database_tool_result"]["rows"][0]
        assert row["price_brl"] == 599.9
        assert evidence["pdf_tool_result"] == pdf.return_value
        pdf.assert_called_once()
        assert runtime.generate.call_count == 4
    else:
        assert "Yamaha C40" in body["answer"]
        assert "599.90" in body["answer"]
        pdf.assert_not_called()
        assert runtime.generate.call_count == 3



async def test_database_failure_returns_503(runtime):
    runtime.generate.return_value = '{"question_type":"database"}'
    async with api.app.router.lifespan_context(api.app):
        service = api.app.state.question_service
        service._database_lookup = MagicMock(
            side_effect=DatabaseLookupError("unavailable")
        )
        async with AsyncClient(transport=ASGITransport(app=api.app),
                               base_url="http://testserver") as connection:
            response = await connection.post(
                "/questions", params={"question": "Product 81?"}
            )
    assert response.status_code == 503
    assert response.json() == {"detail": "Store database is unavailable"}


@pytest.mark.parametrize("operation", ["overview", "list"])
async def test_catalog_question_passes_complete_category_evidence(runtime,
                                                                operation):
    runtime.generate.side_effect = [
        '{"question_type":"database"}',
        json.dumps({"entity": "products", "operation": operation, "limit": 5}),
        "The catalog spans six product categories.",
    ]
    async with api.app.router.lifespan_context(api.app):
        async with AsyncClient(transport=ASGITransport(app=api.app),
                               base_url="http://testserver") as connection:
            response = await connection.post(
                "/questions", params={"question": "What products do you sell?"}
            )
    assert response.status_code == 200
    assert response.json()["answer"].startswith(
        "The catalog spans six product categories."
    )
    assert ("seleção parcial" in response.json()["answer"]) == (operation == "list")
    payload = json.loads(runtime.generate.call_args.args[0])
    overview = payload["database_tool_result"]["catalog_overview"]
    assert overview["total_products"] == 65
    assert overview["total_categories"] == 6
    assert runtime.generate.call_count == 3




async def test_price_question_receives_active_promotion_evidence(runtime):
    runtime.generate.side_effect = [
        '{"question_type":"database"}',
        json.dumps({"entity": "products", "filters": [
            {"field": "product_id", "value": 127},
        ]}),
        "Kalani: de R$ 359,00 por R$ 323,10 (10% de desconto).",
    ]
    async with api.app.router.lifespan_context(api.app):
        async with AsyncClient(transport=ASGITransport(app=api.app),
                               base_url="http://testserver") as connection:
            response = await connection.post(
                "/questions",
                params={"question": "What does product 127 cost?"},
            )
    assert response.status_code == 200
    assert "de R$ 359,00 por R$ 323,10 (10% de desconto)" in (
        response.json()["answer"]
    )
    assert runtime.generate.call_count == 3
