# PBICompass: AI-Native Output Plan

## Context — current scenario (verified by reading the source)

The product pitches "AI-powered documentation", but today the AI is a **text-polish layer, not the reasoning core**:

1. **Everything is computed deterministically first; the LLM only restates it.** `agents/deterministic.py` + `agents/report_facts.py` build every fact and draft; `agents/audit_rules.py` (1,763 lines of regex/heuristics) produces all findings, scores, and recommendations with zero LLM involvement. The 8 prompts in `agents/io.py` (Business Analyst, DAX Translator, Data Modeler, Column Describer, Audit Narrator, Executive Writer, User Guide Writer) + `agents/critic.py` each receive a narrow, pre-digested JSON slice and fill prose fields. The Executive Writer's own prompt literally says "you are given deterministic drafts... and you compress them."
2. **No shared context, no synthesis.** Each call is stateless single-shot via `generators/base.py::call_llm`. No agent sees another agent's output or the whole model at once — so no document can say anything that crosses section boundaries ("these 3 pages form a weekly sales workflow", "this model's real subject is restaurant franchising").
3. **No real grounding/verification.** The only hallucination check is a regex (`critic.py::_unknown_bracketed_names`). The critic's LLM pass is style-only.
4. **Wasted spend + unused signal.** In a multi-doc job the DAX Translator runs 3× redundantly (`technical.py::_measure_catalog`, `executive.py::_key_kpis`, `user_guide.py::_build_glossary`); the SQLite `LLMResponseCache` is **off by default in the hosted service**. Uniform `effort="high"` for every call regardless of task. `Column.cardinality`/`size_bytes` (VertiPaq stats) are parsed but never read by any agent or audit rule.
5. **No interactive AI surface.** Strictly upload → download.
6. **User-reported defect:** the Executive Summary doc reads like it's for a technical manager. Root cause: its Top Risks copy `audit_rules.build_recommendations()` text nearly verbatim (only literal DAX words stripped via `_business_safe_ask`); "What's Next" injects a documentation-completeness % nag; `maintenance_note` says "X best-practice gap(s) and Y governance finding(s)" (audit-speak); Ownership renders "Steward"/"Classification: not specified" governance rows nobody asked for. **Decision: audience stays managers/sponsors; fix is editorial.**

**Standing constraints (all phases):** zero data leakage (agents only ever see the metadata-only `SemanticModel`); zero retention in the service (nothing persists beyond job TTL / sandbox shred); graceful degradation (every LLM feature has a deterministic fallback; LLM failure never fails a job); freemium cost-consciousness (effort/model tiered by plan, BYOK); keep the `STYLE_RULES` editorial bar; **every feature must land in all renderers (md/html/docx→pdf) and both entry points (`cli.py` + `service/worker.py`)** — this repo's recurring failure mode. `tests/test_agents.py::FakeLLMClient` routes on distinctive system-prompt substrings — keep a unique phrase per prompt when editing, and extend the fake for every new schema.

---

## Phase 0 — AI plumbing: one shared context per job (cheap, enables everything)

**New module `agents/context.py`** with a `JobAIContext` dataclass:
```
JobAIContext:
  translations: dict[str, dict] | None   # measure name -> DAX Translator result
  insights: dict | None                  # Phase 2's report-intelligence output (None until then)
  usage: dict                            # content-free call/token counters per agent
```
- `build_job_context(model, client, warn) -> JobAIContext` runs the DAX Translator batches **once** and stores results.
- Thread it through: `service/worker.py::process_job` and `cli.py` build it once per job before the doc-type loop and pass it to every generator via a new optional kwarg `ai_context=None` on `generate(...)` (backward compatible: `None` → generator builds its own, preserving direct-import callers and existing tests).
- `technical.py::_measure_catalog`, `executive.py::_key_kpis`, `user_guide.py::_build_glossary` consume `ai_context.translations` instead of re-calling. **Kills the 3× redundant spend.**

**Service cache fix:** in `worker.py`, point `PBICOMPASS_LLM_CACHE` at a file **inside the job sandbox** (e.g. `sandbox.path("llm_cache.db")`) for the duration of the job — dedupes retries within the job, shredded in the existing `finally`, zero-retention preserved. CLI keeps its persistent default.

