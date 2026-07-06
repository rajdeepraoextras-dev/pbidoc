# PBICompass — Enterprise Power BI Documentation Generator

AI-powered pipeline that ingests Power BI files (`.pbip` / `.pbix`), extracts
**metadata only**, and generates enterprise-grade documentation for both
technical BI developers and business stakeholders.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the full freemium-first
architecture and phased roadmap.

---

## Status — Phases 0–5 complete

The foundation is in place and tested (182 tests passing):

- **Canonical schemas** — the `model.json` and `document.json` contracts that
  every parser and AI agent keys off ([src/pbicompass/schemas](src/pbicompass/schemas)).
- **`.pbip` parser** — extracts the semantic model (TMDL **and** TMSL/`model.bim`)
  and the report layout (PBIR enhanced **and** legacy) into `model.json`
  ([src/pbicompass/parsers](src/pbicompass/parsers)).
- **`.pbix` adapter** — legacy binary files via a `pbixray` adapter that reads
  metadata frames only (never the VertiPaq `DataModel` rows), plus report layout
  from the ZIP. Degrades gracefully to layout-only if `pbixray` is unavailable
  ([src/pbicompass/adapters](src/pbicompass/adapters)).
- **AI agents** — Business Analyst, DAX Translator, and Data Modeler agents plus
  a deterministic Auditor turn `model.json` into the 7-section `document.json`
  ([src/pbicompass/agents](src/pbicompass/agents)). A **deterministic offline engine**
  produces the full document with no API key; pass `--provider anthropic` to use
  Claude (Opus 4.8, structured outputs) for the prose agents.
- **Renderers** — `document.json` → **Markdown, HTML, and DOCX** with no external
  tools (the styled HTML prints to PDF from any browser; the `.docx` is
  hand-written OOXML — no `python-docx`/`lxml`), plus **PDF** via an optional
  Pandoc adapter that degrades gracefully when Pandoc is absent
  ([src/pbicompass/render](src/pbicompass/render)).
- **Zero-retention web service** — a FastAPI app: upload a `.pbix` or a zipped
  `.pbip`, it processes inside a per-job sandbox (shredded in a `finally` block)
  and serves the rendered docs for a short TTL. Single-page upload UI at `/`,
  async jobs via a queue-agnostic worker (Celery-ready), zip-slip guard, no
  customer metadata persisted ([src/pbicompass/service](src/pbicompass/service)).
- **Auth & multi-tenancy** — optional API-key auth (SQLite-backed accounts —
  **no new dependency**), per-tenant job isolation, and per-plan freemium daily
  quotas (HTTP 429 when exceeded). Off by default (self-hosted/local); enable
  with `PBICOMPASS_REQUIRE_AUTH=1`
  ([src/pbicompass/service/accounts.py](src/pbicompass/service/accounts.py)).
- **Admin panel** — a token-gated `/admin` page to create, list, and revoke
  API keys from the browser (no shell/CLI access needed); brute-force lockout
  on the admin token itself. Enable with `PBICOMPASS_ADMIN_TOKEN`
  ([src/pbicompass/service/admin.py](src/pbicompass/service/admin.py)).
- **CLI** — `pbicompass parse`, `pbicompass generate`, `pbicompass serve`, and `pbicompass account`
  ([src/pbicompass/cli.py](src/pbicompass/cli.py)).
- **Tests** — end-to-end + unit tests against synthetic fixtures
  ([tests/](tests/)), including the LLM path via an in-process fake client.

### Zero-dependency core
The parser uses the Python **standard library only**. This keeps it portable,
easy to audit, and minimises the number of third-party libraries that ever
touch customer metadata. FastAPI/Celery/LangGraph/Pydantic are pulled in only
by later phases (declared as optional extras in `pyproject.toml`).

---

## What it extracts (metadata only — no row-level data)

| Section | Extracted |
|---|---|
| Tables | name, hidden flag, fact/dimension/calculation kind (heuristic) |
| Columns | data type, summarize-by, calculated-column DAX, sort-by, key flags |
| Measures | full DAX expression (multi-line preserved), format string, home table, display folder |
| Relationships | from/to table+column, cardinality, cross-filter direction, active flag |
| RLS roles | model permission, per-table filter DAX, members |
| M / Power Query | shared expressions + parameters; partition source per table |
| Data sources | connector + server/database inferred from M (credentials stripped) |
| Report | pages (ordinal/hidden/drill-through), visuals (type, title, fields, slicers) |

---

## Usage

No install required (src layout + `PYTHONPATH`):

