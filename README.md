# Artefact AI Engineering Project

## Design assumptions: workload data was not provided

The requirements do not specify expected request volume, peak concurrency,
data growth, response-time targets, or the frequency and distribution of
repeated questions. These missing inputs limit the basis for architectural
choices. Every decision described below should be read in that context,
not as an optimal design for a measured production workload.

This implementation assumes a small store dataset and low concurrent demand,
prioritizing local execution, modest hardware requirements, cost, and a clear
separation between model interpretation, factual retrieval, and generation.
These are working assumptions, not conclusions supported by usage metrics.

Explicitly different workloads could change the entire design:

| Workload evidence | Decisions that would need reconsideration |
| --- | --- |
| Very low data and request volumes | Whether an LLM, semantic index, and API service justify their complexity; direct queries and maintained FAQ responses may be sufficient |
| Large datasets | Storage engine, indexes, query plans, pagination, retrieval/indexing strategy, and update processes |
| High request volume or concurrency | Inference hosting, model size, batching, queues, worker isolation, resource scaling, and operational controls |
| A few questions account for most requests | Dedicated handlers or maintained FAQ answers, intent/result caching with explicit invalidation, and reducing model calls for common questions |
| Mostly unique questions or frequent data changes | The usefulness of caching, freshness guarantees, retrieval cost, and the balance between general generation and specialized flows |

Consequently, SQLite, local Ollama inference, serialized requests, semantic
PDF retrieval, bounded interpretation caches, and the current tool workflow
are provisional choices for this exercise. Reliable measurements could lead
to replacing these components or simplifying the workflow, not merely tuning
their parameters. No throughput, capacity, or production latency guarantee
is implied by the local tests and small-sample benchmarks.

Before treating this as a production design, collect representative question
logs, repeated-intent frequencies, request rates, concurrency peaks, data
sizes/growth, update frequency, and latency/cost objectives. Reevaluate the
following implementation decisions against that evidence.

A local store-assistant API. The LLM chooses the information source and query
arguments, tools retrieve factual evidence, and the LLM writes the final
answer. SQL execution and price calculations are deterministic; model
interpretation and wording are not.

## Quick start

After installing the prerequisites below, run from the repository root:

```text
make install
make run
```

Open **http://127.0.0.1:8000/docs**, expand **POST /questions**, click
**Try it out**, type your question in the `question` box, and click **Execute**.
No JSON request body is required. Press **Ctrl+C** in the server terminal to
stop the API and release resources started by the project.

No virtual-environment activation, manual model commands, server commands,
or runtime parameters are needed for the normal workflow. On subsequent
sessions, use only `make run`. Run `make install` again when dependencies
change or when repairing an incomplete setup.

### Prerequisites: Windows

- Python 3.10+ on PATH; Python 3.12 is the development/test version.
- GNU Make on PATH. The development machine uses Chocolatey's GNU Make.
- For NVIDIA acceleration, an appropriate current NVIDIA driver.
- Internet access and enough free disk space for the runtime, dependencies,
  both models, and temporary extraction. Model weights alone are about 2.6 GB;
  the runtime and its extraction require additional space.

Check prerequisites once in PowerShell or the VS Code integrated terminal:

```powershell
python --version
make --version
```

Then use `make install` followed by `make run`. Make no longer depends on
PowerShell-specific aliases or execution-policy changes. It delegates tasks
to Python and always uses the project's `.venv/Scripts/python.exe` afterward.
The legacy `scripts/setup_ollama.ps1` entry point only delegates to the same
portable installer; it is not part of the normal workflow.

### Prerequisites: Linux

The automatic runtime installer supports Linux x86_64 and ARM64. For
Ubuntu/Debian, install prerequisites once (adapt for other distributions):

```bash
sudo apt-get update
sudo apt-get install python3 python3-venv make tar zstd
```

Use a supported Python version; 3.12 is recommended. Then, as your normal
user in the repository root:

```bash
make install
make run
```

