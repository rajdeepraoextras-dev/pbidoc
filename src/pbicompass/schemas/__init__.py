"""Canonical data contracts for the documentation pipeline.

``model``    -> the normalised *metadata* extracted from a Power BI file
               (the "model.json" contract every parser must produce).
``document`` -> the assembled documentation object the AI agents populate
               (the "document.json" contract the renderers consume).

These are stdlib ``dataclasses`` so the parser core stays dependency-free.
They map 1:1 onto Pydantic models, which the API/agent layer adds in Phase 2.
"""

from .model import (
    SemanticModel,
    Table,
    Column,
    Measure,
    Partition,
    Relationship,
    Role,
    TablePermission,
    MExpression,
    DataSource,
    Page,
    Visual,
    ModelMeta,
)
from .document import (
    Document,
    DocumentMetadata,
    ExecutiveSummary,
    PageSummary,
    VisualExplainer,
    LineageArchitecture,
    SemanticModelDoc,
    MeasureCatalog,
    MeasureEntry,
    SecurityGovernance,
    TechDebtAudit,
)
from .shared import DocMetadataCore
from .audit_document import (
    AuditDocument,
    HealthScore,
    ComplexityAssessment,
    DaxFinding,
    BestPracticeCheck,
    PerformanceRisk,
    GovernanceFinding,
    UnusedAssets,
    Recommendation,
)
from .executive_document import ExecutiveDocument
from .user_guide_document import GlossaryTerm, PageGuide, UserGuideDocument

__all__ = [
    # model.json
    "SemanticModel",
    "Table",
    "Column",
    "Measure",
    "Partition",
    "Relationship",
    "Role",
    "TablePermission",
    "MExpression",
    "DataSource",
    "Page",
    "Visual",
    "ModelMeta",
    # document.json
    "Document",
    "DocumentMetadata",
    "ExecutiveSummary",
    "PageSummary",
    "VisualExplainer",
    "LineageArchitecture",
    "SemanticModelDoc",
    "MeasureCatalog",
    "MeasureEntry",
    "SecurityGovernance",
    "TechDebtAudit",
    # shared
    "DocMetadataCore",
    # audit_document.json
    "AuditDocument",
    "HealthScore",
    "ComplexityAssessment",
    "DaxFinding",
    "BestPracticeCheck",
    "PerformanceRisk",
    "GovernanceFinding",
    "UnusedAssets",
    "Recommendation",
    # executive_document.json
    "ExecutiveDocument",
    # user_guide_document.json
    "GlossaryTerm",
    "PageGuide",
    "UserGuideDocument",
]
