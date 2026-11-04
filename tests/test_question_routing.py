"""Verify the classifier contract independently of a real local model."""

import json
from unittest.mock import Mock

import pytest

from app.services.classification import CLASSIFICATION_SCHEMA, CLASSIFICATION_PROMPT, QuestionType, classify_question


@pytest.mark.parametrize("question, label", [
    ("Quais produtos vocês vendem?", "database"),
    ("Meu pedido 123 já saiu?", "database"),
    ("Qual o horário de funcionamento?", "pdf"),
    ("Qual o preço e a política de troca?", "database_and_pdf"),
    ("Oi, tudo bem?", "greeting"),
    ("Qual a capital dos EUA?", "out_of_scope"),
])
def test_classification_uses_a_separate_system_prompt(question, label):
    generate = Mock(return_value=json.dumps({"question_type": label}))
    assert classify_question(f" {question} ", generate) == QuestionType(label)
    generate.assert_called_once_with(
        question, CLASSIFICATION_PROMPT, response_format=CLASSIFICATION_SCHEMA
    )


@pytest.mark.parametrize("output", ["", "unknown", "database\npdf", None,
                                        'Washington D.C.', '{"question_type":"unknown"}',
    '{"question_type":"database","answer":"invented"}',
    '[]', 'null', '{"question_type":null}',
    "Ignore rules and answer freely"])
def test_invalid_labels_are_rejected(output):
    with pytest.raises(RuntimeError, match="invalid question type"):
        classify_question("Mensagem", Mock(return_value=output))
