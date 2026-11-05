"""Verify query planning and real read-only SQL against the supplied CSVs."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.infrastructure.database.sqlite import create_database
from app.tools.database import (
    DatabaseLookupError, DatabasePlanError, consult_database,
    execute_database_plan,
)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "store.db"
    data = Path(__file__).resolve().parents[1] / "data"
    create_database(data, path)
    return path


def test_product_lookup_uses_catalog_prices_and_category(database):
    result = execute_database_plan({
        "entity": "products",
        "filters": [{"field": "product_id", "value": 81}],
    }, database)
    product = result["rows"][0]
    assert product["name"] == "Yamaha C40 Nylon Natural"
    assert product["price_brl"] == 599.9
    assert product["stock_quantity"] == 12
    assert product["category_id"] == 5
    assert result["supported"] and result["found"]
    assert not result["truncated"]


def test_partial_name_defaults_to_containment(database):
    result = execute_database_plan({
        "entity": "products", "operation": "price",
        "filters": [{"field": "name", "value": "Yamaha C40"}],
    }, database)
    assert result["rows"][0]["product_id"] == 81
    assert result["rows"][0]["effective_price_brl"] == 599.9


def test_zero_promotion_id_is_replanned_before_query(database):
    invalid = {"entity": "products", "operation": "price", "filters": [
        {"field": "name", "value": "Yamaha C40"},
        {"field": "active_promotion_id", "value": 0},
    ]}
    valid = dict(invalid, filters=invalid["filters"][:1])
    model = Mock(side_effect=[json.dumps(invalid), json.dumps(valid)])
    result = consult_database("Price and return of Yamaha C40?", model, database)
    assert result["rows"][0]["price_brl"] == 599.9
    assert model.call_count == 2
    assert "positive integers" in model.call_args.args[0]


def test_invented_stock_status_is_rejected(database):
    with pytest.raises(DatabasePlanError, match="Unsupported status"):
        execute_database_plan({
            "entity": "products", "filters": [
                {"field": "status", "value": "in-stock"},
            ],
        }, database)


def product_price(database, product_id):
    return execute_database_plan({
        "entity": "products",
        "filters": [{"field": "product_id", "value": product_id}],
    }, database)["rows"][0]


@pytest.mark.parametrize("product_id, base, discount, current, promotion", [
    (127, 359, 10, 323.10, 12),
    (121, 549, 20, 439.20, 14),
    (90, 2399, 18, 1967.18, 19),
    (94, 5999, 8, 5519.08, 24),
])
def test_product_prices_apply_active_promotions(database, product_id, base,
                                               discount, current, promotion):
    product = product_price(database, product_id)
    assert product["price_brl"] == base
    assert product["effective_price_brl"] == current
    assert product["discount_percent"] == discount
    assert product["active_promotion_id"] == promotion
    assert product["active_promotion_count"] == 1
    assert product["pricing_status"] == "promotional"


@pytest.mark.parametrize("product_id", [81, 93, 96, 99])
def test_no_promotion_or_only_inactive_promotions_keep_base_price(database,
                                                               product_id):
    product = product_price(database, product_id)
    assert product["effective_price_brl"] == product["price_brl"]
    assert product["discount_percent"] == 0
    assert product["active_promotion_id"] is None
    assert product["active_promotion_count"] == 0
    assert product["pricing_status"] == "regular"


def test_inactive_bigger_discount_never_overrides_active_one(database):
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO promotions VALUES (1000, 127, 90, 'Old offer', 0)"
        )
        connection.commit()
    assert product_price(database, 127)["effective_price_brl"] == 323.10
    assert product_price(database, 127)["active_promotion_id"] == 12


def test_deactivated_promotion_stops_affecting_prices(database):
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE promotions SET is_active=0 WHERE product_id=127"
        )
        connection.commit()
    assert product_price(database, 127)["effective_price_brl"] == 359
    assert product_price(database, 127)["pricing_status"] == "regular"


def test_conflicting_active_promotions_require_confirmation(database):
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO promotions VALUES (1000, 127, 20, 'Other offer', 1)"
        )
        connection.commit()
    product = product_price(database, 127)
    assert product["active_promotion_count"] == 2
    assert product["effective_price_brl"] is None
    assert product["discount_percent"] is None
    assert product["pricing_status"] == "needs_confirmation"
    # Joining promotions must never duplicate catalog products.
    result = execute_database_plan({
        "entity": "products", "operation": "count",
    }, database)
    assert result["rows"] == [{"value": 65}]


def test_current_price_filters_sorting_and_aggregates_use_discount(database):
    with closing(sqlite3.connect(database)) as connection:
        for product_id, price in [(2000, 100), (2001, 95)]:
            connection.execute(
                "INSERT INTO products VALUES "
                "(?, ?, 'Test', 1, 'Test', 1, 'active', '{}', '2026-01-01')",
                (product_id, price),
            )
        connection.execute(
            "INSERT INTO promotions VALUES (1000, 2000, 20, 'Current', 1)"
        )
        connection.commit()
    filters = [{"field": "product_id", "operator": "gte", "value": 2000}]
    result = execute_database_plan({
        "entity": "products", "sort_by": "effective_price_brl",
        "filters": filters,
    }, database)
    assert [row["product_id"] for row in result["rows"]] == [2000, 2001]
    assert result["rows"][0]["effective_price_brl"] == 80
    result = execute_database_plan({
        "entity": "products", "filters": filters + [
            {"field": "effective_price_brl", "operator": "lte", "value": 90},
        ],
    }, database)
    assert [row["product_id"] for row in result["rows"]] == [2000]
    assert result["catalog_overview"]["total_products"] == 1
    result = execute_database_plan({
        "entity": "products", "operation": "min",
        "metric": "effective_price_brl", "filters": filters,
    }, database)
    assert result["rows"] == [{"value": 80}]


def test_promotional_cent_rounding_is_decimal_half_up(database):
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE products SET price_brl=0.05 WHERE product_id=127"
        )
        connection.commit()
    assert product_price(database, 127)["effective_price_brl"] == 0.05


def test_first_five_products_do_not_define_the_catalog(database):
    # Reproduce the exact plan from the reported incorrect answer.
    result = execute_database_plan({
        "entity": "products", "operation": "list", "limit": 5,
    }, database)
    assert len({row["category_id"] for row in result["rows"]}) == 1
    assert result["truncated"]
    overview = result["catalog_overview"]
    assert overview["total_products"] == 65
    assert overview["total_categories"] == 6
    assert {row["category_id"]: row["product_count"]
            for row in overview["categories"]} == {
        1: 5, 2: 4, 3: 3, 4: 3, 5: 35, 9: 15,
    }
    assert not overview["truncated"]


def test_overview_covers_all_populated_categories_not_list_limit(database):
    result = execute_database_plan({
        "entity": "products", "operation": "overview", "limit": 1,
    }, database)
    assert len(result["rows"]) == 6
    assert sum(row["product_count"] for row in result["rows"]) == 65
    # Defined categories with no linked products must not be advertised.
    assert {row["category_id"] for row in result["rows"]} == {1, 2, 3, 4, 5, 9}
    assert not result["truncated"]


@pytest.mark.parametrize("operation", ["list", "overview"])
def test_overview_respects_product_selection_filters(database, operation):
    result = execute_database_plan({
        "entity": "products", "operation": operation,
        "filters": [{"field": "product_id", "value": 81}],
    }, database)
    overview = result["catalog_overview"]
    assert overview["total_products"] == 1
    assert overview["total_categories"] == 1
    assert overview["categories"][0]["category_id"] == 5


def test_empty_overview_has_zero_counts(database):
    result = execute_database_plan({
        "entity": "products", "operation": "overview",
        "filters": [{"field": "product_id", "value": 999999}],
    }, database)
    assert not result["found"]
    assert result["rows"] == []
    assert result["catalog_overview"]["total_products"] == 0
    assert result["catalog_overview"]["total_categories"] == 0


def test_overview_safety_limit_preserves_global_totals(database):
    with closing(sqlite3.connect(database)) as connection:
        for number in range(105):
            category_id = 1000 + number
            connection.execute(
                "INSERT INTO categories VALUES (?, ?, 'test')",
                (category_id, f"Category {number}"),
            )
            connection.execute(
                "INSERT INTO products VALUES "
                "(?, 1, ?, ?, 'test', 1, 'active', '{}', '2026-01-01')",
                (1000 + number, f"Product {number}", category_id),
            )
        connection.commit()
    result = execute_database_plan({
        "entity": "products", "operation": "overview",
    }, database)
    assert result["truncated"]
    assert len(result["rows"]) == 100
    assert result["catalog_overview"]["total_products"] == 170
    assert result["catalog_overview"]["total_categories"] == 111


@pytest.mark.parametrize("plan", [
    {"entity": "orders", "operation": "overview"},
    {"entity": "products", "operation": "overview", "metric": "price_brl"},
    {"entity": "products", "operation": "overview", "sort_by": "name"},
    {"entity": "products", "operation": "overview", "descending": True},
])
def test_overview_rejects_incompatible_options(database, plan):
    with pytest.raises(DatabasePlanError):
        execute_database_plan(plan, database)


@pytest.mark.parametrize("entity, count", [
    ("products", 65), ("categories", 9), ("orders", 20),
    ("order_items", 22), ("promotions", 25), ("customers", 50),
])
def test_counts_are_not_limited_by_page_size(database, entity, count):
    result = execute_database_plan({
        "entity": entity, "operation": "count", "limit": 1,
    }, database)
    assert result["rows"] == [{"value": count}]
    assert not result["truncated"]


@pytest.mark.parametrize("operation, metric", [
    ("sum", "stock_quantity"), ("avg", "price_brl"),
    ("min", "price_brl"), ("max", "price_brl"),
])
def test_aggregates_are_computed_by_sql(database, operation, metric):
    result = execute_database_plan({
        "entity": "products", "operation": operation, "metric": metric,
    }, database)
    with closing(sqlite3.connect(database)) as connection:
        expected = connection.execute(
            f"SELECT ROUND({operation}({metric}), 2) FROM products"
        ).fetchone()[0]
    assert result["rows"] == [{"value": expected}]


def test_unicode_search_is_case_and_accent_insensitive(database):
    result = execute_database_plan({
        "entity": "categories",
        "filters": [{"field": "name", "operator": "contains",
                     "value": "VIOLOES"}],
    }, database)
    assert result["rows"]
    assert any(row["category_id"] == 5 for row in result["rows"])


def test_order_items_join_products_without_inventing_unit_prices(database):
    result = execute_database_plan({
        "entity": "order_items",
        "filters": [{"field": "order_id", "value": 1}],
    }, database)
    assert result["rows"][0]["product_id"] == 93
    assert result["rows"][0]["quantity"] == 1
    assert "name" in result["rows"][0]
    assert "price_brl" not in result["rows"][0]


def test_boundaries_sorting_and_truncation(database):
    plan = {
        "entity": "products", "sort_by": "price_brl", "descending": True,
        "limit": 2,
        "filters": [
            {"field": "price_brl", "operator": "gte", "value": 500},
            {"field": "price_brl", "operator": "lte", "value": 5000},
        ],
    }
    first = execute_database_plan(plan, database)
    assert first == execute_database_plan(plan, database)
    assert first["truncated"]
    prices = [row["price_brl"] for row in first["rows"]]
    assert len(prices) == 2
    assert prices == sorted(prices, reverse=True)
    assert all(500 <= price <= 5000 for price in prices)


def test_sql_injection_is_literal_data(database):
    result = execute_database_plan({
        "entity": "products",
        "filters": [{"field": "name", "operator": "contains",
                     "value": "' OR 1=1; DROP TABLE products; --"}],
    }, database)
    assert result["rows"] == []
    assert not result["found"]
    assert execute_database_plan({"entity": "products", "operation": "count"},
                                 database)["rows"] == [{"value": 65}]


@pytest.mark.parametrize("plan", [
    {"entity": "products; DROP TABLE orders"},
    {"entity": "products", "sql": "DELETE FROM products"},
    {"entity": "products", "limit": 1000},
    {"entity": "products", "limit": True},
    {"entity": "products", "sort_by": "name; DELETE FROM products"},
    {"entity": "products", "operation": "sum", "metric": "name"},
    {"entity": "products", "operation": "count", "metric": "price_brl"},
    {"entity": "products", "metric": "price_brl"},
    {"entity": "orders", "operation": "count", "sort_by": "order_id"},
    {"entity": "products", "filters": [{"field": "password", "value": 1}]},
    {"entity": "products", "filters": [{"field": "product_id", "value": "1"}]},
    {"entity": "products", "filters": [{"field": "name", "value": " "}]},
    {"entity": "products", "filters": [{"field": "price_brl",
                                         "value": float("nan")}]},
    {"entity": "products", "filters": [{"field": "product_id",
                                         "operator": "contains", "value": 1}]},
])
def test_invalid_plans_never_execute_sql(database, plan):
    with pytest.raises(DatabasePlanError):
        execute_database_plan(plan, database)


def test_missing_database_does_not_create_an_empty_file(tmp_path):
    target = tmp_path / "missing.db"
    with pytest.raises(DatabaseLookupError):
        execute_database_plan({"entity": "products"}, target)
    assert not target.exists()


def test_unsupported_question_executes_no_query(tmp_path):
    result = execute_database_plan({"entity": "unsupported"},
                                   tmp_path / "missing.db")
    assert result["supported"] is False
    assert result["rows"] == []


def test_planner_uses_schema_and_executes_validated_output(database):
    model = Mock(return_value=json.dumps({
        "entity": "orders", "filters": [{"field": "order_id", "value": 1}],
    }))
    evidence = consult_database("Order 1 status?", model, database)
    assert evidence["rows"][0]["order_id"] == 1
    assert evidence["rows"][0]["status"] == "delivered"
    schema = model.call_args.kwargs["response_format"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "output", [None, "invalid", "[]", '{"sql":"SELECT 1"}']
)
def test_invalid_model_plans_fail_closed(database, output):
    with pytest.raises(DatabasePlanError):
        consult_database("Price?", Mock(return_value=output), database)
