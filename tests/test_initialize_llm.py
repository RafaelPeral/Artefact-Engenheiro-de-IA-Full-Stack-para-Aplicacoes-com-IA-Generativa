"""Test inference initialization separately from server process management."""

import importlib
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


llm = importlib.import_module("app.initialize_llm")


@pytest.fixture
def runtime(monkeypatch):
    client = Mock()
    client.chat.return_value.message.content = " Hello "
    client_factory = Mock(return_value=client)
    lifecycle = Mock()

    @contextmanager
    def server(*args):
        lifecycle.start()
        try:
            yield True
        finally:
            lifecycle.stop()

    monkeypatch.setattr(llm, "Client", client_factory)
    monkeypatch.setattr(llm, "ollama_server", server)
    return SimpleNamespace(
        client=client, factory=client_factory, lifecycle=lifecycle
    )


def test_reuses_client_and_closes_resources(runtime):
    with llm.initialize_llm() as respond:
        assert respond(" Hi ") == "Hello"
        assert respond("Again", "Custom instructions") == "Hello"
        runtime.lifecycle.stop.assert_not_called()
    runtime.factory.assert_called_once()
    runtime.client.close.assert_called_once()
    runtime.lifecycle.stop.assert_called_once()
    messages = runtime.client.chat.call_args.kwargs["messages"]
    assert messages[0]["content"] == "Custom instructions"
    assert runtime.client.generate.call_args.kwargs["keep_alive"] == 0


@pytest.mark.parametrize("options", [
    {"model": ""}, {"model": None}, {"context_size": True},
    {"context_size": 1.5}, {"context_size": 0}, {"context_size": 5000},
    {"timeout": 0}, {"timeout": float("inf")},
    {"startup_timeout": 0}, {"startup_timeout": float("nan")},
])
def test_invalid_configuration_does_not_start_server(runtime, options):
    with pytest.raises(ValueError):
        with llm.initialize_llm(**options):
            pass
    runtime.lifecycle.start.assert_not_called()
    runtime.factory.assert_not_called()


@pytest.mark.parametrize("message", ["", " ", None, 42])
def test_invalid_messages_never_call_model(runtime, message):
    with llm.initialize_llm() as respond:
        with pytest.raises(ValueError, match="message"):
            respond(message)
    runtime.client.chat.assert_not_called()


@pytest.mark.parametrize("content", [None, "", " "])
def test_empty_responses_raise(runtime, content):
    runtime.client.chat.return_value.message.content = content
    with llm.initialize_llm() as respond:
        with pytest.raises(RuntimeError, match="no response"):
            respond("Hello")


@pytest.mark.parametrize("error, expected", [
    (llm.ResponseError("missing", status_code=404), "make model"),
    (llm.ResponseError("failure", status_code=500), "could not load"),
    (ConnectionError("offline"), "Lost connection"),
])
def test_startup_errors_cleanup_without_downloading(runtime, error, expected):
    runtime.client.show.side_effect = error
    with pytest.raises(RuntimeError, match=expected):
        with llm.initialize_llm():
            pass
    runtime.client.pull.assert_not_called()
    runtime.client.close.assert_called_once()
    runtime.lifecycle.stop.assert_called_once()


def test_application_error_still_releases_resources(runtime):
    with pytest.raises(ValueError, match="application failed"):
        with llm.initialize_llm():
            raise ValueError("application failed")
    runtime.client.close.assert_called_once()
    runtime.lifecycle.stop.assert_called_once()


def test_unload_failure_still_closes_client_and_server(runtime):
    runtime.client.generate.side_effect = [Mock(), RuntimeError("unload")]
    with llm.initialize_llm():
        pass
    runtime.client.close.assert_called_once()
    runtime.lifecycle.stop.assert_called_once()


def test_external_server_does_not_receive_shutdown_requests(monkeypatch):
    client = Mock()
    monkeypatch.setattr(llm, "Client", Mock(return_value=client))

    @contextmanager
    def existing_server(*args):
        yield False

    monkeypatch.setattr(llm, "ollama_server", existing_server)
    with llm.initialize_llm():
        pass
    client.generate.assert_called_once()
    assert client.generate.call_args.kwargs["keep_alive"] == 0
    client.close.assert_called_once()