Do not use `sudo make install`: dependencies belong in the project environment
and model files in your user account. Make automatically bootstraps with
`python3` on Linux and uses `.venv/bin/python` for project tasks.
For another available interpreter, the optional override is
`make PYTHON=python3.12 install` for creation of a new environment. An existing
`.venv` is reused; this override does not upgrade its interpreter. No override
is required for a standard setup.

The standalone runtime is extracted under `~/.local` without creating a
systemd service. Make adds its executable directory to child processes' PATH;
no permanent PATH edit is needed. An existing Ollama installation is reused.
If an existing system service owns Ollama, stop it through your service
manager first when you want the project to own the complete server lifecycle.

### Platform support and verification

Windows x64 and Linux x64/ARM64 have automatic runtime installation paths.
The same Make targets are available on both. Windows command dispatch and
application behavior are checked locally; Linux command construction and
installer behavior are checked with mocks, not an end-to-end Linux run.
macOS runtime installation is not automated. A manually installed Ollama may
be discoverable there, but macOS execution has not been validated.
GPU support and actual speed depend on the runtime, drivers, and hardware.

## What the commands execute, and why

Each task prints its purpose and exact subprocess command before execution.
Failures stop the sequence instead of continuing with incomplete setup.
The Makefile is a small front end to `scripts/project.py`, whose bootstrap
requires only the Python standard library.

| Command | Actions and purpose |
| --- | --- |
| `make install` | Create/reuse `.venv`; install runtime and test dependencies; install/reuse Ollama; download missing generation and embedding weights |
| `make run` | Use `.venv`; create/import SQLite; start/reuse Ollama; load generation weights; build the PDF semantic index; start the API |
| `make test` | Run functional tests, coverage, offline speed checks, and show slow tests; no real inference by default |
| `make test-live` | Include actual local-model latency checks; requires completed setup |
| `make benchmark` | Measure full startup, request stages, and shutdown using actual models |
| `make help` | Show the primary commands and optional repair tasks |

`make install` installs `requirements-dev.txt`, which includes
`requirements.txt`, so `make test` works without a second dependency command.
Packages stay inside `.venv`; the globally installed Python only bootstraps
and dispatches tasks. Environment activation is optional for IDE/manual work.

### Installation lifecycle

1. Create `.venv` if it does not already exist.
2. Install Python packages for the API, tools, and tests.
3. Reuse Ollama from PATH or its standard user location. Otherwise download
   the official standalone archive and extract it into the user installation.
   This avoids the desktop installer's automatic login/server behavior.
4. Start a temporary Ollama server only if no server is already available.
5. Check `llama3.2:3b` and `embeddinggemma` (or `PDF_EMBEDDING_MODEL`). Download
   missing weights, skip installed ones, and show download/timing progress.
6. Stop the temporary server started by setup. No API is launched and no
   inference is performed. A server that existed beforehand is left running.

Ollama's download API needs a running server: setup therefore uses a temporary
one for preparation, not a persistent application service. The first install
can take a while because it downloads packages, runtime libraries, and weights.
Repeating setup skips installed runtime/model downloads, but does not force
model or runtime upgrades. Installation failures can be retried with
`make install`.

### Application startup and shutdown

Before accepting any request, the FastAPI lifespan:

1. Runs `create_database()` from `app/infrastructure/database/sqlite.py`.
   All six `data/*.csv` files are imported into `app/store.db` in one
   transaction. Existing IDs are updated without duplicate rows. Records absent
   from CSVs are retained; this is an upsert, not a destructive rebuild.
2. Starts Ollama if needed, verifies the generation model, and loads it.
3. Extracts the policy PDF and builds its in-memory semantic index using the
   embedding model. Both models are retained during the API's lifetime.
4. Exposes the API at `http://127.0.0.1:8000` when preparation succeeds.

`make run` never installs packages or downloads missing weights. Missing files,
invalid CSVs, or missing models abort startup; repair with `make install`.
CSV edits take effect on the next startup; there is no CSV file watcher.
Startup upserts overwrite matching database rows from those source files.
Startup can still take time to load weights and embed PDF chunks. Trace logs
show the current step and elapsed time.

