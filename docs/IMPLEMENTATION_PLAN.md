# Enterprise Power BI Documentation Generator — Implementation Plan

**Product:** AI-powered SaaS that ingests `.pbix` / `.pbip` files, extracts *metadata only*, and generates enterprise-grade documentation for both technical BI developers and business stakeholders.

**Guiding principle:** *Freemium-first.* Every layer is built on open-source or free-tier tooling so you can ship an MVP at ~$0/month and only pay as you scale. Paid services are flagged explicitly with their free-tier limits.

---

## 0. Architecture at a Glance

```
                        ┌─────────────────────────────────────────────┐
                        │  Frontend (Next.js / Vercel free tier)       │
                        │  Upload widget • Job status • Doc download    │
                        └───────────────┬─────────────────────────────┘
                                        │  HTTPS (signed upload URL)
                        ┌───────────────▼─────────────────────────────┐
                        │  API Gateway (FastAPI on Render/Fly.io)      │
                        │  Auth • Rate limit • Job enqueue             │
                        └───────────────┬─────────────────────────────┘
                                        │  enqueue job id
                        ┌───────────────▼─────────────────────────────┐
                        │  Redis (Upstash free) ── Celery broker/queue │
                        └───────────────┬─────────────────────────────┘
                                        │
        ┌───────────────────────────────▼───────────────────────────────────┐
        │  Worker (Celery) — runs INSIDE a tmpfs/RAM sandbox per job          │
        │                                                                     │
        │  1. INGEST & PARSE  →  2. AI AGENTS  →  3. DOC GEN  →  4. SHRED      │
        │  pbixray / pbi-tools   LangGraph        Jinja2+Pandoc   finally:rm   │
        └─────────────────────────────────────────────────────────────────────┘
                                        │ final document only
                        ┌───────────────▼─────────────────────────────┐
                        │  Short-TTL object store (R2/Supabase) OR     │
                        │  direct stream to user. Auto-expire < 1 hr.  │
                        └──────────────────────────────────────────────┘
```

**Zero-retention contract:** the only thing that survives a job is (a) the *final generated document* (short TTL, user-owned) and (b) a row in the jobs table containing **status + timestamps only** — never customer metadata content.

---

## 1. Tech Stack (Freemium Mapping)

