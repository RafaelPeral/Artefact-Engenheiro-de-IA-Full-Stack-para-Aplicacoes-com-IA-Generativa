"""Test download preparation independently from inference and real servers."""

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


preparation = importlib.import_module("app.prepare_model")


@pytest.fixture
def runtime(monkeypatch):
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    lifecycle = MagicMock()
    client.pull.return_value = [
        SimpleNamespace(status="downloading"),
        SimpleNamespace(status="downloading"),
        SimpleNamespace(status="success"),
    ]
    monkeypatch.setattr(preparation, "Client", factory)
    monkeypatch.setattr(preparation, "ollama_server", lifecycle)
    return SimpleNamespace(factory=factory, client=client, lifecycle=lifecycle)


def test_download_never_performs_inference(runtime, capsys):
    preparation.prepare_model()
    runtime.client.pull.assert_called_once_with(
        model="llama3.2:3b", stream=True
    )
    runtime.client.show.assert_called_once_with(model="llama3.2:3b")
    runtime.client.generate.assert_not_called()
    runtime.client.chat.assert_not_called()
    runtime.factory.return_value.__exit__.assert_called_once()
    runtime.lifecycle.return_value.__exit__.assert_called_once()
    output = capsys.readouterr().out
    assert output.count("downloading") == 1
    assert "Model ready: llama3.2:3b" in output


def test_download_failure_exits_both_contexts(runtime):
    runtime.client.pull.side_effect = RuntimeError("download failed")
    with pytest.raises(RuntimeError, match="download failed"):
        preparation.prepare_model()
    runtime.factory.return_value.__exit__.assert_called_once()
    runtime.lifecycle.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("model", ["", " ", None, 42])
def test_invalid_model_never_starts_server(runtime, model):
    with pytest.raises(ValueError):
        preparation.prepare_model(model)
    runtime.lifecycle.assert_not_called()
