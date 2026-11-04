"""Resolve application services for HTTP handlers."""

from fastapi import Request

from app.services.questions import QuestionService


def get_question_service(request: Request) -> QuestionService:
    return request.app.state.question_service
