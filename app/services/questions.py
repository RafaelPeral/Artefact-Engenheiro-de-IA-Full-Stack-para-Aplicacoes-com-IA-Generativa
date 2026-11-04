"""Coordinate classification, retrieval and generation without an HTTP dependency."""

import json
from threading import Lock
from typing import Callable

from httpx import TimeoutException
from ollama import ResponseError

from app.core.tracing import LOGGER, trace_step
from app.domain.errors import (
    InferenceFailure, InferenceTimeout, InferenceUnavailable, PolicyUnavailable,
)
from app.domain.questions import QuestionResult, QuestionType
from app.prompts import load_prompt
from app.services.classification import classify_question
from app.services.responses import ANSWERS
from app.tools.pdf_search import PdfLookupError, consult_pdf


PDF_ANSWER_PROMPT = load_prompt("pdf_answer.md")
PDF_TYPES = {QuestionType.PDF, QuestionType.DATABASE_AND_PDF}
LOOKUP_TYPES = PDF_TYPES | {QuestionType.DATABASE}


class QuestionService:
    """Handle questions and serialize local inference to respect the memory budget."""

    def __init__(self, generate_response: Callable, pdf_lookup: Callable | None = None):
        self._generate = generate_response
        self._pdf_lookup = pdf_lookup if pdf_lookup is not None else consult_pdf
        self._inference_lock = Lock()

    def answer(self, question: str) -> QuestionResult:
        """Return a domain result; translate provider failures into application errors."""
        question = question.strip()
        with trace_step("service.question"):
            try:
                with self._inference_lock:
                    question_type = classify_question(question, self._generate)
                    LOGGER.info("Question route | type=%s", question_type.value)
                    if question_type in PDF_TYPES:
                        evidence = self._pdf_lookup(question)
                        answer = self._generate(
                            json.dumps({"question": question, "pdf_tool_result": evidence},
                                       ensure_ascii=False),
                            PDF_ANSWER_PROMPT,
                        )
                        if question_type == QuestionType.DATABASE_AND_PDF:
                            answer += "\n\n" + ANSWERS[QuestionType.DATABASE]
                    else:
                        answer = ANSWERS[question_type]
            except PdfLookupError as error:
                LOGGER.exception("Policy PDF lookup failed")
                raise PolicyUnavailable("Store policy document is unavailable") from error
            except TimeoutException as error:
                raise InferenceTimeout("Local model request timed out") from error
            except ConnectionError as error:
                raise InferenceUnavailable("Local model server is unavailable") from error
            except (ResponseError, RuntimeError) as error:
                LOGGER.exception("Local model could not answer the question")
                raise InferenceFailure("Local model inference failed") from error
            return QuestionResult(answer, question_type, question_type in LOOKUP_TYPES)
