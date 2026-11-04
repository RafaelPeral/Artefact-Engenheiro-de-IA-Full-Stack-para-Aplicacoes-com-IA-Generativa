"""HTTP request/response contracts."""

from pydantic import BaseModel

from app.domain.questions import QuestionType


class QuestionResponse(BaseModel):
    answer: str
    question_type: QuestionType
    requires_lookup: bool
