# Artefact AI Engineering Project

## SQLite import

Use the single public function from Python 3.9 or later. No external
packages are required. Importing the module performs no database operations.

```python
from app.SQLite_Creation import create_database

counts = create_database()
```

By default, CSVs are read from the project-root `data` directory and the
database is created at `app/store.db`. You can supply both paths:

```python
counts = create_database(
    data_dir="path/to/csvs",
    database_path="path/to/store.db",
)
```

The destination directory must exist. If only `data_dir` is supplied, the
database still defaults to `app/store.db`, independently of `data_dir`. The return value maps
each table name to its imported row count. Errors propagate to the caller.

| Table | Primary key | Foreign keys |
| --- | --- | --- |
| categories | category_id | None |
| customers | customer_id | None |
| products | product_id | category_id references categories |
| orders | order_id | customer_id references customers |
| order_items | (order_id, product_id) | order_id references orders; product_id references products |
| promotions | promotion_id | product_id references products |

The composite key in `order_items` allows one row per product in each order;
`quantity` records how many units were purchased.

The function validates ISO dates, JSON specifications, quantities, discounts,
and references. Empty optional fields become `NULL`. Phone numbers remain
text. Emails are not unique because the source contains a repeated address.
Source CSV contents are preserved in their original language.

Repeated calls update matching keys without duplicating rows. Rows absent
from the CSVs remain in the database. Any failure rolls back the transaction,
and the connection is always closed. Other connections must also enable
`PRAGMA foreign_keys = ON` to enforce foreign keys.

Monetary columns preserve the CSV names and amounts in Brazilian reais with
`NUMERIC` affinity. SQLite may store fractional values as floating point;
use integer cents if exact financial calculations are required. Order totals
are preserved because the CSVs do not provide historical item prices.


## Testing and coverage

Install development dependencies inside the active virtual environment:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

`pytest.ini` enables branch
coverage and reports uncovered lines in the terminal. It also generates
`htmlcov/index.html`, which can be opened in a browser to inspect individual
lines and branches. Coverage artifacts are ignored by Git.

Tests use a fresh temporary directory inside `.pytest_cache/` for each run
and remove it on exit. This avoids permission errors in shared Windows
`Temp/pytest-of-*` directories. An explicit `--basetemp` overrides this default.

