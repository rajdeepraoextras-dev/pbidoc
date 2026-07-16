"""Render an :class:`AuditDocument` to Markdown, HTML, and DOCX.

Evaluation-shaped, not description-shaped: health score, complexity,
DAX/best-practice/performance/governance findings, unused assets, and
prioritized recommendations — a different structure from the 17-section
technical document, so it gets its own small renderer rather than being
forced through the technical renderer's section-by-section body. All three
renderers reuse the same low-level primitives as the technical renderer
(``_shared``, ``_html_shell``, ``_docx_writer``) so nothing is duplicated
beyond what each document shape actually needs.
"""

from __future__ import annotations

from pathlib import Path

from ..agents.audit_rules import RULE_METADATA
from ..schemas.audit_document import AuditDocument
from ._docx_writer import _Docx
from ._html_shell import page_shell
from .docx import _add_para_with_md
from ._shared import HEALTH_COMPONENT_LABELS, OPTIONAL_CONTEXT_FIELDS
from ._shared import anchor_slug
from ._shared import pluralize
from ._shared import pluralize_count
from ._shared import doc_subtitle as _doc_subtitle
from ._shared import format_timestamp as _fmt_ts
from ._shared import html_discrepancy_callout as _html_discrepancy_callout
from ._shared import html_e as _e
from ._shared import html_table as _html_table
from ._shared import md_discrepancy_callout as _md_discrepancy_callout
from ._shared import md_table as _table
from .html import _render_md
from .html import format_prose_with_code

_SECTION_TITLES = [
    "1. Overall Health Score",
    "2. Model Complexity",
    "3. DAX Review",
    "4. Model Best Practices",
    "5. Performance Risks",
    "6. Governance",
    "7. Unused Assets",
    "8. Recommendations",
    "9. Root-Cause Analysis",
]

_GOVERNANCE_AREA_LABELS = {
    "rls": "RLS", "descriptions": "Descriptions", "ownership": "Ownership",
    "sensitive_columns": "Sensitive Columns", "data_source_consistency": "Data Source Consistency",
}


def _component_label(key: str) -> str:
    # Reuses the same mapping the technical document's §16 renders with
    # (Day 2) — the Audit doc and technical.py's "Same deterministic rule
    # engine as the full Audit & Health Report" callout must never label the
    # identical health-score component two different ways.
    return HEALTH_COMPONENT_LABELS.get(key, key.replace("_", " ").title())


def _area_label(area: str) -> str:
    return _GOVERNANCE_AREA_LABELS.get(area, area.replace("_", " ").title())


def _kind_label(kind: str) -> str:
    return kind.replace("_", " ").title()


def _rule_name(rule_id: str) -> str:
    meta = RULE_METADATA.get(rule_id)
    return meta[2] if meta else rule_id


def _component_checks_summary(doc: AuditDocument) -> dict[str, str]:
    """Per-health-score-component "N/M passed" string, built from the rule
    ledger's per-category counts (J.A.1). ``naming`` checks fold into
    ``modeling`` — that's how they're already costed in the health score
    (see ``compute_health_score``'s ``modeling_cost``); ``unused_assets``
    has no rule-ID-backed checks, so it's always "—"."""
    merged: dict[str, dict[str, int]] = {
        "modeling": {"run": 0, "passed": 0}, "dax": {"run": 0, "passed": 0},
        "performance": {"run": 0, "passed": 0}, "governance": {"run": 0, "passed": 0},
    }
    for category, counts in (getattr(doc, "checks_by_category", None) or {}).items():
        target = "modeling" if category in ("modeling", "naming") else category
        if target not in merged:
            continue
        merged[target]["run"] += counts.get("run", 0)
        merged[target]["passed"] += counts.get("passed", 0)
    summary = {k: (f"{v['passed']}/{v['run']} passed" if v["run"] else "—") for k, v in merged.items()}
    summary["unused_assets"] = "—"
    return summary


def _checks_ledger_line(doc: AuditDocument) -> str:
    return (f"Checks run: {getattr(doc, 'checks_run', 0)} · "
            f"Passed: {getattr(doc, 'checks_passed', 0)} · "
            f"Failed: {getattr(doc, 'checks_failed', 0)} · "
            f"Suppressed: {getattr(doc, 'checks_suppressed', 0)}")


