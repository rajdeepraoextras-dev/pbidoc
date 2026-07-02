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
    measure_names = {m.name for m in model.all_measures()}
    out = []
    for p in model.pages:
        visuals, decorative = [], 0
        visuals_raw = []
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
                "metrics": metrics, "dimensions": dims,
            })

        # Disambiguate duplicate visual names on the same page
        label_counts = {}
        for vis in visuals_raw:
            label_counts[vis["label"]] = label_counts.get(vis["label"], 0) + 1

        label_seen = {}
        for vis in visuals_raw:
            lbl = vis["label"]
            if label_counts[lbl] > 1:
                label_seen[lbl] = label_seen.get(lbl, 0) + 1
                vis["label"] = f"{lbl} [{label_seen[lbl]}]"
            visuals.append(vis)

        out.append({"name": p.display_name, "hidden": p.is_hidden, "drillthrough": p.is_drillthrough,
                    "visual_count": len(p.visuals), "visuals": visuals, "decorative_count": decorative})
    return out


def slicers(model: SemanticModel) -> list[dict]:
    return [
        {"field": (v.fields[0] if v.fields else (v.title or "—")), "page": p.display_name}
        for p in model.pages for v in p.visuals if v.is_slicer
    ]


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
