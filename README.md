# Artefact AI Engineering Project

## Application lifecycle

### Execution tracing and performance

`make run` and the Uvicorn command above enable timestamped console logs.
Importing `app.app` defines the API without loading the LLM. Launch the API
through Uvicorn rather than running `app/app.py` directly.
`make model` also enables tracing during preparation. Logs appear immediately
on standard error; generated answers continue to use standard output.
No additional logging dependency is required.

Each instrumented stage emits `START`, then `DONE` with elapsed seconds or
`FAIL` with the traceback. Blocking stages emit `WAIT` every five seconds,
so you can identify the active operation before it finishes. Example format
(illustrative timings):

```text
2026-11-03 12:00:00.000 | INFO | START llm.load
2026-11-03 12:00:05.000 | INFO | WAIT llm.load | elapsed=5.000s
2026-11-03 12:00:07.200 | INFO | DONE llm.load | elapsed=7.200s
```

| Stage | What it measures |
| --- | --- |
| api.question | HTTP question processing, including the inference queue |
| server.startup | API check, optional process launch, and readiness wait |
| llm.check_model | Verify local model availability |
| llm.load | Initial model load request |
| llm.generate | Response generation request and response processing |
| llm.unload | Release the model on an owned server |
| llm.client_close | Close HTTP client resources |
| server.shutdown | Stop and wait for the owned server process |
| model.download | Stream model preparation/download |

`METRICS llm.generate` reports Ollama's total duration, model loading time,
prompt evaluation time, output generation time, output token count, and
tokens per second, when available. These server-side durations are converted
from nanoseconds to seconds; tokens per second uses output generation time.
See the [Ollama response fields](https://docs.ollama.com/api/chat).

Elapsed stage times use a monotonic performance clock and include waiting
and request overhead. Stages can be nested: do not add application time to
its child timings. `WAIT` means the operation has not returned; it does not
indicate the model's internal percentage of completion. Compare repeated
runs and separate initial model loading from generation when optimizing.

To retain logs across runs, set `TRACE_LOG_FILE`:

```powershell
$env:TRACE_LOG_FILE = "logs/execution.log"
make run
```

On Linux:

```bash
TRACE_LOG_FILE=logs/execution.log python -m uvicorn app.app:app --host 127.0.0.1 --port 8000
```

Log files are appended and `*.log` is already ignored by Git. Set `LOG_LEVEL`
to `WARNING` to hide stage progress. To enable tracing when importing project
functions elsewhere, call `configure_logging()` once at your entry point.
Additional operations can use the same timer:

```python
from app.tracing import configure_logging, trace_step

configure_logging()
with trace_step("database.import"):
    counts = create_database()
```

### Resource ownership

The application owns startup and shutdown through a context manager:

```python
from app.initialize_llm import initialize_llm

with initialize_llm() as generate_response:
    reply = generate_response("Hello!")
    print(reply)
```

Initialization starts a background server if needed, waits up to 30 seconds
for readiness, verifies the model exists, and loads it from disk for inference.
It never downloads weights. A missing model raises an error asking you to run
`make model`. Loading weights into RAM/VRAM still takes time at startup, but
network download time is confined to the preparation step.

Leaving the `with` block unloads the model, stops the server started by the
project, and closes the client. Cleanup also runs on startup failures,
application exceptions, and normal Ctrl+C interruption. A pre-existing server
is reused and never terminated; requests to that shared server use zero
keep-alive so they do not leave the model loaded. Forced process termination
or power loss can bypass Python cleanup. To let the project control the full
server lifecycle, quit the Ollama tray application before running it.

The default `llama3.2:3b` model uses Q4_K_M quantization, is approximately 2 GB,
and supports Portuguese. A 2,048-token context and 256-token output budget
are a starting point for the GTX 1650 (4 GB VRAM) and 16 GB system RAM.
Runtime memory includes context and inference overhead; measure GPU placement
and speed on the target machine. Intel integrated graphics memory does not
add to NVIDIA VRAM. If necessary, use `initialize_llm(model="llama3.2:1b")`.
Llama 3.3 70B's approximately 43 GB quantized download is unsuitable here.
Local inference needs no paid API; Meta's community license applies.

Importing `app.initialize_llm` has no startup side effects. Responses are
independent; conversation history is not stored. A custom system prompt can
be supplied as the response function's second argument. Server launch logs
are written to `artefact-ollama-server.log` in the system temporary directory.
Routing, SQL access, PDF retrieval, and semantic embeddings are not yet wired.

Validation uses pytest; see the testing and coverage section below.
Ollama services and downloads are mocked, and SQLite uses temporary files.

Sources: [model](https://ollama.com/library/llama3.2:3b),
[languages](https://ollama.com/library/llama3.2),
[standalone Windows runtime](https://docs.ollama.com/windows), and
[Python client](https://github.com/ollama/ollama-python).

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

