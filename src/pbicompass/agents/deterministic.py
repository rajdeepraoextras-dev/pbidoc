"""Deterministic, rule-based generators.

These run with no external dependencies and no API key. They are both the
zero-config default and the fallback if an LLM call fails. The prose is
serviceable rather than polished — the LLM agents produce the polished version
of the same structured output.
"""

from __future__ import annotations

import re
from collections import Counter

from ..schemas.document import ExecutiveSummary, PageSummary, VisualExplainer
from ..schemas.model import SemanticModel

# friendly names for visual types (singular form; pluralised on use)
_VISUAL_NAMES = {
    "card": "card", "multiRowCard": "multi-row card", "kpi": "KPI",
    "clusteredColumnChart": "column chart", "columnChart": "column chart",
    "clusteredBarChart": "bar chart", "barChart": "bar chart",
    "lineChart": "line chart", "areaChart": "area chart", "lineClusteredColumnComboChart": "combo chart",
    "pieChart": "pie chart", "donutChart": "donut chart", "treemap": "treemap",
    "tableEx": "table", "pivotTable": "matrix", "matrix": "matrix",
    "map": "map", "filledMap": "map", "shapeMap": "map", "gauge": "gauge",
    "slicer": "slicer", "image": "image", "textbox": "text box", "actionButton": "button",
    "shape": "shape", "scatterChart": "scatter chart", "funnel": "funnel",
    "waterfallChart": "waterfall chart", "ribbonChart": "ribbon chart",
    "decompositionTreeVisual": "decomposition tree", "keyInfluencersVisual": "key-influencers visual",
}


def _visual_name(vtype: str, n: int) -> str:
    base = _VISUAL_NAMES.get(vtype, vtype)
    if n == 1:
        return base
    return base + "es" if base.endswith(("s", "x", "ch")) else base + "s"

# -- DAX translation ----------------------------------------------------------
_TIME_FUNCS = {"TOTALYTD", "TOTALQTD", "TOTALMTD", "DATESYTD", "DATESQTD",
               "DATESMTD", "SAMEPERIODLASTYEAR", "DATEADD", "PARALLELPERIOD"}
_VERB = {
    "SUM": "the total of", "SUMX": "the total of",
    "AVERAGE": "the average of", "AVERAGEX": "the average of",
    "MIN": "the minimum of", "MINX": "the minimum of",
    "MAX": "the maximum of", "MAXX": "the maximum of",
    "COUNT": "a count of", "COUNTA": "a count of",
    "COUNTROWS": "the number of rows in", "DISTINCTCOUNT": "the distinct count of",
}


def _top_func(expr: str):
    m = re.search(r"\b([A-Z][A-Z0-9\.]{1,})\s*\(", expr)
    return m.group(1) if m else None


