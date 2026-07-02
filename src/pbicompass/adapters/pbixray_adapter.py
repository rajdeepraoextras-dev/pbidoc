"""pbixray adapter — legacy ``.pbix`` semantic-model extraction.

``pbixray`` reads the metadata out of a ``.pbix`` file's compressed ``DataModel``
without us ever materialising row-level data (we never call ``get_table()``).

This module is split in two so the mapping logic is testable without the
dependency installed:

* :func:`load_frames_from_pbix` — the only place that imports ``pbixray``;
  turns its pandas frames into plain ``list[dict]`` records.
* :func:`build_model_from_frames` — a *pure* transform from those records onto
  the canonical schema. No third-party imports; fully unit-testable.

Note: ``pbixray`` does **not** expose RLS roles or the report layout. Roles are
flagged as unavailable for ``.pbix`` here; the report layout is read separately
from the ``.pbix`` ZIP (see ``parsers.pbip``).

Runtime requirement: ``pbixray`` currently needs Python <= 3.13 (its ``xpress9``
decompressor has no 3.14 wheel yet). The transform layer runs on any version.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from ..schemas.model import (
    Column,
    Measure,
    MExpression,
    Partition,
    Relationship,
    Table,
)


# -- value helpers ------------------------------------------------------------
def _s(value: Any) -> Optional[str]:
    """Coerce to a clean string, mapping NaN / empty to None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "1", "yes")


