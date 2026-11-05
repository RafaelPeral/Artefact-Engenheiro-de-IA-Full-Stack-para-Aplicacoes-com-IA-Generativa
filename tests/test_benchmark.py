"""Verify the benchmark owns its server without a manual preparation step."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.infrastructure.llm import server
from scripts import benchmark


async def test_benchmark_starts_and_releases_its_server(monkeypatch):
    context = MagicMock()
    factory = MagicMock(return_value=context)
    monkeypatch.setattr(server, "ollama_server", factory)
    operation = AsyncMock(return_value={"requests": []})
    monkeypatch.setattr(benchmark, "_benchmark", operation)
    recorder = benchmark.Recorder()
    assert await benchmark.benchmark(None, recorder) == {"requests": []}
    context.__enter__.assert_called_once()
    context.__exit__.assert_called_once()
    assert [sample["stage"] for sample in recorder.samples] == [
        "benchmark.server_startup", "benchmark.server_shutdown",
    ]


async def test_benchmark_failure_still_releases_server(monkeypatch):
    context = MagicMock()
    factory = MagicMock(return_value=context)
    monkeypatch.setattr(server, "ollama_server", factory)
    monkeypatch.setattr(benchmark, "_benchmark", AsyncMock(
        side_effect=RuntimeError("benchmark failed")
    ))
    with pytest.raises(RuntimeError, match="benchmark failed"):
        await benchmark.benchmark(None, benchmark.Recorder())
    context.__exit__.assert_called_once()
