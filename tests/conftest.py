"""Keep test temporary files isolated from shared Windows temp directories."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-live-performance", action="store_true", default=False,
        help="Run local Ollama timing tests (requires prepared model weights)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live-performance"):
        return
    skip = pytest.mark.skip(reason="Use --run-live-performance for real Ollama")
    for item in items:
        if "live_performance" in item.keywords:
            item.add_marker(skip)


def pytest_configure(config):
    """Use a fresh local directory unless --basetemp was explicitly supplied."""
    if config.option.basetemp is not None:
        return

    temporary_root = config.rootpath / ".pytest_cache"
    temporary_root.mkdir(exist_ok=True)
    temporary_directory = TemporaryDirectory(
        prefix="test-run-", dir=temporary_root
    )
    # pytest may recreate basetemp; the parent remains owned by this run.
    config.option.basetemp = str(Path(temporary_directory.name) / "tmp")
    config.add_cleanup(temporary_directory.cleanup)
