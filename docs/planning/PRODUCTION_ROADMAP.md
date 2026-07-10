# PBICompass — Production Readiness Roadmap

_Prepared 2026-07-07. Owner: Rajdeep Rao. Scope: take the current build from "works end-to-end" to a launch-ready, enterprise-grade SaaS with consultant-quality output. Faithful to the existing vision — no new product directions._

This document is the single source of truth for the path to launch. It supersedes nothing: `IMPLEMENTATION_PLAN.md`, `DOCUMENTATION_QUALITY_PLAN.md`, and `AI_NATIVE_PLAN.md` remain the detailed specs; this roadmap sequences their **remaining** work plus the SaaS/enterprise/production layers they don't cover, into an executable day-by-day plan.

---

## 0. How to read this

- **Section 1** — honest current-state assessment with a score.
- **Sections 2–3** — audit findings and a gap matrix (what's actually missing).
- **Sections 4–8** — the five improvement roadmaps (AI, docs, UX, auth, enterprise).
- **Sections 9–13** — the operational checklists (hardening, testing, performance, deployment, launch).
- **Section 14** — the day-by-day execution plan (Jul 8 → Aug 28), the primary deliverable.

Findings are cited to real files/outputs so they're verifiable, not generic.

---

# 1. Current Project Assessment

## 1.1 Verdict

**The foundation is genuinely strong and the architecture is correct — keep it.** The parser, the deterministic backbone, the audit rule engine (50 checks, stable IDs), the renderer set (md/html/docx/pdf), and the design system are all at or near production quality. The product runs end-to-end today.

**Two things stand between this and a launch:**
1. **Output credibility** — a handful of visible LLM-leak defects in the generated docs undercut the "Big-4 handover" promise. They're small in code but large in perception. These are launch-blockers.
2. **SaaS/product surface** — there is no user login, no billing, no persistent server state, and no monitoring. The app is a self-hostable tool with API-key multi-tenancy, not yet a SaaS a stranger can sign up for and pay for.

## 1.2 Scorecard

| Dimension | Score | One-line assessment |
|---|---:|---|
| Architecture & code quality | 85 | Clean, layered, stdlib-core, 182 tests. Minor debt (render duplication). |
| Parsing & extraction | 82 | Covers ~95% of real exports; graceful degradation. Field-selector gap leaks `select`. |
| Deterministic engine & audit rules | 90 | 50 rules, stable IDs, fix snippets, suppression config. Best part of the product. |
| AI intelligence | 68 | Phases 0/2/3 done. Still more restatement than reasoning; Phases 1/4/5 pending. |
| Generated-doc content quality | 78 | Excellent backbone, dragged down by ~5 visible LLM-leak defects. |
| HTML/visual design | 84 | Professional system (indigo/slate, dark mode, a11y, scroll-spy, syntax highlighting). Wireframe/lineage hidden. |
| SaaS product surface | 38 | API-key + admin only. No signup/login, no billing, no account UI. |
| Production infrastructure | 42 | In-memory job state, SQLite on ephemeral disk, in-process worker, no monitoring. |
| Security & compliance | 70 | Strong zero-retention/zero-leakage design; missing audit logging, SSO, secrets hygiene. |
| Testing | 80 | Broad unit + golden coverage. No load/integration/e2e-browser tests. |
| **Overall (weighted to launch-readiness)** | **~66** | Strong tool; not yet a shippable paid SaaS. Closable in ~7 weeks. |

## 1.3 What's already done (do not re-do)

- **Documentation Quality Plan Phases 1–5** — quality floor, presentation/nav (hub, search, dark mode, syntax highlighting), new content (wireframe/lineage/dep-tree/RLS matrix), 50-rule audit engine, enrichment/critic/cache. Marked done and test-verified per Part J status (2026-07-06).
- **AI-Native Plan Phase 0** — shared `JobAIContext`, effort tiers, DAX-translator dedupe.
- **AI-Native Plan Phase 2** — Report Intelligence synthesis call (`agents/insights.py`).
- **AI-Native Plan Phase 3** — Grounding pass (`agents/grounding.py`). _Done, but see §2.2 — it isn't catching the leaks it should._

## 1.4 What's pending (this roadmap's scope)

- **AI-Native Phase 1** — Executive Summary editorial fix (never started; still visibly broken — §2.2).
- **AI-Native Phase 4** — AI audit synthesizer + VertiPaq rules + AI fix snippets.
- **AI-Native Phase 5** — "Ask about this report" interactive Q&A.
- **Wireframe + Lineage reintroduction** — code exists but is commented out (`render/html.py:344-345, 456-457`) with placeholder "WIP" text; needs the J.C-spec redesign before it goes back in.
- **Everything SaaS/production** — auth, billing, persistence, monitoring, enterprise. Not covered by either existing plan.

## 1.5 Output Quality Rating — now vs. after this plan (out of 100)

This rates the **generated documentation output specifically** (the four docs + hub a customer receives) — distinct from the whole-product launch-readiness score in §1.2.

### Current output: **72 / 100**

Genuinely strong bones, dragged down by a handful of visible defects. A knowledgeable reviewer would say "impressive depth, but I can't hand this to a client until these rough edges are gone."

| What lifts it | What drags it down |
|---|---|
| 19-section technical doc with a real DAX dictionary | **D1** exec summary reads like an audit log (audit-speak, completeness nag) |
| 50-rule audit engine, stable IDs, parameterized fix snippets | **D2** LLM meta-commentary shipped as glossary definitions |
| Professional HTML design system (dark mode, a11y, syntax highlighting) | **D3** grounding produces broken mid-sentence grammar |
| Provenance labels (`Extracted`/`AI-inferred`/`Human-provided`) | **D4** `select`/`select1` field-selectors leak into prose |
| Deterministic backbone → factually trustworthy | **D6** "requires business confirmation" fires on obvious keys (reads as the AI giving up) |
| | Wireframe + lineage hidden; AI still restating more than reasoning |

### Projected after full plan: **93 / 100**

| Improvement | Points |
|---|---:|
| D1–D6 defects eliminated + guard-tested | +8 |
| AI-maximal: cross-provider max reasoning, multi-pass, senior-reviewer whole-doc pass, Phase-4 root-cause synthesizer | +7 |
| Wireframe + lineage reintroduced at production quality | +3 |
| Dedicated landing + hub design push | +2 |
| Consistent provenance/confidence + merged/trimmed redundancy | +1 |
| **Projected total** | **93** |

### Why not 100 (honest ceiling)

The product extracts **metadata only** — by design it never sees row-level data or the human context behind a model (why a measure exists, who owns it, the business rule a filter encodes). Some of that is genuinely unknowable without a person confirming it. The honest ceiling for _fully-automated_ docs is **~93–95**. The last 5 points come from the **enrichment round-trip** (a human confirms owner/business rules once, and the doc absorbs it) — the September fast-follow "moat" feature. So: **72 → 93 automated, → ~97 with human-in-the-loop enrichment.**

---

# 2. Full Project Audit

## 2.1 Architecture (verdict: correct, minor debt)

Confirmed against `DOCUMENTATION_QUALITY_PLAN.md` Part A and the source. The pipeline `parse → AI agents → render → service → auth` is well-factored. Standing debt to clear:

- **A2-1 — Hosted state is ephemeral (HIGH).** In-memory `service/jobs.py::JobStore` and SQLite on Cloud Run's ephemeral disk: a second instance 404s on another instance's job; any restart wipes jobs, accounts, API keys, and quota counters. This is the single biggest production risk. Fix in Sprint 4.
- **A2-2 — Render duplication (MEDIUM). ✅ RESOLVED (verified 2026-07-08, Day 11).** Originally: `render/html.py` carries its own copy of the shell that `render/_html_shell.py` also provides. Turned out to already be fixed (in `d4d195f`, pre-dating this roadmap) — `html.py`, `audit.py`, `executive.py`, and `user_guide.py` all build only section-body HTML and call the shared `page_shell()`. No migration was needed; golden snapshots (`tests/test_golden_html.py`) confirm and guard it.
- **A2-3/A2-4 (LOW)** — drop the vestigial `jinja2` extra; add the `python_version < '3.14'` marker to the `pbix` extra.

## 2.2 Generated-output defects (the launch-blockers)

These are real, reproduced in the current `Corporate_Spend_Report` output (2026-07-07 16:58). Each is small to fix and each is the kind of thing a Fortune-500 reviewer notices in the first minute.

**D1 — Executive Summary reads like an audit log (AI-Native Phase 1 not done).** `executive.md`:
- §4: _"Regular review of 5 modeling and 11 governance gaps is vital…"_ — audit-speak leaked into an exec doc.
- §6 "What's Next": _"This document is 6% complete — 16 field(s) still need business input: owner, refresh_schedule, version, status, author, reviewer, classification…"_ — an internal completeness nag shown to executives.
- §5 Ownership renders empty `Steward` / `Classification: not specified` rows nobody asked for.
- **Fix:** AI-Native Phase 1 exactly as specced (`executive.py`, `io.py`, `render/executive.py`, schema). Reframe risks as business consequences + a named ask; move the completeness % to a job warning; render Steward/Classification only when set.

**D2 — LLM meta-commentary leaks into user-facing prose (grounding/critic failure).** `user-guide.md`:
- Intro (line 8): _"…exploring variance across different financial scenarios such as **Verify existence of 'Plan, LE1, LE2, and LE3' in the model.**"_ — a model instruction rendered as body copy.
- Glossary: _"select — **Consider providing a more specific description** of how 'select' is used…"_, _"Sales Region — **Remove the duplicated entry as it is identical to glossary[15].plain_definition.**"_ — editing directives, referencing an internal array index, shipped as definitions.
- **Root cause:** the critic/DAX-translator agents sometimes return instructional text; the grounding pass (Phase 3) doesn't reject it because it isn't a false _factual_ claim. `user_guide.py::_narrative_triples` does feed glossary definitions to grounding, so the guard belongs there.
- **Fix:** an output-sanitation validator (deterministic) that rejects any prose/definition field matching meta-commentary patterns (`^(Consider|Remove|Verify|Ensure|Add a|Provide)\b`, `glossary\[`, `plain_definition`, `the duplicated entry`, bracketed array indices) and falls back to the deterministic definition. Cheap, high-impact, no new LLM cost.

**D3 — Grounding inline-replacement breaks grammar.** `audit.md` line 8: _"However, **Unknown — requires business confirmation.**, are aspects that need attention, whereas **Unknown — requires business confirmation.**."_ Grounding replaced two mid-sentence clauses with the canned "Unknown…" sentence, producing nonsense.
- **Fix:** grounding should replace at sentence/field granularity, not splice a full stop mid-clause. When a claim spans part of a sentence and is unverifiable, drop the whole sentence rather than substitute in place.

**D4 — Field selectors leak as `select`/`select1` (I4 regressed or incomplete).** `user-guide.md`: visuals titled _"Actual, Plan by select, select1"_ and question _"How is Actual distributed by select?"_ still appear — even though `DOCUMENTATION_QUALITY_PLAN` Part J item **I4** claims this was fixed and test-verified on 2026-07-06. The fix isn't firing on visual titles / generated questions in this output.
- **Fix:** verify `report_facts` field-parameter recognition covers the visual-title and question-generation paths, not just the glossary; add a golden assertion that no rendered doc contains a standalone `select`/`select1` field token.

**D6 — "Unknown — requires business confirmation" is massively over-applied (P0, user-flagged).** The phrase fires 10× across the sample — on columns whose role is mechanically obvious. `technical.md`: `Country/Region ID`, `Business Area ID`, `IT Sub Area ID`, `Scenario ID`, `Department` all render _"Purpose could not be inferred automatically; requires business confirmation."_ — yet these are numeric keys the engine already knows participate in relationships (it even flags them as high-cardinality IDs in the audit). It reads as the AI giving up on trivial things, which is worse for trust than saying nothing.
- **Root cause (subtle):** the _deterministic_ layer is actually correct — `technical.py:237-238` already derives `*ID`/`*Key` columns as _"Key identifier; used to join {table} to related tables."_ But the **LLM Column Describer then overwrites that good structural description with the punt phrase**, because `io.py:27/201/347` instructs it "NEVER guess → write 'requires business confirmation.'" The anti-hallucination guardrail is too blunt: it makes the model _downgrade_ a perfectly good mechanical description to an alarming compliance flag whenever it's unsure of the deeper _business_ meaning.
- **Fix (three deterministic layers, no new LLM cost):**
  1. **LLM may only improve, never downgrade.** In the merge step (`technical.py:248-254` and the measure path at `:544`), if the LLM returns the punt phrase (or empty/low-signal) but a real deterministic description exists (key identifier, calculated, date column), **keep the deterministic one.** The AI can enrich, not erase.
  2. **Broaden deterministic derivation before any punt.** Any column participating in a relationship → "Join key linking {table} to {related}." Date/time columns, hidden technical columns, and calculated columns already have honest lines. Only genuinely roleless columns fall through.
  3. **Soften the terminal wording.** Reserve "requires business confirmation" for genuine _business-meaning_ ambiguity in prose. For an undescribed reference-table column with no structural role, emit the calm, accurate _"No description set."_ — not a compliance-sounding alarm. Update `io.py` prompts so the model, when unsure, returns the structural fact it _does_ know (type + table + relationship role) instead of punting.
- **Guard test:** assert the join-key columns in the fixture never render the punt phrase, and that the phrase count in a rendered doc is bounded.

**D5 — Auto date/time noise inflates the audit (AI opportunity, not a bug).** `audit.md`: 31 unused assets and a "galaxy schema, 2 fact tables" finding are both driven largely by auto-generated `DateTableTemplate_*` / `LocalDateTable_*` local tables. A human consultant would say: _"Disable Auto Date/Time (PBIC-PERF-007) and ~20 of these findings collapse, and the star-schema warning likely clears."_ The engine reports each finding in isolation; it never connects them.
- **Fix:** this is precisely the AI-Native Phase 4 audit-synthesizer's job (root-cause clustering + remediation order). High-value.

## 2.3 AI pipeline (verdict: still under-reasoning)

Per `AI_NATIVE_PLAN.md`'s own diagnosis, confirmed in output: the AI mostly restates deterministic facts. Phases 0/2/3 added shared context, a whole-model synthesis call, and grounding — real progress — but:
- The synthesis (`insights.py`) is computed but under-surfaced: docs still read section-by-section rather than as one narrative. D5 shows the AI never connects related findings.
- Grounding exists but is too weak to catch D2/D3.
- Cardinality/`size_bytes` VertiPaq signals are parsed but unused by any rule (Phase 4 item 2).
- No interactive surface (Phase 5).

## 2.4 SaaS/product surface (verdict: not launchable yet)

Confirmed from `service/app.py` routes and `service/accounts.py`. Present: `/`, `/admin`, `/admin/api/*`, `/healthz`, `/me`, `POST /jobs`, `GET /jobs/{id}`, download. Multi-tenancy is **API-key-only** (`pbicompass_sk_…`), minted via a shared admin token. **Absent:** user signup, login/session, password reset, email, Stripe/billing, self-serve plan selection, account dashboard, org/team model, SSO, audit logging. A prospective customer cannot self-serve today.

---

# 3. Gap Analysis

| Area | Have | Missing (this roadmap closes) | Priority |
|---|---|---|---|
| Output credibility | Deterministic backbone, 50-rule audit | D1–D6 defect fixes (§2.2) | **P0** |
| AI reasoning | Phases 0/2/3 | Phase 1 (exec), Phase 4 (synthesizer+VertiPaq), Phase 5 (Ask) | P0 (1), P1 (4), P2 (5) |
| Reasoning control | Anthropic-only effort | Cross-provider reasoning knob (Gemini/Cohere/OpenAI) + user-selectable level | **P0** |
| Hidden content | Wireframe/lineage code exists | J.C redesign + reintroduction | P1 |
| Server state | In-memory jobs, SQLite | Persistent job store, managed Postgres | **P0** |
| Async execution | In-process worker | Celery+Redis (or managed queue) | P1 |
| Observability | `/healthz`, stderr | Structured logs, error tracking, metrics, alerts | **P0** |
| AuthN | API keys | Signup, login, sessions, email verify, reset | **P0** |
| Billing | `plan` field + quotas | Stripe Checkout, portal, webhooks, dunning | **P0** |
| Account mgmt | — | Dashboard: plan, usage, keys, job history | P1 |
| Enterprise | tenant tag | Orgs/teams, RBAC, seats, audit log, SSO/SCIM | P2 (foundations now, SSO fast-follow) |
| Testing | unit + golden | Integration, e2e-browser, load, billing-webhook | P1 |
| Deployment | Dockerfile, Render/Fly docs | Managed Postgres/Redis, secrets, CI gates, staging | **P0** |

---

# 4. AI Improvement Roadmap

Goal (from `AI_NATIVE_PLAN.md`): the AI behaves like an experienced BI consultant, not a document generator. Sequenced to build on the done Phases 0/2/3.

**4.0 Cross-provider reasoning control (P0, 1.5 days, user-mandated).** Today the `effort`/reasoning level only reaches Anthropic (`llm.py:92` `output_config.effort`); Gemini, Cohere, and MeshAPI all accept the kwarg and **silently ignore it** (`llm.py:154, 206, 289-298`). Requirement: high-level reasoning must be selectable on **any** provider — token cost is explicitly not a concern for the owner's own runs. Wire the `EFFORT_LEVELS` (`low/medium/high/xhigh/max`) to each provider's native reasoning knob:
- **Gemini** — map effort → `thinking_config` (`thinking_budget` token budget, or `thinking_level` on Gemini 3.x); `xhigh/max` → dynamic/maximum budget. The default `gemini-3.5-flash` supports thinking.
- **Cohere** — the reasoning knob only exists on reasoning models (`command-a-reasoning`), not the default `command-a-03-2025`. When effort ≥ high, either route to the reasoning model or pass Cohere's `thinking` budget; expose via `--model`.
- **MeshAPI/OpenAI** — send `reasoning_effort` **only when the routed model is reasoning-capable** (o-series/gpt-5, not `gpt-4o`, which 400s — the exact landmine noted in `llm.py:290-298` and the git history). Default the MeshAPI reasoning path to a reasoning-capable model.
- **Graceful degradation (critical):** wrap every provider's reasoning param so a rejecting model (400 / unknown-arg) **retries the same call without it** rather than failing the agent — this permanently defuses the MeshAPI 400 class and matches the repo's fallback philosophy.
- **Cost policy (owner decision, 2026-07-07): best output, token cost is not a concern.** Every tier runs at **max reasoning** — do **not** clamp reasoning depth by plan. Remove/disable `worker.py::_clamp_effort_for_plan`. The only cost guardrail is the **daily job quota that already exists** (`PLAN_LIMITS`: free 10 / pro 200 / enterprise) — free users get full-quality output on fewer runs, never degraded output. Expose a `--effort` CLI flag and a service upload field so the level is user-selectable (defaulting to max).
- _Verify:_ per-provider unit tests asserting the effort maps to the right native param (via a mock capturing call kwargs); a rejecting-model test asserting the retry-without-reasoning fallback fires; one real smoke per provider.

**4.1 Phase 1 — Executive Summary editorial fix (P0, 1 day).** Fixes D1. Reframe risks as _business consequence + named ask_; ban IT-governance vocabulary in the exec doc via `EXEC_STYLE_RULES`; rewrite `maintenance_note` to plain language; drop the completeness nag (emit as job warning); conditional Ownership rows. Cost-neutral (same single Executive Writer call).

**4.2 Output sanitation + anti-punt guard (P0, 1 day).** Fixes D2/D3/D6. Deterministic post-generation validator rejecting meta-commentary/editing-directive text in any prose or definition field; sentence-granular grounding replacement; and the **"AI may only improve, never downgrade"** merge policy so the LLM can't overwrite a good structural description with "requires business confirmation" (D6). No LLM cost. This is the cheapest, highest-perception-value AI-quality work in the plan.

**4.3 Phase 4 — AI-augmented audit (P1, 3 days).** Fixes D5 and delivers the "consultant" jump.
- **Audit Synthesizer agent** — clusters findings by root cause, orders remediation, writes a strategic narrative ("disable Auto Date/Time → ~20 findings clear"). New fields on `schemas/audit_document.py`; "Root-Cause Analysis" section in all three renderers; top cluster surfaced in technical §16. Deterministic fallback = section omitted.
- **VertiPaq threshold rules** (deterministic) — read the parsed `cardinality`/`size_bytes` (high-card slicer, wide text column dominating size, near-constant dimension). No-op when stats absent.
- **AI fix snippets** for top-N findings, labelled "AI-suggested — review before applying" (paid feature).

**4.4 Phase 5 — "Ask about this report" (P2, 2 days, paid).** Interactive Q&A over a finished job within TTL. `POST /api/jobs/{id}/ask`; grounding source = retained `model.json` + Phase-2 digest; citations to doc anchors; zero server-side chat history (zero-retention preserved); plan-gated (pro+) with a per-day question quota; chat box on the result page; `pbicompass ask` CLI parity. This is the new revenue-story surface — ship after 1–4 are proven.

**4.6 AI-maximal output architecture (target direction, threaded through 4.0–4.4).** The owner's mandate: _make the most of AI — the architecture should lean fully into AI for the best possible output_, with token cost no object. This does **not** mean abandoning the deterministic backbone — that backbone is what guarantees zero hallucination, zero leakage, and graceful degradation, and it stays the ground truth for **facts** (extraction and audit _detection_ are never LLM-guessed). It means maximizing AI everywhere it adds _judgment_:

- **Max reasoning by default, all tiers.** Every prose/interpretive agent runs at `max` effort across all providers (§4.0). Per the owner's cost decision, reasoning depth is **not** plan-clamped — quality is never traded for tokens; the daily job quota is the only cost lever.
- **Multi-pass reasoning, not single-shot.** Today each field is one stateless call polished by a style critic. Move to a **draft → self-critique → ground → refine** loop reusing the existing `critic.py`/`grounding.py` infra, so every narrative field is reasoned over more than once.
- **A "senior reviewer" whole-document pass (new).** After all four docs are assembled, one high-effort AI pass reads the **entire document set at once** and improves cross-document coherence, executive framing, and narrative flow — the thing a Big-4 partner does before a deliverable ships. Consumes the Phase-2 Report Intelligence synthesis; deterministic fallback = skipped. This is the single biggest "consultant-grade" lever and the clearest expression of "full AI usage."
- **Widen AI coverage to every interpretive surface**, guarded by D6's _improve-never-downgrade_ rule and the grounding pass — column meanings, page questions, glossary, recommendations — so nothing interpretive is left to a mechanical template when AI can do better, but AI can never erase a correct deterministic fact.
- **Surface the reasoning.** Promote the Phase-2 synthesis (`insights.py`) into an explicit, readable "How this report works" narrative rather than only using it as hidden context — show the buyer the AI actually understood their model.

_Net cost:_ materially higher tokens/job — explicitly accepted ("want the best output, no issue of the token"). Bounded only by the daily job quota, never by degrading reasoning. _Verify:_ A/B read of the sample with/without the senior-reviewer pass; confirm deterministic fallback still produces complete docs with every AI pass disabled.

**Global AI verification (every phase):** extend `tests/test_agents.py::FakeLLMClient` with a unique-keyword branch; every new schema field lands in md+html+docx and both entry points (`cli.py` + `service/worker.py`); one offline run proving deterministic fallback still completes.

---

# 5. Documentation Enhancement Roadmap

Beyond the D1–D4 output fixes (§2.2, sequenced in §4):

**5.1 Reintroduce Wireframe v2 (P1, 1.5 days).** Follow `DOCUMENTATION_QUALITY_PLAN` J.C spec: draw each page as a "slide" (framed canvas, light in dark mode), friendly visual-type names (map `lineStackedColumn…` → "Stacked column chart"), visual titles, resolved field links, no inline `style=`/`onmouseover=`. Then uncomment `render/html.py:456-457` and `user_guide.py:146-147`. Golden tests for all hrefs resolving.

**5.2 Reintroduce Lineage graph (P1, 1 day).** Redesign `render/_lineage.py` SVG to the same visual bar (layered left-to-right, readable labels, dark-mode-aware, no truncated internal names). Uncomment `render/html.py:344-345`. Keep the deterministic edge-list table as the fallback (already in md/docx).

**5.3 Document-control & provenance consistency (P2, 0.5 day).** Confirm every renderer shows the `[Extracted]`/`[AI-inferred]`/`[Human-provided]` provenance and confidence labels identically (technical doc already does; audit the exec/user-guide/audit HTML). This is the "trust" polish that makes it read as consultant-grade.

**5.4 Merge/trim recommendations.** From the output review: the audit's §3 DAX table and §8 recommendations partially restate each other (every "Missing Description" measure is listed in both). Collapse the per-measure "Missing Description" rows into a single grouped finding with a count + expandable list (the pattern already used for unused assets). Keeps information density high without repetition.

---

# 6. UX / UI Polish Roadmap

The design system is already strong; this is polish, not a redesign.

- **6.1 Migrate `html.py` onto `_html_shell.py`** (A2-2) — ✅ already done pre-roadmap, verified 2026-07-08 (Day 11); golden tests already in place. (0 days — no work needed)
- **6.2 Upload UI → product UI.** `static/index.html` is a bare upload box. For SaaS it needs: signed-in state, plan/quota badge, recent-jobs list, and the API-key field surfaced only for programmatic users. (folded into Sprint 5)
- **6.3 `index.html` professional redesign — dedicated 2–3 day design push (owner-requested).** Both `index.html` surfaces get a real design pass, not a polish tweak:
  - **The product landing / upload page** (`service/static/index.html`) — the first thing a prospect sees. Treat it as a proper SaaS landing: clear value proposition, a "what you get" preview (thumbnails of the four generated docs), trust signals (zero-retention/zero-leakage, "metadata only"), a clean upload/CTA, and the sign-in state. This is the conversion surface — it must look like a product, not a script's front door.
  - **The per-job documentation hub** (`render/hub.py` → per-job `index.html`, the 56 KB file in the output bundle) — the artifact a customer _shares_. Redesign into a cover page worthy of a deliverable: report title, generated date, an at-a-glance health-score chip + top-3 findings, document cards with descriptions and read-time, cross-doc breadcrumbs, and consistent branding. A shared link should read like a Big-4 cover sheet, not a file listing.
  - Reuse the existing design system (`_html_shell.py` tokens, dark mode, a11y) so the two surfaces and the four docs read as one product. _(2–3 days, Sprint 7 — placed pre-launch so the landing page can show real sign-in state and pricing once auth/billing exist.)_
- **6.4 Print/PDF pass.** Verify all four docs print to clean PDF (page breaks, no clipped tables, light theme forced) — the primary "share with an executive" path. (0.5 day, Sprint 1 QA)
- **6.5 Accessibility & mobile.** Skip-link, scroll-spy, and visually-hidden classes exist; do one axe pass and one mobile-TOC check before launch. (0.5 day, Sprint 7)

---

# 7. Authentication Implementation Plan (Standard SaaS)

Target flow: **create account → verify email → log in → pick plan → pay → upload → generate → manage account.** Build on the existing `AccountStore`/tenant model — extend it, don't replace it.

**7.1 Data model (extend `accounts.py`, move to Postgres in Sprint 4).** Add `users` (id, email, password_hash [argon2/bcrypt], email_verified, created_at), keep `accounts`/tenants as the billing/quota entity, add a `user↔account` membership link (sets up teams in §8). API keys become one auth method _on_ an account, not the only one.

**7.2 Sessions.** Cookie-based server sessions (HTTP-only, Secure, SameSite=Lax) signed with a server secret; CSRF token on state-changing forms. Keep Bearer API keys for programmatic `/jobs` use.

**7.3 Recommended: add Microsoft Entra ID (Azure AD) sign-in early.** The audience _is_ Power BI users; "Sign in with Microsoft" is the lowest-friction path and doubles as the on-ramp to enterprise SSO later. Implement OAuth2/OIDC alongside email+password.

**7.4 Email (transactional).** Pick one provider (Resend/Postmark/SES). Flows: email verification, password reset, and (later) billing receipts. Content-free w.r.t. report data.

**7.5 Endpoints.** `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/verify?token`, `POST /auth/reset-request`, `POST /auth/reset`, `GET /auth/oidc/*`. Rate-limit all auth routes; reuse the admin brute-force-lockout pattern already in `admin.py`.

**7.6 Account dashboard.** `/app`: current plan + usage vs quota, API-key management (create/revoke — logic already exists), job history (status only, zero-retention), billing link (§ below). Replaces the need for the shared-admin-token flow for end users.

---

# 8. Enterprise Implementation Plan

Keep practical and aligned to the current tenant architecture. Deliver **foundations** in the 35-day plan; SSO/SCIM as a defined fast-follow.

**8.1 Organizations & teams (foundations, Sprint 7).** Promote `tenant` to a first-class `organization` (name, plan, seat count). Users belong to an org via membership with a **role**: `owner` / `admin` / `member`. Jobs are already tenant-tagged — reuse that isolation. Shared workspace = all jobs under an org, visible per role.

**8.2 RBAC.** Enforce at the route layer: owners/admins manage members, keys, and billing; members generate docs. A small decorator over the existing `resolve_tenant` in `app.py`.

**8.3 Seat-based licensing.** Enterprise plan = N seats (not the current daily job quota). Invites by email; seat count syncs to the Stripe subscription quantity.

**8.4 Audit logging (SOC 2 hygiene).** Append-only log of _actions_ (login, key mint/revoke, member add/remove, plan change, job submitted) — **never** report metadata. Content-free by construction. New `service/audit_log.py`, Postgres-backed.

**8.5 SSO / SCIM (fast-follow, post-launch).** SAML 2.0 + SCIM provisioning for enterprise buyers. Entra ID OIDC from §7.3 is the stepping stone; full SAML/SCIM is a 1–2 week project scoped after the first enterprise lead. Design the user/org model now so this doesn't require a migration.

**8.6 On-prem / self-host** remains supported (the zero-dependency core and `PBICOMPASS_REQUIRE_AUTH=0` already enable it) — position as an enterprise option.

---

# 9. Production Hardening Checklist

- [ ] **Persistent job store** — back `JobStore` with Postgres/Redis behind its existing `create/mark_*/get/get_output/sweep` surface; outputs in a BLOB/object store with TTL sweep (A2-1). **P0.**
- [ ] **Managed Postgres** for accounts/users/orgs/audit-log (Supabase/Neon free tier); select backend via `PBICOMPASS_DB` URL scheme; keep SQLite for self-host. **P0.**
- [ ] **Async worker** — Celery + Redis (or a managed queue); the worker signature is already queue-agnostic. Removes the Cloud Run CPU-throttling failure class. **P1.**
- [ ] **Structured logging** — JSON logs with request/job IDs; **assert content-free** (no report metadata) in a test. **P0.**
- [ ] **Error tracking** — Sentry (or equivalent) with PII scrubbing on. **P0.**
- [ ] **Metrics & alerts** — jobs/min, failure rate, LLM latency/spend per job, quota-429 rate; alert on error-rate and worker-stall. **P1.**
- [ ] **Health/readiness** — extend `/healthz` to a real readiness check (DB, queue, disk). **P0.**
- [ ] **Rate limiting** — per-IP on auth + upload routes, above the per-plan daily quota. **P0.**
- [ ] **Secrets management** — no secrets in image/env files; use the platform secret store; rotate the admin token. **P0.**
- [ ] **Security headers** — CSP, HSTS, X-Content-Type-Options (partially present via `_security_headers`); audit and complete. **P1.**
- [ ] **Upload hardening** — confirm size cap, zip-slip guard (present), MIME/type checks, and per-job timeout watchdog (present) all active in prod config. **P0.**
- [ ] **Backups** — automated Postgres backups + restore drill. **P1.**
- [ ] **Zero-retention regression test** — post-job: sandbox shredded, no report content in logs, cache file gone. Run in CI. **P0.**

---

# 10. Testing Strategy

Current: 182 unit + golden tests. Add, in priority order:

1. **Output-quality guards (P0)** — assertions that the meta-commentary patterns (D2), mid-sentence "Unknown…" splices (D3), `select`/`select1` tokens (D4), and over-applied "requires business confirmation" on relationship-participating columns (D6) never appear in any rendered doc. These lock the launch-blocker fixes permanently.
2. **Golden HTML snapshots (P0)** for all four doc types before the `html.py`→shell migration and the wireframe/lineage reintroduction, so refactors are provably non-visual.
3. **Integration tests (P1)** — full FastAPI flow with a fake LLM: signup → verify → login → checkout (Stripe test mode) → upload → poll → download; TTL-expiry → 404; quota-429; plan gating.
4. **Billing-webhook tests (P0 for billing)** — Stripe CLI fixtures for `checkout.session.completed`, `customer.subscription.updated/deleted`, payment failure → plan downgrade.
5. **e2e browser smoke (P1)** — Playwright: sign in, upload a fixture `.pbip`, download HTML.
6. **Load test (P1)** — k6/Locust against `/jobs` at target concurrency; confirm the worker + persistent store hold; capture p95 latency and cost/job.
7. **Auth/security tests (P0)** — session fixation, CSRF, brute-force lockout, tenant isolation (another tenant's key → 404, already covered — extend to orgs/roles).

**CI gate:** all P0 tests green + coverage floor before deploy to staging; staging smoke before prod.

---

# 11. Performance Optimization Plan

- **LLM spend & latency** — Phase 0 already dedupes the DAX translator and tiers effort. Add: per-job token/cost telemetry (already scoped in Phase 0 `usage`), surface cost/job in metrics, and confirm the response cache is enabled per-job in the service (sandbox-scoped, zero-retention).
- **Parallelize agent calls** — the four prose agents are independent per job; run them concurrently (bounded) rather than serially to cut wall-clock. Verify the in-process fan-out already does this; if not, it's a quick win.
- **Render performance** — the hand-rolled builders are fast; the risk is large models producing very large HTML (technical.html is 188 KB here). Confirm streaming/download works and consider lazy-rendering giant tables (collapsible, already a pattern).
- **Cold start** — keep the parser stdlib-only (already true); lazy-import FastAPI/agents extras so the CLI stays snappy.
- **DB** — index `usage(tenant, day)` (PK already), `jobs(id)`, `accounts(key_hash)` (unique already); add connection pooling with Postgres.
- **Targets to set & measure:** p95 job time for a typical `.pbip` (all 4 docs), cost/job by plan, and max concurrent jobs per instance — capture these in the load test (§10.6) before setting quotas/pricing.

---

# 12. Deployment Readiness Checklist

- [ ] **Managed Postgres + Redis** provisioned (free tier for beta); `PBICOMPASS_DB` and queue URLs wired.
- [ ] **Object store** for rendered outputs with TTL (or Postgres BLOB for beta).
- [ ] **Secrets** in platform secret store: `ANTHROPIC_API_KEY`/provider keys, `STRIPE_*`, session secret, admin token, email provider key.
- [ ] **Staging environment** mirroring prod; deploy pipeline promotes staging→prod on green CI.
- [ ] **Cloud Run/host config** — `--no-cpu-throttling` (or the managed worker), `--max-instances` sized, health checks wired (A2-1 mitigations).
- [ ] **Domain, TLS, custom email domain** (SPF/DKIM for transactional email).
- [ ] **CI/CD** — the existing `.github` workflow extended with the new test gates; no deploy on red.
- [ ] **Rollback plan** — tagged releases, one-command rollback, DB migration up/down tested.
- [ ] **Runbook** — update `DEPLOYMENT.md`/`BEGINNER_DEPLOY.md` for the new stateful architecture; document env vars in `.env.example`.

---

# 13. Launch Checklist

- [ ] All P0 output defects (D1–D4) fixed and guard-tested.
- [ ] Signup → login → verify → pay → generate → manage flow works end-to-end in staging.
- [ ] Stripe live-mode keys, webhook endpoint verified, one real test purchase + refund.
- [ ] Legal: Terms, Privacy, DPA stub, the zero-retention/zero-leakage claims in `SECURITY.md` reviewed against actual behavior.
- [ ] Pricing page reflects real plans/quotas; free-tier limits enforced and tested.
- [ ] Monitoring dashboards + alerts live; on-call/notification path set.
- [ ] Wireframe + lineage either reintroduced at production quality **or** explicitly kept out of scope for v1 (decide, don't leave commented-out code shipping).
- [ ] Backups running; restore drill passed.
- [ ] Load test at expected launch concurrency passed.
- [ ] Landing page + docs updated; demo report + sample outputs published.
- [ ] Beta cohort (5–10 friendly BI users) onboarded before public announcement.

---

# 14. Day-by-Day Execution Plan

**Assumptions:** solo engineer (Rajdeep), ~6–8 focused hrs/day, Mon–Fri (weekends = buffer/review). 38 business days, **Jul 8 → Aug 28 2026** (includes the cross-provider reasoning work and the dedicated 2–3-day `index.html` design push). Multi-tool handoff (Claude / Antigravity+Gemini) is fine at sprint boundaries — each sprint ends verified. Beta launch target: **~Aug 31**; GA hardening continues into September.

**Dependency spine:** Output credibility (S1) → AI depth (S2) → reintroduce hidden content (S3) → make it stateful & observable (S4) → let users sign in (S5) → let them pay (S6) → org/enterprise + Ask + launch (S7). Auth (S5) depends on persistent Postgres (S4); billing (S6) depends on auth (S5).

---

## Sprint 1 — Output credibility (Jul 8–14 · Days 1–5)

Kill every embarrassing line before anyone new sees the output. Highest perception-per-hour work in the plan.

**Day 1 (Jul 8) — AI-Native Phase 1: Executive Summary editorial fix.**
- _Objective:_ exec doc reads for a business owner, not an auditor (fixes D1).
- _Tasks:_ extend `EXECUTIVE_WRITER_SCHEMA` with `reframed_risks`; pass full recommendation objects; add `EXEC_STYLE_RULES` banning IT-governance vocab; rewrite `_maintenance_note`; remove completeness block from `_next_steps` (emit as warning); conditional Ownership rows in all 3 renderers.
- _Deliverable:_ new exec doc across md/html/docx; updated goldens.
- _Dependencies:_ none. _Done-when:_ grep of rendered exec doc finds none of {"governance finding","best practice","% complete","fields still need"}; Steward/Classification absent when unset.

**Day 2 (Jul 9) — Output sanitation + anti-punt guard.**
- _Objective:_ no LLM meta-commentary in prose (D2); no over-applied "requires business confirmation" on obvious columns (D6).
- _Tasks (D2):_ deterministic validator rejecting `^(Consider|Remove|Verify|Ensure|Provide|Add a)\b`, `glossary\[`, `plain_definition`, array-index refs; wire into glossary build + all `_narrative_triples`; fall back to deterministic text on reject.
- _Tasks (D6):_ "AI may only improve, never downgrade" merge policy in `technical.py:248-254` and the measure path `:544` — keep the deterministic structural description when the LLM returns the punt phrase/empty; broaden deterministic derivation (relationship-participating columns → join keys); soften terminal wording to "No description set." for roleless columns; update `io.py:27/201/347` so the model returns the structural fact it knows instead of punting.
- _Deliverable:_ clean glossary + intro; column/measure descriptions that no longer punt on keys; guard util + tests.
- _Dependencies:_ none. _Done-when:_ the D2 strings can't appear; the sample's join-key columns render real descriptions; the "requires business confirmation" count in a rendered doc is bounded and never appears on a relationship-participating column.

**Day 3 (Jul 10) — Grounding sentence-granularity fix.**
- _Objective:_ grounding never produces mid-sentence "Unknown…" splices (fixes D3).
- _Tasks:_ change `grounding.py` unverifiable handling to drop/replace whole sentences; add the audit-narrative case as a test fixture.
- _Deliverable:_ grammatical audit narrative. _Done-when:_ no rendered doc contains `.,` or a "Unknown — requires business confirmation." fragment mid-sentence.

**Day 4 (Jul 13) — Field-selector (I4) regression fix.**
- _Objective:_ no `select`/`select1` leaks in titles/questions/glossary (fixes D4).
- _Tasks:_ trace `report_facts` field-parameter recognition into the visual-title and question-generation paths; label as "(field selector)"; exclude from generated questions.
- _Deliverable:_ clean user guide + goldens. _Done-when:_ no rendered doc contains a standalone `select`/`select1` token; "How is Actual distributed by select?" cannot be generated.

**Day 5 (Jul 14) — Full regen + QA + print pass.**
- _Objective:_ verify Sprint 1 end-to-end across all 4 docs × 4 formats.
- _Tasks:_ regenerate the Corporate Spend sample (Gemini smoke + offline run); read-through all formats; PDF print check (§6.4); add the §10.1 output-quality guard tests to CI.
- _Deliverable:_ clean sample bundle; green CI with new guards. _Done-when:_ manual read-through finds zero D1–D6 defects; offline fallback still produces complete docs.

**Sprint 1 outcome:** the generated documentation is genuinely "share with an executive" clean. This alone materially raises the content score (78 → ~88).

---

## Sprint 2 — Reasoning control + consultant-grade audit (Jul 15–21 · Days 6–10)

**Day 6 (Jul 15) — Cross-provider reasoning control (§4.0, user-mandated).**
- _Objective:_ reasoning level works on every provider, not just Anthropic; token cost is not a constraint for owner runs.
- _Tasks:_ wire `effort` → Gemini `thinking_config`, Cohere reasoning-model/`thinking`, MeshAPI/OpenAI `reasoning_effort` (reasoning-capable models only); wrap each in a retry-without-reasoning fallback so a rejecting model degrades instead of failing (defuses the MeshAPI 400 class); add `--effort` CLI flag + service upload field; keep the per-plan ceiling in `worker.py`.
- _Deliverable:_ any provider honours the selected reasoning level; unit tests per provider + a rejecting-model fallback test.
- _Dependencies:_ none. _Done-when:_ a Gemini/MeshAPI run at `max` visibly reasons (token/latency delta), and a non-reasoning model still completes via fallback.

**Day 7 (Jul 16) — VertiPaq deterministic rules + Audit Synthesizer call.** Add threshold rules in `audit_rules.py` reading `cardinality`/`size_bytes` (no-op when absent; feed into the Phase-2 digest); then the new `io.py` synthesizer prompt + `generators/audit.py` call producing `{clusters, strategic_narrative}` with a FakeLLMClient branch. _Done-when:_ new rules covered in `tests/test_audit_rules.py`; the Auto-Date/Time root cause (D5) is clustered with its dependent findings on the sample.

**Day 8 (Jul 17) — Render the Root-Cause Analysis section (md/html/docx).** Deep-link clusters to finding anchors; surface top cluster in technical §16; deterministic fallback = omitted. _Done-when:_ section appears in all three formats and every cluster link resolves.

**Day 9 (Jul 20) — AI fix snippets (paid).** Append "AI-suggested — review before applying" DAX/M sketches to top-N `suggested_fix`; plan-gated. _Done-when:_ snippets render fenced; critic skips them; free plan omits.

**Day 10 (Jul 21) — Sprint 2 QA + A/B read.** Regenerate; compare audit with/without synthesizer; confirm the doc now explains _why_ and _what first_. _Done-when:_ the audit reads like a consultant's root-cause memo, not a findings dump.

**Sprint 2 outcome:** AI score 68 → ~80; the audit becomes a differentiator.

---

## Sprint 3 — Reintroduce hidden content at production quality (Jul 22–28 · Days 11–15)

**Day 11 (Jul 22) — `html.py` → `_html_shell.py` migration (A2-2). ✅ DONE (2026-07-08 — pre-existing, verified not re-implemented).** Audited `render/html.py` against `render/_html_shell.py`: the migration described in A2-2 was already completed back in the `d4d195f` ("Documentation Quality Plan Step 0/1/2") commit, well before this roadmap was drafted — `html.py` builds only its own section-body HTML and hands it to the shared `page_shell()`; it carries no local `_CSS`/`<!DOCTYPE>`/script copy. Confirmed `audit.py`, `executive.py`, and `user_guide.py` also all call the shared `page_shell()` — no duplicated shell remains in any of the four document-type HTML renderers. `render/hub.py` (the separate per-job cover-page artifact, not one of the four docs) intentionally keeps its own lightweight shell — out of scope for A2-2 and explicitly redesigned on its own in Sprint 7 Day 34. `tests/test_golden_html.py`'s 4 byte-exact snapshots (technical/audit/executive/user-guide) already exist and all pass, locking this in against regression. _Done-when:_ snapshots byte-identical (or intentional-diff reviewed); duplication gone. — **Verified: no duplication found, all 4 golden snapshots pass (`pytest tests/test_golden_html.py` → 4 passed). No code changes were needed.**

**Day 12 (Jul 23) — Wireframe v2 redesign (part 1).** Framed "slide" canvas, friendly visual-type names, visual titles, dark-mode-aware (J.C spec). _Done-when:_ no truncated internal type names; no inline `style=`/`onmouseover=`.

> **Correction found during Day 12 execution (2026-07-08):** the User Guide's wireframe path (`user_guide.py:146-147`) was **never actually commented out** — only `html.py`'s copy (456-457) is. The wireframe has been rendering *live* in the Business User Guide the whole time, shipping literal `WIP` placeholder text to end users (from commit `b1367db`, which replaced all SVG text with `WIP`). So Day 12's redesign fixes a **live output bug**, not dormant/commented code, and the Day-13 "reintroduce" premise below is only half true — see the Day 13 correction. The identical `WIP`-placeholder bug was also present (and dormant, since its append is commented) in the lineage renderer `_lineage.py`, fixed at the same time.

> **Addendum to Day 13 — ✅ DONE (2026-07-08, later same session).** Implemented per "Option A" (user-confirmed against a mockup artifact): `render/_wireframe.py` and `render/_lineage.py` rewritten to v4's exact visual language (colors, Poppins weights, card treatment, stroke icons, hover/focus states, pill-chip legend) applied to the wireframe's real per-visual positions, plus a matching "similar design" pass on the lineage graph (4 layers mapped onto the same 4 accent colors). Shared CSS added to `_html_shell.py`. Google Fonts CDN import from the reference file was swapped for the project's self-hosted Poppins WOFF2. Full detail in `ROADMAP_PROGRESS.md`'s addendum. Original correction note follows below.

> **Addendum added to Day 13 (2026-07-08, user-supplied reference file):** the user added `wireframe-v4-light.html` (repo root) as an exact visual-design reference and asked for the production wireframe to match it **100%** — font, colors, and "all the things" — plus a **similar design applied to the lineage view**. This is a materially different, richer visual language than the "J.C Wireframe v2" spec already implemented (`DOCUMENTATION_QUALITY_PLAN.md` §J.C, done below): v4 is a CSS-grid card layout (rounded 14px cards, 3px colored top-accent bar, tinted-square stroke icons, hover lift+shadow+pixel-size tag, animated sparkline/bar/line/dot charts, pill-shaped legend chips, kicker/meta header), not the current scaled-SVG "slide" of literal x/y/w/h boxes. Exact palette: data `#4f6ef7`/soft `#eef1fe`, slicer `#f59e0b`/`#fef4e4`, nav `#10b981`/`#e7f8f1`, **decorative `#8b5cf6` (purple) / `#f3eefe`** — a real change from the current spec's muted-gray decorative treatment. Font: Poppins 400/500/600/700, but the reference file loads it via a **Google Fonts CDN `@import`**, which conflicts with this repo's own documented constraint (`DOCUMENTATION_QUALITY_PLAN.md` line 722: diagrams are "hand-rolled inline SVG... no Mermaid/D3/CDN") — must be swapped for the project's already-self-hosted base64 WOFF2 (`render/_poppins_font.py`) before implementation, same fix already applied to the Day-12 mockup artifact. **Open design question, not yet resolved:** v4's mockup uses a fixed representative 12-column grid, not real per-visual coordinates — implementation must decide whether to keep the current architecture's core value (each box is the *actual* report page's real x/y/width/height, scaled) while re-skinning to v4's exact visual language, or adopt v4's layout approach wholesale. Not yet implemented — logged here per explicit request; see `ROADMAP_PROGRESS.md`'s Day 13 addendum for full detail.

**Day 13 (Jul 24) — Wireframe v2 (part 2) + reintroduce. ✅ DONE (2026-07-08) for its original scope — v4 exact-match addendum above still open.** Uncommented `html.py:456-457`; the wireframe now renders in the Technical doc (it was already live in the User Guide, see Day 12 correction). Found and fixed the real "resolve field links" bug: `render_wireframe()` computed each visual's `<a href>` independently, but `report_pages()` relabels 2+ identical visuals into one "Label — Type ×N" row and `dedupe_ids()` resolves any remaining slug collision — so any page with duplicate or slug-colliding visuals got a dead/wrong wireframe link. Fixed by having `report_pages()` build a `visual_anchor_map` (group key → resolved row-anchor slug) and threading it into `render_wireframe()`. An existing but previously-vacuous "href-resolution golden test" (`test_render.py::WireframeHrefResolutionTest`, pre-dating today) now genuinely exercises the Technical doc for the first time; a new end-to-end duplicate-visual case was added to it and proven non-vacuous (fails without the fix, confirmed via `git stash`). _Done-when:_ every wireframe `href` resolves — confirmed via 3 layers of test coverage (unit/`visual_anchor_map`, `report_pages()`-level, full-HTML-render golden). Wireframes visible again in the Technical doc.

**Day 14 (Jul 27) — Lineage graph redesign + reintroduce. ✅ DONE (2026-07-08, pulled forward from Sprint 3's own Day-13 v4 addendum + a same-day user request).** The "readable layered SVG, dark-mode-aware" redesign already happened as part of Day 13's v4 addendum (lineage got the same v4 card treatment as the wireframe). Reintroduction (`html.py:344-345` uncommented) happened today at the user's explicit request ("make the lineage visuals appear"). At the same request, the wireframe was *re-hidden* (`html.py:456-457` and `user_guide.py:146-147` re-commented) — an explicit, temporary, user-directed state ("for now"), not a regression; see `ROADMAP_PROGRESS.md`'s note for the swap. _Done-when:_ lineage renders cleanly (confirmed in the regenerated `technical.html` golden); md/docx fallback intact (`lineage_edges` table — unaffected, was never gated on the SVG).

**Day 15 (Jul 28) — Sprint 3 QA. ✅ DONE (2026-07-09).** Full regen read-through across all 4 docs × formats — lineage back in; wireframe intentionally still hidden (owner request, 2026-07-08 — see Day 14's note). Scope narrows to: lineage renders cleanly in light+dark, no regressions on the docs as they actually ship today. _(The dedicated `index.html` redesign moves to Sprint 7, §6.3.)_ Full offline regen (`--document all --bundle --provider none`) across all 4 docs × md/html/docx/json confirmed zero D1–D6 regressions and a clean lineage graph (no `WIP` text, real node labels, no CSS variables inside its SVG so it's theme-safe in both light and dark — only its card border re-themes). One genuine, pre-existing bug was found and fixed during the QA pass itself: `tests/test_golden_html.py`'s golden-snapshot normalization never stripped the technical doc's sign-off-table date (`YYYY-MM-DD`, stamped from `today()` at render time), so the suite silently failed on the first run after any calendar-day boundary since the Day 13 golden was captured — fixed by extending the test's normalization regex, not the product. Full detail in `ROADMAP_PROGRESS.md`'s Day 15 entry.

> **Owner note (2026-07-08):** the wireframe re-enablement/final polish is intentionally deferred, not forgotten — do it **last**, bundled into Sprint 7's dedicated design push (Days 33–35, §6.3) alongside the `index.html`/hub redesign, when the team is already deep in cross-surface visual work. Re-enabling it before then is a one-line-per-file revert (`html.py:456-457`, `user_guide.py:146-147`) whenever wanted sooner, but the *default* plan is: wireframe waits for the website-UI phase.

**Sprint 3 outcome:** the two intentionally-hidden sections are back at the same bar as the rest; nothing embarrassing ships commented-out.

---

## Sprint 4 — Stateful & observable (Jul 29 – Aug 4 · Days 16–20)

The infrastructure that makes it a real service. **Prerequisite for all auth/billing work.**

**Day 16 (Jul 29) — Persistent JobStore. ✅ DONE (2026-07-09).** Back `JobStore` with the DB behind its existing method surface; outputs as BLOB/object with TTL sweep (A2-1). `service/jobs.py` rewritten onto stdlib `sqlite3` (same shared-connection-plus-lock pattern `AccountStore` already established), keeping every method signature (`create/mark_processing/mark_done/mark_failed/store_outputs/get/get_output/sweep/public`) byte-identical so `app.py`/`worker.py` needed no logic changes. Defaults to `:memory:` (so all existing tests, which construct `JobStore()` themselves, are unaffected); `create_app()`'s own default (the real `uvicorn` entrypoint) now points at a file path via new `PBICOMPASS_JOBS_DB` env var (mirrors `PBICOMPASS_DB`) and closes it on shutdown. _Done-when:_ single-instance restart survives in-flight jobs (proven directly: create → mark_processing → close → reopen a second `JobStore` at the same path → job and its status/timestamps are still there); zero-retention test still passes (`test_sandbox_is_shredded` and the rest of `test_service.py` green, full suite otherwise unchanged at 496 passed). **Honest scope note:** this closes the single-instance-restart half of A2-1, not the multi-instance-sharing half — two concurrent instances still can't see each other's jobs (still SQLite-on-local-disk, still needs the Day-17-and-beyond managed-Postgres/object-store work for that); documented explicitly in `DEPLOYMENT.md` rather than implied as fully solved.

**Day 17 (Jul 30) — Managed Postgres for accounts. ✅ DONE (2026-07-09).** Ported `AccountStore` to Postgres behind the identical method surface (`create_account/verify/list_accounts/revoke_account/limit_for/usage_today/try_consume`) — a new `_Connection` wrapper (`service/accounts.py`) unifies sqlite3/psycopg placeholder (`?` vs `%s`) and multi-statement-script differences, so every method's SQL and row-access code stays the same for both backends. `PBICOMPASS_DB` URL scheme selects the backend: a `postgres://`/`postgresql://` URL routes to Postgres (lazy-imported `psycopg`, new `postgres` extra — `pip install "pbicompass[postgres]"`); anything else (including the `:memory:` test default and a plain file path) stays sqlite, unchanged. _Done-when:_ accounts/keys/quotas survive redeploy (already true via the sqlite path since Day 16's persistence pattern; Postgres adds the *multi-instance-shared* half); both backends tested — `tests/test_accounts_postgres.py` (6 tests: URL-scheme detection, a clear install-message `RuntimeError` when `psycopg` is missing, and the full create/verify/list/revoke/quota lifecycle exercised end-to-end against a fake-but-real-SQL-backed `psycopg` module, mirroring the existing fake-SDK test pattern in `test_agents.py`). **Honest scope note:** this closes accounts' half of the multi-instance constraint, not jobs' — `PBICOMPASS_JOBS_DB` is still sqlite-single-instance until it gets the same swap (documented in `DEPLOYMENT.md`, not implied solved).

**Day 18 (Jul 31) — Async worker (Celery+Redis or managed). ✅ DONE (2026-07-09).** Added `service/celery_app.py`: a Celery app (`PBICOMPASS_BROKER_URL`/`PBICOMPASS_RESULT_BACKEND`) and a thin `process_job_task` that reconstructs its own `JobStore`/`JobSandbox` from plain paths (a Celery task's args cross the broker as JSON, not live Python objects) and calls the exact same, already-queue-agnostic `process_job` the inline `BackgroundTasks` path calls directly — no change to job-processing logic itself. New `PBICOMPASS_QUEUE` env var (`inline` default / `celery`) selects the dispatch path in `app.py::create_job`; a file-backed job store is required in `celery` mode (an in-memory store would silently strand every job at "queued" forever across processes) and is enforced with a clear 500 rather than a silent hang. New `queue` extra (`celery`, `redis`). _Done-when:_ jobs complete regardless of request-driven CPU windows — proven by driving the real `/jobs` → poll → download flow through the Celery dispatch path using Celery's own `task_always_eager` mode (the real `celery` package, installed for this work, running the task synchronously with no broker needed — not a faked API); watchdog still bounds stalls — unchanged, since `sweep()`'s force-fail logic lives in `JobStore` and runs identically regardless of which executor calls `process_job`. **Honest scope note:** no live Redis/real-worker-process smoke test (no Redis server in this sandbox, flagged like every prior day's provider/browser gaps); a shared filesystem between API and worker processes is required for the sandbox directory (documented in `DEPLOYMENT.md`, same constraint the jobs DB already has).

**Day 19 (Aug 3) — Observability. ✅ DONE (2026-07-09).** New `service/logging_config.py`: every log line is one JSON object (timestamp/level/logger/message/`request_id`/`job_id`) — deliberately excludes raw exception text/tracebacks, recording only the exception's *type name* (matches the `type(exc).__name__`-only convention already used elsewhere in this service, and this project's standing "content-free message only" contract on `Job.error`). `request_id` is set per-HTTP-request by a new middleware in `app.py`; `job_id` is set explicitly inside `process_job` itself (`worker.py`) so it's correct regardless of executor — inline `BackgroundTasks`, a Celery worker in a separate process, or the CLI. New `service/sentry_config.py`: `init_sentry()`, off unless `SENTRY_DSN` is set (new `observability` extra, lazy-imported), with `send_default_pii=False`, `include_local_variables=False`, `include_source_context=False`, and a `before_send` hook scrubbing every exception's message down to its type name. `/healthz` is now a real readiness check — `{"ok": bool, "checks": {"jobs_db": bool, "accounts_db": bool (if configured), "queue": bool}}`, 503 when any check fails; the broker reachability check (`PBICOMPASS_QUEUE=celery` only) runs in a background thread with a hard 1.5s wall-clock deadline rather than trusting the driver's own socket timeout. _Done-when:_ a failed job produces a traceable, content-free log + Sentry event — proven directly: `tests/test_logging_config.py::FailedJobProducesTraceableContentFreeLogTest` runs a real failing job through `process_job` and asserts every log line during it carries the job's id and a planted "secret" string never appears anywhere in the log stream; `tests/test_sentry_config.py` proves the same for a captured Sentry event using the real `sentry_sdk` package with a fake in-memory transport (no network, no real DSN needed).

**A genuine leak vector found and fixed while building the Sentry test itself:** Sentry's default `include_source_context=True` attaches the literal source-code lines surrounding an exception's frame — a value embedded directly in an f-string on that line would be captured as source text even with `include_local_variables=False` (which only suppresses runtime variable *values*, not the literal source text around the frame). Caught by the test itself (it failed until `include_source_context=False` was added), not by inspection — exactly the kind of subtle, second-order leak this system is meant to guard against.

**Day 20 (Aug 4) — Metrics, rate limiting, secrets, backups. ✅ DONE (2026-07-09).** New `service/metrics.py` (`MetricsRegistry`, injectable clock) tracks jobs/min, failure rate, a token-count "cost/job" proxy (avg input/output tokens + LLM calls per job — deliberately not a dollar figure, since per-token pricing varies by provider/model and would go stale silently), and 429 rate (quota-429s vs. rate-limit-429s counted separately, summed as `http_429_total`). Wired into `JobStore` (optional `metrics=` param, `None` by default so every pre-existing `JobStore()` test call site is unaffected) at its three lifecycle call sites — `create`, `mark_done`, `mark_failed` — plus the watchdog force-fail path inside `sweep()` (via the UPDATE's `cursor.rowcount`). New `GET /metrics` (JSON default, `?format=prometheus` for a hand-rolled Prometheus text exposition — no new dependency), gated by the same `PBICOMPASS_ADMIN_TOKEN` as `/admin` (a scrape config can supply the header too). New `service/ratelimit.py` (`RateLimiter`, sliding-window per-key, injectable clock) enforces `PBICOMPASS_UPLOAD_RATE_LIMIT`/`PBICOMPASS_UPLOAD_RATE_WINDOW_SECONDS` (default 20/60s) on `POST /jobs`, checked *before* auth/quota resolution so it also protects an open, unauthenticated deployment. **Secrets:** no code changes needed (already all env-var-based) — new DEPLOYMENT.md "Secrets management" section enumerates every actual secret and where to put it in a platform's secret store, backed by a new regression test (`tests/test_logging_config.py::SecretsNeverLoggedTest`) that drives a real wrong-admin-token attempt and a real failing job carrying a BYOK provider key through the app and asserts neither secret ever reaches the log stream — operationalizing "no secret in image/env" as an executable check, not just a documentation claim. **Backups:** new `AccountStore.dump()`/`.restore()` (a portable, stdlib-only logical snapshot/upsert-restore that works identically against the SQLite or Postgres backend, no `pg_dump`/`pg_restore` client binaries required) plus `service/db_backup.py`'s file wrapper and new `pbicompass account backup --out .../account restore --in ...` CLI subcommands — positioned in DEPLOYMENT.md as the actual restore-drill mechanism alongside (not instead of) a managed Postgres provider's own automated snapshots. Also fixed a real, unrelated `.env.example` gap found while touching the file: Day 18's Celery queue vars (`PBICOMPASS_QUEUE`/`PBICOMPASS_BROKER_URL`/`PBICOMPASS_RESULT_BACKEND`) were never actually added to it despite that day's own notes claiming so — added now. _Done-when:_ `/metrics` reports live jobs/min, failure rate, cost/job, and 429 rate (dashboards can be pointed at it via the Prometheus format); the restore drill passes end-to-end against both backends (SQLite directly, Postgres via the same fake-`psycopg`-module technique Day 17 established) — proven, not just documented; no secret appears in a log line, proven by a real regression test rather than asserted by design intent alone. **Honest gap:** no live Postgres/Prometheus/Grafana instance in this sandbox to smoke-test the real dashboard/scrape path or a genuine `pg_dump`-vs-this-tool comparison — same class of gap flagged on Days 17–19.

**Sprint 4 outcome:** infra score 42 → ~80; the service survives restarts and scales past one instance.

---

## Sprint 5 — Standard SaaS auth (Aug 5–11 · Days 21–25)

**Day 21 (Aug 5) — User model + password auth. ✅ DONE (2026-07-09).** New `users` (id/email/password_hash/email_verified/created_at), `memberships` (user↔account, role, sets up teams in §8), and `sessions` (hashed opaque token + csrf_token + expiry) tables added to `AccountStore` (`accounts.py`) — same Postgres/SQLite-agnostic `_Connection` surface every existing method already uses, no new dialect fork. **Password hashing uses stdlib `hashlib.scrypt`** (new `service/passwords.py`), not argon2/bcrypt as literally named — a deliberate substitution documented at the point of the deviation (same precedent as Day 6 resolving a roadmap contradiction): adding a mandatory third-party dependency to a core, non-optional auth path would break this project's standing "stdlib-core, everything else a lazy extra" architecture; scrypt is in the same security class and has shipped in the stdlib since Python 3.6. New `POST /auth/signup` (creates user + a new owned account/tenant + an API key, auto-logs in), `POST /auth/login` (brute-force-locked out after 8 failures via a reused `AdminGuard` instance, identical 401 for "no such user" and "wrong password" so failures can't enumerate emails), and `POST /auth/logout` (CSRF-protected) — all rate-limited per-IP via a second `RateLimiter` instance separate from Day 20's upload limiter. Sessions are `HttpOnly`/`Secure`/`SameSite=Lax` cookies verified by hash (same reasoning as an API key); a parallel non-`HttpOnly` CSRF cookie backs the double-submit check required on state-changing session-authenticated requests. **Scope line honestly held at exactly what Day 21 asks:** session cookies are not yet accepted by `resolve_tenant`/`POST /jobs` (only the API key signup also returns works there today) — deferred to the account-dashboard/upload-UI work (Days 24-25), which is also where the CSRF story for `/jobs` itself gets decided; `email_verified` exists on the schema but isn't enforced or emailed (Day 22). _Done-when:_ a new user can register and log in — verified end-to-end via `tests/test_user_auth.py`; the existing Bearer-API-key path is provably unchanged (`test_api_key_path_is_completely_unchanged` signs up a user, then drives `/me` with the API key that signup itself returned).

**Day 22 (Aug 6) — Email flows. ✅ DONE (2026-07-10).** New `service/email.py` — a transactional-email layer built on **stdlib `smtplib`** (same "prefer stdlib over a vendor SDK" judgment as Day 21's scrypt choice): every transactional provider (Resend/Postmark/SES) exposes SMTP, so `smtplib` reaches all of them with **zero new dependencies**. Backends selected by `PBICOMPASS_EMAIL_BACKEND`: `console` (default — *logs* the verify/reset link so the whole flow works on a bare self-host with no provider), `smtp` (real delivery via `PBICOMPASS_SMTP_*`, with a safe fallback to console if host/from are unset), and an injectable `MemoryEmailBackend` for tests. **Content-free w.r.t. report data by construction** — an email only ever carries an auth link + the recipient's own address; a transient SMTP error is logged (type name only) and swallowed so a mail hiccup can't fail the auth request. New `email_tokens` table + `AccountStore` methods (`create_email_token`/`consume_email_token` — single-use, hashed, expiring, a fresh token invalidating the prior one of its purpose; `mark_email_verified`; `set_password` — which also invalidates every existing session for that user; `get_user`). New routes: `GET /auth/verify?token` (one-click, returns a minimal HTML result page), `POST /auth/reset-request` (**always 200**, enumeration-safe — email only sent if the user exists), `GET /auth/reset?token` (a minimal landing form) + `POST /auth/reset` (accepts JSON or form; consumes token, sets password, kills sessions). Signup now sends a verification email. All new routes go through the existing per-IP auth rate limiter. **Unverified-user gate:** new `PBICOMPASS_REQUIRE_EMAIL_VERIFICATION` (off by default so a self-host isn't locked out pre-email-config); when on, an unverified login is refused (403) *after* credential validation (not an enumeration vector) and a fresh verification link is auto-re-sent so the user isn't dead-ended. _Done-when:_ verify + reset work end-to-end — proven by `tests/test_email_auth.py` (signup → pull the emailed link from the `MemoryEmailBackend` → `GET /auth/verify` → user verified; reset-request → emailed link → `POST /auth/reset` → old password fails, new works, sessions invalidated), including single-use tokens, expiry, enumeration-safety, the form-post path, and the unverified-login gate (blocked → resent → verify → login succeeds). **Scope note:** the verify result page and reset form are intentionally minimal unstyled HTML (just enough to make emailed links usable); the branded account UI is Day 25.

**Day 23 (Aug 7) — "Sign in with Microsoft" (OIDC). ✅ DONE (2026-07-10).** New `service/oidc.py` — a standard OIDC **authorization-code + PKCE** flow against Microsoft Entra ID, **zero new dependencies** (same architecture call as Days 21/22): the token exchange is a stdlib `urllib.request` POST over verified TLS, and the ID token's claims are read by base64url-decoding the payload rather than JWKS signature verification — spec-sanctioned for this flow (OIDC Core §3.1.3.7: a confidential client that gets the token by direct TLS server-to-server call to the token endpoint may rely on TLS instead of signature checking). We still validate audience, expiry, anti-replay `nonce`, and issuer (exact for single-tenant, shape+`tid` for multi-tenant `common`/`organizations`/`consumers`). New `oidc_states` table + `AccountStore.create_oidc_state`/`consume_oidc_state` (single-use, hashed, expiring — stores the per-flow nonce + PKCE verifier server-side, keyed by the `state` param that also gives the redirect CSRF protection) and `get_or_create_sso_user` (links to an existing account **by email** — a password user who later uses SSO lands in the same account, now email-verified; a new SSO user gets the same tenant/account/API-key setup with a random unusable password, `email_verified` already true since the IdP verified it). Routes: `GET /auth/oidc/login` (mints state/nonce/PKCE, 302s to Entra's authorize endpoint) and `GET /auth/oidc/callback` (validates state, exchanges the code, validates claims, find-or-creates the user, opens a session, 302s home) — both rate-limited, and **404 when OIDC isn't configured** so an install that never sets `PBICOMPASS_OIDC_*` sees no new surface. Config via `OIDCConfig.from_env()` (client id/secret + a redirect URI derived from `PBICOMPASS_PUBLIC_URL`), injectable into `create_app`. _Done-when:_ a Microsoft account can sign in and map to a user — proven end-to-end by `tests/test_oidc.py` (drives `/auth/oidc/login` for a real state row, then the callback with a monkeypatched token exchange returning a crafted id_token echoing the flow's own nonce → user created + verified, session cookie set, redirect home; plus account-linking, state single-use/CSRF rejection, nonce-mismatch rejection, provider-error page, and 404-when-disabled). **Honest gap:** no live-Entra smoke test (no tenant in the sandbox) — the crafted-token stand-in exercises the real validation path, but a one-time real sign-in against an actual app registration is still owed, and JWKS signature verification is a documented optional add-on left out by design.

