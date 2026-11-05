"""Classify store questions without querying data or generating factual answers."""

import json
import re

from app.domain.questions import QuestionIntent, QuestionType
from app.prompts import load_prompt

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "question_type": {
            "type": "string", "enum": [kind.value for kind in QuestionType],
        },
        "database_question": {"type": ["string", "null"]},
        "policy_question": {"type": ["string", "null"]},
    },
    "required": ["question_type", "database_question", "policy_question"],
    "additionalProperties": False,
}


CLASSIFICATION_PROMPT = load_prompt("classification.md")


def classify_request(question, generate_response):
    """Use the model for intent only; reject unexpected classification output."""
    label = generate_response(
        question.strip(), CLASSIFICATION_PROMPT,
        response_format=CLASSIFICATION_SCHEMA,
    )
    try:
        payload = json.loads(label)
        if not isinstance(payload, dict) or set(payload) - {
            "question_type", "database_question", "policy_question",
        }:
            raise ValueError("Unexpected classification fields")
        question_type = QuestionType(payload["question_type"])
        scopes = [payload.get("database_question"), payload.get("policy_question")]
        if any(scope is not None and (
            not isinstance(scope, str) or not scope.strip()
        ) for scope in scopes):
            raise ValueError("Invalid scoped question")
        if scopes[0]:
            pattern = r"\b(?:pedido|order)\s*#?\s*(\d+)\b"
            original_ids = set(re.findall(pattern, question, re.IGNORECASE))
            scoped_ids = set(re.findall(pattern, scopes[0], re.IGNORECASE))
            if original_ids - scoped_ids:
                raise ValueError("The scoped request omitted an order identifier")
        return QuestionIntent(question_type, *scopes)
    except (ValueError, TypeError, KeyError) as error:
        raise RuntimeError("Local model returned an invalid question type") from error


def classify_question(question, generate_response):
    """Expose the route alone for callers that do not need scoped requests."""
    return classify_request(question, generate_response).question_type
