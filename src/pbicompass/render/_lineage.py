from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..schemas.model import SemanticModel

from ._shared import anchor_slug, html_e
from ..agents.usage import measure_usage
from ..agents.report_facts import find_referenced_tables, data_source_summaries

# v4 design (2026-07-08) — "similar design" to the page wireframe (same
# session, user-supplied wireframe-v4-light.html, Option A): a white card
# per node with a colored *left* accent bar (top bar is the wireframe's own
# convention; left distinguishes a lineage node from a wireframe visual at
# a glance) instead of a plain filled rect, a tinted icon badge per layer,
# and the same hover-lift .wf-node treatment. Lineage has no data/slicer/
# nav/decorative categories — it has four *layers* — so the same four v4
# accent colors are reassigned: source=purple, table=blue, measure=amber,
# page=green.
#
# v5 layout (2026-07-10) — the v4 card language kept, but the *layout* was
# rebuilt. The old version hardcoded W=960 while the last column sat at
# x=840+190=1030, so every Page node was clipped; and nodes were stacked in
# plain alphabetical order, which crosses edges into spaghetti. Now the
# canvas width is derived from the columns (can't clip), and nodes within
# each column are ordered by the iterated median heuristic — the crossing-
# minimization pass at the heart of dagre/Graphviz — implemented natively so
# the renderer stays dependency-free. Column headers + real-case titles
# replace the old repeat-the-layer-name sub-label and the global
# text-transform:uppercase that was shouting every table/measure name.
_INK = "#1f2433"
_MUTED = "#8a93a8"
_EDGE = "#e7eaf3"
_SURFACE = "#ffffff"
_LAYER_STYLE = [
    ("source", "#8b5cf6", "#f3eefe", "Source"),
    ("table", "#4f6ef7", "#eef1fe", "Table"),
    ("measure", "#f59e0b", "#fef4e4", "Measure"),
    ("page", "#10b981", "#e7f8f1", "Page"),
]
_LAYER_ICON = {
    "source": '<ellipse cx="12" cy="5" rx="8" ry="3" fill="none" stroke-width="1.8"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" fill="none" stroke-width="1.8"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke-width="1.8"/><path d="M3 10h18M9 10v10" fill="none" stroke-width="1.8"/>',
    "measure": '<path d="M4 19V5M4 19h16M8 15l3-4 3 3 4-6" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    "page": '<rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke-width="1.8"/><path d="M3 9h18" fill="none" stroke-width="1.8"/>',
}
_LAYER_PLURAL = ["SOURCES", "TABLES", "MEASURES", "PAGES"]

_LEGEND = (
    '<div class="legend legend--upper wf-legend">'
    '<span class="wf-chip"><i class="wf-chip-dot wf-chip-dot--source"></i>Source</span>'
    '<span class="wf-chip"><i class="wf-chip-dot wf-chip-dot--table"></i>Table</span>'
    '<span class="wf-chip"><i class="wf-chip-dot wf-chip-dot--measure"></i>Measure</span>'
    '<span class="wf-chip"><i class="wf-chip-dot wf-chip-dot--page"></i>Page</span>'
    "</div>"
)