Graceful shutdown unloads the models, clears PDF caches, closes clients, and
stops only the Ollama process started by the project. A pre-existing server
is never terminated, although model residency is shared and the application
requests model unload on exit. Quit a separately installed Ollama tray app
first if you want the project to own server shutdown. Forced termination or
power loss may bypass Python cleanup. Weights and database files remain on disk.

### Optional repair commands

These split setup for troubleshooting; none is needed after a successful
`make install`:

| Command | Scope |
| --- | --- |
| `make pip` | Create/reuse `.venv` and install Python runtime/test packages |
| `make ollama` | Install/reuse the runtime only; no server or model loading |
| `make models` | Prepare both required model weights using a temporary server |
| `make model` | Prepare only `llama3.2:3b` |
| `make embeddings` | Prepare only the configured embedding model |

The compatibility command `make test LIVE=1` is equivalent to `make test-live`.
Prefer the named target; no parameter is needed.

## Question API

The only business endpoint is **POST /questions** with a required `question`
query parameter. Swagger is at `/docs`; the schema is at `/openapi.json`.

Example from another PowerShell terminal:

```powershell
$question = [Uri]::EscapeDataString("How much is the Kalani KAL-700T Tenor Natural?")
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/questions?question=$question"
```

On Linux:

```bash
curl -X POST -G http://127.0.0.1:8000/questions \
  --data-urlencode 'question=How much is the Kalani KAL-700T Tenor Natural?'
```

The JSON response contains `answer`, `question_type`, and `requires_lookup`.
The latter indicates the required source, not a pending implementation.

| Question type | Behavior |
| --- | --- |
| `database` | Validated SQL tool, then LLM answer from returned facts |
| `pdf` | Semantic policy retrieval, then LLM answer with supporting pages |
| `database_and_pdf` | Both tools, then a response addressing both parts |
| `greeting` | Fixed store greeting |
| `out_of_scope` | Fixed refusal for unrelated subjects |

Missing/blank questions return 422. Unavailable database/PDF/model services
return 503; model timeouts return 504; invalid model plans and other inference
failures return 502. Startup errors prevent serving entirely. Inference and
lookups are serialized to respect the target machine's memory budget; use one
Uvicorn worker. Answers are independent; conversation history is not stored.

## Tools and answer guarantees

### Database

The LLM chooses an entity, operation, and filters using a JSON schema.
Application-owned allowlists compile the plan into parameterized SQL. Raw
model-authored SQL is never executed. Each request uses a closed-after-use,
read-only connection with an execution budget. Query values are bound parameters.

Supported data includes products/categories, customers, orders/items, and
promotions. Operations include listing, a focused price lookup, catalog
category overview, count, sum, average, minimum, and maximum over allowed
columns. Lists default to five rows, at most ten. Overviews cover populated
categories independently of product sampling, capped at 100 category rows
with explicit truncation and global totals. Defined categories without products
are excluded. Arbitrary joins/grouping and historical unit-price calculations
are unsupported; the CSVs do not contain historical per-item prices.

Product price lookups return original price, active discount, effective price,
and enough status/stock data to identify ambiguity or unavailability. Only
promotions with `is_active=1` affect the current price. Decimal arithmetic with
half-up cent rounding calculates the price before generation. No discount is
stacked, and multiple active promotions require confirmation rather than an
invented priority. Original order totals are preserved.

The LLM writes a concise answer; Python does not dump all columns or append a
fixed receipt. A promotional quote must include original price, percentage,
and payable price. Evidence checks allow one LLM correction request;
if the second draft still omits required values, it is not published. This
checks disclosure presence, explicit currency amounts against tool evidence,
and a citation to a retrieved PDF page. It does not establish semantic
entailment of every claim. Mixed questions use a separate prompt to preserve
both the database and policy parts, even if one tool finds nothing. Invalid
database plans get one validation-feedback retry before execution; omitted
name operators default to partial matching and identifier filters must be
positive integers. For example, an answer
can naturally say that a product originally priced at BRL 359.00 is now
BRL 323.10 with 10% off.

