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
from typing import Optional

from ..schemas.model import SemanticModel
from .deterministic import translate_dax

# decorative elements that aren't worth a documentation row when they carry no data
DECORATIVE = {"image", "shape", "basicShape", "textbox", "actionButton", "visualGroup"}

# Reader-facing name for a Power BI internal ``visualType`` string — the one
# place this mapping lives (previously duplicated, slightly differently, in
# render/_wireframe.py too); every renderer that shows a visual type name
# (data dictionary/visual tables, the page-wireframe SVG) imports it from
# here so a camelCase internal name never leaks into a rendered document,
# e.g. "lineStackedColumnComboChart"/"decompositionTree"/"stackedAreaChart"
# from the wireframe v2 bug report (J.C).
FRIENDLY_VISUAL = {
    "card": "Card", "multiRowCard": "Multi-row card", "kpi": "KPI",
    "clusteredColumnChart": "Column chart", "columnChart": "Column chart",
    "hundredPercentStackedColumnChart": "100% stacked column chart",
    "stackedColumnChart": "Stacked column chart",
    "clusteredBarChart": "Bar chart", "barChart": "Bar chart",
    "hundredPercentStackedBarChart": "100% stacked bar chart",
    "stackedBarChart": "Stacked bar chart",
    "lineChart": "Line chart", "lineStackedColumnComboChart": "Combo chart",
    "lineClusteredColumnComboChart": "Combo chart",
    "areaChart": "Area chart", "stackedAreaChart": "Area chart",
    "pieChart": "Pie chart", "donutChart": "Donut chart",
    "tableEx": "Table", "table": "Table", "pivotTable": "Matrix", "matrix": "Matrix",
    "treemap": "Treemap", "map": "Map", "filledMap": "Map", "shapeMap": "Map",
    "gauge": "Gauge", "scatterChart": "Scatter chart", "funnel": "Funnel",
    "waterfallChart": "Waterfall chart", "ribbonChart": "Ribbon chart",
    "decompositionTreeVisual": "Decomposition tree", "decompositionTree": "Decomposition tree",
    "keyInfluencersVisual": "Key influencers", "qnaVisual": "Q&A",
    "slicer": "Slicer", "advancedSlicerVisual": "Slicer",
    "image": "Image", "shape": "Shape", "basicShape": "Shape",
    "textbox": "Text box", "actionButton": "Button", "visualGroup": "Group",
}


def friendly_visual_type(raw_type: str | None) -> str:
    """The reader-facing name for ``raw_type`` — falls back to the raw
    string itself for an unrecognized type (never blank, never crashes on
    ``None``)."""
    if not raw_type:
        return "Visual"
    return FRIENDLY_VISUAL.get(raw_type, raw_type)


_FIELD_PARAM_NAME_RE = re.compile(r"^(select\d*|range\d*|field\s*parameter\d*|fields?)$", re.IGNORECASE)


def field_parameter_table_names(model: SemanticModel) -> set[str]:
    """Recognize Power BI field parameters and similar disconnected helper
    tables (I4): a calculated table, never joined to the model via any
    relationship, that exists only to drive a slicer or chart axis — not a
    real dimension. Heuristic: not related to anything, ``is_calculated``,
    and either a handful of columns or a telltale name ('select', 'select1',
    'Range', 'Field Parameter', ...). Left unrecognized, these leak into
    generated business questions ("How is Actual distributed by select?")
    and get documented as if they were real report data."""
    related = {r.from_table for r in model.relationships} | {r.to_table for r in model.relationships}
    names = set()
    for t in model.tables:
        if t.name in related or not t.is_calculated:
            continue
        if len(t.columns) <= 3 or _FIELD_PARAM_NAME_RE.match(t.name.strip()):
            names.add(t.name)
    return names