def _call_args(expr: str, func: str):
    """Return the top-level comma-separated arguments of ``func(...)``."""
    m = re.search(re.escape(func) + r"\s*\(", expr)
    if not m:
        return None
    i = m.end()
    depth, start, args = 1, i, []
    while i < len(expr) and depth > 0:
        ch = expr[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                args.append(expr[start:i].strip())
        elif ch == "," and depth == 1:
            args.append(expr[start:i].strip())
            start = i + 1
        i += 1
    return [a for a in args if a]


# ``(?<!\w)`` pins the bare-name branch to the start of an identifier run,
# so a run with no closing bracket after it fails once in O(1) instead of
# being rescanned from every offset — which goes quadratic on a long
# identifier run (e.g. a malformed/minified DAX expression). A \w+ match
# can never start mid-run anyway (any earlier start would have consumed
# through this position), so the lookbehind changes no results.
_COLUMN_REF_RE = re.compile(r"(?:'[^']+'|(?<!\w)\w+)\[[^\]]+\]")


def _column_refs(expr: str) -> list[str]:
    """Find ``Table[Column]`` / ``'Table Name'[Column]`` references."""
    return _COLUMN_REF_RE.findall(expr)


def _measure_refs(expr: str) -> list[str]:
    return re.findall(r"(?<![\w'\]])\[([^\]]+)\]", expr)


def _pretty(expr: str) -> str:
    collapsed = re.sub(r"\s+", " ", expr).strip()
    # strip brackets off bare measure references for readability
    return re.sub(r"(?<![\w'\]])\[([^\]]+)\]", r"\1", collapsed)


def _category(name: str, expr: str, func, fmt) -> str:
    u = expr.upper()
    nl = name.lower()
    fmt = fmt or ""
    if func in _TIME_FUNCS or any(t in u for t in ("YTD", "QTD", "MTD")):
        return "Time-Intelligence"
    if "RANKX" in u or "TOPN" in u or func in ("RANKX", "RANK"):
        return "Ranking"
    if '&"' in expr or '"&' in expr or expr.strip().startswith('"'):
        return "Text"
    if func == "DIVIDE" or "%" in fmt or any(k in nl for k in ("ratio", "rate", "%", "pct", "percent")):
        return "Ratio"
    if func in ("COUNT", "COUNTA", "COUNTROWS", "DISTINCTCOUNT"):
        return "Count"
    money_words = ("revenue", "sales", "amount", "price", "margin", "value", "gmv", "profit", "cost", "expense")
    money = (
        "$" in fmt or "€" in fmt or "£" in fmt
        or any(k in nl for k in money_words)
        or any(k in u for k in ("[VALUE]", "[AMOUNT]", "[SALE", "[PRICE", "[REVENUE", "[NETAMOUNT"))
    )
    if money and any(k in nl for k in ("cost", "expense")):
        return "Cost"
    if money:
        return "Revenue"
    if func in ("SUM", "SUMX", "AVERAGE", "AVERAGEX", "MIN", "MINX", "MAX", "MAXX"):
        return "Aggregation"
    return "Other"


def _caveats(expr: str) -> str:
    notes: list[str] = []
    for col, val in re.findall(r"\[?(\w+)\]?\s*<>\s*\"([^\"]+)\"", expr):
        notes.append(f'excludes rows where {col} = "{val}"')
    if re.search(r"\bALL\s*\(", expr):
        notes.append("ignores existing filters via ALL()")
    if re.search(r"\bUSERELATIONSHIP\s*\(", expr):
        notes.append("activates an inactive relationship")
    if re.search(r"\bFILTER\s*\(", expr) and not notes:
        notes.append("applies a row-level filter")
    return "; ".join(notes)


def translate_dax(name: str, expression: str, format_string: str | None) -> tuple[str, str, str]:
    """Return (plain_english, caveats, category) for one measure."""
    expr = expression or ""
    func = _top_func(expr)
    cols = _column_refs(expr)
    measures = _measure_refs(expr)
    caveats = _caveats(expr)
    category = _category(name, expr, func, format_string)

    is_text = '&"' in expr or '"&' in expr or expr.strip().startswith('"')
    if is_text:
        refs = ", ".join(measures + cols) or "the selected values"
        english = f"Builds a dynamic text label by combining {refs}."
    elif func == "RANKX" or "RANKX(" in expr.upper():
        args = _call_args(expr, "RANKX") or []
        ranked = _pretty(args[1]) if len(args) > 1 else (measures[0] if measures else "a measure")
        english = f"Ranks items by {ranked} (used for Top-N filtering)."
    elif func == "SELECTEDVALUE":
        args = _call_args(expr, "SELECTEDVALUE") or []
        col = _pretty(args[0]) if args else "a column"
        english = f"Returns the currently selected {col} value (blank if more than one is selected)."
    elif func in _TIME_FUNCS:
        args = _call_args(expr, func) or []
        target = _pretty(args[0]) if args else "the base measure"
        english = f"A time-intelligence calculation: the {('year' if 'YTD' in func else 'period')}-to-date value of {target}."
    elif func == "DIVIDE":
        args = _call_args(expr, func) or []
        if len(args) >= 2:
            english = f"A ratio: {_pretty(args[0])} divided by {_pretty(args[1])}."
        else:
            english = "A ratio calculated with DIVIDE()."
    elif func in ("SUMX", "AVERAGEX", "MINX", "MAXX"):
        args = _call_args(expr, func) or []
        scope = _pretty(args[0]) if args else "a table"
        body = _pretty(args[1]) if len(args) > 1 else "an expression"
        english = f"Computes {_VERB.get(func, 'an aggregate of')} {body}, evaluated row by row over {scope}."
    elif func in _VERB:
        target = cols[0] if cols else (measures[0] if measures else "values")
        english = f"Computes {_VERB[func]} {target}."
    elif func == "CALCULATE":
        args = _call_args(expr, func) or []
        inner = _pretty(args[0]) if args else "a base expression"
        english = f"Evaluates {inner} with additional filter context applied."
    elif func == "IF":
        english = f"A conditional measure: {_pretty(expr)}."
    elif not func:
        english = f"A derived metric: {_pretty(expr)}."
    else:
        refs = ", ".join(measures or cols) or "model fields"
        english = f"Computes {func}() over {refs}."
    return english, caveats, category


# -- Business Analyst (deterministic) -----------------------------------------
_COMPLEX_VISUALS = {
    "decompositionTreeVisual": "A decomposition tree breaks a measure down one "
        "field at a time. Click the + on a node and pick a field to expand by; "
        "the tree shows which categories contribute most to the total.",
    "keyInfluencersVisual": "A key-influencers visual ranks the factors that most "
        "increase or decrease the selected metric. Read the left panel for the top "
        "drivers and select one to see its effect.",
    "scatterChart": "A scatter chart plots two measures against each other (X vs Y), "
        "often with bubble size as a third. Look for clusters and outliers rather "
        "than reading exact values.",
    "map": "A map plots values geographically. Bubble size or colour encodes the "
        "measure — compare regions visually and zoom for detail.",
    "filledMap": "A filled (choropleth) map shades regions by a measure. Darker / "
        "more saturated areas indicate higher values.",
    "gauge": "A gauge shows a single value against a target. The needle position "
        "relative to the target band tells you whether you are on track.",
    "waterfallChart": "A waterfall chart shows how a starting value rises and falls "
        "to an ending value. Green/red bars are the incremental contributions.",
    "ribbonChart": "A ribbon chart shows ranking changes over time — the ribbons "
        "reorder between periods to show who moved up or down.",
    "funnel": "A funnel chart shows drop-off through sequential stages; compare the "
        "width of each stage to spot where the biggest losses occur.",
    "treemap": "A treemap shows proportion by rectangle area — bigger tiles are "
        "larger contributors to the total.",
}


def _page_questions(visuals, measure_names: set[str], field_param_tables: set[str] = frozenset()) -> list[str]:
    """Up to three business questions grounded in the metric/dimension pairs
    actually shown on the page — never invented beyond the fields present.
    Fields from a field-parameter/disconnected helper table (I4) are
    excluded — they're a UI selector, not a real dimension to ask about."""
    from .report_facts import is_field_selector

    metrics: list[str] = []
    dims: list[str] = []
    for v in visuals:
        for f in v.fields:
            if is_field_selector(f, field_param_tables):
                continue
            leaf = f.split(".")[-1]
            if not leaf:
                continue
            bucket = metrics if leaf in measure_names else dims
            if leaf not in bucket:
                bucket.append(leaf)
    questions: list[str] = []
    if metrics and dims:
        questions.append(f"How does {metrics[0]} break down by {dims[0]}?")
    if metrics and len(dims) > 1:
        questions.append(f"Which {dims[1]} values drive {metrics[0]}?")
    if len(metrics) > 1:
        questions.append(f"How do {metrics[0]} and {metrics[1]} compare for the selected period?")
    return questions[:3]


def _page_theme(visuals, field_param_tables: set[str] = frozenset()) -> str:
    """Field-parameter fields (I4) are excluded — a UI selector isn't a
    "key field" worth naming in the page summary any more than it's a real
    dimension to ask a business question about."""
    from .report_facts import is_field_selector

    seen: list[str] = []
    for v in visuals:
        for f in v.fields:
            if is_field_selector(f, field_param_tables):
                continue
            leaf = f.split(".")[-1]
            if leaf and leaf not in seen:
                seen.append(leaf)
    return ", ".join(seen[:5])


def business_analyst_deterministic(model: SemanticModel) -> ExecutiveSummary:
    from .report_facts import field_parameter_table_names

    visible_pages = [p for p in model.pages if not p.is_hidden]
    facts = [t.name for t in model.tables if t.kind == "fact"]
    dims = [t.name for t in model.tables if t.kind == "dimension"]
    key_measures = [m.name for m in model.all_measures() if not m.is_hidden][:6]
    measure_names = {m.name for m in model.all_measures()}
    field_param_tables = field_parameter_table_names(model)

    subject = facts[0] if facts else (model.tables[0].name if model.tables else "the data")
    purpose = f"'{model.report_name}' reports on {subject}"
    if key_measures:
        purpose += f", tracking {', '.join(key_measures[:4])}"
    if dims:
        purpose += f" across {', '.join(dims[:4])}"
    from ..render._shared import pluralize_count  # lazy: avoids the agents<->render import cycle

    purpose += (
        f". It spans {pluralize_count('report page', len(visible_pages))}, documented individually below. "
        "This purpose statement is derived from the model contents alone — the business "
        "context requires confirmation from the report owner."
    )

    pages: list[PageSummary] = []
    for p in visible_pages:
        theme = _page_theme(p.visuals, field_param_tables)
        counts = Counter(v.type for v in p.visuals)
        inv = ", ".join(f"{n} {_visual_name(vt, n)}" for vt, n in counts.most_common(5))
        summary = f"Presents {pluralize_count('visual', len(p.visuals))}"
        if inv:
            summary += f" — {inv}"
        summary += "."
        if theme:
            summary += f" Key fields: {theme}."
        if p.is_drillthrough:
            summary += " Drill-through detail page reached from other pages."
        pages.append(PageSummary(
            page_title=p.display_name,
            summary=summary,
            business_questions=_page_questions(p.visuals, measure_names, field_param_tables),
        ))

    from .report_facts import FIELD_SELECTOR_LABEL, is_field_selector

    nav: list[str] = []
    for p in model.pages:
        for v in p.visuals:
            if v.is_slicer:
                raw_field = v.fields[0] if v.fields else v.title or "a field"
                field = FIELD_SELECTOR_LABEL if v.fields and is_field_selector(raw_field, field_param_tables) else raw_field
                nav.append(
                    f"On '{p.display_name}', use the '{field}' slicer to filter the visuals on that page."
                )
    for p in model.pages:
        if p.is_drillthrough:
            nav.append(
                f"Right-click a data point on a summary page and choose Drill through to open "
                f"the '{p.display_name}' page for row-level detail."
            )
    if not nav:
        nav.append("This report has no slicers or drill-through pages; all visuals show unfiltered data.")

    explainers: list[VisualExplainer] = []
    seen: set[tuple[str, str]] = set()
    for p in model.pages:
        for v in p.visuals:
            if v.type in _COMPLEX_VISUALS and (v.type, p.display_name) not in seen:
                seen.add((v.type, p.display_name))
                explainers.append(
                    VisualExplainer(
                        visual=v.title or v.type,
                        page=p.display_name,
                        how_to_read=_COMPLEX_VISUALS[v.type],
                    )
                )

    return ExecutiveSummary(
        core_purpose=purpose,
        pages=pages,
        navigation_guide=nav,
        complex_visual_explainers=explainers,
    )


# -- Data Modeler (deterministic) ---------------------------------------------
def schema_shape(model: SemanticModel) -> tuple[str, list[str], list[str]]:
    """Return ``(shape_description, fact_table_names, dimension_table_names)``.

    Extracted out of :func:`data_modeler_deterministic` so the audit rules'
    star-schema best-practice check can reuse the same shape detection
    instead of re-deriving it.
    """
    facts = [t.name for t in model.tables if t.kind == "fact"]
    dims = [t.name for t in model.tables if t.kind == "dimension"]
    snowflake = any(
        r.from_table in dims and r.to_table in dims for r in model.relationships
    )
    if len(facts) == 1 and dims and not snowflake:
        shape = f"a star schema centred on the '{facts[0]}' fact table"
    elif snowflake:
        shape = "a snowflake schema (dimensions relate to other dimensions)"
    elif len(facts) > 1:
        shape = f"a multi-fact (galaxy) schema with {len(facts)} fact tables"
    elif not model.relationships:
        shape = "a flat / disconnected model (no relationships defined)"
    else:
        shape = "a relational model"
    return shape, facts, dims


def data_modeler_deterministic(model: SemanticModel) -> tuple[str, list[str]]:
    from ..render._shared import pluralize_count  # lazy: avoids the agents<->render import cycle

    shape, facts, dims = schema_shape(model)

    summary = (
        f"The model is {shape}. It has {pluralize_count('fact table', len(facts))} "
        f"({', '.join(facts) or 'none'}) and {pluralize_count('dimension table', len(dims))} "
        f"({', '.join(dims) or 'none'}), connected by "
        f"{pluralize_count('relationship', len(model.relationships))}."
    )

    risks: list[str] = []
    for r in model.relationships:
        if r.cross_filter == "both":
            risks.append(
                f"{r.from_table} ↔ {r.to_table} uses bi-directional cross-filtering, "
                f"which can create ambiguous filter paths and slow performance."
            )
        if not r.is_active:
            risks.append(
                f"{r.from_table}[{r.from_column}] → {r.to_table}[{r.to_column}] is "
                f"inactive and only applies via USERELATIONSHIP() in DAX."
            )
    related = {r.from_table for r in model.relationships} | {r.to_table for r in model.relationships}
    for t in model.tables:
        if t.kind in ("fact", "dimension") and t.name not in related:
            risks.append(f"'{t.name}' has no relationships and is disconnected from the model.")

    return summary, risks


def relationship_lines(model: SemanticModel) -> list[str]:
    lines = []
    for r in model.relationships:
        direction = "single-direction" if r.cross_filter == "single" else "bi-directional"
        state = "active" if r.is_active else "INACTIVE"
        lines.append(
            f"{r.from_table}[{r.from_column}] → {r.to_table}[{r.to_column}] — "
            f"{r.from_cardinality}-to-{r.to_cardinality}, {direction}, {state}"
        )
    return lines
