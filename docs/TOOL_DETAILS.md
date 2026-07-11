# PBICompass — Tool Details

A complete, start-to-end reference for **PBICompass**, the enterprise Power BI
documentation generator. This document covers every stage of the pipeline, every
package and module, every CLI command and flag, every web endpoint, and every
environment variable.

If you only want to *use* the tool, the [README](../README.md) is enough. This
document is the exhaustive reference for operators, contributors, and reviewers.

---

## Table of contents

1. [What PBICompass is](#1-what-pbicompass-is)
2. [Design principles](#2-design-principles)
3. [Installation & optional extras](#3-installation--optional-extras)
4. [End-to-end data flow](#4-end-to-end-data-flow)
5. [Package reference (`src/pbicompass`)](#5-package-reference-srcpbicompass)
6. [CLI reference](#6-cli-reference)
7. [Document types](#7-document-types)
8. [Output formats](#8-output-formats)
9. [The AI pipeline](#9-the-ai-pipeline)
10. [Enrichment, diff & rules configuration](#10-enrichment-diff--rules-configuration)
11. [The web service](#11-the-web-service)
12. [Environment variables](#12-environment-variables)
13. [Security & data handling](#13-security--data-handling)
14. [Testing](#14-testing)
15. [Repository layout](#15-repository-layout)
16. [Further reading](#16-further-reading)

---

## 1. What PBICompass is

PBICompass ingests Power BI files — modern `.pbip` projects or legacy `.pbix`
binaries — extracts **metadata only** (never row-level data), and produces
enterprise-grade documentation for both technical BI developers and business
stakeholders.

It runs in two modes:

- **CLI** — a single `pbicompass` command for parsing and generating docs locally.
- **Web service** — a zero-retention FastAPI app where users upload a file and
  download rendered documentation for a short TTL.

Both modes share the same core: parse → assemble → render, with an optional AI
layer that upgrades the prose sections.

---

## 2. Design principles

| Principle | What it means in practice |
|---|---|
| **Metadata only** | The parser reads schema, DAX, relationships, roles, M/Power Query, and report layout. It never materialises VertiPaq rows. The `.pbix` adapter never calls `get_table()`. |
| **Zero-retention service** | Each upload is processed in a per-job sandbox that is shredded in a `finally` block. Only rendered documents survive, for a short TTL. No customer metadata is ever logged or persisted. |
| **Zero-dependency core** | The parser and schemas use the Python **standard library only**. FastAPI, the AI SDKs, Celery, PyYAML, etc. are optional extras pulled in only by later phases. |
| **Deterministic by default** | Every document section can be produced with no API key. The AI layer only *upgrades* the prose agents; it can never downgrade a deterministic fact. The §orphaned-measure audit is always a set difference, never a guess. |
| **Graceful degradation** | Unrecognised constructs are recorded in `meta.warnings` rather than raised. Missing optional tools (Pandoc, pbixray) degrade to a clear message, not a crash. |

---

## 3. Installation & optional extras

Core install (stdlib only, no third-party deps):

```bash
pip install -e .
```

Optional extras, declared in [`pyproject.toml`](../pyproject.toml):

| Extra | Install | Adds |
|---|---|---|
| `pbix` | `pip install -e ".[pbix]"` | `pbixray` — legacy `.pbix` semantic-model extraction (needs Python ≤ 3.13) |
| `enrich` | `pip install -e ".[enrich]"` | `PyYAML` — the enrichment round-trip file |
| `agents` | `pip install -e ".[agents]"` | `anthropic`, `google-genai`, `cohere`, `openai` — AI provider clients |
| `service` | `pip install -e ".[service]"` | `fastapi`, `uvicorn`, `python-multipart` — the web service |
| `postgres` | `pip install -e ".[postgres]"` | `psycopg[binary]` — managed Postgres backend for accounts |
| `queue` | `pip install -e ".[queue]"` | `celery`, `redis` — async worker |
| `observability` | `pip install -e ".[observability]"` | `sentry-sdk` — error tracking |
| `dev` | `pip install -e ".[dev]"` | `pytest`, `httpx` — test suite |

Nothing above is required to parse a `.pbip` and generate offline documentation.

Python version: `>=3.10`. The `.pbix` path requires `≤ 3.13` (pbixray's `xpress9`
decompressor has no 3.14 wheel yet); the `.pbip` path has no such constraint.

---

## 4. End-to-end data flow

```
  .pbip / .pbix / .zip
          │
          ▼
   ┌──────────────┐   parsers/ + adapters/
   │   PARSE      │   TMDL, TMSL, PBIR, pbixray → metadata only
   └──────┬───────┘
          ▼
     model.json          canonical extracted-metadata contract (schemas/model.py)
          │
          ▼
   ┌──────────────┐   agents/ (deterministic + optional LLM)
   │   ASSEMBLE   │   Business Analyst · DAX Translator · Data Modeler · Auditor
   └──────┬───────┘   + report intelligence, grounding, critic passes
          ▼
    document.json        assembled multi-section contract (schemas/document.py)
          │
          ▼
   ┌──────────────┐   render/
   │   RENDER     │   Markdown · HTML · DOCX · (PDF via Pandoc)
   └──────┬───────┘
          ▼
   report.md / .html / .docx / .pdf / .json  (+ a documentation hub for multi-doc)
```

The **web service** wraps this exact flow inside a per-job sandbox: `ingest.py`
turns an upload into a `SemanticModel`, `worker.py` runs the assemble+render
stages, and `sandbox.py` shreds the working directory when done.

---

## 5. Package reference (`src/pbicompass`)

### `schemas/` — the data contracts

| Module | Contract |
|---|---|
| `model.py` | `SemanticModel` — the `model.json` contract: tables, columns, measures, relationships, roles, M/Power Query, data sources, report pages/visuals, plus `meta` (counts, warnings, source format). |
| `document.py` | The `document.json` contract for the technical document (multi-section). |
| `audit_document.py` | The audit document contract (health score, rule findings, clusters). |
| `executive_document.py` | The executive summary contract. |
| `user_guide_document.py` | The business/user-guide contract. |
| `shared.py` | Types shared across the document schemas. |

### `parsers/` — `.pbip` extraction (stdlib only)

| Module | Role |
|---|---|
| `base.py` | TMDL tokenizer + indentation helpers. |
| `tmdl.py` | TMDL semantic-model parser (modern `.pbip`). |
| `tmsl.py` | `model.bim` JSON parser (older `.pbip` / extracted `.pbix`). |
| `pbir.py` | Report-layout parser (PBIR enhanced **and** legacy). |
| `m_steps.py` | Power Query / M step parsing. |
| `pbip.py` | Orchestrator: detects the format, runs the right parsers, and enriches with data-source and table-kind inference. Exposes `detect_and_parse`. |

### `adapters/` — `.pbix` extraction

| Module | Role |
|---|---|
| `pbixray_adapter.py` | Reads the semantic model from a `.pbix` via the optional `pbixray` library (metadata frames only — never row data), plus report layout from the ZIP. Degrades to layout-only if `pbixray` is unavailable. `--stats` opts into VertiPaq **aggregate** stats (column cardinality/size), never row-level data. |

### `agents/` — assembly (deterministic + optional AI)

| Module | Role |
|---|---|
| `orchestrator.py` | `generate_document` — turns `model.json` into `document.json`, fanning out to the agents with a deterministic reducer. |
| `deterministic.py` | Offline, rule-based generators for every section (DAX explanations, model narrative, business prose). No API key needed. |
| `llm.py` | `LLMClient` protocol + concrete Anthropic (Claude), Gemini, Cohere, and MeshAPI clients. Handles each provider's native reasoning knob and a retry-without-reasoning fallback. |
| `io.py` | Agent prompts + JSON-schema output contracts (structured outputs). |
| `context.py` | Shared per-job AI context — one DAX-Translator pass reused across every requested document type (avoids redundant LLM calls). |
| `insights.py` | Report Intelligence pass — one whole-model reasoning pass. |
| `grounding.py` | Grounding & verification pass — LLM-routed fact-check that keeps prose tied to the extracted facts. |
| `critic.py` | Senior-reviewer whole-document pass. |
| `report_facts.py` | Deterministic fact extraction the prose agents are grounded against. |
| `audit_rules.py` | The audit rule engine + rule-config loader (disable rule IDs, override severities, set thresholds). |
| `cache.py` | `LLMResponseCache` — optional on-disk response cache (SQLite), keyed on prompt. |
| `sanitize.py` | Output sanitisation. |
| `usage.py` | Token/usage accounting. |
| `generators/` | One generator per document type — see [§7](#7-document-types). `DOCUMENT_TYPES` is the registry the CLI and service consult. |

### `render/` — output rendering

| Module | Output |
|---|---|
| `markdown.py` | `document.json` → Markdown. |
| `html.py` | `document.json` → styled, self-contained HTML (prints to PDF from any browser). |
| `docx.py` / `_docx_writer.py` | Word `.docx` via hand-written OOXML — **no** `python-docx`/`lxml`. |
| `pandoc.py` | Optional Pandoc adapter for PDF; degrades gracefully when Pandoc or a PDF engine is absent. |
| `registry.py` | `RENDERERS` — maps each document type to its `{md, html, docx}` renderers. |
| `hub.py` | The documentation-hub index page linking every document in a multi-doc run. |
| `audit.py`, `executive.py`, `user_guide.py` | Per-document-type renderers. |
| `_lineage.py`, `_wireframe.py`, `_measure_deps.py`, `_nav_map.py` | Visual artifacts: lineage graph, report wireframe, measure dependencies, navigation map. |
| `_dax_highlight.py`, `_html_shell.py`, `_shared.py`, `_poppins_font.py` | HTML shell, DAX syntax highlighting, shared helpers, embedded font. |

### `service/` — the web service

See [§11](#11-the-web-service) for full detail. Modules: `app.py` (routes),
`ingest.py`, `worker.py`, `jobs.py`, `sandbox.py`, `accounts.py`, `admin.py`,
`passwords.py`, `oidc.py`, `email.py`, `ratelimit.py`, `celery_app.py`,
`db_backup.py`, `metrics.py`, `logging_config.py`, `sentry_config.py`, and the
`static/` upload/admin/app UIs.

### Top-level modules

| Module | Role |
|---|---|
| `cli.py` | The `pbicompass` command (see [§6](#6-cli-reference)). |
| `enrichment.py` | The enrichment round-trip, model diff/change-log, and fingerprinting. |
| `__main__.py` | Enables `python -m pbicompass`. |

---

## 6. CLI reference

Invoke as `pbicompass <command>` (installed) or `python -m pbicompass <command>`
(with `PYTHONPATH=src`). Five commands: `parse`, `generate`, `diff`, `serve`,
`account`.

### `pbicompass parse`

Extract metadata to the canonical `model.json` and print a summary.

| Flag | Effect |
|---|---|
| `path` | `.pbip` file, project directory, or `.pbix`. |
| `-o, --out` | Write `model.json` to this path. |
| `--compact` | Minified JSON output. |
| `--quiet` | Suppress the summary. |
| `--stats` | `.pbix` only: also read VertiPaq **aggregate** stats (column cardinality/size). Opt-in; never row-level data. |

### `pbicompass generate`

Parse a file and generate documentation. This is the main command.

**Core:**

| Flag | Effect |
|---|---|
| `path` | `.pbip` file, project directory, or `.pbix`. |
| `-o, --out` | Output path; format inferred from suffix. |
| `--format` | Force `md` \| `json` \| `html` \| `docx` \| `pdf`. |
| `--document` | `technical` (default) \| `audit` \| `executive` \| `user-guide` \| `all`. |
| `--bundle` | Render **every** format for the requested document type(s) into one zip, plus `model.json` (and, with `--enrich`, the enrichment skeleton). |
| `--stats` | `.pbix` only: aggregate VertiPaq stats (as in `parse`). |
| `--quiet` | Suppress warnings/status. |

**AI provider:**

| Flag | Effect |
|---|---|
| `--provider` | `none` (deterministic, default) \| `anthropic` \| `gemini` \| `cohere` \| `meshapi`. |
| `--model` | Model id (default `claude-opus-4-8`). For MeshAPI use `provider/model-name`, e.g. `openai/gpt-4o`. |
| `--effort` | Reasoning effort: `low` \| `medium` \| `high` (default) \| `xhigh` \| `max`. Applied to each provider's native reasoning knob where supported. Ignored for `--provider none`. |
| `--plan` | `free` \| `pro` \| `enterprise` (default). Gates paid AI features (e.g. AI fix snippets on the audit doc). Defaults to `enterprise` for self-hosted runs. |

**Enrichment & diff:**

| Flag | Effect |
|---|---|
| `--enrich <file>` | Enrichment YAML round-trip. If the file doesn't exist, a skeleton is written and the run stops. If it exists, its descriptions/overrides are applied and the file is rewritten so filled-in fields persist. |
| `--diff-against <model.json>` | With `--enrich`: diff against a previous `model.json` to produce the "Changes since last documentation" section. |
| `--rules <toml>` | A `pbicompass.rules.toml`: disable rule IDs, override severities, set thresholds (audit document only). |

**Document metadata** (all optional free-text; enrichment file values act as
defaults, explicit flags win): `--owner`, `--audience`, `--refresh`,
`--version`, `--status`, `--author`, `--reviewer`, `--classification`,
`--business-decision`, `--requirements`, `--security-notes`, `--refresh-notes`,
`--deployment-notes`, `--access-notes`, `--glossary`, `--assumptions`,
`--support-notes`.

> The CLI defaults an on-disk LLM response cache (`.pbicompass_cache.db`) and a
> score-history file (`.pbicompass_history.json`); both are git-ignored. The
> hosted service leaves both off.

### `pbicompass diff`

Compare two `model.json` files and print a change log.

| Flag | Effect |
|---|---|
| `old`, `new` | The two `model.json` files. |
| `-o, --out` | Write the change log here instead of stdout. |

### `pbicompass serve`

Run the web service (upload UI + API). Needs `pip install -e ".[service]"`.

| Flag | Default |
|---|---|
| `--host` | `127.0.0.1` |
| `--port` | `8000` |
| `--reload` | off (dev auto-reload) |

### `pbicompass account`

Manage API accounts for multi-tenant auth. All subcommands take
`--db` (default `$PBICOMPASS_DB` or `pbicompass.db`).

| Subcommand | Purpose |
|---|---|
| `create --tenant <t> [--name] [--plan]` | Create an account and mint an API key (printed once). |
| `list` | List accounts (`id  tenant  plan  name`). |
| `revoke --id <id>` | Revoke an account — its key stops working immediately. |
| `backup --out <file>` | Snapshot accounts/keys/quotas to JSON. |
| `restore --in <file>` | Restore from a snapshot (point `--db` at a scratch DB for a restore drill). |

---

## 7. Document types

`--document` selects one of four generators (`agents/generators/`), or `all` to
produce every type from a single parse:

| Type | Generator | Audience |
|---|---|---|
| `technical` | `TechnicalDocumentationGenerator` | BI developers — the full multi-section technical documentation (schema, DAX, lineage, RLS, refresh, etc.). Default. |
| `audit` | `AuditReportGenerator` | Reviewers — health score, rule findings, orphaned-measure audit, clustered issues, optional AI fix snippets. |
| `executive` | `ExecutiveSummaryGenerator` | Leadership — a concise business-outcomes summary. |
| `user-guide` | `BusinessGuideGenerator` | Business users — a how-to-read-this-report guide. |

When both `technical` and `audit` are requested, the audit document is generated
first so its synthesizer clusters can be surfaced on the technical doc without a
second, potentially inconsistent LLM call.

---

## 8. Output formats

Chosen by `-o` suffix or forced with `--format`:

| Format | Needs | Notes |
|---|---|---|
| `md` | stdlib | Markdown to stdout or file. |
| `json` | stdlib | The structured `document.json`. |
| `html` | stdlib | Styled, self-contained HTML — print to PDF from any browser. |
| `docx` | stdlib | Word via hand-written OOXML (no `python-docx`/`lxml`). Requires `-o`. |
| `pdf` | Pandoc + a PDF engine | Prints an actionable message pointing to the HTML→print path if missing. Requires `-o`. |

`--bundle` renders every format for the requested type(s) into one zip, and for a
multi-document run also writes an `index.html` documentation hub linking them.

---

## 9. The AI pipeline

The offline (deterministic) engine fills **every** section with no API key.
Passing `--provider` upgrades only the prose agents; each falls back to the
deterministic engine per-agent on any error.

**Providers** (`agents/llm.py`): Anthropic (Claude), Gemini, Cohere, and MeshAPI
(one key routes to 1000+ models; use `provider/model-name` ids). Keys come from
the environment (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`,
`COHERE_API_KEY`/`CO_API_KEY`, `MESHAPI_API_KEY`) or, in the web UI, per-request
(BYOK).

**Reasoning effort** (`--effort`) maps to each provider's native reasoning knob
where the model supports one — Anthropic always; Gemini via thinking budget;
Cohere/MeshAPI only for reasoning-capable models. There is no plan clamp on
reasoning depth; the per-job quota is the only cost guardrail. A
retry-without-reasoning fallback covers models/providers that reject the knob.

**Passes** (beyond the four base agents — Business Analyst, DAX Translator, Data
Modeler, deterministic Auditor):

- **Shared job context** (`context.py`) — one DAX-Translator pass reused across
  every requested document type.
- **Report Intelligence** (`insights.py`) — a whole-model reasoning pass.
- **Grounding** (`grounding.py`) — LLM-routed fact-check anchoring prose to the
  extracted facts (`report_facts.py`).
- **Critic** (`critic.py`) — a senior-reviewer whole-document pass.

**Guardrails:** the AI may only *improve* prose, never downgrade a deterministic
description or invent facts. Mechanically-obvious columns must not be labelled
"Unknown / requires business confirmation." The orphaned-measure audit is always
a deterministic set difference.

**Cache** (`cache.py`): the CLI defaults an on-disk SQLite response cache
(`PBICOMPASS_LLM_CACHE`); the service leaves it off.

---

## 10. Enrichment, diff & rules configuration

**Enrichment round-trip** (`enrichment.py`, `--enrich`): a YAML file where you
supply the human context the parser can't infer — measure/column descriptions,
data-source and role details, report metadata, and rule overrides. First run
bootstraps a skeleton; subsequent runs apply it and rewrite it so filled-in
fields persist across runs. Report metadata in the file becomes the default for
`--owner`/`--author`/etc. (explicit flags still win).

**Model diff** (`pbicompass diff`, or `--diff-against` with `--enrich`): compares
two `model.json` files and produces a change log for the "Changes since last
documentation" section. The enrichment file stores a fingerprint + previous
summary so the change log stays current between runs.

**Rules config** (`--rules <toml>`): a `pbicompass.rules.toml` that disables rule
IDs, overrides severities, and sets thresholds for the audit document. Invalid
TOML is a warning, not a fatal error — the job runs without the overrides.

---

## 11. The web service

A FastAPI app (`service/app.py`). Start with `pbicompass serve`. **Zero-retention:**
each upload runs in a per-job sandbox (`sandbox.py`) shredded in a `finally`
block; `jobs.py` holds only job status + rendered bytes with TTL expiry; no
extracted metadata is ever logged or persisted.

### Endpoints

| Method & path | Purpose |
|---|---|
| `GET /` | Single-page upload UI (`static/index.html`). |
| `GET /app` | Logged-in dashboard UI (`static/app.html`). |
| `GET /admin` | Token-gated admin panel (`static/admin.html`). |
| `POST /jobs` | Multipart upload → starts a job (rate-limited per IP). |
| `GET /jobs/{id}` | Job status. |
| `GET /jobs/{id}/download?format=html\|docx\|md\|json\|pdf` | Download a rendered doc. |
| `GET /me` | Caller's plan + remaining quota. |
| `GET /healthz` | Health check. |
| `GET /metrics` | Prometheus-style metrics (`metrics.py`). |
| `POST /auth/signup`, `/auth/login`, `/auth/logout` | Email/password sessions. |
| `GET /auth/verify`, `POST /auth/reset-request`, `GET/POST /auth/reset` | Email verification + password reset. |
| `GET /auth/oidc/login`, `/auth/oidc/callback` | "Sign in with Microsoft" (Entra ID OIDC). |
| `GET /app/api/config`, `/me`, `/keys`, `POST /app/api/keys`, `DELETE /app/api/keys/{id}`, `GET /app/api/jobs` | Dashboard API. |
| `POST /admin/api/verify`, `GET/POST /admin/api/accounts`, `DELETE /admin/api/accounts/{id}` | Admin API. |

### Auth & multi-tenancy

Off by default (open `public` tenant, no limits) — ideal for self-hosting.
Enable with `PBICOMPASS_REQUIRE_AUTH=1`. Then every request needs
`Authorization: Bearer <key>` (or `X-API-Key`). Jobs are isolated per tenant
(another tenant's key gets `404`). Per-plan monthly quotas, mirroring
`/#pricing`: `free` 1, `pro` 10, `business` 30 → `429` when exhausted
(`accounts.py`).

Accounts, keys, and per-billing-period usage **counts** are stored in SQLite by
default, or managed Postgres via `PBICOMPASS_DB=postgres://...` + the `postgres`
extra. Only account metadata and usage counts — never report metadata.

### Admin panel

`/admin`, enabled with `PBICOMPASS_ADMIN_TOKEN`. Create/list/revoke API keys from
the browser. The admin token is a single shared secret with brute-force lockout
(15 min after 8 failed attempts). Without the token set, `/admin/api/*` returns
503.

### Sessions, email & OIDC

- **Sessions** (`passwords.py`, cookies): signup/login create a server-side
  session (opaque hashed cookie token) plus a CSRF cookie for the double-submit
  check. Active only when `PBICOMPASS_DB` is configured. TTL and cookie-Secure
  are configurable.
- **Email** (`email.py`): transactional verify/reset emails via `console`
  (logs the link, default) or `smtp` (any provider, via stdlib `smtplib`).
  Emails only ever contain an auth link + the recipient's own address.
- **OIDC** (`oidc.py`): Entra ID auth-code + PKCE flow, zero new deps. Enabled
  only when client id/secret + a resolvable redirect URI are all set; otherwise
  the routes report 404.

### Async worker & scale

`worker.py` is queue-agnostic. Default `inline` runs jobs via FastAPI
`BackgroundTasks` in-process. `PBICOMPASS_QUEUE=celery` (+ the `queue` extra)
enqueues onto Celery + Redis (`celery_app.py`) — removes Cloud Run's
CPU-throttling failure class. A watchdog force-fails jobs stuck longer than
`PBICOMPASS_JOB_TIMEOUT_SECONDS`.

### Rate limiting, logging, observability

- **Rate limits** (`ratelimit.py`): per-IP budgets on `POST /jobs` and on
  `POST /auth/*`, independent of and ahead of the per-plan monthly quota.
- **Logging** (`logging_config.py`): structured JSON logs to stdout, content-free
  (only an exception's type name — never report content or raw exception text).
- **Backups** (`db_backup.py`): snapshot/restore accounts/keys/quotas (see the
  `account backup`/`restore` CLI).
- **Sentry** (`sentry_config.py`): optional, off by default; PII/locals/source
  context all off, exception messages scrubbed to type name.

---

## 12. Environment variables

Full list (see [`.env.example`](../.env.example) for inline docs). Nothing is
required to run locally with the offline engine.

**Web service & storage**

| Var | Default | Purpose |
|---|---|---|
| `PBICOMPASS_REQUIRE_AUTH` | `0` | Require an API key on every request. |
| `PBICOMPASS_DB` | `pbicompass.db` | Accounts + usage counts. SQLite path or `postgres://` URL. |
| `PBICOMPASS_JOBS_DB` | `pbicompass_jobs.db` | Job status + rendered bytes (TTL-swept). |
| `PBICOMPASS_QUEUE` | `inline` | `inline` or `celery`. |
| `PBICOMPASS_BROKER_URL` | `redis://localhost:6379/0` | Celery broker. |
| `PBICOMPASS_RESULT_BACKEND` | = broker URL | Celery result backend. |
| `PBICOMPASS_SANDBOX_ROOT` | (system temp) | Per-job working dir — point at tmpfs for strict zero-retention. |
| `PBICOMPASS_MAX_UPLOAD_MB` | `100` | Max upload size. |
| `PBICOMPASS_JOB_TIMEOUT_SECONDS` | `600` | Watchdog: force-fail a stuck job. |

**Rate limits**

| Var | Default |
|---|---|
| `PBICOMPASS_UPLOAD_RATE_LIMIT` / `PBICOMPASS_UPLOAD_RATE_WINDOW_SECONDS` | `20` / `60` |
| `PBICOMPASS_AUTH_RATE_LIMIT` / `PBICOMPASS_AUTH_RATE_WINDOW_SECONDS` | `10` / `60` |

**Sessions & cookies**

| Var | Default |
|---|---|
| `PBICOMPASS_SESSION_TTL_SECONDS` | `2592000` (30 days) |
| `PBICOMPASS_COOKIE_SECURE` | `1` (HTTPS-only; set `0` only for local http dev) |

**Email**

| Var | Default | Purpose |
|---|---|---|
| `PBICOMPASS_EMAIL_BACKEND` | `console` | `console` (logs link) or `smtp`. |
| `PBICOMPASS_PUBLIC_URL` | (unset) | Base URL for emailed links. |
| `PBICOMPASS_SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS` | — / `587` / — / — / — / `1` | SMTP settings (when backend is `smtp`). |
| `PBICOMPASS_REQUIRE_EMAIL_VERIFICATION` | `0` | Require a verified email before login. |

**Microsoft OIDC**

| Var | Default | Purpose |
|---|---|---|
| `PBICOMPASS_OIDC_CLIENT_ID` / `_CLIENT_SECRET` | (unset) | Entra ID app credentials. |
| `PBICOMPASS_OIDC_TENANT` | `common` | Tenant GUID or `common`/`organizations`/`consumers`. |
| `PBICOMPASS_OIDC_REDIRECT_URI` | (derived from `PBICOMPASS_PUBLIC_URL`) | Registered redirect URI. |

**Admin, logging & observability**

| Var | Default | Purpose |
|---|---|---|
| `PBICOMPASS_ADMIN_TOKEN` | (unset → panel disabled) | Enables `/admin`. |
| `PBICOMPASS_LOG_LEVEL` | `INFO` | Structured JSON log level. |
| `SENTRY_DSN` | (unset → disabled) | Sentry error tracking. |
| `PBICOMPASS_ENV` | `production` | Free-text environment label. |

**AI engines**

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude. |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini. |
| `COHERE_API_KEY` / `CO_API_KEY` | Cohere. |
| `MESHAPI_API_KEY` | MeshAPI (1000+ models). |

**CLI-only**

| Var | Default | Purpose |
|---|---|---|
| `PBICOMPASS_LLM_CACHE` | `.pbicompass_cache.db` (CLI) / off (service) | On-disk LLM response cache. |
| `PBICOMPASS_SCORE_HISTORY` | `.pbicompass_history.json` (CLI) / off (service) | Audit score trend history. |

---

## 13. Security & data handling

- **Metadata only.** No row-level data is ever read or materialised. The `.pbix`
  adapter never calls `get_table()`; `--stats` reads only aggregate column stats.
- **Zero-retention service.** Uploads live in a per-job sandbox shredded in a
  `finally` block. Only rendered documents survive for a short TTL. Point
  `PBICOMPASS_SANDBOX_ROOT` at tmpfs so uploads never touch physical disk.
- **Content-free logging.** Logs, Sentry events, and emails never contain report
  content — only an exception's type name and non-sensitive identifiers.
- **Auth hardening.** API keys and session tokens are stored hashed; the admin
  token has brute-force lockout; state-changing session requests use CSRF
  double-submit; cookies are Secure by default.
- **Zip-slip guard** on uploaded `.zip` (`ingest.py`).

See [SECURITY.md](../SECURITY.md) for the full data-handling model and how to
report a vulnerability.

---

## 14. Testing

The suite lives in [`tests/`](../tests/) with a synthetic `SampleSales` `.pbip`
fixture exercising every code path (including the LLM path via an in-process fake
client). Run it:

```bash
pip install -e ".[dev,service,agents]"
pytest -v
```

Use `pytest`, not plain `unittest discover` — `tests/conftest.py` carries an
autouse fixture (forcing the LLM cache off between tests) that only pytest
loads; running via `unittest discover` skips it and produces spurious
failures from cache bleed between tests.

Golden-HTML tests (`tests/test_golden_html.py`) compare rendered HTML against
fixtures in `tests/fixtures/golden/`.

---

## 15. Repository layout

```
pbicompass/
  README.md  LICENSE  SECURITY.md  CONTRIBUTING.md
  pyproject.toml  Dockerfile  .dockerignore  .env.example
  .github/workflows/          # CI / deploy
  src/pbicompass/             # the package (see §5)
    schemas/  parsers/  adapters/  agents/  render/  service/
    cli.py  enrichment.py  __main__.py
  tests/                      # test suite + SampleSales fixture
  docs/
    TOOL_DETAILS.md           # this file
    IMPLEMENTATION_PLAN.md    # architecture + phased roadmap
    DEPLOYMENT.md             # production deployment guide
    BEGINNER_DEPLOY.md        # click-by-click first host
    planning/                 # AI_NATIVE_PLAN, PRODUCTION_ROADMAP,
                              #   ROADMAP_PROGRESS, DOCUMENTATION_QUALITY_PLAN
    design/                   # wireframe / lineage HTML mockups
  assets/                     # logo + brand images
  examples/                   # sample Power BI export (zipped)
```

---

## 16. Further reading

| Doc | Covers |
|---|---|
| [README.md](../README.md) | Quick start, usage, project overview |
| [docs/IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Full architecture, agent prompts, phased roadmap |
| [docs/DEPLOYMENT.md](DEPLOYMENT.md) | Production deployment, env vars, zero-retention checklist |
| [docs/BEGINNER_DEPLOY.md](BEGINNER_DEPLOY.md) | Click-by-click deploy for a first-time host |
| [docs/planning/](planning/) | AI-native plan, production roadmap, roadmap progress, doc-quality plan |
| [SECURITY.md](../SECURITY.md) | Data-handling model, zero-leakage guarantees |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Dev setup, test workflow, ground rules |
| [.env.example](../.env.example) | Every environment variable with defaults |
</content>
</invoke>
