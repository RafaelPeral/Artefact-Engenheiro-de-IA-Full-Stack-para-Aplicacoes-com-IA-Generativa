"""Retrieve bounded, page-labelled excerpts from the store policy PDF."""

from functools import lru_cache
import json
import math
import os
from pathlib import Path

from ollama import Client, ResponseError
from pypdf import PdfReader

from app.core.tracing import LOGGER, trace_step


DEFAULT_PDF = Path(__file__).resolve().parents[2] / "data" / "políticas_da_loja.pdf"
EMBEDDING_MODEL = "embeddinggemma"


class PdfLookupError(RuntimeError):
    """The configured policy document cannot be read."""


def _unit_vector(vector):
    if not vector or any(not math.isfinite(value) for value in vector):
        raise PdfLookupError("Invalid embedding vector")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise PdfLookupError("Empty embedding vector")
    return tuple(value / norm for value in vector)


def _embed(texts, model):
    with trace_step("pdf.embed"):
        try:
            with Client(host="http://127.0.0.1:11434", timeout=120, trust_env=False) as client:
                # Release embedding weights before the generation model runs.
                response = client.embed(model=model, input=texts, truncate=False, keep_alive=0)
            if len(response.embeddings) != len(texts):
                raise PdfLookupError("Embedding count does not match input count")
            return tuple(_unit_vector(vector) for vector in response.embeddings)
        except ResponseError as error:
            if error.status_code == 404:
                raise PdfLookupError(
                    f"Embedding model '{model}' is not installed. Run make embeddings."
                ) from error
            raise PdfLookupError("Ollama could not generate PDF embeddings") from error
        except PdfLookupError:
            raise
        except Exception as error:
            raise PdfLookupError("PDF embedding service is unavailable") from error


@lru_cache(maxsize=4)
def _read_chunks(path, modified, size):
    # Metadata in the cache key invalidates extraction when the file changes.
    reader = PdfReader(path)
    chunks = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = " ".join((page.extract_text() or "").split())
        for offset in range(0, len(text), 700):
            excerpt = text[offset:offset + 900]
            if excerpt.strip():
                chunks.append((page_number, excerpt))
    if not chunks:
        raise PdfLookupError("Policy PDF contains no extractable text; OCR is required")
    return tuple(chunks)


@lru_cache(maxsize=4)
def _semantic_index(path, modified, size, model):
    with trace_step("pdf.index"):
        chunks = _read_chunks(path, modified, size)
        documents = [f"title: Políticas da Empório da Música | text: {text}"
                     for _, text in chunks]
        vectors = _embed(documents, model)
        if len({len(vector) for vector in vectors}) != 1:
            raise PdfLookupError("Inconsistent embedding dimensions")
        return chunks, vectors


def consult_pdf(question: str, pdf_path=None) -> dict:
    """Tool: search policies and return evidence, never a generated answer."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    path = Path(pdf_path) if pdf_path is not None else DEFAULT_PDF
    with trace_step("tool.consult_pdf"):
        try:
            model = os.environ.get("PDF_EMBEDDING_MODEL", EMBEDDING_MODEL)
            minimum = float(os.environ.get("PDF_MIN_SIMILARITY", "0.3"))
            if not math.isfinite(minimum) or not -1 <= minimum <= 1:
                raise ValueError("PDF_MIN_SIMILARITY must be between -1 and 1")
            metadata = path.stat()
            chunks, vectors = _semantic_index(
                str(path.resolve()), metadata.st_mtime_ns, metadata.st_size, model
            )
            query = _embed([f"task: search result | query: {question.strip()}"], model)[0]
            if len(query) != len(vectors[0]):
                raise PdfLookupError("Query and document embedding dimensions differ")
        except PdfLookupError:
            raise
        except Exception as error:
            raise PdfLookupError("Could not read the store policy PDF") from error
        ranked = sorted(
            ((sum(a * b for a, b in zip(query, vector)), page, index, text)
             for index, ((page, text), vector) in enumerate(zip(chunks, vectors))),
            key=lambda item: (-item[0], item[2]),
        )
        excerpts = [
            {"page": page, "text": text, "score": round(score, 6)}
            for score, page, _, text in ranked[:3] if score >= minimum
        ]
        result = {"source": path.name, "excerpts": excerpts, "found": bool(excerpts),
                  "retrieval": "semantic", "embedding_model": model,
                  "minimum_similarity": minimum,
                  "candidate_scores": [round(item[0], 6) for item in ranked[:3]]}
        return result