SQL facts are deterministic for the same plan and database snapshot.
Classification, plan selection, and final prose remain model-based. Adding
punctuation may cause a new interpretation; no claim of paraphrase invariance
or complete end-to-end determinism is made.

Classification and validated plans have per-service LRU caches of 128 exact
stripped questions. Rows, stock, prices, and final answers are never cached.
Every repeated question executes fresh SQL and generates a new answer.
Failed interpretations are not cached; restarting clears the caches.

### Policy PDF

`app/tools/pdf_search.py` extracts page-labelled chunks from the policy PDF,
embeds them with `embeddinggemma`, and retrieves up to three excerpts using
cosine similarity. Query/document prefixes follow EmbeddingGemma's format.
The index is prepared at startup, retained in memory, and invalidated when
file metadata or the configured embedding model changes.

The LLM receives retrieved evidence and cites supporting pages. Similarity
thresholds are heuristics, not probabilities; retrieval and generation may
still be incomplete or wrong. Scanned documents need OCR, which is not
implemented. General return rules do not establish eligibility of a specific
product/order without evidence.

## Tracing and diagnostics

Console logs show `START`, `WAIT`, `DONE`, and `FAIL` with timestamps and elapsed
seconds. Long operations emit `WAIT` every five seconds. Logs identify startup,
classification, database planning/execution, PDF retrieval, generation, and
cleanup. `DATABASE_RESULT` distinguishes sampled rows from matching catalog
totals. Cache-hit flags show which interpretation calls were reused.

Ollama metrics report load time, prompt evaluation, generation time, token
counts, and throughput. Nested stage times are inclusive; do not add parent
and child durations. `WAIT` shows a pending operation, not percentage progress.
At INFO level, question/tool/model payloads and final answers are also logged.
`TRACE_LOG_FILE` optionally appends logs to a file; `LOG_LEVEL=WARNING` hides
progress. Configuration is optional, not required for normal commands.

If startup fails, read the first failed stage. SQLite errors usually concern
CSV/path/constraint problems; model errors usually require completed setup;
Ollama process errors include the temporary server log's path. Do not interpret
a connection failure as proof of insufficient VRAM.

## Tests, coverage, and speed

```text
make test
make test-live
make benchmark
```

`make test` runs pytest, branch coverage, an HTML report at `htmlcov/index.html`,
offline speed tests, and a list of slow tests. Ollama and embeddings are mocked
by default; real SQLite files and PDF extraction are exercised using test
resources. Temporary databases do not modify `app/store.db`. Tests use a fresh
local temporary directory under `.pytest_cache/` and clean it afterward.
Use the generated report for current coverage; fixed test counts/percentages
in older notes are not maintained as guarantees.

Default performance checks measure 20 SQL and ASGI samples with mocked
inference, verify interpretation-call reuse, and verify that a changed
promotion affects the next answer. These timings are not real-model SLAs.
`make test-live` adds actual Ollama timing, model-call checks, and fresh answer
generation on every repetition. It needs the generation weights from setup,
does not download anything, and excludes initial model loading from timed
questions. Small sample counts are not throughput or p95 guarantees.

`make benchmark` exercises full API startup, PDF indexing, sequential requests,
and shutdown with actual models. It records stage timings, responses, Ollama
metrics, and available GPU snapshots. Older measurements from the removed
static-response formatter do not describe the current LLM-formatted path.

| Artifact | Source |
| --- | --- |
| `htmlcov/index.html` | Current pytest coverage |
| `reports/performance/test-speed.json` | Default offline timing tests |
| `reports/performance/test-speed-live.json` | Opt-in actual-model timing tests |
| `reports/performance/latest.json` | Full pipeline benchmark |

Reports, `.venv`, caches, logs, and generated SQLite files are ignored by Git.

