"""Page wireframe SVG — a scaled layout of a report page's visuals (3.1).

v6 "Product mock" (2026-07-11, replacing the v5 dashed "Blueprint"): every
visual renders as a real white card — soft shadow, hairline border, a
gradient icon chip + real-case Poppins title + small-caps type caption —
with a per-type *skeleton chart* ghosted inside it (bars, line + area fill,
map landmass + pins, matrix rows, KPI blocks, decomposition tree, slicer
chips…), so a page reads like a believable product mockup instead of an
annotated sketch. The sheet is the shared v6 canvas (soft gradient + dot
grid, see ``_diagram_theme``) and gains a Power BI-style page-tab bar:
the active page as a pill, sibling pages as linked ghost tabs.

Same four categories (data/slicer/nav/decorative — decorative/nav cards are
quieter "ghost" cards), same real x/y/width/height positions, same
tiny-object / decorative-overflow collapse, and the same
hover-via-CSS-class / I3 link-resolution logic as v5 — only the visual
language changed. Still pure inline SVG: zero external dependencies.
"""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..schemas.model import Page, Visual

from ..agents.report_facts import friendly_visual_type, visual_label
from ._diagram_theme import (
    ACCENT, CAPTION, EDGE, FAINT, GHOST_EDGE, HAIRLINE, INK, MUTED,
    SKELETON, SKELETON_SOFT, canvas, canvas_defs, chip, legend,
)
from ._shared import anchor_slug, html_e, pluralize_count

# Non-data layout elements — quieter styling, never linked to a
# data-dictionary row (I3).
_DECORATIVE_TYPES = {"image", "shape", "basicShape", "textbox"}
_NAV_TYPES = {"actionButton", "button", "navBar", "bookmarkNavigator"}
_SLICER_TYPES = {"slicer", "advancedSlicerVisual"}

# visualType -> glyph id (feather-style stroke icons, drawn white inside the
# category-gradient chip).
_GLYPH_BY_TYPE = {
    "clusteredColumnChart": "bars", "columnChart": "bars",
    "hundredPercentStackedColumnChart": "bars", "stackedColumnChart": "bars",
    "clusteredBarChart": "bars", "barChart": "bars",
    "hundredPercentStackedBarChart": "bars", "stackedBarChart": "bars",
    "lineChart": "line",
    "lineStackedColumnComboChart": "combo", "lineClusteredColumnComboChart": "combo",
    "areaChart": "area", "stackedAreaChart": "area", "ribbonChart": "area",
    "map": "pin", "filledMap": "pin", "shapeMap": "pin",
    "tableEx": "matrix", "table": "matrix", "pivotTable": "matrix", "matrix": "matrix",
    "card": "card123", "multiRowCard": "card123", "kpi": "card123",
    "decompositionTreeVisual": "tree", "decompositionTree": "tree",
    "keyInfluencersVisual": "tree",
    "pieChart": "donut", "donutChart": "donut",
    "gauge": "gauge", "scatterChart": "scatter", "treemap": "treemap",
    "funnel": "funnel", "waterfallChart": "bars",
    "slicer": "funnel", "advancedSlicerVisual": "funnel",
    "actionButton": "button", "button": "button", "navBar": "button", "bookmarkNavigator": "button",
    "image": "image", "textbox": "textbox", "shape": "shape", "basicShape": "shape",
    "qnaVisual": "textbox", "visualGroup": "shape",
}

# visualType -> skeleton kind. Anything not listed falls back through its
# glyph, then to generic vertical bars — every data visual gets *some*
# believable ghost content.
_SKEL_OVERRIDE = {
    "clusteredBarChart": "hbars", "barChart": "hbars",
    "hundredPercentStackedBarChart": "hbars", "stackedBarChart": "hbars",
    "funnel": "funnelchart",
    "slicer": "chips", "advancedSlicerVisual": "chips",
}
_SKEL_BY_GLYPH = {
    "bars": "bars", "line": "line", "combo": "combo", "area": "area",
    "pin": "pin", "matrix": "matrix", "card123": "card123", "tree": "tree",
    "donut": "donut", "gauge": "gauge", "scatter": "scatter",
    "treemap": "treemap", "funnel": "chips", "button": "button",
    "image": "imageframe", "textbox": "textlines", "shape": "none",
}

# Deterministic "random" heights/positions for skeleton content — rotated by
# a per-visual CRC so charts differ from card to card but never between two
# runs (golden-file stability).
_VARIATION = (0.52, 0.78, 0.40, 0.95, 0.62, 0.72, 0.48, 0.85, 0.58, 0.68)