```bash
# Windows PowerShell
$env:PYTHONPATH = "src"
python -m pbicompass parse "tests\fixtures\SampleSales\SampleSales.pbip" -o model.json

# bash
PYTHONPATH=src python -m pbicompass parse tests/fixtures/SampleSales/SampleSales.pbip -o model.json
```

Or install as a package to get the `pbicompass` command:

```bash
pip install -e .
pbicompass parse path/to/Project.pbip -o model.json
```

### Generate documentation

```bash
# Deterministic, offline, no API key — Markdown to stdout or a file
PYTHONPATH=src python -m pbicompass generate tests/fixtures/SampleSales/SampleSales.pbip -o report.md

# Emit the structured document.json instead
PYTHONPATH=src python -m pbicompass generate path/to/Project.pbip -o report.document.json

# AI-written prose (needs pip install -e ".[agents]" and the provider's key in the environment):
PYTHONPATH=src python -m pbicompass generate path/to/Project.pbip --provider anthropic -o report.md   # ANTHROPIC_API_KEY
PYTHONPATH=src python -m pbicompass generate path/to/Project.pbip --provider gemini    -o report.md   # GEMINI_API_KEY
PYTHONPATH=src python -m pbicompass generate path/to/Project.pbip --provider cohere    -o report.md   # COHERE_API_KEY

# Or one key for any of 1000+ models via https://developers.meshapi.ai (model ids are "provider/model-name"):
PYTHONPATH=src python -m pbicompass generate path/to/Project.pbip --provider meshapi --model anthropic/claude-opus-4-8 -o report.md   # MESHAPI_API_KEY
```

Output format is inferred from the `-o` extension (or forced with `--format`):

```bash
PYTHONPATH=src python -m pbicompass generate Project.pbip -o report.html   # styled HTML (print → PDF)
PYTHONPATH=src python -m pbicompass generate Project.pbip -o report.docx   # Word, no external tools
PYTHONPATH=src python -m pbicompass generate Project.pbip -o report.pdf    # needs Pandoc + a PDF engine
PYTHONPATH=src python -m pbicompass generate Project.pbip --format md      # Markdown to stdout
```

`md`, `json`, `html`, and `docx` need nothing beyond the standard library; `pdf`
uses Pandoc and prints an actionable message (pointing to the HTML→print path) if
Pandoc or a PDF engine is missing.

The offline engine fills every section; `--provider anthropic` upgrades only the
three prose agents (Executive Summary, DAX translations, model narrative) and
falls back to the deterministic engine per-agent on any error. The §VII
orphaned-measure audit is always deterministic (a set difference, never a guess).

### Run the web service

```bash
pip install -e ".[service]"
PYTHONPATH=src python -m pbicompass serve            # http://127.0.0.1:8000
```

Open the URL, drop a `.pbix` or a `.zip` of a `.pbip` project, choose the engine,
and download the docs (HTML / DOCX / Markdown / JSON, plus PDF when Pandoc is
available). **Zero-retention:** each upload is processed in a per-job sandbox
that is shredded the moment rendering finishes — only the rendered documents
survive, for a short TTL, and no extracted metadata is ever logged or persisted.

API: `POST /jobs` (multipart upload) → `GET /jobs/{id}` (status) →
`GET /jobs/{id}/download?format=html|docx|md|json|pdf`.

### Hosted mode — API keys & multi-tenancy

By default the service is open (the `public` tenant, no limits) — ideal for
self-hosting. To run it as a multi-tenant SaaS, enable auth and mint keys —
either from the browser via the admin panel:

```bash
export PBICOMPASS_ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export PBICOMPASS_REQUIRE_AUTH=1
pbicompass serve
# open http://127.0.0.1:8000/admin, paste the token, create an account
```

or from the CLI:

```bash
export PBICOMPASS_DB=pbicompass.db
pbicompass account create --tenant acme --name "Acme BI" --plan pro   # prints the API key once
pbicompass account revoke --id <id>                                    # if a key leaks

export PBICOMPASS_REQUIRE_AUTH=1
pbicompass serve
```

