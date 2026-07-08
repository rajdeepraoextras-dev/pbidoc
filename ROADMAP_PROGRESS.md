# Production Roadmap — Progress Tracker

Tracks execution against `PRODUCTION_ROADMAP.md` §14 (Day-by-Day Execution Plan), day by day. Update this file at the end of each day/session so a handoff (Claude ↔ Antigravity/Gemini) always has an accurate "what's actually done" record instead of relying on the plan document alone (the plan describes *intent*; this file records *reality*).

Status legend: ✅ Done · 🔶 Partial · ⬜ Not started

---

## Sprint 1 — Output credibility (Jul 8–14 · Days 1–5)

| Day | Date | Task | Status |
|---|---|---|---|
| 1 | Jul 8 | Executive Summary editorial fix (D1) | ✅ **Done** |
| 2 | Jul 9 | Output sanitation + anti-punt guard (D2/D6) | ✅ **Done** |
| 3 | Jul 10 | Grounding sentence-granularity fix (D3) | ✅ **Done** |
| 4 | Jul 13 | Field-selector regression fix (D4) | ✅ **Done** |
| 5 | Jul 14 | Full regen + QA + print pass | ✅ **Done** |

---

## Day 1 (Jul 8) — AI-Native Phase 1: Executive Summary editorial fix

**Objective:** exec doc reads for a business owner, not an auditor (fixes D1: audit-speak in the maintenance note, the completeness nag in "What's Next", and empty Steward/Classification rows).

### Task checklist

