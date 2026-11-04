"""Exercise the question use case independently of FastAPI and its lifespan."""

import json
from unittest.mock import Mock

import pytest
from httpx import ReadTimeout

from app.domain.errors import InferenceTimeout, PolicyUnavailable
from app.domain.questions import QuestionResult, QuestionType
from app.services.questions import QuestionService
from app.services.responses import ANSWERS


def test_fixed_response_requires_no_http_or_pdf_resources():
    model = Mock(return_value='{"question_type":"out_of_scope"}')
    result = QuestionService(model).answer("Qual a capital dos EUA?")
    assert result == QuestionResult(ANSWERS[QuestionType.OUT_OF_SCOPE],
                                    QuestionType.OUT_OF_SCOPE, False)






def test_model_timeout_exposes_application_error_without_http_types():
    cause = ReadTimeout("slow model")
    service = QuestionService(Mock(side_effect=cause))
    with pytest.raises(InferenceTimeout) as captured:
        service.answer("Horário?")
    assert captured.value.__cause__ is cause
