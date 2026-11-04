"""Expose local LLM inference through a single FastAPI question endpoint."""

from contextlib import asynccontextmanager
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from httpx import TimeoutException
from ollama import ResponseError
from pydantic import BaseModel

from .initialize_llm import initialize_llm
from .question_routing import ANSWERS, QuestionType, classify_question
from .tracing import LOGGER, configure_logging, trace_step


class QuestionResponse(BaseModel):
    """Return the route and a fixed response until retrieval is implemented."""

    answer: str
    question_type: QuestionType
    requires_lookup: bool


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize the LLM once and release owned resources on shutdown."""
    configure_logging()
    with initialize_llm() as generate_response:
        application.state.generate_response = generate_response
        application.state.inference_lock = Lock()
        LOGGER.info("API ready | POST /questions")
        try:
            yield
        finally:
            del application.state.generate_response
            del application.state.inference_lock
            LOGGER.info("API shutting down; releasing LLM resources")


app = FastAPI(
    title="Store Assistant API",
    lifespan=lifespan,
    redoc_url=None,
)


@app.post("/questions", response_model=QuestionResponse)
def ask_question(
    request: Request,
    question: Annotated[
        str,
        Query(
            min_length=1,
            pattern=r"\S",
            description="Write your question here.",
        ),
    ],
) -> QuestionResponse:
    """Classify the question and respond without executing data lookups."""
    with trace_step("api.question"):
        try:
            # Serialize inference to fit the target machine's memory budget.
            # A sync endpoint keeps blocking inference off the event loop.
            with request.app.state.inference_lock:
                question_type = classify_question(
                    question, request.app.state.generate_response
                )
        except TimeoutException as error:
            raise HTTPException(
                504, "Local model request timed out"
            ) from error
        except ConnectionError as error:
            raise HTTPException(
                503, "Local model server is unavailable"
            ) from error
        except (ResponseError, RuntimeError) as error:
            LOGGER.exception("Local model could not answer the question")
            raise HTTPException(502, "Local model inference failed") from error
        LOGGER.info("Question route | type=%s", question_type.value)
        return QuestionResponse(
            answer=ANSWERS[question_type],
            question_type=question_type,
            requires_lookup=question_type in {
                QuestionType.DATABASE, QuestionType.PDF,
                QuestionType.DATABASE_AND_PDF,
            },
        )
