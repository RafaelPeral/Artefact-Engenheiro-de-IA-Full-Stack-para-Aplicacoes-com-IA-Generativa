"""Execute bounded, parameterized, read-only database queries."""

import json
import math
import os
import sqlite3
import unicodedata
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.tracing import LOGGER, trace_step
from app.prompts import load_prompt


DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "store.db"


class DatabaseLookupError(RuntimeError):
    """The configured database cannot be queried."""


class DatabasePlanError(RuntimeError):
    """The model produced an unsupported or invalid query plan."""


class QueryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: str
    operator: Literal["auto", "eq", "contains", "gte", "lte"] = "auto"
    value: str | int | float | bool


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    entity: Literal[
        "products", "categories", "orders", "order_items",
        "promotions", "customers", "unsupported",
    ]
    operation: Literal[
        "list", "price", "overview", "count", "sum", "avg", "min", "max",
    ] = "list"
    metric: str | None = None
    filters: list[QueryFilter] = Field(default_factory=list, max_length=6)
    sort_by: str | None = None
    descending: bool = False
    limit: int = Field(default=5, ge=1, le=10)


# Every identifier and join below is application-owned, never model-authored.
PRODUCT_SOURCE = """
    products p JOIN categories c ON c.category_id = p.category_id
    LEFT JOIN (
        SELECT product_id, COUNT(*) AS active_count,
               MIN(promotion_id) AS promotion_id,
               MIN(discount_percent) AS discount_percent
        FROM promotions WHERE is_active = 1 GROUP BY product_id
    ) ap ON ap.product_id = p.product_id
"""
EFFECTIVE_PRICE = """CASE WHEN COALESCE(ap.active_count, 0) > 1 THEN NULL
    ELSE discounted_price(p.price_brl, COALESCE(ap.discount_percent, 0)) END"""


TABLES = {
    "products": (
        PRODUCT_SOURCE,
        {
            "product_id": "p.product_id", "name": "p.name",
            "category_id": "p.category_id", "category": "c.name",
            "price_brl": "p.price_brl", "stock_quantity": "p.stock_quantity",
            "effective_price_brl": EFFECTIVE_PRICE,
            "discount_percent": (
                "CASE WHEN COALESCE(ap.active_count, 0) > 1 THEN NULL "
                "ELSE COALESCE(ap.discount_percent, 0) END"
            ),
            "active_promotion_id": (
                "CASE WHEN ap.active_count = 1 THEN ap.promotion_id END"
            ),
            "active_promotion_count": "COALESCE(ap.active_count, 0)",
            "pricing_status": (
                "CASE WHEN ap.active_count > 1 THEN 'needs_confirmation' "
                "WHEN ap.active_count = 1 THEN 'promotional' "
                "ELSE 'regular' END"
            ),
            "status": "p.status", "specs": "p.specs",
            "description": "p.description",
        },
    ),
    "categories": (
        "categories c",
        {"category_id": "c.category_id", "name": "c.name",
         "description": "c.description"},
    ),
    "orders": (
        "orders o",
        {name: f"o.{name}" for name in (
            "order_id", "customer_id", "order_date", "status", "total_brl",
            "payment_method", "tracking_code", "estimated_delivery", "notes",
        )},
    ),
    "order_items": (
        "order_items i JOIN products p ON p.product_id = i.product_id",
        {"order_id": "i.order_id", "product_id": "i.product_id",
         "name": "p.name", "quantity": "i.quantity"},
    ),
    "promotions": (
        "promotions r JOIN products p ON p.product_id = r.product_id",
        {"promotion_id": "r.promotion_id", "product_id": "r.product_id",
         "name": "p.name", "discount_percent": "r.discount_percent",
         "description": "r.description", "is_active": "r.is_active"},
    ),
    "customers": (
        "customers c",
        {name: f"c.{name}" for name in (
            "customer_id", "name", "phone", "email", "city",
        )},
    ),
}
METRICS = {
    "products": {"price_brl", "effective_price_brl", "stock_quantity"},
    "orders": {"total_brl"},
    "order_items": {"quantity"},
    "promotions": {"discount_percent"},
    "categories": set(), "customers": set(),
}
NUMERIC_FIELDS = {
    "product_id", "category_id", "customer_id", "order_id", "promotion_id",
    "price_brl", "stock_quantity", "total_brl", "quantity",
    "discount_percent", "is_active",
    "effective_price_brl", "active_promotion_id", "active_promotion_count",
}


