# PBICompass — Architecture Review & Documentation Quality Roadmap

**Scope.** Two questions answered here:
1. Is the current module/architecture choice correct? (Part A)
2. What exactly must change to take the generated documentation from its
   current 66/100 to a 10/10 enterprise deliverable? (Parts B–F)

Benchmark artifact: the four Zomato v0 outputs generated 2026-07-04 on the
GCloud deployment (technical, executive, user-guide, audit).

Standing constraints respected throughout: metadata-only (never row-level
data), stdlib-only parsers, graceful degradation, free-tier infra, and the
"Big 4 consultant handover, not ChatGPT" editorial bar.

> **Status (2026-07-05): Step 0, Phase 1, and Phase 2 implemented and
> test-verified.**
>
> Step 0 + Phase 1 (1.1–1.12): jinja2 extra dropped, pbixray env marker
> added, Cloud Run deploy flags documented; mad-libs killed, visuals/filters
> deduped with counts, chart-pair questions, per-batch LLM retry+fallback,
> one glossary source of truth, real usage-based KPI selection, merged Data
> Sources/Dependencies, severity-aligned known risks across all four docs
> (new `Recommendation.category`, `disconnected_tables`/`dev_leftover_naming`
> best-practice checks, `hardcoded_year`/`hardcoded_paths` findings folded
> into the shared audit engine), human-readable timestamps, zero CDN calls.
>
> Phase 2 (A2-2 shell consolidation + F.3 golden-file tests, then 2.1–2.10):
> `render/html.py` now consumes the shared `_html_shell.py` (no more
> duplicated CSS/shell) — a prerequisite that also surfaced and fixed a real
> reproducibility bug (`build_recommendations()`'s set-comprehension dedup
> depended on Python's per-process string hash randomization). Golden HTML
> snapshots added for all four renderers. Then: dark mode, mobile TOC,
> accessibility pass (skip link, landmarks, contrast, labeled SVG), DAX
> syntax highlighting + copy buttons, collapsible >10-line DAX, an
> interactive model diagram (pan/zoom/hover-highlight/click-to-jump),
> client-side search, a documentation hub + doc-switcher (CLI multi-doc
> path, where sibling filenames are deterministic — the hosted service needs
> 5.7's zip bundle first), cross-document content links (audit→technical
> measure anchors, executive→audit recommendations), and print/PDF polish
> (cover page, Confidential/Restricted watermark, Pandoc YAML title block).
>
> Phase 3 (3.1–3.9) and Phase 4 (4.1–4.5) implemented and test-verified
> (2026-07-06): page wireframes, a layered lineage graph, per-measure
> dependency trees, column impact analysis ("Used by" in the data
> dictionary), a stdlib Power Query M-step parser, an RLS role×table matrix,
> a bookmarks/drillthrough navigation map, a consolidated data-source
> inventory table, and unused-assets grouped by table with Tabular Editor
> C# snippets — plus the rule engine expanded past 50 stable-ID checks with
> fix snippets, `pbicompass.rules.toml` suppression/severity overrides, an
> opt-in `--stats` flag for VertiPaq aggregate stats, and score-trend
> persistence. This work was first drafted by another AI tool and then
> hardened here: fixed a DOCX-crashing bullet-list bug, an LLM/score-history
> cache that was silently on by default in the hosted service (a
> zero-retention violation), a catastrophic-backtracking regex in the M
> parser (real hang risk, not just "never raises"), an audit rule-ID
> collision that broke per-rule suppression, an unescaped SVG attribute, an
> unverified VertiPaq opt-in gate, and a DOCX renderer that silently lacked
> every one of the new Phase 3/4 sections while HTML/Markdown had them.
>
> Phase 5 implemented and test-verified (2026-07-06) — see Part K for the
> full work order. Part G is still open.
>
> **Part J (2026-07-06) implemented and test-verified.** J.A: every finding/
> check/recommendation now carries a stable rule ID pill; audit §1 gained
> the "Checks run/passed/failed/suppressed" ledger plus a per-component
> checks column (backed by a new `compute_checks_ledger`); the two rule IDs
> that were declared but never actually evaluated (`m2m_no_bridge`/
> `bidirectional_fact`) are now real, independently-triggerable checks;
> every High/Critical recommendation carries a fix snippet parameterized
> with its actual objects (5 new dynamic-fix branches, test-enforced on a
> multi-category stress fixture); rule suppression/severity/threshold
> overrides now support an explicit path (CLI `--rules`, a service
> `rules_file` upload field saved into the job's own sandbox) instead of
> only a cwd scan, plus two new configurable thresholds
> (`visual_density_limit`, `description_coverage_pct`); 4.5 score trend is
> formally re-scoped to ship with Phase 5's enrichment file (see 4.5's own
> note). J.B: I1 (hub/zip in the hosted service) was already fixed and is
> now test-covered; I2's anchor-slug collisions are fixed everywhere ids are
> generated from arbitrary names (a new `dedupe_ids` helper); I3's wireframe
> dead links, I4's field-parameter leakage into generated questions/
> glossary, I5's section-level (not per-finding) exec risk links, and I6/
> G.1's executive-doc restructure are all fixed — see Part J below for the
> full work order. J.C's wireframe v2
> redesign (canvas, friendly type names, title-first labels, category
> glyphs, tiny-object/decorative-overflow handling, native tooltips, clean
> `.wf-node` markup, legend) is implemented, consolidating what had been
> three separately-duplicated friendly-visual-type dictionaries into one
> (`agents/report_facts.FRIENDLY_VISUAL`). The executive document's schema
> changed shape (6 sections; `ExecutiveDocument.purpose`/`top_risks`/
> `next_steps`/etc. replace the old 11-section field set) — a deliberate
> breaking change to the JSON contract, not a bug.

---

# Part A — Architecture & module review

## A.1 Verdict: the architecture is correct. Keep it.

The layered pipeline is the right shape and each boundary is in the right
place:

```
schemas (stdlib dataclasses — the model.json contract)
   ↑ produced by
parsers (pbip / tmdl / tmsl / pbir — stdlib only)      adapters (pbixray, optional)
   ↓ consumed by
agents (orchestrator → generators per doc type; LLMClient protocol;
        deterministic fallback; audit_rules = pure rules, no AI)
   ↓ produce Document dataclasses
render (registry → md/html/docx per doc type; pandoc → pdf with fallback)
   ↓ dispatched by
service (FastAPI; queue-agnostic process_job; per-job sandbox, shredded
         in finally; in-memory JobStore + TTL; SQLite accounts/quotas)
cli (same pipeline, no service)
```

What is specifically *right* and should not be changed:

- **Zero-dependency core** (`dependencies = []`, extras for pbix/agents/
  service). This is a real differentiator — auditable supply chain matches the
  zero-leakage pitch. Almost no competitor can claim it.
- **Dataclasses over pydantic for `model.json`** — keeps the contract stdlib;
  FastAPI can validate at its own boundary. Correct call.
- **`LLMClient` as a Protocol** with `complete_json(system, user, schema)`,
  structured JSON output on all three providers, lazy SDK imports, and hard
  call timeouts. Textbook. The three official SDKs (`anthropic`,
  `google-genai`, `cohere`) are the correct modules — no LangChain needed, and
  adding it would only obscure the pipeline.
- **`process_job` as a plain queue-agnostic function** — the Celery migration
  later is a task-wrapper, not a rewrite.
- **Renderer registry** (`render/registry.py`) — one place to add a doc type.
- **Deterministic-first audit** (`audit_rules.py`, 822 lines of pure rules) —
  reproducible scores are the trust story; never move scoring into the LLM.
- **Test layout** — 11 test modules mirroring the package layout.

## A.2 Issues to fix (ranked by real-world risk)

### A2-1. Hosted-deployment state is ephemeral (HIGH — affects the GCloud deployment today)

Three related problems, all "works on my machine, breaks on Cloud Run":

1. **In-memory `JobStore`** (`service/jobs.py`): if Cloud Run scales to a 2nd
   instance, a job created on instance A returns 404 when the browser polls
   instance B. Any instance restart loses all jobs and rendered outputs.
2. **SQLite accounts DB** (`service/accounts.py`): Cloud Run's filesystem is
   ephemeral — every deploy/restart wipes accounts, API keys, and quota
   counters. Freemium quotas are currently resettable by redeploy.
3. **`BackgroundTasks` under CPU throttling**: with default Cloud Run
   settings, CPU is throttled to ~zero between requests. A background LLM job
   only makes progress during the CPU windows opened by status polls. This is
   the same failure class as the Render hang that motivated the watchdog.

**Fix now (config, no code):** deploy with `--max-instances=1` and
`--no-cpu-throttling` (a.k.a. "CPU always allocated"; check current billing —
it may reduce free-tier coverage, in which case keep throttling and accept
poll-driven progress, which the watchdog already bounds). Add these flags to
the README deploy section so it isn't tribal knowledge.

**Fix soon (small code):** back `JobStore` with SQLite in the same file,
behind the existing method surface (`create/mark_*/get/get_output/sweep`).
Outputs go in a BLOB column with the same TTL sweep. This makes single-instance
restarts survivable and costs nothing. The interface was designed for this —
use it.

**Fix later (when auth matters commercially):** port `AccountStore` to a free
managed Postgres (Supabase or Neon free tier) behind the same interface.
SQLite-over-GCS-FUSE is *not* recommended (locking semantics). Keep SQLite as
the default for self-hosted; select backend via `PBICOMPASS_DB` URL scheme.

### A2-2. Render-layer duplication (MEDIUM — slows every doc improvement)

`render/html.py` (973 lines, technical doc) predates and duplicates the shared
shell in `render/_html_shell.py` (535 lines) — CSS, font links, sidebar, and
scroll-spy JS exist twice (confirmed: both files carry the same `--font-sans`
block and Google-Fonts `<link>`s). Every Phase-2 presentation feature (search,
dark mode, hub links) would have to be built twice.

**Fix:** migrate `render/html.py` to consume `_html_shell.py`, then delete its
private copy of the shell. Do this *before* Phase 2. Add golden-file snapshot
tests for all four HTML renderers first, so the migration is provably
non-visual (see F.3).

### A2-3. Vestigial `jinja2` extra (LOW — hygiene)

`pyproject.toml` declares `docs = ["jinja2"]` but nothing imports jinja2
anywhere in `src/`. Decision: **drop the extra.** Hand-rolled builders are the
right call for single-file self-contained HTML (a feature, not a shortcut),
and `_html_shell.py` is the abstraction that keeps them maintainable. Adopt a
template engine only if doc types grow past ~6.

### A2-4. `pbixray` extra lacks an environment marker (LOW)

The comment says pbixray needs Python ≤ 3.13; encode it:
`pbix = ["pbixray; python_version < '3.14'"]` so a 3.14 install degrades
gracefully at pip time instead of failing.

### A2-5. Missing cross-cutting layers the roadmap needs (planned, not bugs)

No LLM response cache, no per-batch retry, no enrichment/diff persistence.
These are Phase 5 items below — listed here because they are *architectural*
additions (a new `agents/cache.py`, a new `enrichment.py`), not renderer
tweaks.

## A.3 Module & dependency inventory (verdict table)

| Layer | Choice today | Verdict |
|---|---|---|
| Parsing | stdlib only (zipfile, json, re) | ✅ Keep — core differentiator |
| .pbix support | `pbixray` (optional extra) | ✅ Keep; add env marker (A2-4) |
| Schemas | stdlib dataclasses | ✅ Keep |
| LLM SDKs | `anthropic`, `google-genai`, `cohere` | ✅ Keep; no framework needed |
| Agent framework | none (hand-rolled orchestrator) | ✅ Correct — LangChain/LlamaIndex would add opacity, deps, and zero capability here |
| Web | FastAPI + uvicorn + python-multipart | ✅ Keep |
| Jobs | in-memory JobStore + BackgroundTasks | ⚠️ SQLite-back it (A2-1); Celery+Redis stays the scale path |
| Accounts | stdlib sqlite3 | ⚠️ Fine self-hosted; managed Postgres for hosted SaaS (A2-1) |
| HTML | hand-rolled builders + shared shell | ⚠️ Consolidate the two shells (A2-2); then keep |
| Templates | jinja2 declared, unused | ❌ Drop the extra (A2-3) |
| DOCX | own `_docx_writer` | ✅ Keep (zero-dep) |
| PDF | pandoc if present, HTML-print fallback | ✅ Keep |
| Tests | pytest + httpx | ✅ Keep; add golden-file snapshots (F.3) |
| To add | `PyYAML` (service/CLI only, enrichment file) | Phase 5; parsers stay stdlib |

**Bottom line:** you are using the correct modules and the correct
architecture. The problems are (1) hosted-state durability, (2) one duplicated
shell, (3) two hygiene nits — plus everything in Parts B–F, which is about the
*content* of what the pipeline emits, not its shape.

---

# Part B — Where the v0 output stands: 66/100

| Category | Weight | Score | Evidence from the Zomato output |
|---|---|---|---|
| Accuracy & trustworthiness | 20 | 17 | Deterministic audit; honest "requires business confirmation"; real catches (hardcoded 2020, six local paths, `GaintCustomers` typo, m:n double-count). |
| Content completeness | 20 | 12 | No lineage graph, page wireframes, column impact analysis, M step breakdown, bookmark/drillthrough map, RLS matrix, or change log. |
| Writing quality & consistency | 15 | 8 | "What is our dyanmic_subheading?"; "Shows Sale_Value." ×5; doubled filter bullets; glossary filler ("A custom metric specific to this report"); 2 of 3 pages got fallback prose while City Performance got rich narrative. |
| Presentation & navigation | 15 | 10 | Clean shell, TOC, print CSS — but 4 siloed files, no hub/search/cross-links, static diagram, raw ISO timestamps, Google-Fonts CDN call. |
| Actionability of audit | 15 | 11 | Prioritized recs with impact/effort — but 12 checks vs ~60 in Tabular Editor BPA, no fix snippets, 32 unused columns as one comma-blob cell. |
| Delivery & workflow | 15 | 8 | HTML/DOCX/PDF exist; no bundle, no run-over-run diff, TODOs reset every run. |
| **Total** | **100** | **66** | Strong v0; below Big-4 handover. |

**Key insight driving the plan:** the parser already extracts more than the
docs render — visual x/y/width/height, per-visual field bindings, measure
dependency edges, full M expressions. A large share of the score gap is
rendering data you already have.

---

# Part C — Definition of 10/10 (target spec)

1. **One deliverable** — hub page, cross-linked docs, search, zip bundle
   (HTML + DOCX + PDF + model.json + enrichment file).
2. **Zero noise** — no templated filler; a hostile reviewer finds nothing to
   screenshot.
3. **Visually explanatory** — wireframes, lineage, interactive model diagram,
   dependency trees.
4. **Actionable to the keystroke** — every finding names objects and ships the
   exact fix snippet, with stable rule IDs.
5. **Provenance-labeled** — every section marked ⚙ Extracted / ✨ AI-inferred
   (confidence) / 👤 Human-provided.
6. **Living** — human answers persist across runs; each run reports what
   changed since the last.
7. **Air-gap safe** — self-contained files, zero CDN calls, zero telemetry.

---

# Part D — The phased plan, in detail

Format per item: **Current** (what the Zomato output proves) → **Change**
(implementation steps, files/functions) → **Done when** (acceptance check).
Effort: S ≤ ½ day · M ≤ 2 days · L ≤ 1 week.

## Phase 1 — Quality floor: kill every embarrassing line (66 → ~74)

### 1.1 Delete the mad-libs fallback — S
- **Current:** `agents/generators/user_guide.py:97` builds
  `f"What is our {m.lower()}?"` from raw measure names →
  "What is our dyanmic_subheading?". Lines 88–90 emit "Shows Sale_Value." per
  visual; line 115 emits "check activeuser at a glance".
- **Change:** in the deterministic path, *omit* "Questions this page answers"
  and "Common scenarios" sections entirely (renderers already skip empty
  lists — verify in `render/user_guide.py`, add the guard if not). Keep only
  fact-based content: visual table, filter list, page stats. Never lowercase a
  measure name into prose.
- **Done when:** generating the Zomato user guide offline contains zero
  occurrences of `what is our` (case-insensitive) and zero measure-name-echo
  sentences. Add a regression test asserting these strings are absent.

### 1.2 Deduplicate repeated visuals — S
- **Current:** Overview lists `Sale_Value [1]` … `Sale_Value [5]` as five rows
  each explained "Shows Sale_Value."
- **Change:** in the page-facts builder (`agents/report_facts.py`), group
  visuals by `(type, frozenset(metrics), frozenset(dims))`; emit one row with
  a count: "Sale_Value — card ×5". Applies to user guide *and* technical §8
  tables.
- **Done when:** no two rows in any visual table are textually identical;
  Zomato Overview table shrinks from 14 rows to ≤ 8.

### 1.3 Derive page questions from chart pairs, deterministically — S
- **Current:** questions come from measure names, not from what's charted.
- **Change:** only for visuals with both a metric and a dimension, emit
  templates keyed on dimension category: time dim → "How has {metric} trended
  by {dim}?"; geo dim → "How does {metric} compare across {dims}?"; else →
  "How is {metric} distributed by {dim}?" Use `Column.data_category` /
  data_type to classify; cap at 3, dedupe. No pair → no section (per 1.1).
- **Done when:** Zomato Overview yields "How has Sale_Value trended by Year?"
  and "How does TopN_Sale compare across cities?" and nothing else.

### 1.4 Per-page narrative retry, then per-page fallback — M
- **Current:** City Performance got rich LLM narrative; Overview and User
  Performance silently got the fallback — one failed batch degraded ⅔ of the
  doc with no signal.
- **Change:** in `agents/io.py` batching, on a failed/invalid batch response:
  retry once (jittered 2–5 s). On second failure, fall back *only for the
  pages in that batch* and call `on_warning("AI narrative unavailable for
  pages: X, Y — deterministic summary used")` so the job UI shows it.
- **Done when:** a fake client failing exactly one batch (extend
  `tests/test_agents.py::FakeLLMClient` — mind its substring routing) produces
  a doc where only that batch's pages degrade, and the warning lists them.

### 1.5 One glossary source of truth — S
- **Current:** user-guide glossary says "A custom metric specific to this
  report" for measures the technical doc *already* defines precisely
  (LostCustomers has a full DAX-translated definition two files away).
- **Change:** build the glossary from, in priority order: human-provided
  description → DAX Translator business definition (reuse the technical doc's
  agent output — generate once, share via the orchestrator context) → typed
  fallback ("definition pending — see technical doc"). Never emit the generic
  category sentence for a measure that has a real definition anywhere in the
  doc set.
- **Done when:** Zomato glossary defines LostCustomers/GaintCustomers with the
  same meaning as technical §7, and the string "A custom metric specific to
  this report" appears at most once (for genuinely unknown measures).

### 1.6 Real KPI selection for the executive doc — S
- **Current:** "Key KPIs" lists the first 5 measures in model order, so a
  title-string measure (`Dyanmic_subHeading`) ranks as a KPI.
- **Change:** in `agents/generators/executive.py`, score each measure =
  (#visuals using it) × (#pages it appears on); exclude measures whose
  category pill is Text (categories already computed by the DAX Translator)
  and hidden/orphaned measures; take top 5 with a one-line meaning from the
  shared glossary (1.5).
- **Done when:** Zomato exec doc lists Sale_Value, Order_Count, ActiveUser,
  UserCount, Rating_Count — no text measures — each with a definition.

### 1.7 Dedupe filters and navigation bullets — S
- **Current:** "Filters on this page: Type, Type." and the same "Use the
  'Type' filter…" bullet twice (two slicers bound to the same field).
- **Change:** dedupe slicer fields by (table, field) in `report_facts.py`
  before templating; note multiplicity instead: "Type (2 slicers)".
- **Done when:** no repeated field name in any filter list or bullet.

### 1.8 Human-readable timestamps — S
- **Current:** headers show `2026-07-04T11:07:25.853407+00:00`.
- **Change:** one `format_timestamp()` in `render/_shared.py` → "4 July 2026,
  11:07 UTC"; use in all four shells + Document Control. Keep ISO in
  `doc.to_json()` (machine channel).
- **Done when:** no microsecond string in any rendered HTML/DOCX/MD.

### 1.9 Executive doc: merge Data Sources & Dependencies — S
- **Current:** §3 and §10 are byte-identical six-item lists.
- **Change:** §10 becomes "Upstream dependencies": gateway (from enrichment
  when present), source systems deduped by directory/host, refresh chain.
  Until those exist, drop §10 and fold a "Dependencies" one-liner into §3.
- **Done when:** no two sections in the exec doc contain identical bodies.

### 1.10 Align risk surfacing across documents — M
- **Current:** exec doc says "1 Known Risk"; the audit found 36 findings, 3
  High + 1 Critical-class (hardcoded year appears only in the technical doc).
- **Change:** run `audit_rules` once in the orchestrator regardless of which
  doc types were requested (it's deterministic and cheap); exec "Known Risks"
  = top 3–5 findings by severity, phrased for executives, each tagged with
  severity and linked (2.7) to the audit entry. KPI card count must equal the
  list length.
- **Done when:** for the same model, exec Known Risks ⊆ audit findings, ordered
  by the same severity, counts consistent across all four docs.

### 1.11 Remove the Google Fonts CDN call — S
- **Current:** every doc requests `fonts.googleapis.com`
  (`render/_html_shell.py:501-503`, `render/html.py:571-573`) — a network
  beacon on open; air-gapped viewers silently get fallback fonts anyway.
- **Change:** delete the `<link>`s; lead the stack with system fonts
  (`-apple-system, "Segoe UI", …`). Optional later: embed a subsetted WOFF2 as
  base64 (~30 KB) behind a `--brand-font` flag.
- **Done when:** rendered HTML contains zero external URLs (assert in a
  renderer test: `http` absent outside `<a href>` of user content). Marketing
  gets a true "opens fully offline" claim.

### 1.12 Dev-leftover naming rule — S
- **Current:** a table literally named `test` (2 cols, unused) surfaced only
  inside the unused-assets blob.
- **Change:** new rule in `agents/audit_rules.py`: table/column names matching
  `^(test|tmp|temp|copy of|backup|sheet\d+|table\d+)\b` (case-insensitive) →
  Medium finding "Development leftover in production model", listing objects.
- **Done when:** Zomato audit shows the finding for table `test`; rule has a
  unit test in `tests/test_audit_rules.py`.

## Phase 2 — Presentation & navigation: product, not printout (74 → ~80)

> Prerequisite: A2-2 shell consolidation + F.3 golden-file tests, or every
> item below is built twice.

### 2.1 Documentation hub (`index.html` per job) — M
- **Change:** new `render/hub.py`: report name, health-score dial (reuse audit
  score), generated date, completeness meter (5.5), four cards (per-doc KPI
  strip + open link), "Download pack (.zip)" (5.7). Worker adds `hub.html` to
  outputs when >1 doc type; each doc's sidebar gets a doc-switcher block above
  the TOC (plain relative links — works from the zip on disk).
- **Done when:** unzipping a 4-doc job and opening `index.html` offline
  navigates to all four docs and back without a web server.

### 2.2 Client-side search — M
- **Change:** at render time embed `<script type="application/json"
  id="search-index">` holding `{title, type, anchor}` for every section,
  table, measure, column, finding. ~100 lines vanilla JS in the shell: input
  above TOC, substring+prefix ranking, ↑↓/Enter, jump-to-anchor. Index built
  by a shared helper in `_html_shell.py` from the section list each renderer
  already has. No CDN, no lunr.
- **Done when:** typing "gaint" in any doc surfaces the measure + the typo
  finding; file still opens offline; added weight < 50 KB on the Zomato doc.

### 2.3 DAX/M syntax highlighting + copy buttons — M
- **Change:** `render/_dax_highlight.py` (~80 lines): tokenize comments,
  strings, numbers, `[Measure]`/`Table[Column]` refs, keywords
  (VAR/RETURN/CALCULATE/FILTER/…) → `<span class="tok-*">`. Escape first,
  wrap after (XSS-safe: expressions are attacker-controlled text). Copy button
  per `<pre>` via one delegated listener in the shell JS.
- **Done when:** golden-file test with a DAX snippet containing `<script>`
  renders escaped; TopN_Sale in the Zomato doc shows colored keywords; copy
  button yields the raw expression.

### 2.4 Collapsible depth — S
- **Change:** `<details>` for: full M query per table (3.5), unused-asset
  groups (3.9), DAX > 10 lines (summary line = measure name). Print CSS forces
  `details[open]` (`details { display: block }` + open attribute via
  `onbeforeprint`, with a no-JS CSS fallback).
- **Done when:** screen view is scannable; Ctrl+P still shows full content.

### 2.5 Dark mode — S
- **Change:** the CSS vars already centralize color: add a
  `[data-theme="dark"]` block + `prefers-color-scheme` default + toggle
  persisted to localStorage. Force light in print CSS.
- **Done when:** toggle works in all four docs; PDFs unaffected.

### 2.6 Interactive model diagram — M
- **Change:** keep the inline SVG; add viewBox-math pan/zoom (wheel + drag,
  ~60 lines), hover-highlight of a table's edges (`data-table` attrs +
  CSS/JS), click table → `#table-{name}` anchor in the data dictionary, edge
  hover tooltip showing join columns (already in `Relationship`).
- **Done when:** on the Zomato diagram, hovering `orders` highlights its 4
  relationships; clicking `menu` jumps to its dictionary rows; print/DOCX
  unchanged (static fallback).

### 2.7 Cross-document links — M
- **Change:** stable per-object anchors everywhere: `#measure-{slug}`,
  `#table-{slug}`, `#finding-{rule-id}-{n}`. One slug helper in
  `render/_shared.py`. Then: audit findings link to the technical-doc object
  (`technical.html#measure-…` — relative, zip-safe); user-guide measure names
  link to glossary; exec risks link to audit findings. Emit links only when
  the sibling doc type is being generated in the same job (worker knows the
  set — pass it to renderers).
- **Done when:** in a 4-doc job, clicking the GaintCustomers typo finding
  opens the measure entry in the technical doc; single-doc jobs contain no
  dead links.

### 2.8 Print/PDF polish — M
- **Change:** cover page div (title, classification banner from metadata,
  version, owner, generated date, PBICompass mark) shown only in `@media
  print`; `@page` margins + footer counters where supported; optional
  diagonal CONFIDENTIAL watermark when classification ∈ {Confidential,
  Restricted}. Mirror the cover in `pandoc.to_pdf` via a Markdown title block.
- **Done when:** Ctrl+P on the technical doc yields cover + numbered pages;
  classification "Confidential" produces the watermark in both PDF paths.

### 2.9 Mobile TOC — S
- **Change:** below 1024 px, replace `display:none` with a hamburger button
  toggling the sidebar as an overlay (~15 lines JS + CSS).
- **Done when:** all sections reachable on a 375 px viewport.

### 2.10 Accessibility pass — S
- **Change:** `<title>`+`aria-label` on SVGs, `<caption>` on tables
  (visually-hidden ok), bump `--text-faint` contrast to ≥ 4.5:1,
  skip-to-content link, `aria-current` on the active TOC item.
- **Done when:** Lighthouse accessibility ≥ 95 on all four Zomato docs.

## Phase 3 — New content: show what no free tool shows (80 → ~88)

### 3.1 Page wireframes — M *(flagship; zero new parsing)*
- **Current:** `Visual` already carries x/y/width/height; `Page` carries
  width/height. None of it is rendered.
- **Change:** `render/_wireframe.py`: scaled SVG per page (≤ 480 px wide) —
  one rect per visual, type glyph + truncated title, slicers/nav tinted
  distinctly, z-ordered. Wireframe SVG links each box to the visual's row.
  Place in user guide (top of each page section) and technical §8. Skip
  gracefully when coordinates are missing (pbix path).
- **Done when:** the Zomato Overview wireframe is instantly recognizable as
  that dashboard (34 boxes, slicer top-left) and each box anchors to its
  table row.

### 3.2 Lineage graph — L
- **Change:** layered left-to-right SVG: sources → tables (via partitions/M) →
  measures (via DAX refs, already computed in `agents/usage.py`) → pages.
  Layout is deterministic layering (no graphviz): order each column to
  minimize crossings greedily; cap nodes shown (top-N by connectivity,
  "+12 more" overflow). Full edge list also as a table (DOCX/PDF/a11y
  fallback). Technical doc gets a new §6b "Data lineage".
- **Done when:** Zomato lineage shows orders.xlsx → orders → Sale_Value →
  Overview/City Performance; every column of the graph is also readable as a
  table in the DOCX.

### 3.3 Measure dependency tree — M
- **Change:** render the existing dependency edges: per-measure indented tree
  in its §7 card (LostCustomers → CurrYrSale → CurrYear …), plus one
  full-model SVG for measures with ≥ 1 edge. New audit signal in
  `audit_rules.py`: chain depth > 3 → Low finding (fragile refactoring).
- **Done when:** LostCustomers shows its 3-level tree; depth rule unit-tested.

### 3.4 Column impact analysis — M
- **Current:** unused-asset detection already computes column→consumer edges,
  then discards everything except "unused".
- **Change:** invert it: per column, list consumers — relationships, measures
  (DAX refs), visuals/pages, RLS filters. Add a "Used by" column to the §6
  data dictionary (counts, expandable detail via 2.4) and per-column anchors
  (2.7). This is the "what breaks if I delete this" table — the single most
  consulted artifact in enterprise BI docs.
- **Done when:** `orders[Value]` shows "1 measure (Sale_Value) · 2 pages";
  `users[Occupation]` shows "not referenced — see unused assets".

### 3.5 Power Query step breakdown — M
- **Change:** stdlib parser for M `let` blocks (`parsers/m_steps.py`): split
  top-level bindings respecting nesting/strings/`#"quoted names"`; classify
  steps by function (Source / Navigation / Promoted Headers / Changed Type
  (n cols) / Removed Columns / Merge …). Technical §5 gets per-table numbered
  steps; full M inside `<details>`. Unparseable M → "custom logic" + raw
  fallback, never an error.
- **Done when:** Zomato `orders` shows its real step chain; fuzz test: parser
  never raises on arbitrary strings from the M corpus in test fixtures.

### 3.6 RLS matrix — S
- **Change:** when roles exist: role × table grid of filter expressions +
  members + model permission; per-role "test with 'View as role'" checklist.
  When empty, keep the current explicit statement + link to the audit RLS
  finding (2.7).
- **Done when:** a fixture model with 2 roles renders the matrix; Zomato
  (0 roles) is unchanged except the cross-link.

### 3.7 Bookmarks, drillthrough & navigation map — L
- **Change:** extend `parsers/pbir.py` (+ mirror in tmsl path if applicable)
  to read bookmarks (name, target page, state), button actions (page nav /
  bookmark / URL), and per-page drillthrough target fields. Extend schemas
  (`Bookmark` dataclass; `Page.drillthrough_fields`; `Visual.action`).
  Render: "Navigation map" — pages as nodes, buttons/drillthroughs as labeled
  arrows — in user guide + technical §9, replacing that ✎ TODO. Parser stays
  stdlib.
- **Done when:** Zomato's Index-page button arrows render; a drillthrough
  fixture shows target + passed fields; the §9 TODO disappears when data
  exists.

### 3.8 Data source inventory table — S
- **Change:** replace both source lists with one table: type icon, trimmed
  location (basename + hover full path), tables fed (join partitions→sources),
  storage mode, auth (👤 from enrichment; "not specified"), flags (hardcoded
  path ⚠). The six identical hardcoded-path `.risk` divs collapse into one
  finding listing six objects.
- **Done when:** technical §5 and exec §3 render the table; hardcoded-path
  warning appears exactly once per doc.

### 3.9 Unused assets redesign — S
- **Change:** group by table inside `<details>`; per column show the evidence
  ("no visuals, no measures, no relationships, no RLS") from 3.4; per table
  group emit a ready Tabular Editor C# removal snippet in a code block.
- **Done when:** the 32-column comma blob is gone; each group ≤ 1 screen.

## Phase 4 — Audit depth: a real rule engine (88 → ~93)

### 4.1 Expand 12 checks → 50+ rules with stable IDs — L
- **Change:** restructure `audit_rules.py` into a rule registry: each rule =
  `id` (`PBIC-MOD-001`…), category (DAX/Model/Naming/Perf/Governance/Format),
  severity, `applies(model) -> [findings]`, fix-template ref (4.2). Port the
  *concepts* of Tabular Editor BPA rules (re-implement; verify the repo
  license before vendoring any JSON): unformatted numeric measures, floating
  `double` columns, IFERROR-in-measure, auto date/time artifacts, summarize-by
  on keys, m:n without bridge, snowflake depth, etc. Report shows rule ID per
  finding + a "Checks run: 54 · passed: 41" summary table.
- **Done when:** ≥ 50 rules, each with a fixture-backed unit test; Zomato
  audit shows rule IDs and the pass/fail ledger.

### 4.2 Fix snippets per finding — M
- **Change:** fix templates keyed by rule ID, parameterized by the finding's
  objects: bidirectional relationship → exact TMDL `crossFilteringBehavior`
  diff for *that* relationship; missing date table → complete `CALENDAR()`
  DAX; hardcoded year → the rewritten measure body; visible keys → Tabular
  Editor one-liner. Rendered in a collapsible "How to fix" with copy button
  (2.3/2.4).
- **Done when:** every High/Critical Zomato finding carries a copy-paste fix
  naming its actual objects (not generic advice).

### 4.3 Rule suppression & severity overrides — M
- **Change:** optional `pbicompass.rules.toml` (stdlib `tomllib`): disable
  rule IDs, override severities, set thresholds (visual-density limit,
  description-coverage %). CLI `--rules path`, service upload field.
  Suppressed rules render as "suppressed by configuration" (auditable, not
  hidden). Enables per-plan rule packs later (free = core 15, pro = all).
- **Done when:** suppressing PBIC-GOV-001 removes it from score + list but
  shows it in the suppressed ledger; invalid TOML fails with a clear message.

### 4.4 Opt-in VertiPaq aggregate stats (.pbix only) — M
- **Change:** extend `adapters/pbixray_adapter.py` to read aggregate
  statistics only (column cardinality, dictionary/size estimates — never
  `get_dataframe()`, enforced by the existing adapter test). Gate behind
  `--stats` / an upload checkbox, off by default. Feed into rules: "named like
  an identifier" heuristic becomes "user_id: 211k distinct (measured)".
  Document the boundary in the Methodology appendix (5.6): aggregates ≠ rows.
- **Done when:** with `--stats` on a pbix fixture, cardinality findings show
  measured numbers labeled "measured"; without it, output is byte-identical
  to today; adapter test proves no row-level API is ever called.

### 4.5 Score trend — S — **formally deferred to Phase 5 (J.A.4, 2026-07-06)**
- **Change:** persist component scores + date in the enrichment file (5.1);
  when present, audit header renders "77 → 82 since 12 Jun 2026" with
  per-component deltas.
- **Done when:** two consecutive runs with the enrichment file round-tripped
  show the delta line.
- **Status:** blocked on 5.1 (the enrichment file doesn't exist yet — see
  Part I's own note). Do not count this against "Phase 4 complete"; it
  ships alongside 5.1. The CLI's simpler env-var-keyed score history
  (`agents/audit_rules.get_and_update_score_history`, opt-in, off in the
  hosted service) is a different, already-shipped mechanism and doesn't
  satisfy this item — it has no per-component deltas and isn't tied to the
  enrichment file's round-trip.

## Phase 5 — Enterprise workflow: the moat (93 → 96+)

### 5.1 Enrichment round-trip — L *(the killer feature)*
- **Current:** every ✎ To complete resets on each run; human knowledge has
  nowhere to live.
- **Change:** new `pbicompass/enrichment.py`. Emit
  `{report}.enrichment.yaml` skeleton containing every human field: document
  control (owner/version/status/classification), refresh schedule, per-source
  auth/latency, per-measure & per-column description overrides, assumptions,
  support contacts, suppressions (4.3), last-run scores (4.5), model
  fingerprint (5.2). Accept it back: CLI `--enrich file`, service upload
  field. Precedence: enrichment > model metadata > AI inference — enriched
  values render with the 👤 badge (5.6). PyYAML lives in the CLI/service layer
  only (`enrich` extra); parsers stay stdlib.
- **Done when:** run → fill 3 fields in the YAML → rerun: the 3 TODOs are
  replaced by 👤-badged values, completeness meter (5.5) rises, and the YAML
  skeleton is included in the zip (5.7). Round-trip property test: emit →
  load → emit is stable.

### 5.2 Diff / change log — L
- **Change:** `pbicompass diff old-model.json new-model.json` +
  auto-section "Changes since last documentation" when the enrichment file
  carries the previous fingerprint + summary. Diff on the normalized dict:
  added/removed/renamed tables, columns, measures (DAX text hash → "logic
  changed"), relationships, pages/visual counts, health delta. Store only
  hashes + names in the enrichment file (zero-retention: no DAX bodies
  persisted server-side).
- **Done when:** editing one measure in the Zomato fixture and rerunning
  yields exactly one "changed" entry; the service stores nothing beyond what
  the user's own enrichment file carries.

### 5.3 Critic agent pass — M
- **Change:** after generation, one LLM call per doc (cheap model, low
  effort): input = doc JSON + STYLE_RULES + object-name list; output
  (structured JSON) = violations {location, quote, rule, suggested fix} for:
  banned marketing words, name-echo prose, claims referencing nonexistent
  objects, duplicated sentences. Orchestrator applies safe auto-fixes
  (deletions/replacements), logs the rest as warnings. Deterministic
  validators (name-existence, duplicate detection) run first in pure Python so
  the LLM only judges style. Skipped silently when offline.
- **Done when:** seeding a generator with a banned word in a test causes the
  critic path to strip it; offline runs are unaffected; net extra cost ≤ 1
  call per doc.

### 5.4 LLM response cache — M
- **Change:** `agents/cache.py`: SQLite keyed on SHA-256 of (agent name,
  prompt version, model id, canonical input JSON) → response JSON + timestamp.
  Wired inside `call_llm` (generators/base.py). Env
  `PBICOMPASS_LLM_CACHE=path|off`; off by default in the hosted service
  (zero-retention: cache would persist derived metadata server-side — allow
  only per-job in-memory reuse there), on by default for CLI.
- **Done when:** second identical CLI run makes zero network calls (assert
  with a counting fake client); hosted service behavior unchanged.

### 5.5 Completeness meter — S
- **Change:** count human-input fields filled vs total (the ✎ registry
  already implies the denominator — make it explicit in each generator).
  Render "84% complete · 6 fields awaiting input" bar in each doc header +
  hub card, listing the missing field names as anchors.
- **Done when:** filling one enrichment field moves the number on the next
  run.

### 5.6 Provenance badges + Methodology appendix — M
- **Change:** every section/field renders one of: ⚙ Extracted · ✨ AI-inferred
  (confidence) · 👤 Human-provided. Carried in the Document schema per section
  (extend dataclasses with `provenance: str`), set by generators, rendered by
  all three formats (DOCX: bracketed tags). New final section in technical +
  audit: "Methodology & guarantees" — what was parsed, which agents ran
  (engine + prompt version), what is deterministic, the no-row-data and
  no-retention guarantees, and what this tool *cannot* know (measured
  performance, verified business meaning).
- **Done when:** every H2 section in all four Zomato docs shows exactly one
  badge; the appendix renders engine + prompt-version actually used.

### 5.7 Export bundle — S
- **Change:** worker zips hub + all rendered formats + `model.json` +
  enrichment skeleton into `{report}-documentation.zip` (stdlib `zipfile`,
  in-sandbox, stored like other outputs). Hub button + `?format=zip`
  download; CLI `--bundle`.
- **Done when:** the zip opens fully offline with working relative links
  (2.1/2.7) and contains the enrichment skeleton ready to fill.

---

# Part E — Free tooling inventory

| Need | Choice | License/Cost | Note |
|---|---|---|---|
| LLM narrative | Gemini Flash free tier · Cohere trial · offline fallback | free | already integrated; add retry (1.4), critic (5.3), cache (5.4) |
| Structured output | native JSON-schema modes on all 3 SDKs | free | already used — keep, never parse freeform |
| Diagrams (model/lineage/wireframe/deps) | hand-rolled inline SVG | free | preserves single-file portability; no Mermaid/D3/CDN |
| Search | vanilla JS + embedded JSON index | free | no lunr at this scale |
| Highlighting | own ~80-line DAX/M tokenizer | free | avoids bundling Prism |
| Enrichment file | PyYAML (new `enrich` extra) | MIT | CLI/service layer only; parsers stay stdlib |
| Rules config | stdlib `tomllib` | stdlib | read-only suffices |
| .pbix stats | pbixray aggregates, opt-in | MIT | never `get_dataframe()` |
| BPA rules | re-implement concepts; cite inspiration | — | verify license before vendoring any rule JSON |
| Zip/diff/M-parse | stdlib (`zipfile`, `difflib`, `re`) | stdlib | |
| Hosted accounts (later) | Supabase or Neon free Postgres | free tier | behind existing AccountStore interface (A2-1) |

Explicitly rejected: LangChain/LlamaIndex (opacity + deps, zero added
capability), headless-Chrome screenshotting (needs real data, heavy), CDN
assets of any kind (breaks air-gap + zero-leakage claim), SQLite-on-GCS-FUSE
(locking).

# Part F — Sequencing, score projection, test strategy

## F.1 Order

| Step | Content | Est. effort | Score after |
|---|---|---|---|
| 0 | A2-1 deploy flags · A2-2 shell consolidation · A2-3/4 hygiene · F.3 golden tests | ~1–2 days | 66 (foundation) |
| 1 | Phase 1 (1.1–1.12) | ~2 days | ~74 |
| 2 | Phase 2 (2.1–2.10) | ~4 days | ~80 |
| 3 | Phase 3 (3.1–3.9) | ~1 week | ~88 |
| 4 | Phase 4 (4.1–4.5) | ~1 week | ~93 |
| 5 | Phase 5 (5.1–5.7) | ~1 week | ~96 |

Dependencies: 0 → everything; 1 before 2 (polish on top of noise fails
review); 2.7 anchors before 3.2/3.4 links; 4.3/4.5 and 5.2 need 5.1's file —
build 5.1 early in the Phase-4/5 stretch if parallelizing. Phases 4 and 5 are
otherwise parallelizable.

## F.2 Why not 100

The last ~4 points require what a metadata-only tool cannot honestly claim:
measured query performance, verified business definitions, actual data
freshness. Saying exactly that in the Methodology appendix (5.6) is itself
part of scoring 96.

## F.3 Test strategy additions

1. **Golden-file HTML snapshots** for all four renderers on the Zomato-like
   fixture (normalize timestamps) — before A2-2 and every Phase-2 change.
2. **Anti-regression string asserts**: banned phrases ("what is our",
   "A custom metric specific to this report" >1, "Shows X." duplicates,
   `fonts.googleapis`) never reappear.
3. **FakeLLMClient batch-failure scenarios** for 1.4/5.3 — mind the
   substring-based routing in `tests/test_agents.py` when adding prompts.
4. **Fuzz the M step parser** (3.5) — must never raise.
5. **Adapter guard test** (4.4) — proves no row-level pbixray API is called.
6. **Round-trip property test** for enrichment (5.1): emit → load → emit
   stable.

---

# Part G — Document format assessment (is the format right?)

Verdict per document type, measured against what enterprises actually
produce and distribute:

| Document | Format verdict | Why |
|---|---|---|
| Technical | ✅ Right | 18-section skeleton matches a consultancy solution-design/handover doc (document control, RLS, gateway, deployment, sign-off). Keep. |
| Audit | ✅ Right | Score → components → findings → prioritized recs mirrors industry review format. Depth, not structure, is the gap (Phase 4). |
| Executive | ❌ Restructure | 12 sections, raw file paths, and table counts are developer content. Execs get 1–2 pages of decisions, KPIs, risks-with-asks, ownership. |
| User guide | ⚠️ Incomplete | Right spine (intro → getting started → pages → glossary); missing freshness, contacts, and limitations sections users always ask about. |
| Output formats | ✅ Right | HTML (read) / DOCX (edit + sign-off) / PDF (distribute) / MD (wiki) / JSON (machine) is the correct, complete set. Four audience docs + hub (2.1) beats one mega-doc. |

### G.1 Executive doc restructure — M
- **Current:** 12 sections; §3 lists raw `C:\Users\faisa\...` paths; §7 is
  table/column counts; §10 duplicates §3; statistics outrank risks.
- **Change:** collapse to 6 sections, ≤ 2 printed pages:
  1. *Purpose & value* (merge current 1+8), 2. *Key KPIs* (top 5 with
  meanings, per 1.6), 3. *Top risks & recommended actions* (merge 9+12;
  sourced from audit per 1.10; each risk phrased as a consequence + ask —
  "6 Excel workbooks on a personal drive: refresh breaks if that laptop is
  off; approve a move to SharePoint"), 4. *Data & refresh at a glance*
  (source *types* and refresh cadence — never paths; merge 3+4+10),
  5. *Ownership & accountability* (owner, steward, classification — from
  enrichment), 6. *What's next* (top remediation + doc-completeness ask).
  Keep the 4-KPI header strip; drop the statistics tables (they live in the
  technical doc).
- **Done when:** exec doc prints ≤ 2 pages (excluding cover), contains zero
  file paths and zero model statistics outside the KPI strip, and every risk
  carries an action.

### G.2 User guide: the three missing sections — M
- **Change:** add (a) *Data freshness* — refresh schedule + "data as of",
  from model/enrichment metadata; explicit "refresh not documented" line
  otherwise; (b) *Getting help* — owner/support contact from enrichment
  (👤-badged), with the section omitted rather than templated when unknown;
  (c) *Known limitations, in user terms* — user-relevant subset of technical
  §15 auto-translated by template, e.g. hardcoded 2020 → "figures labelled
  'current year' show 2020 and do not update". No LLM filler: each section
  renders only from real metadata (per the 1.1 principle).
- **Done when:** the Zomato guide answers: how fresh is the data, who do I
  ask, what should I not trust — or plainly says "not documented" per item.

### G.3 Technical doc format nits — S
- **Change:** (a) render Business Requirements (§3) only when enrichment
  supplies requirements — otherwise a single "requirements not yet captured"
  TODO line, never a 4-row inferred filler table; (b) resolve the §6/§14
  dictionary-vs-glossary split: §6 keeps column-level dictionary, §14 keeps
  business terms, each cross-linking the other via 2.7 anchors, and the
  apologetic placement note is removed.
- **Done when:** no table exists whose every row is "inferred from the page;
  confirm with the business owner".

### G.4 Document-control strip on all four documents — S
- **Current:** only the technical doc carries owner/version/classification;
  the other three circulate with no governance context.
- **Change:** compact one-line control strip under every header card
  (Owner · Version · Status · Classification · Generated), from the same
  metadata/enrichment source; "not specified" renders as such. Classification
  also drives the print watermark (2.8) on all four.
- **Done when:** every generated artifact (HTML/DOCX/PDF, all types) shows
  the strip; setting classification once affects all four.

### G.5 (Backlog) Machine-readable catalog export — M
- **Change:** optional `catalog.csv`/`catalog.json` export of the data
  dictionary + lineage edges in a Purview/Collibra-importable shape —
  positions PBICompass as a feeder for enterprise catalogs. Post-Phase-5.

Sequencing: G.3/G.4 belong in the Phase 1 batch (small, template-level);
G.1 early in Phase 2 (it changes what the exec renderer emits before polish
lands on it); G.2 partially now (freshness/limitations from model metadata)
and fully once enrichment (5.1) supplies contacts.

---

# Part H — Phase 1–2 verification against the 2026-07-05 Zomato output

**Re-score: 74/100** (from 66). Matches the post-Phase-1 projection; short of
the post-Phase-2 target (80) because the flagship Phase-2 items never reach
the *service* path — see P1.

Verified landed: shell consolidation (all four docs share one shell), system
fonts / zero external URLs (1.11), human timestamps (1.8), dark mode (2.5),
mobile TOC + scrim (2.9), skip-link/aria/nav a11y (2.10), print cover +
watermark + @page (2.8, on all four docs — G.4 satisfied for print),
collapsibles + beforeprint (2.4), DAX highlighting + copy buttons (2.3),
per-object anchors `#table-…`/`#measure-…` (2.7 anchors), interactive diagram
with dm-node/data-table wiring (2.6), mad-libs deleted + chart-pair questions
(1.1/1.3), visual dedupe "Card ×5" (1.2), DAX-derived glossary (1.5), exec KPI
selection excludes text measures (1.6), exec Dependencies section removed
(1.9), exec risks = top 5 audit findings with severities, counts consistent
(1.10), dev-leftover rule fires on `test` (1.12), + two new checks
(disconnected tables, hardcoded paths in governance), hardcoded year now
Critical in the audit DAX review. Health score honestly dropped 77 → 71 as
the rule set grew.

## Punch list before Phase 3

| # | Finding | Evidence / root cause | Fix |
|---|---|---|---|
| P1 | **Hub, doc-switcher, and cross-doc links exist only in the CLI path.** The hosted service — the actual product — emits none of them. | `service/worker.py` has zero references to `doc_links`/`sibling_hrefs`/hub; `cli.py:253-279` wires them. The 07-05 outputs carry the `.doc-switcher` CSS but no switcher markup. | Wire `doc_links` + `sibling_hrefs` into the worker's render calls using the composite output names; render `index.html` (hub) when multi-type; **pull 5.7 (zip bundle) forward** — relative links only work when the files sit side by side, so the zip is the natural delivery. |
| P2 | **Search index is sections-only in audit/exec/user-guide.** Only the technical renderer enriches it (measures+tables). Findings, columns, and glossary terms are indexed nowhere — searching "gaint" in the audit doc finds nothing (2.2 acceptance fails). | `render/html.py:559-565` builds the rich index; the other three renderers fall through to the sections-only default in `_html_shell.py:1157`. | Each renderer passes its own entries: audit → findings + checked rules; user-guide → glossary terms + pages; exec → risks + KPIs. |
| P3 | **Business-language regression in glossary and exec KPIs.** "LostCustomers — Computes FILTER() over CurrYrSale, PrevYrSale, UserCount", "CurrYear — A derived metric: 2020." Executives and business users now read DAX function names. | Glossary/KPI lines take the deterministic DAX paraphrase even when the DAX Translator's business definition exists (technical doc has "Number of users who had sales in the previous year but not the current year"). | Priority order per 1.5: human > LLM business definition > *plain-English* deterministic template ("number of unique user IDs", never `DISTINCTCOUNT`). Ban function-name tokens in exec + user-guide renderers (assert in tests). |
| P4 | **"Filters on this page: Type, Type." and the doubled 'Use the Type filter' bullet survived** (1.7 not implemented). | `p.filters` is joined raw at `render/user_guide.py:62,153,229`; no dedupe where the list is built. | Dedupe by (table, field) at the point `filters` is populated; render multiplicity as "Type (2 slicers)". Fixes md/html/docx at once. |
| P5 | **Overview and User Performance still get deterministic-only treatment** while City Performance has LLM narrative — the same ⅔ asymmetry as v0, now degrading gracefully instead of embarrassingly. | Either 1.4's retry isn't recovering those batches or the warning isn't surfacing. | Confirm 1.4 retry landed + warning listing affected pages appears in job output; if retry ran and still failed, log which batch/engine. |
| P6 | Exec doc: §11 Future Recommendations repeats §9 Known Risks items (bidi, m:n), and items are two mashed sentences ("…in their DAX. Calculations stay correct…"). REQ-01 filler table still renders in technical §3. Screen headers still lack the control strip (print cover has it). | G.1/G.3/G.4-screen were defined after the Phase-2 work started. | Fold into the G batch: G.1 restructure subsumes the §9/§11 overlap; G.3 kills the REQ filler; G.4 adds the screen strip. |

Order: P1 → P2 → P3 (visible product gaps), then P4/P6 (small), P5
(verification). Then Phase 3 as planned — 3.1 wireframes first.

---

# Part I — Phase 3–4 verification against the 2026-07-05 Corporate Spend output

**Re-score: 82/100** (66 → 74 → 82). Plan targets: 88 post-Phase-3,
93 post-Phase-4. Verdict: **on plan through Phase 3 (with 3 leftovers);
Phase 4's core is NOT in the output** — the gap to 93 maps exactly to the
missing items below.

**Punch list P1–P5 from Part H: all fixed.** Hub/switcher/cross-links render
in the output (P1), search indexes carry KPIs/risks/pages/terms/39 findings
(P2), business language restored — "Var Plan % — Percentage difference
between Actual and Plan" (P3), no filter dupes (P4), and *both* pages got
LLM narrative with personas (P5).

**Phase 3 verified:** 3.1 wireframes ✓ (clickable, aria-labelled, slicers
tinted, on user guide + technical §8), 3.2 Data lineage section ✓,
3.3 dep-chain audit signal ✓, 3.4 "Used by" impact column ✓, 3.5 partial
(Full M in collapsibles, but no numbered step breakdown), 3.6 n/a (no roles),
**3.7 bookmarks/nav map ✗ — the input project literally contains
bookmarks.json, unparsed**, 3.8 ✗ (exec §3 still a raw Dropbox path list),
3.9 unverified. Bonus: correctly identified the galaxy schema (2 fact
tables), counted the hidden page, detected auto date/time artifacts
(LocalDateTable_*), and shipped Phase-5 items early — provenance pills +
technical §19 "Methodology & Guarantees".

**Phase 4 status:** new rules exist (auto date/time, schema shape,
text-length heuristic, dep-chain depth) and 9 fix-snippet code blocks appear
in audit recommendations (4.2 partial). Missing entirely from the output:
**4.1 rule IDs + "checks run / passed" ledger, 4.3 suppression config,
4.5 score trend** (blocked on 5.1). Without IDs and the ledger it reads as
"more recommendations", not a rule engine.

## Punch list I (before Phase 5)

| # | Finding | Detail |
|---|---|---|
| I1 | Hub `index.html` missing from the output folder | Switcher's "← Documentation hub" link is dead on disk. Verify the hub is emitted by this generation path and included in the download/zip. |
| I2 | Glossary anchor collisions (duplicate HTML ids) | "Var LE1" and "Var LE1 %" both slug to `term-var-le1` (×4 pairs) — the slugifier drops `%`; search jumps to the wrong row. Add uniquing (`-2` suffix) or encode the symbol. |
| I3 | Wireframe boxes link to nonexistent anchors | Non-data visuals (`#visual-…-basicshape`, `-button`, `-text-box`) have no table rows → dead intra-doc links. Only wrap data visuals in `<a>`, or anchor non-data objects to the page card. |
| I4 | Field-parameter leakage into prose | Columns named `select`/`select1` yield "How is Actual distributed by select?" Recognize field-parameter/disconnected helper tables and use display names or suppress the question. |
| I5 | Exec risk links are section-level | They point at `audit.html#sec8` while per-finding anchors exist — deep-link each risk to its finding. |
| I6 | G.1 exec restructure still pending | Raw path in §3, statistics tables in §7, 11 sections. |

Order: finish Phase 4 core (4.1 ledger + IDs, 4.3) → I1–I5 (small) → Phase 5
(5.1 enrichment unblocks 4.5 trend and the completeness meter) → G.1/G.2.

---

# Part J — "Fix till Phase 4" work order (2026-07-06)

Everything required to truthfully call Phases 0–4 done. Self-contained; each
item has a done-when so any tool/agent can implement and verify.

> **Status (2026-07-06): implemented and test-verified**, except 4.5 (J.A.4),
> which is formally re-scoped to Phase 5 per its own note above — every
> other J.A/J.B item, plus J.C, is done. New/changed test coverage:
> `tests/test_audit_rules.py` (rule ledger, the two previously-dead checks,
> fix-snippet coverage, threshold/rules-path config),
> `tests/test_cli.py`/`tests/test_service.py` (`--rules`/`rules_file`),
> `tests/test_report_facts.py` (field-parameter recognition),
> `tests/test_generators.py`/`tests/test_render.py` (the executive-doc
> restructure, per-finding deep links), and a new `tests/test_wireframe.py`
> (wireframe v2 + I3's link-resolution fix).

## J.A Finish Phase 4 core (the 82 → ~87 items)

1. **Rule IDs + ledger (4.1).** Every audit finding and best-practice check
   carries a stable ID (`PBIC-MOD-001`, `PBIC-DAX-003`, `PBIC-GOV-002`, …)
   rendered as a small pill; audit §1 gains a one-line ledger under the score:
   "Checks run: N · Passed: X · Failed: Y · Suppressed: Z" plus a per-category
   row in the component table. IDs live in the rule registry
   (`agents/audit_rules.py`), never renumbered once shipped.
   *Done when:* every finding in the Corporate Spend audit shows an ID pill
   and §1 shows the ledger; IDs are asserted stable by a unit test.
2. **Fix-snippet coverage (4.2 finish).** 9 code blocks exist; require one for
   *every* High/Critical finding, parameterized with the finding's actual
   objects (the date-table snippet names the real fact tables; the path
   snippet contains the real M step rewritten with a parameter).
   *Done when:* count(High+Critical findings) == count(their fix snippets),
   test-enforced on a fixture with all rule categories firing.
3. **Suppression config (4.3).** `pbicompass.rules.toml`: `disable = [ids]`,
   `[severity]` overrides, `[thresholds]` (visual density, description %).
   CLI `--rules`, service upload field. Suppressed rules render in a
   collapsed "Suppressed by configuration (n)" ledger — auditable, not hidden.
   *Done when:* disabling a rule removes it from score + findings but shows
   it in the suppressed ledger; invalid TOML → clear error, job not failed.
4. **4.5 score trend: formally deferred to Phase 5** (needs the enrichment
   file). Note it in the plan, stop counting it against Phase 4.

## J.B Defect fixes (Punch list I, restated as a work order)

5. **I1 — hub emission.** `index.html` must be produced and delivered by every
   generation path (CLI multi-type, service multi-type, zip). *Done when:* the
   output folder of a 4-doc run contains index.html and no switcher link 404s.
6. **I2 — glossary slug collisions.** `Var LE1` vs `Var LE1 %` both →
   `term-var-le1`. Make `anchor_slug` collision-safe (append `-2`, `-3` on
   repeat within a doc). *Done when:* no duplicate `id=` attributes in any
   rendered doc (add a renderer test that parses ids and asserts uniqueness).
7. **I3 — wireframe dead links.** Only data visuals get `<a href>` to their
   table row; slicers link to the page's filter list; buttons/shapes/text
   boxes render unlinked. *Done when:* every `href="#…"` in a wireframe
   resolves to an existing id (test: collect hrefs, assert ⊆ ids).
8. **I4 — field-parameter recognition.** Tables/columns that are field
   parameters or disconnected helper tables (heuristic: single-column
   calculated table used only in slicers/axes, names like `select`,
   `select1`, `Range`) are labeled "(field selector)" and excluded from
   generated questions. *Done when:* "How is Actual distributed by select?"
   can no longer be produced; glossary shows "select — a field selector that
   switches what the chart displays".
9. **I5 — deep-link exec risks.** Exec Known Risks link to the specific
   finding anchor (per-finding ids exist in the audit index), not `#sec8`.
10. **I6 / G.1 — exec restructure.** As specced in G.1: 6 sections, ≤2 print
    pages, no file paths, no statistics tables, risks with asks.

## J.C Wireframe v2 (design rework — replaces the current look)

Current problems (from the 2026-07-06 screenshot): truncated internal type
names ("lineStackedC…", "Decompositio…"), no visual titles, empty white
canvas with no page frame, stray unreadable mini-rects, uniform washed-out
blue, inline `style=`/`onmouseover=` attributes on every rect.

Spec:

1. **Canvas** — draw the page as a "slide": full-viewBox rect, `#f8fafc`
   fill, 1px border, 8px inner margin; boxes sit *on* a page instead of
   floating in white space. Canvas stays light in dark mode (same rule as
   the model diagram).
2. **Friendly type names** — map internal `visualType` to the display names
   already used in the visual tables: `lineStackedColumnComboChart` →
   "Combo chart", `decompositionTree` → "Decomposition tree",
   `stackedAreaChart` → "Area chart", etc. Never render a camelCase
   internal name; unknown types → "Visual".
3. **Title-first labels** — large boxes (≥60×24 viewBox units): line 1 =
   visual title, 600 weight, ellipsis-truncated to the box; line 2 =
   friendly type, smaller + muted. Medium boxes: type only. Small: no text.
4. **Type glyphs** — 12×12 hand-rolled icons in `<defs>` + `<use>`: bars,
   line, combo, area, map pin, matrix grid, "123" card, funnel (slicer),
   tree. Top-left of each data box.
5. **Category styling** — data visuals: white fill, 1.5px indigo stroke
   (pop); slicers: amber tint (keep); buttons/nav: green tint, thin stroke;
   text/images/shapes: light-gray fill at 50% opacity, no stroke — visible
   but receding.
6. **Tiny-object handling** — any object smaller than 0.5% of page area
   renders as a 3px dot, unlabeled, unlinked; ≥3 overlapping decorative
   shapes collapse to one with a footer note "+n decorative shapes".
7. **Native tooltips** — `<title>Visual title — Type (fields)</title>` inside
   each data-visual link.
8. **Clean markup** — replace per-rect `style="…"` + `onmouseover`/`onmouseout`
   attributes with a `.wf-node` class and a CSS `:hover` rule in the shell
   (smaller HTML, CSP-safe).
9. **Legend** — one `.legend` row under each wireframe: Data visual · Slicer
   · Navigation · Decorative.

*Done when:* the IT Spend Trend wireframe shows boxes like
"Var Plan % by Country/Region / Combo chart" with glyphs on a page-framed
canvas; zero camelCase type names, zero dead hrefs, zero inline event
handlers (grep-enforced in a renderer test); a reader can match the
wireframe to the real report page at a glance.

Estimated effort: J.A ≈ 2–3 days, J.B ≈ 1 day, J.C ≈ 1 day.
Score projection once J is done: ~88–89 (Phase-4-complete territory),
leaving Phase 5 + G.2 to reach 93–96.

---

# Part K — Phase 5 implemented and test-verified (2026-07-06)

Investigation before starting found the plan's own status note understated
what was actually true: 5.4 (LLM cache) and 5.5 (completeness meter) were
already fully wired; 5.6 (provenance badges) was wired only in HTML and only
on 5 of 19 sections; 5.7 (export bundle) already zipped per-doc outputs in
the hosted service (pulled forward during the Part H/I punch list) but
lacked `model.json`/the enrichment skeleton and any CLI equivalent. Only
5.1/5.2/5.3 were genuinely unwired, as the note said.

**5.1 Enrichment round-trip.** `--enrich PATH` on `generate` (CLI):
bootstraps a skeleton on first use, applies an existing file's measure/
column descriptions, data-source/role details, and rule overrides on
subsequent runs, and rewrites the file afterward so filled fields persist.
Service gets a matching `enrichment_file` upload field. Fixed two real bugs
surfaced along the way: `generate_enrichment_template` read `model.meta.
owner`/`.refresh_schedule`/etc., fields `ModelMeta` never declared (would
have raised `AttributeError` on first real use — the module was untested,
not just unwired); and `DataSource`/`Role` lacked the `authentication_status`/
`members_description`/`filter_logic_explanation` fields `apply_enrichment`
already tried to set on them. Both fixed at the schema level so the round
trip (emit → load → emit stable, including fields with no model-side home,
like `rules_config`/`history`, carried forward via a new `previous` param)
is genuinely stable, not just plausible-looking.

**5.2 Diff / change log.** New `pbicompass diff old.json new.json` CLI
subcommand. Enrichment-driven auto-section: compares the current model
fingerprint against the enrichment file's stored one and surfaces the
carried-forward summary when they differ. `doc.changelog` rendering — which
existed in `html.py` only — is now in `markdown.py`/`docx.py` too, and the
audit document (which had no changelog rendering in *any* format) gained it
in all three.

**5.3 Critic pass.** `agents/critic.py` rewritten to operate on labelled
`(location, text)` fields rather than one flat string with a blind
`.replace()` — the old shape couldn't have propagated a fix to more than one
renderer, since each renderer builds its own string from the `Document`
dataclass, not from a shared text blob. Deterministic pre-pass (banned
marketing words, duplicate-adjacent-sentences, unknown-bracketed-name
warnings) always runs, pure Python, no LLM; a code-fence guard keeps it from
touching fix-snippet DAX/TMDL. The LLM style pass runs only when a client is
given (offline runs unaffected) and is routed through `call_llm` so it's
cache-covered like every other call. Wired into all four generators — net
cost is exactly 1 extra call per doc, confirmed by updated `FakeLLMClient`
call-count assertions in `tests/test_generators.py`.

**5.6 provenance badges finished + de-iconized.** Extended the field-
override-driven badge logic to all 19 sections (was 5) and ported it to
`markdown.py`/`docx.py` (was HTML-only). Mid-session the user asked to drop
the ⚙/✨/👤 glyphs entirely — folded into this same pass since it touched the
identical code path: canonical labels are now the bare strings `"Extracted"`/
`"AI-inferred"`/`"Human-provided"` everywhere (schema defaults, `enrichment.
py`, `technical.py`, all three renderers) instead of two competing
conventions (iconized strings in some places, bare `"human"/"ai"/"extracted"`
in others).

**5.7 export bundle finished.** `model.json` + (when `--enrich`/
`enrichment_file` was used) the regenerated enrichment skeleton now ride
inside the existing multi-doc zip in both the CLI and the service — gated on
`multi` in both, preserving the single-doc-type API back-compat contract
(flat, dot-free output keys) that a first attempt at this briefly broke.
New CLI `--bundle` flag: renders every format for the requested document
type(s) into one zip, single-doc-type bundles included (useful for handoff
even without a sibling doc).

New/changed test coverage: `tests/test_enrichment.py` (round-trip property
tests, diff/changelog unit tests, changelog renderer-parity across all three
formats for both technical and audit docs), `tests/test_critic.py`
(deterministic pre-pass, LLM pass, end-to-end generator wiring verified
across HTML/Markdown/DOCX), `tests/test_cli.py`/`tests/test_service.py`
(`--enrich`/`enrichment_file`, `diff`, `--bundle`), `tests/test_render.py`
(no-icon regression + full 19-section badge coverage, for HTML/Markdown/DOCX
alike).

Full suite: 337 passed, 2 skipped (service extras not installed in every
environment).