def _minimize_crossings(
    layers: list[list[str]], edges: list[dict[str, str]], sweeps: int = 6
) -> list[list[str]]:
    """Reorder nodes *within* each layer to reduce edge crossings between
    adjacent layers — the iterated median heuristic (Sugiyama's ordering
    pass, the core of what dagre/Graphviz do). This is the readability win a
    fixed alphabetical stack can't give, implemented natively so the renderer
    needs no JS/native graph-layout dependency. Layers are capped at 8 nodes,
    so a handful of down/up sweeps converge instantly.

    A node with no neighbor in the reference layer keeps its current index
    (the ``fallback``), so disconnected nodes don't drift; the original index
    is also the sort tiebreaker, keeping the pass stable.
    """
    order = [list(lay) for lay in layers]
    layer_of = {n: i for i, lay in enumerate(order) for n in lay}
    succ: dict[str, list[str]] = {}
    pred: dict[str, list[str]] = {}
    for e in edges:
        f, t = e["from"], e["to"]
        if f in layer_of and t in layer_of and layer_of[t] == layer_of[f] + 1:
            succ.setdefault(f, []).append(t)
            pred.setdefault(t, []).append(f)

    def _median(neighbors: list[str], pos: dict[str, int], fallback: float) -> float:
        idx = sorted(pos[x] for x in neighbors if x in pos)
        if not idx:
            return fallback
        m = len(idx) // 2
        if len(idx) % 2:
            return float(idx[m])
        if len(idx) == 2:
            return (idx[0] + idx[1]) / 2
        left, right = idx[m - 1] - idx[0], idx[-1] - idx[m]
        if left + right == 0:
            return (idx[m - 1] + idx[m]) / 2
        return (idx[m - 1] * right + idx[m] * left) / (left + right)

    def _reorder(li: int, adj: dict[str, list[str]], ref: int) -> None:
        pos = {n: i for i, n in enumerate(order[ref])}
        keyed = [(_median(adj.get(n, []), pos, i), i, n) for i, n in enumerate(order[li])]
        keyed.sort(key=lambda k: (k[0], k[1]))
        order[li] = [n for _, _, n in keyed]

    for s in range(sweeps):
        if s % 2 == 0:  # down sweep: order each layer by its predecessors
            for li in range(1, len(order)):
                _reorder(li, pred, li - 1)
        else:  # up sweep: order each layer by its successors
            for li in range(len(order) - 2, -1, -1):
                _reorder(li, succ, li + 1)
    return order


def get_source_label(ds) -> str:
    target = ds.server or ds.detail or ""
    if ds.database:
        target = f"{target}/{ds.database}" if target else ds.database
    return f"{ds.type or 'Source'}: {target}".rstrip(": ")

