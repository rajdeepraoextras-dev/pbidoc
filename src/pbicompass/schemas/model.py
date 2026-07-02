"""The canonical ``model.json`` contract.

This is the normalised, metadata-only representation of a Power BI file that
every parser produces and every downstream AI agent consumes. It contains
**no row-level business data** — only schema, DAX, M, relationships, RLS, and
report layout structure.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

# Controlled vocabularies (kept as plain strings so JSON stays clean).
Cardinality = Literal["one", "many", "unknown"]
CrossFilter = Literal["single", "both", "unknown"]
TableKind = Literal["fact", "dimension", "calculation", "calculation-group", "unknown"]
SourceFormat = Literal["pbip-tmdl", "pbip-tmsl", "pbix", "unknown"]


@dataclass
class Column:
    name: str
    data_type: str = "unknown"
    is_hidden: bool = False
    summarize_by: Optional[str] = None
    description: Optional[str] = None
    format_string: Optional[str] = None
    display_folder: Optional[str] = None
    data_category: Optional[str] = None
    sort_by_column: Optional[str] = None
    is_key: bool = False
    # Calculated columns carry a DAX expression.
    is_calculated: bool = False
    expression: Optional[str] = None


@dataclass
class Measure:
    name: str
    expression: str = ""
    table: Optional[str] = None  # home table
    format_string: Optional[str] = None
    display_folder: Optional[str] = None
    description: Optional[str] = None
    is_hidden: bool = False


@dataclass
class Partition:
    """A table's data source definition — the basis for §III Lineage."""
    name: str
    source_kind: Optional[str] = None  # m | calculated | entity | query
    mode: Optional[str] = None         # import | directQuery | dual
    expression: Optional[str] = None   # the M / DAX / entity source text


@dataclass
class Table:
    name: str
    is_hidden: bool = False
    description: Optional[str] = None
    kind: TableKind = "unknown"  # heuristic; refined by the Data Modeler agent
    is_calculated: bool = False
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)


@dataclass
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    name: Optional[str] = None
    from_cardinality: Cardinality = "many"
    to_cardinality: Cardinality = "one"
    cross_filter: CrossFilter = "single"
    is_active: bool = True


@dataclass
class TablePermission:
    """A single RLS row filter on a table within a role."""
    table: str
    filter_expression: str


@dataclass
class Role:
    name: str
    model_permission: Optional[str] = None  # e.g. "read"
    table_permissions: list[TablePermission] = field(default_factory=list)
    members: list[str] = field(default_factory=list)


@dataclass
class MExpression:
    """A shared Power Query expression or parameter (§III Lineage)."""
    name: str
    kind: Literal["expression", "parameter"] = "expression"
    expression: Optional[str] = None


@dataclass
class DataSource:
    """An inferred source system (parsed from M), credentials stripped."""
    type: Optional[str] = None     # e.g. "Sql.Database", "Web.Contents"
    server: Optional[str] = None
    database: Optional[str] = None
    detail: Optional[str] = None   # url / path / other first argument


@dataclass
class Visual:
    id: str
    type: str = "unknown"          # normalised visualType
    title: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    fields: list[str] = field(default_factory=list)  # "Table.Field" references
    is_slicer: bool = False


@dataclass
class Page:
    id: str
    display_name: str
    ordinal: Optional[int] = None
    is_hidden: bool = False
    is_drillthrough: bool = False
    width: Optional[float] = None
    height: Optional[float] = None
    visuals: list[Visual] = field(default_factory=list)


@dataclass
class ModelMeta:
    parser_version: str = "0.1.0"
    source_format: SourceFormat = "unknown"
    source_path: Optional[str] = None
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class SemanticModel:
    """Top-level ``model.json`` object."""
    report_name: str
    model_name: Optional[str] = None
    tables: list[Table] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    roles: list[Role] = field(default_factory=list)
    expressions: list[MExpression] = field(default_factory=list)
    data_sources: list[DataSource] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    meta: ModelMeta = field(default_factory=ModelMeta)

    # -- convenience -----------------------------------------------------
    def all_measures(self) -> list[Measure]:
        return [m for t in self.tables for m in t.measures]

    def compute_counts(self) -> None:
        self.meta.counts = {
            "tables": len(self.tables),
            "columns": sum(len(t.columns) for t in self.tables),
            "measures": len(self.all_measures()),
            "relationships": len(self.relationships),
            "roles": len(self.roles),
            "pages": len(self.pages),
            "visuals": sum(len(p.visuals) for p in self.pages),
            "data_sources": len(self.data_sources),
        }

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
