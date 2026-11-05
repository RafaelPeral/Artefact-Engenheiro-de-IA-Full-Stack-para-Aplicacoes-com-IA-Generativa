"""Coordinate classification, retrieval, and evidence-grounded generation."""

from functools import lru_cache
from threading import Lock
from typing import Callable

from httpx import TimeoutException
from ollama import ResponseError

from app.core.tracing import LOGGER, trace_step
from app.domain.errors import (
    DatabaseUnavailable, InferenceFailure, InferenceTimeout,
    InferenceUnavailable, PolicyUnavailable,
)
from app.domain.questions import QuestionResult, QuestionType
from app.prompts import load_prompt
from app.services.classification import classify_request
from app.services.responses import ANSWERS
from app.services.answer_generation import generate_lookup_answer
from app.tools.pdf_search import PdfLookupError, consult_pdf
from app.tools.database import (
    DatabaseLookupError, execute_database_plan, plan_database_query,
)


PDF_ANSWER_PROMPT = load_prompt("pdf_answer.md")
LOOKUP_ANSWER_PROMPT = load_prompt("lookup_answer.md")
MIXED_ANSWER_PROMPT = load_prompt("mixed_answer.md")
PDF_TYPES = {QuestionType.PDF, QuestionType.DATABASE_AND_PDF}
DATABASE_TYPES = {QuestionType.DATABASE, QuestionType.DATABASE_AND_PDF}
LOOKUP_TYPES = PDF_TYPES | {QuestionType.DATABASE}


class QuestionService:
    """Serialize inference and lookups within the local memory budget."""

    def __init__(self, generate_response: Callable,
                 pdf_lookup: Callable | None = None,
                 database_lookup: Callable | None = None):
        self._generate = generate_response
        # Cache interpretation only; rows, prices, and answers stay fresh.
        self._classify = lru_cache(maxsize=128)(
            lambda question: classify_request(question, self._generate)
        )
        self._plan = lru_cache(maxsize=128)(
            lambda question: plan_database_query(question, self._generate)
        )
        self._pdf_lookup = (
            pdf_lookup if pdf_lookup is not None else consult_pdf
        )
        self._database_lookup = (
            database_lookup if database_lookup is not None
            else self._lookup_database
        )
        self._inference_lock = Lock()

    def _lookup_database(self, question):
        hits = self._plan.cache_info().hits
        plan = self._plan(question)
        LOGGER.info("Database plan | cache_hit=%s",
                    self._plan.cache_info().hits > hits)
        return execute_database_plan(plan)

    def answer(self, question: str) -> QuestionResult:
        """Return domain results and translate provider failures."""
        question = question.strip()
        with trace_step("service.question"):
            try:
                with self._inference_lock:
                    hits = self._classify.cache_info().hits
                    intent = self._classify(question)
                    question_type = intent.question_type
                    LOGGER.info(
                        "Question route | type=%s | cache_hit=%s",
                        question_type.value,
                        self._classify.cache_info().hits > hits,
                    )
                    if question_type in LOOKUP_TYPES:
                        evidence = {"question": question}
                        mixed = question_type == QuestionType.DATABASE_AND_PDF
                        if mixed:
                            evidence.update(
                                database_question=intent.database_question,
                                policy_question=intent.policy_question,
                            )
                        if question_type in DATABASE_TYPES:
                            evidence["database_tool_result"] = (
                                self._database_lookup(
                                    (intent.database_question or question)
                                    if mixed else question
                                )
                            )
                        if question_type in PDF_TYPES:
                            evidence["pdf_tool_result"] = (
                                self._pdf_lookup(
                                    (intent.policy_question or question)
                                    if mixed else question
                                )
                            )
                        prompt = (
                            MIXED_ANSWER_PROMPT
                            if question_type == QuestionType.DATABASE_AND_PDF
                            else LOOKUP_ANSWER_PROMPT
                            if question_type in DATABASE_TYPES
                            else PDF_ANSWER_PROMPT
                        )
                        answer = generate_lookup_answer(
                            self._generate, evidence, prompt
                        )
                    else:
                        answer = ANSWERS[question_type]
            except DatabaseLookupError as error:
                LOGGER.exception("Store database lookup failed")
                raise DatabaseUnavailable(
                    "Store database is unavailable"
                ) from error
            except PdfLookupError as error:
                LOGGER.exception("Policy PDF lookup failed")
                raise PolicyUnavailable(
                    "Store policy document is unavailable"
                ) from error
            except TimeoutException as error:
                raise InferenceTimeout(
                    "Local model request timed out"
                ) from error
            except ConnectionError as error:
                raise InferenceUnavailable(
                    "Local model server is unavailable"
                ) from error
            except (ResponseError, RuntimeError) as error:
                LOGGER.exception("Local model could not answer the question")
                raise InferenceFailure(
                    "Local model inference failed"
                ) from error
            return QuestionResult(
                answer, question_type, question_type in LOOKUP_TYPES
            )
