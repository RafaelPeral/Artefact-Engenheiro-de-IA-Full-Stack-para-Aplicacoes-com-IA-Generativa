"""Test stage timing, heartbeat cleanup, logging, and inference metrics."""

import logging
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core import tracing


def test_success_reports_elapsed_time(monkeypatch, caplog):
    monkeypatch.setattr(tracing, "perf_counter", Mock(side_effect=[10, 12]))
    with caplog.at_level(logging.INFO, logger="project"):
        with tracing.trace_step("example", heartbeat_interval=0):
            pass
    assert "START example" in caplog.text
    assert "DONE example | elapsed=2.000s" in caplog.text


def test_failure_preserves_exception(caplog):
    with caplog.at_level(logging.INFO, logger="project"):
        with pytest.raises(ValueError, match="original failure"):
            with tracing.trace_step("example", heartbeat_interval=0):
                raise ValueError("original failure")
    assert "FAIL example" in caplog.text
    assert "ValueError: original failure" in caplog.text
    assert "DONE example" not in caplog.text


def test_wait_notification_during_blocking_stage(monkeypatch, caplog):
    notified = Event()
    original_info = tracing.LOGGER.info

    def observe(message, *args):
        original_info(message, *args)
        if message.startswith("WAIT"):
            notified.set()

    monkeypatch.setattr(tracing.LOGGER, "info", observe)
    with caplog.at_level(logging.INFO, logger="project"):
        with tracing.trace_step("slow", heartbeat_interval=0.01):
            assert notified.wait(timeout=2)
    assert "WAIT slow" in caplog.text
    assert "DONE slow" in caplog.text


def test_metrics_convert_units_and_calculate_throughput(caplog):
    response = SimpleNamespace(
        total_duration=4_000_000_000,
        load_duration=1_000_000_000,
        prompt_eval_duration=1_000_000_000,
        eval_duration=2_000_000_000,
        eval_count=20,
    )
    with caplog.at_level(logging.INFO, logger="project"):
        tracing.log_inference_metrics(response)
    assert "load_duration=1.000s" in caplog.text
    assert "tokens_per_second=10.00" in caplog.text


def test_missing_metrics_are_ignored(caplog):
    with caplog.at_level(logging.INFO, logger="project"):
        tracing.log_inference_metrics(SimpleNamespace())
    assert "METRICS" not in caplog.text


@pytest.mark.parametrize("use_environment", [False, True])
def test_logging_file_output_and_reconfiguration(monkeypatch, tmp_path,
                                                 use_environment):
    logger = logging.Logger("isolated-project")
    monkeypatch.setattr(tracing, "LOGGER", logger)
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    destination = tmp_path / "logs" / "execution.log"
    if use_environment:
        monkeypatch.setenv("TRACE_LOG_FILE", str(destination))
    else:
        monkeypatch.delenv("TRACE_LOG_FILE", raising=False)
    try:
        for _ in range(2):
            tracing.configure_logging(None if use_environment else destination)
        assert len(logger.handlers) == 2
        logger.info("test log entry")
        for handler in logger.handlers:
            handler.flush()
        assert destination.read_text().count("test log entry") == 1
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
