"""Verify Make task dispatch without installation or actual services."""

import os
import io
import subprocess
from unittest.mock import MagicMock, Mock
import zipfile

import pytest

from scripts import project


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "ROOT", tmp_path)
    return tmp_path


def ready_environment():
    path = project.venv_python()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_install_orders_environment_runtime_and_both_models(root, monkeypatch):
    events = []
    monkeypatch.setattr(project, "install_packages",
                        lambda: events.append("packages"))
    monkeypatch.setattr(project, "install_ollama",
                        lambda: events.append("runtime"))
    ready_environment()
    monkeypatch.setattr(project, "command",
                        lambda args, reason: events.append(args))
    project.run_task("install")
    assert events[:2] == ["packages", "runtime"]
    assert events[2] == [project.venv_python(), "-m", "scripts.prepare_models"]


def test_install_failure_stops_before_runtime_or_download(root, monkeypatch):
    install_runtime = Mock()
    monkeypatch.setattr(project, "install_ollama", install_runtime)
    monkeypatch.setattr(project, "install_packages", Mock(
        side_effect=subprocess.CalledProcessError(1, "pip")
    ))
    with pytest.raises(subprocess.CalledProcessError):
        project.run_task("install")
    install_runtime.assert_not_called()


def test_python_packages_are_installed_inside_project_venv(root, monkeypatch):
    execute = Mock()
    monkeypatch.setattr(project, "command", execute)
    project.install_packages()
    assert execute.call_args_list[0].args[0][-2:] == ["venv", root / ".venv"]
    assert execute.call_args_list[1].args[0][0] == project.venv_python()
    assert execute.call_args_list[1].args[0][-1] == (
        root / "requirements-dev.txt"
    )


def test_existing_venv_is_not_recreated(root, monkeypatch):
    ready_environment()
    execute = Mock()
    monkeypatch.setattr(project, "command", execute)
    project.ensure_environment()
    execute.assert_not_called()


@pytest.mark.parametrize("task, expected", [
    ("run", ["-m", "uvicorn", "app.app:app"]),
    ("test", ["-m", "pytest", "--durations=10"]),
    ("benchmark", ["-m", "scripts.benchmark", "--output"]),
    ("models", ["-m", "scripts.prepare_models"]),
    ("model", ["-m", "scripts.prepare_models", "--only", "model"]),
    ("embeddings", ["-m", "scripts.prepare_models", "--only", "embeddings"]),
])
def test_tasks_use_venv_and_expected_entry_points(root, monkeypatch, task,
                                               expected):
    interpreter = ready_environment()
    execute = Mock()
    monkeypatch.setattr(project, "command", execute)
    project.run_task(task)
    args = execute.call_args.args[0]
    assert args[0] == interpreter
    assert args[1:1 + len(expected)] == expected


def test_live_tests_are_an_explicit_option(root, monkeypatch):
    ready_environment()
    execute = Mock()
    monkeypatch.setattr(project, "command", execute)
    project.run_task("test", live=True)
    assert "--run-live-performance" in execute.call_args.args[0]


def test_run_without_environment_explains_setup(root):
    with pytest.raises(RuntimeError, match="make install"):
        project.run_task("run")


def test_installed_runtime_is_reused_without_download(monkeypatch, tmp_path):
    executable = tmp_path / "ollama.exe"
    download = Mock()
    monkeypatch.setattr(project, "find_ollama", lambda: executable)
    monkeypatch.setattr(project, "_download", download)
    project.install_ollama()
    download.assert_not_called()


def test_windows_install_does_not_launch_server(monkeypatch, tmp_path):
    monkeypatch.setattr(project.platform, "system", lambda: "Windows")
    monkeypatch.setattr(project.platform, "machine", lambda: "AMD64")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    executable = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    monkeypatch.setattr(project, "find_ollama",
                        Mock(side_effect=[None, executable]))

    def download(url, path):
        assert "ollama-windows-amd64.zip" in url
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("ollama.exe", b"test runtime")

    monkeypatch.setattr(project, "_download", download)
    execute = Mock()
    monkeypatch.setattr(project, "command", execute)
    project.install_ollama()
    assert executable.read_bytes() == b"test runtime"
    execute.assert_not_called()