def build_lineage_data(model: SemanticModel) -> tuple[list[dict[str, str]], str]:
    """Build all lineage edges and render the layered left-to-right SVG lineage graph."""
    raw_edges: list[dict[str, str]] = []

    # 1. Source to Table
    sources_map = {get_source_label(ds): ds for ds in model.data_sources}
    for t in model.tables:
        for p in t.partitions:
            if p.expression:
                for src_label, ds in sources_map.items():
                    if (
                        (ds.server and ds.server in p.expression) or
                        (ds.detail and ds.detail in p.expression) or
                        (ds.database and ds.database in p.expression) or
                        (ds.type and ds.type in p.expression)
                    ):
                        raw_edges.append({"from": src_label, "to": t.name, "type": "Source to Table"})

    # 2. Table to Measure
    measures = model.all_measures()
    for m in measures:
        if m.table:
            raw_edges.append({"from": m.table, "to": m.name, "type": "Table to Measure"})
        ref_tables = find_referenced_tables(m.expression)
        for ref_t in ref_tables:
            # check if ref_t exists in the model tables
            if any(tbl.name == ref_t for tbl in model.tables) and ref_t != m.table:
                raw_edges.append({"from": ref_t, "to": m.name, "type": "Table to Measure"})

    # 3. Measure to Page
    usage = measure_usage(model)
    for m_name, pages in usage.items():
        for page_name in pages:
            raw_edges.append({"from": m_name, "to": page_name, "type": "Measure to Page"})

    # De-duplicate edges
    seen_edges = set()
    edges: list[dict[str, str]] = []
    for edge in raw_edges:
        edge_key = (edge["from"], edge["to"], edge["type"])
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            edges.append(edge)

    # Compute layers
    layer_0_all = sorted(list({e["from"] for e in edges if e["type"] == "Source to Table"}))
    layer_1_all = sorted(list(
        {e["to"] for e in edges if e["type"] == "Source to Table"} |
        {e["from"] for e in edges if e["type"] == "Table to Measure"}
    ))
    layer_2_all = sorted(list(
        {e["to"] for e in edges if e["type"] == "Table to Measure"} |
        {e["from"] for e in edges if e["type"] == "Measure to Page"}
    ))
    layer_3_all = sorted(list({e["to"] for e in edges if e["type"] == "Measure to Page"}))

    # Cap nodes in each layer at 8
    max_cap = 8

    def cap_layer(layer_nodes: list[str], layer_index: int) -> tuple[list[str], dict[str, str], int]:
        # Count connections for sorting
        conn_counts = {}
        for n in layer_nodes:
            count = 0
            for e in edges:
                if e["from"] == n or e["to"] == n:
                    count += 1
            conn_counts[n] = count

        sorted_nodes = sorted(layer_nodes, key=lambda n: conn_counts.get(n, 0), reverse=True)
        if len(sorted_nodes) <= max_cap:
            return sorted_nodes, {}, 0

        keep_count = max_cap - 1
        keep_nodes = sorted_nodes[:keep_count]
        overflow_nodes = sorted_nodes[keep_count:]

        overflow_label = f"+{len(overflow_nodes)} more " + ["sources", "tables", "measures", "pages"][layer_index]
        mapping = {n: overflow_label for n in overflow_nodes}
        return keep_nodes + [overflow_label], mapping, len(overflow_nodes)

    l0, map0, count0 = cap_layer(layer_0_all, 0)
    l1, map1, count1 = cap_layer(layer_1_all, 1)
    l2, map2, count2 = cap_layer(layer_2_all, 2)
    l3, map3, count3 = cap_layer(layer_3_all, 3)

    # Map edges to capped labels
    mapped_raw_edges = []
    for e in edges:
        f = e["from"]
        t = e["to"]

        if e["type"] == "Source to Table":
            f = map0.get(f, f)
            t = map1.get(t, t)
        elif e["type"] == "Table to Measure":
            f = map1.get(f, f)
            t = map2.get(t, t)
        elif e["type"] == "Measure to Page":
            f = map2.get(f, f)
            t = map3.get(t, t)

        mapped_raw_edges.append({"from": f, "to": t, "type": e["type"]})

    # Dedup mapped edges
    seen_mapped = set()
    mapped_edges: list[dict[str, str]] = []
    for me in mapped_raw_edges:
        key = (me["from"], me["to"], me["type"])
        if key not in seen_mapped:
            seen_mapped.add(key)
            mapped_edges.append(me)

    # --- Layout: layered DAG (Sugiyama-style) ------------------------------
    # Order nodes within each column to minimise edge crossings (real layout,
    # not an alphabetical stack). Cheap: layers are capped at 8 nodes.
    layers = _minimize_crossings([l0, l1, l2, l3], mapped_edges)
    counts = [len(layer_0_all), len(layer_1_all), len(layer_2_all), len(layer_3_all)]

    # Geometry derived *from* the columns, so the canvas can never be
    # narrower than its own content (the bug that clipped every Page node).
    PAD = 30            # outer margin
    HEAD_Y = 30         # column-header text baseline
    DIVIDER_Y = 44      # rule under the header row
    TOP = 62            # first card top
    BOX_W = 184
    BOX_H = 40
    V_GAP = 16          # vertical gap between cards in a column
    COL_GAP = 92        # horizontal gap between columns (room for the curves)
    col_x = [PAD + i * (BOX_W + COL_GAP) for i in range(4)]
    W = col_x[-1] + BOX_W + PAD

    max_len = max((len(lay) for lay in layers), default=0)
    content_h = max_len * BOX_H + max(0, max_len - 1) * V_GAP
    H = max(240, TOP + content_h + 28)

    # Pass 1: pure geometry — every node's (x, y), each column vertically
    # centred, so edges (pass 2) can be drawn *under* the cards (pass 3).
    node_coords: dict[str, tuple[float, float]] = {}
    for ci, nodes in enumerate(layers):
        n = len(nodes)
        if not n:
            continue
        col_h = n * BOX_H + (n - 1) * V_GAP
        y0 = TOP + (content_h - col_h) / 2
        for i, name in enumerate(nodes):
            node_coords[name] = (col_x[ci], y0 + i * (BOX_H + V_GAP))

    svg = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-labelledby="lineage-diagram-title">'
        f'<style>text {{ font-family: "Poppins", sans-serif !important; }}</style>'
    ]
    svg.append('<title id="lineage-diagram-title">Data lineage graph: sources to pages</title>')
    svg.append(f'''<defs>
<pattern id="wf-dotbg-lineage" width="8" height="8" patternUnits="userSpaceOnUse">
  <rect width="8" height="8" fill="#ffffff"/><circle cx="1" cy="1" r="0.5" fill="#e6eaf3"/>
</pattern>
<symbol id="wf-i-lin-source" viewBox="0 0 24 24">{_LAYER_ICON["source"]}</symbol>
<symbol id="wf-i-lin-table" viewBox="0 0 24 24">{_LAYER_ICON["table"]}</symbol>
<symbol id="wf-i-lin-measure" viewBox="0 0 24 24">{_LAYER_ICON["measure"]}</symbol>
<symbol id="wf-i-lin-page" viewBox="0 0 24 24">{_LAYER_ICON["page"]}</symbol>
</defs>''')
    svg.append(
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" fill="url(#wf-dotbg-lineage)" '
        f'rx="12" stroke="{_EDGE}" stroke-width="1"/>'
    )

    # Column headers — an accent dot + upper-cased layer name + true count.
    # Only these secondary labels are upper-cased (by literal text), so card
    # titles below keep their real case instead of a global text-transform
    # shouting every table/measure name.
    if max_len:
        svg.append(
            f'<line x1="{PAD}" y1="{DIVIDER_Y}" x2="{W - PAD}" y2="{DIVIDER_Y}" '
            f'stroke="{_EDGE}" stroke-width="1"/>'
        )
        for ci, nodes in enumerate(layers):
            if not nodes:
                continue
            accent = _LAYER_STYLE[ci][1]
            hx = col_x[ci]
            svg.append(
                f'<circle cx="{hx + 4:.1f}" cy="{HEAD_Y - 3:.1f}" r="3.5" fill="{accent}"/>'
                f'<text x="{hx + 14:.1f}" y="{HEAD_Y:.1f}" font-size="10" font-weight="600" '
                f'letter-spacing="0.12em" fill="{_MUTED}">{_LAYER_PLURAL[ci]} · {counts[ci]}</text>'
            )

    # Pass 2: edges — a smooth horizontal cubic between the right edge of the
    # source card and the left edge of the target, painted before the cards.
    for me in mapped_edges:
        p1 = node_coords.get(me["from"])
        p2 = node_coords.get(me["to"])
        if not p1 or not p2:
            continue
        x1, y1 = p1[0] + BOX_W, p1[1] + BOX_H / 2
        x2, y2 = p2[0], p2[1] + BOX_H / 2
        dx = (x2 - x1) * 0.5
        svg.append(
            f'  <path d="M {x1:.1f} {y1:.1f} C {x1 + dx:.1f} {y1:.1f}, {x2 - dx:.1f} {y2:.1f}, {x2:.1f} {y2:.1f}" '
            f'fill="none" stroke="#cdd4e2" stroke-width="1.2" stroke-linecap="round" opacity="0.85"/>'
        )

    # Pass 3: node cards — white surface, slim accent pill, tinted icon
    # badge, real-case title (fitted to the card, full name in a <title>
    # tooltip). Overflow "+N more" is a dashed, muted italic pseudo-card.
    est_char_w = 6.0
    text_x = 47
    max_chars = max(6, int((BOX_W - text_x - 10) / est_char_w))
    for ci, nodes in enumerate(layers):
        if not nodes:
            continue
        layer_key, accent, soft, _label = _LAYER_STYLE[ci]
        for name in nodes:
            cx, cy = node_coords[name]
            is_overflow = name.startswith("+")
            svg.append(f'  <g class="wf-node cat-{layer_key}"><title>{html_e(name)}</title>')
            svg.append(
                f'    <rect x="{cx:.1f}" y="{cy:.1f}" width="{BOX_W}" height="{BOX_H}" rx="10" '
                f'class="wf-card-bg" fill="{_SURFACE}" stroke="{_EDGE}" stroke-width="1" '
                f'stroke-dasharray="{"3,3" if is_overflow else "0"}"/>'
            )
            if is_overflow:
                svg.append(
                    f'    <text x="{cx + BOX_W / 2:.1f}" y="{cy + BOX_H / 2 + 3:.1f}" font-size="9" '
                    f'text-anchor="middle" fill="{_MUTED}" font-style="italic">{html_e(name)}</text>'
                )
                svg.append('  </g>')
                continue
            disp = name if len(name) <= max_chars else name[: max_chars - 1] + "…"
            svg.append(
                f'    <rect x="{cx + 7:.1f}" y="{cy + 9:.1f}" width="3" height="{BOX_H - 18:.1f}" rx="1.5" fill="{accent}"/>'
                f'    <rect x="{cx + 16:.1f}" y="{cy + 9:.1f}" width="22" height="22" rx="6" fill="{soft}"/>'
                f'    <use href="#wf-i-lin-{layer_key}" x="{cx + 21.5:.1f}" y="{cy + 14.5:.1f}" width="11" height="11" '
                f'fill="none" stroke="{accent}"/>'
                f'    <text x="{cx + text_x:.1f}" y="{cy + BOX_H / 2 + 3.5:.1f}" font-size="10.5" '
                f'font-weight="600" fill="{_INK}">{html_e(disp)}</text>'
            )
            svg.append('  </g>')

    svg.append('</svg>')

    svg_str = f'<div class="diagram">{"".join(svg)}{_LEGEND}</div>'
    return edges, svg_str

def get_tables_fed(ds, model) -> list[str]:
    fed = []
    for t in model.tables:
        for p in t.partitions:
            if p.expression:
                if (
                    (ds.server and ds.server in p.expression) or
                    (ds.detail and ds.detail in p.expression) or
                    (ds.database and ds.database in p.expression) or
                    (ds.type and ds.type in p.expression)
                ):
                    fed.append(t.name)
                    break
    return fed

def get_storage_mode(ds, model) -> str:
    fed_tables = get_tables_fed(ds, model)
    modes = set()
    for t_name in fed_tables:
        t = next((tbl for tbl in model.tables if tbl.name == t_name), None)
        if t:
            for p in t.partitions:
                if p.mode:
                    modes.add(p.mode)
    if not modes:
        return "import"
    return ", ".join(sorted(modes))

def is_local_path(detail: str) -> bool:
    if not detail:
        return False
    if "://" in detail:
        return False
    return (
        detail.startswith("\\\\") or
        (len(detail) > 2 and detail[1] == ":" and detail[2] == "\\") or
        "/" in detail or
        "\\" in detail
    )

def build_data_sources_inventory(model: SemanticModel) -> list[dict]:
    import os
    inventory = []
    for ds in model.data_sources:
        location = ds.detail or ds.server or ""

        if is_local_path(location):
            display_location = os.path.basename(location.replace("\\", "/")) or location
            flag = "⚠️ Hardcoded local path"
        else:
            display_location = location
            flag = ""

        fed = get_tables_fed(ds, model)
        mode = get_storage_mode(ds, model)

        inventory.append({
            "type": ds.type or "Unknown",
            "location": location,
            "display_location": display_location,
            "tables_fed": fed,
            "storage_mode": mode,
            "auth": ds.authentication_status or "not specified",
            "flag": flag
        })
    return inventory
