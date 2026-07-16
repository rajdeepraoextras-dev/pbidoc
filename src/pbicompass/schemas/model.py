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
TableKind = Literal["fact", "dimension", "calculation", "calculation-group", "parameter", "unknown"]
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
    cardinality: Optional[int] = None
    size_bytes: Optional[int] = None
    provenance: Optional[str] = None
    # Calculated columns carry a DAX expression.
    is_calculated: bool = False
    expression: Optional[str] = None


@dataclass
class MeasureKPI:
    """A measure's KPI definition — the target/status/trend that turns a bare
    measure into a goal-tracked indicator. Expressions are DAX, kept verbatim."""
    target_expression: Optional[str] = None
    target_format_string: Optional[str] = None
    status_expression: Optional[str] = None
    status_graphic: Optional[str] = None
    trend_expression: Optional[str] = None


@dataclass
class Measure:
    name: str
    expression: str = ""
    table: Optional[str] = None  # home table
    format_string: Optional[str] = None
    display_folder: Optional[str] = None
    description: Optional[str] = None
    is_hidden: bool = False
    provenance: Optional[str] = None
    kpi: Optional[MeasureKPI] = None
    # Dynamic format string (formatStringDefinition) — a DAX expression that
    # computes the measure's format at query time, distinct from the static
    # ``format_string``.
    format_string_expression: Optional[str] = None


@dataclass
class Partition:
    """A table's data source definition — the basis for §III Lineage."""
    name: str
    source_kind: Optional[str] = None  # m | calculated | entity | query
    mode: Optional[str] = None         # import | directQuery | dual
    expression: Optional[str] = None   # the M / DAX / entity source text


@dataclass
class CalculationItem:
    """One item inside a calculation group (e.g. ``YTD``, ``MTD``, ``PY``).

    Calculation groups are the backbone of enterprise time-intelligence: each
    item is a DAX expression applied over ``SELECTEDMEASURE()``. Parsed but
    previously dropped — the parser only tagged the host table's ``kind``.
    """
    name: str
    expression: str = ""
    ordinal: Optional[int] = None
    format_string_expression: Optional[str] = None  # formatStringDefinition (dynamic format)
    description: Optional[str] = None
    is_hidden: bool = False


@dataclass
class HierarchyLevel:
    """One level of a user-defined hierarchy, mapped to a column."""
    name: str
    column: Optional[str] = None  # the column this level surfaces
    ordinal: Optional[int] = None


@dataclass
class Hierarchy:
    """A user-defined drill hierarchy on a table (e.g. Year > Quarter > Month)."""
    name: str
    levels: list[HierarchyLevel] = field(default_factory=list)
    description: Optional[str] = None
    is_hidden: bool = False


@dataclass
class RefreshPolicy:
    """An incremental-refresh policy on a table — the actual refresh strategy
    (rolling window + incremental window), extracted from the model rather than
    only described in a human-supplied note."""
    policy_type: Optional[str] = None            # e.g. "basic"
    mode: Optional[str] = None                   # import | directQuery ...
    rolling_window_granularity: Optional[str] = None  # day | month | quarter | year
    rolling_window_periods: Optional[int] = None
    incremental_granularity: Optional[str] = None
    incremental_periods: Optional[int] = None
    source_expression: Optional[str] = None
    polling_expression: Optional[str] = None


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
    hierarchies: list[Hierarchy] = field(default_factory=list)
    # Populated only for calculation-group tables (kind == "calculation-group").
    calculation_items: list[CalculationItem] = field(default_factory=list)
    calculation_group_precedence: Optional[int] = None
    refresh_policy: Optional[RefreshPolicy] = None


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
    # Human-supplied, from the enrichment file (5.1) — who this role's
    # members actually are and what the filter logic means in business terms.
    members_description: str = ""
    filter_logic_explanation: str = ""


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
    # Human-supplied, from the enrichment file (5.1) — never inferred.
    authentication_status: Optional[str] = None


@dataclass
class FieldParameter:
    """A Power BI field parameter — a disconnected table whose rows let a user
    swap which field a visual shows. First-class here (rather than only
    heuristically flagged) so docs can list exactly which fields it exposes."""
    table: str
    fields: list[str] = field(default_factory=list)         # "Table[Column]" references
    display_names: list[str] = field(default_factory=list)  # parallel display labels


@dataclass
class Perspective:
    """A named subset (view) of the model — the tables/measures a perspective
    exposes. Common in enterprise models to tailor what a persona sees."""
    name: str
    tables: list[str] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)


@dataclass
class Culture:
    """A translation culture (language) defined on the model, with a count of
    translated captions — the useful documentation signal for multi-language
    models without dumping every translated string."""
    name: str                       # e.g. "fr-FR"
    translated_object_count: int = 0


