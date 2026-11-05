"""Portable Make task runner; bootstrap uses only Python's standard library."""

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def venv_python():
    folder = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return ROOT / ".venv" / folder


def find_ollama():
    executable = shutil.which("ollama")
    if executable:
        return Path(executable)
    candidates = [Path.home() / ".local" / "bin" / "ollama"]
    if os.environ.get("LOCALAPPDATA"):
        candidates.insert(0, Path(os.environ["LOCALAPPDATA"])
                          / "Programs" / "Ollama" / "ollama.exe")
    return next((path for path in candidates if path.is_file()), None)


def command(arguments, reason):
    """Show intent and the exact command; preserve failures and output."""
    arguments = [str(value) for value in arguments]
    print(f"\n==> {reason}", flush=True)
    print(subprocess.list2cmdline(arguments), flush=True)
    environment = os.environ.copy()
    executable = find_ollama()
    if executable:
        environment["PATH"] = (
            str(executable.parent) + os.pathsep + environment.get("PATH", "")
        )
    environment["PYTHONUNBUFFERED"] = "1"
    with subprocess.Popen(arguments, cwd=ROOT, env=environment) as process:
        try:
            code = process.wait()
        except KeyboardInterrupt:
            # Console Ctrl+C reaches the child; allow its lifespan cleanup.
            print("\nWaiting for the running task to shut down...", flush=True)
            process.wait()
            raise
        if code:
            raise subprocess.CalledProcessError(code, arguments)


def ensure_environment():
    if venv_python().is_file():
        print(f"Using project environment: {venv_python()}", flush=True)
        return
    command([sys.executable, "-m", "venv", ROOT / ".venv"],
            "Create .venv so project packages do not modify global Python")


def install_packages():
    ensure_environment()
    command([venv_python(), "-m", "pip", "install", "-r",
             ROOT / "requirements-dev.txt"],
            "Install application and test dependencies into .venv")


def _download(url, destination):
    print(f"Downloading runtime from {url}", flush=True)
    downloaded, next_notice = 0, 64 * 1024 * 1024
    with urlopen(url, timeout=60) as source, destination.open("wb") as target:
        while block := source.read(1024 * 1024):
            target.write(block)
            downloaded += len(block)
            if downloaded >= next_notice:
                print(f"Runtime download: {downloaded // (1024 * 1024)} MiB",
                      flush=True)
                next_notice += 64 * 1024 * 1024


def install_ollama():
    """Install a standalone runtime without creating an auto-start service."""
    installed = find_ollama()
    if installed:
        print(f"Using installed Ollama: {installed}", flush=True)
        return
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        destination = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama"
        archive = "ollama-windows-amd64.zip"
        url = ("https://github.com/ollama/ollama/releases/latest/download/"
               + archive)
    elif (system == "Linux"
          and machine in {"amd64", "x86_64", "aarch64", "arm64"}):
        if not shutil.which("tar") or not shutil.which("zstd"):
            raise RuntimeError(
                "Install tar and zstd with your Linux package manager"
            )
        architecture = "arm64" if machine in {"aarch64", "arm64"} else "amd64"
        destination = Path.home() / ".local"
        archive = f"ollama-linux-{architecture}.tar.zst"
        url = "https://ollama.com/download/" + archive
    else:
        raise RuntimeError(
            "Automatic Ollama installation supports Windows x64 and Linux "
            "x64/ARM64. Install Ollama manually on this platform."
        )
    print(f"Installing standalone Ollama into {destination}; no login service",
          flush=True)
    with TemporaryDirectory(prefix="artefact-ollama-") as directory:
        path = Path(directory) / archive
        _download(url, path)
        destination.mkdir(parents=True, exist_ok=True)
        if archive.endswith(".zip"):
            with zipfile.ZipFile(path) as package:
                package.extractall(destination)
        else:
            command(["tar", "--zstd", "-xf", path, "-C", destination],
                    "Extract the Linux runtime into the user prefix")
    if find_ollama() is None:
        raise RuntimeError(
            "Runtime extraction finished but ollama was not found"
        )


def project_python():
    if not venv_python().is_file():
        raise RuntimeError(
            "Project environment is missing. Run make install first."
        )
    return venv_python()


def run_task(task, live=False):
    """Keep setup separate from serving; commands need no venv activation."""
    if task == "help":
        print("make install    Prepare dependencies, Ollama and both models\n"
              "make run        Import CSVs, load resources and serve the API\n"
              "make test       Tests, coverage and offline timings\n"
              "make test-live  Include local-model timing checks\n"
              "make benchmark  Measure the full pipeline with real models\n"
              "\nRepair helpers: make pip, ollama, model, embeddings, models")
        return
    if task == "install":
        install_packages()
        install_ollama()
        command([project_python(), "-m", "scripts.prepare_models"],
                "Prepare both model weights; stop the owned server afterward")
        print("\nSetup complete. Start the project with: make run", flush=True)
    elif task == "pip":
        install_packages()
    elif task == "ollama":
        install_ollama()
    elif task in {"model", "embeddings", "models"}:
        arguments = [project_python(), "-m", "scripts.prepare_models"]
        if task != "models":
            arguments += ["--only", task]
        command(arguments, "Prepare weights without starting the API")
    elif task == "run":
        command([project_python(), "-m", "uvicorn", "app.app:app",
                 "--host", "127.0.0.1", "--port", "8000"],
                "Start API: import CSVs, load LLM, index PDF, then serve; "
                "Ctrl+C releases owned resources")
    elif task == "test":
        arguments = [project_python(), "-m", "pytest", "--durations=10"]
        if live:
            arguments.append("--run-live-performance")
        reason = "Run tests and timings"
        reason += " with local inference" if live else " with mocked inference"
        command(arguments, reason)
    elif task == "benchmark":
        command([project_python(), "-m", "scripts.benchmark", "--output",
                 "reports/performance/latest.json"],
                "Measure real startup, requests and shutdown; no downloads")
    else:
        raise ValueError(f"Unknown project task: {task}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=[
        "help", "install", "run", "test", "benchmark",
        "pip", "ollama", "model", "embeddings", "models",
    ])
    parser.add_argument("--live", action="store_true")
    arguments = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("Python 3.10+ is required; Python 3.12 is recommended")
    try:
        run_task(arguments.task, arguments.live)
    except KeyboardInterrupt:
        parser.exit(130, "\nTask interrupted.\n")
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Project command failed: {error}\n")