def named_field_parameter_table_names(model: SemanticModel) -> set[str]:
    """The strict subset of :func:`field_parameter_table_names`: a
    disconnected table whose *name itself* is a telltale field-parameter
    pattern ('select', 'select1', 'Range', 'Field Parameter', ...) — not
    the broader "<=3 columns" fallback, which also matches a legitimate,
    real part of the model (a single-column, no-relationship "Key
    Measures"/measure-home table is exactly that shape too). Also not
    gated on ``is_calculated`` (unlike the broader function): a real field
    parameter can be a plain M-query/web-sourced table rather than a DAX
    calculated one — a disconnected table literally named "Range" is a
    parameter table regardless of how it was built. Used where
    misclassifying a real table would be visibly wrong to a reader (V2:
    the model diagram must never draw a field-parameter node, but must
    still draw every genuine table, including a measure-home one)."""
    related = {r.from_table for r in model.relationships} | {r.to_table for r in model.relationships}
    return {
        t.name for t in model.tables
        if t.name not in related and _FIELD_PARAM_NAME_RE.match(t.name.strip())
    }


# The reader-facing label substituted for a raw field-parameter reference
# wherever one has to remain visible (e.g. a slicer bound directly to it —
# a real, working control the reader needs to know about).
FIELD_SELECTOR_LABEL = "field selector"


def is_field_selector(field: str, field_param_tables: set[str]) -> bool:
    """True if ``field`` (one entry of a ``Visual.fields`` list) refers into
    a field-parameter table (I4) rather than a real dimension/measure.

    Handles two shapes a Power BI report layout produces:

    - Fully qualified, ``"Table.Column"`` — the normal case, resolved via
      ``field_param_tables`` (from :func:`field_parameter_table_names`).
    - Bare, e.g. ``"select"`` — Power BI's ``queryRef`` for a field-parameter
      projection is sometimes just the parameter table's own name with no
      ``Entity.Property`` qualification at all (see
      ``parsers/pbir.py::_extract_fields``'s ``queryRef`` fallback, which
      never reaches the qualified path for these). Since there is no ``.``
      to strip a table name from, a bare token is recognized either by
      matching a table this model *did* resolve, or — when the table itself
      wasn't captured in ``model.tables`` at all, which happens for some
      field-parameter tables — by the same telltale-name heuristic
      (``select``/``select1``/``Range``/``Field Parameter``/...) used to
      recognize the table in the first place.

    Regression (I4 D4): fields shaped this way used to leak past the
    qualified-only check into visual titles, generated business questions,
    and the technical doc's inferred glossary.
    """
    parts = field.split(".")
    if len(parts) > 1:
        return parts[0] in field_param_tables
    return field in field_param_tables or bool(_FIELD_PARAM_NAME_RE.match(field.strip()))


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
    from ..render._shared import anchor_slug, dedupe_ids
    from ..render._wireframe import render_wireframe
    measure_names = {m.name for m in model.all_measures()}
    field_param_tables = field_parameter_table_names(model)
    out = []
    for p in model.pages:
        visuals_raw, decorative = [], 0
        for v in p.visuals:
            if v.is_slicer:
                continue
            metrics, dims = [], []
            for f in v.fields:
                if is_field_selector(f, field_param_tables):
                    continue  # field selector, not a real dimension (I4)
                leaf = f.split(".")[-1]
                (metrics if leaf in measure_names else dims).append(leaf)
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
                # An untitled visual's label already fell back to its bare
                # type name (visual_label()'s last resort) — appending
                # "— {type}" again would double it up ("Matrix — Matrix ×2"
                # / "Bar chart — Bar chart ×2") instead of adding information.
                vis["label"] = (
                    f"{vis['label']} ×{vis['count']}" if vis["label"] == vis["type"]
                    else f"{vis['label']} — {vis['type']} ×{vis['count']}"
                )
            visuals.append(vis)

        # Resolve each group's collision-safe anchor slug in the exact same
        # order/dedup convention html.py and user_guide.py apply to these
        # same labels for their table row ids (I3) — a visual whose label
        # got the "— Type ×N" grouping suffix above, or whose slug collides
        # with another row's, needs the wireframe's own <a href> to land on
        # that *resolved* id, not an independently recomputed, unresolved
        # one. Both renderers dedupe over this identical ``visuals`` list in
        # this identical order, so computing it once here and handing it to
        # ``render_wireframe`` keeps the SVG and both tables permanently in
        # sync instead of three independent computations that can drift.
        visual_anchor_map = dict(zip(order, dedupe_ids([anchor_slug(v["label"]) for v in visuals])))

        wireframe_svg = render_wireframe(
            p, measure_names=frozenset(measure_names), field_param_tables=frozenset(field_param_tables),
            visual_anchor_map=visual_anchor_map,
            # Visible page names, in report order — the wireframe's page-tab
            # bar (active page + linked sibling tabs, same ``#page-…`` anchor
            # formula as the slicer links). Hidden pages are excluded the way
            # Power BI's own tab strip hides them — and because the user
            # guide (which embeds this same SVG) doesn't document them, a
            # hidden-page tab would be a dead link there (I3).
            sibling_pages=[pg.display_name for pg in model.pages if not pg.is_hidden],
        ) or None
        out.append({"name": p.display_name, "hidden": p.is_hidden, "drillthrough": p.is_drillthrough,
                    "visual_count": len(p.visuals), "visuals": visuals, "decorative_count": decorative,
                    "wireframe_svg": wireframe_svg})
    return out


