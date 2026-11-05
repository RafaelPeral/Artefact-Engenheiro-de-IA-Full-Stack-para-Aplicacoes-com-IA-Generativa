"""Separate real SQLite/ASGI latency from optional real-model measurements."""

import importlib
import json
import os
import sqlite3
import statistics
import unicodedata
from contextlib import closing
from time import perf_counter
from unittest.mock import Mock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_question_service
from app.infrastructure.database.sqlite import create_database
from app.infrastructure.llm.client import initialize_llm
from app.services.questions import QuestionService
from app.tools.database import execute_database_plan


api = importlib.import_module("app.app")
pytestmark = pytest.mark.performance


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "store.db"
    create_database(database_path=path)
    monkeypatch.setenv("STORE_DATABASE_PATH", str(path))
    return path


def test_sql_and_promotional_price_latency(database, record_timing):
    plan = {"entity": "products", "filters": [
        {"field": "product_id", "value": 127},
    ]}
    samples = []
    for _ in range(20):
        started = perf_counter()
        evidence = execute_database_plan(plan, database)
        samples.append(perf_counter() - started)
        assert evidence["rows"][0]["effective_price_brl"] == 323.1
    record_timing("sql.promotional_price", samples, model_mode="no_model")
    budget = float(os.environ.get("PERF_SQL_MEDIAN_SECONDS", "0.25"))
    assert statistics.median(samples) < budget


async def test_warm_api_plans_and_price_refresh(database, record_timing):
    def inference(message, system, **kwargs):
        schema = kwargs.get("response_format")
        if schema is not None:
            if "question_type" in schema["properties"]:
                return '{"question_type":"database"}'
            return ('{"entity":"products","filters":'
                    '[{"field":"product_id","value":127}]}')
        payload = json.loads(message)
        disclosures = payload.get("required_price_disclosures", [])
        if disclosures:
            item = disclosures[0]
            return (
                f"Kalani: de {item['original_price']} por "
                f"{item['effective_price']} ({item['discount']} de desconto)."
            )
        row = payload["database_tool_result"]["rows"][0]
        return f"Kalani: R$ {row['effective_price_brl']:.2f}."

    model = Mock(side_effect=inference)
    service = QuestionService(model)
    api.app.dependency_overrides[get_question_service] = lambda: service
    try:
        async with AsyncClient(transport=ASGITransport(app=api.app),
                               base_url="http://performance") as client:
            question = {"question": "What does product 127 cost?"}
            first = await client.post("/questions", params=question)
            assert "323,10" in first.json()["answer"]
            assert model.call_count == 3  # Classify, plan, then generate.
            samples = []
            for _ in range(20):
                started = perf_counter()
                response = await client.post("/questions", params=question)
                samples.append(perf_counter() - started)
                assert response.status_code == 200
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "UPDATE promotions SET is_active=0 WHERE product_id=127"
                )
                connection.commit()
            refreshed = await client.post("/questions", params=question)
            assert "359.00" in refreshed.json()["answer"]
            assert "323,10" not in refreshed.json()["answer"]
            assert model.call_count == 24  # 22 answers, two interpretations.
    finally:
        del api.app.dependency_overrides[get_question_service]
    record_timing("api.warm_price", samples, model_mode="mocked",
                  model_calls=model.call_count, requests=22)
    budget = float(os.environ.get("PERF_API_MEDIAN_SECONDS", "0.5"))
    assert statistics.median(samples) < budget


@pytest.mark.live_performance
def test_local_model_first_and_repeated_database_questions(database,
                                                          record_timing):
    calls = []
    with initialize_llm() as generate:
        def measured(*args, **kwargs):
            calls.append(1)
            return generate(*args, **kwargs)

        service = QuestionService(measured)
        def check_answer(name, answer):
            if name == "price":
                assert "323,10" in answer or "323.10" in answer
                return
            normalized = "".join(
                char for char in unicodedata.normalize("NFKD", answer)
                if not unicodedata.combining(char)
            ).casefold()
            assert all(word in normalized for word in (
                "guitarra", "baixo", "bateria", "teclado", "viol", "ukulele",
            ))

        for name, question, _ in (
            ("catalog", "Quais produtos vocês vendem?", "65"),
            ("price", "Qual o preço do produto 127?", "323"),
        ):
            before = len(calls)
            started = perf_counter()
            first = service.answer(question)
            cold = perf_counter() - started
            check_answer(name, first.answer)
            count = len(calls)
            assert 3 <= count - before <= 4
            samples = []
            for _ in range(3):
                started = perf_counter()
                check_answer(name, service.answer(question).answer)
                samples.append(perf_counter() - started)
            assert count + 3 <= len(calls) <= count + 6
            record_timing(f"live.{name}.first", [cold],
                          model_mode="local_ollama")
            record_timing(
                f"live.{name}.repeat", samples,
                model_mode="local_ollama", model_calls=len(calls) - count,
            )
            budget = float(os.environ.get("PERF_LIVE_COLD_SECONDS", "30"))
            assert cold < budget