def database_path():
    """Share the configured location between startup and read-only lookups."""
    return Path(os.environ.get("STORE_DATABASE_PATH", DEFAULT_DATABASE))


def _normalize(value):
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in decomposed
                   if not unicodedata.combining(char)).casefold()


def _discounted_price(price, discount):
    """Calculate the promotional amount with decimal, half-up cent rounding."""
    amount = Decimal(str(price)) * (
        Decimal(100) - Decimal(str(discount))
    ) / Decimal(100)
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _build_query(plan):
    source, columns = TABLES[plan.entity]
    predicates, parameters = [], []
    for condition in plan.filters:
        if condition.field not in columns:
            raise DatabasePlanError("Unsupported database filter")
        column = columns[condition.field]
        value = condition.value
        numeric = condition.field in NUMERIC_FIELDS
        if numeric:
            if (not isinstance(value, (int, float))
                    or abs(value) > 2**63 - 1 or not math.isfinite(value)):
                raise DatabasePlanError(
                    "Numeric filters require finite, bounded numbers"
                )
        elif not isinstance(value, str) or not value.strip():
            raise DatabasePlanError("Text filters require non-empty strings")
        if condition.field == "status":
            allowed_statuses = {
                "products": {"active", "coming_soon", "discontinued"},
                "orders": {
                    "cancelled", "confirmed", "delivered", "pending", "shipped",
                },
            }.get(plan.entity, set())
            if value not in allowed_statuses:
                raise DatabasePlanError(
                    "Unsupported status. Allowed: "
                    + ", ".join(sorted(allowed_statuses))
                    + ". Stock availability uses stock_quantity, not status."
                )
        if condition.field.endswith("_id") and (
            isinstance(value, bool) or value <= 0 or int(value) != value
        ):
            raise DatabasePlanError("Identifier filters require positive integers")
        comparison = condition.operator
        if comparison == "auto":
            comparison = (
                "contains" if condition.field in {"name", "category", "specs"}
                else "eq"
            )
        if comparison == "contains":
            if numeric:
                raise DatabasePlanError("contains requires a text column")
            predicates.append(f"instr(normalize({column}), normalize(?)) > 0")
        elif not numeric and comparison == "eq":
            predicates.append(f"normalize({column}) = normalize(?)")
        else:
            operator = {"eq": "=", "gte": ">=", "lte": "<="}[
                comparison
            ]
            predicates.append(f"{column} {operator} ?")
        parameters.append(value)

    if plan.operation == "overview":
        if (plan.entity != "products" or plan.metric is not None
                or plan.sort_by is not None or plan.descending):
            raise DatabasePlanError(
                "Overview requires products without sorting"
            )
        selection = (
            "c.category_id AS category_id, c.name AS category, "
            "COUNT(*) AS product_count, "
            "SUM(COUNT(*)) OVER () AS total_products, "
            "COUNT(*) OVER () AS total_categories"
        )
    elif plan.operation in {"list", "price"}:
        if plan.metric is not None:
            raise DatabasePlanError("List queries cannot specify a metric")
        if plan.operation == "price":
            if plan.entity != "products":
                raise DatabasePlanError("Price queries require products")
            selected = {key: columns[key] for key in (
                "product_id", "name", "price_brl", "effective_price_brl",
                "discount_percent", "active_promotion_id", "pricing_status",
                "stock_quantity",
            )}
        else:
            selected = columns
        selection = ", ".join(
            f"{column} AS {name}" for name, column in selected.items()
        )
    elif plan.operation == "count":
        if plan.metric is not None:
            raise DatabasePlanError("Count queries do not use a metric")
        selection = "COUNT(*) AS value"
    else:
        if plan.metric not in METRICS[plan.entity]:
            raise DatabasePlanError("Unsupported aggregation metric")
        selection = f"{plan.operation.upper()}({columns[plan.metric]})"
        if plan.metric in {
            "price_brl", "effective_price_brl", "total_brl",
            "discount_percent",
        }:
            selection = f"ROUND({selection}, 2)"
        selection += " AS value"

    sql = f"SELECT {selection} FROM {source}"
    if predicates:
        sql += " WHERE " + " AND ".join(predicates)
    if plan.operation == "overview":
        sql += (
            " GROUP BY c.category_id, c.name ORDER BY c.category_id LIMIT 101"
        )
    elif plan.operation in {"list", "price"}:
        key = plan.sort_by or next(iter(columns))
        if key not in columns:
            raise DatabasePlanError("Unsupported sorting column")
        direction = "DESC" if plan.descending else "ASC"
        # Stable tie-breaking avoids changing answers between identical calls.
        sql += f" ORDER BY {columns[key]} {direction}, "
        sql += ", ".join(columns.values())
        sql += " LIMIT ?"
        parameters.append(plan.limit + 1)
    elif plan.sort_by is not None:
        raise DatabasePlanError("Aggregations do not support sorting")
    return sql, parameters


