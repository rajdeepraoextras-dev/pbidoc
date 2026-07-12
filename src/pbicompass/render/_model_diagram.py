"""The §6 model (ER) diagram — Day 6 of the visual-layer plan.

**Layout.** For the common case (<=12 tables) a star layout: fact table(s)
centered (a small horizontal cluster when there's more than one — a galaxy
schema), dimension tables ringed around them at equal angular spacing —
the picture a BI developer expects "model diagram" to mean. Past 12 tables
a single ring gets unreadable, so the layout switches to a layered
top-down graph via `grandalf <https://github.com/bdcht/grandalf>`_ (pure
Python, no C extension — fits this project's existing optional-extras
pattern, e.g. ``pbix``/``auth``). When grandalf isn't installed the star
layout is used regardless of table count — denser, never prettier, but
never broken (the same graceful-degradation contract every optional extra
in this codebase honors).

**Design system.** Same v6 "Studio" DNA as the wireframe/lineage diagrams
(``_diagram_theme``): white cards, gradient icon chips, hairline
gradient+dot-grid canvas. Relationship lines carry the two facts a
developer actually needs at a glance: a cardinality glyph pair (``1``/``*``
near each end) and an active/inactive line style (solid vs. dashed) — never
color alone, since color already carries the fact/dimension distinction.
Every table card is a deep link into its own §6 data-dictionary row
(``#table-{slug}``), and diagram <-> dictionary now actually agree (§18's
old "the model diagram is in section 6" claim was, until this, false).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from ._diagram_theme import (
    CAPTION, EDGE, HAIRLINE, INK, MUTED,
    canvas, canvas_defs, chip, legend,
)
from ._shared import anchor_slug, html_e

_FACT_CHIP_CAT = "data"
_DIM_CHIP_CAT = "nav"

_FACT_ICON = ('<rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke-width="1.8"/>'
             '<path d="M3 9h18M3 15h18M9 3v18" fill="none" stroke-width="1.8"/>')
_DIM_ICON = ('<rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke-width="1.8"/>'
            '<path d="M3 10h18" fill="none" stroke-width="1.8"/>')

_BOX_W = 176
_BOX_H = 52

_LEGEND = legend([("data", "Fact table"), ("nav", "Dimension table")])


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def _pluralize(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _table_sublabel(t: dict[str, Any]) -> str:
    parts = []
    if t.get("columns"):
        parts.append(_pluralize(t["columns"], "column"))
    if t.get("measures"):
        parts.append(_pluralize(t["measures"], "measure"))
    return " · ".join(parts)


# -- Layout -------------------------------------------------------------------

def _star_layout(fact_names: list[str], dim_names: list[str]) -> tuple[dict[str, tuple[float, float]], float, float]:
    """Facts centered (small horizontal cluster if more than one),
    dimensions ringed at equal angular spacing — the classic star-schema
    picture. Ring radius grows with dimension count so cards never overlap
    (same growth formula the pre-Day-6 stub used, kept because it already
    behaves well from 1 to ~20 dimensions)."""
    n = len(dim_names)
    R = max(190, int(n * (_BOX_W + 30) / (2 * math.pi))) if n else 0
    margin = _BOX_W
    cx = R + margin
    cy = R + margin
    pos: dict[str, tuple[float, float]] = {}

    if len(fact_names) == 1:
        pos[fact_names[0]] = (cx - _BOX_W / 2, cy - _BOX_H / 2)
    elif len(fact_names) > 1:
        gap = _BOX_W + 20
        start = cx - (len(fact_names) - 1) * gap / 2
        for i, name in enumerate(fact_names):
            pos[name] = (start + i * gap - _BOX_W / 2, cy - _BOX_H / 2)

    for i, name in enumerate(dim_names):
        ang = -math.pi / 2 + 2 * math.pi * i / max(1, n)
        pos[name] = (cx + R * math.cos(ang) - _BOX_W / 2, cy + R * math.sin(ang) - _BOX_H / 2)

    if not pos:
        return pos, 2 * margin, 2 * margin
    xs = [x for x, _ in pos.values()] + [x + _BOX_W for x, _ in pos.values()]
    ys = [y for _, y in pos.values()] + [y + _BOX_H for _, y in pos.values()]
    pad = 24
    W = max(xs) - min(xs) + 2 * pad
    H = max(ys) - min(ys) + 2 * pad
    off_x, off_y = pad - min(xs), pad - min(ys)
    pos = {k: (x + off_x, y + off_y) for k, (x, y) in pos.items()}
    return pos, W, H


def _grandalf_layout(
    table_names: list[str], edges: list[dict[str, Any]],
) -> Optional[tuple[dict[str, tuple[float, float]], float, float]]:
    """Layered top-down layout for larger models (>12 tables) — a single
    ring gets unreadable past that point. Returns ``None`` (never raises)
    when grandalf isn't installed, or the model has too few edges to lay
    out meaningfully; either way the caller falls back to the star layout,
    same contract as every other optional extra in this codebase."""
    try:
        from grandalf.graphs import Edge as GEdge
        from grandalf.graphs import Graph, Vertex
        from grandalf.layouts import SugiyamaLayout
    except ImportError:
        return None

    class _View:
        def __init__(self, w: float, h: float) -> None:
            self.w = w
            self.h = h

    vertices = {name: Vertex(name) for name in table_names}
    for v in vertices.values():
        v.view = _View(_BOX_W, _BOX_H)

    seen_pairs: set[tuple[str, str]] = set()
    gedges = []
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f in vertices and t in vertices and f != t and (f, t) not in seen_pairs:
            seen_pairs.add((f, t))
            gedges.append(GEdge(vertices[f], vertices[t]))

    graph = Graph(list(vertices.values()), gedges)
    pos: dict[str, tuple[float, float]] = {}
    try:
        x_off = 0.0
        for component in graph.C:
            if len(component.sV) <= 1:
                # A singleton (disconnected table) has nothing for Sugiyama
                # to layer — place it directly rather than invoking a layout
                # pass with no edges.
                only = list(component.sV)[0]
                pos[only.data] = (x_off, 0.0)
                x_off += _BOX_W + 60
                continue
            sug = SugiyamaLayout(component)
            sug.init_all()
            sug.draw()
            xs = [v.view.xy[0] for v in component.sV]
            comp_w = (max(xs) - min(xs)) if xs else 0
            shift = x_off - min(xs) if xs else x_off
            for v in component.sV:
                x, y = v.view.xy
                pos[v.data] = (x + shift, y)
            x_off += comp_w + _BOX_W + 60
    except Exception:  # pragma: no cover - defensive: never let a layout bug break rendering
        return None

    if not pos:
        return None
    xs = [x for x, _ in pos.values()] + [x + _BOX_W for x, _ in pos.values()]
    ys = [y for _, y in pos.values()] + [y + _BOX_H for _, y in pos.values()]
    pad = 30
    off_x, off_y = pad - min(xs), pad - min(ys)
    pos = {k: (x + off_x - _BOX_W / 2, y + off_y - _BOX_H / 2) for k, (x, y) in pos.items()}
    W = max(xs) - min(xs) + 2 * pad
    H = max(ys) - min(ys) + 2 * pad
    return pos, W, H


# -- Rendering ------------------------------------------------------------------

def _rect_exit(cx: float, cy: float, dx: float, dy: float) -> tuple[float, float]:
    """Point where a ray from a card's center (cx, cy) heading (dx, dy)
    crosses the card's border — so lines and glyphs start at the edge of
    the card instead of underneath it."""
    tx = (_BOX_W / 2) / abs(dx) if dx else math.inf
    ty = (_BOX_H / 2) / abs(dy) if dy else math.inf
    t = min(tx, ty)
    return cx + dx * t, cy + dy * t


def _edge_endpoints(p1: tuple[float, float], p2: tuple[float, float]) -> tuple[float, float, float, float]:
    c1x, c1y = p1[0] + _BOX_W / 2, p1[1] + _BOX_H / 2
    c2x, c2y = p2[0] + _BOX_W / 2, p2[1] + _BOX_H / 2
    dx, dy = c2x - c1x, c2y - c1y
    if not dx and not dy:
        return c1x, c1y, c2x, c2y
    x1, y1 = _rect_exit(c1x, c1y, dx, dy)
    x2, y2 = _rect_exit(c2x, c2y, -dx, -dy)
    return x1, y1, x2, y2


def _cardinality_glyph(x: float, y: float, symbol: str) -> str:
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="#ffffff" stroke="{EDGE}" stroke-width="1"/>'
            f'<text x="{x:.1f}" y="{y + 3.5:.1f}" font-size="10" font-weight="700" text-anchor="middle" '
            f'fill="{MUTED}">{html_e(symbol)}</text>')


def render_model_diagram_svg(tables: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    """Render the §6 model diagram. Returns ``""`` when there are no tables
    (nothing to draw — the caller already guards on this, matching every
    other diagram's empty-input contract)."""
    if not tables:
        return ""

    fact_names = [t["name"] for t in tables if t.get("kind") == "fact"]
    dim_names = [t["name"] for t in tables if t.get("kind") != "fact"]
    # A model with no detected fact table (e.g. every table came back
    # "unknown") still needs *something* at the center, or the ring has
    # nothing to ring around — the largest table by column+measure count is
    # the closest deterministic proxy for "the table everything relates to".
    if not fact_names and dim_names:
        fact_names = [max(dim_names, key=lambda n: next(
            (t.get("columns", 0) + t.get("measures", 0) for t in tables if t["name"] == n), 0))]
        dim_names = [n for n in dim_names if n != fact_names[0]]

    table_names = [t["name"] for t in tables]
    layout = None
    if len(tables) > 12:
        layout = _grandalf_layout(table_names, edges)
    if layout is None:
        layout = _star_layout(fact_names, dim_names)
    pos, W, H = layout

    table_by_name = {t["name"]: t for t in tables}
    suffix = "model"
    title_chars = int((_BOX_W - 52 - 10) / 6.2)
    sub_chars = int((_BOX_W - 52 - 8) / 5.0)

    svg: list[str] = [
        f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-labelledby="model-diagram-title">'
        f'<style>text {{ font-family: "Poppins", sans-serif !important; }}</style>'
    ]
    table_list = ", ".join(table_names)
    svg.append(f'<title id="model-diagram-title">Data model diagram: {html_e(table_list)}, connected by '
               f'{_pluralize(len(edges), "relationship")}</title>')
    svg.append(f'<defs>{canvas_defs(suffix)}'
               f'<symbol id="dm-i-fact-{suffix}" viewBox="0 0 24 24">{_FACT_ICON}</symbol>'
               f'<symbol id="dm-i-dim-{suffix}" viewBox="0 0 24 24">{_DIM_ICON}</symbol></defs>')
    svg.append(canvas(0.5, 0.5, W - 1, H - 1, suffix))

    # Pass 1: relationship lines — solid for active, dashed for inactive —
    # painted before cards, with a cardinality glyph near each end.
    for e in edges:
        p1, p2 = pos.get(e.get("from")), pos.get(e.get("to"))
        if not p1 or not p2:
            continue
        x1, y1, x2, y2 = _edge_endpoints(p1, p2)
        active = e.get("is_active", True)
        stroke = HAIRLINE if not active else "#c3cadd"
        dash = ' stroke-dasharray="5 4"' if not active else ""
        svg.append(f'<g class="dm-edge" data-from="{html_e(e.get("from"))}" data-to="{html_e(e.get("to"))}">')
        if e.get("from_column") and e.get("to_column"):
            join = f'{e["from"]}[{e["from_column"]}] → {e["to"]}[{e["to_column"]}]'
            svg.append(f'<title>{html_e(join)}</title>')
        svg.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                   f'stroke="{stroke}" stroke-width="1.4"{dash}/>')
        # Glyphs sit a fixed distance outside each card edge (the line is
        # already clipped to card borders), so they never land on a card no
        # matter how short the center-to-center distance is. On very short
        # visible segments fall back to fractions of what's visible.
        seg = math.hypot(x2 - x1, y2 - y1)
        off = 16.0
        if seg > 3 * off:
            ux, uy = (x2 - x1) / seg, (y2 - y1) / seg
            ax, ay = x1 + ux * off, y1 + uy * off
            bx, by = x2 - ux * off, y2 - uy * off
        else:
            ax, ay = x1 + (x2 - x1) * 0.25, y1 + (y2 - y1) * 0.25
            bx, by = x1 + (x2 - x1) * 0.75, y1 + (y2 - y1) * 0.75
        from_sym = "1" if e.get("from_card") == "one" else "*"
        to_sym = "1" if e.get("to_card") == "one" else "*"
        svg.append(_cardinality_glyph(ax, ay, from_sym))
        svg.append(_cardinality_glyph(bx, by, to_sym))
        svg.append('</g>')

    # Pass 2: table cards.
    for name in table_names:
        p = pos.get(name)
        if not p:
            continue
        x, y = p
        t = table_by_name.get(name, {})
        is_fact = name in fact_names
        chip_cat = _FACT_CHIP_CAT if is_fact else _DIM_CHIP_CAT
        glyph = f"dm-i-fact-{suffix}" if is_fact else f"dm-i-dim-{suffix}"
        sub = _table_sublabel(t)
        title_disp = _truncate(name, title_chars)
        sub_disp = _truncate(sub, sub_chars) if sub else ("Fact table" if is_fact else "Dimension table")

        node = [
            f'<a href="#table-{html_e(anchor_slug(name))}">',
            f'<g class="dm-node" data-table="{html_e(name)}">',
            f'<title>{html_e(name)} — {html_e("Fact table" if is_fact else "Dimension table")}'
            f'{f" ({html_e(sub)})" if sub else ""} (click to jump to its row)</title>',
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{_BOX_W}" height="{_BOX_H}" rx="11" '
            f'class="wf-card-bg" fill="#ffffff" stroke="{EDGE}" stroke-width="1"/>',
            f'<rect x="{x + 8:.1f}" y="{y + 9:.1f}" width="3.5" height="{_BOX_H - 18:.1f}" rx="1.75" '
            f'fill="url(#dg-chip-{chip_cat}-{suffix})"/>',
            chip(x + 19, y + (_BOX_H - 24) / 2, 24, chip_cat, glyph, suffix),
            f'<text x="{x + 52:.1f}" y="{y + 22:.1f}" font-size="11" font-weight="600" '
            f'fill="{INK}">{html_e(title_disp)}</text>',
            f'<text x="{x + 52:.1f}" y="{y + 35:.1f}" font-size="8.5" font-weight="500" '
            f'fill="{CAPTION}">{html_e(sub_disp)}</text>',
            '</g></a>',
        ]
        svg.append("".join(node))

    svg.append('</svg>')
    return f'<div class="diagram">{"".join(svg)}{_LEGEND}</div>'
