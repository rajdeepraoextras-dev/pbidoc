from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..schemas.model import SemanticModel

import re
from ._shared import anchor_slug, html_e
from ..agents.usage import measure_dependencies

def build_measure_dependency_tree(
    measure_name: str,
    measure_deps: dict[str, list[str]],
    seen: set[str],
    indent: str = ""
) -> list[str]:
    """Build an indented Unicode tree text representation of measure dependencies recursively."""
    lines = []
    deps = measure_deps.get(measure_name, [])
    for i, dep in enumerate(deps):
        is_last = (i == len(deps) - 1)
        connector = "└── " if is_last else "├── "
        lines.append(f"{indent}{connector}{dep}")
        if dep not in seen:
            seen.add(dep)
            next_indent = indent + ("    " if is_last else "│   ")
            lines.extend(build_measure_dependency_tree(dep, measure_deps, seen, next_indent))
            seen.remove(dep)
    return lines

def get_measure_chain_depth(
    measure_name: str,
    measure_deps: dict[str, list[str]],
    memo: dict[str, int],
    path: set[str]
) -> int:
    """Find maximum depth of dependency chain recursively."""
    if measure_name in path:
        return 0
    if measure_name in memo:
        return memo[measure_name]
    deps = measure_deps.get(measure_name, [])
    if not deps:
        return 1
    path.add(measure_name)
    max_dep_depth = max(get_measure_chain_depth(dep, measure_deps, memo, path) for dep in deps) if deps else 0
    path.remove(measure_name)
    depth = 1 + max_dep_depth
    memo[measure_name] = depth
    return depth

def render_measure_dependency_graph_svg(model: SemanticModel) -> str:
    """Render an SVG graph showing the connections between measures that depend on other measures."""
    measures = model.all_measures()
    measure_names = {m.name for m in measures}
    
    measure_deps: dict[str, list[str]] = {}
    for m in measures:
        # extract only measure-to-measure dependencies
        from ..agents.deterministic import _measure_refs
        deps = _measure_refs(m.expression or "")
        m_deps = [d for d in deps if d in measure_names and d != m.name]
        measure_deps[m.name] = m_deps

    edges: list[dict[str, str]] = []
    for m_name, deps in measure_deps.items():
        for dep in deps:
            edges.append({"from": dep, "to": m_name})

    if not edges:
        return ""

    # Nodes are all measures involved in edges
    nodes_all = sorted(list({e["from"] for e in edges} | {e["to"] for e in edges}))
    
    # Calculate depth / level for each node
    memo: dict[str, int] = {}
    node_levels: dict[str, int] = {}
    for n in nodes_all:
        depth = get_measure_chain_depth(n, measure_deps, memo, set())
        # level is depth - 1
        node_levels[n] = max(0, depth - 1)

    # Group nodes by level
    levels: dict[int, list[str]] = {}
    for n in nodes_all:
        lvl = node_levels[n]
        levels.setdefault(lvl, []).append(n)

    sorted_levels = sorted(levels.keys())
    max_lvl = max(sorted_levels) if sorted_levels else 0
    
    # Layout geometry
    H = max(300, max(len(nodes) for nodes in levels.values()) * 50 + 40)
    W = 960
    
    # Distribute columns
    col_count = max_lvl + 1
    if col_count == 1:
        col_x = [390]
    elif col_count == 2:
        col_x = [200, 580]
    elif col_count == 3:
        col_x = [100, 390, 680]
    else:
        col_x = [60 + i * 260 for i in range(col_count)]
        
    box_w = 180
    box_h = 30
    node_coords: dict[str, tuple[float, float]] = {}
    
    svg = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="inherit" role="img" aria-labelledby="dep-diagram-title">'
    ]
    svg.append('<title id="dep-diagram-title">Measure dependency graph</title>')
    
    for lvl in sorted_levels:
        lvl_nodes = levels[lvl]
        n_count = len(lvl_nodes)
        y_start = (H - (n_count * box_h + (n_count - 1) * 15)) / 2
        
        # Place columns
        cx = col_x[min(lvl, len(col_x) - 1)]
        
        for i, name in enumerate(lvl_nodes):
            cy = y_start + i * 45
            node_coords[name] = (cx, cy)
            
            svg.append(f'  <g class="measure-dep-node">')
            svg.append(
                f'    <rect x="{cx:.1f}" y="{cy:.1f}" width="{box_w}" height="{box_h}" rx="6" '
                f'fill="#f5f3ff" stroke="#a78bfa" stroke-width="1.2"/>'
            )
            
            display_name = name
            if len(display_name) > 22:
                display_name = display_name[:20] + "..."
                
            svg.append(
                f'    <text x="{cx + box_w/2:.1f}" y="{cy + box_h/2 + 3:.1f}" font-size="9" '
                f'text-anchor="middle" fill="#5b21b6">{html_e(display_name)}</text>'
            )
            svg.append(f'  </g>')
            
    # Render edges
    for e in edges:
        n_from, n_to = e["from"], e["to"]
        p1 = node_coords.get(n_from)
        p2 = node_coords.get(n_to)
        
        if not p1 or not p2:
            continue
            
        x1 = p1[0] + box_w
        y1 = p1[1] + box_h / 2
        x2 = p2[0]
        y2 = p2[1] + box_h / 2
        
        dx = (x2 - x1) / 2
        svg.append(
            f'  <path d="M {x1:.1f} {y1:.1f} C {x1 + dx:.1f} {y1:.1f}, {x1 + dx:.1f} {y2:.1f}, {x2:.1f} {y2:.1f}" '
            f'fill="none" stroke="#a78bfa" stroke-width="1.2" stroke-linecap="round"/>'
        )
        
    svg.append('</svg>')
    return f'<div class="diagram">{"".join(svg)}</div>'
