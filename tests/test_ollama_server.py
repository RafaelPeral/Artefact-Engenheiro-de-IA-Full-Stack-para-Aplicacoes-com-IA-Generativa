"""Test owned server readiness and shutdown without launching processes."""

import importlib
import io
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock, mock_open
from urllib.error import URLError

import pytest


server = importlib.import_module("app.ollama_server")


def ready():
    return io.BytesIO(b'{"version": "test"}')


@pytest.fixture
def runtime(monkeypatch):
    opener = Mock()
    process = Mock()
    process.poll.return_value = None
    launch = Mock(return_value=process)
    which = Mock(return_value="ollama")
    monkeypatch.setattr(server, "build_opener", Mock(return_value=opener))
    monkeypatch.setattr(server.subprocess, "Popen", launch)
    monkeypatch.setattr(server.shutil, "which", which)
    monkeypatch.setattr(server.Path, "open", mock_open())
    monkeypatch.setattr(server, "time", SimpleNamespace(
        monotonic=time.monotonic, sleep=Mock()
    ))
    return SimpleNamespace(
        opener=opener, process=process, launch=launch, which=which
    )


def test_existing_server_is_never_stopped(runtime):
    runtime.opener.open.return_value = ready()
    with server.ollama_server() as owned:
        assert owned is False
    runtime.launch.assert_not_called()
    runtime.which.assert_not_called()


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_owned_server_is_ready_before_yield_and_stopped(runtime, monkeypatch,
                                                       platform):
    monkeypatch.setattr(server, "os", SimpleNamespace(
        name=platform, environ=os.environ
    ))
    monkeypatch.setattr(server.subprocess, "CREATE_NO_WINDOW", 0x08000000,
                        raising=False)
    runtime.opener.open.side_effect = [
        URLError("offline"), URLError("starting"), ready()
    ]
    with server.ollama_server() as owned:
        assert owned is True
        runtime.process.terminate.assert_not_called()
    runtime.process.terminate.assert_called_once()
    runtime.process.wait.assert_called_once_with(timeout=5)
    args, options = runtime.launch.call_args
    assert args[0] == ["ollama", "serve"]
    assert options["env"]["OLLAMA_HOST"] == "127.0.0.1:11434"
    if platform == "nt":
        assert options["creationflags"] == 0x08000000
    else:
        assert options["start_new_session"] is True


def test_standard_windows_installation_is_found(runtime, monkeypatch,
                                                tmp_path):
    candidate = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    candidate.parent.mkdir(parents=True)
    candidate.touch()
    monkeypatch.setattr(server, "os", SimpleNamespace(
        name="nt", environ={"LOCALAPPDATA": str(tmp_path)}
    ))
    monkeypatch.setattr(server.subprocess, "CREATE_NO_WINDOW", 0x08000000,
                        raising=False)
    runtime.which.return_value = None
    runtime.opener.open.side_effect = [URLError("offline"), ready()]
    with server.ollama_server():
        pass
    assert runtime.launch.call_args.args[0][0] == str(candidate)


def test_missing_executable_does_not_launch(runtime, monkeypatch):
    runtime.opener.open.side_effect = URLError("offline")
    runtime.which.return_value = None
    monkeypatch.setattr(server.Path, "is_file", lambda path: False)
    with pytest.raises(RuntimeError, match="not installed"):
        with server.ollama_server():
            pass
    runtime.launch.assert_not_called()


def test_spawn_failure_has_log_context(runtime):
    runtime.opener.open.side_effect = URLError("offline")
    runtime.launch.side_effect = OSError("cannot launch")
    with pytest.raises(RuntimeError, match="Server log"):
        with server.ollama_server():
            pass


def test_early_exit_reports_code(runtime):
    runtime.opener.open.side_effect = URLError("offline")
    runtime.process.poll.return_value = 1
    runtime.process.returncode = 1
    with pytest.raises(RuntimeError, match="code 1"):
        with server.ollama_server():
            pass


def test_readiness_timeout_stops_owned_process(runtime, monkeypatch):
    runtime.opener.open.side_effect = URLError("offline")
    monkeypatch.setattr(server.time, "monotonic", Mock(side_effect=[0, 31]))
    with pytest.raises(RuntimeError, match="not ready"):
        with server.ollama_server():
            pass
    runtime.process.terminate.assert_called_once()


def test_unresponsive_process_is_killed(runtime):
    runtime.opener.open.side_effect = [URLError("offline"), ready()]
    runtime.process.wait.side_effect = [
        server.subprocess.TimeoutExpired("ollama", 5), None
    ]
    with server.ollama_server():
        pass
    runtime.process.kill.assert_called_once()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_rejected_before_launch(runtime, timeout):
    with pytest.raises(ValueError):
        with server.ollama_server(timeout):
            pass
    runtime.launch.assert_not_called()
