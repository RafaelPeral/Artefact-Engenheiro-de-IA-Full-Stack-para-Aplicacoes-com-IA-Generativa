"""Exercise CSV imports against real temporary SQLite databases."""

import csv
import importlib
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest


storage = importlib.import_module("app.SQLite_Creation")
EXPECTED_COUNTS = {
    "categories": 9, "customers": 50, "products": 65,
    "orders": 20, "order_items": 22, "promotions": 25,
}


@pytest.fixture
def csv_directory(tmp_path):
    destination = tmp_path / "data"
    destination.mkdir()
    source = Path(__file__).resolve().parents[1] / "data"
    for path in source.glob("*.csv"):
        shutil.copyfile(path, destination / path.name)
    return destination


def change_first_row(directory, table, column, value):
    path = directory / f"{table}.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        rows = list(reader)
    rows[0][column] = value
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_import_and_reimport_preserve_counts_and_integrity(csv_directory,
                                                         tmp_path):
    database = tmp_path / "custom.db"
    for _ in range(2):
        counts = storage.create_database(csv_directory, database)
        assert counts == EXPECTED_COUNTS
    with closing(sqlite3.connect(database)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        assert integrity == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        for table, count in EXPECTED_COUNTS.items():
            actual = connection.execute(f"SELECT COUNT(*) FROM {table}")
            assert actual.fetchone()[0] == count
        assert connection.execute(
            "SELECT tracking_code FROM orders WHERE tracking_code IS NULL"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT typeof(phone) FROM customers LIMIT 1"
        ).fetchone() == ("text",)


def test_default_paths_are_relative_to_module(csv_directory, tmp_path,
                                             monkeypatch):
    module = tmp_path / "app" / "SQLite_Creation.py"
    module.parent.mkdir()
    monkeypatch.setattr(storage, "__file__", str(module))
    assert storage.create_database() == EXPECTED_COUNTS
    assert (module.parent / "store.db").is_file()


def test_foreign_keys_and_item_primary_key_enforced(csv_directory, tmp_path):
    database = tmp_path / "store.db"
    storage.create_database(csv_directory, database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO order_items VALUES (9999, 1, 81)")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO order_items SELECT * FROM order_items LIMIT 1"
            )
        connection.rollback()


@pytest.mark.parametrize("table, column, value, message", [
    ("products", "category_id", "9999", "FOREIGN KEY"),
    ("products", "price_brl", "nan", "Invalid number"),
    ("products", "price_brl", "-1", "CHECK constraint"),
    ("products", "stock_quantity", "-1", "CHECK constraint"),
    ("products", "name", " ", "Empty required field"),
    ("products", "created_at", "invalid", "isoformat"),
    ("products", "specs", "[]", "JSON object"),
    ("products", "specs", "invalid", "Expecting value"),
    ("promotions", "discount_percent", "101", "CHECK constraint"),
    ("promotions", "is_active", "2", "CHECK constraint"),
    ("order_items", "quantity", "0", "CHECK constraint"),
])
def test_invalid_csv_rolls_back_entire_import(csv_directory, tmp_path,
                                            table, column, value, message):
    database = tmp_path / "store.db"
    storage.create_database(csv_directory, database)
    change_first_row(csv_directory, "categories", "name", "Changed name")
    change_first_row(csv_directory, table, column, value)
    with pytest.raises(ValueError, match=message):
        storage.create_database(csv_directory, database)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT name FROM categories WHERE category_id=1"
        ).fetchone()[0] != "Changed name"


@pytest.mark.parametrize("problem, expected", [
    ("header", "Unexpected header"),
    ("duplicate", "Duplicate primary key"),
    ("extra", "extra columns"),
    ("missing", "Missing column value"),
])
def test_malformed_csv_has_file_and_line_context(csv_directory, tmp_path,
                                               problem, expected):
    path = csv_directory / "categories.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    if problem == "header":
        rows[0][0] = "unexpected"
    elif problem == "duplicate":
        rows.append(rows[1])
    elif problem == "extra":
        rows[1].append("extra")
    else:
        rows[1].pop()
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)
    with pytest.raises(ValueError, match=expected) as caught:
        storage.create_database(csv_directory, tmp_path / "store.db")
    assert "categories.csv" in str(caught.value)


def test_updated_ids_update_existing_rows(csv_directory, tmp_path):
    database = tmp_path / "store.db"
    storage.create_database(csv_directory, database)
    change_first_row(csv_directory, "categories", "name", "Updated category")
    storage.create_database(csv_directory, database)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT name FROM categories WHERE category_id=1"
        ).fetchone() == ("Updated category",)
