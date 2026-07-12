# PBICompass Output Quality Benchmark v2.0

**Purpose:** Score any PBICompass documentation bundle out of 100 against a fixed, repeatable standard. Run it on every release candidate and every golden fixture. A bundle = technical + audit + executive + user-guide (+ hub) generated from one model in one run.

**Design principles**
1. **Trust beats beauty.** A single self-contradiction costs more than ten cosmetic flaws, because the product's promise is *grounded, hallucination-free documentation*. Hard gates enforce this.
2. **Every check is testable.** Each check has an ID, a point value, a method (`AUTO` = grep/DOM assertion, `RENDER` = headless-browser assertion, `MANUAL` = human judgment), and a pass criterion. The AUTO/RENDER checks ARE the Day 7 test suite.
3. **Score what a Big-4 QA partner would score.** Internal consistency, sign-off readiness, defensible claims, audience fit.

---

## Scoring model

| Pillar | Points |
|---|---|
| 1. Trust & Numeric Integrity | 30 |
| 2. Content Completeness & Correctness | 25 |
| 3. Visual & Diagram Layer | 20 |
| 4. Design, UX & Accessibility | 15 |
| 5. Differentiators & Claim Integrity | 10 |
| **Total** | **100** |

**Hard gates (auto-caps).** Applied *after* summing points — final score is `min(sum, lowest triggered cap)`:

| Gate | Trigger | Cap |
|---|---|---|
| G1 | Any placeholder/guardrail phrase in narrative prose (any doc) | 75 |
| G2 | Any fabricated fact — a name, number, or object not present in the model metadata or human input | 70 |
| G3 | Health score, band, or check counts differ anywhere in the bundle | 80 |
| G4 | Narrative contradicts a deterministic audit verdict (star-schema class) | 78 |
| G5 | Any diagram renders below 50% of its intrinsic viewBox height | 85 |
| G6 | >5% of internal anchor links broken | 88 |
| G7 | Any external network request at load (violates offline guarantee) | 82 |
| G8 | Any document throws a JS error or fails to render | 60 |

**Ship bands**

| Score | Meaning |
|---|---|
| 95–100 | Flagship sample — publish on the website, use in demos |
| 90–94 | Ship to customers |
| 85–89 | Internal/dogfood only |
| 75–84 | Fix before any external eyes |
| < 75 | Trust layer broken — stop the release |

---

## Pillar 1 — Trust & Numeric Integrity (30 pts)

The non-negotiables. These encode every credibility defect found in review rounds 1–4.

| ID | Check | Pts | Method | Pass criterion |
|---|---|---|---|---|
| T1 | **Zero guardrail/placeholder leaks.** The literal string `requires business confirmation` (and any configured placeholder) appears in narrative prose in 0 of 4 docs. Legitimate use only inside unexplained-field description cells. | 8 | AUTO | grep = 0 outside whitelisted cell contexts, all docs |
| T2 | **One health score everywhere.** Hero chip, §1 prose, component table intro, tech §16, exec chip, hub — identical integer and band. | 6 | AUTO | Set of all `NN/100` / `score is NN` / `Health Score: NN` values across bundle has cardinality 1 |
| T3 | **One set of check counts.** Run/passed/failed/suppressed identical in audit and tech §16. | 4 | AUTO | Extracted tuples equal |
| T4 | **No verdict–narrative contradictions.** For the controlled vocabulary — star schema, RLS present/absent, refresh configured, description coverage, fact/dim counts, bidirectional relationships — prose never asserts the opposite of the deterministic verdict. | 6 | AUTO + MANUAL | Vocabulary scan 0 conflicts; reviewer spot-reads §2, §6, §16, audit summary |
| T5 | **Table-kind classification correct.** Date tables are dimensions, not facts; auto date/time internals excluded from kind stats; single-column parameter tables not "unknown". | 3 | AUTO | Kinds in Key tables match fixture expectations |
| T6 | **Clean prose mechanics.** No orphan fragments, no sentences under 4 content words, no doubled punctuation (`..`, `——`), no doubled phrases ("a star schema — a star schema"). | 3 | AUTO + MANUAL | Regex sweep 0 hits; reviewer reads all summary paragraphs |

