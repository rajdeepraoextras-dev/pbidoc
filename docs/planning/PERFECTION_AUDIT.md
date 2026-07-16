# PBICompass — "Best-on-the-Internet" Perfection Audit & Task Plan

> Written 2026-07-16 after a full read of the source tree (28k LOC, 958 test
> functions, 817+ passing), the four planning docs (AI_NATIVE_PLAN, GO_LIVE_PLAN,
> PRODUCTION_ROADMAP, PERFECT_OUTPUT_TASKS), the benchmark, and the model schema
> + parsers. Purpose: answer "is the architecture perfect / what's missing" and
> turn the answer into a verifiable task list. Follows the repo's multi-tool
> handoff convention (plan lives in-repo so it survives a Claude ↔ Gemini switch).

## Verdict

**The architecture is not the problem.** The pipeline
(`parse → shared AI context → generators (technical/executive/audit/user-guide)
→ grounding/consistency/reviewer loop → render md/html/docx/pdf → zero-retention
service`) is genuinely well-designed, well-layered, and unusually disciplined
(zero-leakage stdlib parsers, deterministic-fallback-everywhere, benchmark-gated
reviewer loop). There is **no rewrite to do.** AI_NATIVE_PLAN Phases 0–4 and
Go-Live Sprint 6 (Supabase auth) are done.

The gap between "very good tool" and "best report-documentation tool on the
internet" is **not** architectural. It is three concrete things: (1) the output
quality has **never actually been measured on a live AI run**, (2) the parser
**silently skips several common enterprise model features**, so docs are
incomplete on real models, and (3) the **differentiating surfaces** (interactive
Q&A, version-diff docs, publish-to-where-docs-live) and the **commercial layer**
(billing/admin/launch) aren't built yet.

---

## Track A — Prove the output is excellent (the #1 gap; do first)

Every quality claim in the repo rests on **deterministic/offline mode + unit
tests**. Per the benchmark's own Day 38 log: *"No live AI-provider
verification… reads more template-y than a live run would… full pillar-by-pillar
manual scoring worksheet not re-run."* You cannot credibly call it "the best
output" until a real Claude/Gemini bundle has been scored end-to-end. This track
is cheap and unblocks the marketing claim.

- **A1 — First live end-to-end run.** Configure a real provider key, run
  `pbicompass generate <fixture> --document all --bundle` with a live provider on
  the Corporate Spend fixture. Read all four docs by hand.
- **A2 — First live benchmark score.** Run the executable scorer
  (`agents/benchmark.py`) against the *live* bundle, record the first real score
  in `PBICOMPASS_OUTPUT_BENCHMARK.md`, and drive the reviewer loop
  (`agents/reviewer.py`) until it stops improving. Fix what it can't.
- **A3 — Build a real test corpus.** Today there are effectively two fixtures
  (Corporate Spend, SampleSales). Add 5–8 genuinely diverse real models —
  a clean star schema, a messy >12-table galaxy, a DirectQuery/composite model,
  a heavy-RLS model, a calc-group-driven model — and score across all of them.
  One report is not a quality signal.

## Track B — Document the *whole* model (coverage completeness)

Confirmed by reading `schemas/model.py` + `parsers/tmdl.py`: several first-class
Power BI features are **detected-but-dropped or not parsed at all**. On a real
enterprise model this makes the "complete documentation" claim false.

- **B1 — Calculation groups.** `tmdl.py:220` sets `kind="calculation-group"` but
  **never parses the calculation items** (their DAX, ordinal, format-string
  definition). Calc groups are the backbone of enterprise time-intelligence.
  Add a `CalculationItem` schema, parse it, render it in the technical doc, and
  add an audit rule for precedence/format-string coverage.
- **B2 — Hierarchies.** Not parsed at all. Add `Hierarchy`/`Level` to the schema,
  parse `hierarchy`/`level` in TMDL/TMSL, render drill paths in the technical doc
  and user guide.
- **B3 — Measure KPIs & goals.** The `kpi` keyword is in the ignore list. Extract
  target/status/trend expressions and document them.
- **B4 — Incremental-refresh policy & refresh metadata.** `refreshPolicy` isn't
  captured. Parse it and document the actual refresh strategy in the technical
  doc's refresh section instead of only the human-supplied note.
