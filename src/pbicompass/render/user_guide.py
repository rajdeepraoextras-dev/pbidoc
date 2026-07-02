"""Render a :class:`UserGuideDocument` to Markdown, HTML, and DOCX.

Per-page narrative for business users — no DAX/table/semantic-model talk.
Bookmarks and Tooltips subsections are omitted entirely when empty (today's
``model.json`` never populates them) rather than shown as a misleading
"None" row that would imply the check actually ran against real data.
Reuses the same low-level primitives as the other renderers (``_shared``,
``_html_shell``, ``_docx_writer``).
"""

from __future__ import annotations

from pathlib import Path

from ..schemas.user_guide_document import UserGuideDocument
from ._docx_writer import _Docx
from ._html_shell import page_shell
from ._shared import html_e as _e
from ._shared import html_table as _html_table
from ._shared import md_table as _table

_SECTION_TITLES = [
    "1. Introduction",
    "2. Getting Started",
    "3. Report Pages",
    "4. Glossary of Business Terms",
]


# -- Markdown -------------------------------------------------------------------
def render_markdown(doc: UserGuideDocument) -> str:
    md = doc.metadata
    out: list[str] = [f"# {md.report_name} — Business User Guide\n"]
    out.append(f"_{md.target_audience or ''} · generated {md.generated_at or ''}_\n")

    out.append(f"\n## {_SECTION_TITLES[0]}\n")
    out.append(doc.introduction + "\n")

    out.append(f"\n## {_SECTION_TITLES[1]}\n")
    for tip in doc.getting_started:
        out.append(f"- {tip}")
    out.append("")

    out.append(f"\n## {_SECTION_TITLES[2]}\n")
    for p in doc.pages:
        out.append(f"\n### {p.page_title}\n")
        out.append(p.purpose + "\n")

        if p.main_kpis:
            out.append("**What to look at**\n")
            for kpi in p.main_kpis:
                out.append(f"- {kpi}")
            out.append("")

        if p.visual_descriptions:
            out.append("**What each visual shows**\n")
            out.append(_table(["Visual", "What it shows"],
                              [[v["visual"], v["what_it_shows"]] for v in p.visual_descriptions]))

        if p.filters:
            out.append(f"**Filters on this page:** {', '.join(p.filters)}.\n")

        if p.navigation_tips:
            out.append("**How to navigate this page**\n")
            for tip in p.navigation_tips:
                out.append(f"- {tip}")
            out.append("")

        if p.business_questions_answered:
            out.append("**Questions this page answers**\n")
            for q in p.business_questions_answered:
                out.append(f"- {q}")
            out.append("")

        if p.drillthrough_actions:
            out.append("**Drilling into detail**\n")
            for action in p.drillthrough_actions:
                out.append(f"- {action}")
            out.append("")

        # Bookmarks/Tooltips subsections are only emitted when present —
        # today's model.json never populates them, so they're omitted rather
        # than shown as a misleading "None" row.
        if p.bookmarks:
            out.append("**Saved views**\n")
            for b in p.bookmarks:
                out.append(f"- {b}")
            out.append("")

        if p.tooltips:
            out.append("**Hover for more detail**\n")
            for t in p.tooltips:
                out.append(f"- {t}")
            out.append("")

        if p.common_scenarios:
            out.append("**Common scenarios**\n")
            for s in p.common_scenarios:
                out.append(f"- {s}")
            out.append("")

    out.append(f"\n## {_SECTION_TITLES[3]}\n")
    if doc.glossary:
        out.append(_table(["Term", "What it means"],
                          [[g.term, g.plain_definition] for g in doc.glossary]))
    else:
        out.append("_No glossary terms available._\n")

    return "\n".join(out).rstrip() + "\n"


# -- HTML -------------------------------------------------------------------------
def _bullet_list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>"