def _severity_note() -> str:
    return ("All performance risks below are heuristics inferred from metadata only — no "
            "row-level data is ever extracted, so nothing here reflects a measured runtime.")


def _auto_datetime_note(ua) -> str:
    """Footnote for the Unused Assets section (Day 2): the excluded count
    is tracked on ``ua.auto_datetime_excluded`` rather than silently
    dropped, and cross-references PBIC-PERF-007 where those artifacts are
    reported as their own category."""
    n = getattr(ua, "auto_datetime_excluded", 0)
    if not n:
        return ""
    return (f"{pluralize_count('additional otherwise-unused item', n)} belonging to Power BI's "
            f"auto-generated Auto Date/Time tables are excluded from the counts above — see "
            f"PBIC-PERF-007 in Performance Risks.")


def _unused_rows(ua) -> list[list]:
    return [
        ["Measures", len(ua.measures), ", ".join(ua.measures) or "—"],
        ["Columns", len(ua.columns), ", ".join(f"{c['table']}[{c['column']}]" for c in ua.columns) or "—"],
        ["Tables", len(ua.tables), ", ".join(ua.tables) or "—"],
        ["Calculated columns", len(ua.calculated_columns),
         ", ".join(f"{c['table']}[{c['column']}]" for c in ua.calculated_columns) or "—"],
        ["Report pages", len(ua.report_pages), ", ".join(ua.report_pages) or "—"],
    ]


def _top_cluster(doc: AuditDocument):
    """The broadest-impact cluster (most related findings) — what gets
    surfaced onto the technical document's §16 (Day 8). ``None`` when no
    clusters were produced."""
    if not doc.clusters:
        return None
    return max(doc.clusters, key=lambda c: len(c.rule_ids))


def _rule_id_anchors(doc: AuditDocument) -> dict[str, list[tuple[str, str, str]]]:
    """``rule_id -> [(section_label, anchor_id, display_text), ...]`` for
    every finding/check that carries that stable rule ID — the deep-link
    targets a cluster's ``rule_ids`` resolve against (Day 8).

    Deliberately excludes ``doc.recommendations`` (Day 2): every
    recommendation with a rule_id is generated *from* one of the findings/
    checks already indexed below (``build_recommendations``), so including
    it too just adds a second, near-duplicate "Related findings" link for
    the same rule_id (recommendations already have their own visibility in
    §8) — e.g. PBIC-MOD-010 used to render both its Model Best Practices
    check and its own recommendation echo as separate links."""
    index: dict[str, list[tuple[str, str, str]]] = {}

    def _add(rule_id: str, section: str, anchor: str, text: str) -> None:
        if not rule_id:
            return
        index.setdefault(rule_id, []).append((section, anchor, text))

    for i, f in enumerate(doc.dax_findings):
        _add(f.rule_id, "DAX Review", f"finding-dax-{i}", f"{f.measure} — {_kind_label(f.kind)}")
    for bp in doc.best_practices:
        _add(bp.rule_id, "Model Best Practices", f"check-{bp.id}", bp.name)
    for i, r in enumerate(doc.performance_risks):
        _add(r.rule_id, "Performance Risks", f"finding-perf-{i}", f"{r.object_name} — {_kind_label(r.kind)}")
    for i, g in enumerate(doc.governance):
        _add(g.rule_id, "Governance", f"finding-gov-{i}", _area_label(g.area))
    return index


