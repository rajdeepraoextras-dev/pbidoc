"""TMDL parser — the modern ``.pbip`` semantic-model definition format.

Parses ``*.SemanticModel/definition/**/*.tmdl`` into the canonical
``SemanticModel`` building blocks: tables (+ columns, measures, partitions),
relationships, RLS roles, and shared M expressions.

Pragmatic v0: covers the common ~95% of real exports; anything unrecognised is
recorded as a warning, never raised.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from ..schemas.model import (
    Column,
    Measure,
    MExpression,
    Partition,
    Relationship,
    Role,
    Table,
    TablePermission,
)
from .base import (
    Line,
    block_body,
    capture_expression,
    parse_decl,
    props_at_level,
    split_prop,
    to_bool,
    tokenize,
    unquote,
)

TABLE_PROPS = {
    "isHidden", "description", "lineageTag", "annotation", "changedProperty",
    "dataCategory", "showAsVariationsOnly", "isPrivate", "sourceLineageTag",
    "extendedProperty",
}
COLUMN_PROPS = {
    "dataType", "summarizeBy", "sourceColumn", "isHidden", "lineageTag",
    "formatString", "displayFolder", "dataCategory", "sortByColumn", "isKey",
    "isNullable", "isAvailableInMdx", "description", "annotation",
    "changedProperty", "encoding", "type", "sourceLineageTag",
    "relatedColumnDetails", "extendedProperty",
}
MEASURE_PROPS = {
    "formatString", "displayFolder", "description", "isHidden", "lineageTag",
    "annotation", "changedProperty", "formatStringDefinition",
    "detailRowsDefinition", "dataType", "isSimpleMeasure", "kpi",
    "sourceLineageTag", "extendedProperty", "relatedColumnDetails",
}
PARTITION_PROPS = {
    "mode", "description", "annotation", "queryGroup", "lineageTag",
    "changedProperty", "dataView", "sourceLineageTag",
}
EXPRESSION_PROPS = {
    "mode", "lineageTag", "queryGroup", "annotation", "changedProperty",
    "description", "sourceLineageTag",
}


def _first_token(text: str) -> str:
    return text.split(None, 1)[0] if text else ""


def _iter_children(body: list[Line], parent_indent: int) -> Iterator[tuple[Line, list[Line]]]:
    """Yield (header_line, sub_body) for each object at ``parent_indent + 1``."""
    level = parent_indent + 1
    i, n = 0, len(body)
    while i < n:
        ln = body[i]
        if ln.indent == level:
            j = i + 1
            sub: list[Line] = []
            while j < n and body[j].indent > level:
                sub.append(body[j])
                j += 1
            yield ln, sub
            i = j
        else:
            i += 1


def _strip_code_fence(expr: str) -> str:
    """Remove the ``` ``` ``` fences TMDL uses to wrap multi-line expressions."""
    s = expr.strip()
    if s.startswith("```"):
        s = s[3:].lstrip("\n")
        end = s.rfind("```")
        if end != -1:
            s = s[:end]
    return s.strip()


def _combine_expr(inline: Optional[str], sub: list[Line], obj_indent: int, props: set) -> str:
    parts: list[str] = []
    if inline:
        parts.append(inline)
    captured = capture_expression(sub, obj_indent, props)
    if captured:
        parts.append(captured)
    expr = _strip_code_fence("\n".join(parts).strip())
    # tidy common leading indentation left over from the TMDL block
    import textwrap
    return textwrap.dedent(expr.replace("\t", "    ")).strip()