- **B5 — Field parameters (first-class), perspectives, translations/cultures.**
  Field parameters are only heuristically detected in `report_facts`; promote to
  schema. Perspectives and translations aren't captured — document multi-language
  and role-tailored views for enterprise/global models.
- **B6 — Dynamic format strings.** `formatStringDefinition` is a known keyword but
  not captured for measures/calc items. Capture and surface it.

## Track C — Differentiators that make it "the best"

- **C1 — "Ask about this report" (AI_NATIVE_PLAN Phase 5).** Not built
  (`assist.py` is a *form-fill* helper, not job Q&A). Grounded interactive Q&A
  over a finished job — service `POST /api/jobs/{id}/ask` + CLI `ask`, reusing the
  Phase-2 digest, plan-gated, zero-retention. The one genuinely new AI surface and
  the clearest "wow" differentiator.
- **C2 — Version-diff documentation.** The killer enterprise feature nobody nails:
  ingest two `model.json` versions → a "What Changed" document (added/removed/
  modified measures, relationships, RLS, pages, DAX) with impact analysis. Turns a
  one-shot tool into a change-management system of record.
- **C3 — Publish to where docs live.** Meet teams where they are: push generated
  Markdown/HTML to Confluence, SharePoint, a Git repo, or Teams. Documentation
  nobody can find isn't documentation.
- **C4 — Live cross-provider verification.** Memory flags that reasoning/effort
  must work on Gemini/Cohere/OpenAI, not just Anthropic, with a
  retry-without-reasoning fallback. This has native knobs wired but is unverified
  on a live call per provider — verify each on a real request.

## Track D — Make it a product people can actually use

- **D1 — Stripe billing** (Go-Live Sprint 7 — not started). No monetization path
  exists; `accounts.plan`/`set_plan`/`PLAN_LIMITS` is the seam.
- **D2 — Full admin app** (Sprint 8 — not started).
- **D3 — Launch hardening + real deploy smoke** (Sprint 9). Closes the recurring
  "no live smoke test against Supabase/Stripe" gap that appears in nearly every
  day's log.
- **D4 — RTM semantic candidate-widening** (known C4 partial: 2/7 rows can't reach
  their adjudicated tier because the deterministic candidate list offers no
  measure at all). Close it safely — the blunt currency-vocabulary bridge was
  correctly reverted; needs real semantic widening.
- **D5 — Customer-scale diagram/layout test.** `grandalf` layout verified only on
  a synthetic 16-table model, never on real messy topology.

---

## Recommended sequence

1. **Track A first** — you cannot claim "best output" without one live scored run.
   Cheap, and it will surface real prose defects the deterministic path hides.
2. **Track B in parallel** — completeness gaps are concrete, well-scoped, and each
   lands independently. Prioritize B1 (calc groups) — highest real-world impact.
3. **C1 (Q&A) + C2 (version diff)** — the two features that move it from "great
   generator" to "category-best." Do after A proves the base quality.
4. **Track D** — billing/admin/launch when you're ready to charge; D3's live smoke
   test is owed regardless.

Standing constraints apply to every task (from CLAUDE.md / memory): zero data
leakage, zero retention, graceful degradation, every feature lands in **all**
renderers (md/html/docx→pdf) **and** both entry points (`cli.py` +
`service/worker.py`), keep the `STYLE_RULES` editorial bar, extend
`FakeLLMClient` with a distinct keyword per new prompt.

---

## Progress log

### 2026-07-16 — Track A started (first-ever live run) + two fixes

**First live bundle ever generated** via `--provider meshapi --model
openai/gpt-5.5 --effort high` over the Corporate Spend fixture, driving the real
CLI `generate --document all --bundle` orchestration (Claude ids can't be used
through MeshAPI — Bedrock rejects the structured-output param the agents need).

- **Graceful degradation proven live for the first time.** MeshAPI credits hit
  **0 mid-run** (`402 spend_limit_exceeded`); the job still completed (`rc=0`),
  produced all four docs in every format, and scored **58/59**. The
  "an LLM failure never fails a job" guarantee held under a real provider outage
  — previously only mocked. The grounding/trust layer also fired live on real AI
  prose before credits died (corrected 1 contradicted + softened ~16
  unverifiable claims).