# -- Markdown -------------------------------------------------------------------
def render_markdown(doc: AuditDocument) -> str:
    md = doc.metadata
    h = doc.health
    c = doc.complexity
    subtitle_str = _doc_subtitle(md)
    if getattr(md, "score_trend", None):
        subtitle_str += f" · Score Trend: {md.score_trend}"
    out: list[str] = [f"# {md.report_name} — Audit & Health Report\n"]
    out.append(f"_{subtitle_str}_\n")
    
    from ._shared import compute_completeness
    pct, missing_count, _ = compute_completeness(md)
    out.append(f"**Optional context supplied:** {len(OPTIONAL_CONTEXT_FIELDS) - missing_count}/"
               f"{len(OPTIONAL_CONTEXT_FIELDS)} "
               f"({missing_count} optional fields not provided)\n")
    if doc.narrative_overview:
        out.append(f"\n{doc.narrative_overview}\n")
    if getattr(doc, "changelog", None):
        out.append("\n### Changes since last documentation\n")
        out.append(doc.changelog + "\n")

    out.append(f"\n## {_SECTION_TITLES[0]}\n")
    out.append(f"**{h.overall} / 100 — {h.band}**\n")
    out.append(f"_{_checks_ledger_line(doc)}_\n")
    _notes = getattr(h, "component_notes", {}) or {}
    _checks = _component_checks_summary(doc)
    out.append(_table(["Component", "Score", "Checks", "Why"],
                      [[_component_label(k), v, _checks.get(k, "—"), _notes.get(k, "")]
                       for k, v in h.component_scores.items()]))

    if doc.suppressed_rules:
        out.append("\n**Suppressed by configuration "
                   f"({len(doc.suppressed_rules)}):**\n")
        out.append(_table(["Rule", "Name"],
                          [[rid, _rule_name(rid)] for rid in sorted(doc.suppressed_rules)]))

    out.append(f"\n## {_SECTION_TITLES[1]}\n")
    out.append(f"**{c.level}**\n")
    out.append(c.rationale + "\n")
    out.append(_table(["Metric", "Value"], [
        ["Tables", c.table_count], ["Measures", c.measure_count],
        ["Relationships", c.relationship_count], ["Calculated columns", c.calculated_column_count],
        ["Max relationship depth", c.max_relationship_depth],
    ]))

    out.append(f"\n## {_SECTION_TITLES[2]}\n")
    out.append(_table(
        ["Rule", "Measure", "Table", "Finding", "Severity", "Detail"],
        [[f.rule_id, f.measure, f.table or "—", _kind_label(f.kind), f.severity, f.detail]
         for f in doc.dax_findings],
        "_No DAX findings — no duplicate logic, overly long expressions, missing descriptions, "
        "or naming issues detected._",
    ))

    out.append(f"\n## {_SECTION_TITLES[3]}\n")
    out.append(_table(
        ["Rule", "Check", "Result", "Detail"],
        [[bp.rule_id, bp.name, "✅ Pass" if bp.passed else "❌ Fail", bp.detail] for bp in doc.best_practices],
    ))

    out.append(f"\n## {_SECTION_TITLES[4]}\n")
    out.append(f"_{_severity_note()}_\n")
    out.append(_table(
        ["Rule", "Kind", "Object", "Table", "Severity", "Detail"],
        [[r.rule_id, _kind_label(r.kind), r.object_name, r.table or "—", r.severity, r.detail]
         for r in doc.performance_risks],
        "_No performance risk signals detected._",
    ))

    out.append(f"\n## {_SECTION_TITLES[5]}\n")
    if getattr(doc, "discrepancies", None):
        out.append(_md_discrepancy_callout(doc.discrepancies))
    out.append(_table(
        ["Rule", "Area", "Severity", "Detail"],
        [[g.rule_id, _area_label(g.area), g.severity, g.detail] for g in doc.governance],
        "_No governance gaps detected._",
    ))

    out.append(f"\n## {_SECTION_TITLES[6]}\n")
    out.append(_table(["Asset Type", "Count", "Items"], _unused_rows(doc.unused_assets)))
    out.append("\n_Hierarchies and calculation groups are documented in the technical document's "
               "Data Model section; this unused-asset audit covers tables, columns, and measures._\n")
    auto_dt_note = _auto_datetime_note(doc.unused_assets)
    if auto_dt_note:
        out.append(f"\n_{auto_dt_note}_\n")

    out.append(f"\n## {_SECTION_TITLES[7]}\n")
    if doc.recommendations:
        for r in doc.recommendations:
            rule_suffix = f" ({r.rule_id})" if getattr(r, "rule_id", "") else ""
            out.append(f"### [{r.priority}] {r.issue}{rule_suffix}\n")
            out.append(f"**Why it matters:** {r.why_it_matters}\n")
            out.append(f"**Suggested fix:** {r.suggested_fix}\n")
            out.append(f"**Expected benefit:** {r.expected_benefit}\n")
            out.append(f"**Estimated effort:** {getattr(r, 'effort', 'Medium')}\n")
    else:
        out.append("_No recommendations — the model passed every deterministic check._\n")

    if getattr(doc, "requirements_gaps", None):
        out.append("\n### Requirements gaps\n")
        out.append(f"_{pluralize('Business requirement', len(doc.requirements_gaps))} with nothing in the "
                   "report satisfying them (Requirements Traceability Matrix — see Section 3 of the "
                   "technical document):_\n")
        for g in doc.requirements_gaps:
            priority = f"[{g['priority']}] " if g.get("priority") else ""
            out.append(f"- {priority}{g['text']}")

    if doc.clusters:
        out.append(f"\n## {_SECTION_TITLES[8]}\n")
        if doc.strategic_narrative:
            out.append(f"{doc.strategic_narrative}\n")
        for cl in doc.clusters:
            out.append(f"\n### {cl.root_cause} ({cl.confidence} confidence)\n")
            if cl.narrative:
                out.append(f"{cl.narrative}\n")
            if cl.rule_ids:
                out.append(f"**Related findings:** {', '.join(cl.rule_ids)}\n")

    return "\n".join(out).rstrip() + "\n"


