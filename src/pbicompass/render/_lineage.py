from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..schemas.model import SemanticModel

from ._diagram_theme import (
    ACCENT, CAPTION, EDGE, FAINT, GHOST_EDGE, GHOST_FILL, INK, MUTED,
    canvas, canvas_defs, chip, legend,
)
from ._shared import anchor_slug, html_e
from ..agents.usage import measure_usage
from ..agents.report_facts import find_referenced_tables, data_source_summaries

# v6 "Studio" (2026-07-11) — the lineage graph now shares the wireframe's
# v6 design DNA (see ``_diagram_theme``): the soft gradient + dot-grid
# canvas, white cards with gradient icon chips and a gradient left accent
# bar, two-line cards (real-case title + an informative sublabel: a table's
# column/measure count, a measure's home table, a page's visual count, a
# source's friendly kind), and column headers as pill badges with counts.
#
# Two functional upgrades over v5:
# * **Every node is a deep link.** Tables jump to their §6 row, measures to
#   their §7 entry, pages to their §8 block, sources to their §5 inventory
#   row, and "+N more" overflow cards to the section heading — the graph is
#   now a navigation surface, not just a picture (mirrors the wireframe's
#   I3 linking).
# * **Hover-connect.** Nodes carry ``data-node`` and edges ``data-from``/
#   ``data-to`` (layer-prefixed slugs); the shell script highlights a
#   hovered node's edges + neighbors and dims the rest.
#
# Edges are cubic Béziers stroked with a per-edge gradient running from the
# source layer's accent to the target layer's. Each gradient MUST use
# ``gradientUnits="userSpaceOnUse"`` with the edge's own endpoints: the
# default objectBoundingBox units collapse on a perfectly horizontal path
# (zero-height bbox), which would make every straight edge invisible.
#
# The v5 layout engine is kept as-is: canvas geometry derived from the
# columns (nothing can clip), nodes ordered by the iterated median heuristic
# (Sugiyama's crossing-minimization pass, the core of dagre/Graphviz),
# implemented natively so the renderer stays dependency-free.
_LAYER_STYLE = [
    ("source", ACCENT["source"]),
    ("table", ACCENT["table"]),
    ("measure", ACCENT["measure"]),
    ("page", ACCENT["page"]),
]
_LAYER_ICON = {
    "source": '<ellipse cx="12" cy="5" rx="8" ry="3" fill="none" stroke-width="1.8"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" fill="none" stroke-width="1.8"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke-width="1.8"/><path d="M3 10h18M9 10v10" fill="none" stroke-width="1.8"/>',
    "measure": '<path d="M4 19V5M4 19h16M8 15l3-4 3 3 4-6" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    "page": '<rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke-width="1.8"/><path d="M3 9h18" fill="none" stroke-width="1.8"/>',
}
_LAYER_PLURAL = ["SOURCES", "TABLES", "MEASURES", "PAGES"]
# The chip-gradient category name each layer borrows (see _diagram_theme:
# the four accent hues are shared between wireframe categories and layers).
_LAYER_CHIP_CAT = ["decorative", "data", "slicer", "nav"]
# Section each layer's "+N more" overflow card links to.
_LAYER_SECTION = [("sec5", "§5"), ("sec6", "§6"), ("sec7", "§7"), ("sec8", "§8")]

_LEGEND = legend([("source", "Source"), ("table", "Table"),
                  ("measure", "Measure"), ("page", "Page")])

# M source-function prefix -> reader-facing kind, for source-card sublabels.
_FRIENDLY_SOURCE_KIND = {
    "File.Contents": "Local file", "Excel.Workbook": "Excel workbook",
    "Sql.Database": "SQL Server", "Sql.Databases": "SQL Server",
    "AnalysisServices.Database": "Analysis Services",
    "PowerBI.Dataflows": "Power BI dataflow", "PowerPlatform.Dataflows": "Dataflow",
    "Web.Contents": "Web", "OData.Feed": "OData feed",
    "SharePoint.Files": "SharePoint", "SharePoint.Contents": "SharePoint",
    "Odbc.DataSource": "ODBC", "Databricks.Catalogs": "Databricks",
    "Snowflake.Databases": "Snowflake", "GoogleBigQuery.Database": "BigQuery",
}


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