def slicers(model: SemanticModel) -> list[dict]:
    """One row per distinct (page, field) slicer. Multiple slicer visuals
    bound to the same field on the same page collapse into a single row —
    ``count`` tells the caller how many, so callers can note the multiplicity
    ("Type (2 slicers)") instead of repeating the same filter bullet twice.

    A slicer bound to a field-parameter table (I4) is a real, working
    control the reader needs to know about — unlike a chart axis/legend it
    isn't dropped — but its raw internal name (``select``/``select1``) is
    relabeled to :data:`FIELD_SELECTOR_LABEL` rather than leaked verbatim."""
    field_param_tables = field_parameter_table_names(model)
    counts: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    for p in model.pages:
        for v in p.visuals:
            if not v.is_slicer:
                continue
            raw_field = v.fields[0] if v.fields else (v.title or "—")
            field = FIELD_SELECTOR_LABEL if v.fields and is_field_selector(raw_field, field_param_tables) else raw_field
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


_WORD_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def name_word_tokens(name: str) -> set[str]:
    """Split a field/column name (``camelCase``, ``PascalCase``, ``snake_case``,
    or space-separated) into lowercase whole words.

    Used instead of a raw ``keyword in name.lower()`` substring check, which
    false-positives whenever a keyword happens to appear as a fragment of a
    longer word -- e.g. ``"city"`` is a substring of ``"Ethnicity"``, so a
    naive check misclassifies an Ethnicity column as a geography field."""
    return {tok.lower() for tok in _WORD_TOKEN_RE.findall(name)}


def has_keyword_token(name: str, keywords) -> bool:
    """True if any of ``keywords`` matches a whole word of ``name`` (see
    :func:`name_word_tokens`) rather than merely a substring of it."""
    return bool(name_word_tokens(name) & set(keywords))


# A leading ordered- or unordered-list marker ("1. ", "12) ", "- ", "* ",
# "• ") on a human-glossary line -- see parse_human_glossary.
_GLOSSARY_LIST_MARKER_RE = re.compile(r"^(?:[-*•]|\d+[.)])\s+")


def parse_human_glossary(text: Optional[str]) -> dict[str, str]:
    """Parse the intake form's free-text "Glossary of Key Business Terms"
    field into ``{term: definition}`` (Day 3).

    One term per line, ``Term: Definition`` — the same convention already
    used for the rendered Document Control glossary. A line with no colon
    can't be attributed to a term, so it's skipped rather than guessed at;
    the caller's merge only ever *adds to or overrides* the deterministic
    glossary, it never silently drops a human line — an unparsed line still
    means the whole ``glossary`` blob renders verbatim wherever the field
    itself is shown (§14's own paragraph), just not merged term-by-term."""
    entries: dict[str, str] = {}
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        # Users often paste this free-text box as a formatted list --
        # markdown-bolded "**Term:** definition", or a numbered/bulleted
        # list ("1. Term: definition", "- Term: definition"). That
        # decoration is authoring style, not part of the term itself --
        # left in, "1. EmpCount" or "**EmpCount" no longer matches the
        # "EmpCount" measure's existing glossary entry, so the merge below
        # creates a second, decorated-prefix duplicate row instead of
        # overriding the original.
        line = line.replace("**", "")
        line = _GLOSSARY_LIST_MARKER_RE.sub("", line)
        if ":" not in line:
            continue
        term, _, definition = line.partition(":")
        term, definition = term.strip(), definition.strip()
        if term and definition:
            entries[term] = definition
    return entries


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