**Day 24 (Aug 10) — Account dashboard. ✅ DONE (2026-07-10).** New session-authenticated dashboard at **`/app`** (no admin token) — the end-user replacement for the shared-admin-token flow. **Real per-account API-key management:** introduced an `api_keys` table (multiple keys per account) as the authoritative store `verify()` now consults, with a zero-migration idempotent **backfill** of every existing account's key at startup and dump/restore extended to carry keys (snapshot `version`→2, v1 still restores). `AccountStore` gained `create_api_key`/`list_api_keys`/`revoke_api_key` (scoped to the owning account, soft-capped) and `revoke_account` now clears an account's keys too; `JobStore.list_for_tenant` returns a tenant's recent jobs (status/timestamps only — zero-retention preserved). New `_require_user` resolves the signed-in user from the session cookie (distinct from the API-key `resolve_tenant`), backing the dashboard API: `GET /app/api/config` (public, tells the sign-in view whether to show the Microsoft button), `GET /app/api/me` (plan + usage/quota/remaining), `GET/POST /app/api/keys` + `DELETE /app/api/keys/{id}` (CSRF-guarded, new key shown once), `GET /app/api/jobs`. The `/app` page (`static/app.html`) is a self-contained sign-in-or-dashboard view (email/password + "Sign in with Microsoft" when OIDC is on) so it needs no separate login page. _Done-when:_ a user self-serves keys and sees usage without the admin token — proven by `tests/test_dashboard.py` (signup → `/app/api/me` shows plan/usage → create a key via the dashboard → that key authenticates the real `/jobs` API-key path → revoke it → it stops mapping to the account; plus job-history tenant scoping, two-user key isolation, CSRF enforcement, session gating, and the key store's create/revoke/cap/cross-account-isolation/backfill/dump-restore behavior). **Scope note:** `/app` is deliberately functional-but-plain; the branded signed-in product surface (and the upload page itself) is Day 25.

