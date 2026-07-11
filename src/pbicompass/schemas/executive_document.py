"""The ``executive_document.json`` contract — a concise, non-technical
summary readable in under ten minutes (G.1: 6 sections, printing to no more
than 2 pages).

Audience: managers, executives, project owners. No implementation details:
no DAX, no table/column inventories, no relationship diagrams, no raw file
paths, and no model/report statistics tables — those live in the technical
document and the audit report; this document gets only the 4-KPI header
strip. Most fields are assembled deterministically from facts already
computed elsewhere in the pipeline (data sources, modeling risks, audit
findings); only the narrative prose fields optionally go through an LLM,
with a deterministic fallback so the document is always complete offline.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .audit_document import HealthScore
from .shared import DocMetadataCore


@dataclass
class ExecutiveRisk:
    """One risk phrased for executives (G.1): a consequence if left
    unaddressed, plus a specific ask — never raw audit/DAX terminology.
    ``rule_id``, when set, deep-links to the exact audit finding behind this
    risk (I5) instead of a generic section-level link."""
    severity: str
    consequence: str
    ask: str
    rule_id: str = ""


@dataclass
class ExecutiveNextStep:
    """One row of the "What's Next" table (Day 5 restructure): a remediation
    item not already covered by Top Risks, scored the same way (severity,
    a business-phrased action, and the audit engine's own effort estimate)
    so a reader can triage in seconds instead of reading prose."""
    severity: str
    action: str
    effort: str = "Medium"
    rule_id: str = ""


@dataclass
class ExecutivePageThumbnail:
    """A small (25%-scale) rendering of one report page's wireframe (Day 5):
    gives a non-technical reader the shape of the report at a glance without
    opening the technical document or user guide. Reuses the exact same SVG
    building block those two documents use full-size (``render._wireframe``)
    — never a second, divergent drawing of the same page. ``anchor`` is the
    stable ``page-{slug}`` id shared by every renderer, so the HTML version
    can deep-link into a sibling document's full-size page section when one
    was generated in the same job."""
    name: str
    svg: str
    anchor: str


@dataclass
class ExecutiveDocument:
    """Top-level ``executive_document.json`` object."""
    metadata: DocMetadataCore
    purpose: str = ""
    business_value: str = ""
    key_kpis: list[str] = field(default_factory=list)
    top_risks: list[ExecutiveRisk] = field(default_factory=list)
    # Source *types* only (e.g. "3 Excel workbook(s)") — never a path,
    # server, or database name (G.1).
    data_source_types: list[str] = field(default_factory=list)
    refresh_schedule: Optional[str] = None
    # Day 3: gateway/latency detail from the intake form's "Gateway, Latency
    # & Refresh Details" field — the "Data & Refresh at a Glance" section's
    # only source for anything beyond the bare schedule string.
    refresh_notes: Optional[str] = None
    maintenance_note: str = ""
    # Owner comes from ``metadata.owner`` (shared across doc types); steward
    # has no source yet — will be sourced from the enrichment file (5.1)
    # once it's wired in — always "not specified" until then.
    steward: Optional[str] = None
    classification: Optional[str] = None
    # Day 5: structured rows (severity/action/effort), replacing the old
    # plain-string bullet list — a boardroom reader triages a table in
    # seconds; prose sentences made them re-read every line.
    next_steps: list[ExecutiveNextStep] = field(default_factory=list)
    # Day 4: "7/9"-style Requirements Traceability coverage stat (empty
    # when no requirements were supplied) — see agents.traceability.
    requirements_coverage: str = ""
    # Day 5 (G.1 boardroom-grade pass): the deterministic audit engine's
    # health score, reused verbatim (never re-derived) so the exec doc's
    # number always agrees with the Audit & Health Report's. ``None`` only
    # if the caller didn't run the audit engine at all (never happens via
    # the normal generator entry point).
    health: Optional[HealthScore] = None
    # Day 5: up to 6 visible report pages, each a small wireframe thumbnail
    # — "Report at a glance" for a reader who will never open the technical
    # document or user guide.
    page_thumbnails: list[ExecutivePageThumbnail] = field(default_factory=list)
    # True count of visible pages (may exceed len(page_thumbnails) — the
    # section caps at 6 and notes "+N more" rather than growing unbounded).
    page_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
