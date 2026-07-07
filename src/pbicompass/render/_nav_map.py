from __future__ import annotations
import math
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..schemas.model import SemanticModel

from ._shared import anchor_slug, html_e

def render_navigation_map(model: SemanticModel) -> tuple[list[dict[str, str]], str]:
    """Render an SVG mapping page-to-page navigation via buttons, bookmarks, and drill-throughs."""
    pages = [p for p in model.pages if not p.is_hidden]
    if not pages:
        return [], ""

    page_names = {p.display_name for p in pages}
    page_by_id = {p.id: p for p in model.pages}
    page_by_display = {p.display_name: p for p in model.pages}
    
    # 1. Map Bookmark Name -> Target Page Name
    bookmark_targets = {}
    for b in model.bookmarks:
        target = b.target_page
        if target:
            # target could be page name or page id
            if target in page_by_id:
                bookmark_targets[b.name] = page_by_id[target].display_name
            elif target in page_by_display:
                bookmark_targets[b.name] = page_by_display[target].display_name
            else:
                bookmark_targets[b.name] = target

    edges: list[dict[str, str]] = []

    # 2. Extract Button Navigations & Bookmarks
    for p_from in pages:
        for v in p_from.visuals:
            act = v.action
            if not act or not isinstance(act, dict):
                continue
            atype = act.get("type")
            target = act.get("target")
            
            if atype == "pageNavigation" and target:
                # Find matching target page name
                target_page = None
                if target in page_by_id:
                    target_page = page_by_id[target].display_name
                elif target in page_by_display:
                    target_page = page_by_display[target].display_name
                else:
                    # best effort check if page display name starts with target or is similar
                    target_page = target
                
                if target_page in page_names and target_page != p_from.display_name:
                    edges.append({
                        "from": p_from.display_name,
                        "to": target_page,
                        "label": v.title or "Navigate",
                        "type": "button"
                    })
                    
            elif atype == "bookmark" and target:
                target_page = bookmark_targets.get(target)
                if target_page in page_names and target_page != p_from.display_name:
                    edges.append({
                        "from": p_from.display_name,
                        "to": target_page,
                        "label": f"Bookmark: {target}",
                        "type": "bookmark"
                    })

    # 3. Extract Drill-through Navigation
    for p_to in pages:
        if p_to.is_drillthrough and p_to.drillthrough_fields:
            for field in p_to.drillthrough_fields:
                field_leaf = field.split(".")[-1]
                # Look for pages that have visuals referencing this field
                for p_from in pages:
                    if p_from.display_name == p_to.display_name:
                        continue
                    has_ref = False
                    for v in p_from.visuals:
                        if any(f.split(".")[-1].lower() == field_leaf.lower() for f in v.fields):
                            has_ref = True
                            break
                    if has_ref:
                        edges.append({
                            "from": p_from.display_name,
                            "to": p_to.display_name,
                            "label": f"Drill-through: {field_leaf}",
                            "type": "drillthrough"
                        })

    # Dedup edges
    seen = set()
    deduped_edges = []
    for e in edges:
        key = (e["from"], e["to"], e["label"], e["type"])
        if key not in seen:
            seen.add(key)
            deduped_edges.append(e)

    # Fallback: if no custom navigation edges exist, generate sequential flow
    is_sequential = False
    if not deduped_edges and len(pages) > 1:
        is_sequential = True
        for i in range(len(pages) - 1):
            deduped_edges.append({
                "from": pages[i].display_name,
                "to": pages[i+1].display_name,
                "label": "Next Page",
                "type": "sequential"
            })

    # Geometry Layout
    W = 640
    H = 400
    centerX = W / 2
    centerY = H / 2
    radius = 140
    
    node_coords: dict[str, tuple[float, float]] = {}
    N = len(pages)
    
    # Position nodes in a circle
    for i, p in enumerate(pages):
        angle = i * (2 * math.pi / N) - (math.pi / 2)
        cx = centerX + radius * math.cos(angle)
        cy = centerY + radius * math.sin(angle)
        node_coords[p.display_name] = (cx, cy)

    svg = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="\'Poppins\', sans-serif" role="img" aria-labelledby="nav-diagram-title">'
    ]
    svg.append('<title id="nav-diagram-title">Report page navigation flow map</title>')

    # Render marker for arrow heads
    svg.append('  <defs>')
    svg.append('    <marker id="arrow" viewBox="0 0 10 10" refX="22" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">')
    svg.append('      <path d="M 0 2 L 10 5 L 0 8 z" fill="#64748b"/>')
    svg.append('    </marker>')
    svg.append('  </defs>')

    box_w = 120
    box_h = 34

    # Render edge paths
    for e in deduped_edges:
        p_from, p_to = e["from"], e["to"]
        pt1 = node_coords.get(p_from)
        pt2 = node_coords.get(p_to)
        
        if not pt1 or not pt2:
            continue
            
        x1, y1 = pt1
        x2, y2 = pt2
        
        # Draw arrow from center of node 1 to center of node 2 (marker offset will handle boundary)
        stroke_dash = ' stroke-dasharray="4,4"' if e["type"] in ("sequential", "drillthrough") else ""
        stroke_color = "#94a3b8" if e["type"] == "sequential" else "#3b82f6"
        
        # Center line
        svg.append(f'  <line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                   f'stroke="{stroke_color}" stroke-width="1.5" marker-end="url(#arrow)"{stroke_dash}/>')
                   
        # Label offset
        lx = (x1 + x2) / 2
        ly = (y1 + y2) / 2
        # Shift label slightly to avoid sitting directly on the line
        angle = math.atan2(y2 - y1, x2 - x1)
        lx_offset = lx + 12 * math.cos(angle + math.pi/2)
        ly_offset = ly + 12 * math.sin(angle + math.pi/2)
        
        lbl = e["label"]
        if len(lbl) > 18:
            lbl = lbl[:16] + "..."
            
        svg.append(f'  <text x="{lx_offset:.1f}" y="{ly_offset + 3:.1f}" font-size="8" fill="#475569" '
                   f'text-anchor="middle">{html_e(lbl)}</text>')

    # Render node boxes
    for p in pages:
        x, y = node_coords[p.display_name]
        
        # Center the box around the coordinate
        bx = x - box_w / 2
        by = y - box_h / 2
        
        fill = "#eff6ff" if p.is_drillthrough else "#ecfdf5" if not p.is_hidden else "#f1f5f9"
        stroke = "#3b82f6" if p.is_drillthrough else "#10b981" if not p.is_hidden else "#cbd5e1"
        text_color = "#1e3a8a" if p.is_drillthrough else "#047857" if not p.is_hidden else "#475569"
        
        page_slug = anchor_slug(p.display_name)
        
        svg.append(f'  <a href="#page-{page_slug}">')
        svg.append(f'    <rect x="{bx:.1f}" y="{by:.1f}" width="{box_w}" height="{box_h}" rx="6" '
                   f'fill="{fill}" stroke="{stroke}" stroke-width="1.2" style="cursor:pointer; transition:opacity 0.2s;" '
                   f'onmouseover="this.style.opacity=0.8" onmouseout="this.style.opacity=1.0"/>')
                   
        display_name = p.display_name
        if len(display_name) > 18:
            display_name = display_name[:16] + "..."
            
        svg.append(f'    <text x="{x:.1f}" y="{y + 3:.1f}" font-size="8.5" font-weight="600" fill="{text_color}" '
                   f'text-anchor="middle">{html_e(display_name)}</text>')
        svg.append('  </a>')

    svg.append('</svg>')
    return deduped_edges, f'<div class="diagram">{"".join(svg)}</div>'