**Day 25 (Aug 11) — Upload UI → product UI + auth tests.** Signed-in `index.html` with plan/quota badge and recent jobs; §10 auth/security tests (CSRF, fixation, isolation). _Done-when:_ signed-in upload flow works; auth tests green.

**Sprint 5 outcome:** SaaS surface 38 → ~70; a stranger can create an account and generate docs.

---

## Sprint 6 — Billing (Aug 12–14 · Days 26–28)

**Day 26 (Aug 12) — Stripe products + Checkout.** Prices for free/pro/enterprise; Checkout session from the plan picker; customer created on signup. _Done-when:_ a test purchase upgrades the account's `plan`.

**Day 27 (Aug 13) — Webhooks + portal.** Handle `checkout.session.completed`, `customer.subscription.updated/deleted`, payment failure → downgrade; Stripe customer portal for self-serve plan/cancel; tie to existing quota enforcement. _Done-when:_ subscription changes sync to `plan`; §10.4 webhook tests green.

**Day 28 (Aug 14) — Billing hardening + pricing page.** Idempotent webhooks, dunning email, receipts; pricing page reflects real quotas. _Done-when:_ duplicate webhooks are safe; a failed renewal downgrades and notifies.

**Sprint 6 outcome:** the money path is closed; free→pro self-serve works. SaaS surface → ~82.