def _defs(suffix: str) -> str:
    """Glyph ``<symbol>`` defs + the shared v6 canvas/chip/skeleton defs,
    namespaced by ``suffix`` — each wireframe is a self-contained SVG
    embedded independently (one per page), so without a per-instance suffix a
    document with more than one page would define the same ``id`` twice. All
    icons are stroke-style paths (``fill="none"``, no local ``stroke``), so
    color is set by the referencing ``<use>``."""
    return f"""<defs>{canvas_defs(suffix)}
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
<symbol id="wf-i-donut-{suffix}" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" fill="none" stroke-width="2"/><path d="M12 4v5M12 12l5.5 5.5" fill="none" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="wf-i-gauge-{suffix}" viewBox="0 0 24 24"><path d="M4 18a8.5 8.5 0 1116 0" fill="none" stroke-width="2" stroke-linecap="round"/><path d="M12 18l4-6" fill="none" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="wf-i-scatter-{suffix}" viewBox="0 0 24 24"><path d="M4 20V4M4 20h16" fill="none" stroke-width="2" stroke-linecap="round"/><circle cx="10" cy="14" r="1.7" fill="none" stroke-width="1.7"/><circle cx="15" cy="9" r="1.7" fill="none" stroke-width="1.7"/><circle cx="19" cy="15" r="1.7" fill="none" stroke-width="1.7"/></symbol>
<symbol id="wf-i-treemap-{suffix}" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke-width="1.7"/><path d="M12 3v18M12 12h9" fill="none" stroke-width="1.7"/></symbol>
</defs>"""


_LEGEND = legend([("data", "Data visual"), ("slicer", "Slicer"),
                  ("nav", "Navigation"), ("deco", "Decorative")])


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


def _seed(v: "Visual") -> int:
    return zlib.crc32((v.id or "x").encode("utf-8", "ignore"))


def _var(seed: int, i: int) -> float:
    return _VARIATION[(seed + i) % len(_VARIATION)]


# --------------------------------------------------------------------------
# Skeleton chart content — the per-type "ghost" drawn inside a data card.
# Each helper fills the box (x0, y0)-(x1, y1); all geometry is deterministic
# (seeded by the visual id) so output is stable run to run.
# --------------------------------------------------------------------------

