"""The canonical ``document.json`` contract.

This is what the AI agents populate and the renderers (Markdown/PDF/DOCX)
consume. The field layout mirrors the seven enterprise sections, in order:

    I.   Document Metadata
    II.  Executive Summary & Business Guide   (Business Analyst Agent)
    III. Lineage & Architecture
    IV.  Semantic Model                       (Data Modeler Agent)
    V.   Measure Catalog                      (DAX Translator Agent)
    VI.  Security & Governance
    VII. Tech Debt / Audit                    (Auditor Agent)
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Optional


# -- II. Executive Summary & Business Guide -----------------------------------
@dataclass
class PageSummary:
    page_title: str
    summary: str  # 2-3 sentences on this page's analytical focus
    # Decision-focused documentation fields (empty when not inferable; defaults
    # keep pre-existing document.json payloads loadable).
    users: str = ""                    # role(s) who use this page
    business_questions: list[str] = field(default_factory=list)
    decisions: str = ""                # the decision/action this page informs
    confidence: str = ""               # High | Medium | Low — inferred-purpose confidence


@dataclass
class VisualExplainer:
    visual: str           # name or type
    page: str
    how_to_read: str      # plain-English interpretation guidance


@dataclass
class ExecutiveSummary:
    core_purpose: str = ""
    pages: list[PageSummary] = field(default_factory=list)
    navigation_guide: list[str] = field(default_factory=list)
    complex_visual_explainers: list[VisualExplainer] = field(default_factory=list)
    provenance: str = "AI-inferred"


# -- I. Document Metadata -----------------------------------------------------
@dataclass
class DocumentMetadata:
    report_name: str
    owner: Optional[str] = None
    refresh_schedule: Optional[str] = None
    target_audience: Optional[str] = None
    source_format: Optional[str] = None
    generated_at: Optional[str] = None
    version: Optional[str] = None
    status: Optional[str] = None
    author: Optional[str] = None
    reviewer: Optional[str] = None
    classification: Optional[str] = None
    business_decision: Optional[str] = None
    requirements: Optional[str] = None
    security_notes: Optional[str] = None
    refresh_notes: Optional[str] = None
    deployment_notes: Optional[str] = None
    access_notes: Optional[str] = None
    glossary: Optional[str] = None
    assumptions: Optional[str] = None
    support_notes: Optional[str] = None
    score_trend: Optional[str] = None
    overridden_fields: list[str] = field(default_factory=list)


# -- III. Lineage & Architecture ----------------------------------------------
@dataclass
class LineageArchitecture:
    source_systems: list[str] = field(default_factory=list)
    # name -> plain-English description of the Power Query / ETL transform
    transformations: list[dict[str, str]] = field(default_factory=list)
    lineage_svg: Optional[str] = None
    lineage_edges: list[dict[str, str]] = field(default_factory=list)
    data_sources_inventory: list[dict[str, Any]] = field(default_factory=list)
    provenance: str = "Extracted"


# -- IV. Semantic Model -------------------------------------------------------
@dataclass
class SemanticModelDoc:
    summary: str = ""  # Data Modeler Agent narrative
    # flattened data dictionary rows: {table, column, data_type, description}
    data_dictionary: list[dict[str, str]] = field(default_factory=list)
    # human-readable relationship lines
    relationships: list[str] = field(default_factory=list)
    # modeling risks, rendered as their own list (not folded into summary)
    risks: list[str] = field(default_factory=list)
    # structured table/edge data for the model diagram
    tables: list[dict[str, Any]] = field(default_factory=list)          # {name, kind, columns, measures}
    relationship_edges: list[dict[str, Any]] = field(default_factory=list)  # {from, to, from_card, to_card, cross_filter, is_active}
    provenance: str = "Extracted"


# -- V. Measure Catalog -------------------------------------------------------
@dataclass
class MeasureEntry:
    name: str
    table: Optional[str]
    dax: str
    plain_english: str = ""    # business definition (what the number means)
    caveats: str = ""
    category: str = ""
    format_string: Optional[str] = None
    used_on: list[str] = field(default_factory=list)  # report pages that use it
    calculation_logic: str = ""   # how it computes, distinct from the business definition
    dependencies: list[str] = field(default_factory=list)  # measures/columns it references
    confidence: str = ""          # High | Medium | Low — inferred business meaning
    dependency_tree: str = ""
    provenance: Optional[str] = None


@dataclass
class MeasureCatalog:
    measures: list[MeasureEntry] = field(default_factory=list)
    dependency_svg: Optional[str] = None
    provenance: str = "Extracted"


# -- VI. Security & Governance ------------------------------------------------
@dataclass
class SecurityGovernance:
    roles: list[dict[str, Any]] = field(default_factory=list)
    workspace_constraints: list[str] = field(default_factory=list)
    provenance: str = "Extracted"


# -- VII. Tech Debt / Audit ---------------------------------------------------
@dataclass
class TechDebtAudit:
    orphaned_measures: list[str] = field(default_factory=list)
    hidden_but_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unused_assets: dict[str, Any] = field(default_factory=dict)
    suppressed_rules: list[str] = field(default_factory=list)
    provenance: str = "Extracted"


@dataclass
class Document:
    """Top-level ``document.json`` object (sections in canonical order)."""
    metadata: DocumentMetadata
    executive_summary: ExecutiveSummary = field(default_factory=ExecutiveSummary)
    lineage: LineageArchitecture = field(default_factory=LineageArchitecture)
    semantic_model: SemanticModelDoc = field(default_factory=SemanticModelDoc)
    measure_catalog: MeasureCatalog = field(default_factory=MeasureCatalog)
    security: SecurityGovernance = field(default_factory=SecurityGovernance)
    tech_debt: TechDebtAudit = field(default_factory=TechDebtAudit)
    # extracted facts for the enterprise-template sections
    stats: dict[str, int] = field(default_factory=dict)
    report_pages: list[dict[str, Any]] = field(default_factory=list)
    slicers: list[dict[str, Any]] = field(default_factory=list)
    calculated_columns: list[dict[str, Any]] = field(default_factory=list)
    glossary_entries: list[dict[str, str]] = field(default_factory=list)
    # Model health score computed by the deterministic audit rules:
    # {overall, band, component_scores: {...}, component_notes: {...}}
    health_score: dict[str, Any] = field(default_factory=dict)
    # Prioritized AI recommendations, each
    # {priority, issue, why_it_matters, suggested_fix, expected_benefit, effort}
    ai_recommendations: list[dict[str, str]] = field(default_factory=list)
    # Day 8: the broadest-impact root-cause cluster from the sibling Audit
    # document's Audit Synthesizer (Day 7), when both "technical" and
    # "audit" are generated in the same job — {root_cause, narrative,
    # confidence, rule_ids}. None when audit wasn't generated alongside this
    # doc or produced no clusters; the renderer then omits the callout
    # entirely rather than showing a placeholder.
    top_cluster: Optional[dict[str, Any]] = None
    navigation_map_svg: Optional[str] = None
    navigation_edges: list[dict[str, str]] = field(default_factory=list)
    changelog: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