---

## Sprint 7 — Enterprise foundations, Ask, design, launch (Aug 17–28 · Days 29–38)

**Day 29 (Aug 17) — Orgs/teams/RBAC foundations.** Promote tenant→organization; roles owner/admin/member; member invites; RBAC decorator over `resolve_tenant`. _Done-when:_ an org owner invites a member who sees shared jobs per role.

**Day 30 (Aug 18) — Audit logging + seat model.** Append-only content-free action log (`audit_log.py`); enterprise seat count synced to Stripe subscription quantity. _Done-when:_ actions are logged (no report data); seats enforced.

**Day 31 (Aug 19) — AI-Native Phase 5: "Ask about this report" (part 1).** `POST /api/jobs/{id}/ask`; retain `model.json` internally; single LLM call over digest+question; citations; plan-gated + question quota. _Done-when:_ a pro user asks a question and gets a cited answer within TTL; TTL-expiry → 404.

**Day 32 (Aug 20) — Phase 5 (part 2): chat UI + CLI parity.** Chat box on the result page; `pbicompass ask` CLI; zero-retention verified (no server-side history). _Done-when:_ end-to-end ask works in browser + CLI; logs content-free.

**Days 33–35 (Aug 21, 24, 25) — `index.html` professional redesign (dedicated design push, §6.3, owner-requested).**
- _Day 33:_ product **landing / upload page** (`service/static/index.html`) — value prop, four-doc preview, trust signals (zero-retention/metadata-only), signed-in state + pricing CTA (auth/billing now exist).
- _Day 34:_ per-job **documentation hub** (`render/hub.py`) — deliverable-grade cover: title, generated date, health chip + top-3 findings, document cards, breadcrumbs, branding.
- _Day 35:_ cross-surface design QA — landing, hub, and all four docs read as one system in light+dark; responsive/mobile check. **Re-enable the wireframe here too** (uncomment `html.py:456-457`, `user_guide.py:146-147` — hidden since 2026-07-08 at owner request, deferred to this exact design push per that same request) and give it a final pass alongside the landing/hub work, so every visual surface lands in one coherent design sweep instead of the wireframe shipping separately/earlier.
- _Done-when:_ a first-time visitor understands the product in 10 seconds and a shared hub link reads like a Big-4 cover sheet; design score → ~93.

