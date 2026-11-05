"""Initialize the local language model for reuse by the application."""

import math
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ollama import Client, ResponseError

from .server import ollama_server
from app.core.tracing import LOGGER, log_inference_metrics, trace_step
from app.prompts import load_prompt


@contextmanager
def initialize_llm(
    model: str = "llama3.2:3b",
    context_size: int = 2048,
    timeout: float = 120.0,
    startup_timeout: float = 30.0,
) -> Iterator[Callable[..., str]]:
    """Manage the local LLM for the lifetime of an application context.

    Use with initialize_llm() as generate_response during application startup.
    Start Ollama in the background if its local API is unavailable.
    Ollama and the model must be installed; use make install and make model.
    Importing this module does not load the model or contact the server.

    Args:
        model: Installed Ollama model name.
        context_size: Context budget including input and generated tokens.
        timeout: HTTP request timeout in seconds.
        startup_timeout: Maximum server readiness wait in seconds.

    Yields:
        A function accepting a user message and an optional system prompt.
        Each call is independent and returns the generated response text.

    Raises:
        ValueError: A configuration value is empty or out of range.
        RuntimeError: Ollama is unavailable or cannot load the model.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if isinstance(context_size, bool) or not isinstance(context_size, int):
        raise ValueError("context_size must be an integer")
    if not 512 <= context_size <= 4096:
        raise ValueError("context_size must be between 512 and 4096")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if not math.isfinite(startup_timeout) or startup_timeout <= 0:
        raise ValueError("startup_timeout must be finite and positive")

    model = model.strip()
    LOGGER.info(
        "LLM configuration | model=%s | context=%s | timeout=%.1fs",
        model, context_size, timeout,
    )
    with ollama_server(startup_timeout):
        client = Client(
            host="http://127.0.0.1:11434", timeout=timeout, trust_env=False
        )
        options = {
            "num_ctx": context_size,
            "num_predict": 256,
            "temperature": 0,
        }

        try:
            try:
                with trace_step("llm.check_model"):
                    client.show(model=model)
                # An empty prompt preloads the model without generating text.
                with trace_step("llm.load"):
                    client.generate(
                        model=model,
                        prompt="",
                        stream=False,
                        keep_alive=-1,
                        options=options,
                    )
            except ConnectionError as error:
                raise RuntimeError(
                    "Lost connection to local Ollama after startup. "
                    "Check the Ollama server logs and retry initialization."
                ) from error
            except ResponseError as error:
                if error.status_code == 404:
                    raise RuntimeError(
                        f"Model '{model}' is not installed. "
                        "Run make model first."
                    ) from error
                raise RuntimeError(
                    f"Ollama could not load '{model}': {error}"
                ) from error

            def generate_response(
                message: str,
                system_prompt: str | None = None,
                *,
                response_format: dict | None = None,
            ) -> str:
                """Generate a response using the model selected at startup."""
                if not isinstance(message, str) or not message.strip():
                    raise ValueError("message must be a non-empty string")
                if system_prompt is None:
                    system_prompt = load_prompt("assistant.md")
                properties = (response_format or {}).get("properties", {})
                operation = "llm.generate"
                if "question_type" in properties:
                    operation = "llm.classify"
                elif "entity" in properties:
                    operation = "llm.plan"
                with trace_step(operation):
                    response = client.chat(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": message.strip()},
                        ],
                        stream=False,
                        keep_alive=-1,
                        options=options,
                        **({"format": response_format} if response_format is not None else {}),
                    )
                    log_inference_metrics(response, operation=operation)
                    content = response.message.content
                    LOGGER.info(
                        "LLM_OUTPUT | mode=%s | content=%s",
                        "structured" if response_format is not None else "text",
                        json.dumps(content, ensure_ascii=False),
                    )
                    if not content or not content.strip():
                        raise RuntimeError(
                            "The local model returned no response"
                        )
                    return content.strip()

            yield generate_response
        finally:
            try:
                try:
                    with trace_step("llm.unload"):
                        client.generate(model=model, keep_alive=0)
                except Exception:
                    LOGGER.warning("Model unload failed during shutdown", exc_info=True)
            finally:
                with trace_step("llm.client_close"):
                    client.close()