**Evidence commands**

```bash
# T1 — leak sweep (must print 0 for every file)
grep -c "requires business confirmation" *_technical*.html *_audit.html *_executive.html *_user-guide.html

# T2 — score set (must print 1)
grep -ho "[0-9]\{2\}/100\|score is [0-9]\{2\}\|Health Score: [0-9]\{2\}" *.html | grep -o "[0-9]\{2\}" | sort -u | wc -l

# T3 — counts (each line count must appear for BOTH files identically)
grep -o "Passed: [0-9]*\|Failed: [0-9]*" *_audit.html *_technical*.html | sort | uniq -c

# T6 — doubled punctuation
grep -o "[a-z]\.\. [A-Z]" *.html
```

---

## Pillar 2 — Content Completeness & Correctness (25 pts)

| ID | Check | Pts | Method | Pass criterion |
|---|---|---|---|---|
| C1 | **Technical doc: all 19 sections present** with correct provenance pill per section (Extracted / AI-inferred / Human-provided), numbered, canonical order, each non-empty or carrying an honest "To complete" callout. Conditional claims rule: never reference an artifact ("model diagram is in section 6") unless it rendered. | 5 | AUTO | h2 census matches canonical list; every section has a pill; claim–artifact cross-check |
| C2 | **DAX dictionary card completeness.** Every measure card: home table (bold) + "Operates on" secondary, business description, calculation explanation, caveats, dependency tree (when deps exist), used-on pages, format string, confidence chip, syntax-highlighted copyable DAX. Descriptions must be grounded — a `* .3` factor described as 30% is a PASS; an unexplained factor must carry the confirmation flag *in the description cell, deliberately*. | 4 | AUTO + MANUAL | Card field census = 100%; reviewer verifies 3 random cards against raw DAX |
| C3 | **Audit findings anatomy.** Every finding: rule ID, severity pill, why-it-matters, suggested fix (code snippet with *actual model values* substituted), expected benefit, estimated effort. Heuristic sections carry the "not measured against actual data" disclaimer; unparsed features (hierarchies, calc groups) carry the scoping note. Auto date/time internals roll up into one finding, never inflate unused-asset counts. | 4 | AUTO + MANUAL | Field census; disclaimers present; adjusted counts |
| C4 | **RTM accuracy.** Score each row against the adjudicated key below. Tiering rule enforced: matching dimension table ⇒ floor Partial; measure + dimension pair ⇒ eligible for Covered; Gap only when nothing matches. **A false Gap (report demonstrably satisfies the requirement) fails the check outright.** Evidence must be the strongest available object — measure+dimension beats lone column; never an unrelated column (`Department[VP]` for a spend question). | 5 | AUTO vs key | ≥ 6/7 rows correct on the Corporate Spend key; 0 false Gaps |
| C5 | **Executive completeness.** Health score chip + band, component mini-bars, requirements-coverage stat, top risks with severity pills and "Ask:" framing + full-detail deep links, data & refresh in human phrasing ("1 Excel workbook — Data.xlsx", never `File.Contents(s)`), ownership + classification, What's Next as Severity/Action/Effort table (3–5 rows), page thumbnails excluding hidden pages. | 4 | AUTO + MANUAL | Element census; phrasing regex; 60-second-CFO read test |
| C6 | **User guide quality.** Per visible page: wireframe, what-to-look-at, visual/what-it-shows table, filters, navigation, "questions this page answers". Glossary contains business terms only — no field parameters or system fields (`select`, `select1`, `LocalDateTable*`); definitions grounded in DAX or human input. | 3 | AUTO + MANUAL | Junk-term grep = 0; per-page census |

**Adjudicated RTM key — Corporate Spend fixture**