def _catalog_overview(connection, plan):
    """Summarize every match, independently of sample ordering and limits."""
    overview = plan.model_copy(update={
        "operation": "overview", "metric": None, "sort_by": None,
        "descending": False,
    })
    sql, parameters = _build_query(overview)
    categories = [dict(row) for row in connection.execute(sql, parameters)]
    total_products = categories[0]["total_products"] if categories else 0
    total_categories = categories[0]["total_categories"] if categories else 0
    return {
        "scope": "all_products_matching_filters",
        "total_products": total_products,
        "total_categories": total_categories,
        "categories": [
            {key: row[key] for key in (
                "category_id", "category", "product_count",
            )}
            for row in categories[:100]
        ],
        "truncated": len(categories) > 100,
    }


def execute_database_plan(plan, path=None):
    """Execute a plan and return factual evidence, not a generated answer."""
    try:
        plan = QueryPlan.model_validate(plan)
    except ValidationError as error:
        raise DatabasePlanError("Invalid database query plan") from error
    if plan.entity == "unsupported":
        return {"source": "sqlite", "supported": False, "found": False,
                "rows": [], "truncated": False}
    sql, parameters = _build_query(plan)
    target = Path(path) if path is not None else database_path()
    overview = None
    with trace_step("database.query"):
        try:
            uri = target.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(
                uri, uri=True, timeout=2
            )) as connection:
                connection.execute("PRAGMA query_only = ON")
                connection.row_factory = sqlite3.Row
                connection.create_function("normalize", 1, _normalize,
                                           deterministic=True)
                connection.create_function(
                    "discounted_price", 2, _discounted_price,
                    deterministic=True,
                )
                deadline = monotonic() + 3
                connection.set_progress_handler(
                    lambda: int(monotonic() > deadline), 1000
                )
                # Sample and overview must refer to the same read snapshot.
                connection.execute("BEGIN")
                if plan.operation == "overview":
                    overview = _catalog_overview(connection, plan)
                    rows = overview["categories"]
                else:
                    rows = [
                        dict(row)
                        for row in connection.execute(sql, parameters)
                    ]
                    if plan.entity == "products" and plan.operation == "list":
                        overview = _catalog_overview(connection, plan)
        except (sqlite3.Error, OSError) as error:
            raise DatabaseLookupError(
                "Store database lookup failed"
            ) from error
    truncated = plan.operation in {"list", "price"} and len(rows) > plan.limit
    if truncated:
        rows = rows[:plan.limit]
    if plan.operation == "overview":
        truncated = overview["truncated"]
    result = {
        "source": "sqlite", "supported": True, "entity": plan.entity,
        "operation": plan.operation, "metric": plan.metric,
        "filters": [condition.model_dump() for condition in plan.filters],
        "found": bool(rows), "rows": rows, "truncated": truncated,
    }
    if overview is not None:
        result["catalog_overview"] = overview
    LOGGER.info(
        "DATABASE_RESULT | entity=%s | operation=%s | rows=%s | truncated=%s "
        "| matching_products=%s | matching_categories=%s",
        plan.entity, plan.operation, len(rows), truncated,
        overview["total_products"] if overview else None,
        overview["total_categories"] if overview else None,
    )
    return result


def plan_database_query(question, generate_response):
    """Translate intent to a plan without executing model-written SQL."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    with trace_step("database.plan"):
        message = question.strip()
        for attempt in range(2):
            output = generate_response(
                message, load_prompt("database_plan.md"),
                response_format=QueryPlan.model_json_schema(),
            )
            try:
                plan = QueryPlan.model_validate(json.loads(output))
                if plan.entity != "unsupported":
                    _build_query(plan)
                return plan
            except (ValueError, TypeError, DatabasePlanError) as error:
                if attempt:
                    raise DatabasePlanError(
                        "Model returned an invalid database plan"
                    ) from error
                message = json.dumps({
                    "question": question.strip(),
                    "invalid_plan": output,
                    "validation_feedback": str(error),
                    "instruction": "Correct the plan. Do not invent filters.",
                })


def consult_database(question, generate_response, path=None):
    """Plan and execute a read-only query without caching database rows."""
    return execute_database_plan(
        plan_database_query(question, generate_response), path
    )
