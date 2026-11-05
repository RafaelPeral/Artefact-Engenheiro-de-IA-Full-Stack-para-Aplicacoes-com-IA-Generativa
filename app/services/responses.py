"""Deterministic store responses."""

from app.domain.questions import QuestionType

ANSWERS = {
    QuestionType.GREETING: (
        "Oi! Sou o assistente virtual da Empório da Música. "
        "Como posso te ajudar com instrumentos, pedidos ou dúvidas sobre a loja?"
    ),
    QuestionType.OUT_OF_SCOPE: (
        "Essa dúvida fica fora do que posso te ajudar por aqui. "
        "Posso ajudar com instrumentos musicais, pedidos e informações "
        "sobre a Empório da Música."
    ),
}
