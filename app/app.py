"""HTTP interface for the store assistant; business logic lives in services."""

from typing import Annotated

from fastapi import Depends, FastAPI, Query

from app.api.dependencies import get_question_service
from app.api.error_handlers import register_error_handlers
from app.api.lifespan import lifespan
from app.api.schemas import QuestionResponse
from app.core.tracing import LOGGER, trace_step
from app.services.questions import QuestionService


app = FastAPI(title="Store Assistant API", lifespan=lifespan, redoc_url=None)
register_error_handlers(app)


@app.post("/questions", response_model=QuestionResponse)
def ask_question(
    question: Annotated[str, Query(
        min_length=1, pattern=r"\S", description="Write your question here.",
    )],
    service: Annotated[QuestionService, Depends(get_question_service)],
) -> QuestionResponse:
    """Validate HTTP input, delegate to the use case, and serialize its result."""
    with trace_step("api.question"):
        result = service.answer(question)
        response = QuestionResponse(
            answer=result.answer,
            question_type=result.question_type,
            requires_lookup=result.requires_lookup,
        )
        return response