| Requirement | Expected | Minimum evidence |
|---|---|---|
| Monitor total corporate spend by department | Covered | Actual/Amount + Department[Department] |
| Track monthly and yearly spending trends | Covered (Partial acceptable) | TOTALYTD measures + Date + "IT Spend Trend" page |
| Identify top vendors by expenditure | **Gap** (correct gap — no vendor dim) | — |
| Compare actual spend against budget | Covered | Actual, Plan, Actual/Plan, Var Plan % |
| Analyze spending by category and region | Covered | Cost Element + Country Region |
| Detect unusual spending patterns | **Gap** (correct gap) | — |
| Support procurement planning and cost optimization | Partial | Plan + Cost Element |

---

## Pillar 3 — Visual & Diagram Layer (20 pts)

| ID | Check | Pts | Method | Pass criterion |
|---|---|---|---|---|
| V1 | **Intrinsic aspect ratio preserved.** Every diagram SVG renders at ≥ 0.9 × (clientWidth × vbH/vbW) at desktop 1440px, mobile 390px, and print emulation. The anti-collapse check. | 6 | RENDER | Playwright assertion per SVG (below) |
| V2 | **Model diagram quality.** Star layout (facts centred, dimensions ringed), cardinality glyphs (1/\*) on every relationship, active vs inactive line styles, **no auto date/time internals or parameter tables drawn** (or collapsed into one faded "auto date/time ×N" chip), node click ⇒ data-dictionary anchor. | 4 | RENDER + MANUAL | Label census excludes `DateTableTemplate*` / `LocalDateTable*`; glyph pairs = relationship count |
| V3 | **Wireframe occlusion handling.** On the densest page (20 visuals): no card fully hides another's title; overlapped visuals render as ghost outlines with numbered callouts + legend list; hidden pages excluded from exec thumbnails; true page dimensions labelled. | 4 | RENDER + MANUAL | Occlusion scan: no text node fully covered; callout chips present when overlap detected |
| V4 | **Lineage correctness + interaction.** Columns sources→tables→measures→pages; edges match model relationships/usages; hover dims non-neighbours; click jumps to section; overflow node ("+N more") only when capacity exceeded, links to full list. | 3 | RENDER + MANUAL | Edge census vs model.json; anchors resolve |
| V5 | **Pan-zoom integration hygiene.** Zoom/pan works on every diagram; degrades to static SVG if the vendor script fails; **vendor JS embedded only in docs that contain diagrams**; print always gets the static render. | 3 | RENDER + AUTO | Feature probe; `svgPanZoom` absent from diagram-less docs; print CSS pins static |

**V1 render assertion (drop straight into the Day 7 suite)**

```python
for svg in page.query_selector_all('.diagram svg'):
    vb = svg.get_attribute('viewBox').split()
    ratio = float(vb[3]) / float(vb[2])
    box = svg.bounding_box()
    assert box['height'] >= 0.9 * box['width'] * ratio, \
        f"Diagram collapsed: {box['width']}x{box['height']} vs viewBox ratio {ratio:.2f}"
```

---

## Pillar 4 — Design, UX & Accessibility (15 pts)

| ID | Check | Pts | Method | Pass criterion |
|---|---|---|---|---|
| D1 | **Brand consistency.** One hero gradient/family across all four docs *and* the hub; identical stat-card, pill, callout, table styling; Poppins everywhere including SVG text. | 3 | MANUAL | Side-by-side screenshot review |
| D2 | **Dark mode.** Toggle in all docs (and hub); callouts, tables, code blocks, diagrams legible; active TOC state readable. | 2 | RENDER + MANUAL | Toggle probe + screenshot review |
| D3 | **Mobile 390px.** Hamburger TOC, stat cards reflow, tables scroll or stack, diagrams pannable, no horizontal page scroll. | 2 | RENDER | `document.body.scrollWidth <= 390` |
| D4 | **Print/PDF.** `@media print` present; diagrams full-size static; `page-break-inside: avoid` on cards/diagrams; no interactive chrome in print. | 3 | RENDER (print emulation) | Visual diff of printed diagram vs screen |
| D5 | **Accessibility.** Every diagram SVG has `role="img"` + `aria-labelledby` title; severity pills meet WCAG AA contrast; search input labelled; visible focus states. | 2 | AUTO + MANUAL | Title census = diagram count; contrast spot-check |
| D6 | **Microcopy.** Correct pluralization (no `asset(s)`), truncated labels carry `title` tooltips, no machine phrasing (`File.Contents(s)`), consistent terminology (decide DAX Quality vs Calculation Quality per audience and document the mapping). | 3 | AUTO + MANUAL | Regex sweep = 0 hits |