@pytest.mark.parametrize("machine, archive", [
    ("x86_64", "ollama-linux-amd64.tar.zst"),
    ("aarch64", "ollama-linux-arm64.tar.zst"),
])
def test_linux_installer_uses_user_prefix_not_service(monkeypatch, tmp_path,
                                                     machine, archive):
    monkeypatch.setattr(project.platform, "system", lambda: "Linux")
    monkeypatch.setattr(project.platform, "machine", lambda: machine)
    monkeypatch.setattr(project.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(project.shutil, "which", lambda name: name)
    monkeypatch.setattr(project, "find_ollama", Mock(side_effect=[
        None, tmp_path / ".local" / "bin" / "ollama",
    ]))
    download, execute = Mock(), Mock()
    monkeypatch.setattr(project, "_download", download)
    monkeypatch.setattr(project, "command", execute)
    project.install_ollama()
    assert download.call_args.args[0].endswith(archive)
    args = execute.call_args.args[0]
    assert args[:3] == ["tar", "--zstd", "-xf"]
    assert args[-1] == tmp_path / ".local"
    assert "sudo" not in args and "serve" not in args


def test_missing_linux_extraction_tools_have_clear_error(monkeypatch):
    monkeypatch.setattr(project.platform, "system", lambda: "Linux")
    monkeypatch.setattr(project.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(project, "find_ollama", lambda: None)
    monkeypatch.setattr(project.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="tar and zstd"):
        project.install_ollama()


def test_commands_show_purpose_and_runtime_path(root, monkeypatch, capsys):
    executable = root / "runtime" / "ollama.exe"
    monkeypatch.setattr(project, "find_ollama", lambda: executable)
    process = MagicMock()
    process.__enter__.return_value = process
    process.wait.return_value = 0
    launch = Mock(return_value=process)
    monkeypatch.setattr(project.subprocess, "Popen", launch)
    project.command(["python", "-m", "pytest"], "Run the test suite")
    assert "Run the test suite" in capsys.readouterr().out
    assert launch.call_args.kwargs["env"]["PATH"].split(os.pathsep)[0] == (
        str(executable.parent)
    )
    assert launch.call_args.kwargs["cwd"] == root


def test_unknown_task_is_rejected():
    with pytest.raises(ValueError, match="Unknown project task"):
        project.run_task("unknown")


def test_command_failure_propagates_exit_code(root, monkeypatch):
    process = MagicMock()
    process.__enter__.return_value = process
    process.wait.return_value = 7
    monkeypatch.setattr(project, "find_ollama", lambda: None)
    launch = Mock(return_value=process)
    monkeypatch.setattr(project.subprocess, "Popen", launch)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        project.command(["python", "-m", "pytest"], "Run tests")
    assert caught.value.returncode == 7


def test_interrupt_waits_for_child_cleanup(root, monkeypatch):
    process = MagicMock()
    process.__enter__.return_value = process
    process.wait.side_effect = [KeyboardInterrupt, 0]
    monkeypatch.setattr(project, "find_ollama", lambda: None)
    launch = Mock(return_value=process)
    monkeypatch.setattr(project.subprocess, "Popen", launch)
    with pytest.raises(KeyboardInterrupt):
        project.command(["python", "-m", "uvicorn"], "Run API")
    assert process.wait.call_count == 2
    process.kill.assert_not_called()


def test_runtime_download_uses_bounded_socket_timeout(monkeypatch, tmp_path):
    source = io.BytesIO(b"runtime archive")
    opening = Mock(return_value=source)
    monkeypatch.setattr(project, "urlopen", opening)
    destination = tmp_path / "runtime.zip"
    project._download("https://example.test/runtime.zip", destination)
    assert destination.read_bytes() == b"runtime archive"
    opening.assert_called_once_with(
        "https://example.test/runtime.zip", timeout=60
    )
