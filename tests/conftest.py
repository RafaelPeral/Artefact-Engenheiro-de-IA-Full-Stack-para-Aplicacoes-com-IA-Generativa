"""Keep test temporary files isolated from shared Windows temp directories."""

from pathlib import Path
from tempfile import TemporaryDirectory


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
