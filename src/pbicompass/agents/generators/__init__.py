"""Document generators — one per document type, all fanning out from the
same parsed :class:`~pbicompass.schemas.model.SemanticModel`.

``DOCUMENT_TYPES`` is the registry the CLI (and, in later phases, the web
service) consults to resolve a ``--document`` choice to a generator.
"""

from __future__ import annotations

from .audit import AuditReportGenerator
from .executive import ExecutiveSummaryGenerator
from .technical import TechnicalDocumentationGenerator
from .user_guide import BusinessGuideGenerator

DOCUMENT_TYPES = {
    "technical": TechnicalDocumentationGenerator,
    "audit": AuditReportGenerator,
    "executive": ExecutiveSummaryGenerator,
    "user-guide": BusinessGuideGenerator,
}

__all__ = [
    "TechnicalDocumentationGenerator", "AuditReportGenerator", "ExecutiveSummaryGenerator",
    "BusinessGuideGenerator", "DOCUMENT_TYPES",
]