# -- HTML -------------------------------------------------------------------------
def _severity_pill(severity: str) -> str:
    return f'<span class="pill {severity.lower()}">{_e(severity)}</span>'


def _rule_pill(rule_id: str) -> str:
    if not rule_id:
        return ""
    return f'<span class="pill rule-id" title="{_e(_rule_name(rule_id))}">{_e(rule_id)}</span>'


def _measure_cell(name: str, technical_href: str | None) -> str:
    """A measure name, linked to its entry in the technical document's
    Measure Catalog when that sibling doc was generated in the same job —
    never a dead link when it wasn't (2.7).

    Known gap (I2): this cross-document link computes the bare
    ``anchor_slug(name)`` because this renderer never receives the
    technical doc's own measure order/collision map — only its href. When
    two measure names collapse to the same slug (e.g. "Var LE1"/"Var LE1
    %"), ``render/html.py`` dedupes its own card ids so the *first* such
    measure keeps the bare slug and resolves correctly here; a link
    naming the *second* one in the same collision group still lands on
    the first measure's card instead of its own (not a dead link, but the
    wrong target) until the technical doc's anchor map is threaded across
    this document boundary too."""
    if not technical_href:
        return _e(name)
    return f'<a href="{_e(technical_href)}#measure-{_e(anchor_slug(name))}">{_e(name)}</a>'


