"""Handle classified questions independently of the HTTP interface."""
from threading import Lock
from typing import Callable
from httpx import TimeoutException
from ollama import ResponseError
from app.core.tracing import LOGGER, trace_step
from app.domain.errors import InferenceFailure, InferenceTimeout, InferenceUnavailable
from app.domain.questions import QuestionResult, QuestionType
from app.services.classification import classify_question
from app.services.responses import ANSWERS

class QuestionService:
    def __init__(self, generate_response: Callable):
        self._generate = generate_response
        self._inference_lock = Lock()

    def answer(self, question: str) -> QuestionResult:
        with trace_step("service.question"):
            try:
                with self._inference_lock:
                    question_type = classify_question(question.strip(), self._generate)
                    LOGGER.info("Question route | type=%s", question_type.value)
                    answer = ANSWERS[question_type]
            except TimeoutException as error:
                raise InferenceTimeout("Local model request timed out") from error
            except ConnectionError as error:
                raise InferenceUnavailable("Local model server is unavailable") from error
            except (ResponseError, RuntimeError) as error:
                LOGGER.exception("Local model could not answer the question")
                raise InferenceFailure("Local model inference failed") from error
            return QuestionResult(answer, question_type, question_type in {
                QuestionType.DATABASE, QuestionType.PDF, QuestionType.DATABASE_AND_PDF,
            })
