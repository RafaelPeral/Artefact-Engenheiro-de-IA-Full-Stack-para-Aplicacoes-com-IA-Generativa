"""Translate application failures into HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.errors import (
    InferenceFailure, InferenceTimeout, InferenceUnavailable,
    PolicyUnavailable, QuestionServiceError,
)


STATUS_CODES = {
    PolicyUnavailable: 503,
    InferenceUnavailable: 503,
    InferenceTimeout: 504,
    InferenceFailure: 502,
}


def register_error_handlers(application: FastAPI):
    @application.exception_handler(QuestionServiceError)
    async def question_error(request: Request, error: QuestionServiceError):
        return JSONResponse(
            status_code=STATUS_CODES.get(type(error), 500),
            content={"detail": str(error)},
        )
