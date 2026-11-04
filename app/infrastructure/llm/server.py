"""Own a temporary Ollama server without stopping pre-existing instances."""

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from app.core.tracing import LOGGER, trace_step


@contextmanager
def ollama_server(startup_timeout: float = 30.0) -> Iterator[bool]:
    """Yield server ownership and stop only the process launched here."""
    if not math.isfinite(startup_timeout) or startup_timeout <= 0:
        raise ValueError("startup_timeout must be finite and positive")
    owned_process = None

    def ensure_server_running():
        """Reuse a healthy server or launch one and wait for its local API."""
        nonlocal owned_process
        # Bypass HTTP proxies for the local readiness check.
        opener = build_opener(ProxyHandler({}))

        def is_ready(request_timeout):
            try:
                with opener.open(
                    "http://127.0.0.1:11434/api/version",
                    timeout=request_timeout,
                ) as response:
                    payload = json.load(response)
                return (
                    isinstance(payload, dict) and bool(payload.get("version"))
                )
            except (OSError, URLError, ValueError):
                return False

        if is_ready(min(1.0, startup_timeout)):
            LOGGER.info("Reusing existing Ollama server")
            return

        executable = shutil.which("ollama")
        if executable is None and os.name == "nt":
            # VS Code may still have PATH from before Ollama was installed.
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                candidate = (
                    Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
                )
                if candidate.is_file():
                    executable = str(candidate)
        if executable is None:
            raise RuntimeError(
                "Ollama is not installed or its executable cannot be found. "
                "Install it from https://ollama.com/download/windows and "
                "restart your terminal."
            )

        log_path = Path(tempfile.gettempdir()) / "artefact-ollama-server.log"
        environment = os.environ.copy()
        environment["OLLAMA_HOST"] = "127.0.0.1:11434"
        process_options = {"env": environment}
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            process_options["start_new_session"] = True

        try:
            with log_path.open("ab") as log_file:
                process = subprocess.Popen(
                    [executable, "serve"],
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    **process_options,
                )
        except OSError as error:
            raise RuntimeError(
                f"Could not start Ollama. Server log: {log_path}"
            ) from error

        LOGGER.info("Started Ollama | pid=%s | log=%s", process.pid, log_path)
        owned_process = process
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if is_ready(max(0.001, min(1.0, remaining))):
                return
            if process.poll() is not None:
                raise RuntimeError(
                    f"Ollama exited with code {process.returncode}. "
                    f"Server log: {log_path}"
                )
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))

        raise RuntimeError(
            f"Ollama was not ready within {startup_timeout} seconds. "
            f"Server log: {log_path}"
        )

    try:
        with trace_step("server.startup"):
            ensure_server_running()
        yield owned_process is not None
    finally:
        if owned_process is not None and owned_process.poll() is None:
            with trace_step("server.shutdown"):
                owned_process.terminate()
                try:
                    owned_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    owned_process.kill()
                    owned_process.wait(timeout=5)
