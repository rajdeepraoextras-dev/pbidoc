"""Extract structured facts about pages/visuals/calculated columns from a
:class:`SemanticModel`.

Originally lived inline in ``orchestrator.py`` (used only by the technical
document generator). Promoted to its own module because the audit and
business-guide generators need the same page/visual/calc-column facts —
duplicating this logic per generator would violate the "avoid duplicated
logic" mandate. Pure functions, no LLM involved.
"""

from __future__ import annotations

import re

from ..schemas.model import SemanticModel
from .deterministic import translate_dax

# decorative elements that aren't worth a documentation row when they carry no data
DECORATIVE = {"image", "shape", "basicShape", "textbox", "actionButton", "visualGroup"}
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


def table_priority_key(table_name: str) -> int:
    name_lower = table_name.lower()
    if "measure" in name_lower:
        return 100
    if "rank" in name_lower:
        return 90
    if "slicer" in name_lower or "parameter" in name_lower:
        return 80
    if "date" in name_lower or "calendar" in name_lower or "time" in name_lower:
        return 70
    if "config" in name_lower or "setup" in name_lower or "metadata" in name_lower:
        return 60
    if "sale" in name_lower or "order" in name_lower or "transaction" in name_lower or "fact" in name_lower:
        return 0
    return 10


def visual_label(title: str | None, vtype: str, metrics: list[str], dims: list[str]) -> str:
    """A human-readable name for a visual: its title, else what it shows."""
    if title:
        return title
    friendly = FRIENDLY_VISUAL.get(vtype, vtype)
    if metrics and dims:
        return f"{', '.join(metrics)} by {', '.join(dims)}"
    if metrics:
        return ", ".join(metrics)
    if dims:
        return ", ".join(dims)
    return friendly


def report_pages(model: SemanticModel) -> list[dict]:
    from ..render._wireframe import render_wireframe
    measure_names = {m.name for m in model.all_measures()}
    out = []
    for p in model.pages:
        visuals_raw, decorative = [], 0
        for v in p.visuals:
            if v.is_slicer:
                continue
            metrics, dims = [], []
            for f in v.fields:
                (metrics if f.split(".")[-1] in measure_names else dims).append(f.split(".")[-1])
            if not (v.title or metrics or dims) and v.type in DECORATIVE:
                decorative += 1
                continue
            visuals_raw.append({
                "label": visual_label(v.title, v.type, metrics, dims),
                "type": FRIENDLY_VISUAL.get(v.type, v.type),
                "metrics": metrics, "dimensions": dims, "_title": v.title,
            })

        # Group visuals identical in every way that matters to a reader (same
        # explicit title-or-lack-of-one, type, metrics, and dimensions) into
        # one row with a count, instead of one near-duplicate row per instance
        # (e.g. five "Sale_Value" cards, each explained "Shows Sale_Value.").
        groups: dict[tuple, dict] = {}
        order: list[tuple] = []
        for vis in visuals_raw:
            key = (vis["_title"], vis["type"], frozenset(vis["metrics"]), frozenset(vis["dimensions"]))
            if key not in groups:
                groups[key] = {"label": vis["label"], "type": vis["type"],
                               "metrics": vis["metrics"], "dimensions": vis["dimensions"], "count": 1}
                order.append(key)
            else:
                groups[key]["count"] += 1

        visuals = []
        for key in order:
            vis = groups[key]
            if vis["count"] > 1:
                vis["label"] = f"{vis['label']} — {vis['type']} ×{vis['count']}"
            visuals.append(vis)

        wireframe_svg = render_wireframe(p) or None
        out.append({"name": p.display_name, "hidden": p.is_hidden, "drillthrough": p.is_drillthrough,
                    "visual_count": len(p.visuals), "visuals": visuals, "decorative_count": decorative,
                    "wireframe_svg": wireframe_svg})
    return out


def slicers(model: SemanticModel) -> list[dict]:
    """One row per distinct (page, field) slicer. Multiple slicer visuals
    bound to the same field on the same page collapse into a single row —
    ``count`` tells the caller how many, so callers can note the multiplicity
    ("Type (2 slicers)") instead of repeating the same filter bullet twice."""
    counts: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    for p in model.pages:
        for v in p.visuals:
            if not v.is_slicer:
                continue
            field = v.fields[0] if v.fields else (v.title or "—")
            key = (p.display_name, field)
            if key not in counts:
                order.append(key)
            counts[key] = counts.get(key, 0) + 1
    return [{"field": field, "page": page, "count": counts[(page, field)]} for page, field in order]


def calc_columns(model: SemanticModel) -> list[dict]:
    return [
        {"table": t.name, "column": c.name, "expression": c.expression}
        for t in model.tables for c in t.columns if c.is_calculated and c.expression
    ]


