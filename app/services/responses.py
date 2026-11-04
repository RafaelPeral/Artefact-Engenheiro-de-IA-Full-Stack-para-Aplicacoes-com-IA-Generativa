"""Deterministic store responses."""

from app.domain.questions import QuestionType

ANSWERS = {
    QuestionType.GREETING: (
        "Olá! Sou o assistente da Empório da Música. "
        "Posso ajudar com produtos, pedidos e informações sobre a loja."
    ),
    QuestionType.OUT_OF_SCOPE: (
        "Não sei responder, pois meus conhecimentos se limitam "
        "aos assuntos da Empório da Música."
    ),
    QuestionType.DATABASE: (
        "Esta pergunta necessita de consulta ao banco de dados da Empório "
        "da Música. Essa consulta ainda não está implementada."
    ),
}