Then every request needs `Authorization: Bearer <key>` (or `X-API-Key`). Jobs
are isolated per tenant (another tenant's key gets `404`), and each plan has a
daily quota (`free` 10, `pro` 200, `enterprise` 100k) that returns `429` when
exhausted. `GET /me` reports the caller's plan and remaining quota. The web UI
has an optional "Account API Key" field (stored locally) for hosted use. Only
account metadata and per-day usage **counts** are stored — never report
metadata. The admin token is a single shared secret (not per-admin) — repeated
wrong attempts from a client are locked out for 15 minutes after 8 failures.

### Run the tests

```bash
# PowerShell
$env:PYTHONPATH = "src"; python -m unittest discover -s tests -v
```

---

## Project layout

```
src/pbicompass/
  schemas/
    model.py      # the model.json contract (extracted metadata)
    document.py   # the document.json contract (assembled docs, 7 sections)
  parsers/
    base.py       # TMDL tokenizer + indentation helpers
    tmdl.py       # TMDL semantic-model parser (modern .pbip)
    tmsl.py       # model.bim JSON parser (older .pbip / extracted .pbix)
    pbir.py       # report layout parser (PBIR enhanced + legacy)
    pbip.py       # orchestrator + data-source / table-kind enrichment
  adapters/
    pbixray_adapter.py  # .pbix metadata extraction (optional pbixray)
  agents/
    io.py         # agent prompts + JSON-schema output contracts
    deterministic.py    # offline rule-based generators (DAX, model, business)
    llm.py        # LLMClient protocol + Anthropic (Claude) / Gemini / Cohere clients
    orchestrator.py     # model.json -> document.json (LLM or deterministic)
  render/
    markdown.py   # document.json -> Markdown
    html.py       # document.json -> styled, self-contained HTML
    docx.py       # document.json -> Word .docx (hand-written OOXML)
    pandoc.py     # optional Pandoc adapter for PDF
  service/        # Phase 4-5 zero-retention web service + auth
    sandbox.py    # per-job temp dir + best-effort shred
    jobs.py       # in-memory job registry with TTL expiry (tenant-tagged)
    ingest.py     # upload -> SemanticModel (.pbix / zipped .pbip, zip-slip guard)
    worker.py     # queue-agnostic job worker (Celery-ready)
    accounts.py   # SQLite accounts, API keys, freemium quotas (stdlib)
    admin.py      # admin-token auth + brute-force lockout (pure logic)
    app.py        # FastAPI routes (upload / status / download / auth / me / admin)
    static/index.html   # single-page upload UI
    static/admin.html   # token-gated admin panel (create/list/revoke accounts)
  cli.py
tests/                  # 182 tests across parser, adapter, agents, renderers, service
  fixtures/SampleSales/  # synthetic .pbip exercising every code path
```

---

## `.pbix` support (Phase 1)

```bash
pip install -e ".[pbix]"     # installs pbixray (needs Python <= 3.13)
pbicompass parse path/to/Report.pbix -o model.json
```

`pbixray` reads the semantic model (schema, DAX measures/columns/tables,
relationships, Power Query, parameters) from the compressed `DataModel` — the
adapter never calls `get_table()`, so no row-level data is materialised. RLS
roles are **not** exposed by pbixray; for role definitions, export the file as a
`.pbip` project. If `pbixray` isn't installed, `.pbix` parsing still returns the
report layout and records a clear warning.

> **Note:** `pbixray`'s `xpress9` decompressor has no Python 3.14 wheel yet, so
> the `.pbix` path requires Python ≤ 3.13. The `.pbip` path has no such
> constraint (stdlib only).

## Roadmap (next)

The product runs end-to-end: **parse → agents → render → web service → API-key
multi-tenancy with freemium quotas**. Remaining for scale/commercial:

- **Billing** — wire Stripe to the plans/quotas (the `plan` field and quota
  enforcement are already in place).
- Swap the in-process worker for **Celery + Redis** (the worker signature is
  already queue-agnostic) for horizontal scale; persist job *status* (not
  content) and accounts in Postgres.
- **SSO / session login** for the web UI (API keys cover programmatic use today).
- SOC 2 hygiene: audit logging of actions (never metadata), on-prem option.

> **Design note:** the orchestrator is a lightweight in-process fan-out rather
> than LangGraph (as the original plan suggested) — for four agents with a
> deterministic reducer, this keeps the core dependency-free and fully testable.
> LangGraph can wrap it later if graph features (checkpointing, human-in-the-loop)
> are needed.

> The `.pbip` / `.pbix` parsers are a pragmatic v0 covering the common ~95% of
> real exports. Unrecognised constructs are recorded in `meta.warnings` rather
> than raised, so a single odd object never aborts a whole document build.

---

## Documentation index

| Doc | Covers |
|---|---|
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Full architecture, agent prompts, and phased roadmap |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Production deployment (Render/Fly.io/VM), env vars, zero-retention checklist |
| [BEGINNER_DEPLOY.md](BEGINNER_DEPLOY.md) | Click-by-click deploy guide for a first-time host |
| [SECURITY.md](SECURITY.md) | Data-handling model, zero-leakage guarantees, reporting a vulnerability |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, test workflow, ground rules for changes |
| [.env.example](.env.example) | Every environment variable the app reads, with defaults |

## License

[MIT](LICENSE) © Rajdeep Rao