## Configuration and hardware

Defaults need no parameters. Advanced optional settings:

| Setting | Effect |
| --- | --- |
| `PDF_EMBEDDING_MODEL` | Embedding weights prepared by install and used for PDF retrieval |
| `PDF_MIN_SIMILARITY` | Cosine threshold, default 0.3; range -1 to 1 |
| `STORE_DATABASE_PATH` | SQLite destination for both startup import and lookups; parent must exist |
| `TRACE_LOG_FILE` | Append diagnostic logs to this path |
| `LOG_LEVEL` | Log verbosity, default INFO |
| `PERF_SQL_MEDIAN_SECONDS` | Offline SQL timing budget, default 0.25 s |
| `PERF_API_MEDIAN_SECONDS` | Mocked-inference ASGI timing budget, default 0.5 s |
| `PERF_LIVE_COLD_SECONDS` | First live-question budget, default 30 s |

A compatible model alone is not enough when changing `PDF_EMBEDDING_MODEL`:
retrieval prefixes may also need adaptation. Re-run `make models` after changing
that setting, then restart. A persistent `.env` loader is not implemented;
settings must be present in the environment of the Make process.

The development target is a GTX 1650 (4 GB VRAM) with 16 GB system RAM.
The approximately 2 GB quantized Llama 3.2 3B supports Portuguese; the embedding
model is about 622 MB. Context cache and runtime overhead also consume memory.
The default 2,048-token generation context and 256-token output budget are a
starting point, not a guarantee that both models always fit entirely on GPU.
Ollama chooses GPU/CPU placement; integrated Intel graphics memory does not
add to NVIDIA VRAM. Llama 3.3 70B's roughly 43 GB quantized weights are unsuitable
for this machine. Local inference needs no paid API, but still consumes local
resources and is subject to model licenses.

## Architecture and limits

```text
app/app.py                         HTTP endpoint
app/api/                           HTTP schemas, DI, errors, startup/shutdown
app/services/questions.py          Classification -> tools -> answer workflow
app/services/answer_generation.py  LLM wording and promotion disclosure checks
app/services/classification.py     Model-based intent classification
app/services/responses.py          Fixed greetings/refusals
app/tools/database.py              Read-only validated database plans
app/tools/pdf_search.py            Semantic PDF evidence
app/infrastructure/database/       Transactional CSV importer
app/infrastructure/llm/            Model, server, and preparation lifecycle
app/prompts/                       Versioned UTF-8 system prompts
app/core/tracing.py                Timings and diagnostics
scripts/project.py                 Portable Make task dispatcher and bootstrap
scripts/prepare_models.py          Required model checks/download orchestration
```

The use case is independent of FastAPI. HTTP code validates/delegates and maps
application errors to statuses. Prompts are loaded relative to the package and
cached; restart after changing them. A low-volume local assistant is assumed:
there is no calibrated capacity target, authentication layer, distributed
cache, or multi-user conversation state. Stronger latency or concurrency
requirements would require reevaluating the inference/service design rather
than silently substituting fixed row listings for conversational answers.

The standalone SQLite importer also accepts custom CSV/database paths. It
validates required fields, ISO dates, JSON specs, ranges, and relationships.
Empty optional fields become NULL; phones remain text; repeated emails in the
source are not constrained to be unique. `order_items` uses the composite key
`(order_id, product_id)`. Foreign keys connect products/categories,
orders/customers, items/orders/products, and promotions/products. Non-importer
connections must enable SQLite foreign keys when performing writes.

## Sources

- [Ollama Windows runtime](https://docs.ollama.com/windows)
- [Ollama Linux manual installation](https://docs.ollama.com/linux)
- [Llama 3.2 model](https://ollama.com/library/llama3.2:3b)
- [EmbeddingGemma model](https://ollama.com/library/embeddinggemma)
- [Ollama Python client](https://github.com/ollama/ollama-python)
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [pytest coverage reports](https://pytest-cov.readthedocs.io/en/latest/reporting.html)
