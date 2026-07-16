"""TMSL parser — the JSON ``model.bim`` form of a semantic model.

Used for older ``.pbip`` projects and for models extracted from ``.pbix`` by
tools such as pbi-tools. TMSL is plain JSON, so this is a straightforward map
onto the canonical schema. Only the model *definition* is read — never any
VertiPaq partition data.
"""

from __future__ import annotations

from typing import Any

from ..schemas.model import (
    CalculationItem,
    Column,
    Culture,
    Hierarchy,
    HierarchyLevel,
    Measure,
    MeasureKPI,
    MExpression,
    Partition,
    Perspective,
    RefreshPolicy,
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


def _parse_kpi(obj: dict) -> MeasureKPI | None:
    kpi = MeasureKPI(
        target_expression=_expr(obj.get("targetExpression")) or None,
        target_format_string=obj.get("targetFormatString"),
        status_expression=_expr(obj.get("statusExpression")) or None,
        status_graphic=obj.get("statusGraphic"),
        trend_expression=_expr(obj.get("trendExpression")) or None,
    )
    if any((kpi.target_expression, kpi.status_expression, kpi.trend_expression,
            kpi.status_graphic, kpi.target_format_string)):
        return kpi
    return None


def _parse_measure(obj: dict, table: str) -> Measure:
    kpi_obj = obj.get("kpi")
    fmt_def = obj.get("formatStringDefinition")
    if isinstance(fmt_def, dict):
        fmt_def = _expr(fmt_def.get("expression")) or None
    elif fmt_def is not None:
        fmt_def = _expr(fmt_def) or None
    return Measure(
        name=obj.get("name", ""),
        expression=_expr(obj.get("expression")),
        table=table,
        format_string=obj.get("formatString"),
        display_folder=obj.get("displayFolder"),
        description=_expr(obj.get("description")) or None,
        is_hidden=bool(obj.get("isHidden", False)),
        kpi=_parse_kpi(kpi_obj) if isinstance(kpi_obj, dict) else None,
        format_string_expression=fmt_def,
    )


def _parse_refresh_policy(obj: dict) -> RefreshPolicy:
    def _int(key):
        v = obj.get(key)
        return int(v) if isinstance(v, (int, float)) else None
    return RefreshPolicy(
        policy_type=obj.get("policyType"),
        mode=obj.get("mode"),
        rolling_window_granularity=obj.get("rollingWindowGranularity"),
        rolling_window_periods=_int("rollingWindowPeriods"),
        incremental_granularity=obj.get("incrementalGranularity"),
        incremental_periods=_int("incrementalPeriods"),
        source_expression=_expr(obj.get("sourceExpression")) or None,
        polling_expression=_expr(obj.get("pollingExpression")) or None,
    )


def _parse_partition(obj: dict) -> Partition:
    source = obj.get("source", {}) or {}
    return Partition(
        name=obj.get("name", ""),
        source_kind=source.get("type"),
        mode=obj.get("mode") or source.get("mode"),
        expression=_expr(source.get("expression")) or None,
    )


def _parse_calculation_item(obj: dict) -> CalculationItem:
    fmt = obj.get("formatStringDefinition")
    if isinstance(fmt, dict):
        fmt = _expr(fmt.get("expression")) or None
    elif fmt is not None:
        fmt = _expr(fmt) or None
    return CalculationItem(
        name=obj.get("name", ""),
        expression=_expr(obj.get("expression")),
        ordinal=obj.get("ordinal"),
        format_string_expression=fmt,
        description=_expr(obj.get("description")) or None,
        is_hidden=bool(obj.get("isHidden", False)),
    )


def _parse_hierarchy(obj: dict) -> Hierarchy:
    hierarchy = Hierarchy(
        name=obj.get("name", ""),
        description=_expr(obj.get("description")) or None,
        is_hidden=bool(obj.get("isHidden", False)),
    )
    for i, lvl in enumerate(obj.get("levels", [])):
        hierarchy.levels.append(HierarchyLevel(
            name=lvl.get("name", ""),
            column=lvl.get("column"),
            ordinal=lvl.get("ordinal", i),
        ))
    # TMSL levels may arrive out of order; ordinal is authoritative.
    hierarchy.levels.sort(key=lambda lv: (lv.ordinal if lv.ordinal is not None else 0))
    return hierarchy


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
    for h in obj.get("hierarchies", []):
        try:
            table.hierarchies.append(_parse_hierarchy(h))
        except Exception as exc:
            warnings.append(f"table '{table.name}': hierarchy parse error: {exc}")
    rp = obj.get("refreshPolicy")
    if isinstance(rp, dict):
        try:
            table.refresh_policy = _parse_refresh_policy(rp)
        except Exception as exc:
            warnings.append(f"table '{table.name}': refreshPolicy parse error: {exc}")
    cg = obj.get("calculationGroup")
    if cg is not None:
        table.kind = "calculation-group"
        if isinstance(cg, dict):
            if isinstance(cg.get("precedence"), int):
                table.calculation_group_precedence = cg["precedence"]
            for ci in cg.get("calculationItems", []):
                try:
                    table.calculation_items.append(_parse_calculation_item(ci))
                except Exception as exc:
                    warnings.append(f"table '{table.name}': calculationItem parse error: {exc}")
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


def _parse_perspective(obj: dict) -> Perspective:
    persp = Perspective(name=obj.get("name", ""))
    for pt in obj.get("tables", []):
        persp.tables.append(pt.get("name", ""))
        for pm in pt.get("measures", []):
            persp.measures.append(pm.get("name", ""))
    return persp


def _count_translated_captions(node) -> int:
    """Recursively count ``translatedCaption`` keys in a TMSL culture's
    translations tree — the useful documentation signal (how many objects
    carry a translation) without materialising every string."""
    total = 0
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "translatedCaption" and v:
                total += 1
            else:
                total += _count_translated_captions(v)
    elif isinstance(node, list):
        for item in node:
            total += _count_translated_captions(item)
    return total


def _parse_culture(obj: dict) -> Culture:
    return Culture(
        name=obj.get("name", ""),
        translated_object_count=_count_translated_captions(obj.get("translations")),
    )


def parse_semantic_model_tmsl(bim: dict, warnings: list[str]) -> dict:
    """Parse a TMSL/``model.bim`` document into canonical building blocks."""
    model = bim.get("model", bim)
    agg: dict = {
        "tables": [], "relationships": [], "roles": [], "expressions": [],
        "perspectives": [], "cultures": [],
        "model_name": bim.get("name") or model.get("name"),
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
    for pv in model.get("perspectives", []):
        try:
            agg["perspectives"].append(_parse_perspective(pv))
        except Exception as exc:
            warnings.append(f"perspective parse error: {exc}")
    for cu in model.get("cultures", []):
        try:
            agg["cultures"].append(_parse_culture(cu))
        except Exception as exc:
            warnings.append(f"culture parse error: {exc}")
    return agg
