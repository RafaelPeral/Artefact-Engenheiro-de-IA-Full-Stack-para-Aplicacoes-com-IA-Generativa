"""Classify store questions without querying data or generating factual answers."""

import json

from app.domain.questions import QuestionType
from app.prompts import load_prompt

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "question_type": {
            "type": "string", "enum": [kind.value for kind in QuestionType],
        },
    },
    "required": ["question_type"],
    "additionalProperties": False,
}


CLASSIFICATION_PROMPT = load_prompt("classification.md")


def classify_question(question, generate_response):
    """Use the model for intent only; reject unexpected classification output."""
    label = generate_response(
        question.strip(), CLASSIFICATION_PROMPT,
        response_format=CLASSIFICATION_SCHEMA,
    )
    try:
        payload = json.loads(label)
        if not isinstance(payload, dict) or set(payload) != {"question_type"}:
            raise ValueError("Expected only question_type")
        return QuestionType(payload["question_type"])
    except (ValueError, TypeError) as error:
        raise RuntimeError("Local model returned an invalid question type") from error