def render_html(doc: UserGuideDocument) -> str:
    md = doc.metadata

    toc = [("sec1", "Introduction"), ("sec2", "Getting Started"), ("sec3", "Report Pages"),
           ("sec4", "Glossary")]
    kpis = [
        ("Pages", len(doc.pages)),
        ("Glossary Terms", len(doc.glossary)),
    ]

    o: list[str] = []
    o.append(f'<h2 id="sec1">{_e(_SECTION_TITLES[0])}</h2>')
    o.append(f"<p>{_e(doc.introduction)}</p>")

    o.append(f'<h2 id="sec2">{_e(_SECTION_TITLES[1])}</h2>')
    o.append(_bullet_list(doc.getting_started))

    o.append(f'<h2 id="sec3">{_e(_SECTION_TITLES[2])}</h2>')
    for p in doc.pages:
        o.append('<div class="card-section">')
        o.append(f"<h3>{_e(p.page_title)}</h3>")
        o.append(f"<p>{_e(p.purpose)}</p>")

        if p.main_kpis:
            o.append("<p><strong>What to look at:</strong> " + _e(", ".join(p.main_kpis)) + "</p>")

        if p.visual_descriptions:
            o.append(_html_table(["Visual", "What it shows"],
                                 [[_e(v["visual"]), _e(v["what_it_shows"])] for v in p.visual_descriptions]))

        if p.filters:
            o.append(f'<p class="caveat"><strong>Filters on this page:</strong> {_e(", ".join(p.filters))}.</p>')

        if p.navigation_tips:
            o.append("<p><strong>How to navigate this page</strong></p>")
            o.append(_bullet_list(p.navigation_tips))

        if p.business_questions_answered:
            o.append("<p><strong>Questions this page answers</strong></p>")
            o.append(_bullet_list(p.business_questions_answered))

        if p.drillthrough_actions:
            o.append("<p><strong>Drilling into detail</strong></p>")
            o.append(_bullet_list(p.drillthrough_actions))

        if p.bookmarks:
            o.append("<p><strong>Saved views</strong></p>")
            o.append(_bullet_list(p.bookmarks))

        if p.tooltips:
            o.append("<p><strong>Hover for more detail</strong></p>")
            o.append(_bullet_list(p.tooltips))

        if p.common_scenarios:
            o.append("<p><strong>Common scenarios</strong></p>")
            o.append(_bullet_list(p.common_scenarios))

        o.append("</div>")

    o.append(f'<h2 id="sec4">{_e(_SECTION_TITLES[3])}</h2>')
    if doc.glossary:
        o.append(_html_table(["Term", "What it means"],
                             [[f"<code>{_e(g.term)}</code>", _e(g.plain_definition)] for g in doc.glossary]))
    else:
        o.append('<p class="muted">No glossary terms available.</p>')

    return page_shell(
        title=f"{md.report_name} — Business User Guide",
        subtitle=f"{md.target_audience or ''} · generated {md.generated_at or ''}",
        toc=toc, kpis=kpis, body_html="\n".join(o),
    )


# -- DOCX -------------------------------------------------------------------------
def render_docx(doc: UserGuideDocument, out_path) -> Path:
    """Write ``doc`` to a ``.docx`` at ``out_path`` and return the path."""
    out_path = Path(out_path)
    d = _Docx()
    md = doc.metadata

    d.heading(0, f"{md.report_name} — Business User Guide")
    d.para([d._run(f"{md.target_audience or ''} · generated {md.generated_at or ''}", italic=True)])

    def _bullets(items: list[str]) -> None:
        for item in items:
            d.bullet(item)

    d.heading(1, _SECTION_TITLES[0])
    d.para(doc.introduction)

    d.heading(1, _SECTION_TITLES[1])
    _bullets(doc.getting_started)

    d.heading(1, _SECTION_TITLES[2])
    for p in doc.pages:
        d.heading(2, p.page_title)
        d.para(p.purpose)

        if p.main_kpis:
            d.para([d._run("What to look at: ", bold=True), d._run(", ".join(p.main_kpis))])

        if p.visual_descriptions:
            d.table(["Visual", "What it shows"],
                    [[v["visual"], v["what_it_shows"]] for v in p.visual_descriptions])

        if p.filters:
            d.para([d._run("Filters on this page: ", bold=True), d._run(", ".join(p.filters) + ".")])

        if p.navigation_tips:
            d.para([d._run("How to navigate this page", bold=True)])
            _bullets(p.navigation_tips)

        if p.business_questions_answered:
            d.para([d._run("Questions this page answers", bold=True)])
            _bullets(p.business_questions_answered)

        if p.drillthrough_actions:
            d.para([d._run("Drilling into detail", bold=True)])
            _bullets(p.drillthrough_actions)

        if p.bookmarks:
            d.para([d._run("Saved views", bold=True)])
            _bullets(p.bookmarks)

        if p.tooltips:
            d.para([d._run("Hover for more detail", bold=True)])
            _bullets(p.tooltips)

        if p.common_scenarios:
            d.para([d._run("Common scenarios", bold=True)])
            _bullets(p.common_scenarios)

    d.heading(1, _SECTION_TITLES[3])
    if doc.glossary:
        d.table(["Term", "What it means"], [[g.term, g.plain_definition] for g in doc.glossary])
    else:
        d.para("No glossary terms available.")

    d.save(out_path)
    return out_path
