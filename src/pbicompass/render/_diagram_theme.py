"""Shared "v6 Studio" design language for the hand-rolled SVG diagrams
(page wireframe + lineage graph, 2026-07-11).

One visual DNA for every diagram so they read as a family:

* **Canvas** — soft white→cool-gray vertical gradient over a faint dot grid,
  rounded 14, hairline border (replaces v5's flat grid "sheet").
* **Cards** — solid white, hairline stroke, layered CSS drop-shadow (the
  ``.wf-node`` class in the HTML shell), hover-lift.
* **Icon chips** — small rounded squares filled with a vertical
  accent→deep-accent gradient and a white stroke icon, replacing v5's solid
  pills / tinted badges. The gradient is the "category color" carrier.
* **Typography** — real-case Poppins 600 titles in ink, 500 sublabels in
  muted gray, small-caps letter-spaced captions.

Everything is still pure inline SVG + inline CSS: zero external
dependencies, so the air-gap / single-file guarantees hold — the upgrade is
purely how much design the same technique carries.
"""

from __future__ import annotations

# Ink / neutrals shared by both diagrams (fixed light-theme hex — diagram
# canvases are intentionally always-light, same rule as v4/v5).
INK = "#1f2433"
MUTED = "#8a93a8"
CAPTION = "#9aa3b8"
FAINT = "#b6bdcf"
EDGE = "#e2e7f2"
HAIRLINE = "#e7eaf3"
GHOST_FILL = "#fbfcfe"
GHOST_EDGE = "#dfe4ef"
SKELETON = "#e4e8f2"          # neutral skeleton bars (decorative text lines)
SKELETON_SOFT = "#edf0f7"

# The four accent hues (unchanged from v4/v5) plus the deep stop each chip
# gradient falls to. Wireframe categories and lineage layers share them:
# data/table = indigo, slicer/measure = amber, nav/page = emerald,
# decorative/source = violet.
ACCENT = {
    "data": "#4f6ef7", "table": "#4f6ef7",
    "slicer": "#f59e0b", "measure": "#f59e0b",
    "nav": "#10b981", "page": "#10b981",
    "decorative": "#8b5cf6", "source": "#8b5cf6",
}
_GRAD_STOPS = {
    "#4f6ef7": ("#6b85f9", "#3d5bf0"),
    "#f59e0b": ("#f7b23b", "#e08c06"),
    "#10b981": ("#2ec695", "#0a9e6e"),
    "#8b5cf6": ("#a37ef8", "#7a48ec"),
}


def accent_of(category: str) -> str:
    return ACCENT.get(category, "#4f6ef7")


def canvas_defs(suffix: str) -> str:
    """The defs every v6 diagram canvas needs: dot-grid pattern, canvas
    gradient, and one chip gradient per accent hue — namespaced by
    ``suffix`` because each diagram is an independent inline SVG and a
    document embeds several (one wireframe per page + the lineage graph)."""
    out = [
        f'<pattern id="dg-dots-{suffix}" width="22" height="22" patternUnits="userSpaceOnUse">'
        f'<circle cx="1.2" cy="1.2" r="1" fill="#dfe5f0"/></pattern>',
        f'<linearGradient id="dg-canvas-{suffix}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="#fdfefe"/><stop offset="1" stop-color="#f5f7fc"/></linearGradient>',
    ]
    for cat in ("data", "slicer", "nav", "decorative"):
        hue = ACCENT[cat]
        hi, lo = _GRAD_STOPS[hue]
        out.append(
            f'<linearGradient id="dg-chip-{cat}-{suffix}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{hi}"/><stop offset="1" stop-color="{lo}"/></linearGradient>'
        )
    # Vertical skeleton fills (ghost chart content), one per accent.
    for cat in ("data", "slicer", "nav", "decorative"):
        hue = ACCENT[cat]
        out.append(
            f'<linearGradient id="dg-sk-{cat}-{suffix}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{hue}" stop-opacity=".38"/>'
            f'<stop offset="1" stop-color="{hue}" stop-opacity=".10"/></linearGradient>'
        )
        out.append(
            f'<linearGradient id="dg-area-{cat}-{suffix}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{hue}" stop-opacity=".28"/>'
            f'<stop offset="1" stop-color="{hue}" stop-opacity=".02"/></linearGradient>'
        )
    return "".join(out)


def canvas(x: float, y: float, w: float, h: float, suffix: str) -> str:
    """The rounded gradient + dot-grid canvas rectangle pair."""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="14" '
        f'fill="url(#dg-canvas-{suffix})" stroke="{EDGE}" stroke-width="1"/>'
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="14" '
        f'fill="url(#dg-dots-{suffix})" opacity=".55"/>'
    )


def chip(x: float, y: float, size: float, cat: str, glyph_href: str, suffix: str) -> str:
    """A gradient icon chip: rounded accent-gradient square + white stroke
    icon (``glyph_href`` is a ``<symbol>`` id, already namespaced)."""
    rx = max(4, size * 0.29)
    pad = size * 0.2
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{size:.0f}" height="{size:.0f}" rx="{rx:.1f}" '
        f'fill="url(#dg-chip-{cat}-{suffix})"/>'
        f'<use href="#{glyph_href}" x="{x + pad:.1f}" y="{y + pad:.1f}" '
        f'width="{size - 2 * pad:.1f}" height="{size - 2 * pad:.1f}" fill="none" stroke="#ffffff"/>'
    )


def legend(pairs: list[tuple[str, str]]) -> str:
    """The rounded-pill legend chips shared by wireframe + lineage:
    ``pairs`` is ``[(css_modifier, label), ...]``."""
    chips = "".join(
        f'<span class="wf-chip"><i class="wf-chip-dot wf-chip-dot--{mod}"></i>{label}</span>'
        for mod, label in pairs
    )
    return f'<div class="legend legend--upper wf-legend">{chips}</div>'
