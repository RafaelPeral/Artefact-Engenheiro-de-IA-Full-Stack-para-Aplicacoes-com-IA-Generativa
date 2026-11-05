"""Prepare required model weights with one owned temporary Ollama server."""

import argparse
import os

from ollama import Client, ResponseError

from app.core.tracing import configure_logging, trace_step
from app.infrastructure.llm.preparation import prepare_model
from app.infrastructure.llm.server import ollama_server


def prepare_models(only=None):
    configure_logging()
    models = {
        "model": "llama3.2:3b",
        "embeddings": os.environ.get("PDF_EMBEDDING_MODEL", "embeddinggemma"),
    }
    selected = [models[only]] if only else list(models.values())
    print("Preparing weights only; no API or inference.", flush=True)
    with trace_step("setup.models"), ollama_server():
        with Client(host="http://127.0.0.1:11434", timeout=30,
                    trust_env=False) as client:
            for model in selected:
                try:
                    client.show(model=model)
                except ResponseError as error:
                    if error.status_code != 404:
                        raise
                    print(f"Downloading missing model: {model}", flush=True)
                    prepare_model(model)
                else:
                    print(f"Model installed; skipping download: {model}",
                          flush=True)
    print("Models prepared; owned temporary server released.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["model", "embeddings"])
    prepare_models(parser.parse_args().only)
