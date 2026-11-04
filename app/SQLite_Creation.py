"""Create the store database from related CSV files."""

import csv
import json
import math
import sqlite3
from datetime import date
from pathlib import Path


def create_database(data_dir=None, database_path=None):
    """Create related tables and import all six CSV files atomically.

    Args:
        data_dir: CSV directory; defaults to data beside this module.
        database_path: SQLite file; defaults to store.db in data_dir.

    Returns:
        A dictionary mapping table names to imported row counts.

    Existing keys are updated; rows absent from CSVs are retained.
    Invalid data rolls back the entire import and raises an exception.
    The database connection is always closed before returning.
    """
    base_dir = Path(__file__).resolve().parent
    # Import parent records before the rows that reference them.
    table_schemas = {
        "categories": """
            category_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL
        """,
        "customers": """
            customer_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT NOT NULL,
            city TEXT NOT NULL
        """,
        "products": """
            product_id INTEGER PRIMARY KEY,
            price_brl NUMERIC NOT NULL CHECK (price_brl >= 0),
            name TEXT NOT NULL,
            category_id INTEGER NOT NULL REFERENCES categories(category_id),
            description TEXT NOT NULL,
            stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
            status TEXT NOT NULL,
            specs TEXT NOT NULL,
            created_at TEXT NOT NULL
        """,
        "orders": """
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
            order_date TEXT NOT NULL,
            status TEXT NOT NULL,
            total_brl NUMERIC NOT NULL CHECK (total_brl >= 0),
            payment_method TEXT NOT NULL,
            tracking_code TEXT,
            estimated_delivery TEXT,
            notes TEXT
        """,
        "order_items": """
            order_id INTEGER NOT NULL REFERENCES orders(order_id),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            product_id INTEGER NOT NULL REFERENCES products(product_id),
            PRIMARY KEY (order_id, product_id)
        """,
        "promotions": """
            promotion_id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(product_id),
            discount_percent NUMERIC NOT NULL
                CHECK (discount_percent BETWEEN 0 AND 100),
            description TEXT NOT NULL,
            is_active INTEGER NOT NULL CHECK (is_active IN (0, 1))
        """,
    }

    integer_columns = {
        "category_id", "customer_id", "product_id", "order_id",
        "promotion_id", "stock_quantity", "quantity", "is_active",
    }
    decimal_columns = {"price_brl", "total_brl", "discount_percent"}
    date_columns = {"created_at", "order_date", "estimated_delivery"}
    optional_columns = {"tracking_code", "estimated_delivery", "notes"}
    foreign_key_indexes = {
        "products": "category_id",
        "orders": "customer_id",
        "order_items": "product_id",
        "promotions": "product_id",
    }

    def convert_value(column, value):
        """Convert and validate CSV values."""
        if value is None:
            raise ValueError(f"Missing column value: {column}")
        if not value.strip():
            if column in optional_columns:
                return None
            raise ValueError(f"Empty required field: {column}")
        if column in integer_columns:
            return int(value)
        if column in decimal_columns:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"Invalid number in {column}: {value}")
            return number
        if column in date_columns:
            return date.fromisoformat(value).isoformat()
        if column == "specs":
            if not isinstance(json.loads(value), dict):
                raise ValueError("specs must contain a JSON object")
        return value

    def import_csv(connection, table, data_dir):
        """Import a table and update existing IDs without duplicating rows."""
        columns = [
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        ]
        primary_keys = [
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            if row[5]
        ]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column} = excluded.{column}"
            for column in columns if column not in primary_keys
        )
        statement = (
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(primary_keys)}) DO UPDATE SET {updates}"
        )
        seen_keys = set()
        count = 0
        path = data_dir / f"{table}.csv"
        with path.open(encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            if reader.fieldnames != columns:
                raise ValueError(
                    f"Unexpected header in {path.name}: {columns}"
                )
            for row in reader:
                try:
                    if None in row:
                        raise ValueError("Row contains extra columns")
                    values = {
                        column: convert_value(column, row[column])
                        for column in columns
                    }
                    key = tuple(values[column] for column in primary_keys)
                    if key in seen_keys:
                        raise ValueError(f"Duplicate primary key: {key}")
                    seen_keys.add(key)
                    connection.execute(
                        statement, tuple(values[column] for column in columns)
                    )
                except (ValueError, sqlite3.IntegrityError) as error:
                    raise ValueError(
                        f"{path.name}, line {reader.line_num}: {error}"
                    ) from error
                count += 1
        return count

    data_dir = Path(data_dir) if data_dir is not None else base_dir / "data"
    database_path = ("app/store.db")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN")
            for table, schema in table_schemas.items():
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} ({schema})"
                )
            for table, column in foreign_key_indexes.items():
                connection.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_{column} "
                    f"ON {table} ({column})"
                )
            counts = {
                table: import_csv(connection, table, data_dir)
                for table in table_schemas
            }
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Database contains invalid relationships")
        return counts
    finally:
        connection.close()