| Layer | Tool | License / Free Tier | Notes |
|---|---|---|---|
| **Parsing — .pbix** | [`pbixray`](https://github.com/Hugoberry/pbixray) | MIT, free | Pure-Python, cross-platform. Reads schema, DAX, M, relationships, metadata. **Never call its data-dump methods** → zero leakage. |
| **Parsing — high-fidelity report/layout** | [`pbi-tools`](https://github.com/pbi-tools/pbi-tools) | OSS (AGPL), free | Best visual/layout JSON fidelity. .NET CLI. (`.pbix` full extract historically needs Windows + PBI Desktop libs; `core` build does model-only.) |
| **Parsing — .pbip (preferred path)** | Native file read | free | `.pbip` is already plain text: **TMDL** under `*.SemanticModel/definition/`, **PBIR JSON** under `*.Report/definition/`. No binary parsing at all. |
| **AI orchestration** | [LangGraph](https://github.com/langchain-ai/langgraph) | MIT, free | Deterministic multi-agent graph, structured (JSON-schema) outputs. |
| **LLM — dev / free tier** | Ollama (local), Groq free, **Google Gemini Flash free tier** | free | Use cheap/local models for dev + low-tier users. |
| **LLM — prod** | **BYOK** (user's own OpenAI/Anthropic/Gemini key) or Claude Haiku | usage-based | BYOK keeps your inference cost at $0 and strengthens the zero-retention story. |
| **Doc generation** | Jinja2 → Markdown → **Pandoc** (PDF/DOCX), `python-docx`, WeasyPrint | free | Canonical structured JSON renders to MD/PDF/Word. |
| **API** | FastAPI | free | Async, OpenAPI built-in. |
| **Queue / broker** | Celery + **Upstash Redis** | free tier (10k cmd/day) | Handles large-upload timeouts. |
| **Frontend** | Next.js + **Vercel** | free (hobby) | Upload UI, polling, download. |
| **Backend hosting** | **Render / Fly.io / Railway** | free/hobby tiers | Worker + API. |
| **Ephemeral file handling** | tmpfs / RAM disk + **Cloudflare R2** | R2 10GB free | Final doc only, short TTL. |
| **App DB (jobs + users)** | **Supabase / Neon** Postgres | free tier | Status rows only — no customer metadata. |
| **Auth** | Supabase Auth / **Clerk** | free tier | |
| **Billing (later)** | Stripe | usage-based | |

---

## 2. Ingestion & Parsing Layer (Zero Data-Leakage)

### Strategy by file type

- **`.pbip` (modern, recommended):** Treat as the golden path. It is a folder of plain-text artifacts — parse directly, no decompression, **no VertiPaq data ever present**. Encourage users to export `.pbip` (Power BI Desktop → File → Save as project) for the cleanest, safest pipeline.
- **`.pbix` (legacy):** A ZIP container. The cached business data lives in the `DataModel` part (VertiPaq, xpress9-compressed). To guarantee zero leakage:
  1. Extract **only** the model *definition* (TMSL/TMDL) and the `Report/Layout` JSON.
  2. Use `pbixray` to read **schema, measures, M, relationships** — and explicitly **skip** any `get_dataframe()` / row-level calls.
  3. Immediately delete the raw `DataModel` binary before any further processing (`finally` block).

### What to extract (and where it lives)

| Artifact | .pbip source | .pbix source |
|---|---|---|
| Tables / columns / data types | TMDL `tables/*.tmdl` | `pbixray.schema` |
| DAX measures | TMDL measure blocks | `pbixray.dax_measures` |
| M / Power Query (ETL lineage) | TMDL `expressions` / partitions | `pbixray.power_query` |
| Relationships (1:M, filter direction, active flag) | TMDL `relationships` | `pbixray.relationships` |
| **RLS roles + filter expressions** | TMDL `roles/*.tmdl` | model `roles` (TMSL) — parse directly; pbixray does not expose roles |
| Report pages / visuals / layout | PBIR `pages/*/visuals/*.json` | `Report/Layout` JSON (via pbi-tools) |
| Slicers / interactions / drill-through | PBIR visual config + `filters` | `Report/Layout` visualContainers |

### Sanitization checklist (enforced in code)
- [ ] Reject/parse-only: never deserialize VertiPaq partitions.
- [ ] Strip any `dataView`/sample-data blobs that some visual JSON embeds.
- [ ] Redact connection strings → keep server/db *type* only, drop credentials.
- [ ] Output a single normalized `model.json` in memory; no intermediate disk writes outside the tmpfs sandbox.

---

## 3. AI Orchestration Layer (The Reasoning Engine)

A **LangGraph** orchestrator fans out to specialized agents, then a reducer assembles the canonical document JSON. All agents return **schema-validated JSON** (not prose) so document assembly is deterministic.

### Agent roster

| Agent | Input | Output (→ Doc section) |
|---|---|---|
| **Business Analyst Agent** | PBIR layout + semantic schema | Core Purpose, Page-by-Page, Navigation Guide, Complex-Visual Explainers (§II) |
| **DAX Translator Agent** | each measure (batched map-reduce) | Plain-English definition per measure (§V) |
| **Data Modeler Agent** | relationships graph | Technical model summary (§IV) |
| **Auditor Agent** | measures ∩ visuals (deterministic diff, LLM only to phrase) | Orphaned/unused measures, tech debt (§VII) |

> Tip: the orphaned-measure detection should be **computed deterministically** (set difference of measures referenced in `Report/Layout` vs. measures defined in the model). The LLM only *narrates* the finding — never let it "guess" which measures are unused.

### Prompt — Business Analyst Agent (system)
```
You are an Enterprise BI Business Analyst. You are given JSON describing a Power BI
report's pages, visuals, slicers, and semantic schema (tables + key measures).
You write for NON-TECHNICAL stakeholders. Be concrete and reference real page/visual
names from the input. Never invent visuals or pages not present in the input.

Return STRICT JSON matching this schema:
{
  "core_purpose": "<one plain-English paragraph: the exact business questions this report answers, synthesized from report name + tables + key measures>",
  "pages": [ { "page_title": "<refined title>", "summary": "<2-3 sentences on this page's analytical focus>" } ],
  "navigation_guide": [ "<explicit instruction mapping a real slicer/interaction, e.g. 'Use the Date slicer on the left to filter all visuals on Sales Overview'>" ],
  "complex_visual_explainers": [ { "visual": "<name/type>", "page": "<page>", "how_to_read": "<brief explainer for a business user>" } ]
}
Only include complex_visual_explainers for: scatter plots, decomposition trees,
key influencers, R/Python visuals, maps, gauges, or custom visuals. Detect
drill-through targets and right-click/cross-filter behavior from the metadata.
```

### Prompt — DAX Translator Agent (system)
```
You translate one Power BI DAX measure into a business definition.
Input: { "name", "expression", "table", "format_string" }.
Return JSON: { "plain_english": "<what it calculates, in business terms>",
"caveats": "<filters/exclusions e.g. excludes canceled orders; time intelligence;
edge cases>", "category": "<Revenue|Cost|Ratio|Count|Time-Intelligence|Other>" }.
Do not restate the DAX. Explain intent, not syntax. If a filter like
'Status <> "Canceled"' exists, surface it in caveats.
```

### Prompt — Data Modeler Agent (system)
```
You are a data modeling expert. Given relationships (from/to table+column,
cardinality, cross-filter direction, active flag) and table list, write a
technical summary. Return JSON: { "model_shape": "<star/snowflake/flat + why>",
"fact_tables": [...], "dimension_tables": [...], "relationship_notes":
[ "<e.g. 'Sales[CustomerKey] → Customer[CustomerKey], M:1, single-direction, active'>" ],
"risks": [ "<bi-directional filters, inactive relationships, ambiguous paths>" ] }.
```

### Orchestration logic
1. Parse → normalized `model.json`.
2. **Fan-out** (parallel): Business Analyst (1 call w/ layout), DAX Translator (batched, ~10 measures/call), Data Modeler (1 call), Auditor (deterministic diff + 1 narration call).
3. **Reduce:** validate each agent's JSON against its Pydantic schema; on failure, one self-correction retry.
4. Assemble canonical `document.json`.
5. Cost/latency control: cache nothing across jobs (zero-retention), but *do* batch and run agents concurrently with `asyncio.gather`.

---

## 4. Document Generation Layer (The Output)

Render the canonical `document.json` through **Jinja2** templates → Markdown → **Pandoc** (PDF/DOCX) or WeasyPrint. Sections in this **exact order**:

```
I.   Document Metadata        — report name, ownership, refresh schedule, target audience
II.  Executive Summary &      — Core Purpose, Page-by-Page Breakdown,
     Business Guide              Navigation Guide, Complex-Visual Explainers  (Business Analyst Agent)
III. Lineage & Architecture   — source systems + Power Query/ETL transformations
IV.  Semantic Model           — Data Dictionary (tables/columns/types) + relationship schema (Data Modeler)
V.   Measure Catalog          — raw DAX side-by-side with plain-English (DAX Translator)
VI.  Security & Governance     — RLS role definitions + workspace access constraints
VII. Tech Debt / Audit        — orphaned/unused measures (Auditor)
```

- **Output formats:** Markdown (always), PDF + DOCX via Pandoc. Offer all three as downloads.
- **Measure Catalog layout:** two-column table — raw DAX (monospace) | plain-English + caveats.
- **Branding (paid SaaS tier):** swap a Pandoc reference doc / CSS for white-labeled output.

---

## 5. Security & Infrastructure

### Asynchronous workflow (no upload timeouts)
- Frontend requests a **signed upload URL**; file goes to a per-job tmpfs path (or R2 with immediate-delete-after-read).
- API enqueues `job_id` to Celery via Redis and returns `202 Accepted` instantly.
- Worker processes; frontend polls `/jobs/{id}` (or SSE) for status: `queued → parsing → generating → done/failed`.
- Large files never block the request thread.

### Zero-Retention Policy (enforced, not aspirational)
```python
def process_job(job_id, upload_ref):
    workdir = make_tmpfs_sandbox(job_id)   # RAM-backed, per-job
    try:
        raw = fetch_to_sandbox(upload_ref, workdir)
        model = parse_metadata_only(raw)   # never touches VertiPaq rows
        secure_delete(raw)                 # drop binary the instant parsing ends
        doc = run_agents_and_render(model)
        final_ref = store_final_doc(doc, ttl="1h")  # ONLY the output survives
        return final_ref
    finally:
        secure_delete_dir(workdir)         # shred temp dir on success AND failure
        # NOTE: no metadata content is ever logged or written to the app DB
```
- **No content logging:** structured logs carry `job_id`, durations, error *types* — never DAX, M, table names, or any model content.
- **Encryption:** TLS in transit; short-lived output encrypted at rest (R2/Supabase default).
- **DB hygiene:** jobs table = `{id, user_id, status, created_at, finished_at, error_code}`. That's it.
- **BYOK option:** lets enterprise customers route inference through *their* LLM key so model content never persists with any third party you control.

---

## 6. Phased Roadmap (Freemium-First)

### Phase 0 — Foundations *(Week 1)*  · cost: $0
- Repo, CI (GitHub Actions free), monorepo (`/api`, `/worker`, `/web`).
- Define canonical `model.json` and `document.json` Pydantic schemas (the contract everything depends on).
- Spike `pbixray` on a sample `.pbix` and direct-parse a sample `.pbip`.
- **Exit:** can dump schema + measures + relationships from one real file to console.

### Phase 1 — Parsing MVP (CLI only) *(Weeks 2–3)*  · $0
- Implement `.pbip` direct parser (TMDL + PBIR) and `.pbix` parser (pbixray + pbi-tools for layout).
- Extract all 7 artifact types incl. **RLS roles** and **slicer/drill-through** metadata.
- Sanitization layer + unit tests proving no row-level data is read.
- **Exit:** `python -m generator parse file.pbix > model.json` works for both formats.

### Phase 2 — AI Agents *(Weeks 4–5)*  · $0 dev (Ollama/Gemini free), BYOK later
- LangGraph orchestrator + 4 agents with the prompts above.
- Deterministic orphaned-measure diff.
- Schema validation + 1-retry self-correction.
- **Exit:** `model.json → document.json` end-to-end with real LLM output.

### Phase 3 — Document Generation *(Week 6)*  · $0
- Jinja2 templates for all 7 sections in exact order; Pandoc → PDF/DOCX/MD.
- Side-by-side Measure Catalog rendering.
- **Exit:** full document downloadable from CLI for a real report.

### Phase 4 — Async API + Web App *(Weeks 7–9)*  · $0 (free tiers)
- FastAPI + Celery + Upstash Redis; signed-upload flow; job polling/SSE.
- Next.js upload UI on Vercel; Render/Fly.io worker.
- Implement full **zero-retention** sandbox + shred-in-`finally`.
- **Exit:** upload a file in the browser → download docs; temp data provably deleted.

### Phase 5 — SaaS Productization *(Weeks 10–13)*  · free tiers + Stripe
- Auth (Clerk/Supabase), multi-tenant job isolation, **BYOK** key vault.
- Freemium gating: free = N docs/month on Gemini Flash; paid = higher limits + DOCX/branding + premium models.
- Stripe billing, usage metering, rate limits.
- **Exit:** a stranger can sign up, run a doc, and hit a paywall.

### Phase 6 — Enterprise Hardening *(Weeks 14+)*  · paid as needed
- SOC 2 path (audit logging of *actions* not content), DPA, SSO (SAML).
- Horizontal worker autoscaling; dedicated Redis; observability (OpenTelemetry, free Grafana Cloud tier).
- Optional **self-hosted / on-prem** deployment for security-sensitive customers (Docker Compose / Helm) — the strongest possible zero-retention guarantee.
- Power BI Service / Fabric REST API integration (workspace access constraints, refresh schedules) to enrich §I and §VI automatically.

---

## 7. Key Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `pbi-tools` full `.pbix` extract may need Windows/PBI Desktop | Prefer `.pbip`; use `pbixray` (cross-platform) for model; run pbi-tools in a Windows worker only if layout fidelity demands it |
| Accidental VertiPaq data exposure | Parse-only code paths + tests asserting no `get_dataframe` calls; shred binary immediately |
| LLM hallucinating pages/measures | Feed real names; deterministic diffs for audit; schema validation + retry |
| Free-tier rate limits (Redis/LLM) | BYOK for inference; queue backpressure; upgrade path documented |
| Large files time out | Async signed-upload + Celery from Phase 4; never parse in request thread |

---

## 8. Immediate Next Steps
1. Lock the `model.json` / `document.json` schemas (Phase 0) — everything keys off these.
2. Build the `.pbip` parser first (simplest, safest, modern).
3. Stand up the Business Analyst + DAX Translator agents against a real sample.
4. Wire Pandoc rendering for the 7-section template.

> Want me to scaffold the repo (FastAPI + Celery + LangGraph) and write the Phase 0 schemas and the `.pbip` parser to start? I can generate the actual code next.
