"""Page wireframe SVG — a scaled layout of a report page's visuals (3.1),
redesigned per J.C to fix the v1 look: truncated internal type names, no
visual titles, an empty white canvas, stray unreadable mini-rects, uniform
washed-out styling, and inline ``style=``/``onmouseover=`` markup.

v2: the page renders as a "slide" (a light, bordered canvas — always light,
even in dark mode, matching the interactive model diagram's own rule);
every visual gets its real friendly type name and, space permitting, its
title; data visuals get a small category glyph and a native tooltip;
slicers/navigation/decorative objects get distinct, deliberately quieter
styling; objects too small to read collapse to a dot or a "+n" footer note;
and hover styling lives in the shared shell's CSS (a ``.wf-node`` class)
instead of a ``style=``/``onmouseover=`` attribute on every rect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..schemas.model import Page, Visual

from ..agents.report_facts import friendly_visual_type, visual_label
from ._shared import anchor_slug, html_e

# Non-data layout elements — quieter styling, never a glyph, never linked to
# a data-dictionary row (I3).
_DECORATIVE_TYPES = {"image", "shape", "basicShape", "textbox"}
_NAV_TYPES = {"actionButton", "button", "navBar", "bookmarkNavigator"}
_SLICER_TYPES = {"slicer", "advancedSlicerVisual"}

# visualType -> glyph id, for the handful of shapes worth a hand-rolled icon
# (J.C item 4). Anything else still gets a friendly name and a box, just no
# icon — drawing a bespoke icon per Power BI visual type isn't worth it.
_GLYPH_BY_TYPE = {
    "clusteredColumnChart": "bars", "columnChart": "bars",
    "hundredPercentStackedColumnChart": "bars", "stackedColumnChart": "bars",
    "clusteredBarChart": "bars", "barChart": "bars",
    "hundredPercentStackedBarChart": "bars", "stackedBarChart": "bars",
    "lineChart": "line",
    "lineStackedColumnComboChart": "combo", "lineClusteredColumnComboChart": "combo",
    "areaChart": "area", "stackedAreaChart": "area",
    "map": "pin", "filledMap": "pin", "shapeMap": "pin",
    "tableEx": "matrix", "table": "matrix", "pivotTable": "matrix", "matrix": "matrix",
    "card": "card123", "multiRowCard": "card123", "kpi": "card123",
    "decompositionTreeVisual": "tree", "decompositionTree": "tree",
}

def _glyph_defs(suffix: str) -> str:
    """The glyph ``<symbol>`` defs, namespaced by ``suffix`` — each wireframe
    is a self-contained SVG embedded independently (report_facts.report_pages
    computes one per page), so without a per-instance suffix a document with
    more than one page would define the same ``id="wf-i-bars"`` twice."""
    return f"""<defs>
