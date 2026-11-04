# Artefact AI Engineering Project

## Technical decisions and assumptions

The requirements do not specify question volume, concurrent demand, the
frequency of repeated questions, or a response-time target. I therefore chose
a general-purpose approach, assuming a low volume of questions and no need
for near-zero response latency. These are design assumptions rather than
measured workload characteristics, and should be revisited with actual usage.

If a small set of questions accounts for a large share of requests, dedicated
functionality would be more appropriate for those answers. Frequent questions
such as "Quais os produtos vocês vendem?" or "Qual o horário de funcionamento?"
could be answered deterministically from the product catalog or configured
business information, without invoking an LLM for each response. This would
reduce inference cost and latency while keeping answers tied to maintained
data. The current implementation classifies intent, but does not yet provide
these deterministic catalog or policy lookups.

Cost and the available hardware were the main drivers of the model choice.
The project uses a compact, quantized `llama3.2:3b` model running locally to
balance language capabilities with modest computational requirements and
avoid per-request charges from a hosted model API. Local execution still
incurs hardware and electricity costs, and its answer quality and performance
need to be evaluated against the intended workload.

Under the low-volume assumption, inference latency was given lower priority
than cost and resource use. This does not establish that latency is
unimportant to users: higher demand, stricter response-time targets, or a
high rate of repeated questions would justify revisiting the architecture,
including deterministic handlers, caching, and the inference infrastructure.

## Project initialization

Run all commands from the repository root, which contains `requirements.txt`,
`Makefile`, and the `app` directory. Python 3.12 is the recommended version
and the version used during development. Installation and model preparation
require internet access; later inference uses the locally downloaded weights.

### Operating system compatibility

| Component | Windows | Linux |
| --- | --- | --- |
| Makefile | Uses Windows PowerShell (`powershell.exe`) | Not supported by the current recipes |
| Runtime installation script | Installs Windows x64 binaries | Not supported; install manually below |
| Python application and model preparation | Supported by the implementation | Designed to work with Ollama on PATH |
| Server startup/shutdown | Windows process handling | Unix process handling |

Windows installation checks and mocked lifecycle tests have been run.
The Linux instructions follow Ollama's documentation but have not been
validated end to end on a Linux machine. GPU acceleration also depends on
the drivers and hardware supported by Ollama. macOS setup is not covered here.

### Windows: step by step

1. Install Python 3.12 with Python on PATH and install GNU Make. Use PowerShell
   in the repository root and verify the commands are available:

   ```powershell
   python --version
   make --version
   ```

2. Create and activate an isolated Python environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the Python dependencies and Ollama runtime:

   ```powershell
   make install
   ```

   `make pip` and `make ollama` run these steps separately. The setup script
   reuses an existing installation or extracts the standalone Windows archive
   into `%LOCALAPPDATA%/Programs/Ollama`. It does not start a server, download
   model weights, or create a login startup entry. The application finds that
   installation even when it is absent from PATH.

4. If you installed the Ollama desktop application separately, quit it from
   the system tray before continuing so the project can own server shutdown.
   Download the model once:

   ```powershell
   make model
   ```

   This step starts a temporary server, downloads approximately 2 GB for
   `llama3.2:3b`, and stops the server it started. It performs no inference.
   Existing servers are reused and left running. Weights stay on disk.

5. Start the project:

   ```powershell
   make run
   ```

   This starts the FastAPI server at `http://127.0.0.1:8000`. Leave the
   terminal open while sending questions. Press Ctrl+C to stop the API and
   release its owned server and model resources.

6. For later runs, activate the environment again and run `make run`.
   Repeat model preparation only when you need another model or update.
   To leave the environment after the application exits:

   ```powershell
   deactivate
   ```

### Linux: step by step

Use Bash and the Python commands below rather than the Windows Makefile.
The package installation example targets Ubuntu/Debian. Adapt package names
and installation commands for other distributions.