def split_ref(value: str) -> tuple[str, str]:
    """Split a ``Table.Column`` reference, honouring single-quoted identifiers."""
    value = value.strip()
    if value.startswith("'"):
        i, buf = 1, []
        while i < len(value):
            ch = value[i]
            if ch == "'":
                if i + 1 < len(value) and value[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                i += 1
                break
            buf.append(ch)
            i += 1
        table = "".join(buf)
        rest = value[i:].lstrip(".")
        return table, unquote(rest)
    if "." in value:
        table, _, column = value.partition(".")
        return table.strip(), unquote(column)
    return value, ""


# -- object parsers -----------------------------------------------------------
def _parse_column(header: Line, sub: list[Line]) -> Column:
    name, inline = parse_decl(header.text, "column")
    props = props_at_level(sub, header.indent + 1)
    col = Column(
        name=unquote(name),
        data_type=props.get("dataType", "unknown"),
        is_hidden=to_bool(props.get("isHidden")) or "isHidden" in props,
        summarize_by=props.get("summarizeBy"),
        description=props.get("description"),
        format_string=props.get("formatString"),
        display_folder=props.get("displayFolder"),
        data_category=props.get("dataCategory"),
        sort_by_column=props.get("sortByColumn"),
        is_key=to_bool(props.get("isKey")) or "isKey" in props,
    )
    if inline is not None:  # presence of '=' => calculated column
        expr = _combine_expr(inline, sub, header.indent, COLUMN_PROPS)
        if expr:
            col.is_calculated = True
            col.expression = expr
    return col


def _parse_measure(header: Line, sub: list[Line]) -> Measure:
    name, inline = parse_decl(header.text, "measure")
    props = props_at_level(sub, header.indent + 1)
    return Measure(
        name=unquote(name),
        expression=_combine_expr(inline, sub, header.indent, MEASURE_PROPS),
        format_string=props.get("formatString"),
        display_folder=props.get("displayFolder"),
        description=props.get("description"),
        is_hidden=to_bool(props.get("isHidden")) or "isHidden" in props,
    )


def _parse_partition(header: Line, sub: list[Line]) -> Partition:
    name, source_kind = parse_decl(header.text, "partition")
    props = props_at_level(sub, header.indent + 1)
    part = Partition(
        name=unquote(name),
        source_kind=(source_kind or None),
        mode=props.get("mode"),
    )
    # The actual source sits in a `source = ...` sub-object.
    src_level = header.indent + 1
    for i, ln in enumerate(sub):
        if ln.indent == src_level and _first_token(ln.text) == "source":
            _, inline = parse_decl(ln.text, "source")
            inner = [x for x in sub[i + 1:] if x.indent > src_level]
            # stop at the next sibling at src_level
            stop = next((k for k, x in enumerate(sub[i + 1:]) if x.indent <= src_level), None)
            if stop is not None:
                inner = sub[i + 1:i + 1 + stop]
            part.expression = _combine_expr(inline, inner, src_level, set())
            break
    return part


def _parse_table(header: Line, body: list[Line], warnings: list[str]) -> Table:
    name, _ = parse_decl(header.text, "table")
    props = props_at_level(body, header.indent + 1)
    table = Table(
        name=unquote(name),
        is_hidden=to_bool(props.get("isHidden")) or "isHidden" in props,
        description=props.get("description"),
    )
    for child, sub in _iter_children(body, header.indent):
        kw = _first_token(child.text)
        try:
            if kw == "column":
                table.columns.append(_parse_column(child, sub))
            elif kw == "measure":
                measure = _parse_measure(child, sub)
                measure.table = table.name
                table.measures.append(measure)
            elif kw == "partition":
                part = _parse_partition(child, sub)
                table.partitions.append(part)
                if part.source_kind == "calculated":
                    table.is_calculated = True
                    table.kind = "calculation"
            elif kw == "calculationGroup":
                table.kind = "calculation-group"
        except Exception as exc:  # defensive: never abort a whole table
            warnings.append(f"table '{table.name}': failed to parse {kw}: {exc}")
    return table


def _parse_relationship(header: Line, body: list[Line]) -> Relationship:
    name, _ = parse_decl(header.text, "relationship")
    props = props_at_level(body, header.indent + 1)
    from_t, from_c = split_ref(props.get("fromColumn", "."))
    to_t, to_c = split_ref(props.get("toColumn", "."))
    cfb = props.get("crossFilteringBehavior", "oneDirection")
    return Relationship(
        from_table=from_t, from_column=from_c,
        to_table=to_t, to_column=to_c,
        name=unquote(name) or None,
        from_cardinality=props.get("fromCardinality", "many"),  # default M:1
        to_cardinality=props.get("toCardinality", "one"),
        cross_filter="both" if cfb == "bothDirections" else "single",
        is_active=to_bool(props.get("isActive"), default=True),
    )


def _parse_role(header: Line, body: list[Line]) -> Role:
    name, _ = parse_decl(header.text, "role")
    role = Role(name=unquote(name))
    for child, sub in _iter_children(body, header.indent):
        kw = _first_token(child.text).rstrip(":")
        if kw == "modelPermission":
            _, role.model_permission = split_prop(child.text)
        elif kw == "tablePermission":
            tbl, inline = parse_decl(child.text, "tablePermission")
            expr = _combine_expr(inline, sub, child.indent, set())
            role.table_permissions.append(
                TablePermission(table=unquote(tbl), filter_expression=expr)
            )
    # members can appear at any depth and in two shapes — flat scan catches both
    for ln in body:
        if _first_token(ln.text) == "member" and ln.text.strip() != "member":
            role.members.append(unquote(ln.text[len("member"):].strip()))
    return role


def _parse_expression(header: Line, body: list[Line]) -> MExpression:
    name, inline = parse_decl(header.text, "expression")
    # parameters carry `... meta [IsParameterQuery=true, ...]`
    is_param = "IsParameterQuery=true" in header.text or any(
        "IsParameterQuery=true" in ln.text for ln in body
    )
    if inline and " meta [" in inline:
        inline = inline.split(" meta [", 1)[0].strip()
    return MExpression(
        name=unquote(name),
        kind="parameter" if is_param else "expression",
        expression=_combine_expr(inline, body, header.indent, EXPRESSION_PROPS) or None,
    )


def parse_tmdl_text(text: str, agg: dict, warnings: list[str]) -> None:
    """Parse one TMDL document, appending objects into ``agg``."""
    lines = tokenize(text)
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.indent != 0:
            i += 1
            continue
        body, nxt = block_body(lines, i)
        kw = _first_token(ln.text)
        try:
            if kw == "table":
                agg["tables"].append(_parse_table(ln, body, warnings))
            elif kw == "relationship":
                agg["relationships"].append(_parse_relationship(ln, body))
            elif kw == "role":
                agg["roles"].append(_parse_role(ln, body))
            elif kw == "expression":
                agg["expressions"].append(_parse_expression(ln, body))
            elif kw in ("database", "model"):
                rest = ln.text[len(kw):].strip()
                if rest and not agg.get("model_name"):
                    agg["model_name"] = unquote(rest.split(" meta ")[0])
        except Exception as exc:
            warnings.append(f"failed to parse top-level '{kw}': {exc}")
        i = nxt


def parse_semantic_model_tmdl(definition_dir: Path, warnings: list[str]) -> dict:
    """Parse every ``*.tmdl`` under a SemanticModel ``definition/`` folder."""
    agg: dict = {
        "tables": [], "relationships": [], "roles": [],
        "expressions": [], "model_name": None,
    }
    for path in sorted(definition_dir.rglob("*.tmdl")):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            warnings.append(f"could not read {path.name}: {exc}")
            continue
        parse_tmdl_text(text, agg, warnings)
    return agg