---

## Pillar 5 — Differentiators & Claim Integrity (10 pts)

| ID | Check | Pts | Method | Pass criterion |
|---|---|---|---|---|
| X1 | **Provenance honesty.** Every section pill matches its true source; human-provided sections render human text faithfully; AI-inferred never masquerades as Extracted. | 3 | AUTO + MANUAL | Pill census vs generation manifest |
| X2 | **Human-context precedence + discrepancy surfacing.** Human facts override extraction in prose; when human input contradicts the model (e.g. "RLS validated" vs 0 roles), a Discrepancy callout renders — silent override is a fail. | 3 | MANUAL vs fixture | Contradiction fixture produces the callout |
| X3 | **Guarantee claims verified.** "Fully offline, zero CDNs, zero telemetry": page load makes 0 network requests; no external URLs in src/href (data: URIs allowed). Methodology lists engine + prompt versions matching the run manifest. | 2 | RENDER (network log) + AUTO | Request count = 0 |
| X4 | **Completeness meter accuracy.** Percentage and "N fields awaiting input" exactly match the count of empty human fields; 100% ⇔ zero "To complete" callouts for human fields. | 2 | AUTO | Recomputed count = displayed count |

---

## Regression fixtures — never let these return

Every defect found in review rounds 1–4, as permanent named tests. Each maps to the check that now guards it.

| Fixture ID | Defect (round found) | Guarded by |
|---|---|---|
| RF-01 | "Conversely, Unknown — requires business confirmation." fragment in audit summary (R1) | T1, T6 |
| RF-02 | Placeholder replacing entire tech §16 root-cause paragraph, doubled (R2) | T1 + deterministic-fallback rule |
| RF-03 | Placeholder in exec §4 closing sentence with doubled period (R3) | T1, T6 |
| RF-04 | Hero 79/100 vs prose "score is 78" (R2) | T2, G3 |
| RF-05 | Audit 35/17 vs tech 37/15 check counts (R1) | T3, G3 |
| RF-06 | §6 "follows a star schema" vs MOD-007 FAIL galaxy (R1) | T4, G4 |
| RF-07 | Date table classified as fact ⇒ false galaxy finding (R1) | T5 |
| RF-08 | DAX card titled "Amount · Date" while housed in Fact (R1) | C2 |
| RF-09 | Auto date/time internals inflating unused assets 31→21 (R1) | C3, T5 |
| RF-10 | RTM false Gap: "monthly/yearly trends" despite TOTALYTD + IT Spend Trend page (R2) | C4 |
| RF-11 | RTM false Gaps: department & region despite Department / Country Region / Cost Element dims (R3–R4) | C4 |
| RF-12 | RTM weak evidence: `Department[VP]` for spend-by-department (R2) | C4 |
| RF-13 | `select` / `select1` field parameters in business glossary (R1–R2) | C6 |
| RF-14 | "The model diagram is in section 6" rendered while no diagram existed (R1–R3) | C1 conditional-claims rule |
| RF-15 | All diagrams collapsed to 150px strips after pan-zoom integration (R4) | V1, G5 |
| RF-16 | Auto date/time + Range tables drawn in model diagram (R4) | V2 |
| RF-17 | Wireframe cards fully occluding others on 20-visual page (R1–R3) | V3 |
| RF-18 | Pan-zoom vendor JS embedded in diagram-less audit doc (R4) | V5 |
| RF-19 | `1 File.Contents(s)` machine phrasing in exec (R1–R2) | C5, D6 |
| RF-20 | `asset(s)` pluralization (R1–R3) | D6 |
| RF-21 | "a star schema — a star schema centred on" doubled phrase (R3) | T6 |
| RF-22 | Hub hero brand-diverged from docs; no dark mode; no completeness bar (R3) | D1, D2 |
| RF-23 | Empty sign-off table because owner names never collected (R1) | C1, X4 |
| RF-24 | Exec "What's Next" single bullet; no health score in exec (R1–R2) | C5 |

