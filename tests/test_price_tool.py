"""Verify that price questions receive a focused deterministic tool result."""

import pytest

from app.infrastructure.database.sqlite import create_database
from app.tools.database import DatabasePlanError, execute_database_plan


def test_price_result_excludes_specs_and_catalog_metadata(tmp_path):
    path = tmp_path / "store.db"
    create_database(database_path=path)
    result = execute_database_plan({
        "entity": "products", "operation": "price",
        "filters": [{"field": "product_id", "value": 127}],
    }, path)
    row = result["rows"][0]
    assert row["price_brl"] == 359
    assert row["effective_price_brl"] == 323.1
    assert row["discount_percent"] == 10
    assert "specs" not in row and "description" not in row
    assert "catalog_overview" not in result


def test_price_operation_rejects_other_entities():
    with pytest.raises(DatabasePlanError):
        execute_database_plan({"entity": "customers", "operation": "price"})
