"""Opt-in real-model stage benchmark: python -m scripts.benchmark --help."""

import argparse
import asyncio
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile
from time import perf_counter
from unittest.mock import patch


CASE = ContextVar("benchmark_case", default="setup")
ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "greeting": ("Oi, tudo bem?", "greeting"),
    "out_of_scope": ("Qual é a capital dos Estados Unidos?", "out_of_scope"),
    "database": ("Quais produtos vocês vendem?", "database"),
    "pdf": ("Qual o horário de funcionamento?", "pdf"),
    "mixed": ("Quanto custa o Yamaha C40 e posso devolvê-lo?", "database_and_pdf"),
}


class Recorder:
    def __init__(self):
        self.samples = []

    @contextmanager
    def measure(self, stage, **details):
        sample = {"case": CASE.get(), "stage": stage, **details}
        started = perf_counter()
        try:
            yield sample
        except BaseException as error:
            sample["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            sample["seconds"] = perf_counter() - started
            self.samples.append(sample)

    def instrument_ollama(self, method, original, retention=None):
        def call(client, *args, **kwargs):
            if retention is not None and (method in {"chat", "embed"} or
                                          (method == "generate" and "prompt" in kwargs)):
                kwargs["keep_alive"] = retention
            kind = ("classification" if kwargs.get("format") else "answer") if method == "chat" else method
            with self.measure(f"ollama.{kind}", model=kwargs.get("model"),
                              keep_alive=kwargs.get("keep_alive")) as sample:
                response = original(client, *args, **kwargs)
                for field in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
                    value = getattr(response, field, None)
                    if isinstance(value, (int, float)):
                        sample[field + "_seconds"] = value / 1_000_000_000
                for field in ("prompt_eval_count", "eval_count"):
                    value = getattr(response, field, None)
                    if isinstance(value, int):
                        sample[field] = value
                if method == "embed":
                    value = kwargs.get("input")
                    sample["input_count"] = 1 if isinstance(value, str) else len(value)
                if method in {"chat", "embed"}:
                    sample["loaded_models_after"] = client.ps().model_dump(mode="json")
                return response
        return call

    def summary(self):
        groups = defaultdict(list)
        for sample in self.samples:
            groups[sample["stage"]].append(sample["seconds"])
        return {stage: {"count": len(values), "median_seconds": statistics.median(values),
                        "min_seconds": min(values), "max_seconds": max(values)}
                for stage, values in sorted(groups.items())}


def gpu_snapshot():
    try:
        result = subprocess.run([
            "nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
            "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        return result.stdout.strip() or result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return str(error)


async def benchmark(args, recorder):
    with recorder.measure("python.application_imports"):
        from httpx import ASGITransport, AsyncClient
        from ollama import Client
        from app.core.tracing import LOGGER
        from app.infrastructure.database.sqlite import create_database
        from app.tools import pdf_search
        api = importlib.import_module("app.app")

    # Keep timing and metrics in memory; avoid console payload output in this harness.
    LOGGER.setLevel("WARNING")
    report = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "platform": platform.platform(),
        "rounds": args.rounds, "cases": args.cases, "gpu_before": gpu_snapshot(),
        "experimental_overrides": {"generation_keep_alive": args.keep_alive,
                                   "embedding_keep_alive": args.embed_keep_alive},
        "configuration": {key: os.environ.get(key) for key in (
            "LLM_KEEP_ALIVE", "PDF_EMBED_KEEP_ALIVE", "PDF_MIN_SIMILARITY", "PDF_EMBEDDING_MODEL")},
        "requests": [],
        "notes": ["Sequential local requests via ASGITransport; excludes network transit.",
                  "Nested stage timings are inclusive and must not be added together.",
                  "First/warm refer to application caches, not a forced OS/model-cache reset.",
                  "Small sample; no throughput or p95 claim. Startup never downloads weights."],
    }
    with Client(host="http://127.0.0.1:11434", timeout=120, trust_env=False) as client:
        report["initial_loaded_models"] = client.ps().model_dump(mode="json")
        report["available_models"] = client.list().model_dump(mode="json")

    cache_root = ROOT / ".pytest_cache"
    cache_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="benchmark-", dir=cache_root) as directory:
        database = Path(directory) / "store.db"
        for phase in ("first", "repeat"):
            CASE.set(f"sqlite.{phase}")
            with recorder.measure("sqlite.import"):
                create_database(database_path=database)

    path = pdf_search.DEFAULT_PDF
    metadata = path.stat()
    pdf_search._read_chunks.cache_clear()
    pdf_search._semantic_index.cache_clear()
    for phase in ("first", "cached"):
        CASE.set(f"pdf.extract.{phase}")
        with recorder.measure("pdf.extraction") as sample:
            chunks = pdf_search._read_chunks(str(path.resolve()), metadata.st_mtime_ns, metadata.st_size)
            sample["chunks"] = len(chunks)
    # Restore a first-use extraction state for the actual request pipeline.
    pdf_search._read_chunks.cache_clear()

    with ExitStack() as stack:
        for method in ("chat", "generate", "embed", "show"):
            original = getattr(Client, method)
            retention = args.embed_keep_alive if method == "embed" else args.keep_alive
            stack.enter_context(patch.object(Client, method, recorder.instrument_ollama(method, original, retention)))
        for module_name in ("app.infrastructure.llm.server", "app.infrastructure.llm.client",
                            "app.tools.pdf_search", "app.services.questions", "app.app"):
            module = importlib.import_module(module_name)
            original = module.trace_step

            @contextmanager
            def trace(name, heartbeat_interval=5., _original=original):
                with recorder.measure(name), _original(name, heartbeat_interval):
                    yield

            stack.enter_context(patch.object(module, "trace_step", trace))
        # Suppress normal logging setup just for the benchmark process.
        lifecycle = importlib.import_module("app.api.lifespan")
        stack.enter_context(patch.object(lifecycle, "configure_logging", lambda: None))
        CASE.set("startup")
        context = api.app.router.lifespan_context(api.app)
        with recorder.measure("application.startup"):
            await context.__aenter__()
        try:
            async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://benchmark") as client:
                for case in args.cases:
                    question, expected = CASES[case]
                    for number in range(args.rounds):
                        case_id = f"{case}.{number + 1}"
                        CASE.set(case_id)
                        with recorder.measure("http.request") as sample:
                            response = await client.post("/questions", params={"question": question})
                            sample["status_code"] = response.status_code
                        body = response.json()
                        report["requests"].append({
                            "case": case_id, "question": question, "expected_type": expected,
                            "status_code": response.status_code, "response": body,
                            "seconds": sample["seconds"],
                        })
                        print(f"{case_id}: {sample['seconds']:.3f}s | HTTP {response.status_code}", flush=True)
        finally:
            CASE.set("shutdown")
            with recorder.measure("application.shutdown"):
                await context.__aexit__(None, None, None)
    report["gpu_after"] = gpu_snapshot()
    with Client(host="http://127.0.0.1:11434", timeout=120, trust_env=False) as client:
        report["final_loaded_models"] = client.ps().model_dump(mode="json")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--output", type=Path, default=ROOT / "reports/performance/baseline.json")
    parser.add_argument("--keep-alive", help="Experiment: override generation retention (e.g. 5m)")
    parser.add_argument("--embed-keep-alive", help="Experiment: override embedding retention (e.g. 5m)")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    recorder = Recorder()
    try:
        report = asyncio.run(benchmark(args, recorder))
    except Exception as error:
        report = {"error": f"{type(error).__name__}: {error}"}
    report["samples"] = recorder.samples
    report["summary"] = recorder.summary()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output}", flush=True)
    if "error" in report or any(request["status_code"] != 200 for request in report.get("requests", [])):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
