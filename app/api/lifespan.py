"""Compose and release application resources at server startup/shutdown."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.tracing import LOGGER, configure_logging
from app.infrastructure.llm.client import initialize_llm
from app.services.questions import QuestionService
from app.tools.pdf_search import pdf_resources


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    with initialize_llm() as generate_response, pdf_resources():
        application.state.question_service = QuestionService(generate_response)
        LOGGER.info("API ready | POST /questions")
        try:
            yield
        finally:
            del application.state.question_service
            LOGGER.info("API shutting down; releasing LLM resources")