- [x] Extend `EXECUTIVE_WRITER_SCHEMA` with `reframed_risks` — [io.py:454-476](src/pbicompass/agents/io.py#L454-L476)
- [x] Pass full recommendation objects (rule_id/severity/consequence/ask) to the Executive Writer, not just a flattened string — [executive.py:297-300](src/pbicompass/agents/generators/executive.py#L297-L300)
- [x] Add `EXEC_STYLE_RULES` banning IT-governance vocabulary in the exec doc — [io.py:443-449](src/pbicompass/agents/io.py#L443-L449)
- [x] Rewrite `_maintenance_note` to plain language — [executive.py:162-167](src/pbicompass/agents/generators/executive.py#L162-L167)
- [x] Remove the completeness % block from `_next_steps`; emit it as a job warning instead — [executive.py:169-186](src/pbicompass/agents/generators/executive.py#L169-L186)
- [x] Conditional Ownership rows (Steward/Classification hidden when unset) in all 3 renderers (md/html/docx) — [render/executive.py](src/pbicompass/render/executive.py)

### Deliverable

- [x] New exec doc logic lands in md/html/docx (`render/executive.py`), all three renderers updated consistently.
- [x] Golden snapshot regenerated: `tests/fixtures/golden/executive.html` (reviewed diff before accepting — see note below).

### Done-when (from the roadmap)

- [x] Grep of rendered exec doc finds **none** of `{"governance finding", "best practice", "% complete", "fields still need"}` — verified against the regenerated golden file, clean.
- [x] Steward/Classification rows absent when unset (Owner still shown, with "not specified" fallback).

### Test coverage added (beyond the roadmap's minimum bar)

- [x] `test_generators.py::ExecutiveGeneratorDeterministicTest` — new tests asserting no completeness nag in `next_steps`, no governance/audit jargon in `maintenance_note`.
- [x] `test_generators.py` — `test_incomplete_metadata_surfaces_as_a_warning_not_doc_content` (confirms the completeness info moved to `on_warning`, not deleted).
- [x] `test_generators.py::ApplyReframedRisksTest` — new class covering `_apply_reframed_risks`: matching-count application, mismatched-count no-op (safety net against a malformed LLM response), `None` no-op.
- [x] `test_generators.py::ExecutiveGeneratorLlmTest::test_llm_prose_is_used` — updated to verify severity/rule_id stay deterministic while consequence/ask get reframed.
- [x] `test_render.py` — new `test_unset_steward_and_classification_rows_are_omitted` in both the markdown and HTML render test classes.
- [x] Full suite run: **365 passed**, 0 new failures.

### Known pre-existing (not Day 1 scope, not touched)

5 failures remain in the suite, all traced back to the prior `56f2788` commit ("Hide SVG diagrams and wireframes...") and confirmed present on `main` **before** today's changes (verified via `git stash`):
- `test_golden_html.py` — `audit`, `technical`, `user_guide` snapshots (stale vs. the SVG/font change; `executive.html` was updated since it's this task's own output).
- `test_render.py::HtmlRenderTest::test_accessibility_landmarks_present` and `test_interactive_diagram_nodes_and_edges` (model-diagram markup currently commented out).

These belong to the Sprint 3 wireframe/lineage reintroduction work (§5.1/§5.2, Days 12–14), not Day 1 — left as-is to avoid scope creep.

### Files touched

- `src/pbicompass/agents/io.py`
- `src/pbicompass/agents/generators/executive.py`
- `src/pbicompass/render/executive.py`
- `tests/test_generators.py`
- `tests/test_render.py`
- `tests/fixtures/golden/executive.html` (regenerated)

**Verdict: Day 1 is fully done** — all roadmap tasks, the stated deliverable, and the done-when grep/row criteria are satisfied and guard-tested.

---

## Day 2 (Jul 9) — Output sanitation + anti-punt guard (D2/D6)

**Objective:** no LLM meta-commentary in prose or definitions (D2); no over-applied "requires business confirmation" on obvious/relationship-participating columns (D6).

### Task checklist

- [x] Deterministic meta-commentary validator (D2) — new module [sanitize.py](src/pbicompass/agents/sanitize.py) with `is_meta_commentary` (rejects `^(Consider|Remove|Verify|Ensure|Add a|Provide)\b`, `glossary\[`, `plain_definition`, `the duplicated entry`) and `is_punt_phrase` (rejects empty/"requires business confirmation" text).
- [x] Wired the guard into the **one choke point every generator's critic + grounding results pass through** — [critic.py::apply_results](src/pbicompass/agents/critic.py#L166-L182) now rejects a meta-commentary replacement and keeps the prior text, covering `_narrative_triples` in all four generators (technical/executive/user_guide/audit) with a single change instead of four.
- [x] D6 "AI may only improve, never downgrade" merge policy — [technical.py::_column_descriptions](src/pbicompass/agents/generators/technical.py#L225-L286): the Column Describer's result is discarded (keeping the deterministic description) whenever it's empty, meta-commentary, or a punt phrase.
- [x] Broadened deterministic derivation (D6 fix 2) — new [_related_tables](src/pbicompass/agents/generators/technical.py#L225-L236) helper: any column participating in a relationship now gets "Join key linking {table} to {related}." even without an `*Id`/`*Key` name, not just the naming heuristic.
- [x] Softened terminal wording (D6 fix 3) — a genuinely roleless column (no relationship, not calculated, no ID/Key name) now renders "No description set." instead of the alarming "Unknown — requires business confirmation."; `_infer_glossary`'s dimension lookup updated to treat both as non-definitions.
- [x] Same anti-punt merge policy applied to the measure catalog — [technical.py::_measure_catalog](src/pbicompass/agents/generators/technical.py#L385-L429): a punted/meta-commentary `plain_english`/`calculation_logic`/`caveats` from the DAX Translator falls back to the deterministic `translate_dax` gloss instead of shipping an empty or punt sentence.
- [x] Same D2/D6 guard applied to the Business User Guide's glossary — [user_guide.py::_build_glossary](src/pbicompass/agents/generators/user_guide.py#L71-L96): a punted/meta-commentary DAX Translator result is never used as a glossary definition.
- [x] Softened `io.py` prompts so the model states a structural fact instead of only punting — [STYLE_RULES](src/pbicompass/agents/io.py#L21-L29), [DAX_TRANSLATOR_SYSTEM](src/pbicompass/agents/io.py#L193-L204), [COLUMN_DESCRIBER_SYSTEM](src/pbicompass/agents/io.py#L342-L351).

### Deliverable

- [x] Clean glossary + column/measure descriptions that no longer punt on relationship-participating or otherwise structurally-known columns, across md/html/docx (shared by all three renderers since the fix lives in the generator, not the renderer).
- [x] New guard module + tests: [test_sanitize.py](tests/test_sanitize.py) (13 tests), plus guard tests wired into [test_critic.py](tests/test_critic.py), [test_agents.py](tests/test_agents.py) (`AntiPuntGuardTest`, 5 tests against a hand-built model with a non-`*Id`-named relationship column), and [test_generators.py](tests/test_generators.py) (`BusinessGuideGlossaryAntiPuntTest`).
- [x] Golden snapshots regenerated (`PBICOMPASS_UPDATE_GOLDEN=1`) and diffs reviewed line-by-line before accepting: `technical.html`'s data dictionary now shows "No description set." instead of the punt phrase, `audit.html`/`user_guide.html` diffs are pre-existing CSS/wireframe drift only (see note below), unrelated to today's change.

### Done-when (from the roadmap)

- [x] The D2 strings (`Consider providing…`, `Remove the duplicated entry…`, `glossary[…].plain_definition`, etc.) can no longer appear in a rendered doc — enforced at the shared `apply_results` choke point.
- [x] The sample's join-key columns (`CustomerKey`, `OrderDateKey`, `ShipDateKey` in the fixture) render real deterministic descriptions, never the punt phrase.
- [x] The "requires business confirmation" phrase is bounded and never appears on a relationship-participating column — verified by `test_no_column_ever_renders_the_punt_phrase` and confirmed absent from the regenerated `technical.html` golden.

### Known pre-existing (not Day 2 scope, not touched)

- `test_render.py::HtmlRenderTest::test_accessibility_landmarks_present` / `test_interactive_diagram_nodes_and_edges` — same 2 failures noted on Day 1, traced to the model-diagram markup commented out in `56f2788`; Sprint 3 scope.
- Regenerating the goldens surfaced a **separate, pre-existing CSS/style drift** unrelated to Day 1 or Day 2: the checked-in `audit.html`/`technical.html`/`user_guide.html` fixtures predate the SVG-wireframe/font-fix commits (`306880e`, `c12a786`) and the SVG-hiding commit (`56f2788`) — none of those commits regenerated all four golden files. Confirmed by isolating the diff: `audit.html`'s entire diff is CSS/style-only (no content change, since Day 2 never touches `audit.py`); `technical.html`'s and `user_guide.html`'s diffs are the CSS drift plus exactly the intended Day 2 content changes (verified by grepping the diff with the CSS/style lines excluded). Left in the regenerated goldens since reverting it would just leave the tests failing again for an unrelated, already-latent reason — but flagging here so Sprint 3's wireframe/lineage work knows the goldens are now current.

### Files touched

- `src/pbicompass/agents/sanitize.py` (new)
- `src/pbicompass/agents/critic.py`
- `src/pbicompass/agents/io.py`
- `src/pbicompass/agents/generators/technical.py`
- `src/pbicompass/agents/generators/user_guide.py`
- `tests/test_sanitize.py` (new)
- `tests/test_critic.py`
- `tests/test_agents.py`
- `tests/test_generators.py`
- `tests/fixtures/golden/{audit,technical,user_guide}.html` (regenerated)

**Verdict: Day 2 is fully done** — D2 and D6 are fixed at their real root cause (the merge point where an LLM result would overwrite a good deterministic value), guard-tested, and verified against the regenerated golden HTML with no unintended regressions.

---

## Day 3 (Jul 10) — Grounding sentence-granularity fix (D3)

**Objective:** grounding never produces mid-sentence "Unknown — requires business confirmation." splices (fixes D3: `audit.md` line 8's *"However, Unknown — requires business confirmation., are aspects that need attention, whereas Unknown — requires business confirmation.."*).

### Root cause

`grounding.py::apply_grounding_pass`'s `unverifiable` branch did `current.replace(quote, UNVERIFIABLE_TEXT)` — a bare substring replace. `UNVERIFIABLE_TEXT` already ends in its own full stop, so whenever the flagged `quote` was an internal clause (more sentence text followed it, e.g. a comma-separated clause) rather than the tail of the sentence, splicing it in place produced a full stop butted against whatever came next (`.,`) — grammatically broken, and if two claims landed in the same sentence, doubly so.

### Task checklist

- [x] Added sentence-splitting + "does the claim reach the end of its sentence" classification — [grounding.py:92-134](src/pbicompass/agents/grounding.py#L92-L134) (`_split_sentences`, `_replace_unverifiable_claim`).
- [x] Changed the `unverifiable` branch to route through the new helper instead of a bare `str.replace` — [grounding.py:188-190](src/pbicompass/agents/grounding.py#L188-L190).
- [x] Behavior: if the claim runs to the end of its sentence, inline substitution with `UNVERIFIABLE_TEXT` is kept (reads fine, matches the pre-existing `test_unverifiable_claim_is_downgraded` contract). If the claim is a mid-sentence clause, **the whole sentence is dropped** instead of substituted in place (per the roadmap's explicit fix direction), and the remaining sentences in the field are kept intact. If dropping the sentence would empty the field entirely (it was the field's only content), falls back to the standalone `UNVERIFIABLE_TEXT` sentence rather than leaving it blank.
- [x] Added the audit-narrative case as a test fixture — [test_grounding.py](tests/test_grounding.py): `test_unverifiable_mid_sentence_claim_drops_whole_sentence`, `test_audit_narrative_two_mid_sentence_claims_collapse_cleanly` (reproduces the exact two-claims-in-one-sentence D3 production bug and asserts no `.,` / no stray `UNVERIFIABLE_TEXT` survives), `test_unverifiable_claim_spanning_the_whole_sentence_falls_back_to_the_convention_text` (empty-after-drop edge case).
- [x] Added an end-to-end wiring test through the real `TechnicalDocumentationGenerator` (not just the unit-level helper) — `GroundingMidSentenceWiringTest::test_mid_sentence_unverifiable_claim_drops_whole_sentence_not_a_splice` in [test_grounding.py](tests/test_grounding.py), mirroring the existing `GroundingGeneratorWiringTest` pattern used for the `contradicted` verdict.

### Deliverable

- [x] Grammatically clean grounding output for `unverifiable` verdicts, at both the unit (`apply_grounding_pass`) and generator-wiring level.
- [x] All 9 pre-existing `apply_grounding_pass` tests still pass unchanged (including the exact-string-match `test_unverifiable_claim_is_downgraded` and `test_multiple_claims_apply_in_sequence_on_the_same_location`) — the fix only changes behavior for genuine mid-clause claims, not the sentence-final case those tests cover.

### Done-when (from the roadmap)

- [x] No rendered doc contains `.,` — verified directly: the new fixture tests assert `assertNotIn(".,", rendered)` against text that, pre-fix, reproduced exactly that pattern; confirmed by temporarily reverting the fix and seeing both new fixture tests fail on the `.,`/double-period assertions before re-applying it.
- [x] No `"Unknown — requires business confirmation."` fragment mid-sentence — the mid-clause branch never inserts `UNVERIFIABLE_TEXT` into a sentence that has more content after it; it only appears now as either (a) a clean sentence-final inline substitution (pre-existing, tested case) or (b) a standalone whole-field fallback sentence.
- [x] Existing golden HTML fixtures (`audit.html`, `technical.html`, `user_guide.html`, `executive.html`) contain zero `.,` occurrences — confirmed via grep; no golden regeneration was needed since the fixture generation's `FakeLLMClient` doesn't exercise the grounding `unverifiable` path with mid-sentence claims.

### Full suite

- [x] `python -m pytest -q` — **392 passed**, 2 skipped, only the 2 known pre-existing failures remain (`test_render.py::HtmlRenderTest::test_accessibility_landmarks_present` / `test_interactive_diagram_nodes_and_edges`, both traced to the Sprint 3-scoped model-diagram markup still commented out from `56f2788` — unrelated to Day 3, not touched).

### Files touched

- `src/pbicompass/agents/grounding.py`
- `tests/test_grounding.py`

**Verdict: Day 3 is fully done** — the D3 mid-sentence splice is fixed at its root cause (granularity of the replacement, not just the wording), guard-tested at both the unit and full-generator level, and verified against the exact production bug string from `audit.md`.

---

## Day 4 (Jul 13) — Field-selector (I4) regression fix (D4)

**Objective:** no `select`/`select1` field-parameter leaks in visual titles, generated business questions, or glossaries (fixes D4). The roadmap's own claim that I4 was "fixed and test-verified on 2026-07-06" was checked against the actual `Corporate_Spend_Report` sample bundle referenced in §2.2 and found to still leak on multiple surfaces — this day root-causes and closes the real gap, not just the previously-covered one.

### Root cause

The existing I4 filter (`field_parameter_table_names()` + a `len(parts) > 1 and parts[0] in field_param_tables` check scattered across five call sites) only recognized a field-parameter reference in its fully-qualified `Table.Column` shape. Inspecting the actual sample's `model.json` and visual field lists showed Power BI's report layout sometimes emits a field-parameter projection as a **bare, unqualified token** — e.g. `fields: ["select", "select1", "Fact.Actual", "Fact.Plan"]` — because `parsers/pbir.py::_extract_fields`'s `queryRef` fallback (line 92-94) appends the raw `queryRef` string as-is, and for a field-parameter axis/legend binding that `queryRef` is just the parameter table's own name with no `Entity.Property` qualification. Worse, the parameter table itself ("select"/"select1") didn't even appear in `model.tables` for this real report, so there was no table object for the qualified-path heuristic to have recognized in the first place — only the bare token's own name (`select`, `select1`) gives it away. Every consumer's `len(parts) > 1` guard silently no-ops on a bare token, so it falls straight through as if it were a real dimension.

### Task checklist

- [x] Added a single shared predicate, `report_facts.py::is_field_selector(field, field_param_tables)` — handles both the qualified `Table.Column` shape (existing behavior) and the bare-token shape (new: matches against a resolved table name, or falls back to the same telltale-name regex used to recognize the table) — plus `FIELD_SELECTOR_LABEL = "field selector"` for the one place a selector reference has to stay visible (a real, working slicer bound to it).
- [x] `report_pages()` — dims/metrics filter now uses `is_field_selector` (was qualified-only) — [report_facts.py:125-130](src/pbicompass/agents/report_facts.py#L125-L130). Fixes visual titles/labels in the technical doc's Report Pages & Visuals table and the user guide's page visual list (was: "Actual, Plan by select, select1"; now: "Actual, Plan").
- [x] `slicers()` — a slicer legitimately bound to a field-parameter table is kept (it's a real control) but relabeled to `FIELD_SELECTOR_LABEL` instead of leaking the raw table name — [report_facts.py:206-227](src/pbicompass/agents/report_facts.py#L206-L227).
- [x] `deterministic.py::_page_questions` — same fix; excludes selector fields from generated business questions ("How is Actual distributed by select?" can no longer be generated) — [deterministic.py:226-243](src/pbicompass/agents/deterministic.py#L226-L243).
- [x] `deterministic.py::_page_theme` — previously did **no** field-parameter filtering at all; now threads `field_param_tables` through and excludes selector leaves from the "Key fields: …" page-summary text — [deterministic.py:254-267](src/pbicompass/agents/deterministic.py#L254-L267).
- [x] `deterministic.py::business_analyst_deterministic`'s navigation-guide loop — previously leaked the raw slicer field name into "use the 'select1' slicer…" nav tips with no filtering at all; now relabels via `FIELD_SELECTOR_LABEL` — [deterministic.py:313-322](src/pbicompass/agents/deterministic.py#L313-L322).
- [x] `user_guide.py::_build_glossary` — the `is_field_param` check had the identical bare-token gap (silently always `False` for a bare token), so a selector's glossary entry fell back to the generic dimension definition instead of "A field selector that switches what the chart displays."; now uses `is_field_selector` — [user_guide.py:104-121](src/pbicompass/agents/generators/user_guide.py#L104-L121).
- [x] `technical.py::_infer_glossary` — previously had **no** field-parameter filtering at all, so `select`/`select1` were added as phantom "Dimension" glossary rows that could never resolve to a real column description and always rendered the alarming "Unknown — requires business confirmation." punt (compounding D6); now excluded entirely — [technical.py:571-585](src/pbicompass/agents/generators/technical.py#L571-L585).
- [x] `render/_wireframe.py` — the wireframe SVG's own local copy of the dims/metrics filter had the same bare-token gap; fixed for consistency so a wireframe visual's anchor link (`visual_label()`-derived) never drifts out of sync with the now-fixed `report_pages()` label it must match (I3) — [_wireframe.py:238-254](src/pbicompass/render/_wireframe.py#L238-L254). Wireframes are still hidden from HTML output (Sprint 3 scope) but the SVG is still generated and stored on every page fact.

### Deliverable

- [x] One shared, correctly-generalized I4 predicate (`is_field_selector`) replacing five independent, differently-buggy copies of the same qualified-only check.
- [x] New regression coverage reproducing the exact production bug shape: `tests/test_report_facts.py::BareFieldSelectorRegressionTest` (8 tests) — unit coverage of `is_field_selector` itself, plus end-to-end coverage through `report_pages()`, `business_analyst_deterministic()` (questions, page-theme text, nav guide), `slicers()`, `TechnicalDocumentationGenerator` (glossary), and `BusinessGuideGenerator` (glossary).

### Done-when (from the roadmap)

- [x] No rendered doc contains a standalone `select`/`select1` field token — verified directly against the reproduction fixture (`_model_with_bare_field_parameter`, built from the real `Corporate_Spend_Report` sample's exact field-list shape) across every consumer path.
- [x] "How is Actual distributed by select?" cannot be generated — `test_bare_field_parameter_excluded_from_business_questions` asserts no generated question contains "select" for this fixture.

### Full suite

- [x] `python -m pytest -q` — **400 passed**, 2 skipped, only the 2 known pre-existing failures remain (`test_render.py::HtmlRenderTest::test_accessibility_landmarks_present` / `test_interactive_diagram_nodes_and_edges`, traced to the Sprint-3-scoped model-diagram markup still commented out from `56f2788` — unrelated to Day 4, not touched). Golden HTML fixtures unaffected (the checked-in `SampleSales` golden model contains no field-parameter tables, so this fix has no observable diff there); no golden regeneration was needed.

### Files touched

- `src/pbicompass/agents/report_facts.py`
- `src/pbicompass/agents/deterministic.py`
- `src/pbicompass/agents/generators/user_guide.py`
- `src/pbicompass/agents/generators/technical.py`
- `src/pbicompass/render/_wireframe.py`
- `tests/test_report_facts.py`

**Verdict: Day 4 is fully done** — the I4 field-selector recognition gap is fixed at its actual root cause (a bare-token shape the original heuristic never accounted for), applied consistently across every surface that previously leaked it independently, and guard-tested against the exact real-world reproduction shape found in the `Corporate_Spend_Report` sample cited in the roadmap's own audit (§2.2, D4).

---

## Day 5 (Jul 14) — Full regen + QA + print pass

**Objective:** verify Sprint 1 end-to-end across all 4 docs × 4 formats; lock the D1–D6 fixes into CI so they can never silently regress.

### Task checklist

- [x] Regenerated the SampleSales fixture (the repo's canonical offline sample — see note below on `Corporate_Spend_Report.zip`) as a full `--document all --bundle --provider none` bundle: all 4 doc types × md/html/docx/json.
- [x] Read through every rendered `.md` (representative of md/html/docx, since all three renderers consume the same generator output) for D1–D6 defects.
- [x] Print/PDF pass: no PDF engine (pandoc + tectonic/wkhtmltopdf/weasyprint) or browser is installed in this sandbox, so an actual rendered PDF couldn't be produced here. Verified instead at the source: the shared shell's `@media print` block ([_html_shell.py:818-881](src/pbicompass/render/_html_shell.py#L818-L881)) forces the light theme regardless of on-screen theme, hides sidebar/nav chrome, sets `page-break-before: always` on `h2` (avoided on the first), sets `page-break-inside: avoid` on `pre`/`table`/`.measure`/`.diagram`/`.card-section`, force-opens every collapsed `<details>` for print (with a no-JS CSS fallback), and renders a print-only cover page + confidentiality watermark. `test_render.py::test_print_cover_page_present` and `test_print_watermark_only_for_confidential_or_restricted` already guard the cover/watermark. **Gap:** a real browser/PDF-engine visual check (the roadmap's actual "print pass") still needs to happen once on a machine that has one — flagging honestly rather than claiming a visual check that didn't happen.
- [x] Added the §10.1 output-quality guard tests to CI: new [tests/test_output_quality_guards.py](tests/test_output_quality_guards.py) (6 tests) — parses SampleSales once, generates all 4 documents, renders each to md+html, and asserts D1 (no audit-speak in the executive doc), D2 (no `glossary[`/`plain_definition`/`the duplicated entry` artifacts in any doc), D3 (no `.,` splice, no doubled terminal punctuation), D4 (no bare lowercase `select`/`select1` token), and D6 (punt-phrase count bounded, and never on a relationship-participating column — cross-checked against the model's actual `relationships`, not just column naming). This is the holistic complement to the per-fix unit tests Days 1–4 already added.
- [x] Verified the new suite isn't vacuous: an over-broad first draft of the D2 check (`\[\d+\]\.\w+`) correctly failed on legitimate inline JS (`shown[0].anchor` in the HTML shell's search script) and had to be narrowed to `glossary\[\d+\]` — proof the test actually executes real matching logic, not a tautology.

### New defects found during the read-through (fixed today, not part of D1–D6, found by the QA pass itself)

- **`_infer_glossary` D6 residual gap** — [technical.py:602-603](src/pbicompass/agents/generators/technical.py#L602-L603): the section-14 "Data Dictionary / Glossary" glossary builder had its own, separate fallback that still defaulted a genuinely roleless dimension (no date/customer/product/region keyword match — e.g. `Segment`, `Year` in the SampleSales fixture) to `"Unknown — requires business confirmation."`, even though the section-6 data dictionary (`_column_descriptions`) was already fixed on Day 2 to say `"No description set."` for the identical case. Two different sections of the same technical doc were giving two different answers for the same column. Fixed to match the D6 policy. Guard-tested: `tests/test_agents.py::AntiPuntGuardTest::test_glossary_dimension_with_no_keyword_match_never_gets_the_punt_phrase` (confirmed it fails without the fix via `git stash`).
- **Duplicated-word typo** — "page layout **layout** tables" in the §19 Methodology & Guarantees boilerplate, hardcoded identically in [markdown.py:458](src/pbicompass/render/markdown.py#L458), [html.py:768](src/pbicompass/render/html.py#L768), and [docx.py:459](src/pbicompass/render/docx.py#L459). Fixed to "page layout tables" in all three.
- **Grammar** — same boilerplate: "zero-CDNs, zero telemetries" → "zero CDNs, zero telemetry" (a plural was applied to an uncountable noun; "CDNs" as a hyphenated adjective read oddly next to "zero"). Fixed in all three renderers.

### Deliverable

- [x] Clean, freshly-regenerated SampleSales bundle (all 4 docs × md/html/docx/json) with zero D1–D6 defects and the two new typo/glossary fixes applied.
- [x] Golden HTML snapshots regenerated (`PBICOMPASS_UPDATE_GOLDEN=1`) and diff reviewed: the diff vs. `HEAD` (nothing from Sprint 1 has been committed yet, so `git diff` shows the full cumulative Sprint 1 change) is exactly the expected D1–D6 fixes plus today's two new fixes plus the already-documented pre-existing CSS/font drift (Poppins injection, wireframe-hiding) from commits before Sprint 1 started. No unexpected regressions.
- [x] `tests/test_output_quality_guards.py` added as a permanent CI gate for D1–D6.

### Done-when (from the roadmap)

- [x] Manual read-through of `executive.md`, `audit.md`, `user-guide.md`, `technical.md` (all four, offline/deterministic) finds **zero** D1–D6 defects, plus the two newly-found and now-fixed typo/glossary issues.
- [x] Offline fallback (`--provider none`) still produces complete docs across all 4 types × md/html/docx/json — confirmed via the regenerated bundle; the CLI correctly emits the metadata-completeness note as a `warning:` on stderr (not embedded in doc content), matching Day 1's fix end-to-end through the real CLI path, not just the unit-tested generator call.

### Known gap (honest, not hidden)

- **No live Gemini smoke run.** No `GEMINI_API_KEY`/`GOOGLE_API_KEY` (or any other provider key) is configured in this environment, so the roadmap's "Gemini smoke" half of Day 5 could not be executed here — only the offline/deterministic run. This needs to happen once on a machine with a real key configured; nothing about today's changes is provider-specific (all four fixes today are in deterministic code paths), so risk is low, but it's an explicit gap, not a silently-skipped one.
- **No real PDF/browser print check.** See the print-pass note above — the print CSS was verified by reading it, not by rendering it.

### Full suite

- [x] `python -m pytest -q` — **407 passed**, 2 skipped, only the 2 known pre-existing failures remain (`test_render.py::HtmlRenderTest::test_accessibility_landmarks_present` / `test_interactive_diagram_nodes_and_edges`, traced to the Sprint-3-scoped model-diagram markup still commented out from `56f2788` — unrelated to Day 5, not touched).

### Files touched

- `src/pbicompass/agents/generators/technical.py` (`_infer_glossary` D6 fix)
- `src/pbicompass/render/markdown.py`, `src/pbicompass/render/html.py`, `src/pbicompass/render/docx.py` (typo/grammar fix in §19 boilerplate)
- `tests/test_output_quality_guards.py` (new)
- `tests/test_agents.py` (new regression test in `AntiPuntGuardTest`)
- `tests/fixtures/golden/{audit,executive,technical,user_guide}.html` (regenerated)

**Verdict: Day 5 is fully done** for everything executable in this environment — full offline regen across all 4 docs × all formats, a thorough manual QA read-through that caught and fixed two real (if minor) residual defects beyond D1–D6, the print-CSS verified at the source, and a new permanent CI guard suite locking in the Sprint 1 fixes. The Gemini smoke run and a real browser/PDF visual check are flagged as explicit, un-silenced gaps for a session with provider credentials / a browser available.

---

## Sprint 2 — Reasoning control + consultant-grade audit (Jul 15–21 · Days 6–10)

| Day | Date | Task | Status |
|---|---|---|---|
| 6 | Jul 15 | Cross-provider reasoning control (§4.0) | ✅ **Done** |
| 7 | Jul 16 | VertiPaq deterministic rules + Audit Synthesizer call | ⬜ Not started |
| 8 | Jul 17 | Render the Root-Cause Analysis section | ⬜ Not started |
| 9 | Jul 20 | AI fix snippets (paid) | ⬜ Not started |
| 10 | Jul 21 | Sprint 2 QA + A/B read | ⬜ Not started |

---

## Day 6 (Jul 15) — Cross-provider reasoning control (§4.0)

**Objective:** the `effort` reasoning-depth level must work on every LLM provider, not just Anthropic; a model that rejects a reasoning param must degrade gracefully instead of failing the job.

### Root cause / starting state

`GeminiClient`, `CohereClient`, and `MeshAPIClient` (`agents/llm.py`) all accepted the `effort=` kwarg on `complete_json` for protocol compatibility but silently discarded it — only `AnthropicClient` actually spent it on deeper thinking. The CLI's `--effort` flag and the service's `effort` Form field already existed, but were wired to the client's constructor for Anthropic only ([cli.py](src/pbicompass/cli.py), [worker.py](src/pbicompass/service/worker.py)).

### A roadmap contradiction found and resolved

The Day 6 task bullet in `PRODUCTION_ROADMAP.md` said "keep the per-plan ceiling in worker.py," but §4.0 of the same document — an explicit, dated owner cost-policy decision (2026-07-07) — says the opposite: *"do not clamp reasoning depth by plan… Remove/disable `worker.py::_clamp_effort_for_plan`. The only cost guardrail is the daily job quota."* Treated §4.0 as authoritative (it's the more specific, dated instruction, and matches the standing cost-policy record from prior sessions) and removed the clamp rather than keeping it.

### Task checklist

- [x] **Gemini** — `effort` now maps to `types.ThinkingConfig(thinking_budget=…)`; `max` requests Gemini's own "dynamic thinking" convention (`thinking_budget=-1`) — [llm.py:156-240](src/pbicompass/agents/llm.py#L156-L240) (`_GEMINI_THINKING_BUDGET`, `GeminiClient.complete_json`).
- [x] **Cohere** — the reasoning `thinking`/`token_budget` param is only sent when the *configured model itself* is reasoning-capable (`command-a-reasoning` and similar, detected by name); the default `command-a-03-2025` has no such knob, so effort stays accepted-and-ignored there — users opt in via `--model` per the roadmap's own guidance — [llm.py:243-343](src/pbicompass/agents/llm.py#L243-L343) (`_cohere_reasoning_capable`, `CohereClient.complete_json`).
- [x] **MeshAPI/OpenAI** — `reasoning_effort` is only sent when the routed model id looks reasoning-capable (o-series / gpt-5, matched via `_MESHAPI_REASONING_MODEL_RE`); every other model, including the `openai/gpt-4o` default, never receives it — preserves the existing regression test that this must never 400 on gpt-4o — [llm.py:346-480](src/pbicompass/agents/llm.py#L346-L480) (`_meshapi_reasoning_capable`, `MeshAPIClient.complete_json`).
- [x] **Graceful degradation, all four clients** — each `complete_json` now attempts the call with its reasoning param, and on that provider's own `BadRequestError`-equivalent, retries once without it rather than raising (which would otherwise trip `call_llm`'s fallback to the deterministic engine unnecessarily) — added to Anthropic too, for symmetry, via a new `_resolve_error_class` helper that looks up an SDK's error class defensively (root or `errors.` submodule) — [llm.py:48-63](src/pbicompass/agents/llm.py#L48-L63).
- [x] **`--effort` CLI flag generalized** — now passed to every provider's client constructor, not just Anthropic's; help text rewritten to describe the cross-provider behavior — [cli.py:226-231](src/pbicompass/cli.py#L226-L231), [cli.py:423-427](src/pbicompass/cli.py#L423-L427).
- [x] **Service upload field** — the `effort` Form field already existed ([app.py:220](src/pbicompass/service/app.py#L220)); `_make_client` in `worker.py` now passes it to every provider, not just Anthropic — [worker.py:63-74](src/pbicompass/service/worker.py#L63-L74).
- [x] **Per-plan effort ceiling removed** — `_clamp_effort_for_plan`/`_PLAN_EFFORT_CEILING` deleted from `worker.py` per §4.0; the stale "Phase 0: the caller's plan clamps effort" comment in `app.py` corrected to say `plan` only gates the job quota now — [worker.py:56-60](src/pbicompass/service/worker.py#L56-L60), [app.py:300-302](src/pbicompass/service/app.py#L300-L302).
- [x] **Frontend** — `service/static/index.html`'s `EFFORT_CAPABLE_PROVIDERS` broadened from `["anthropic"]` to all four LLM providers (offline `"none"` still hides the effort row), and `ESTIMATED_SECONDS` given a per-effort breakdown for gemini/cohere/meshapi to match — [index.html:1691-1725](src/pbicompass/service/static/index.html#L1691-L1725).

### Deliverable

- [x] Every provider honours the selected effort tier where its configured model supports reasoning; a rejecting model degrades via retry instead of failing the agent call.
- [x] New test class `ReasoningEffortWiringTest` (10 tests) in [test_agents.py](tests/test_agents.py) — per-provider assertions that the right native param is sent for a given effort + model (and correctly withheld for a non-reasoning model), plus a rejecting-model fallback test for each of the four clients, using the same fake-SDK-module pattern the existing MeshAPI/Cohere tests already established (`anthropic` and `openai` aren't installed in this environment; `google-genai` and `cohere` are, so those two are tested against the real SDK types with only the network call stubbed).

### Done-when (from the roadmap)

- [x] Per-provider unit tests assert the effort maps to the right native param (via a mock capturing call kwargs) — done for all four providers.
- [x] A rejecting-model test asserts the retry-without-reasoning fallback fires — done for all four providers.
- [ ] **One real smoke per provider** — not done. No `ANTHROPIC_API_KEY`/`GEMINI_API_KEY`/`COHERE_API_KEY`/`MESHAPI_API_KEY` is configured in this environment (same gap noted on Day 5 for the Gemini smoke run), so a live "does a real Gemini/MeshAPI call at `max` visibly reason (token/latency delta)" check could not be executed here. Flagged honestly, not silently skipped — needs a session with provider credentials.

### Full suite

- [x] `python -m pytest -q` — **417 passed**, 2 skipped, only the 2 known pre-existing failures remain (`test_render.py::HtmlRenderTest::test_accessibility_landmarks_present` / `test_interactive_diagram_nodes_and_edges`, traced to the Sprint-3-scoped model-diagram markup still commented out from `56f2788` — unrelated to Day 6, not touched).
- [x] Offline CLI smoke (`--provider none`) re-verified unaffected (provider-selection short-circuits before any client construction).

### Files touched

- `src/pbicompass/agents/llm.py`
- `src/pbicompass/cli.py`
- `src/pbicompass/service/worker.py`
- `src/pbicompass/service/app.py`
- `src/pbicompass/service/static/index.html`
- `tests/test_agents.py`

**Verdict: Day 6 is fully done** for everything executable in this environment — every provider's reasoning knob is genuinely wired (not just accepted-and-ignored), the retry-without-reasoning fallback is in place and guard-tested for all four clients, the CLI/service effort controls are generalized past Anthropic, and the roadmap's own internal contradiction on the per-plan clamp was resolved in favor of the explicit, dated owner cost-policy decision. The one gap is a live per-provider smoke test, blocked on provider credentials not being available in this sandbox — same class of gap already flagged and accepted on Day 5.

---

## Sprint 3–7 (Days 11–38)

Not started. See `PRODUCTION_ROADMAP.md` §14 for the full day-by-day breakdown.