**Per-call effort tiers:** extend the `LLMClient` protocol to `complete_json(system, user, schema, *, effort=None)` (None → client default). `AnthropicClient` maps it to `output_config.effort`; Gemini/Cohere ignore it. Add an `AGENT_EFFORT` map in `io.py` (e.g. Column Describer/Critic → `low`, DAX Translator/User Guide → `medium`, Data Modeler/Executive Writer → `high`, Phase-2 insights → `xhigh`). Plan-based ceiling in the service: free plan clamps to `medium` + cheap model default; pro/enterprise get `high/xhigh` (plans already exist in `service/accounts.py`).

**Telemetry:** count calls + input/output tokens per agent (Anthropic responses carry `usage`) into `JobAIContext.usage`; log content-free totals per job and include in the job's `warnings`-style status payload. No content ever logged.

*Cost:* strictly saves money (dedupe + lower effort where deep reasoning isn't needed). *Verify:* existing suite green; new tests in `tests/test_agents.py` asserting the DAX Translator is invoked once per job for `--document all` (count FakeLLMClient calls); service test asserting cache file lives under the sandbox and is gone after the job.

---

## Phase 1 — Executive Summary editorial fix (agreed with user; independent, ship first or parallel)

Audience unchanged (managers/executives/project owners). Files: `agents/generators/executive.py`, `agents/io.py`, `render/executive.py` (md + html + docx; pdf comes from md), `schemas/executive_document.py`, tests.

1. **Risk reframing becomes part of the Executive Writer call** (cost-neutral: same single call). Extend `EXECUTIVE_WRITER_SCHEMA` with `reframed_risks: [{severity, consequence, ask}]`; the input already carries `known_risks` — pass the full recommendation objects (issue/why/fix/priority/rule_id) instead of preformatted strings. Prompt instructs: rewrite each risk as *business consequence* ("numbers on this dashboard will silently go stale at year-end") + *a plain ask naming who does what* ("ask the BI team to make the year dynamic — small change"). Never IT-governance vocabulary: ban "best practice", "governance finding", "bi-directional", "cross-filter", "semantic model" in this doc via a new `EXEC_STYLE_RULES` appended to the prompt. Deterministic fallback: today's `_top_risks`/`_business_safe_ask` path unchanged. Keep `rule_id` on each reframed risk so the audit deep-link (`_risk_href`) still works.
2. **`maintenance_note` rephrasing:** deterministic draft in `_maintenance_note` rewritten to plain language ("Nothing needs urgent attention" / "N items need a developer's attention — mostly X; the audit report has the fix list"), and the writer prompt told to phrase upkeep as "what could break and who fixes it", never counts of "gaps/findings".
3. **Drop the completeness nag from the doc:** remove the `compute_completeness` block from `_next_steps`; emit it as a job **warning** instead (already rendered by CLI stderr + service warnings list). "What's Next" = top unaddressed remediation (business-phrased) + up to 2 concrete business asks (e.g. "confirm the report owner"), nothing about "fields in this document".
4. **Ownership section:** render `Steward`/`Classification` rows **only when set** (all 3 renderers); `Owner` always shown ("not specified" for a missing owner is genuinely useful).

*Verify:* update `tests/test_generators.py` + `tests/test_render.py` goldens; FakeLLMClient's executive branch returns the new `reframed_risks` field; manual read-through of all 4 formats for one sample report; grep the rendered exec doc for the banned vocabulary as a unit test.

---

## Phase 2 — Report Intelligence pass: give the AI the whole model (the core architectural change)

One **synthesis call per job** that finally lets the LLM *reason about the report as a whole*, whose output becomes shared context for every downstream prompt.

- **New `agents/insights.py`:**
  - `build_model_digest(model, audit_summary, char_budget)` — a compact whole-model digest: every table with column names/types (capped per table), every measure with truncated DAX, relationships as lines, pages with visual→field bindings, RLS roles, data-source types, audit finding counts, and cardinality/size stats when present. Deterministic, reuses `report_facts.py` helpers. Budgeted (~30–60k chars) so it fits comfortably.
  - `REPORT_INTELLIGENCE_SYSTEM` + schema producing `ModelInsights`: `business_domain`, `report_purpose` (inferred, with confidence), `audience_hypotheses`, `entity_definitions` (what "Customer", "Sale_Value" actually mean here), `page_workflows` (how pages chain into real tasks), `kpi_relationships` (which measures explain which), `cross_cutting_observations`, `data_quality_notes`, each with a confidence level and grounded in named objects only. Runs at `xhigh` effort — this is *the* reasoning call.
- Stored on `JobAIContext.insights` by `build_job_context` (Phase 0 hook point). Failure/offline → `None` → everything behaves exactly as today (graceful degradation).
- **Downstream prompts get context:** each `io.py` input builder adds `payload["report_context"]` (a slimmed insights view) and each system prompt gains one paragraph: "You are also given report_context — a synthesized understanding of the whole report. Use it for consistency and depth; never contradict the concrete metadata; never copy it verbatim." Business Analyst can now write page summaries that reference the actual workflow; DAX Translator explains measures using the report's entity definitions; Data Modeler ties risks to the inferred domain; Executive/User-Guide writers stop sounding templated. With richer context, several downstream calls can drop from `high` to `medium` effort (Phase 0 map), roughly offsetting the insights call's cost.
- Surface the insights themselves where they earn it: `core_purpose` and the user guide introduction consume `report_purpose`; add an optional "How this report thinks" short section to the technical doc only if trivially rendered from existing fields — otherwise defer rendering new sections to keep this phase about *quality of existing sections*.

*Cost:* +1 xhigh call/job, minus effort reductions elsewhere ≈ modest net increase; biggest quality jump per token in the plan. *Verify:* new FakeLLMClient branch with a distinctive "report_context" keyword; unit tests that downstream payloads embed insights when present and omit cleanly when `None`; A/B manual comparison of generated docs for the sample `.pbip` with/without insights.

---

## Phase 3 — Grounding & verification pass (the trust layer)

- **New `agents/grounding.py`:** after generation (post-critic), one call per document at `medium` effort: input = the doc's narrative fields (same labelled-triple mechanism `critic.py` already uses) + the Phase-2 model digest; output schema = `{claims: [{location, quote, verdict: supported|contradicted|unverifiable, correction}]}`. Apply `correction` only for `contradicted`; rewrite `unverifiable` factual claims to the established "Unknown — requires business confirmation" convention; leave `supported` untouched.
- Wire into all 4 generators next to `_run_critic` (same triple-collection pattern — reuse it, don't duplicate). Offline/failed → skip silently; the regex bracket-name check remains as the deterministic floor.
- Propagate the existing provenance convention (`AI-inferred` / `Extracted` / `Human-provided`) consistently: audit that every renderer that shows AI-written prose can show its confidence/provenance where the schema already carries it (measures and columns already do; pages carry `confidence` — make sure all 3 renderers show it, per the recurring-failure checklist).

*Cost:* +1 medium call per doc type (≤4/job); worth it — this is the "trustworthy documentation" differentiator. Gate off on free plan if needed. *Verify:* unit tests feeding a fake grounding response with one contradicted + one unverifiable claim and asserting the doc text is corrected/downgraded; goldens updated.

---

## Phase 4 — AI-augmented audit: judgment on top of the deterministic backbone

`audit_rules.py` detection stays 100% deterministic (project convention — never LLM-guess a finding). AI adds the layer regex can't do:

1. **Audit Synthesizer agent** (`io.py` prompt + call in `generators/audit.py`): input = full findings list (ids/kinds/severities/details), health components, complexity, top recommendations, cardinality outliers; output = `{clusters: [{title, root_cause, related_finding_ids, business_impact, remediation_order}], strategic_narrative}`. E.g. "7 findings (PBIC-DAX-003 ×4, PBIC-BP-002, …) share one root cause: no dedicated date dimension — fix that first and 5 findings disappear." New fields on `schemas/audit_document.py`, rendered as a "Root-Cause Analysis" section in `render/audit.py` (md/html/docx) with deep-links to the finding anchors that already exist; the technical doc's §16 shows the top cluster. Deterministic fallback: section omitted (today's behavior).
2. **Use the dead VertiPaq signal deterministically:** new threshold rules in `audit_rules.py` reading `Column.cardinality`/`size_bytes` when present (slicer bound to a high-cardinality column; high-cardinality key on a bi-directional relationship; wide text columns dominating model size; near-constant columns used as dimensions). No-op when stats are absent (`--stats` is opt-in, pbix-only). Same stats go into the Phase-2 digest.
3. **AI-suggested fix snippets** for the top N recommendations (DAX rewrite sketches), clearly labelled "AI-suggested — review before applying", appended to `suggested_fix` as fenced blocks (the critic already skips fenced code). Paid-plan feature.

*Cost:* +1 high call for the synthesizer (+1 optional for fix snippets) only when the audit doc is requested. *Verify:* `tests/test_audit_rules.py` for the new deterministic rules (with and without stats); FakeLLMClient synthesizer branch; renderer tests asserting the cluster section appears in **all three** formats and cross-links resolve.

---

## Phase 5 (separate tier — new AI-native capability, gated & optional) — "Ask about this report"

Interactive Q&A over a finished job — the genuinely new product surface, deliberately last because it's the biggest scope/cost item for a freemium product.

- **Service:** `POST /api/jobs/{job_id}/ask` `{question, history?}` while the job is within TTL. Grounding source: `model.json` retained in the job's output store (it already is for multi-doc jobs; extend to always store it internally without exposing a new download for single-doc API back-compat). Answer = one LLM call: Phase-2 digest (+ insights) + question + client-supplied capped history; response includes citations (doc section anchors). **Zero retention preserved:** no server-side chat history, nothing outlives the existing job TTL, logs stay content-free. Plan-gated (pro+), per-day question quota via the existing accounts/usage machinery.
- **UI:** a chat box on the job status/result page (`service/static/index.html`).
- **CLI parity:** `pbicompass ask <model.json> "question"` for local/BYOK users (no retention concerns locally).

*Cost:* per-question spend, quota-controlled; meaningful new revenue story ("talk to your report"). *Verify:* service tests with FakeLLMClient (auth, plan gating, TTL expiry → 404, content-free logging); manual end-to-end.

---

## Sequencing & where to stop

| Phase | What the user gets | Net LLM cost | Risk |
|---|---|---|---|
| 0 | Same docs, ~⅓ fewer calls in multi-doc jobs, tiered effort, spend visibility | **Saves** | Low |
| 1 | Exec Summary reads like it's for a business owner (their complaint) | ~Neutral | Low |
| 2 | Docs that synthesize the whole report — the "real AI" jump | Modest + | Medium |
| 3 | Verified, trustworthy claims; consistent confidence labels | +1 call/doc | Medium |
| 4 | Audit that explains *why* and *what first*, VertiPaq-aware rules | +1–2 calls (audit jobs) | Medium |
| 5 | Interactive Q&A — new product tier | Per-question | High (new surface) |

Phases 0+1 first (0 pays for everything after; 1 is the live complaint). 2 → 3 → 4 in order (3 and 4 both consume 2's digest). 5 only when 2–3 are proven.

## Global verification checklist (every phase)

- Full suite `python -m pytest` green; extend `tests/test_agents.py::FakeLLMClient` with a distinctive-keyword branch per new/changed prompt (keyword collisions silently misroute — check existing keywords first).
- The recurring-failure audit: every new schema field lands in **md + html + docx** renderers for every affected doc type, and every new module is wired into **both** `cli.py` and `service/worker.py`.
- Manual end-to-end per phase: `pbicompass generate <sample.pbip> --document all --provider gemini --bundle` (cheap real-LLM smoke) and one offline run (`--provider none`) proving deterministic fallback still produces complete docs.
- Zero-retention spot-check after service-touching phases: job sandbox gone post-job, no model content in logs, cache file shredded.
