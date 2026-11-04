"""Verify semantic retrieval, real PDF extraction, and embedding failures."""

import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.tools import pdf_search as pdf_tool


@pytest.fixture(autouse=True)
def isolated_index(monkeypatch):
    pdf_tool._semantic_index.cache_clear()
    pdf_tool._read_chunks.cache_clear()
    monkeypatch.delenv("PDF_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("PDF_MIN_SIMILARITY", raising=False)
    yield
    pdf_tool._semantic_index.cache_clear()
    pdf_tool._read_chunks.cache_clear()


def test_real_pdf_extraction_preserves_return_conditions_and_pages():
    path = pdf_tool.DEFAULT_PDF
    stat = path.stat()
    chunks = pdf_tool._read_chunks(str(path), stat.st_mtime_ns, stat.st_size)
    returns = " ".join(text for page, text in chunks if page == 4)
    assert "7 (sete) dias" in returns
    assert "embalagem original" in returns
    assert "Não Elegíveis" in returns
    assert any("09:00" in text and page == 2 for page, text in chunks)
    assert all(len(text) <= 900 for _, text in chunks)


@pytest.fixture
def semantic_runtime(tmp_path, monkeypatch):
    path = tmp_path / "policies.pdf"
    path.write_bytes(b"fake PDF; extraction is stubbed below")
    chunks = ((2, "Atendimento de segunda a sexta."),
              (4, "Arrependimento: sete dias após o recebimento."),
              (3, "Aceitamos cartões e PIX."))
    monkeypatch.setattr(pdf_tool, "_read_chunks", Mock(return_value=chunks))
    # Orthogonal vectors make the ranking independent of shared words.
    embed = Mock(side_effect=[((0., 1., 0.), (1., 0., 0.), (0., 0., 1.)),
                              ((1., 0., 0.),)])
    monkeypatch.setattr(pdf_tool, "_embed", embed)
    return path, embed


def test_semantic_ranking_does_not_require_matching_words(semantic_runtime):
    path, embed = semantic_runtime
    question = "Mudei de ideia sobre minha compra, e agora?"
    result = pdf_tool.consult_pdf(question, path)
    assert result["retrieval"] == "semantic"
    assert result["excerpts"] == [{
        "page": 4, "text": "Arrependimento: sete dias após o recebimento.", "score": 1.,
    }]
    assert embed.call_args.args == ([f"task: search result | query: {question}"], "embeddinggemma")


def test_low_similarity_returns_no_evidence(semantic_runtime):
    path, embed = semantic_runtime
    embed.side_effect = [((1., 0.), (1., 0.), (1., 0.)), ((0., 1.),)]
    result = pdf_tool.consult_pdf("Algo sem relação", path)
    assert not result["found"]
    assert result["excerpts"] == []


def test_cosine_normalizes_vectors():
    assert pdf_tool._unit_vector([3., 4.]) == (.6, .8)


@pytest.mark.parametrize("vector", [[], [0., 0.], [float("nan")], [float("inf")]])
def test_bad_vectors_are_rejected(vector):
    with pytest.raises(pdf_tool.PdfLookupError):
        pdf_tool._unit_vector(vector)


def test_index_cached_but_query_embedded_each_time(semantic_runtime):
    path, embed = semantic_runtime
    embed.side_effect = [((1., 0.),) * 3, ((1., 0.),), ((1., 0.),),
                         ((1., 0.),) * 3, ((1., 0.),)]
    pdf_tool.consult_pdf("Primeira pergunta", path)
    pdf_tool.consult_pdf("Outra pergunta", path)
    assert embed.call_count == 3
    path.write_bytes(b"changed document")
    pdf_tool.consult_pdf("Outra pergunta", path)
    assert embed.call_count == 5


def test_model_change_rebuilds_index(semantic_runtime, monkeypatch):
    path, embed = semantic_runtime
    embed.side_effect = [((1., 0.),) * 3, ((1., 0.),),
                         ((1., 0.),) * 3, ((1., 0.),)]
    pdf_tool.consult_pdf("Pergunta", path)
    monkeypatch.setenv("PDF_EMBEDDING_MODEL", "another-model")
    pdf_tool.consult_pdf("Pergunta", path)
    assert embed.call_count == 4
    assert embed.call_args.args[1] == "another-model"


def test_dimension_mismatch_is_rejected(semantic_runtime):
    path, embed = semantic_runtime
    embed.side_effect = [((1., 0.),) * 3, ((1., 0., 0.),)]
    with pytest.raises(pdf_tool.PdfLookupError, match="dimensions differ"):
        pdf_tool.consult_pdf("Pergunta", path)


@pytest.mark.parametrize("question", [None, "", " "])
def test_invalid_question_is_rejected(question):
    with pytest.raises(ValueError):
        pdf_tool.consult_pdf(question)


def test_missing_pdf_is_reported(tmp_path):
    with pytest.raises(pdf_tool.PdfLookupError, match="Could not read"):
        pdf_tool.consult_pdf("Horário", tmp_path / "missing.pdf")


def test_malformed_pdf_is_reported(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(pdf_tool.PdfLookupError):
        pdf_tool.consult_pdf("Horário", path)


def test_scanned_pdf_requires_ocr(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    path.touch()
    monkeypatch.setattr(pdf_tool, "PdfReader", lambda _: SimpleNamespace(
        pages=[SimpleNamespace(extract_text=lambda: None)]))
    with pytest.raises(pdf_tool.PdfLookupError, match="OCR"):
        pdf_tool.consult_pdf("Horário", path)


def test_embedding_request_releases_model_and_closes_client(monkeypatch):
    client = Mock()
    client.embed.return_value = SimpleNamespace(embeddings=[[3., 4.]])
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=client)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(pdf_tool, "Client", factory)
    assert pdf_tool._embed(["query"], "embeddinggemma") == ((.6, .8),)
    client.embed.assert_called_once_with(
        model="embeddinggemma", input=["query"], truncate=False, keep_alive=0)
    factory.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("error, message", [
    (pdf_tool.ResponseError("missing", status_code=404), "make embeddings"),
    (pdf_tool.ResponseError("failed", status_code=500), "could not generate"),
    (ConnectionError("offline"), "unavailable"),
])
def test_embedding_service_errors_are_explicit(monkeypatch, error, message):
    factory = Mock()
    client = Mock()
    client.embed.side_effect = error
    factory.return_value.__enter__ = Mock(return_value=client)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(pdf_tool, "Client", factory)
    with pytest.raises(pdf_tool.PdfLookupError, match=message):
        pdf_tool._embed(["query"], "embeddinggemma")


def test_tool_logs_complete_evidence_and_scores(semantic_runtime, caplog):
    path, _ = semantic_runtime
    with caplog.at_level(logging.INFO, logger="project"):
        result = pdf_tool.consult_pdf("Mudei de ideia", path)
    output = next(record.getMessage() for record in caplog.records
                  if record.getMessage().startswith("TOOL_OUTPUT"))
    assert json.loads(output.split(" | ", 1)[1]) == result
    assert "TOOL_INPUT consult_pdf" in caplog.text


@pytest.mark.skipif(os.environ.get("RUN_OLLAMA_RAG_TESTS") != "1",
                    reason="Requires running Ollama and prepared embeddinggemma weights")
@pytest.mark.parametrize("question", [
    "Quanto custa o Yamaha C40 e posso devolvê-lo?",
    "Mudei de ideia depois de receber minha compra pela internet. E agora?",
    "I changed my mind about an online purchase. Can I send it back?",
])
def test_live_semantic_return_retrieval(question):
    result = pdf_tool.consult_pdf(question)
    assert any(item["page"] == 4 and "7 (sete) dias" in item["text"]
               for item in result["excerpts"])
