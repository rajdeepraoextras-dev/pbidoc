"""Backward-compatible entry point: ``SemanticModel`` -> ``Document``.

The technical-document generation logic that used to live in this module has
moved to :mod:`pbicompass.agents.generators.technical` (``TechnicalDocumentation
Generator``), alongside the new document-type generators (Audit & Health
Report, and more to follow). ``generate_document`` is kept here, unchanged in
signature and behavior, as a one-line delegate — any existing caller
(``pbicompass.cli``, the web service, direct imports) keeps working exactly as
before.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..schemas.audit_document import FindingCluster
from ..schemas.document import Document
from ..schemas.model import SemanticModel
from .consistency import AuditVerdicts
from .context import JobAIContext
from .generators.technical import TechnicalDocumentationGenerator
from .llm import LLMClient

Warn = Callable[[str], None]


def generate_document(
    model: SemanticModel,
    client: Optional[LLMClient] = None,
    *,
    owner: Optional[str] = None,
    audience: Optional[str] = None,
    refresh: Optional[str] = None,
    on_warning: Optional[Warn] = None,
    # Custom metadata fields
    version: Optional[str] = None,
    status: Optional[str] = None,
    author: Optional[str] = None,
    reviewer: Optional[str] = None,
    classification: Optional[str] = None,
    business_decision: Optional[str] = None,
    requirements: Optional[str] = None,
    security_notes: Optional[str] = None,
    refresh_notes: Optional[str] = None,
    deployment_notes: Optional[str] = None,
    access_notes: Optional[str] = None,
    glossary: Optional[str] = None,
    assumptions: Optional[str] = None,
    support_notes: Optional[str] = None,
    supplied_optional_fields: Optional[list[str]] = None,
    ai_context: Optional[JobAIContext] = None,
    top_cluster: Optional[FindingCluster] = None,
    audit_verdicts: Optional[AuditVerdicts] = None,
    requirements_matrix: Optional[list] = None,
) -> Document:
    """Assemble the seven-section :class:`Document` from a parsed model.

    Pass an ``LLMClient`` to use Claude for the prose agents; omit it (or pass
    ``None``) to run the fully deterministic offline pipeline. ``ai_context``
    (Phase 0) forwards a job-shared :class:`JobAIContext`; omit it (or pass
    ``None``) and the generator builds its own on demand. ``top_cluster``
    (Day 8) forwards the sibling Audit document's top root-cause cluster for
    §16; omit it (or pass ``None``) and §16 carries no root-cause callout.
    ``audit_verdicts`` (Day 2) forwards the sibling Audit document's
    ground-truth verdicts for the cross-artifact consistency check; omit it
    (or pass ``None``) and that check is skipped. ``requirements_matrix``
    (Day 4) forwards the pre-computed Requirements Traceability Matrix; omit
    it (or pass ``None``) and the generator computes its own.
    """
    return TechnicalDocumentationGenerator.generate(
        model, client,
        owner=owner, audience=audience, refresh=refresh, on_warning=on_warning,
        version=version, status=status, author=author, reviewer=reviewer,
        classification=classification, business_decision=business_decision,
        requirements=requirements, security_notes=security_notes,
        refresh_notes=refresh_notes, deployment_notes=deployment_notes,
        access_notes=access_notes, glossary=glossary,
        assumptions=assumptions, support_notes=support_notes,
        supplied_optional_fields=supplied_optional_fields,
        ai_context=ai_context, top_cluster=top_cluster, audit_verdicts=audit_verdicts,
        requirements_matrix=requirements_matrix,
    )
