"""Check LLM-written answers and bounded repair of omitted promotion values."""

import json
from unittest.mock import Mock

import pytest

from app.services.answer_generation import (
    generate_lookup_answer, has_required_prices, promotion_disclosures,
)


@pytest.fixture
def evidence():
    return {"question": "Price of Kalani?", "database_tool_result": {
        "entity": "products", "operation": "list", "rows": [{
            "product_id": 127, "name": "Kalani KAL-700T Tenor Natural",
            "price_brl": 359, "effective_price_brl": 323.10,
            "discount_percent": 10, "pricing_status": "promotional",
        }],
    }}


def test_compliant_llm_wording_is_returned_unchanged(evidence):
    text = "Kalani: de R$ 359,00 por R$ 323,10, com 10% de desconto."
    model = Mock(return_value=text)
    assert generate_lookup_answer(model, evidence, "prompt") == text
    model.assert_called_once()
    payload = json.loads(model.call_args.args[0])
    assert payload["required_price_disclosures"][0]["discount"] == "10%"
    assert "Correspond?ncias" not in text


def test_missing_amounts_trigger_one_llm_repair_not_a_static_append(evidence):
    corrected = "Kalani: de R$ 359,00 por R$ 323,10 (10% de desconto)."
    model = Mock(side_effect=["It costs R$ 323,10.", corrected])
    assert generate_lookup_answer(model, evidence, "prompt") == corrected
    assert model.call_count == 2
    assert "formatting_feedback" in json.loads(model.call_args.args[0])


def test_repeated_noncompliance_is_not_published(evidence):
    model = Mock(return_value="It costs R$ 323,20.")
    with pytest.raises(RuntimeError, match="omitted required"):
        generate_lookup_answer(model, evidence, "prompt")
    assert model.call_count == 2


@pytest.mark.parametrize("text", [
    "Original R$ 359,00; 10% off; current R$ 323,10.",
    "Was BRL 359.00, now BRL 323.10, with 10% off.",
    "De R$ 359 por R$ 323,10, com 10,0% de desconto.",
])
def test_amount_validation_does_not_require_a_fixed_sentence(evidence, text):
    disclosures = promotion_disclosures(evidence["database_tool_result"])
    assert has_required_prices(text, disclosures)


@pytest.mark.parametrize("status", ["regular", "needs_confirmation"])
def test_no_promotion_is_invented_for_other_states(evidence, status):
    evidence["database_tool_result"]["rows"][0]["pricing_status"] = status
    model = Mock(return_value="No confirmed promotional price.")
    result = generate_lookup_answer(model, evidence, "prompt")
    assert result == model.return_value
    model.assert_called_once()


def test_invalid_tool_price_is_rejected(evidence):
    evidence["database_tool_result"]["rows"][0]["effective_price_brl"] = None
    with pytest.raises(RuntimeError, match="Invalid promotional"):
        generate_lookup_answer(Mock(), evidence, "prompt")


def test_empty_database_does_not_hide_retrieved_return_policy():
    evidence = {
        "question": "Quanto custa o Yamaha C40 e posso devolvê-lo?",
        "database_tool_result": {"rows": [], "found": False},
        "pdf_tool_result": {"found": True, "excerpts": [{
            "page": 4, "text": "Online returns within 7 calendar days.",
        }]},
    }
    corrected = (
        "Não confirmei o preço. Compras online podem ser devolvidas "
        "em até 7 dias corridos, conforme as condições (página 4)."
    )
    model = Mock(side_effect=[
        "O Yamaha C40 custa R$ 2.499,00. "
        "Não há informações disponíveis sobre devoluções.",
        corrected,
    ])
    assert generate_lookup_answer(model, evidence, "prompt") == corrected
    assert model.call_count == 2


def test_promotion_repair_with_empty_pdf_rejects_invented_page(evidence):
    evidence["question"] = "Quanto custa a Kalani KAL-700T?"
    evidence["pdf_tool_result"] = {"found": False, "excerpts": []}
    draft = (
        "A compra foi online. O preço é R$ 323,10. "
        "A política de devolução está na página 5."
    )
    corrected = "Kalani: de R$ 359,00 por R$ 323,10, com 10% de desconto."
    model = Mock(side_effect=[draft, corrected])
    assert generate_lookup_answer(model, evidence, "prompt") == corrected
    payload = json.loads(model.call_args.args[0])
    assert "Mandatory price facts" in model.call_args.args[1]
    assert "invented page" in payload["formatting_feedback"]
    assert "original price" in payload["formatting_feedback"]


def test_correct_promotion_with_invented_pdf_citation_is_not_published(evidence):
    evidence["pdf_tool_result"] = {"found": False, "excerpts": []}
    model = Mock(return_value=(
        "Kalani: de R$ 359,00 por R$ 323,10, com 10% de desconto (page 5)."
    ))
    with pytest.raises(RuntimeError, match="grounding"):
        generate_lookup_answer(model, evidence, "prompt")
    assert model.call_count == 2


@pytest.mark.parametrize("draft", [
    "Yamaha costs R$ 2499,00 (page 4).",
    "Yamaha costs R$ 599,90. No return information.",
    "Yamaha costs R$ 599,90 (page 99).",
])
def test_unsupported_price_or_missing_valid_citation_is_rejected(draft):
    evidence = {
        "database_tool_result": {"rows": [{"price_brl": 599.9}]},
        "pdf_tool_result": {"found": True, "excerpts": [{
            "page": 4, "text": "Returns within 7 calendar days.",
        }]},
    }
    model = Mock(return_value=draft)
    with pytest.raises(RuntimeError, match="grounding"):
        generate_lookup_answer(model, evidence, "prompt")
    assert model.call_count == 2