_FRIENDLY_SOURCE_TYPE = {
    "sql.database": "SQL database", "sql.databases": "SQL database",
    "postgresql.database": "PostgreSQL database", "mysql.database": "MySQL database",
    "oracle.database": "Oracle database", "snowflake.databases": "Snowflake database",
    "amazonredshift.database": "Amazon Redshift database",
    "googlebigquery.database": "Google BigQuery database",
    "databricks.catalogs": "Databricks catalog",
    "excel.workbook": "Excel workbook",
    "web.contents": "Web/API source", "odata.feed": "OData feed",
    "sharepoint.contents": "SharePoint source", "sharepoint.tables": "SharePoint source",
    "sharepoint.files": "SharePoint source",
    "odbc.datasource": "ODBC source", "folder.contents": "folder source",
    "folder.files": "folder source",
    "csv.document": "CSV file", "json.document": "JSON file",
    "azuredatalake.contents": "Azure Data Lake source", "azurestorage.blobs": "Azure Storage source",
}

# ``File.Contents``/``Folder.Files`` name the *mechanism* Power Query used to
# read a file, not what kind of file it is — the extension on ``ds.detail``
# is the only signal that distinguishes "1 Excel workbook" from "1 CSV file"
# for those two connectors.
_FRIENDLY_EXTENSION = {
    ".xlsx": "Excel workbook", ".xls": "Excel workbook", ".xlsm": "Excel workbook",
    ".csv": "CSV file", ".tsv": "CSV file", ".json": "JSON file",
    ".pbix": "Power BI file", ".txt": "text file", ".pdf": "PDF file",
    ".xml": "XML file", ".parquet": "Parquet file",
}
_FILE_LIKE_TYPES = {"file.contents", "folder.files"}


def _basename(path_str: str | None) -> str:
    """Bare filename from a path/URL string, stripping every directory
    component — the exec doc may show *which* file (G.1's own worked
    example: "1 Excel workbook — Data.xlsx"), but never the directory it
    lives in (that's a local-path finding, not a data-source summary)."""
    if not path_str:
        return ""
    return re.split(r"[\\/]+", path_str.strip())[-1]


def _friendly_source_type(raw_type: str | None, detail: str | None = None) -> str:
    if not raw_type:
        return "data source"
    key = raw_type.lower()
    friendly = _FRIENDLY_SOURCE_TYPE.get(key)
    if friendly:
        return friendly
    if key in _FILE_LIKE_TYPES:
        ext = "." + _basename(detail).rsplit(".", 1)[-1].lower() if "." in _basename(detail) else ""
        friendly_ext = _FRIENDLY_EXTENSION.get(ext)
        if friendly_ext:
            return friendly_ext
        return "file source"
    # Last resort: a generic, honest label — never the raw internal
    # connector name (e.g. never literally "File.Contents").
    return "data source"


def data_source_type_counts(model: SemanticModel) -> list[str]:
    """Executive-safe data source summary (G.1): counts by source *type*
    only — e.g. "3 Excel workbooks" — never a path, server, or database
    name, with one exception: when there's exactly one source of a given
    type, its bare filename is appended ("1 Excel workbook — Data.xlsx")
    since a single named file is what a reader actually asks "which file?"
    about, and a filename alone (no directory) carries none of the
    machine-specific path risk the hardcoded-path finding exists to catch."""
    from collections import defaultdict

    from ..render._shared import pluralize  # lazy: avoids the agents<->render import cycle

    by_type: dict[str, list] = defaultdict(list)
    for ds in model.data_sources:
        by_type[ds.type].append(ds)
    ordered = sorted(by_type.items(), key=lambda kv: -len(kv[1]))

    lines = []
    for raw_type, sources in ordered:
        n = len(sources)
        friendly = _friendly_source_type(raw_type, sources[0].detail if n == 1 else None)
        line = f"{n} {pluralize(friendly, n)}"
        if n == 1 and (raw_type or "").lower() in _FILE_LIKE_TYPES | {"excel.workbook", "csv.document", "json.document"}:
            fname = _basename(sources[0].detail)
            if fname and "." in fname:
                line += f" — {fname}"
        lines.append(line)
    return lines


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
