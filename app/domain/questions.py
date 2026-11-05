"""Question categories and results, independent of HTTP and model providers."""

from dataclasses import dataclass
from enum import Enum


class QuestionType(str, Enum):
    DATABASE = "database"
    PDF = "pdf"
    DATABASE_AND_PDF = "database_and_pdf"
    GREETING = "greeting"
    OUT_OF_SCOPE = "out_of_scope"
    ACCESSORY = "accessory"


@dataclass(frozen=True)
class QuestionIntent:
    question_type: QuestionType
    database_question: str | None = None
    policy_question: str | None = None


@dataclass(frozen=True)
class QuestionResult:
    answer: str
    question_type: QuestionType
    requires_lookup: bool