def render_html(
    doc: AuditDocument, *,
    doc_links: list[tuple[str, str]] | None = None,
    sibling_hrefs: dict[str, str] | None = None,
) -> str:
    technical_href = (sibling_hrefs or {}).get("technical")
    md = doc.metadata
    h = doc.health
    c = doc.complexity

    # Section 9 (Root-Cause Analysis) only exists when the Audit Synthesizer
    # produced clusters (Day 7/8) — deterministic fallback is that it's
    # simply absent from the TOC, not an empty section.
    _visible_titles = _SECTION_TITLES if doc.clusters else _SECTION_TITLES[:8]
    toc = [(f"sec{i+1}", title.split(". ", 1)[1]) for i, title in enumerate(_visible_titles)]
    kpis = [
        ("Health Score", f"{h.overall}/100"), ("Band", h.band), ("Complexity", c.level),
        ("Findings", str(len(doc.dax_findings) + len(doc.performance_risks) + len(doc.governance))),
    ]

    o: list[str] = []
    if doc.narrative_overview:
        o.append(f'<div class="card-section"><p>{_e(doc.narrative_overview)}</p></div>')
    if getattr(doc, "changelog", None):
        o.append("<h3>Changes since last documentation</h3>")
        o.append(f'<div class="card-section">{_render_md(doc.changelog)}</div>')

    o.append(f'<h2 id="sec1">{_e(_SECTION_TITLES[0])}</h2>')
    o.append('<div class="score-hero">')
    o.append(f'<div class="score-big">{_e(h.overall)}/100</div>')
    o.append(f'<div class="score-band">{_e(h.band)}</div>')
    o.append('</div>')
    o.append(f'<p class="caveat">{_e(_checks_ledger_line(doc))}</p>')
    _notes = getattr(h, "component_notes", {}) or {}
    _checks = _component_checks_summary(doc)
    o.append(_html_table(["Component", "Score", "Checks", "Why"],
                         [[_e(_component_label(k)), _e(v), _e(_checks.get(k, "—")), _e(_notes.get(k, ""))]
                          for k, v in h.component_scores.items()]))

    if doc.suppressed_rules:
        o.append(f'<details class="collapsible"><summary>Suppressed by configuration '
                 f'({len(doc.suppressed_rules)})</summary><div class="collapsible-body">')
        o.append(_html_table(["Rule", "Name"],
                             [[_e(rid), _e(_rule_name(rid))] for rid in sorted(doc.suppressed_rules)]))
        o.append('</div></details>')

    o.append(f'<h2 id="sec2">{_e(_SECTION_TITLES[1])}</h2>')
    o.append(f'<p><strong>{_e(c.level)}</strong></p>')
    o.append(f"<p>{_e(c.rationale)}</p>")
    o.append(_html_table(["Metric", "Value"], [
        ["Tables", f'<span class="num">{c.table_count}</span>'],
        ["Measures", f'<span class="num">{c.measure_count}</span>'],
        ["Relationships", f'<span class="num">{c.relationship_count}</span>'],
        ["Calculated columns", f'<span class="num">{c.calculated_column_count}</span>'],
        ["Max relationship depth", f'<span class="num">{c.max_relationship_depth}</span>'],
    ]))

    dax_ids = [f"finding-dax-{i}" for i in range(len(doc.dax_findings))]
    o.append(f'<h2 id="sec3">{_e(_SECTION_TITLES[2])}</h2>')
    o.append(_html_table(
        ["Rule", "Measure", "Table", "Finding", "Severity", "Detail"],
        [[_rule_pill(f.rule_id), _measure_cell(f.measure, technical_href), _e(f.table or "—"),
          _e(_kind_label(f.kind)), _severity_pill(f.severity), _e(f.detail)]
         for f in doc.dax_findings],
        "No DAX findings — no duplicate logic, overly long expressions, missing descriptions, "
        "or naming issues detected.",
        row_ids=dax_ids,
    ))

    check_ids = [f"check-{_e(bp.id)}" for bp in doc.best_practices]
    o.append(f'<h2 id="sec4">{_e(_SECTION_TITLES[3])}</h2>')
    o.append(_html_table(
        ["Rule", "Check", "Result", "Detail"],
        [[_rule_pill(bp.rule_id), _e(bp.name), f'<span class="pill {"pass" if bp.passed else "fail"}">'
                       f'{"Pass" if bp.passed else "Fail"}</span>', _e(bp.detail)]
         for bp in doc.best_practices],
        row_ids=check_ids,
    ))

    perf_ids = [f"finding-perf-{i}" for i in range(len(doc.performance_risks))]
    o.append(f'<h2 id="sec5">{_e(_SECTION_TITLES[4])}</h2>')
    o.append(f'<p class="caveat">{_e(_severity_note())}</p>')
    o.append(_html_table(
        ["Rule", "Kind", "Object", "Table", "Severity", "Detail"],
        [[_rule_pill(r.rule_id), _e(_kind_label(r.kind)), _e(r.object_name), _e(r.table or "—"),
          _severity_pill(r.severity), _e(r.detail)] for r in doc.performance_risks],
        "No performance risk signals detected.",
        row_ids=perf_ids,
    ))

    gov_ids = [f"finding-gov-{i}" for i in range(len(doc.governance))]
    o.append(f'<h2 id="sec6">{_e(_SECTION_TITLES[5])}</h2>')
    if getattr(doc, "discrepancies", None):
        o.append(_html_discrepancy_callout(doc.discrepancies))
    o.append(_html_table(
        ["Rule", "Area", "Severity", "Detail"],
        [[_rule_pill(g.rule_id), _e(_area_label(g.area)), _severity_pill(g.severity), _e(g.detail)]
         for g in doc.governance],
        "No governance gaps detected.",
        row_ids=gov_ids,
    ))

    o.append(f'<h2 id="sec7">{_e(_SECTION_TITLES[6])}</h2>')
    o.append(_html_table(["Asset Type", "Count", "Items"],
                         [[_e(row[0]), _e(row[1]), _e(row[2])] for row in _unused_rows(doc.unused_assets)]))
    o.append('<p class="caveat">Hierarchies and calculation groups are documented in the technical '
             'document\'s Data Model section; this unused-asset audit covers tables, columns, and measures.</p>')
    auto_dt_note = _auto_datetime_note(doc.unused_assets)
    if auto_dt_note:
        o.append(f'<p class="caveat">{_e(auto_dt_note)}</p>')

    # Anchored by rule_id when present (stable across renders, and how the
    # executive doc's per-risk deep links (I5) address a specific
    # recommendation) — falls back to a positional id for the rare
    # recommendation with no backing rule (e.g. "unused assets").
    rec_ids = [f"rec-{r.rule_id}" if getattr(r, "rule_id", "") else f"rec-{i}"
               for i, r in enumerate(doc.recommendations)]
    o.append(f'<h2 id="sec8">{_e(_SECTION_TITLES[7])}</h2>')
    if doc.recommendations:
        for r, rec_id in zip(doc.recommendations, rec_ids):
            o.append(f'<div class="card-section" id="{_e(rec_id)}">')
            o.append(f'<h3>{_severity_pill(r.priority)} {_e(r.issue)}{_rule_pill(getattr(r, "rule_id", ""))}</h3>')
            o.append(f'<p><strong>Why it matters:</strong> {_e(r.why_it_matters)}</p>')
            o.append(f'<p><strong>Suggested fix:</strong> {format_prose_with_code(r.suggested_fix)}</p>')
            o.append(f'<p><strong>Expected benefit:</strong> {_e(r.expected_benefit)}</p>')
            o.append(f'<p><strong>Estimated effort:</strong> {_e(getattr(r, "effort", "Medium"))}</p>')
            o.append('</div>')
    else:
        o.append('<p class="muted">No recommendations — the model passed every deterministic check.</p>')

    if getattr(doc, "requirements_gaps", None):
        o.append('<div class="card-section" style="border-left: 4px solid #b42318;">')
        o.append('<h3>Requirements gaps</h3>')
        o.append(f'<p class="muted">{_e(pluralize("Business requirement", len(doc.requirements_gaps)))} '
                 'with nothing in the report satisfying them '
                 '(Requirements Traceability Matrix — see Section 3 of the technical document):</p>')
        o.append('<ul>')
        for g in doc.requirements_gaps:
            priority = f'<span class="pill high">{_e(g["priority"])}</span> ' if g.get("priority") else ""
            o.append(f'<li>{priority}{_e(g["text"])}</li>')
        o.append('</ul>')
        o.append('</div>')

    cluster_ids = [f"cluster-{i}" for i in range(len(doc.clusters))]
    if doc.clusters:
        anchor_index = _rule_id_anchors(doc)
        o.append(f'<h2 id="sec9">{_e(_SECTION_TITLES[8])}</h2>')
        if doc.strategic_narrative:
            o.append(f'<div class="card-section"><p>{_e(doc.strategic_narrative)}</p></div>')
        for cl, cluster_id in zip(doc.clusters, cluster_ids):
            o.append(f'<div class="card-section" id="{_e(cluster_id)}">')
            o.append(f'<h3>{_e(cl.root_cause)} {_severity_pill(cl.confidence)}</h3>')
            if cl.narrative:
                o.append(f'<p>{_e(cl.narrative)}</p>')
            links = []
            for rid in cl.rule_ids:
                targets = anchor_index.get(rid)
                if not targets:
                    links.append(f'<code>{_e(rid)}</code>')
                    continue
                links.extend(f'<a href="#{_e(anchor)}">{_e(rid)} — {_e(text)}</a>'
                             for _section, anchor, text in targets)
            if links:
                o.append(f'<p class="caveat"><strong>Related findings:</strong> {", ".join(links)}</p>')
            o.append('</div>')

    search_index = [{"title": sec_title, "type": "section", "anchor": sec_id} for sec_id, sec_title in toc]
    search_index += [
        {"title": cl.root_cause, "type": "finding", "anchor": cid}
        for cl, cid in zip(doc.clusters, cluster_ids)
    ]
    search_index += [
        {"title": f"{f.measure} — {_kind_label(f.kind)}", "type": "finding", "anchor": rid}
        for f, rid in zip(doc.dax_findings, dax_ids)
    ]
    search_index += [
        {"title": bp.name, "type": "check", "anchor": rid}
        for bp, rid in zip(doc.best_practices, check_ids) if not bp.passed
    ]
    search_index += [
        {"title": f"{r.object_name} — {_kind_label(r.kind)}", "type": "finding", "anchor": rid}
        for r, rid in zip(doc.performance_risks, perf_ids)
    ]
    search_index += [
        {"title": _area_label(g.area), "type": "finding", "anchor": rid}
        for g, rid in zip(doc.governance, gov_ids)
    ]
    search_index += [
        {"title": r.issue, "type": "recommendation", "anchor": rid}
        for r, rid in zip(doc.recommendations, rec_ids)
    ]

    subtitle_str = _doc_subtitle(md)
    if getattr(md, "score_trend", None):
        subtitle_str += f" · Score Trend: {md.score_trend}"

    from ._shared import compute_completeness
    comp = compute_completeness(md)

    return page_shell(
        title=f"{md.report_name} — Audit & Health Report",
        subtitle=subtitle_str,
        toc=toc, kpis=kpis, body_html="\n".join(o), doc_links=doc_links, search_index=search_index,
        owner=md.owner, version=md.version, status=md.status,
        completeness=comp,
    )