def _get(row: dict, *names: str, default: Any = None) -> Any:
    """Case-insensitive lookup across candidate column names (version-tolerant)."""
    lower = {k.lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return default


_TYPE_MAP = {
    "int64": "int64", "int32": "int64", "int": "int64",
    "float64": "double", "float32": "double", "double": "double",
    "object": "string", "string": "string", "str": "string",
    "datetime64[ns]": "dateTime", "datetime64": "dateTime", "datetime": "dateTime",
    "bool": "boolean", "boolean": "boolean",
    "decimal": "decimal",
}


def _map_dtype(value: Any) -> str:
    raw = (_s(value) or "unknown").lower()
    return _TYPE_MAP.get(raw, _s(value) or "unknown")


def _cross_filter(value: Any) -> str:
    """Map pbixray/TMSL crossFilteringBehavior (int or string) to our vocab."""
    if value is None:
        return "single"
    text = str(value).strip().lower()
    if text in ("2", "bothdirections", "both"):
        return "both"
    return "single"


# -- pure transform -----------------------------------------------------------
def build_model_from_frames(frames: dict[str, list[dict]], warnings: list[str]) -> dict:
    """Map pbixray-shaped records onto canonical building blocks.

    ``frames`` keys (each a ``list[dict]`` of records):
    ``schema``, ``dax_measures``, ``dax_columns``, ``dax_tables``,
    ``relationships``, ``power_query``, ``m_parameters``, ``metadata``.
    """
    tables: dict[str, Table] = {}

    def table_for(name: Optional[str]) -> Table:
        key = name or "(unknown)"
        if key not in tables:
            tables[key] = Table(name=key)
        return tables[key]

    # columns (from schema)
    for row in frames.get("schema", []):
        tname = _s(_get(row, "TableName", "Table"))
        cname = _s(_get(row, "ColumnName", "Column", "Name"))
        if not cname:
            continue
        table_for(tname).columns.append(
            Column(name=cname, data_type=_map_dtype(_get(row, "PandasDataType", "DataType", "Type")))
        )

    # calculated columns (overlay onto existing, else add)
    for row in frames.get("dax_columns", []):
        tname = _s(_get(row, "TableName", "Table"))
        cname = _s(_get(row, "ColumnName", "Column", "Name"))
        expr = _s(_get(row, "Expression", "DAX"))
        if not cname:
            continue
        table = table_for(tname)
        col = next((c for c in table.columns if c.name == cname), None)
        if col is None:
            col = Column(name=cname)
            table.columns.append(col)
        col.is_calculated = True
        col.expression = expr

    # measures
    for row in frames.get("dax_measures", []):
        tname = _s(_get(row, "TableName", "Table"))
        name = _s(_get(row, "Name", "MeasureName"))
        if not name:
            continue
        table_for(tname).measures.append(
            Measure(
                name=name,
                expression=_s(_get(row, "Expression", "DAX")) or "",
                table=tname,
                format_string=_s(_get(row, "FormatString", "Format")),
                display_folder=_s(_get(row, "DisplayFolder")),
                description=_s(_get(row, "Description")),
                is_hidden=_as_bool(_get(row, "IsHidden")),
            )
        )

    # calculated tables
    for row in frames.get("dax_tables", []):
        tname = _s(_get(row, "TableName", "Name"))
        expr = _s(_get(row, "Expression", "DAX"))
        table = table_for(tname)
        table.is_calculated = True
        table.kind = "calculation"
        table.partitions.append(Partition(name=tname or "", source_kind="calculated", expression=expr))

    # Power Query (M) partitions
    for row in frames.get("power_query", []):
        tname = _s(_get(row, "TableName", "Table"))
        expr = _s(_get(row, "Expression", "Query", "M"))
        table = table_for(tname)
        if not any(p.source_kind == "m" for p in table.partitions):
            table.partitions.append(Partition(name=tname or "", source_kind="m", expression=expr))

    # relationships
    relationships: list[Relationship] = []
    for row in frames.get("relationships", []):
        relationships.append(
            Relationship(
                from_table=_s(_get(row, "FromTableName", "FromTable")) or "",
                from_column=_s(_get(row, "FromColumnName", "FromColumn")) or "",
                to_table=_s(_get(row, "ToTableName", "ToTable")) or "",
                to_column=_s(_get(row, "ToColumnName", "ToColumn")) or "",
                name=_s(_get(row, "RelationshipName", "Name")),
                cross_filter=_cross_filter(_get(row, "CrossFilteringBehavior", "CrossFilter")),
                is_active=_as_bool(_get(row, "IsActive"), default=True),
            )
        )

    # M parameters
    expressions: list[MExpression] = []
    for row in frames.get("m_parameters", []):
        name = _s(_get(row, "ParameterName", "Name"))
        if not name:
            continue
        expressions.append(
            MExpression(
                name=name,
                kind="parameter",
                expression=_s(_get(row, "Expression", "Value", "CurrentValue")),
            )
        )

    # model name (best effort from metadata key/value rows)
    model_name = None
    for row in frames.get("metadata", []):
        key = _s(_get(row, "Name", "Key"))
        if key in ("ModelName", "Name", "Database"):
            model_name = _s(_get(row, "Value")) or model_name

    warnings.append(
        "RLS roles are not extracted from .pbix via pbixray (not exposed by the "
        "library); use a .pbip export or a TMSL/TOM extractor for role definitions."
    )

    return {
        "tables": list(tables.values()),
        "relationships": relationships,
        "roles": [],
        "expressions": expressions,
        "model_name": model_name,
    }


# -- dependency-isolated loader ----------------------------------------------
def load_frames_from_pbix(path) -> dict[str, list[dict]]:
    """Extract pbixray frames from a ``.pbix`` as plain records.

    Imports ``pbixray`` lazily so the rest of the package works without it.
    Raises ``ImportError`` if pbixray is unavailable.
    """
    from pbixray import PBIXRay  # noqa: PLC0415 (intentional lazy import)

    model = PBIXRay(str(path))

    def recs(attr: str) -> list[dict]:
        try:
            frame = getattr(model, attr, None)
        except Exception:
            return []
        if frame is None:
            return []
        if hasattr(frame, "to_dict"):
            return frame.to_dict("records")
        return list(frame)

    return {
        "schema": recs("schema"),
        "dax_measures": recs("dax_measures"),
        "dax_columns": recs("dax_columns"),
        "dax_tables": recs("dax_tables"),
        "relationships": recs("relationships"),
        "power_query": recs("power_query"),
        "m_parameters": recs("m_parameters"),
        "metadata": recs("metadata"),
    }
