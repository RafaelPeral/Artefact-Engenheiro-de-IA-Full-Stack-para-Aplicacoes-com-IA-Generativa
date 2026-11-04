"""Application failures independent of HTTP status codes."""


class QuestionServiceError(RuntimeError):
    """Base error exposed by the question use case."""


class PolicyUnavailable(QuestionServiceError):
    pass


class InferenceUnavailable(QuestionServiceError):
    pass


class InferenceTimeout(QuestionServiceError):
    pass


class InferenceFailure(QuestionServiceError):
    pass