# -- DOCX -------------------------------------------------------------------------
def render_docx(doc: AuditDocument, out_path) -> Path:
    """Write ``doc`` to a ``.docx`` at ``out_path`` and return the path."""
    out_path = Path(out_path)
    d = _Docx()
    md = doc.metadata
    h = doc.health
    c = doc.complexity

    d.heading(0, f"{md.report_name} — Audit & Health Report")
    subtitle_str = _doc_subtitle(md)
    if getattr(md, "score_trend", None):
        subtitle_str += f" · Score Trend: {md.score_trend}"
    d.para([d._run(subtitle_str, italic=True)])
    
    from ._shared import compute_completeness
    pct, missing_count, _ = compute_completeness(md)
    d.para([d._run(f"Optional context supplied: {len(OPTIONAL_CONTEXT_FIELDS) - missing_count}/"
                   f"{len(OPTIONAL_CONTEXT_FIELDS)} "
                   f"({missing_count} optional fields not provided)", italic=True)])
    if doc.narrative_overview:
        d.para(doc.narrative_overview)
    if getattr(doc, "changelog", None):
        d.heading(2, "Changes since last documentation")
        _add_para_with_md(d, doc.changelog)

    def _t(rows):
        return [[str(cell) for cell in row] for row in rows]

    d.heading(1, _SECTION_TITLES[0])
    d.para([d._run(f"{h.overall} / 100 — {h.band}", bold=True)])
    d.para([d._run(_checks_ledger_line(doc), italic=True)])
    _notes = getattr(h, "component_notes", {}) or {}
    _checks = _component_checks_summary(doc)
    d.table(["Component", "Score", "Checks", "Why"],
            _t([[_component_label(k), v, _checks.get(k, "—"), _notes.get(k, "")]
                for k, v in h.component_scores.items()]))

    if doc.suppressed_rules:
        d.para([d._run(f"Suppressed by configuration ({len(doc.suppressed_rules)}):", bold=True)])
        d.table(["Rule", "Name"], _t([[rid, _rule_name(rid)] for rid in sorted(doc.suppressed_rules)]))

    d.heading(1, _SECTION_TITLES[1])
    d.para([d._run(c.level, bold=True)])
    d.para(c.rationale)
    d.table(["Metric", "Value"], _t([
        ["Tables", c.table_count], ["Measures", c.measure_count],
        ["Relationships", c.relationship_count], ["Calculated columns", c.calculated_column_count],
        ["Max relationship depth", c.max_relationship_depth],
    ]))

    d.heading(1, _SECTION_TITLES[2])
    d.table(["Rule", "Measure", "Table", "Finding", "Severity", "Detail"],
            _t([[f.rule_id, f.measure, f.table or "—", _kind_label(f.kind), f.severity, f.detail]
                for f in doc.dax_findings]) or [["—", "—", "—", "—", "—", "No DAX findings."]])

    d.heading(1, _SECTION_TITLES[3])
    d.table(["Rule", "Check", "Result", "Detail"],
            _t([[bp.rule_id, bp.name, "Pass" if bp.passed else "Fail", bp.detail]
                for bp in doc.best_practices]))

    d.heading(1, _SECTION_TITLES[4])
    d.para([d._run(_severity_note(), italic=True)])
    d.table(["Rule", "Kind", "Object", "Table", "Severity", "Detail"],
            _t([[r.rule_id, _kind_label(r.kind), r.object_name, r.table or "—", r.severity, r.detail]
                for r in doc.performance_risks]) or [["—", "—", "—", "—", "—", "No performance risk signals."]])

    d.heading(1, _SECTION_TITLES[5])
    for disc in (getattr(doc, "discrepancies", None) or []):
        d.para([d._run("⚠ Discrepancy — human input vs. model", bold=True)])
        d.para([d._run("You stated: ", bold=True), d._run(disc.get("human_claim", ""))])
        d.para([d._run("The model shows: ", bold=True), d._run(disc.get("model_finding", ""))])
        d.para([d._run(disc.get("explanation", ""), italic=True)])
    d.table(["Rule", "Area", "Severity", "Detail"],
            _t([[g.rule_id, _area_label(g.area), g.severity, g.detail] for g in doc.governance])
            or [["—", "—", "—", "No governance gaps detected."]])

    d.heading(1, _SECTION_TITLES[6])
    d.table(["Asset Type", "Count", "Items"], _t(_unused_rows(doc.unused_assets)))
    d.para([d._run("Hierarchies and calculation groups are documented in the technical document's Data "
                   "Model section; this unused-asset audit covers tables, columns, and measures.", italic=True)])
    auto_dt_note = _auto_datetime_note(doc.unused_assets)
    if auto_dt_note:
        d.para([d._run(auto_dt_note, italic=True)])

    d.heading(1, _SECTION_TITLES[7])
    if doc.recommendations:
        for r in doc.recommendations:
            rule_suffix = f" ({r.rule_id})" if getattr(r, "rule_id", "") else ""
            d.heading(3, f"[{r.priority}] {r.issue}{rule_suffix}")
            d.para([d._run("Why it matters: ", bold=True), d._run(r.why_it_matters)])
            d.para([d._run("Suggested fix: ", bold=True), d._run(r.suggested_fix)])
            d.para([d._run("Expected benefit: ", bold=True), d._run(r.expected_benefit)])
            d.para([d._run("Estimated effort: ", bold=True), d._run(getattr(r, "effort", "Medium"))])
    else:
        d.para("No recommendations — the model passed every deterministic check.")

    if getattr(doc, "requirements_gaps", None):
        d.heading(2, "Requirements gaps")
        d.para([d._run(f"{pluralize('Business requirement', len(doc.requirements_gaps))} with nothing in "
                       "the report satisfying them (Requirements Traceability Matrix — see Section 3 of "
                       "the technical document):", italic=True)])
        for g in doc.requirements_gaps:
            prefix = f"[{g['priority']}] " if g.get("priority") else ""
            d.bullet(f"{prefix}{g['text']}")

    if doc.clusters:
        d.heading(1, _SECTION_TITLES[8])
        if doc.strategic_narrative:
            d.para(doc.strategic_narrative)
        for cl in doc.clusters:
            d.heading(3, f"{cl.root_cause} ({cl.confidence} confidence)")
            if cl.narrative:
                d.para(cl.narrative)
            if cl.rule_ids:
                d.para([d._run("Related findings: ", bold=True), d._run(", ".join(cl.rule_ids))])

    d.save(out_path)
    return out_path