**Day 36 (Aug 26) — Full test sweep.** Integration (signup→pay→generate→ask), e2e browser smoke, billing-webhook, zero-retention regression — all in CI as deploy gates. _Done-when:_ all P0/P1 tests green; CI blocks on red.

**Day 37 (Aug 27) — Load test + performance + security review.** k6 at launch concurrency; capture p95 job time + cost/job; run `/security-review`; a11y/mobile pass (§6.5). _Done-when:_ load target met; no High security findings open.

**Day 38 (Aug 28) — Deployment readiness + launch checklist.** Staging→prod promotion, live Stripe keys + one real purchase/refund, legal pages, monitoring/alerts confirmed, backups verified, runbook updated. _Done-when:_ the §13 launch checklist is fully checked.

**Aug 31 — Beta launch** to a 5–10 user friendly cohort. Public announcement after 1 week of clean beta telemetry.

---

## 14.1 Post-launch fast-follow (September)

- Full **SAML 2.0 + SCIM** enterprise SSO (design already in place from §7.3/§8.5).
- **Score-trend / diff** (enrichment round-trip; `DOCUMENTATION_QUALITY_PLAN` item 4.5/5.2) — the "moat" feature, scoped after launch telemetry.
- Effort/model A/B tuning (the deferred Phase 0/2 cost optimizations) once real usage data exists.

