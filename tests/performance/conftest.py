"""Persist explicit offline/live timing samples without benchmark plugins."""

import json
from pathlib import Path
import statistics

import pytest

MEASUREMENTS = []


def report_name():
    live = any(row["model_mode"] == "local_ollama" for row in MEASUREMENTS)
    return "test-speed-live.json" if live else "test-speed.json"


@pytest.fixture(scope="session")
def record_timing():
    records = MEASUREMENTS
    records.clear()

    def record(name, samples, *, model_mode, **details):
        row = {
            "case": name, "model_mode": model_mode,
            "samples_seconds": samples,
            "median_seconds": statistics.median(samples),
            "max_seconds": max(samples), **details,
        }
        records.append(row)
        return row

    yield record
    destination = (
        Path(__file__).resolve().parents[2]
        / "reports" / "performance" / report_name()
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"answer_mode": "llm_generated", "measurements": records},
                   indent=2) + "\n",
        encoding="utf-8",
    )


def pytest_terminal_summary(terminalreporter):
    if not MEASUREMENTS:
        return
    terminalreporter.write_sep("-", "Performance measurements (seconds)")
    for row in MEASUREMENTS:
        terminalreporter.write_line(
            f"{row['case']} [{row['model_mode']}]: "
            f"median={row['median_seconds']:.4f}, "
            f"max={row['max_seconds']:.4f}"
        )
    terminalreporter.write_line(f"Report: reports/performance/{report_name()}")
