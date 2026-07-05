from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..schemas.model import Page

from ._shared import anchor_slug, html_e

FRIENDLY_VISUAL = {
    "card": "Card", "multiRowCard": "Multi-row card", "kpi": "KPI",
    "clusteredColumnChart": "Column chart", "columnChart": "Column chart",
    "clusteredBarChart": "Bar chart", "barChart": "Bar chart", "lineChart": "Line chart",
    "areaChart": "Area chart", "pieChart": "Pie chart", "donutChart": "Donut chart",
    "tableEx": "Table", "pivotTable": "Matrix", "matrix": "Matrix", "treemap": "Treemap",
    "map": "Map", "filledMap": "Map", "shapeMap": "Map", "gauge": "Gauge",
    "scatterChart": "Scatter chart", "funnel": "Funnel", "waterfallChart": "Waterfall chart",
    "ribbonChart": "Ribbon chart", "decompositionTreeVisual": "Decomposition tree",
    "keyInfluencersVisual": "Key influencers", "image": "Image", "shape": "Shape",
    "textbox": "Text box", "actionButton": "Button", "visualGroup": "Group",
}

def render_wireframe(page: Page) -> str:
    """Render a scaled SVG layout of the page's visuals if coordinates exist."""
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

    target_w = 480
    scale = target_w / page_w
    target_h = page_h * scale

    page_title_slug = anchor_slug(page.display_name)
    title_id = f"wireframe-title-{anchor_slug(page.id)}"

    svg = [
        f'<svg viewBox="0 0 {target_w:.0f} {target_h:.0f}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-labelledby="{title_id}">'
    ]
    svg.append(f'<title id="{title_id}">Wireframe layout for page {html_e(page.display_name)}</title>')

    # Sort visuals by Z-index (z) ascending
    sorted_visuals = sorted(valid_visuals, key=lambda v: v.z or 0)

    for v in sorted_visuals:
        vx = v.x * scale
        vy = v.y * scale
        vw = v.width * scale
        vh = v.height * scale

        if vw <= 0 or vh <= 0:
            continue

        is_slicer = v.is_slicer or v.type in {"slicer", "advancedSlicerVisual"}
        is_nav = v.type in {"actionButton", "button", "navBar"}

        if is_slicer:
            fill = "#fef3c7"      # amber-100
            stroke = "#f59e0b"    # amber-500
            text_color = "#b45309"# amber-700
        elif is_nav:
            fill = "#ecfdf5"      # emerald-50
            stroke = "#10b981"    # emerald-500
            text_color = "#047857"# emerald-700
        else:
            fill = "#eff6ff"      # blue-50
            stroke = "#3b82f6"    # blue-500
            text_color = "#1d4ed8"# blue-700

        friendly = FRIENDLY_VISUAL.get(v.type, v.type or "Visual")
        label = v.title or friendly
        visual_slug = anchor_slug(label)
        link_target = f"#visual-{page_title_slug}-{visual_slug}"

        svg.append(f'<a href="{link_target}">')
        svg.append(
            f'  <rect x="{vx:.1f}" y="{vy:.1f}" width="{vw:.1f}" height="{vh:.1f}" rx="3" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2" style="cursor:pointer; transition:opacity 0.2s;" '
            f'onmouseover="this.style.opacity=0.8" onmouseout="this.style.opacity=1.0"/>'
        )

        display_text = v.title or friendly
        if len(display_text) > 15:
            display_text = display_text[:12] + "..."

        if vh > 20 and vw > 35:
            svg.append(
                f'  <text x="{vx + vw/2:.1f}" y="{vy + vh/2 + 3:.1f}" font-size="8" font-family="sans-serif" '
                f'font-weight="500" fill="{text_color}" text-anchor="middle">{html_e(display_text)}</text>'
            )
        svg.append('</a>')

    svg.append('</svg>')
    return f'<div class="diagram">{"".join(svg)}</div>'