def find_referenced_tables(dax: str) -> list[str]:
    if not dax:
        return []
    quoted = re.findall(r"'([^']+)'\[", dax)
    unquoted = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)(?=\[)", dax)
    return list(set(quoted + unquoted))


def detect_hardcoded_years(dax: str) -> list[str]:
    if not dax:
        return []
    years = re.findall(r"\b(19\d{2}|20\d{2})\b", dax)
    return [y for y in years if 1990 <= int(y) <= 2040]


def first_sentence(text: str) -> str:
    """First sentence of a description — glossaries reference the fuller
    definition living elsewhere (the Measure Catalog, DAX translation) rather
    than repeating it in full."""
    text = (text or "").strip()
    m = re.match(r"(.+?[.!?])(?:\s|$)", text)
    return m.group(1) if m else text


_TABLE_COLUMN_RE = re.compile(r"(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]")


def declassify(text: str) -> str:
    """Strip ``Table[Column]``/``'Table Name'[Column]`` bracket notation down
    to the bare field name. Business-facing prose surfaces (the user guide's
    glossary, the executive doc's Key KPIs) reuse the deterministic DAX-to-
    English translator as their offline fallback for a real per-measure
    meaning (1.5/1.6) — this keeps that text free of DAX-flavored syntax even
    though the translator itself is written for a technical audience."""
    return _TABLE_COLUMN_RE.sub(r"\1", text)


# Single-argument DAX aggregation calls translate_dax() only prettifies at
# the *outermost* level — a nested call inside another function's argument
# (e.g. "DIVIDE ( Revenue, DISTINCTCOUNT ( Sales[Key] ) )") passes straight
# through _pretty() untouched, so the raw function-call syntax otherwise
# leaks into a business-facing KPI/glossary line.
_SIMPLE_AGG_PHRASES = {
    "DISTINCTCOUNT": "the number of unique {arg} values",
    "COUNTROWS": "the number of {arg} rows",
    "COUNT": "the count of {arg}",
    "COUNTA": "the count of {arg}",
    "SUM": "the total {arg}",
    "AVERAGE": "the average {arg}",
    "MIN": "the minimum {arg}",
    "MAX": "the maximum {arg}",
}
_SIMPLE_AGG_RE = re.compile(
    r"\b(" + "|".join(_SIMPLE_AGG_PHRASES) + r")\s*\(\s*([^()]+?)\s*\)"
)


def simplify_dax_prose(text: str) -> str:
    """Rewrite simple single-argument DAX aggregation calls into plain
    English, however deeply nested — repeatedly replaces the innermost
    matching call (one with no parens left in its own argument) until none
    remain, so nesting inside DIVIDE/CALCULATE/etc. is covered without a
    full DAX parser. Business-facing surfaces only (technical calculation
    logic keeps the precise DAX)."""
    prev = None
    while prev != text:
        prev = text

        def _repl(m: re.Match) -> str:
            func, arg = m.group(1), m.group(2).strip()
            return _SIMPLE_AGG_PHRASES[func].format(arg=arg)

        text = _SIMPLE_AGG_RE.sub(_repl, text)
    return text


def business_plain_english(name: str, expression: str, format_string: str | None) -> str:
    """The deterministic, business-safe one-liner for a measure: the same
    DAX-to-English translation the technical doc's fallback uses, then
    stripped of DAX-flavored syntax (bracket notation, raw aggregation
    function calls) that has no place in a glossary or an executive KPI
    line. Shared by the user guide's glossary and the executive doc's Key
    KPIs so the two never phrase the same measure differently."""
    english, _, _ = translate_dax(name, expression, format_string)
    return declassify(simplify_dax_prose(first_sentence(english)))


def data_source_summaries(model: SemanticModel) -> list[str]:
    """One human-readable line per data source, e.g. ``"Sql.Database:
    prod-sql.contoso.com/SalesDW"``. Shared by the technical document's
    Lineage section and the executive summary's Data Sources section."""
    sources = []
    for ds in model.data_sources:
        target = ds.server or ds.detail or ""
        if ds.database:
            target = f"{target}/{ds.database}" if target else ds.database
        sources.append(f"{ds.type or 'Source'}: {target}".rstrip(": "))
    return sources


_LOCAL_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def local_path_sources(model: SemanticModel) -> list[str]:
    """Data-source targets that look like a hardcoded local path (a drive
    letter or a user-profile directory) rather than a server/gateway address
    — these break refresh as soon as the report runs on any machine other
    than the report author's. Shared by the technical document's Data Model
    risks and the deterministic audit engine's governance findings."""
    paths = []
    for ds in model.data_sources:
        target = ds.server or ds.detail or ""
        if ds.database:
            target = f"{target}/{ds.database}" if target else ds.database
        if _LOCAL_PATH_RE.search(target) or "Users/" in target or "Users\\" in target:
            paths.append(target)
    return paths
