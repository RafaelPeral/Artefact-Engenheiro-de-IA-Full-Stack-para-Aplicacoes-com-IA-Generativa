"""Download model weights independently from application startup."""

from ollama import Client

from .server import ollama_server
from app.core.tracing import trace_step


def prepare_model(model: str = "llama3.2:3b") -> None:
    """Download weights, then release the temporary server and client.

    Existing servers are reused and left running. No inference is performed.
    Streaming avoids applying the inference timeout to a large download.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    model = model.strip()
    with ollama_server():
        with Client(
            host="http://127.0.0.1:11434", timeout=None, trust_env=False
        ) as client:
            with trace_step("model.download"):
                previous_status = None
                for progress in client.pull(model=model, stream=True):
                    if progress.status != previous_status:
                        print(progress.status, flush=True)
                        previous_status = progress.status
            client.show(model=model)
    print(f"Model ready: {model}")
