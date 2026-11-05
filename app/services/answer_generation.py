"""Check price evidence and PDF citations in LLM-written answers."""

import json
import re
from decimal import Decimal, InvalidOperation

from app.core.tracing import LOGGER
from app.prompts import load_prompt


PAGE_CITATION = r"\b(?:pages?|p[aá]ginas?|p\.)\s*(\d+(?:[.,]\d+)*)\b"


def generate_draft(generate, payload, prompt):
    """Collect source metadata without asking the model to format citations."""
    pdf = payload.get("pdf_tool_result", {})
    pages = sorted({item["page"] for item in pdf.get("excerpts", [])})
    message = json.dumps(payload, ensure_ascii=False)
    if not pages or not pdf.get("found", True):
        return generate(message, prompt)
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "answer": {"type": "string", "minLength": 1},
            "source_pages": {
                "type": "array", "minItems": 1, "maxItems": len(pages),
                "uniqueItems": True,
                "items": {"type": "integer", "enum": pages},
            },
        },
        "required": ["answer", "source_pages"],
    }
    output = generate(
        message,
        prompt + "\nReturn JSON with answer and source_pages. Write the "
        "natural answer in answer; select the actual supporting PDF page "
        "numbers in source_pages. Citation formatting is handled separately.",
        response_format=schema,
    )
    draft = json.loads(output)
    if (not isinstance(draft, dict)
            or set(draft) != set(schema["required"])
            or not isinstance(draft["answer"], str)
            or not draft["answer"].strip()
            or not isinstance(draft["source_pages"], list)
            or not draft["source_pages"]
            or any(type(page) is not int or page not in pages
                   for page in draft["source_pages"])):
        raise ValueError("Invalid answer or unsupported source pages")
    answer = draft["answer"].strip()
    inline = set(re.findall(PAGE_CITATION, answer, re.IGNORECASE))
    missing = sorted(set(draft["source_pages"]) - {int(page) for page in
                     inline if page.isdigit()})
    if missing:
        answer += " " + " ".join(f"(página {page})" for page in missing)
    return answer


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
    citations = re.findall(PAGE_CITATION, answer, re.IGNORECASE)
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
        rows = database.get("rows", [])
        tracking_codes = {
            row["tracking_code"] for row in rows if row.get("tracking_code")
        }
        mentioned_codes = set(re.findall(r"\bBR[A-Z0-9]+BR\b", answer))
        if mentioned_codes - tracking_codes:
            errors.append("Quote only exact tracking codes from the returned order.")
        if database.get("entity") == "orders" and len(rows) == 1:
            status = rows[0].get("status")
            if status == "delivered" and not re.search(
                r"entreg|deliver", answer, re.IGNORECASE
            ):
                errors.append(
                    "The order status is delivered: explicitly state that it "
                    "was delivered (entregue), not merely shipped."
                )
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
                if (field == "value" and value is not None
                        and database.get("metric") in {
                            "price_brl", "effective_price_brl", "total_brl",
                        }):
                    allowed.add(Decimal(str(value)))
        for condition in database.get("filters", []):
            if condition.get("field") in {
                "price_brl", "effective_price_brl", "total_brl",
            }:
                allowed.add(Decimal(str(condition["value"])))
        if any(value not in allowed for value in money(answer)):
            errors.append(
                "Remove unsupported currency amounts. Quote only prices "
                "returned by the tools. Empty database rows mean the price "
                "could not be confirmed."
            )
    return errors


def disclose_partial_results(answer, evidence):
    """Expose tool pagination metadata without rewriting the model's facts."""
    if evidence.get("database_tool_result", {}).get("truncated"):
        if not re.search(
            r"parcial|partial|seleção|selection|algum|some|exempl|examples",
            answer, re.IGNORECASE,
        ):
            answer += "\n\nEsta é uma seleção parcial dos resultados."
    return answer


def generate_lookup_answer(generate, evidence, prompt):
    """Generate natural wording and allow one evidence-feedback repair."""
    if "database_tool_result" in evidence and "pdf_tool_result" in evidence:
        original_question = evidence.get("question", "")
        database_answer = generate_lookup_answer(generate, {
            "question": evidence.get("database_question") or original_question,
            "database_tool_result": evidence["database_tool_result"],
        }, load_prompt("lookup_answer.md"))
        policy_answer = generate_lookup_answer(generate, {
            "question": evidence.get("policy_question") or original_question,
            "pdf_tool_result": evidence["pdf_tool_result"],
        }, load_prompt("pdf_answer.md") + "\nAnswer ONLY the policy request. "
           "Do not discuss product prices or unavailable database evidence.")
        return database_answer + "\n\n" + policy_answer
    payload = dict(evidence)
    if "database_tool_result" in payload:
        prompt += "\nAnswer ONLY the database request. Do not discuss "
        prompt += "policies or unavailable PDF evidence."
    disclosures = promotion_disclosures(
        payload.get("database_tool_result", {})
    )
    if disclosures:
        payload["required_price_disclosures"] = disclosures
        prompt += "\nMandatory price facts for the price sentence: " + json.dumps(
            disclosures, ensure_ascii=False,
        )
    try:
        answer = generate_draft(generate, payload, prompt)
        errors = grounding_errors(answer, evidence)
    except (ValueError, TypeError) as error:
        answer = ""
        errors = [f"Return a valid answer JSON and supporting pages: {error}"]
    if not has_required_prices(answer, disclosures):
        errors.append(
            "The previous draft omitted original price, discount percentage, "
            "or effective price. Quote ALL values in "
            "required_price_disclosures. Do not recalculate."
        )
    if not errors:
        return disclose_partial_results(answer, evidence)
    LOGGER.warning(
        "Answer failed evidence checks; retrying once | draft=%s", answer
    )
    payload["formatting_feedback"] = " ".join(errors)
    repair_prompt = prompt + "\nCORRECTION REQUIRED: " + " ".join(errors)
    if disclosures:
        repair_prompt += "\nMandatory price facts: " + json.dumps(
            disclosures, ensure_ascii=False
        )
    try:
        answer = generate_draft(generate, payload, repair_prompt)
    except (ValueError, TypeError) as error:
        raise RuntimeError("Model returned invalid answer evidence") from error
    if not has_required_prices(answer, disclosures):
        raise RuntimeError(
            "Model omitted required promotional price disclosures"
        )
    if grounding_errors(answer, evidence):
        raise RuntimeError("Model answer failed evidence grounding checks")
    return disclose_partial_results(answer, evidence)