- **Caveat:** that 58/59 is *half-live, half-deterministic* (back half fell back
  to deterministic once 402s began), so it is **not** a clean live quality
  score. A fully-live scored bundle is still owed and is **blocked on MeshAPI
  credit top-up.**

**Fix 1 — transient-error retry/backoff (`agents/llm.py`).** The provider clients
only retried `BadRequestError` (400) for reasoning/schema degradation; any
transient `APIStatusError` (429 rate-limit / 5xx / network) propagated straight
to deterministic fallback. Any real rate-limited provider would half-degrade a
multi-doc bundle exactly like the 402 cascade did. Added a shared
`_call_with_retries` helper (bounded exponential backoff + jitter, 2 retries
default, `PBICOMPASS_LLM_MAX_RETRIES` override) wrapping all four SDK call sites;
retries 408/409/425/429/5xx/network, never 400/401/**402**/403/404 (those don't
self-clear — fail fast to the deterministic fallback). Composes with the existing
per-provider BadRequest degradation ladders. New `tests/test_llm_retry.py`
(21 tests) + end-to-end verification through a real `MeshAPIClient`.

**Fix 2 — C13 benchmark false-positive (`agents/benchmark.py`).** The lone
unresolved check (C13, "measure business logic explains why") was a **scorer
bug**, not a content defect: the live "Var Plan %" measure explained its purpose
richly ("…indicator for **comparing** cost centers … when selecting …
**Using** Plan as the denominator…") but the rationale regex used full-word
stems `compare`/`use`, which don't match the inflections "comparing"/"using".
Converted to true morphological stems (`compar`, `us(?:e|ing)`, `estimat`,
`exclud`, + several interpretive verbs); strictly a superset, so no
previously-passing measure can regress. New regression tests in
`tests/test_benchmark.py` (incl. a guard that C13 still bites on mechanics-only
prose).

**Still owed for Track A:** a clean fully-live scored bundle (needs credits);
A3's diverse fixture corpus.

### 2026-07-16 — Track B1 shipped (calc groups + hierarchies)

The biggest completeness gap closed end-to-end. Calculation-group items and
user-defined hierarchies were previously dropped (the TMDL parser only tagged a
calc-group table's `kind`; hierarchies weren't parsed at all), so docs were
silently incomplete on enterprise time-intelligence models.

- **Schema** (`schemas/model.py`): new `CalculationItem`, `Hierarchy`,
  `HierarchyLevel`; wired onto `Table` (`hierarchies`, `calculation_items`,
  `calculation_group_precedence`); `from_dict` round-trip + `compute_counts`
  (`hierarchies`, `calculation_items` counts) updated.
- **Parsers**: TMDL (`parsers/tmdl.py`) parses `calculationGroup` →
  `calculationItem` (multi-line DAX, `ordinal`, `formatStringDefinition`,
  `precedence`) and `hierarchy` → `level`→column; TMSL (`parsers/tmsl.py`) parses
  the JSON equivalents (ordinal-sorted levels). pbixray exposes neither, so the
  legacy `.pbix` path degrades gracefully (documented, like RLS).
- **Renderers** (all three): technical doc §6 Data Model now shows a
  **Hierarchies** list (drill path) and **Calculation groups** tables (item DAX +
  dynamic format) in md/html/docx. The three stale audit-doc caveats ("not yet
  parsed by PBICompass") were corrected.
- **AI digest** (`agents/insights.py`): calc groups + hierarchies are now in the
  whole-model digest, so Report Intelligence / Data Modeler reason over them.
- **Tests**: parser (TMDL+TMSL), schema round-trip, counts, and renderer-presence
  (md/html/docx) tests added; 2 audit goldens regenerated (caveat wording only —
  diff verified one line).

### 2026-07-16 — Track B3 + B4 shipped (measure KPIs + incremental-refresh policy)

Same end-to-end pattern as B1.

- **Schema**: `MeasureKPI` on `Measure` (target/status/trend/graphic/format);
  `RefreshPolicy` on `Table` (policy type, rolling + incremental window
  granularity/periods, source/polling expressions); `from_dict` round-trip.
- **Parsers**: TMDL parses the measure `kpi` sub-block and table `refreshPolicy`
  (new `_extract_expr_prop` helper reused for both); TMSL parses the JSON forms.
- **Renderers**: KPI targets table in technical §7, extracted incremental-refresh
  policies in §11, across md/html/docx. Shared `refresh_policy_summary()` helper
  in `render/_shared.py` gives one plain-language wording everywhere ("basic
  policy — stores the last 3 months; refreshes the last 10 days incrementally").
- **AI digest**: `Measure KPIs` + `Refresh Policies` sections added to the
  whole-model digest.
- **Tests**: parser (TMDL+TMSL), round-trip, and renderer-presence (all 3
  formats) tests added. No golden changes (fixtures carry neither feature; the
  sections are conditional).

### 2026-07-16 — Track B5 + B6 shipped — **Track B COMPLETE**

Final completeness features, same end-to-end pattern.

- **B6 measure dynamic format strings**: `Measure.format_string_expression`
  (`formatStringDefinition`); TMDL+TMSL; "Dynamic format strings" table in §7
  across md/html/docx; digest.
- **B5a field parameters (first-class)**: new `FieldParameter` on `SemanticModel`,
  populated in `parsers/pbip.py::_assemble` (the single choke point for every
  source format) via `report_facts.extract_field_parameters`, which pulls the
  `("Label", NAMEOF(ref), n)` rows out of the field-parameter table's own DAX.
  Rendered as a "Field parameters" list in §6.
- **B5b perspectives**: `Perspective` on `SemanticModel` (tables/measures a view
  exposes); TMDL (`perspective`→`perspectiveTable`/`perspectiveMeasure`) + TMSL;
  §6 render; digest.
- **B5c cultures/translations**: `Culture` on `SemanticModel` (language +
  translated-caption count); TMDL (`culture`) + TMSL (recursive
  `translatedCaption` count); §6 render; digest.
- Round-trip (`from_dict`), `compute_counts`, and full parser→render→digest tests
  added. No golden changes (fixtures carry none of these; all sections conditional).

**Track B is done.** All completeness gaps identified in the original audit are
closed: calc groups, hierarchies, KPIs, refresh policy, field parameters,
perspectives, translations, dynamic format strings.

### 2026-07-16 — Track C2 shipped (version-diff with impact analysis)

Turned the thin `pbicompass diff` into a real change-management surface.

- **New `agents/model_diff.py`** — comprehensive diff over tables/columns
  (incl. type changes), measures (logic + format), relationships (**modified
  properties**, not just add/remove), **RLS roles + filter changes**
  (security-flagged), pages, and every Track-B feature (calc items, KPIs,
  refresh policy, hierarchies, perspectives, cultures). Every change is
  classified with a **severity** (Critical→Info) and a plain-language **impact
  note** driven by a page/visual **usage index**: a removed measure a visual
  still binds to is Critical ("Referenced by visuals on N pages…"); a changed
  cross-filter is High ("can silently alter every number that crosses this
  join"); an RLS filter change is High + security-flagged.
- **`enrichment.py`** now delegates `compute_model_diff`/
  `generate_change_log_markdown` to the new engine (back-compat keys preserved),
  so the changelog already embedded in the technical & audit docs is
  automatically far richer.
- **`render/html.py::_render_md`** upgraded to render bullet lists + inline
  `code` (was paragraphs only) so the embedded change log looks right; plain
  prose (executive core purpose) renders identically.
- **CLI**: `pbicompass diff old.json new.json` now emits a severity-grouped,
  impact-annotated change log; `--format html` writes a self-contained styled
  "What Changed" page (no external assets).
- Tests: new `tests/test_model_diff.py` (coverage + impact + severity + both
  renderers); existing enrichment/CLI diff tests updated for the richer output.

### 2026-07-16 — Track C3 shipped (publish to where docs live)

New stdlib-only `src/pbicompass/publish/` package (no new dependency) plus a
`pbicompass publish <target> <path>` command. Documentation nobody can find
isn't documentation; this puts it where the team already works.

| Target | Behaviour | Fidelity |
|---|---|---|
| `filesystem` | Copies into a directory; `--git` stages+commits, `--git-push` pushes | **Verbatim** — HTML/DOCX/PDF/diagrams intact |
| `sharepoint` | Uploads to a Graph drive folder | **Verbatim** |
| `confluence` | Page per document, created **or updated in place** (idempotent — never duplicates) | HTML→storage format; text/tables/code carry, diagrams don't |
| `teams` | Incoming-webhook notification card | Notice only — **document content never enters a chat** |

Decisions worth keeping:
- **Nothing publishes without an explicit command + that destination's own
  credentials** (env `PBICOMPASS_*` preferred over flags, which leak into shell
  history). `--dry-run` shows exactly what would be sent and sends nothing.
- **SharePoint takes an already-issued Graph token** rather than embedding an
  OAuth flow — token acquisition is tenant-specific and baking one flow in would
  be wrong for most orgs. An honest boundary, documented in the module.
- Fidelity differs by destination **by design**, and is stated rather than
  papered over: Confluence's storage format cannot carry our CSS/JS diagrams.
- Graph's 4 MB simple-upload limit is **flagged, never silently truncated**.
- `tests/test_publish.py` (32 tests): every network target runs against a
  stubbed `http_request` — **no real request leaves the suite**; the filesystem
  target runs for real, including a real `git init` repo. Teams has an explicit
  test that document *body text* is never in the payload.
- New env vars documented in `.env.example`.

### 2026-07-16 — A3 (partial): validated against REAL TMDL — found a real bug

The audit's own warning ("synthetic fixtures encode our own assumptions") proved
correct. Validated the Track-B parsers against **4 real Power BI exports** on the
owner's machine (HR Sample, Corporate Spend, Zomato, RCL demo — parsed locally,
counts only, no content read into any transcript).

**Bug found and fixed — `cultureInfo`.** Real TMDL declares a culture as
`cultureInfo <name>` in `definition/cultures/*.tmdl`; the parser dispatched on
`culture`, so **cultures parsed as 0 on every real model** while the synthetic
tests stayed green (they encoded the same wrong keyword). Fixed, plus a guard:
the model-level `culture: en-US` *property* must not be parsed as a culture
declaration (it would invent a phantom culture on every model).

**Quality call:** Power BI writes a default `en-US` cultureInfo with zero
translations into *every* model. Documenting "Translations / languages: en-US
(0 translated)" on a single-language report is noise, so the generator only
surfaces cultures when there is >1 culture or ≥1 real translated caption. The
parsed model still keeps them all — presentation decision, not a parse one.

**False alarm worth recording:** a raw grep of a whole `.SemanticModel` folder
over-counts, because scratch folders like `TMDLScripts/` contain `.tmdl` files
that are *not* part of the model definition. Ground truth must scan
`definition/` only — which is exactly what the parser does.

**Result after the fix — strict source-vs-parsed comparison, 0 mismatches,
0 parse warnings:** calculationItem 5→5, hierarchy 7→7 / 8→8 / 2→2,
cultureInfo 1→1 (×4), kpi 1→1.

New `scripts/validate_real_models.py` makes this repeatable against any model
(exit code = mismatch count, so it can gate CI once a real corpus exists).

**Still unvalidated against real files:** `refreshPolicy`, `perspective`, and
measure-level `formatStringDefinition` — **none of the 4 real models contain
them**, so those three parsers remain synthetic-only and should be treated as
unproven until a model that uses them is available.

### 2026-07-16 — **TRACK A CLOSED: first clean fully-live scored bundle**

`--provider meshapi --model deepseek/deepseek-v4-flash --effort high --document all`
over the Corporate Spend fixture, through the real CLI. **rc=0 in 822s, zero API
errors, zero deterministic fallbacks — every agent and all four documents ran
live.** Model chosen for cost: v4-flash is reasoning-capable per
`_meshapi_reasoning_capable` and ~$0.14/$0.28 per Mtok (vs gpt-5's $1.25/$10.00).

**Score: 59/61** (benchmark v3.0, 0 fix cycles) — the one unresolved check, **C8,
is a verified false negative**: the judge claimed "no refresh schedule documented
in any bundle document" while technical.md §11 plainly reads *"Refresh schedule:
Daily 06:00 UTC via on-premises gateway"* (present 2× in technical, 1× in
executive). So the true structural result on this bundle is effectively **61/61**.

**The trust layer works on live prose — proven, not assumed.** Grounding fired
across all four documents (corrected contradicted claims, softened unverifiable
ones), and the cross-artifact consistency pass caught the technical doc calling
the model a *"star schema"* when the audit said otherwise → auto-corrected to
*"multi-fact (galaxy) schema"* in three separate places across two documents.

**Real defects found by *reading* the prose (what the score cannot catch):**
1. **Self-contradicting risk (genuine defect).** The exec doc says *"Since
   row-level security is not configured, there is no risk of role misalignment…"*
   then asks *"Review RLS role memberships quarterly"* — reviewing memberships of
   roles that do not exist. The AI reframed the consequence for a no-RLS model but
   kept the deterministic ask written for a model *with* roles.
2. **"based on the same 30% modeled estimate"** appears in two KPI bullets with no
   antecedent for "the same" — reads as confusing/unmoored.
3. **Silent, flattering RTM failure (UX trap, not strictly a bug).**
   `traceability.parse_requirements` documents one-requirement-per-**line**; seven
   semicolon-separated requirements on one line collapse to a single row and
   render as *"Requirements coverage: 1/1"* — which looks perfect. Worth splitting
   on `;` defensively, or warning.

**Scorer reliability caveat:** C8 is a `judge`-method check, and the judge is the
configured model. On a cheap model the judge **hallucinates failures**. Judge-method
checks should be read as advisory; deterministic checks are the trustworthy ones.

### 2026-07-16 — "Make the tool 10/10": gate sense, spec-checked parsers, .pbix honesty, and a broken default

**The default engine could not pass this tool's own output gate.** Measured on the
Corporate Spend fixture, full 4-doc bundle:

| Engine | Runs | Result | ~cost |
|---|---|---|---|
| `inclusionai/ling-2.6-flash` (**was the default**) | **2/2 BLOCKED** on T4 (user-guide prose contradicting the audit, same locations both times) → `rc=1`, **zero documents** | fail | ~$0.005 |
| `deepseek/deepseek-v4-flash` (**now the default**) | 3/3 pass, **59/61** | pass | ~$0.06 |

12× the cost of a default that produces nothing is not a trade-off worth having,
so the MeshAPI default is now `deepseek/deepseek-v4-flash` — which is also
reasoning-capable, so the effort machinery actually runs (ling is not, meaning
"max reasoning by default" was silently unreachable on the shipped default).
`MESHAPI_MODEL` remains the per-deploy override. Read positively: **the gate
works** — it caught real contradictions and refused to ship them.

**#1 Prose-coherence gate (SENSE).** The gate scored 59/61 while shipping a risk
whose ask ("review RLS role memberships quarterly") contradicted its own
consequence ("row-level security is not configured"). Structure passed; nothing
read for *sense*. New check flags an ask that applies a maintenance verb to
something its consequence declares absent. Proven against both real live strings:
fires on the broken one, silent on the fixed one. Narrow by design — needs a
definite absence claim, and treats create-verbs / verify / confirm as correct.

**#2 The three unproven parsers, checked against Microsoft's TMDL spec** instead
of fixtures encoding our own assumptions (the circularity that hid `cultureInfo`).
Spec **confirms** `perspective → perspectiveTable → perspectiveMeasure` and
`FormatStringDefinition` as an `=`-assigned DAX default property — both already
correct. `refreshPolicy`'s TMDL declaration is **not** pinned down by the spec and
no real export on hand has one, so both plausible shapes now parse rather than
betting on a guess; its property names are confirmed via the TMSL form.

**#4 The `.pbix` honesty bug — worse than a limitation.** pbixray cannot expose
RLS, yet the audit fired on `not model.roles` regardless of format, so every
`.pbix` report was told *"No row-level security roles are defined in this model"* —
a **false statement of fact** about a report that may be fully protected. Absence
and unreadability are now distinct (new `PBIC-GOV-012`, Low: "RLS status is
unknown — export as .pbip"). The technical doc's constraint and the human-claim
discrepancy check are fixed the same way (the latter would otherwise raise a false
security contradiction against an owner's accurate note). Note prod is fine on
Python 3.12 per the Dockerfile; pbixray only fails to install on 3.14.

**#3 partially blocked, honestly.** Live validation widened to **2 models** (above).
Widening to *multiple real reports* would send the owner's own business files'
content to MeshAPI — outside the local-only consent under which those files were
examined. Not done without explicit consent for that different action.

**Still true:** `refreshPolicy`/`perspective` remain unseen in any real file;
live scoring is still one report; C8's judge false-negatives on a cheap judge.
