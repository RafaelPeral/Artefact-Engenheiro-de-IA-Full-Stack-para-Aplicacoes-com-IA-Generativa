"""Check price evidence and PDF citations in LLM-written answers."""

import json
import re
from decimal import Decimal, InvalidOperation

from app.core.tracing import LOGGER


def promotion_disclosures(evidence):
    """Format already calculated tool values; never calculate a discount."""
    if (evidence.get("entity") != "products"
            or evidence.get("operation") not in {"list", "price"}):
        return []
    disclosures = []
    for row in evidence.get("rows", []):
        if row.get("pricing_status") != "promotional":
            continue
        try:
            amounts = [Decimal(str(row[field])) for field in (
                "price_brl", "effective_price_brl", "discount_percent",
            )]
            if not all(value.is_finite() for value in amounts):
                raise ValueError("Non-finite price")
        except (KeyError, InvalidOperation, ValueError) as error:
            raise RuntimeError(
                "Invalid promotional pricing evidence"
            ) from error

        def currency(amount):
            return "R$ " + format(amount, ",.2f").translate(
                str.maketrans({",": ".", ".": ","})
            )

        disclosures.append({
            "product": row["name"],
            "original_price": currency(amounts[0]),
            "discount": format(amounts[2].normalize(), "f") + "%",
            "effective_price": currency(amounts[1]),
        })
    return disclosures


def has_required_prices(answer, disclosures):
    """Accept Brazilian/English amounts without requiring a fixed sentence."""
    text = re.sub(r"\s+", "", answer)
    for item in disclosures:
        for field in ("original_price", "effective_price"):
            amount = item[field].removeprefix("R$ ")
            numeric = Decimal(amount.replace(".", "").replace(",", "."))
            variants = {
                amount, format(numeric, ".2f"), format(numeric, ",.2f"),
            }
            if numeric == numeric.to_integral():
                variants.add(format(numeric, ".0f"))
            if not any(re.search(
                r"(?<![\d.,])" + re.escape(value) + r"(?!\d|[.,]\d)", text
            ) for value in variants):
                return False
        expected = Decimal(item["discount"].removesuffix("%"))
        percentages = re.findall(r"(?<!\d)(\d+(?:[.,]\d+)?)%", text)
        if not any(Decimal(value.replace(",", ".")) == expected
                   for value in percentages):
            return False
    return True


def grounding_errors(answer, evidence):
    """Check explicit currency claims and citations, not semantic entailment."""
    errors = []
    database = evidence.get("database_tool_result")
    pdf = evidence.get("pdf_tool_result", {})
    excerpts = pdf.get("excerpts", [])
    citations = re.findall(
        r"\b(?:page|p[aá]gina|p\.)\s*(\d+)\b", answer, re.IGNORECASE
    )
    pages = {str(item["page"]) for item in excerpts}
    if set(citations) - pages:
        errors.append(
            "Remove invented page citations. Cite only pages supplied in "
            "pdf_tool_result.excerpts; empty excerpts support no policy claims."
        )
    if excerpts and pdf.get("found", True):
        if not pages.intersection(citations):
            errors.append(
                "Answer the policy part using the supplied excerpts and cite "
                "a supporting page as (page N). Database absence does not "
                "mean policy evidence is absent."
            )
    if database is not None:
        def money(text):
            values = []
            for raw in re.findall(
                r"(?:R\$|BRL)\s*(\d[\d.,]*)", text, re.IGNORECASE
            ):
                raw = raw.rstrip(".,")
                if "," in raw and "." in raw:
                    separator = "," if raw.rfind(",") > raw.rfind(".") else "."
                    raw = raw.replace("." if separator == "," else ",", "")
                try:
                    values.append(Decimal(raw.replace(",", ".")))
                except InvalidOperation:
                    continue
            return values

        allowed = set(money(" ".join(item["text"] for item in excerpts)))
        for row in database.get("rows", []):
            for field, value in row.items():
                if value is not None and (
                    "price" in field or field.endswith("_brl")
                ):
                    allowed.add(Decimal(str(value)))
        if any(value not in allowed for value in money(answer)):
            errors.append(
                "Remove unsupported currency amounts. Quote only prices "
                "returned by the tools. Empty database rows mean the price "
                "could not be confirmed."
            )
    return errors


def generate_lookup_answer(generate, evidence, prompt):
    """Generate natural wording and allow one evidence-feedback repair."""
    payload = dict(evidence)
    disclosures = promotion_disclosures(
        payload.get("database_tool_result", {})
    )
    if disclosures:
        payload["required_price_disclosures"] = disclosures
    answer = generate(json.dumps(payload, ensure_ascii=False), prompt)
    errors = grounding_errors(answer, evidence)
    if not has_required_prices(answer, disclosures):
        errors.append(
            "The previous draft omitted original price, discount percentage, "
            "or effective price. Quote ALL values in "
            "required_price_disclosures. Do not recalculate."
        )
    if not errors:
        return answer
    LOGGER.warning(
        "Answer failed evidence checks; retrying once | draft=%s", answer
    )
    payload["formatting_feedback"] = " ".join(errors)
    repair_prompt = prompt + "\nCORRECTION REQUIRED: " + " ".join(errors)
    if disclosures:
        repair_prompt += "\nMandatory price facts: " + json.dumps(
            disclosures, ensure_ascii=False
        )
    answer = generate(json.dumps(payload, ensure_ascii=False), repair_prompt)
    if not has_required_prices(answer, disclosures):
        raise RuntimeError(
            "Model omitted required promotional price disclosures"
        )
    if grounding_errors(answer, evidence):
        raise RuntimeError("Model answer failed evidence grounding checks")
    return answer