<symbol id="wf-i-bars-{suffix}" viewBox="0 0 12 12"><rect x="1" y="6" width="2.4" height="5"/><rect x="4.8" y="3" width="2.4" height="8"/><rect x="8.6" y="1" width="2.4" height="10"/></symbol>
<symbol id="wf-i-line-{suffix}" viewBox="0 0 12 12"><polyline points="1,9 4,5 7,7 11,2" fill="none" stroke-width="1.4"/></symbol>
<symbol id="wf-i-combo-{suffix}" viewBox="0 0 12 12"><rect x="1" y="7" width="2.2" height="4"/><rect x="5" y="4" width="2.2" height="7"/><rect x="9" y="6" width="2.2" height="5"/><polyline points="1,4 5,2 9,3.5" fill="none" stroke-width="1.2"/></symbol>
<symbol id="wf-i-area-{suffix}" viewBox="0 0 12 12"><polygon points="1,10 1,7 4,4 7,6 11,2 11,10"/></symbol>
<symbol id="wf-i-pin-{suffix}" viewBox="0 0 12 12"><path d="M6 11 C3 7.5 2 5.6 2 4 A4 4 0 0 1 10 4 C10 5.6 9 7.5 6 11 Z"/><circle cx="6" cy="4" r="1.3" fill="#fff"/></symbol>
<symbol id="wf-i-matrix-{suffix}" viewBox="0 0 12 12"><rect x="1" y="1" width="4.5" height="4.5" fill="none" stroke-width="1"/><rect x="6.5" y="1" width="4.5" height="4.5" fill="none" stroke-width="1"/><rect x="1" y="6.5" width="4.5" height="4.5" fill="none" stroke-width="1"/><rect x="6.5" y="6.5" width="4.5" height="4.5" fill="none" stroke-width="1"/></symbol>
<symbol id="wf-i-card123-{suffix}" viewBox="0 0 12 12"><rect x="1" y="2" width="10" height="8" rx="1" fill="none" stroke-width="1"/><line x1="2.5" y1="7.5" x2="9.5" y2="7.5" stroke-width="1"/></symbol>
<symbol id="wf-i-funnel-{suffix}" viewBox="0 0 12 12"><polygon points="1,1.5 11,1.5 7.5,6.5 7.5,10 4.5,10.5 4.5,6.5"/></symbol>
<symbol id="wf-i-tree-{suffix}" viewBox="0 0 12 12"><circle cx="6" cy="1.8" r="1.4"/><circle cx="2" cy="9.5" r="1.4"/><circle cx="10" cy="9.5" r="1.4"/><line x1="6" y1="3.2" x2="2" y2="8.1" stroke-width="1"/><line x1="6" y1="3.2" x2="10" y2="8.1" stroke-width="1"/></symbol>
</defs>"""

# Category -> (fill, stroke, text color) — data visuals pop (white + indigo
# stroke); slicers keep the amber tint readers already associate with
# filters; nav is a thin green outline; decorative recedes (light gray, no
# stroke) instead of competing with real content for attention (J.C item 5).
_STYLE = {
    "data": ("#ffffff", "#4f46e5", "#312e81"),
    "slicer": ("#fef3c7", "#f59e0b", "#92400e"),
    "nav": ("#ecfdf5", "#10b981", "#065f46"),
    "decorative": ("#e2e8f0", "none", "#64748b"),
}

_LEGEND = (
    '<div class="legend">'
    '<span><i class="swatch" style="background:#ffffff;border:1.5px solid #4f46e5"></i>Data visual</span>'
    '<span><i class="swatch" style="background:#fef3c7;border:1px solid #f59e0b"></i>Slicer</span>'
    '<span><i class="swatch" style="background:#ecfdf5;border:1px solid #10b981"></i>Navigation</span>'
    '<span><i class="swatch" style="background:#e2e8f0"></i>Decorative</span>'
    "</div>"
)


def _category(v: "Visual") -> str:
    if v.is_slicer or v.type in _SLICER_TYPES:
        return "slicer"
    if v.type in _NAV_TYPES:
        return "nav"
    if v.type in _DECORATIVE_TYPES:
        return "decorative"
    return "data"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def render_wireframe(
    page: "Page", *,
    measure_names: frozenset[str] = frozenset(),
    field_param_tables: frozenset[str] = frozenset(),
) -> str:
    """Render a scaled SVG "slide" of the page's visuals, if layout
    coordinates exist (pbix-parsed models don't carry them — skip
    gracefully rather than render an empty diagram).

    ``measure_names``/``field_param_tables``, when given, let a data
    visual's link target be computed with the exact same
    ``report_facts.visual_label()`` a caller used to build the matching
    table row — otherwise an untitled visual bound only to fields (no
    title) would get a *different* label here (just its friendly type)
    than in the table (the fields, per ``visual_label``'s own fallback
    order), producing a dead link (I3)."""
    valid_visuals = [
        v for v in page.visuals
        if v.x is not None and v.y is not None and v.width is not None and v.height is not None
    ]
    if not valid_visuals:
        return ""

    page_w = page.width or 1280
    page_h = page.height or 720
    if page_w <= 0 or page_h <= 0:
        return ""
    page_area = page_w * page_h

    target_w = 480
    scale = target_w / page_w
    target_h = page_h * scale

    page_title_slug = anchor_slug(page.display_name)
    # Same anchor formula both html.py's Report Pages section and
    # user_guide.py's per-page card use for their own page-level wrapper id
    # — this SVG is computed once (report_facts.report_pages) and embedded
    # verbatim in both documents, so it can't carry a document-specific,
    # deduped id; two report pages whose names collapse to the same slug
    # (rare — Power BI page names are otherwise free-form) would share this
    # link, same as the pre-existing visual-anchor scheme below.
    page_anchor = f"page-{page_title_slug}"
    glyph_suffix = anchor_slug(page.id)
    title_id = f"wireframe-title-{glyph_suffix}"

    svg = [
        f'<svg viewBox="0 0 {target_w:.0f} {target_h:.0f}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="inherit" role="img" aria-labelledby="{title_id}">'
    ]
    svg.append(f'<title id="{title_id}">Wireframe layout for page {html_e(page.display_name)}</title>')
    svg.append(_glyph_defs(glyph_suffix))
    # The "slide": a light page background with an inset border — visuals
    # sit on it instead of floating in empty white space. Explicit hex
    # colors (not shell CSS variables) so the canvas stays light in dark
    # mode, same rule as the interactive model diagram.
    margin = 4
    svg.append(
        f'<rect x="{margin}" y="{margin}" width="{target_w - 2 * margin:.0f}" '
        f'height="{target_h - 2 * margin:.0f}" fill="#f8fafc" stroke="#e2e8f0" stroke-width="1"/>'
    )

    sorted_visuals = sorted(valid_visuals, key=lambda v: v.z or 0)

    decorative_shown = 0
    decorative_total = sum(1 for v in sorted_visuals if _category(v) == "decorative")
    decorative_overflow = 0

    for v in sorted_visuals:
        vx, vy = v.x * scale, v.y * scale
        vw, vh = v.width * scale, v.height * scale
        if vw <= 0 or vh <= 0:
            continue

        category = _category(v)

        # Tiny-object handling (J.C item 6): anything under 0.5% of the page
        # area renders as an unlabeled, unlinked dot — a full box + label
        # would be unreadable and misleading at that size regardless of type.
        if (v.width * v.height) < 0.005 * page_area:
            cx, cy = vx + vw / 2, vy + vh / 2
            svg.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="1.5" fill="#cbd5e1"/>')
            continue

        # Collapse decorative clutter (J.C item 6): once a page has 3+
        # decorative objects, show the first two individually and fold the
        # rest into one footer note instead of a wall of near-identical
        # light-gray rectangles.
        if category == "decorative" and decorative_total >= 3:
            decorative_shown += 1
            if decorative_shown > 2:
                decorative_overflow += 1
                continue

        fill, stroke, text_color = _STYLE[category]
        friendly = friendly_visual_type(v.type)
        label = v.title or friendly

        stroke_width = 0 if stroke == "none" else (1.5 if category == "data" else 1)
        rect_attrs = f'x="{vx:.1f}" y="{vy:.1f}" width="{vw:.1f}" height="{vh:.1f}" rx="3" class="wf-node"'
        style_attrs = f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"'

        box = [f'<rect {rect_attrs} {style_attrs}/>']

        glyph = _GLYPH_BY_TYPE.get(v.type) if category == "data" else ("funnel" if category == "slicer" else None)
        glyph_size = 10
        has_glyph = glyph and vw > 24 and vh > 20
        if has_glyph:
            gx, gy = vx + 3, vy + 3
            glyph_fill = "#4f46e5" if category == "data" else "#b45309"
            box.append(f'<use href="#wf-i-{glyph}-{glyph_suffix}" x="{gx:.1f}" y="{gy:.1f}" width="{glyph_size}" '
                      f'height="{glyph_size}" fill="{glyph_fill}" stroke="{glyph_fill}"/>')

        # Title-first labels (J.C item 3): large boxes get the visual's own
        # title plus its friendly type underneath; medium boxes get just
        # the type; small boxes get no text at all (there's no room to be
        # legible, so don't try).
        text_x_offset = 14 if has_glyph else 4
        if vw >= 60 and vh >= 24:
            title_text = _truncate(v.title, 22) if v.title else None
            ty = vy + 10
            if title_text:
                box.append(f'<text x="{vx + text_x_offset:.1f}" y="{ty:.1f}" font-size="7.5" '
                          f'font-weight="600" fill="{text_color}">{html_e(title_text)}</text>')
                ty += 9
            box.append(f'<text x="{vx + text_x_offset:.1f}" y="{ty:.1f}" font-size="6.5" '
                      f'fill="{text_color}" opacity="0.75">{html_e(friendly)}</text>')
        elif vw >= 35 and vh >= 18:
            box.append(f'<text x="{vx + text_x_offset:.1f}" y="{vy + vh / 2 + 2.5:.1f}" font-size="6.5" '
                      f'fill="{text_color}">{html_e(_truncate(friendly, 14))}</text>')

        if category == "data":
            metrics, dims = [], []
            for f in v.fields:
                parts = f.split(".")
                if len(parts) > 1 and parts[0] in field_param_tables:
                    continue
                leaf = parts[-1]
                (metrics if leaf in measure_names else dims).append(leaf)
            # Same label a caller's table row was built with (report_facts's
            # visual_label()) — not the simpler ``label`` used for the
            # on-canvas text above — so the link always resolves (I3).
            link_label = visual_label(v.title, v.type, metrics, dims)
            field_leaves = ", ".join(f.split(".")[-1] for f in v.fields) or "no fields bound"
            tooltip = f"{label} — {friendly} ({field_leaves})"
            visual_slug = anchor_slug(link_label)
            svg.append(f'<a href="#visual-{page_title_slug}-{visual_slug}">')
            svg.append(f"<title>{html_e(tooltip)}</title>")
            svg.extend(box)
            svg.append("</a>")
        elif category == "slicer":
            svg.append(f'<a href="#{page_anchor}">')
            svg.append(f"<title>{html_e(label)} — filters this page</title>")
            svg.extend(box)
            svg.append("</a>")
        else:
            # Buttons/nav and decorative shapes/images/text: not linked —
            # there's no per-object row anywhere in the document for them
            # to resolve to (I3).
            svg.extend(box)

    svg.append("</svg>")

    footer = ""
    if decorative_overflow:
        footer = f'<p class="wf-footer">+{decorative_overflow} decorative shape(s)</p>'

    return f'<div class="diagram">{"".join(svg)}{footer}{_LEGEND}</div>'
