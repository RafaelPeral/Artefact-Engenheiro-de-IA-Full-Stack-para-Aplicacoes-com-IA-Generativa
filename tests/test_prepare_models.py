"""Verify both required models are prepared using one temporary lifecycle."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from ollama import ResponseError

from scripts import prepare_models as preparation


@pytest.fixture
def runtime(monkeypatch):
    server, factory, pull = MagicMock(), MagicMock(), Mock()
    monkeypatch.setattr(preparation, "configure_logging", lambda: None)
    monkeypatch.setattr(preparation, "ollama_server", server)
    monkeypatch.setattr(preparation, "Client", factory)
    monkeypatch.setattr(preparation, "prepare_model", pull)
    monkeypatch.delenv("PDF_EMBEDDING_MODEL", raising=False)
    return SimpleNamespace(
        server=server, client=factory.return_value.__enter__.return_value,
        factory=factory, pull=pull,
    )


def test_installed_models_are_checked_without_downloading(runtime):
    preparation.prepare_models()
    checked = [call.kwargs["model"]
               for call in runtime.client.show.call_args_list]
    assert checked == ["llama3.2:3b", "embeddinggemma"]
    runtime.pull.assert_not_called()
    runtime.server.assert_called_once()
    runtime.server.return_value.__exit__.assert_called_once()


def test_only_missing_weights_are_downloaded(runtime):
    runtime.client.show.side_effect = [None, ResponseError("missing", 404)]
    preparation.prepare_models()
    runtime.pull.assert_called_once_with("embeddinggemma")


def test_embedding_override_is_shared_with_runtime(runtime, monkeypatch):
    monkeypatch.setenv("PDF_EMBEDDING_MODEL", "embeddinggemma:300m")
    preparation.prepare_models("embeddings")
    runtime.client.show.assert_called_once_with(model="embeddinggemma:300m")


def test_download_failure_releases_temporary_server(runtime):
    runtime.client.show.side_effect = ResponseError("missing", 404)
    runtime.pull.side_effect = RuntimeError("download failed")
    with pytest.raises(RuntimeError, match="download failed"):
        preparation.prepare_models()
    runtime.server.return_value.__exit__.assert_called_once()
    runtime.factory.return_value.__exit__.assert_called_once()


def test_non_missing_model_errors_do_not_trigger_download(runtime):
    runtime.client.show.side_effect = ResponseError("failure", 500)
    with pytest.raises(ResponseError):
        preparation.prepare_models()
    runtime.pull.assert_not_called()