def _source_title_sub(label: str) -> tuple[str, str]:
    """Split a raw source label (``"File.Contents: C:\\...\\Data.xlsx"``)
    into a short card title (the file/host name) and a friendly-kind
    sublabel — the full label stays in the tooltip."""
    kind, sep, target = label.partition(": ")
    if not sep:
        kind, target = label, ""
    title = target
    if "/" in target or "\\" in target:
        title = target.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or target
    sub = _FRIENDLY_SOURCE_KIND.get(kind, kind)
    if title.lower().endswith((".xlsx", ".xlsm", ".xls")):
        sub = "Excel workbook"
    elif title.lower().endswith(".csv"):
        sub = "CSV file"
    return (title or kind), sub


def _pluralize(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


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

    # --- Per-node display metadata (title, sublabel, href, tooltip) --------
    table_by_name = {t.name: t for t in model.tables}
    measure_table = {m.name: m.table for m in measures}
    page_visuals = {p.display_name: len(p.visuals) for p in model.pages}
    layer_index = {n: ci for ci, lay in enumerate(layers) for n in lay}

    def _node_meta(name: str, ci: int) -> tuple[str, str, str, str]:
        """-> (title, sublabel, href, tooltip)"""
        if name.startswith("+"):
            sec_id, sec_label = _LAYER_SECTION[ci]
            return name, f"view all in {sec_label}", f"#{sec_id}", name
        if ci == 0:
            title, sub = _source_title_sub(name)
            return title, sub, f"#source-{anchor_slug(name)}", name
        if ci == 1:
            t = table_by_name.get(name)
            parts = []
            if t is not None:
                if t.columns:
                    parts.append(_pluralize(len(t.columns), "column"))
                if t.measures:
                    parts.append(_pluralize(len(t.measures), "measure"))
            return name, " · ".join(parts), f"#table-{anchor_slug(name)}", name
        if ci == 2:
            home = measure_table.get(name)
            return name, (f"in {home}" if home else ""), f"#measure-{anchor_slug(name)}", name
        n_vis = page_visuals.get(name)
        return name, (_pluralize(n_vis, "visual") if n_vis else ""), f"#page-{anchor_slug(name)}", name

    # Geometry derived *from* the columns, so the canvas can never be
    # narrower than its own content (the bug that clipped every Page node).
    PAD = 28            # outer margin
    HEAD_Y = 18         # column-header pill top
    TOP = 64            # first card top
    BOX_W = 190
    BOX_H = 48
    V_GAP = 14          # vertical gap between cards in a column
    COL_GAP = 104       # horizontal gap between columns (room for the curves)
    col_x = [PAD + i * (BOX_W + COL_GAP) for i in range(4)]
    W = col_x[-1] + BOX_W + PAD

    max_len = max((len(lay) for lay in layers), default=0)
    content_h = max_len * BOX_H + max(0, max_len - 1) * V_GAP
    H = max(240, TOP + content_h + 24)

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

    # Hover-connect keys: layer-prefixed slugs, so a table and a measure
    # that share a name can never cross-highlight.
    _PREFIX = ("s-", "t-", "m-", "p-")

    def _node_key(name: str, ci: int) -> str:
        return _PREFIX[ci] + anchor_slug(name)

    _EDGE_LAYERS = {"Source to Table": (0, 1), "Table to Measure": (1, 2), "Measure to Page": (2, 3)}

    svg = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-labelledby="lineage-diagram-title">'
        f'<style>text {{ font-family: "Poppins", sans-serif !important; }}</style>'
    ]
    svg.append('<title id="lineage-diagram-title">Data lineage graph: sources to pages — click any node to jump to its section</title>')

    # Pass 2 prep: edge geometry + one userSpaceOnUse gradient per edge
    # (source-layer accent -> target-layer accent along the actual segment).
    edge_defs: list[str] = []
    edge_elems: list[str] = []
    for i, me in enumerate(mapped_edges):
        p1 = node_coords.get(me["from"])
        p2 = node_coords.get(me["to"])
        if not p1 or not p2:
            continue
        lf, lt = _EDGE_LAYERS.get(me["type"], (0, 1))
        a1, a2 = _LAYER_STYLE[lf][1], _LAYER_STYLE[lt][1]
        x1, y1 = p1[0] + BOX_W, p1[1] + BOX_H / 2
        x2, y2 = p2[0], p2[1] + BOX_H / 2
        dx = (x2 - x1) * 0.5
        gid = f"lg-eg-{i}"
        edge_defs.append(
            f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
            f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}">'
            f'<stop offset="0" stop-color="{a1}"/><stop offset="1" stop-color="{a2}"/></linearGradient>'
        )
        fkey, tkey = _node_key(me["from"], lf), _node_key(me["to"], lt)
        edge_elems.append(
            f'<g class="lg-edge" data-from="{fkey}" data-to="{tkey}">'
            f'<path d="M {x1:.1f} {y1:.1f} C {x1 + dx:.1f} {y1:.1f}, {x2 - dx:.1f} {y2:.1f}, {x2:.1f} {y2:.1f}" '
            f'fill="none" stroke="url(#{gid})" stroke-width="1.6" stroke-linecap="round" opacity="0.38"/>'
            f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="2.2" fill="{a1}" opacity="0.5"/>'
            f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="2.2" fill="{a2}" opacity="0.5"/>'
            f'</g>'
        )

    svg.append(f'''<defs>{canvas_defs("lineage")}
<symbol id="wf-i-lin-source" viewBox="0 0 24 24">{_LAYER_ICON["source"]}</symbol>
<symbol id="wf-i-lin-table" viewBox="0 0 24 24">{_LAYER_ICON["table"]}</symbol>
<symbol id="wf-i-lin-measure" viewBox="0 0 24 24">{_LAYER_ICON["measure"]}</symbol>
<symbol id="wf-i-lin-page" viewBox="0 0 24 24">{_LAYER_ICON["page"]}</symbol>
{"".join(edge_defs)}</defs>''')
    svg.append(canvas(0.5, 0.5, W - 1, H - 1, "lineage"))

    # Column headers — white pill badges: accent dot, letter-spaced layer
    # name, true (pre-cap) count in the accent. Only these captions are
    # upper-cased, so card titles keep their real case.
    if max_len:
        for ci, nodes in enumerate(layers):
            if not nodes:
                continue
            accent = _LAYER_STYLE[ci][1]
            label = _LAYER_PLURAL[ci]
            count_txt = str(counts[ci])
            pill_w = 23 + len(label) * 6.1 + 10 + len(count_txt) * 5.8 + 10
            hx = col_x[ci]
            svg.append(
                f'<g class="wf-node"><rect x="{hx:.1f}" y="{HEAD_Y}" width="{pill_w:.1f}" height="24" rx="12" '
                f'fill="#ffffff" stroke="{EDGE}"/>'
                f'<circle cx="{hx + 14:.1f}" cy="{HEAD_Y + 12:.1f}" r="3.2" fill="{accent}"/>'
                f'<text x="{hx + 23:.1f}" y="{HEAD_Y + 15.5:.1f}" font-size="9" font-weight="600" '
                f'letter-spacing="0.1em" fill="{MUTED}">{label}</text>'
                f'<text x="{hx + 23 + len(label) * 6.1 + 10:.1f}" y="{HEAD_Y + 15.5:.1f}" font-size="9" '
                f'font-weight="700" fill="{accent}">{count_txt}</text></g>'
            )

    # Pass 2: edges — gradient cubics + endpoint dots, painted before cards.
    svg.extend(edge_elems)

    # Pass 3: node cards — white surface, gradient left accent bar, gradient
    # icon chip, real-case title + informative sublabel (full name in a
    # <title> tooltip). Every card is an <a> deep link into the document;
    # overflow "+N more" is a ghost card linking to its section.
    title_chars = int((BOX_W - 52 - 10) / 6.2)
    sub_chars = int((BOX_W - 52 - 8) / 5.0)
    for ci, nodes in enumerate(layers):
        if not nodes:
            continue
        layer_key, accent = _LAYER_STYLE[ci]
        chip_cat = _LAYER_CHIP_CAT[ci]
        for name in nodes:
            cx, cy = node_coords[name]
            is_overflow = name.startswith("+")
            title, sub, href, tooltip = _node_meta(name, ci)
            key = _node_key(name, ci)
            node = [f'<a href="{html_e(href)}">',
                    f'<g class="wf-node cat-{layer_key}" data-node="{html_e(key)}">',
                    f'<title>{html_e(tooltip)}</title>']
            if is_overflow:
                node.append(
                    f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{BOX_W}" height="{BOX_H}" rx="11" '
                    f'class="wf-card-bg" fill="{GHOST_FILL}" stroke="{GHOST_EDGE}" stroke-width="1" '
                    f'stroke-dasharray="4 3"/>'
                    f'<text x="{cx + BOX_W / 2:.1f}" y="{cy + 21:.1f}" font-size="10" text-anchor="middle" '
                    f'font-weight="600" fill="{MUTED}">{html_e(title)}</text>'
                    f'<text x="{cx + BOX_W / 2:.1f}" y="{cy + 34:.1f}" font-size="8.5" text-anchor="middle" '
                    f'font-weight="500" fill="{FAINT}">{html_e(sub)}</text>'
                )
            else:
                disp = title if len(title) <= title_chars else title[: title_chars - 1].rstrip() + "…"
                sub_disp = sub if len(sub) <= sub_chars else sub[: sub_chars - 1].rstrip() + "…"
                node.append(
                    f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{BOX_W}" height="{BOX_H}" rx="11" '
                    f'class="wf-card-bg" fill="#ffffff" stroke="{EDGE}" stroke-width="1"/>'
                    f'<rect x="{cx + 8:.1f}" y="{cy + 9:.1f}" width="3.5" height="{BOX_H - 18:.1f}" rx="1.75" '
                    f'fill="url(#dg-chip-{chip_cat}-lineage)"/>'
                )
                node.append(chip(cx + 19, cy + 12, 24, chip_cat, f"wf-i-lin-{layer_key}", "lineage"))
                if sub_disp:
                    node.append(
                        f'<text x="{cx + 52:.1f}" y="{cy + 22:.1f}" font-size="11" font-weight="600" '
                        f'fill="{INK}">{html_e(disp)}</text>'
                        f'<text x="{cx + 52:.1f}" y="{cy + 35:.1f}" font-size="8.5" font-weight="500" '
                        f'fill="{CAPTION}">{html_e(sub_disp)}</text>'
                    )
                else:
                    node.append(
                        f'<text x="{cx + 52:.1f}" y="{cy + BOX_H / 2 + 3.5:.1f}" font-size="11" '
                        f'font-weight="600" fill="{INK}">{html_e(disp)}</text>'
                    )
            node.append('</g></a>')
            svg.append("".join(node))

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
            # The same label the lineage graph names this source with — html.py
            # derives the §5 row's ``id="source-…"`` anchor from it, so the
            # graph's source cards deep-link onto the matching inventory row.
            "label": get_source_label(ds),
            "location": location,
            "display_location": display_location,
            "tables_fed": fed,
            "storage_mode": mode,
            "auth": ds.authentication_status or "not specified",
            "flag": flag
        })
    return inventory