def _sk_bars(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    w, h = x1 - x0, y1 - y0
    n = max(4, min(9, int(w / 34)))
    slot = w / n
    bw = slot * 0.72
    out = []
    for i in range(n):
        bh = h * (0.35 + 0.6 * _var(seed, i))
        out.append(f'<rect x="{x0 + i * slot + (slot - bw) / 2:.1f}" y="{y1 - bh:.1f}" '
                   f'width="{bw:.1f}" height="{bh:.1f}" rx="2.5" fill="url(#dg-sk-{cat}-{sfx})"/>')
    out.append(f'<line x1="{x0 - 2:.1f}" y1="{y1 + 0.5:.1f}" x2="{x1 + 2:.1f}" y2="{y1 + 0.5:.1f}" '
               f'stroke="{HAIRLINE}" stroke-width="1"/>')
    return "".join(out)


def _sk_hbars(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    w, h = x1 - x0, y1 - y0
    n = max(3, min(6, int(h / 24)))
    row = h / n
    bh = min(10.0, row * 0.55)
    out = []
    for i in range(n):
        bw = w * (0.3 + 0.65 * _var(seed, i))
        out.append(f'<rect x="{x0:.1f}" y="{y0 + i * row + (row - bh) / 2:.1f}" '
                   f'width="{bw:.1f}" height="{bh:.1f}" rx="2.5" fill="url(#dg-sk-{cat}-{sfx})"/>')
    out.append(f'<line x1="{x0 - 0.5:.1f}" y1="{y0 - 2:.1f}" x2="{x0 - 0.5:.1f}" y2="{y1 + 2:.1f}" '
               f'stroke="{HAIRLINE}" stroke-width="1"/>')
    return "".join(out)


def _line_points(x0: float, x1: float, y0: float, y1: float, seed: int, off: int = 0) -> list[tuple[float, float]]:
    w, h = x1 - x0, y1 - y0
    n = max(4, min(8, int(w / 48) + 2))
    return [(x0 + w * i / (n - 1), y1 - h * (0.2 + 0.7 * _var(seed, i + off))) for i in range(n)]


def _sk_line(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    pts = _line_points(x0, x1, y0, y1, seed)
    line = " L ".join(f"{px:.1f} {py:.1f}" for px, py in pts)
    return (
        f'<path d="M {x0:.1f} {y1:.1f} L {line} L {x1:.1f} {y1:.1f} Z" fill="url(#dg-area-{cat}-{sfx})"/>'
        f'<path d="M {line}" fill="none" stroke="{accent}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity=".75"/>'
        f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.6" fill="{accent}"/>'
        f'<line x1="{x0 - 2:.1f}" y1="{y1 + 0.5:.1f}" x2="{x1 + 2:.1f}" y2="{y1 + 0.5:.1f}" '
        f'stroke="{HAIRLINE}" stroke-width="1"/>'
    )


def _sk_combo(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    # Bars in the category accent + an overlay line in the amber companion
    # hue — the two-tone is what reads "combo".
    line_accent = ACCENT["slicer"] if cat != "slicer" else ACCENT["data"]
    pts = _line_points(x0 + 6, x1 - 6, y0, y1 - 4, seed, off=3)
    line = " L ".join(f"{px:.1f} {py:.1f}" for px, py in pts)
    return (
        _sk_bars(x0, y0, x1, y1, cat, sfx, seed)
        + f'<path d="M {line}" fill="none" stroke="{line_accent}" stroke-width="1.8" '
          f'stroke-linecap="round" stroke-linejoin="round" opacity=".8"/>'
        + f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.6" fill="{line_accent}"/>'
    )


def _sk_area(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0

    def wave(base: float, amp: float, off: int) -> str:
        pts = [(x0 + w * i / 4, y1 - h * (base + amp * _var(seed, i + off))) for i in range(5)]
        d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"
        for i in range(1, 5):
            mx = (pts[i - 1][0] + pts[i][0]) / 2
            d += f" C {mx:.1f} {pts[i - 1][1]:.1f}, {mx:.1f} {pts[i][1]:.1f}, {pts[i][0]:.1f} {pts[i][1]:.1f}"
        return d

    back = wave(0.45, 0.45, 0)
    front = wave(0.12, 0.3, 5)
    return (
        f'<path d="{back} L {x1:.1f} {y1:.1f} L {x0:.1f} {y1:.1f} Z" fill="url(#dg-area-{cat}-{sfx})"/>'
        f'<path d="{back}" fill="none" stroke="{accent}" stroke-width="1.8" stroke-linecap="round" opacity=".7"/>'
        f'<path d="{front} L {x1:.1f} {y1:.1f} L {x0:.1f} {y1:.1f} Z" fill="{accent}" fill-opacity=".14"/>'
        f'<path d="{front}" fill="none" stroke="{accent}" stroke-width="1.6" stroke-linecap="round" opacity=".4"/>'
        f'<line x1="{x0 - 2:.1f}" y1="{y1 + 0.5:.1f}" x2="{x1 + 2:.1f}" y2="{y1 + 0.5:.1f}" '
        f'stroke="{HAIRLINE}" stroke-width="1"/>'
    )


def _sk_pin(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    # An abstract rounded "landmass" fitted to the box + proportional pins.
    blob = (
        f'M {x0 + 0.08 * w:.1f} {y0 + 0.62 * h:.1f} '
        f'q {0.05 * w:.1f} {-0.34 * h:.1f} {0.2 * w:.1f} {-0.3 * h:.1f} '
        f'q {0.1 * w:.1f} {-0.18 * h:.1f} {0.26 * w:.1f} {-0.1 * h:.1f} '
        f'q {0.16 * w:.1f} {-0.12 * h:.1f} {0.28 * w:.1f} {0.04 * h:.1f} '
        f'q {0.14 * w:.1f} {0.02 * h:.1f} {0.1 * w:.1f} {0.22 * h:.1f} '
        f'q {-0.04 * w:.1f} {0.24 * h:.1f} {-0.22 * w:.1f} {0.22 * h:.1f} '
        f'q {-0.14 * w:.1f} {0.14 * h:.1f} {-0.3 * w:.1f} {0.06 * h:.1f} '
        f'q {-0.2 * w:.1f} {0.06 * h:.1f} {-0.26 * w:.1f} {-0.08 * h:.1f} z'
    )
    spots = ((0.3, 0.42, 1.0), (0.55, 0.3, 1.45), (0.74, 0.62, 0.85), (0.42, 0.68, 0.65))
    pins = []
    for i, (fx, fy, scale) in enumerate(spots):
        r = (2.2 + 2.2 * _var(seed, i)) * scale
        px, py = x0 + fx * w, y0 + fy * h
        pins.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r * 2.2:.1f}" fill="{accent}" fill-opacity=".2"/>'
                    f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r:.1f}" fill="{accent}"/>')
    return (f'<path d="{blob}" fill="{accent}" fill-opacity=".08" '
            f'stroke="{accent}" stroke-opacity=".18" stroke-width="1"/>' + "".join(pins))


def _sk_matrix(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    rows = max(2, min(5, int((h - 14) / 17)))
    out = [f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="11" rx="3" '
           f'fill="{accent}" fill-opacity=".15"/>']
    row_h = (h - 15) / rows
    for i in range(rows):
        ry = y0 + 15 + i * row_h + (row_h - 7) / 2
        c1 = w * (0.2 + 0.12 * _var(seed, i))
        out.append(f'<rect x="{x0:.1f}" y="{ry:.1f}" width="{c1:.1f}" height="7" rx="3.5" fill="{SKELETON}"/>')
        out.append(f'<rect x="{x0 + w * 0.48:.1f}" y="{ry:.1f}" width="{w * 0.16:.1f}" height="7" rx="3.5" fill="{SKELETON_SOFT}"/>')
        out.append(f'<rect x="{x0 + w * 0.76:.1f}" y="{ry:.1f}" width="{w * 0.13:.1f}" height="7" rx="3.5" fill="{SKELETON_SOFT}"/>')
    return "".join(out)


def _sk_card123(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    cy = y0 + h * 0.38
    big_w = min(w * 0.52, 96.0)
    return (
        f'<rect x="{x0:.1f}" y="{cy - 7:.1f}" width="{big_w:.1f}" height="13" rx="4" fill="#dfe4ef"/>'
        f'<rect x="{x0:.1f}" y="{cy + 12:.1f}" width="{big_w * 0.62:.1f}" height="6" rx="3" fill="{SKELETON_SOFT}"/>'
        f'<path d="M {x0 + big_w + 12:.1f} {cy + 1:.1f} l 5 -7 l 5 7 z" fill="{accent}" fill-opacity=".8"/>'
    )


def _sk_tree(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    yc = y0 + h / 2
    rw, rh = w * 0.2, 16.0
    c_x = x0 + w * 0.42
    g_x = x0 + w * 0.76
    kids = (yc - h * 0.34, yc, yc + h * 0.34)
    out = [f'<rect x="{x0:.1f}" y="{yc - rh / 2:.1f}" width="{rw:.1f}" height="{rh}" rx="5" fill="url(#dg-sk-{cat}-{sfx})"/>']
    for ky in kids:
        out.append(f'<path d="M {x0 + rw:.1f} {yc:.1f} C {x0 + rw + (c_x - x0 - rw) / 2:.1f} {yc:.1f}, '
                   f'{x0 + rw + (c_x - x0 - rw) / 2:.1f} {ky:.1f}, {c_x:.1f} {ky:.1f}" '
                   f'fill="none" stroke="#cdd4e2" stroke-width="1.4"/>')
        out.append(f'<rect x="{c_x:.1f}" y="{ky - 7:.1f}" width="{w * 0.16:.1f}" height="14" rx="4.5" '
                   f'fill="{accent}" fill-opacity=".16"/>')
    for gy in (kids[1] - h * 0.16, kids[1] + h * 0.16):
        out.append(f'<path d="M {c_x + w * 0.16:.1f} {kids[1]:.1f} C {c_x + w * 0.16 + (g_x - c_x - w * 0.16) / 2:.1f} {kids[1]:.1f}, '
                   f'{c_x + w * 0.16 + (g_x - c_x - w * 0.16) / 2:.1f} {gy:.1f}, {g_x:.1f} {gy:.1f}" '
                   f'fill="none" stroke="#cdd4e2" stroke-width="1.4"/>')
        out.append(f'<rect x="{g_x:.1f}" y="{gy - 6:.1f}" width="{w * 0.13:.1f}" height="12" rx="4" fill="{SKELETON_SOFT}"/>')
    return "".join(out)


def _sk_donut(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    r = min(h, w * 0.5) * 0.38
    cx = x0 + (w * 0.28 if w > 150 else w * 0.5)
    cy = y0 + h / 2
    ring = r * 0.42
    out = [
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="none" stroke="{accent}" '
        f'stroke-opacity=".16" stroke-width="{ring:.1f}"/>',
        # ~70% arc in the accent, rotated by the seed for variety.
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="none" stroke="{accent}" stroke-opacity=".65" '
        f'stroke-width="{ring:.1f}" stroke-linecap="round" '
        f'stroke-dasharray="{2 * 3.1416 * r * 0.7:.1f} {2 * 3.1416 * r:.1f}" '
        f'transform="rotate({-90 + (seed % 60)} {cx:.1f} {cy:.1f})"/>',
    ]
    if w > 150:
        lx = cx + r + ring + 14
        for i in range(3):
            ly = cy - 14 + i * 14
            out.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.5" fill="{accent}" fill-opacity="{0.75 - 0.25 * i}"/>')
            out.append(f'<rect x="{lx + 8:.1f}" y="{ly - 3:.1f}" width="{min(52.0, x1 - lx - 12):.1f}" height="6" rx="3" fill="{SKELETON_SOFT}"/>')
    return "".join(out)


def _sk_gauge(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    r = min(w * 0.32, h * 0.78)
    cx, cy = x0 + w / 2, y0 + h * 0.86
    arc = 3.1416 * r
    frac = 0.45 + 0.35 * _var(seed, 0)
    return (
        f'<path d="M {cx - r:.1f} {cy:.1f} A {r:.1f} {r:.1f} 0 0 1 {cx + r:.1f} {cy:.1f}" fill="none" '
        f'stroke="{SKELETON}" stroke-width="9" stroke-linecap="round"/>'
        f'<path d="M {cx - r:.1f} {cy:.1f} A {r:.1f} {r:.1f} 0 0 1 {cx + r:.1f} {cy:.1f}" fill="none" '
        f'stroke="{accent}" stroke-opacity=".7" stroke-width="9" stroke-linecap="round" '
        f'stroke-dasharray="{arc * frac:.1f} {arc:.1f}"/>'
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" fill="{accent}"/>'
    )


def _sk_scatter(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    out = [f'<path d="M {x0:.1f} {y0:.1f} V {y1:.1f} H {x1:.1f}" fill="none" stroke="{HAIRLINE}" stroke-width="1"/>']
    n = max(6, min(11, int(w / 30)))
    for i in range(n):
        px = x0 + w * (0.08 + 0.86 * _var(seed, i * 2))
        py = y0 + h * (0.1 + 0.75 * _var(seed, i * 2 + 1))
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{2 + 2 * _var(seed, i + 5):.1f}" '
                   f'fill="{accent}" fill-opacity="{0.25 + 0.5 * _var(seed, i + 3):.2f}"/>')
    return "".join(out)


def _sk_treemap(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    split = 0.44 + 0.12 * _var(seed, 0)
    right_split = 0.5 + 0.16 * _var(seed, 1)
    g = 2.5
    return (
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w * split - g:.1f}" height="{h:.1f}" rx="3.5" fill="{accent}" fill-opacity=".22"/>'
        f'<rect x="{x0 + w * split:.1f}" y="{y0:.1f}" width="{w * (1 - split):.1f}" height="{h * right_split - g:.1f}" rx="3.5" fill="{accent}" fill-opacity=".13"/>'
        f'<rect x="{x0 + w * split:.1f}" y="{y0 + h * right_split:.1f}" width="{w * (1 - split) * 0.55 - g:.1f}" height="{h * (1 - right_split):.1f}" rx="3.5" fill="{accent}" fill-opacity=".08"/>'
        f'<rect x="{x0 + w * split + w * (1 - split) * 0.55:.1f}" y="{y0 + h * right_split:.1f}" width="{w * (1 - split) * 0.45:.1f}" height="{h * (1 - right_split):.1f}" rx="3.5" fill="{SKELETON_SOFT}"/>'
    )


def _sk_funnelchart(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    w, h = x1 - x0, y1 - y0
    n = max(3, min(5, int(h / 20)))
    row = h / n
    bh = min(11.0, row * 0.6)
    cx = x0 + w / 2
    out = []
    for i in range(n):
        bw = w * (0.9 - i * (0.62 / max(1, n - 1)))
        out.append(f'<rect x="{cx - bw / 2:.1f}" y="{y0 + i * row + (row - bh) / 2:.1f}" '
                   f'width="{bw:.1f}" height="{bh:.1f}" rx="{bh / 2:.1f}" fill="url(#dg-sk-{cat}-{sfx})"/>')
    return "".join(out)


def _sk_chips(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    rows = max(2, min(4, int(h / 19)))
    row = h / rows
    out = []
    for i in range(rows):
        ry = y0 + i * row + (row - 9) / 2
        out.append(f'<rect x="{x0:.1f}" y="{ry:.1f}" width="9" height="9" rx="2.5" fill="none" '
                   f'stroke="{accent}" stroke-opacity="{0.85 if i == 0 else 0.4}" stroke-width="1.4"/>')
        if i == 0:
            out.append(f'<path d="M {x0 + 2:.1f} {ry + 4.5:.1f} l 2 2.2 l 3.2 -4" fill="none" '
                       f'stroke="{accent}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>')
        out.append(f'<rect x="{x0 + 15:.1f}" y="{ry + 1.5:.1f}" width="{w * (0.3 + 0.2 * _var(seed, i)):.1f}" '
                   f'height="6" rx="3" fill="{SKELETON if i == 0 else SKELETON_SOFT}"/>')
    return "".join(out)


def _sk_button(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    accent = ACCENT[cat]
    w, h = x1 - x0, y1 - y0
    bw, bh = min(w * 0.7, 110.0), min(19.0, h * 0.8)
    bx, by = x0 + (w - bw) / 2, y0 + (h - bh) / 2
    return (
        f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="{bh / 2:.1f}" '
        f'fill="{accent}" fill-opacity=".1" stroke="{accent}" stroke-opacity=".4" stroke-width="1"/>'
        f'<rect x="{bx + bw / 2 - bw * 0.26:.1f}" y="{by + bh / 2 - 2.5:.1f}" width="{bw * 0.52:.1f}" height="5" rx="2.5" '
        f'fill="{accent}" fill-opacity=".45"/>'
    )


def _sk_textlines(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    w, h = x1 - x0, y1 - y0
    rows = max(1, min(3, int(h / 13)))
    row = h / rows
    widths = (0.78, 0.5, 0.64)
    out = []
    for i in range(rows):
        out.append(f'<rect x="{x0:.1f}" y="{y0 + i * row + (row - 6) / 2:.1f}" '
                   f'width="{w * widths[(seed + i) % 3]:.1f}" height="6" rx="3" '
                   f'fill="{SKELETON if i == 0 else SKELETON_SOFT}"/>')
    return "".join(out)


def _sk_imageframe(x0: float, y0: float, x1: float, y1: float, cat: str, sfx: str, seed: int) -> str:
    w, h = x1 - x0, y1 - y0
    s = min(w, h, 46.0)
    fx, fy = x0 + (w - s) / 2, y0 + (h - s) / 2
    return (
        f'<rect x="{fx:.1f}" y="{fy:.1f}" width="{s:.1f}" height="{s:.1f}" rx="5" fill="#f2f5fa" stroke="#d9dfec" stroke-width="1"/>'
        f'<circle cx="{fx + s * 0.3:.1f}" cy="{fy + s * 0.3:.1f}" r="{s * 0.09:.1f}" fill="#c9d2e4"/>'
        f'<path d="M {fx + s * 0.12:.1f} {fy + s * 0.82:.1f} L {fx + s * 0.42:.1f} {fy + s * 0.46:.1f} '
        f'L {fx + s * 0.62:.1f} {fy + s * 0.64:.1f} L {fx + s * 0.8:.1f} {fy + s * 0.42:.1f} '
        f'L {fx + s * 0.88:.1f} {fy + s * 0.82:.1f} Z" fill="#c9d2e4"/>'
    )


_SKELETONS = {
    "bars": _sk_bars, "hbars": _sk_hbars, "line": _sk_line, "combo": _sk_combo,
    "area": _sk_area, "pin": _sk_pin, "matrix": _sk_matrix, "card123": _sk_card123,
    "tree": _sk_tree, "donut": _sk_donut, "gauge": _sk_gauge, "scatter": _sk_scatter,
    "treemap": _sk_treemap, "funnelchart": _sk_funnelchart, "chips": _sk_chips,
    "button": _sk_button, "textlines": _sk_textlines, "imageframe": _sk_imageframe,
}


def _skeleton_kind(v: "Visual") -> str:
    kind = _SKEL_OVERRIDE.get(v.type)
    if kind:
        return kind
    glyph = _GLYPH_BY_TYPE.get(v.type)
    if glyph:
        return _SKEL_BY_GLYPH.get(glyph, "bars")
    return "bars"


def _tab_bar(ox: float, right: float, y: float, active: str, siblings: list[str],
             page_w: float, page_h: float) -> str:
    """The Power BI-style page-tab strip under the sheet: the active page as
    a white pill with an accent dot, sibling pages as linked ghost tabs
    (``#page-{slug}`` — the same document-agnostic anchor formula the
    slicer links use), a "+N" overflow, and the true page pixel size."""
    out = [f'<line x1="{ox + 14:.1f}" y1="{y:.1f}" x2="{right - 14:.1f}" y2="{y:.1f}" '
           f'stroke="{HAIRLINE}" stroke-width="1"/>']
    tab_y = y + 10
    dims = f"{page_w:.0f} × {page_h:.0f}"
    dims_w = len(dims) * 5.2 + 16
    x = ox + 14
    name_txt = _truncate(active, 24)
    pill_w = 30 + len(name_txt) * 5.8
    out.append(f'<rect x="{x:.1f}" y="{tab_y:.1f}" width="{pill_w:.1f}" height="22" rx="7" '
               f'fill="#ffffff" stroke="{EDGE}"/>')
    out.append(f'<circle cx="{x + 12:.1f}" cy="{tab_y + 11:.1f}" r="3" fill="{ACCENT["nav"]}"/>')
    out.append(f'<text x="{x + 21:.1f}" y="{tab_y + 14.5:.1f}" font-size="9.5" font-weight="600" '
               f'fill="{INK}">{html_e(name_txt)}</text>')
    x += pill_w + 18
    skipped_active = False
    shown, overflow = 0, 0
    for name in siblings:
        if name == active and not skipped_active:
            skipped_active = True
            continue
        txt = _truncate(name, 24)
        w = len(txt) * 5.2
        if x + w > right - dims_w - 40:
            overflow += 1
            continue
        out.append(f'<a href="#page-{anchor_slug(name)}" class="wf-tab">'
                   f'<text x="{x:.1f}" y="{tab_y + 14.5:.1f}" font-size="9.5" font-weight="500" '
                   f'fill="{MUTED}">{html_e(txt)}</text></a>')
        x += w + 18
        shown += 1
    if overflow:
        out.append(f'<text x="{x:.1f}" y="{tab_y + 14.5:.1f}" font-size="9.5" font-weight="500" '
                   f'fill="{FAINT}">+{overflow}</text>')
    out.append(f'<text x="{right - 14:.1f}" y="{tab_y + 14.5:.1f}" font-size="8.5" font-weight="500" '
               f'fill="{FAINT}" text-anchor="end">{dims}</text>')
    return "".join(out)


def render_wireframe(
    page: "Page", *,
    measure_names: frozenset[str] = frozenset(),
    field_param_tables: frozenset[str] = frozenset(),
    visual_anchor_map: dict[tuple, str] | None = None,
    sibling_pages: list[str] | None = None,
) -> str:
    """Render a scaled SVG "sheet" of the page's visuals, if layout
    coordinates exist (pbix-parsed models don't carry them — skip gracefully
    rather than render an empty diagram).

    ``measure_names``/``field_param_tables``, when given, let a data visual's
    link target be computed with the exact same ``report_facts.visual_label()``
    a caller used to build the matching table row — otherwise an untitled
    visual bound only to fields would get a *different* label here than in the
    table, producing a dead link (I3).

    ``visual_anchor_map``, when given (``report_pages()`` always supplies one),
    maps a visual's ``(title, friendly_type, frozenset(metrics),
    frozenset(dims))`` group key to its *resolved* table-row anchor slug — the
    id it actually gets after ``report_pages()`` groups 2+ identical visuals
    into one row and after ``dedupe_ids`` resolves any remaining slug
    collision. Without the map, a group's link would still point at the raw,
    un-relabeled/un-deduped slug — a guaranteed dead link for any page with
    two or more visuals identical in title/type/metrics/dims (I3). Callers
    that render standalone (tests, or any future caller with no matching
    table) fall back to the raw slug, same as before this map existed.

    ``sibling_pages``, when given, is every report page's display name in
    report order — it turns on the page-tab bar under the sheet, with this
    page as the active tab and the rest linked to their ``#page-…`` anchors
    (which exist in both the technical doc and the user guide)."""
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

    # The viewBox is fitted to the union of the sheet and every visual, so a
    # visual dragged partly off the page can't be clipped by a hardcoded
    # width (same robustness fix the lineage rebuild got).
    base_w = 760
    margin = 16
    sheet_w = base_w - 2 * margin
    scale = sheet_w / page_w
    sheet_h = page_h * scale
    ox = oy = margin  # sheet origin

    right = ox + sheet_w
    bottom = oy + sheet_h
    for v in valid_visuals:
        right = max(right, ox + (v.x + v.width) * scale)
        bottom = max(bottom, oy + (v.y + v.height) * scale)
    target_w = right + margin
    show_tabs = bool(sibling_pages)
    target_h = bottom + margin + (46 if show_tabs else 0)

    page_title_slug = anchor_slug(page.display_name)
    # Same anchor formula html.py's Report Pages section and user_guide.py's
    # per-page card use — this SVG is computed once and embedded verbatim in
    # both documents, so it can't carry a document-specific deduped id.
    page_anchor = f"page-{page_title_slug}"
    glyph_suffix = anchor_slug(page.id)
    title_id = f"wireframe-title-{glyph_suffix}"

    svg = [
        f'<svg viewBox="0 0 {target_w:.0f} {target_h:.0f}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-labelledby="{title_id}">\n<style>text {{ font-family: "Poppins", sans-serif !important; }}</style>'
    ]
    svg.append(f'<title id="{title_id}">Wireframe layout for page {html_e(page.display_name)}</title>')
    svg.append(_defs(glyph_suffix))
    # The "sheet": the page area as the shared v6 gradient + dot-grid canvas.
    # Explicit light hex (not shell CSS variables) so it stays light in dark
    # mode, same rule as every diagram canvas.
    svg.append(canvas(ox, oy, sheet_w, sheet_h, glyph_suffix))

    if show_tabs:
        svg.append(_tab_bar(ox, right, bottom + 12, page.display_name, sibling_pages or [],
                            page_w, page_h))

    sorted_visuals = sorted(valid_visuals, key=lambda v: v.z or 0)

    decorative_shown = 0
    decorative_total = sum(1 for v in sorted_visuals if _category(v) == "decorative")
    decorative_overflow = 0

    for v in sorted_visuals:
        vx, vy = ox + v.x * scale, oy + v.y * scale
        vw, vh = v.width * scale, v.height * scale
        if vw <= 0 or vh <= 0:
            continue

        category = _category(v)

        # Tiny-object handling (J.C item 6): anything under 0.5% of the page
        # area renders as an unlabeled, unlinked dot — a full card would be
        # unreadable and misleading at that size regardless of type.
        if (v.width * v.height) < 0.005 * page_area:
            cx, cy = vx + vw / 2, vy + vh / 2
            svg.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="1.8" fill="{FAINT}"/>')
            continue

        # Collapse decorative clutter (J.C item 6): once a page has 3+
        # decorative objects, show the first two individually and fold the
        # rest into one footer note instead of a wall of near-identical boxes.
        if category == "decorative" and decorative_total >= 3:
            decorative_shown += 1
            if decorative_shown > 2:
                decorative_overflow += 1
                continue

        accent = ACCENT[category]
        friendly = friendly_visual_type(v.type)
        glyph = _GLYPH_BY_TYPE.get(v.type)
        seed = _seed(v)
        ghost = category in ("decorative", "nav")

        metrics, dims = [], []
        if category == "data":
            from ..agents.report_facts import is_field_selector

            for f in v.fields:
                if is_field_selector(f, field_param_tables):
                    continue
                leaf = f.split(".")[-1]
                (metrics if leaf in measure_names else dims).append(leaf)
        # The card title: the same reader-facing label the visuals table row
        # uses ("Actual, Plan" / "Var Plan % by Country/Region"), not just the
        # raw type — this is what makes the mock read like the real report.
        link_label = visual_label(v.title, v.type, metrics, dims)
        label = v.title or (link_label if category == "data" else friendly)

        # ---- the card ----
        rx = 10 if min(vw, vh) >= 40 else 8
        card_style = (
            f'fill="#ffffff" fill-opacity=".65" stroke="{EDGE}" stroke-width="1" stroke-dasharray="4 3"'
            if ghost else
            f'fill="#ffffff" stroke="{EDGE}" stroke-width="1"'
        )
        card = [f'<rect x="{vx:.1f}" y="{vy:.1f}" width="{vw:.1f}" height="{vh:.1f}" rx="{rx}" '
                f'class="wf-card-bg cat-{category}" {card_style}/>']

        big = vw >= 110 and vh >= 54
        compact = not big and vw >= 52 and vh >= 21
        sk_top = vy + 20  # body top when there's no header (safety default)

        if big:
            # Header: gradient icon chip + real-case title + small-caps type.
            chip_s = 20.0
            hx, hy = vx + 10, vy + 10
            if glyph:
                card.append(chip(hx, hy, chip_s, category, f"wf-i-{glyph}-{glyph_suffix}", glyph_suffix))
            tx = hx + (chip_s + 8 if glyph else 0)
            max_chars = max(4, int((vx + vw - 10 - tx) / 6.7))
            name_txt = _truncate(label, max_chars)
            card.append(f'<text x="{tx:.1f}" y="{hy + 11:.1f}" font-size="11.5" font-weight="600" '
                        f'fill="{INK}">{html_e(name_txt)}</text>')
            show_caps = label.strip().lower() != friendly.strip().lower()
            if show_caps:
                caps = _truncate(friendly.upper(), max(4, int((vx + vw - 10 - tx) / 5.3)))
                card.append(f'<text x="{tx:.1f}" y="{hy + 24:.1f}" font-size="7.5" font-weight="600" '
                            f'letter-spacing=".08em" fill="{CAPTION}">{html_e(caps)}</text>')
            sk_top = hy + (32 if show_caps else 22)
        elif compact:
            chip_s = 15.0
            hx = vx + 6
            hy = vy + min(5.0, max(3.0, (vh - chip_s) / 2))
            tx = hx
            if glyph:
                card.append(chip(hx, hy, chip_s, category, f"wf-i-{glyph}-{glyph_suffix}", glyph_suffix))
                tx = hx + chip_s + 6
            max_chars = max(2, int((vx + vw - 6 - tx) / 6.3))
            name_txt = _truncate(label, max_chars) if not (ghost and not v.title) else ""
            if name_txt:
                card.append(f'<text x="{tx:.1f}" y="{hy + chip_s / 2 + 3.5:.1f}" font-size="10.5" '
                            f'font-weight="600" fill="{INK}">{html_e(name_txt)}</text>')
                tx += min(len(name_txt) * 6.3, vx + vw - 6 - tx)
            elif ghost:
                # An untitled decorative object: skeleton text bars instead
                # of repeating "Text box" on every one.
                bar_w = max(0.0, vx + vw - 10 - tx)
                if bar_w > 24:
                    card.append(f'<rect x="{tx:.1f}" y="{hy + chip_s / 2 - 3:.1f}" '
                                f'width="{min(bar_w, 120.0):.1f}" height="6" rx="3" fill="{SKELETON}"/>')
                    if bar_w > 160:
                        card.append(f'<rect x="{tx + min(bar_w, 120.0) + 6:.1f}" y="{hy + chip_s / 2 - 3:.1f}" '
                                    f'width="{min(bar_w - 126, 52.0):.1f}" height="6" rx="3" fill="{SKELETON_SOFT}"/>')
            # Compact slicers still get their filter chips when there's room.
            if category == "slicer" and vx + vw - 6 - tx > 62:
                cx0 = vx + vw - 66
                card.append(f'<rect x="{cx0:.1f}" y="{hy + chip_s / 2 - 6:.1f}" width="26" height="12" rx="6" '
                            f'fill="{accent}" fill-opacity=".14"/>')
                card.append(f'<rect x="{cx0 + 30:.1f}" y="{hy + chip_s / 2 - 6:.1f}" width="26" height="12" rx="6" '
                            f'fill="{accent}" fill-opacity=".14"/>')
        elif glyph:
            # No room for a label: the icon chip only.
            card.append(chip(vx + 4, vy + 4, 15.0, category, f"wf-i-{glyph}-{glyph_suffix}", glyph_suffix))

        # ---- the skeleton chart body ----
        if big:
            bx0, bx1 = vx + 14, vx + vw - 14
            by0, by1 = sk_top + 8, vy + vh - 12
            if bx1 - bx0 >= 56 and by1 - by0 >= 22:
                kind = _skeleton_kind(v)
                draw = _SKELETONS.get(kind)
                if draw:
                    card.append(draw(bx0, by0, bx1, by1, category, glyph_suffix, seed))

        group = [f'<g class="wf-node cat-{category}">'] + card + ["</g>"]

        if category == "data":
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
        footer = f'<p class="wf-footer">+{pluralize_count("decorative shape", decorative_overflow)}</p>'

    return f'<div class="diagram">{"".join(svg)}{footer}{_LEGEND}</div>'
