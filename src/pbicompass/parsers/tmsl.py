"""TMSL parser — the JSON ``model.bim`` form of a semantic model.

Used for older ``.pbip`` projects and for models extracted from ``.pbix`` by
tools such as pbi-tools. TMSL is plain JSON, so this is a straightforward map
onto the canonical schema. Only the model *definition* is read — never any
VertiPaq partition data.
"""

from __future__ import annotations

from typing import Any

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


def _expr(value: Any) -> str:
    """TMSL stores expressions as a string or an array of lines."""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return "" if value is None else str(value)


def _parse_column(obj: dict) -> Column:
    is_calc = obj.get("type") == "calculated"
    return Column(
        name=obj.get("name", ""),
        data_type=obj.get("dataType", "unknown"),
        is_hidden=bool(obj.get("isHidden", False)),
        summarize_by=obj.get("summarizeBy"),
        description=_expr(obj.get("description")) or None,
        format_string=obj.get("formatString"),
        display_folder=obj.get("displayFolder"),
        data_category=obj.get("dataCategory"),
        sort_by_column=obj.get("sortByColumn"),
        is_key=bool(obj.get("isKey", False)),
        is_calculated=is_calc,
        expression=_expr(obj.get("expression")) if is_calc else None,
    )


def _parse_measure(obj: dict, table: str) -> Measure:
    return Measure(
        name=obj.get("name", ""),
        expression=_expr(obj.get("expression")),
        table=table,
        format_string=obj.get("formatString"),
        display_folder=obj.get("displayFolder"),
        description=_expr(obj.get("description")) or None,
        is_hidden=bool(obj.get("isHidden", False)),
    )


def _parse_partition(obj: dict) -> Partition:
    source = obj.get("source", {}) or {}
    return Partition(
        name=obj.get("name", ""),
        source_kind=source.get("type"),
        mode=obj.get("mode") or source.get("mode"),
        expression=_expr(source.get("expression")) or None,
    )


def _parse_table(obj: dict, warnings: list[str]) -> Table:
    table = Table(
        name=obj.get("name", ""),
        is_hidden=bool(obj.get("isHidden", False)),
        description=_expr(obj.get("description")) or None,
    )
    for c in obj.get("columns", []):
        try:
            table.columns.append(_parse_column(c))
        except Exception as exc:
            warnings.append(f"table '{table.name}': column parse error: {exc}")
    for m in obj.get("measures", []):
        try:
            table.measures.append(_parse_measure(m, table.name))
        except Exception as exc:
            warnings.append(f"table '{table.name}': measure parse error: {exc}")
    for p in obj.get("partitions", []):
        part = _parse_partition(p)
        table.partitions.append(part)
        if part.source_kind == "calculated":
            table.is_calculated = True
            table.kind = "calculation"
    if "calculationGroup" in obj:
        table.kind = "calculation-group"
    return table


def _parse_relationship(obj: dict) -> Relationship:
    cfb = obj.get("crossFilteringBehavior", "oneDirection")
    return Relationship(
        from_table=obj.get("fromTable", ""),
        from_column=obj.get("fromColumn", ""),
        to_table=obj.get("toTable", ""),
        to_column=obj.get("toColumn", ""),
        name=obj.get("name"),
        from_cardinality=obj.get("fromCardinality", "many"),
        to_cardinality=obj.get("toCardinality", "one"),
        cross_filter="both" if cfb == "bothDirections" else "single",
        is_active=bool(obj.get("isActive", True)),
    )


def _parse_role(obj: dict) -> Role:
    role = Role(
        name=obj.get("name", ""),
        model_permission=obj.get("modelPermission"),
    )
    for tp in obj.get("tablePermissions", []):
        role.table_permissions.append(
            TablePermission(
                table=tp.get("name") or tp.get("table", ""),
                filter_expression=_expr(tp.get("filterExpression")),
            )
        )
    for mem in obj.get("members", []):
        if isinstance(mem, dict):
            role.members.append(mem.get("memberName") or mem.get("identityProvider", ""))
        else:
            role.members.append(str(mem))
    return role


def parse_semantic_model_tmsl(bim: dict, warnings: list[str]) -> dict:
    """Parse a TMSL/``model.bim`` document into canonical building blocks."""
    model = bim.get("model", bim)
    agg: dict = {
        "tables": [], "relationships": [], "roles": [],
        "expressions": [], "model_name": bim.get("name") or model.get("name"),
    }
    for t in model.get("tables", []):
        try:
            agg["tables"].append(_parse_table(t, warnings))
        except Exception as exc:
            warnings.append(f"table parse error: {exc}")
    for r in model.get("relationships", []):
        try:
            agg["relationships"].append(_parse_relationship(r))
        except Exception as exc:
            warnings.append(f"relationship parse error: {exc}")
    for role in model.get("roles", []):
        agg["roles"].append(_parse_role(role))
    for e in model.get("expressions", []):
        annotations = e.get("annotations", []) or []
        is_param = any(
            a.get("name") == "PBI_NavigationStepName" or "Parameter" in str(a.get("value", ""))
            for a in annotations
        ) or e.get("kind") == "m" and "IsParameterQuery=true" in _expr(e.get("expression"))
        agg["expressions"].append(
            MExpression(
                name=e.get("name", ""),
                kind="parameter" if is_param else "expression",
                expression=_expr(e.get("expression")) or None,
            )
        )
    return agg
