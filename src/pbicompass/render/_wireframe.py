"""Page wireframe SVG — a scaled layout of a report page's visuals (3.1).

v4 (2026-07-08, per a user-supplied ``wireframe-v4-light.html`` reference,
"Option A" — confirmed exact-match applied to the page's *real* per-visual
positions rather than v4's own fixed demo grid): every visual is a white
"card" — a thin neutral border, soft shadow, rounded corners, a colored
top-accent bar, a tinted icon badge with a stroke-style icon, and title +
type label in one uniform ink color (category color now drives only the
accent bar / icon / hover state, not the whole box fill — unlike v2, where
each category tinted the entire box). Large data visuals (KPI cards, bar/
line charts, maps) get a small schematic "ghost content" glyph (a
placeholder value + sparkline, bars, a line, or a dot cluster) — always in
its settled, non-animated state, so a browser's print-to-PDF never
captures a half-drawn frame (the SVG has no page-load or looping
animation; only ``:hover``, which by definition never appears in a static
print capture, matching the pre-existing ``.wf-node`` hover convention).

Same four categories (data/slicer/nav/decorative), same real x/y/width/
height positions, same tiny/medium/large size-tier degradation, and the
same hover-via-CSS-class / I3 link-resolution logic v2 already
established — only the visual skin changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..schemas.model import Page, Visual

from ..agents.report_facts import friendly_visual_type, visual_label
from ._shared import anchor_slug, html_e

# Non-data layout elements — quieter styling, never linked to a
# data-dictionary row (I3).
_DECORATIVE_TYPES = {"image", "shape", "basicShape", "textbox"}
_NAV_TYPES = {"actionButton", "button", "navBar", "bookmarkNavigator"}
_SLICER_TYPES = {"slicer", "advancedSlicerVisual"}

# v4 tokens, copied verbatim from the reference file (same hex).
_INK = "#1f2433"
_MUTED = "#8a93a8"
_FAINT = "#b6bdcf"
_EDGE = "#e7eaf3"
_SURFACE = "#ffffff"

# category -> (accent, soft icon-badge tint) — the card surface/border/text
# stay category-neutral (all white, all _EDGE, all _INK); category color
# only drives the top accent bar, the icon badge, and the hover/tag tint.
_STYLE = {
    "data": ("#4f6ef7", "#eef1fe"),
    "slicer": ("#f59e0b", "#fef4e4"),
    "nav": ("#10b981", "#e7f8f1"),
    "decorative": ("#8b5cf6", "#f3eefe"),
}

# visualType -> glyph id. One shared dict covers all four categories now
# (v2 only iconified data visuals + a generic slicer funnel — v4 gives
# every category its own icon, including nav buttons and each decorative
# kind, so a reader can tell a text box from an image at a glance).
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
    "slicer": "funnel", "advancedSlicerVisual": "funnel",
    "actionButton": "button", "button": "button", "navBar": "button", "bookmarkNavigator": "button",
    "image": "image", "textbox": "textbox", "shape": "shape", "basicShape": "shape",
}

# Visual families that get "ghost content" (a small schematic placeholder
# — never a real/fabricated number) when their card is roomy enough. Kept
# to the same four families v4 itself defines ghost content for.
_GHOST_KPI = {"card123"}
_GHOST_BARS = {"bars"}
_GHOST_LINE = {"line", "combo", "area"}
_GHOST_MAP = {"pin"}


def _glyph_defs(suffix: str) -> str:
    """The glyph ``<symbol>`` defs + the canvas dot-grid pattern,
    namespaced by ``suffix`` — each wireframe is a self-contained SVG
    embedded independently (report_facts.report_pages computes one per
    page), so without a per-instance suffix a document with more than one
    page would define the same ``id="wf-i-bars"`` twice. All icons are
    v4's exact stroke-style paths (feather-icon language) — ``fill="none"``
    and no local ``stroke``, so color is set by whatever the referencing
    ``<use>`` element specifies."""
    return f"""<defs>
<pattern id="wf-dotbg-{suffix}" width="7" height="7" patternUnits="userSpaceOnUse">
  <rect width="7" height="7" fill="#ffffff"/><circle cx="1" cy="1" r="0.55" fill="#dfe4f0"/>