@dataclass
class Bookmark:
    name: str
    target_page: Optional[str] = None
    state: Optional[str] = None


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
    action: Optional[dict] = None


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
    drillthrough_fields: list[str] = field(default_factory=list)


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
    overridden_fields: list[str] = field(default_factory=list)


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
    bookmarks: list[Bookmark] = field(default_factory=list)
    field_parameters: list[FieldParameter] = field(default_factory=list)
    perspectives: list[Perspective] = field(default_factory=list)
    cultures: list[Culture] = field(default_factory=list)
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
            "hierarchies": sum(len(t.hierarchies) for t in self.tables),
            "calculation_items": sum(len(t.calculation_items) for t in self.tables),
            "field_parameters": len(self.field_parameters),
            "perspectives": len(self.perspectives),
            "cultures": len(self.cultures),
        }

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    # -- round-trip ---------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SemanticModel":
        """Reconstruct a :class:`SemanticModel` from ``to_dict()``'s own
        output. Every field here is a plain dataclass of primitives/lists
        (no unions beyond the ``Literal`` string vocabularies, which JSON
        already round-trips as plain strings), so this is a direct nested
        construction rather than a generic reflection-based loader.

        Lets a real, already-parsed ``model.json`` (e.g. one saved by a
        prior CLI run, or shipped as a fixture) be reloaded as a fixture
        without re-parsing the original .pbip/.pbix — useful when only the
        rendered output/model.json of a real report is available, not its
        source project."""
        tables = [
            Table(
                name=t["name"], is_hidden=t.get("is_hidden", False),
                description=t.get("description"), kind=t.get("kind", "unknown"),
                is_calculated=t.get("is_calculated", False),
                columns=[Column(**c) for c in t.get("columns", [])],
                measures=[
                    Measure(**{k: v for k, v in m.items() if k != "kpi"},
                            kpi=MeasureKPI(**m["kpi"]) if m.get("kpi") else None)
                    for m in t.get("measures", [])
                ],
                partitions=[Partition(**p) for p in t.get("partitions", [])],
                refresh_policy=RefreshPolicy(**t["refresh_policy"]) if t.get("refresh_policy") else None,
                hierarchies=[
                    Hierarchy(
                        name=h["name"], description=h.get("description"),
                        is_hidden=h.get("is_hidden", False),
                        levels=[HierarchyLevel(**lvl) for lvl in h.get("levels", [])],
                    )
                    for h in t.get("hierarchies", [])
                ],
                calculation_items=[CalculationItem(**ci) for ci in t.get("calculation_items", [])],
                calculation_group_precedence=t.get("calculation_group_precedence"),
            )
            for t in data.get("tables", [])
        ]
        relationships = [Relationship(**r) for r in data.get("relationships", [])]
        roles = [
            Role(
                name=r["name"], model_permission=r.get("model_permission"),
                table_permissions=[TablePermission(**tp) for tp in r.get("table_permissions", [])],
                members=r.get("members", []),
                members_description=r.get("members_description", ""),
                filter_logic_explanation=r.get("filter_logic_explanation", ""),
            )
            for r in data.get("roles", [])
        ]
        expressions = [MExpression(**e) for e in data.get("expressions", [])]
        data_sources = [DataSource(**d) for d in data.get("data_sources", [])]
        pages = [
            Page(
                id=p["id"], display_name=p["display_name"], ordinal=p.get("ordinal"),
                is_hidden=p.get("is_hidden", False), is_drillthrough=p.get("is_drillthrough", False),
                width=p.get("width"), height=p.get("height"),
                visuals=[Visual(**v) for v in p.get("visuals", [])],
                drillthrough_fields=p.get("drillthrough_fields", []),
            )
            for p in data.get("pages", [])
        ]
        bookmarks = [Bookmark(**b) for b in data.get("bookmarks", [])]
        field_parameters = [FieldParameter(**fp) for fp in data.get("field_parameters", [])]
        perspectives = [Perspective(**pv) for pv in data.get("perspectives", [])]
        cultures = [Culture(**cu) for cu in data.get("cultures", [])]
        meta = ModelMeta(**data["meta"]) if data.get("meta") else ModelMeta()
        return cls(
            report_name=data["report_name"], model_name=data.get("model_name"),
            tables=tables, relationships=relationships, roles=roles,
            expressions=expressions, data_sources=data_sources, pages=pages,
            bookmarks=bookmarks, field_parameters=field_parameters,
            perspectives=perspectives, cultures=cultures, meta=meta,
        )

    @classmethod
    def from_json(cls, text: str) -> "SemanticModel":
        return cls.from_dict(json.loads(text))
