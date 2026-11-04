"""Provide console tracing, stage timings, and local inference metrics."""

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from time import perf_counter


LOGGER = logging.getLogger("project")
LOGGER.addHandler(logging.NullHandler())


def configure_logging(log_file=None):
    """Configure project logs without changing third-party logging settings."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    LOGGER.setLevel(level)
    LOGGER.propagate = False
    for handler in LOGGER.handlers[:]:
        LOGGER.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers = [logging.StreamHandler()]
    destination = log_file or os.environ.get("TRACE_LOG_FILE")
    if destination:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)


@contextmanager
def trace_step(name, heartbeat_interval=5.0):
    """Log stage entry, elapsed time, failures, and periodic wait notices."""
    started = perf_counter()
    finished = Event()
    LOGGER.info("START %s", name)

    def report_wait():
        while not finished.wait(heartbeat_interval):
            LOGGER.info(
                "WAIT %s | elapsed=%.3fs", name, perf_counter() - started
            )

    worker = None
    if heartbeat_interval > 0 and LOGGER.isEnabledFor(logging.INFO):
        worker = Thread(
            target=report_wait, daemon=True, name="trace-heartbeat"
        )
        worker.start()
    try:
        yield
    except BaseException:
        finished.set()
        LOGGER.exception(
            "FAIL %s | elapsed=%.3fs", name, perf_counter() - started
        )
        raise
    else:
        finished.set()
        LOGGER.info("DONE %s | elapsed=%.3fs", name, perf_counter() - started)
    finally:
        finished.set()
        if worker is not None:
            worker.join()


def log_inference_metrics(response):
    """Report Ollama durations in seconds and output token throughput."""
    metrics = {}
    for field in (
        "total_duration", "load_duration", "prompt_eval_duration",
        "eval_duration",
    ):
        value = getattr(response, field, None)
        if isinstance(value, (int, float)):
            metrics[field] = value / 1_000_000_000
    tokens = getattr(response, "eval_count", None)
    duration = metrics.get("eval_duration", 0)
    details = " | ".join(
        f"{key}={value:.3f}s" for key, value in metrics.items()
    )
    if isinstance(tokens, int) and duration > 0:
        details += (
            f" | output_tokens={tokens}"
            f" | tokens_per_second={tokens / duration:.2f}"
        )
    if details:
        LOGGER.info("METRICS llm.generate | %s", details)