</pattern>
<symbol id="wf-i-bars-{suffix}" viewBox="0 0 24 24"><path d="M3 21h18M6 21V10M11 21V4M16 21v-9M21 21V7" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="wf-i-line-{suffix}" viewBox="0 0 24 24"><path d="M3 17l6-6 4 4 8-9" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="wf-i-combo-{suffix}" viewBox="0 0 24 24"><path d="M3 21h4v-7H3zM10 21h4v-11h-4zM17 21h4v-5h-4M4 11l5-4 4 3 6-6" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="wf-i-area-{suffix}" viewBox="0 0 24 24"><path d="M3 20h18L16 8l-4 5-3-3z" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="wf-i-pin-{suffix}" viewBox="0 0 24 24"><path d="M12 21s-7-6.2-7-11a7 7 0 1114 0c0 4.8-7 11-7 11z" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="10" r="2.5" fill="none" stroke-width="2"/></symbol>
<symbol id="wf-i-matrix-{suffix}" viewBox="0 0 24 24"><path d="M3 3h8v8H3zM13 3h8v8h-8zM3 13h8v8H3zM13 13h8v8h-8z" fill="none" stroke-width="1.7" stroke-linejoin="round"/></symbol>
<symbol id="wf-i-card123-{suffix}" viewBox="0 0 24 24"><rect x="2" y="5" width="20" height="14" rx="2" fill="none" stroke-width="2"/><path d="M2 10h20" fill="none" stroke-width="2"/></symbol>
<symbol id="wf-i-tree-{suffix}" viewBox="0 0 24 24"><circle cx="12" cy="5" r="2" fill="none" stroke-width="2"/><circle cx="5" cy="19" r="2" fill="none" stroke-width="2"/><circle cx="19" cy="19" r="2" fill="none" stroke-width="2"/><path d="M12 7l-5.5 10M12 7l5.5 10" fill="none" stroke-width="1.7" stroke-linecap="round"/></symbol>
<symbol id="wf-i-funnel-{suffix}" viewBox="0 0 24 24"><path d="M22 3H2l8 9.5V19l4 2v-8.5z" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="wf-i-button-{suffix}" viewBox="0 0 24 24"><rect x="3" y="8" width="18" height="8" rx="4" fill="none" stroke-width="1.7"/></symbol>
<symbol id="wf-i-image-{suffix}" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke-width="1.7"/><circle cx="8.5" cy="8.5" r="1.5" fill="none" stroke-width="1.7"/><path d="M21 15l-5-5L5 21" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="wf-i-textbox-{suffix}" viewBox="0 0 24 24"><path d="M4 7V4h16v3M9 20h6M12 4v16" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="wf-i-shape-{suffix}" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3" fill="none" stroke-width="1.7"/></symbol>
</defs>"""


# Rounded-pill legend chips (v4) instead of v2's plain swatch squares.
_LEGEND = (
    '<div class="legend legend--upper wf-legend">'
    '<span class="wf-chip"><i class="wf-chip-dot wf-chip-dot--data"></i>Data visual</span>'
    '<span class="wf-chip"><i class="wf-chip-dot wf-chip-dot--slicer"></i>Slicer</span>'
    '<span class="wf-chip"><i class="wf-chip-dot wf-chip-dot--nav"></i>Navigation</span>'
    '<span class="wf-chip"><i class="wf-chip-dot wf-chip-dot--deco"></i>Decorative</span>'
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


def _ghost_kpi(cx: float, cy: float, cw: float, ch: float, accent: str) -> list[str]:
    """A settled (non-animated) KPI ghost: a placeholder value — never a
    real or invented number — plus a small already-drawn sparkline."""
    if cw < 30 or ch < 10:
        return []
    out = [f'<text x="{cx:.1f}" y="{cy + 8:.1f}" font-size="8" font-weight="600" '
           f'font-family="\'Poppins\', sans-serif" fill="{_INK}">▬▬.▬</text>']
    if ch >= 20:
        pts = [0, 0.86, 0.18, 0.64, 0.36, 0.72, 0.54, 0.43, 0.72, 0.5, 0.9, 0.21, 1, 0.07]
        spark_y = cy + 13
        spark_h = min(ch - 13, 8)
        coords = " ".join(
            f"{cx + pts[i] * cw:.1f},{spark_y + pts[i + 1] * spark_h:.1f}"
            for i in range(0, len(pts), 2)
        )
        out.append(f'<polyline points="{coords}" fill="none" stroke="{accent}" stroke-width="1.2" '
                   f'stroke-linecap="round" stroke-linejoin="round"/>')
    return out


def _ghost_bars(cx: float, cy: float, cw: float, ch: float, accent: str, suffix: str) -> list[str]:
    """Settled (already at full height) schematic bars — a fixed relative
    pattern, alternating full/soft opacity, never real values."""
    if cw < 24 or ch < 12:
        return []
    heights = [0.5, 0.78, 0.4, 0.92, 0.63, 0.74]
    n = len(heights)
    gap = cw * 0.03
    bar_w = (cw - gap * (n - 1)) / n
    out = [f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{cw:.1f}" height="{ch:.1f}" '
          f'fill="url(#wf-dotbg-{suffix})" rx="2"/>']
    for i, hf in enumerate(heights):
        bh = ch * hf
        bx = cx + i * (bar_w + gap)
        by = cy + ch - bh
        opacity = "1" if i % 2 == 0 else "0.5"
        out.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
                   f'rx="0.8" fill="{accent}" opacity="{opacity}"/>')
    return out


def _ghost_line(cx: float, cy: float, cw: float, ch: float, accent: str, suffix: str, uid: str) -> list[str]:
    """A settled (fully drawn) schematic trend line with a soft area fill
    underneath and an emphasized endpoint — a fixed shape, never real data."""
    if cw < 24 or ch < 12:
        return []
    pts = [0, 0.78, 0.2, 0.58, 0.4, 0.66, 0.6, 0.32, 0.8, 0.4, 1, 0.12]
    coords = [(cx + pts[i] * cw, cy + pts[i + 1] * ch) for i in range(0, len(pts), 2)]
    line_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area_d = line_d + f" L {cx + cw:.1f},{cy + ch:.1f} L {cx:.1f},{cy + ch:.1f} Z"
    grad_id = f"wf-line-grad-{suffix}-{uid}"
    ex, ey = coords[-1]
    return [
        f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{cw:.1f}" height="{ch:.1f}" fill="url(#wf-dotbg-{suffix})" rx="2"/>',
        f'<defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{accent}" stop-opacity="0.22"/>'
        f'<stop offset="1" stop-color="{accent}" stop-opacity="0"/></linearGradient></defs>',
        f'<path d="{area_d}" fill="url(#{grad_id})"/>',
        f'<path d="{line_d}" fill="none" stroke="{accent}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>',
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="1.8" fill="#ffffff" stroke="{accent}" stroke-width="1.4"/>',
    ]


def _ghost_map(cx: float, cy: float, cw: float, ch: float, accent: str, suffix: str) -> list[str]:
    """A settled cluster of static dots at three sizes — schematic, not a
    real geographic distribution."""
    if cw < 24 or ch < 12:
        return []
    dots = [
        (0.26, 0.24, "big"), (0.44, 0.14, "mid"), (0.62, 0.4, "mid"),
        (0.74, 0.18, "big"), (0.35, 0.6, "sm"), (0.55, 0.72, "mid"), (0.18, 0.5, "sm"),
    ]
    sizes = {"big": (1.9, 1), "mid": (1.3, 0.75), "sm": (0.85, 0.45)}
    out = [f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{cw:.1f}" height="{ch:.1f}" '
          f'fill="url(#wf-dotbg-{suffix})" rx="2"/>']
    for fx, fy, size in dots:
        r, opacity = sizes[size]
        out.append(f'<circle cx="{cx + fx * cw:.1f}" cy="{cy + fy * ch:.1f}" r="{r:.1f}" '
                   f'fill="{accent}" opacity="{opacity}"/>')
    return out


def render_wireframe(
    page: "Page", *,
    measure_names: frozenset[str] = frozenset(),
    field_param_tables: frozenset[str] = frozenset(),
    visual_anchor_map: dict[tuple, str] | None = None,
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
    order), producing a dead link (I3).

    ``visual_anchor_map``, when given (``report_pages()`` always supplies
    one), maps a visual's ``(title, friendly_type, frozenset(metrics),
    frozenset(dims))`` group key to its *resolved* table-row anchor slug —
    the id it actually gets after ``report_pages()`` groups 2+ identical
    visuals into one row (relabeled "Label — Type ×N") and after
    ``dedupe_ids`` resolves any remaining slug collision between different
    rows. Without the map, a group's link would still point at the raw,
    un-relabeled/un-deduped slug — a guaranteed dead link for any page with
    two or more visuals identical in title/type/metrics/dims (I3). Callers
    that render standalone (tests, or any future caller with no matching
    table) fall back to the raw slug, same as before this map existed."""
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
    margin = 8  # inset between the canvas's rounded edge and the visuals
    # sitting on it (v4's own padded-canvas treatment) — scale/size the
    # content area *inside* that inset, not the full viewBox, or a visual
    # at real x=0/y=0 lands exactly on the viewBox edge while the canvas
    # rect it's meant to sit on starts `margin` units further in, poking
    # the card's square corner out past the canvas's rounded one.
    content_w = target_w - 2 * margin
    scale = content_w / page_w
    content_h = page_h * scale
    target_h = content_h + 2 * margin

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
        f'role="img" aria-labelledby="{title_id}">\n<style>text {{ font-family: "Poppins", sans-serif !important; text-transform: uppercase; }}</style>'
    ]
    svg.append(f'<title id="{title_id}">Wireframe layout for page {html_e(page.display_name)}</title>')
    svg.append(_glyph_defs(glyph_suffix))
    # The "slide": a white page with a subtle dot-grid texture and a 1px
    # neutral-edge border — visuals sit on it instead of floating in empty
    # white space. Explicit hex (not shell CSS variables) so the canvas
    # stays light in dark mode, same rule as the interactive model diagram.
    svg.append(
        f'<rect x="{margin}" y="{margin}" width="{content_w:.0f}" '
        f'height="{content_h:.0f}" fill="url(#wf-dotbg-{glyph_suffix})" rx="10" stroke="{_EDGE}" stroke-width="1"/>'
    )

    sorted_visuals = sorted(valid_visuals, key=lambda v: v.z or 0)

    decorative_shown = 0
    decorative_total = sum(1 for v in sorted_visuals if _category(v) == "decorative")
    decorative_overflow = 0

    for v in sorted_visuals:
        vx, vy = margin + v.x * scale, margin + v.y * scale
        vw, vh = v.width * scale, v.height * scale
        if vw <= 0 or vh <= 0:
            continue

        category = _category(v)

        # Tiny-object handling (J.C item 6): anything under 0.5% of the page
        # area renders as an unlabeled, unlinked dot — a full card would be
        # unreadable and misleading at that size regardless of type.
        if (v.width * v.height) < 0.005 * page_area:
            cx, cy = vx + vw / 2, vy + vh / 2
            svg.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="1.5" fill="{_FAINT}"/>')
            continue

        # Collapse decorative clutter (J.C item 6): once a page has 3+
        # decorative objects, show the first two individually and fold the
        # rest into one footer note instead of a wall of near-identical cards.
        if category == "decorative" and decorative_total >= 3:
            decorative_shown += 1
            if decorative_shown > 2:
                decorative_overflow += 1
                continue

        accent, soft = _STYLE[category]
        friendly = friendly_visual_type(v.type)
        label = v.title or friendly

        # ---- the card ----
        rx = 5
        card = [
            f'<rect x="{vx:.1f}" y="{vy:.1f}" width="{vw:.1f}" height="{vh:.1f}" rx="{rx}" '
            f'class="wf-card-bg cat-{category}" fill="{_SURFACE}" stroke="{_EDGE}" stroke-width="1"/>'
        ]
        # Top accent bar — inset slightly so its sharp corners sit inside
        # the card's own rounded border instead of poking past it.
        card.append(
            f'<rect x="{vx + 0.6:.1f}" y="{vy + 0.6:.1f}" width="{vw - 1.2:.1f}" height="1.6" fill="{accent}"/>'
        )

        glyph = _GLYPH_BY_TYPE.get(v.type)
        badge = 10 if vw >= 60 and vh >= 24 else 7.5
        has_badge = glyph and vw > 24 and vh > 20
        badge_x, badge_y = vx + 5, vy + 5.5
        title_x = badge_x + (badge + 3 if has_badge else 0)
        if has_badge:
            card.append(f'<rect x="{badge_x:.1f}" y="{badge_y:.1f}" width="{badge:.1f}" height="{badge:.1f}" '
                       f'rx="2.4" fill="{soft}"/>')
            isz = badge * 0.62
            ioff = (badge - isz) / 2
            card.append(f'<use href="#wf-i-{glyph}-{glyph_suffix}" x="{badge_x + ioff:.1f}" y="{badge_y + ioff:.1f}" '
                       f'width="{isz:.1f}" height="{isz:.1f}" fill="none" stroke="{accent}"/>')

        # Title-first labels (J.C item 3): large cards get the visual's own
        # title plus its friendly type underneath; medium cards get just
        # the title (smaller badge, no sub-label — no room to be legible);
        # small cards get badge/accent only, no text at all.
        title_y = badge_y + badge / 2 + 2.6
        content_top = vy + vh  # no ghost content unless a large card sets it below
        if vw >= 60 and vh >= 24:
            title_text = _truncate(v.title, 22) if v.title else friendly
            card.append(f'<text x="{title_x:.1f}" y="{title_y:.1f}" font-size="7.5" font-family="\'Poppins\', sans-serif" '
                      f'font-weight="600" fill="{_INK}">{html_e(title_text)}</text>')
            sub_y = title_y + 8
            if v.title:
                card.append(f'<text x="{title_x:.1f}" y="{sub_y:.1f}" font-size="6" font-family="\'Poppins\', sans-serif" '
                          f'fill="{_MUTED}" letter-spacing="0.2">{html_e(friendly)}</text>')
                content_top = sub_y + 5
            else:
                content_top = title_y + 5
            # Dimension tag — real box pixel size, top-right, hover-reveal.
            card.append(f'<text x="{vx + vw - 4:.1f}" y="{vy + 9:.1f}" font-size="5.5" text-anchor="end" '
                      f'class="wf-tag" fill="{_FAINT}">{v.width:.0f} × {v.height:.0f}</text>')
        elif vw >= 35 and vh >= 18:
            card.append(f'<text x="{title_x:.1f}" y="{vy + vh / 2 + 2:.1f}" font-size="6.5" '
                      f'font-family="\'Poppins\', sans-serif" fill="{_INK}">{html_e(friendly)}</text>')

        # Ghost content (large cards only, room permitting) — a settled,
        # schematic placeholder; never a real or invented value.
        cw, ch = vw - 8, vy + vh - content_top - 4
        cx0, cy0 = vx + 4, content_top
        glyph_key = glyph or ""
        if glyph_key in _GHOST_KPI:
            card.extend(_ghost_kpi(cx0, cy0, cw, ch, accent))
        elif glyph_key in _GHOST_BARS:
            card.extend(_ghost_bars(cx0, cy0, cw, ch, accent, glyph_suffix))
        elif glyph_key in _GHOST_LINE:
            card.extend(_ghost_line(cx0, cy0, cw, ch, accent, glyph_suffix, v.id))
        elif glyph_key in _GHOST_MAP:
            card.extend(_ghost_map(cx0, cy0, cw, ch, accent, glyph_suffix))

        group = [f'<g class="wf-node cat-{category}">'] + card + ["</g>"]

        if category == "data":
            from ..agents.report_facts import is_field_selector

            metrics, dims = [], []
            for f in v.fields:
                if is_field_selector(f, field_param_tables):
                    continue
                leaf = f.split(".")[-1]
                (metrics if leaf in measure_names else dims).append(leaf)
            # Same label a caller's table row was built with (report_facts's
            # visual_label()) — not the simpler ``label`` used for the
            # on-canvas text above — so the link always resolves (I3).
            link_label = visual_label(v.title, v.type, metrics, dims)
            field_leaves = ", ".join(
                f.split(".")[-1] for f in v.fields if not is_field_selector(f, field_param_tables)
            ) or "no fields bound"
            tooltip = f"{label} — {friendly} ({field_leaves})"
            # report_pages()'s own group key for this exact visual — look up
            # its *resolved* (relabeled/deduped) row anchor; only a caller
            # with no map (no matching table) falls back to the raw slug.
            visual_key = (v.title, friendly, frozenset(metrics), frozenset(dims))
            visual_slug = (visual_anchor_map or {}).get(visual_key) or anchor_slug(link_label)
            svg.append(f'<a href="#visual-{page_title_slug}-{visual_slug}">')
            svg.append(f"<title>{html_e(tooltip)}</title>")
            svg.extend(group)
            svg.append("</a>")
        elif category == "slicer":
            svg.append(f'<a href="#{page_anchor}">')
            svg.append(f"<title>{html_e(label)} — filters this page</title>")
            svg.extend(group)
            svg.append("</a>")
        else:
            # Buttons/nav and decorative shapes/images/text: not linked —
            # there's no per-object row anywhere in the document for them
            # to resolve to (I3).
            svg.extend(group)

    svg.append("</svg>")

    footer = ""
    if decorative_overflow:
        footer = f'<p class="wf-footer">+{decorative_overflow} decorative shape(s)</p>'

    return f'<div class="diagram">{"".join(svg)}{footer}{_LEGEND}</div>'