1. Install Python, virtual environment support, and download/extraction tools:

   ```bash
   sudo apt-get update
   sudo apt-get install python3 python3-venv python3-pip curl zstd
   python3 --version
   ```

   Python 3.12 is recommended. Ensure your distribution supplies a compatible
   Python version before proceeding.

2. From the repository root, create the Python environment and install the
   project dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

3. Install the Ollama runtime manually so installation does not start or
   enable a background service. For a new Linux x86_64 installation:

   ```bash
   uname -m
   curl -fL https://ollama.com/download/ollama-linux-amd64.tar.zst \
     -o /tmp/ollama-linux-amd64.tar.zst
   sudo tar --zstd -xf /tmp/ollama-linux-amd64.tar.zst -C /usr
   command -v ollama
   ```

   For ARM64 (`aarch64`), use `ollama-linux-arm64.tar.zst` in both the URL
   and local archive path. These are the archives listed in the
   [official Linux installation instructions](https://docs.ollama.com/linux).
   Skip the instructions for creating a startup service: the project manages
   its server. Ollama's automatic installer may configure a system service.
   If Ollama was already installed, reuse it rather than extracting over an
   active installation.

4. If an Ollama systemd service was previously installed, stop it and disable
   automatic startup before giving the project control of the server:

   ```bash
   sudo systemctl disable --now ollama.service
   ```

   Run this command only when that service exists and you want it disabled.
   Also stop any manually started `ollama serve` process in its own terminal.
   For NVIDIA acceleration, install the appropriate driver for your Linux
   distribution and verify it using `nvidia-smi`.

5. Download the model independently from application startup:

   ```bash
   python -c "from app.prepare_model import prepare_model; prepare_model()"
   ```

   Preparation downloads weights without performing inference. The temporary
   server is stopped afterward; a pre-existing server is left running.
   Run this command and the application as the same user, without `sudo`,
   so they use the same local model cache.

6. Start the project:

   ```bash
   python -m uvicorn app.app:app --host 127.0.0.1 --port 8000
   ```

   Leave the API running while sending requests and stop it with Ctrl+C.
   For later runs, activate `.venv` again and repeat this command; no download
   is needed. Run `deactivate` after stopping the API.

### Startup, shutdown, and troubleshooting

Normal startup never downloads weights. A missing model produces an error
asking for preparation: use `make model` on Windows or the preparation Python
command above on Linux. Loading local weights into RAM/VRAM still takes time.

Press Ctrl+C in the application's terminal to interrupt it and trigger
cleanup. Only servers started by the project are stopped. A pre-existing
Ollama server stays running; quit or stop it beforehand if you want the
project to manage the full lifecycle. Forced process termination or power
loss may bypass Python cleanup. Closing the Python environment with
`deactivate` does not stop an independently running Ollama server.

For a smaller model, prepare it explicitly and select the same name when
calling `initialize_llm(model="llama3.2:1b")`:

```bash
python -c "from app.prepare_model import prepare_model; prepare_model('llama3.2:1b')"
```

## Question API

The API exposes one question endpoint: `POST /questions`. It uses the existing
local LLM to classify the supplied question; it does not generate factual
answers or execute SQL/PDF lookups yet. Swagger is available at
`http://127.0.0.1:8000/docs`.
Expand `POST /questions`, click **Try it out**, enter text in the required
`question` parameter box, and click **Execute**. No JSON request body is needed.
The OpenAPI schema is available at `/openapi.json`.

Update your Python environment after pulling these API changes:

```powershell
python -m pip install -r requirements.txt
```

Prepare weights with `make model` on Windows or the preparation command
above on Linux, then start the API with `make run` (Windows) or:

```bash
python -m uvicorn app.app:app --host 127.0.0.1 --port 8000
```

Send a question from another PowerShell terminal:

```powershell
$question = [Uri]::EscapeDataString("Quais produtos vocês vendem?")
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/questions?question=$question"
```

On Linux:

```bash
curl -X POST -G http://127.0.0.1:8000/questions \
  --data-urlencode 'question=Quais produtos vocês vendem?'
```

Successful responses contain a fixed `answer`, the `question_type`, and
`requires_lookup`. For example:

```json
{
  "answer": "Esta pergunta necessita de consulta ao banco de dados da Empório da Música. Essa consulta ainda não está implementada.",
  "question_type": "database",
  "requires_lookup": true
}
```

| Question type | Intended source or response |
| --- | --- |
| `database` | Catalog, prices, stock, promotions, customers and orders |
| `pdf` | Store policies and institutional information, including opening hours |
| `database_and_pdf` | Questions requiring both sources |
| `greeting` | Fixed greeting introducing the store assistant |
| `out_of_scope` | Fixed refusal for subjects outside the store context |

Out-of-scope questions receive: "Não sei responder, pois meus conhecimentos
se limitam aos assuntos da Empório da Música." Greetings and out-of-scope
messages have `requires_lookup: false`; all other routes return a pending
lookup notice. No source contents are read in this phase. The model is called
only to classify intent, including greetings and unrelated questions. Its
output is constrained through Ollama's `format` JSON Schema to an object
containing `question_type` with one of the allowed labels. The application
also validates the returned JSON; invalid output returns HTTP 502
instead of being presented as an answer. Classification is model-based and
may be incorrect; mocked tests verify the contract, not semantic accuracy.

Missing or blank questions return HTTP 422. Query parameters are strings. An unavailable local
model server returns 503; inference timeouts return 504; other model failures
return 502. Startup fails if initialization cannot load the prepared model.

The FastAPI lifespan initializes the LLM once and releases its owned
resources when the API shuts down. Use a single Uvicorn worker for this
machine. Questions are handled one at a time to limit inference memory use;
blocking inference runs in FastAPI's thread pool. Existing stage tracing and
inference metrics remain enabled, with `api.question` timing each request.

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
Intent routing is implemented; SQL access, PDF retrieval, and semantic
embeddings are not yet wired.

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

On Windows, `make test` runs the same suite. `pytest.ini` enables branch
coverage and reports uncovered lines in the terminal. It also generates
`htmlcov/index.html`, which can be opened in a browser to inspect individual
lines and branches. Coverage artifacts are ignored by Git.

Tests use a fresh temporary directory inside `.pytest_cache/` for each run
and remove it on exit. This avoids permission errors in shared Windows
`Temp/pytest-of-*` directories. An explicit `--basetemp` overrides this default.

The test suite uses pytest fixtures, parametrization, and pytest-asyncio for
the ASGI interface. Each functional application module has its own tests:

| Application module | Test module | Latest combined line/branch coverage |
| --- | --- | --- |
| app/app.py | tests/test_app.py | 100% |
| app/question_routing.py | tests/test_question_routing.py | 100% |
| app/initialize_llm.py | tests/test_initialize_llm.py | 96% |
| app/ollama_server.py | tests/test_ollama_server.py | 97% |
| app/prepare_model.py | tests/test_prepare_model.py | 100% |
| app/tracing.py | tests/test_tracing.py | 99% |
| app/SQLite_Creation.py | tests/test_sqlite_creation.py | 98% |

The latest Windows run passed 104 tests with 98% overall combined line/branch
coverage, compared with the original 21 tests and 62%.

Tests never start real Ollama processes, download weights, or run inference.
Server launch options for Windows and Unix are checked through mocks; this
does not validate GPU drivers, actual model speed, or Linux execution.
SQLite tests import copies of the real CSV fixtures into temporary databases
and verify keys, constraints, updates, repeated imports, and transaction
rollback. They do not write to the project's `app/store.db`.

For a focused run (the coverage report still includes all application modules):

```bash
python -m pytest tests/test_sqlite_creation.py -v
```

Remaining uncovered paths include direct-script import fallbacks and
defensive checks. Coverage measures executed code, not answer quality or
production reliability. References: [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
and [coverage reporting](https://pytest-cov.readthedocs.io/en/latest/reporting.html).
