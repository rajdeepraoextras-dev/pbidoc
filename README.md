<p align="center">
  <img src="assets/PBICompass-LOGO.png" alt="PBICompass" width="120">
</p>

<h1 align="center">PBICompass</h1>
<p align="center"><b>Enterprise Power BI Documentation Generator — powered by Claude</b></p>

<p align="center">
  <a href="https://pbicompass.duckdns.org"><img alt="Live demo" src="https://img.shields.io/badge/live%20demo-pbicompass.duckdns.org-blue"></a>
  <img alt="Built with Claude" src="https://img.shields.io/badge/built%20with-Claude%20Opus%204.8-D97757">
  <a href="https://github.com/rajdeepraoextras-dev/pbicompass/actions/workflows/ci.yml"><img alt="Tests" src="https://github.com/rajdeepraoextras-dev/pbicompass/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="License" src="https://img.shields.io/badge/license-MIT-informational">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue">
</p>

AI-powered pipeline that ingests Power BI files (`.pbip` / `.pbix`), extracts
**metadata only**, and turns them into enterprise-grade documentation for both
technical BI developers and business stakeholders — in seconds, not the days
a manual write-up takes.

**[Try it live →](https://pbicompass.duckdns.org)** — create a free account
(no credit card), upload a `.pbix`/`.pbip`, and download the generated docs.

> **Why it matters:** manually producing this level of documentation
> (technical, audit & health, executive, and business-user docs) for a
> mid-size company's Power BI estate — say 50 reports, at a typical US
> Power BI developer/consultant rate (~$100/hr) and ~20 hours of writing per
> report — runs **$100,000+** in billable time. PBICompass generates the
> same four documents in minutes. Informally, when asked to grade sample
> output for quality, multiple frontier AI models (Claude, GPT, Gemini) each
> came back with a 100/100 — not an audited benchmark, but a strong signal
> the docs read as genuinely enterprise-grade.

See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the full
freemium-first architecture and phased roadmap, or
[docs/TOOL_DETAILS.md](docs/TOOL_DETAILS.md) for an end-to-end reference of every
component, flag, and endpoint.

---

## Powered by Claude

PBICompass's document pipeline is a small **multi-agent system built on Claude**:
a Business Analyst agent writes the executive narrative, a DAX Translator turns
raw DAX into plain-English explanations, and a Data Modeler describes the
schema and relationships — each one a focused Claude call constrained to a
strict JSON-schema output, not a single do-everything prompt.

- **Claude Opus 4.8** is the default engine, called with **structured outputs**
  so every agent returns a schema-validated object the renderer can trust —
  never freeform text to parse.
- **Adaptive reasoning effort** — agents run Claude's extended thinking at
  `high` by default and can be dialed to `xhigh`/`max` per job; the same knob
  is implemented cross-provider (Gemini `thinking_budget`, Cohere reasoning
  models) so reasoning depth isn't an Anthropic-only feature of the codebase.
- **Deterministic-first, AI-elevated.** A dependency-free deterministic engine
  fills every section of the document from parsed facts alone (works with
  *no* API key); passing `--provider anthropic` upgrades only the prose
  sections. The AI is layered on top of ground truth, never a substitute for
  it — facts like the orphaned-measure audit are always a set difference,
  never a model guess.
- **Graceful degradation everywhere.** Any Claude call that errors falls back
  to the deterministic output for that agent only, so one flaky request never
  fails a whole document.
- **A second layer of AI agents checks the first layer's work.** A grounding
  pass verifies prose against the model's own metadata digest; a consistency
  agent (`agents/consistency.py`) catches one document's narrative
  contradicting a *sibling* document's deterministic verdict (e.g. the
  executive summary calling the model "a star schema" while the Audit
  report's rule engine says otherwise); a Requirements Traceability agent
  (`agents/traceability.py`) turns free-text business requirements into a
  Covered/Partial/Gap matrix with links to the actual measures/columns/pages
  that satisfy each one — and is only allowed to cite evidence it was
  actually offered, never an invented anchor.

See [src/pbicompass/agents](src/pbicompass/agents) for the agent prompts/schemas
and [src/pbicompass/agents/llm.py](src/pbicompass/agents/llm.py) for the Claude
client (`claude-opus-4-8`, structured outputs, adaptive thinking).

---

## Status — Phases 0–5 complete

The foundation is in place and tested (725 tests passing):

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
  a deterministic Auditor turn `model.json` into four document types (Technical,
  Audit & Health, Executive, and Business User Guide — one generator per type,
  [src/pbicompass/agents/generators](src/pbicompass/agents/generators)). A
  **deterministic offline engine** produces every document with no API key;
  pass `--provider anthropic` to use Claude (Opus 4.8, structured outputs) for
  the prose agents.
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
- **Auth & multi-tenancy** — two ways in, same account store
  ([src/pbicompass/service/accounts.py](src/pbicompass/service/accounts.py)):
  self-serve **Supabase** sign-in with a plan picker at `/app` for the hosted
  SaaS (account is created on first sign-in, no operator step), or, for pure
  self-hosting, a classic `PBICOMPASS_REQUIRE_AUTH=1` + minted API keys.
  Per-tenant job isolation and a per-plan monthly quota (`free`/`pro`/`business`,
  HTTP 429 when exceeded) apply either way.
- **Admin portal** — a signed-in-admin `/app` dashboard (overview stats,
  estimated MRR, user search, plan/quota override, suspend/delete, cross-tenant
  job feed, and per-engine AI availability toggles so an admin can pull a
  provider offline SaaS-wide) for the hosted SaaS; the original token-gated
  `/admin` page (mint/revoke API keys, brute-force lockout on the token) still
  works standalone for self-hosters who skip Supabase entirely
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

# Or one key for any of 1000+ models via https://developers.meshapi.ai (model ids are "provider/model-name";
# defaults to openai/gpt-4o — MeshAPI's Bedrock-routed Anthropic models don't yet support the structured
# JSON output every agent here needs, per MeshAPI's own structured-output docs):
PYTHONPATH=src python -m pbicompass generate path/to/Project.pbip --provider meshapi -o report.md   # MESHAPI_API_KEY
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

### Hosted mode — self-serve SaaS or self-host API keys

**Hosted SaaS** ([pbicompass.duckdns.org](https://pbicompass.duckdns.org) runs
this): set `SUPABASE_URL`/`SUPABASE_ANON_KEY`/`SUPABASE_JWT_SECRET` and
`PBICOMPASS_REQUIRE_AUTH=1`. Visitors create a free account and pick a plan at
`/app#signup` — no operator step. Plans are `free` (1 doc/mo), `pro` ($20,
10/mo), `business` ($50, 30/mo); any signed-in user can self-serve switch plans
today (trust-based — payment collection isn't wired up yet). An account with
`is_admin` gets the `/app` admin dashboard (stats, MRR estimate, suspend/
delete, quota overrides). Each user can mint their own API keys from `/app`
for programmatic use.

**Self-host, no Supabase:** skip the `SUPABASE_*` vars and use the original
admin-token + API-key flow instead — either from the browser:

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

Either way, every request needs `Authorization: Bearer <key>` (or
`X-API-Key`). Jobs are isolated per tenant (another tenant's key gets `404`),
and `GET /me` reports the caller's plan and remaining monthly quota. Only
account metadata and usage **counts** are stored — never report metadata. The
break-glass admin token is a single shared secret (not per-admin) — repeated
wrong attempts from a client are locked out for 15 minutes after 8 failures.

### Run the tests

```bash
pip install -e ".[dev,service,agents]"
pytest   # 725 passing (a couple of pre-existing snapshot gaps are tracked in the roadmap)
```

---

## Project layout

Repository top level:

```
pbicompass/
  README.md               # this file
  LICENSE  SECURITY.md  CONTRIBUTING.md
  pyproject.toml          # package metadata + optional extras
  Dockerfile  .dockerignore  .env.example
  .github/workflows/      # CI / deploy
  src/pbicompass/         # the package (see below)
  tests/                  # test suite + synthetic fixtures
  docs/                   # all documentation
    TOOL_DETAILS.md       # complete start-to-end reference
    IMPLEMENTATION_PLAN.md  DEPLOYMENT.md  BEGINNER_DEPLOY.md
    planning/             # AI_NATIVE_PLAN, PRODUCTION_ROADMAP, ROADMAP_PROGRESS, DOCUMENTATION_QUALITY_PLAN
    design/               # wireframe / lineage HTML mockups
  assets/                 # logo and brand images
  examples/               # sample Power BI export (zipped)
```

The package itself:

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
    llm.py        # LLMClient protocol + Claude / Gemini / Cohere / MeshAPI clients
    orchestrator.py     # model.json -> document.json (LLM or deterministic)
    critic.py  grounding.py  sanitize.py   # AI-output QA passes (fabrication/consistency guards)
    generators/   # one generator per document type (technical, audit, executive, user_guide)
  render/
    _html_shell.py   # shared page chrome (nav/TOC/theme) every HTML doc renders into
    markdown.py  html.py  docx.py  hub.py   # document.json -> Markdown / HTML / Word / job cover page
    pandoc.py     # optional Pandoc adapter for PDF
    _wireframe.py  _lineage.py   # report wireframe + data-lineage diagrams
  service/        # zero-retention web service + auth
    sandbox.py    # per-job temp dir + best-effort shred
    jobs.py       # job registry (Postgres-capable via PBICOMPASS_JOBS_DB; rendered bytes stay in-memory)
    ingest.py     # upload -> SemanticModel (.pbix / zipped .pbip, zip-slip guard)
    worker.py     # queue-agnostic job worker (Celery-ready; celery_app.py wires it when enabled)
    accounts.py   # accounts, API keys, monthly plan quotas (SQLite or Postgres via PBICOMPASS_DB)
    supabase_auth.py  # verifies Supabase session JWTs for the hosted SaaS's self-serve signin
    admin.py      # break-glass admin-token auth + brute-force lockout, for self-host without Supabase
    app.py        # FastAPI routes (upload / status / download / auth / me / /app SaaS / admin)
    static/index.html  static/app.html   # marketing site + the sign-in/upload/dashboard app
    static/admin.html   # SaaS admin dashboard (stats, users, suspend/delete) and self-host fallback
  cli.py
tests/                  # 725 tests across parser, adapter, agents, renderers, service
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

- **Payment collection** — plan switching is self-serve and trust-based today
  (pricing/legal pages are Paddle-ready); wiring an actual checkout is next.
- Swap the in-process worker for **Celery + Redis** (the worker signature is
  already queue-agnostic) for horizontal scale — job metadata already lives in
  Postgres.
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
| [docs/TOOL_DETAILS.md](docs/TOOL_DETAILS.md) | **Complete start-to-end reference** — every module, CLI command, flag, env var, endpoint, and data-flow stage |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Full architecture, agent prompts, and phased roadmap |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production deployment (Render/Fly.io/VM), env vars, zero-retention checklist |
| [docs/BEGINNER_DEPLOY.md](docs/BEGINNER_DEPLOY.md) | Click-by-click deploy guide for a first-time host |
| [SECURITY.md](SECURITY.md) | Data-handling model, zero-leakage guarantees, reporting a vulnerability |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, test workflow, ground rules for changes |
| [.env.example](.env.example) | Every environment variable the app reads, with defaults |
| [docs/planning/](docs/planning/) | AI-native plan, production roadmap, roadmap progress, documentation-quality plan |
| [docs/design/](docs/design/) | Design artifacts (wireframe/lineage HTML mockups) |

## License

[MIT](LICENSE) © Rajdeep Rao