## 14.1a Marketing & Growth (fast-follow, post-launch)

Ideas surfaced during the 2026-07-10 landing-page content pass, deliberately **not** built as part of that copy-only work (each needs new pages, backend, or a content pipeline — out of scope for a messaging rewrite):

- **ROI calculator** — interactive "upload N measures → estimated hours saved" widget on the landing page. Needs real usage telemetry first (§11) to back the estimate honestly; premature before that data exists.
- **Blog / SEO content program** — targeting "Power BI documentation," "Power BI governance," "semantic models," "Fabric," "DAX" keywords. Needs a content pipeline/CMS decision, not a landing-page edit.
- **Dedicated Enterprise page** — security, compliance, architecture, and deployment detail for enterprise buyers, split out from the main landing page once there's enough of each to justify a separate page.
- **Public API documentation site** — once the `/jobs` API and auth are stable enough to commit to a public contract.
- **Email waitlist / beta-capture flow** — a real capture mechanism (form + backend + list) for pre-signup interest, distinct from the generator's existing free/no-signup path, which already lets a visitor try the product today without waiting for anything.

## 14.2 Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Timeline is aggressive for one engineer | Slip | Sprints 1–4 are the non-negotiable core; Ask (S7) and enterprise SSO are deferrable without blocking beta. |
| Stripe/webhook edge cases | Billing bugs | Test-mode fixtures + idempotency (Day 27–28); soft-launch billing to beta only. |
| LLM cost at scale | Margin | Phase 0 dedupe + effort tiers already in; add per-job cost telemetry (Day 20) before opening the free tier wide. |
| Zero-retention regression during the stateful rewrite (S4) | Trust/compliance | Zero-retention CI test runs on every S4 change (§9). |
| Multi-tool handoff drops context | Rework | End each sprint verified; this doc + the two plans are the handoff contract. |

---

## Bottom line

The engine and the design are already good. The work to launch is **(1)** the six output-defect fixes (D1–D6) that restore credibility, **(2)** the AI-maximal depth — cross-provider max reasoning, a whole-document senior-reviewer pass, and the Phase-4 synthesizer — that makes it consultant-grade, **(3)** the stateful/observable infrastructure it currently lacks, **(4)** the auth+billing surface that turns a tool into a SaaS, and **(5)** a dedicated design push on the landing + hub. Sequenced above, that's a realistic **~7.5-week path to a paid public beta (~Aug 31)**, with enterprise SSO and the diff/trend moat as September fast-follows — all without departing from the existing product vision.