**Adversarial fixture (must exist alongside Corporate Spend):** a model with a *genuine* galaxy schema, auto date/time ON, bidirectional + inactive relationships, a 20-visual page, field parameters, one RLS role, and one measure with an unexplained scaling factor. Rules must fire here and stay silent on Corporate Spend — both directions tested.

---

## Benchmark session procedure (~45 min)

1. **Generate** the bundle from the golden fixture with the standard intake filled (all 17 human fields + 7 requirements + sign-off names).
2. **Run AUTO suite** (T1–T3, T5–T6 greps; C1/C5/C6/D6 censuses; X3–X4 recomputation). ~5 min.
3. **Run RENDER suite** (V1 aspect assertions; D3 mobile; D4 print; X3 network log; pan-zoom probe) via Playwright. ~10 min.
4. **Manual pass** (T4 spot-read, C2 three-card DAX audit, C4 RTM vs key, V2–V4 diagram review, D1–D2 screenshots, X1–X2 provenance). ~25 min.
5. **Compute:** sum pillar points → apply lowest triggered gate → record in the log.

## Scoring worksheet

```
Run ID: ____________  Date: ____________  Engine/prompt ver: ____________
Fixture: Corporate Spend v___        Scorer: ____________

Pillar 1  Trust & Numeric Integrity   ___ / 30
Pillar 2  Content & Correctness       ___ / 25
Pillar 3  Visual & Diagram Layer      ___ / 20
Pillar 4  Design, UX & A11y           ___ / 15
Pillar 5  Differentiators             ___ / 10
Subtotal                              ___ / 100
Gates triggered: [ ] none  [ ] G__ → cap ___
FINAL SCORE                           ___ / 100   Band: ______________

Top 3 defects this run:
1.
2.
3.
```

## Score history

| Date | Run | Score | Gates | Notes |
|---|---|---|---|---|
| 2026-07-11 | R1 baseline | 75 | G4 (star contradiction), G1 (leak) | First external review |
| 2026-07-11 | R2 (Days 1–4) | 82 | G1 (leak ×4), G3 (78/79) | RTM shipped |
| 2026-07-11 | R3 (fixes + Day 5) | 87 | G1 (exec leak ×1) | Exec transformed |
| 2026-07-12 | R4 (Day 6) | 84 | G5 (diagram collapse) | Leaks eliminated; sizing regression |
| 2026-07-12 | R5 (Day 38 defect sweep) | not formally re-scored — see note | none triggered (G1/G5/G6/V2 all resolved) | AUTO+RENDER suite (T1–T6, C6, D6, I2/I3, V1, D3, X3) clean across all 5 rendered docs; RTM (C4) improved to 0 false Gaps / 4-of-7 exact tier (was 2 false Gaps in the R4-era live-AI bundle this run started from); full manual scoring worksheet (three-card DAX audit, D1/D2 screenshots, X1/X2 provenance) not executed this session — see `docs/planning/ROADMAP_PROGRESS.md`'s Day 38 entry for the complete defect list and honest gaps |
| | | | | |

---

*Maintained alongside the golden fixtures. Bump the version when checks are added; never remove a regression fixture.*
