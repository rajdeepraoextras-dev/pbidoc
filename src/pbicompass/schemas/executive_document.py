"""The ``executive_document.json`` contract — a concise, non-technical
summary readable in under ten minutes.

Audience: managers, executives, project owners. No implementation details:
no DAX, no table/column inventories, no relationship diagrams — those live
in the technical document and the audit report. Most fields are assembled
deterministically from facts already computed elsewhere in the pipeline
(model statistics, data sources, modeling risks, audit findings); only the
narrative prose fields optionally go through an LLM, with a deterministic
fallback so the document is always complete offline.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .shared import DocMetadataCore


@dataclass
class ExecutiveDocument:
    """Top-level ``executive_document.json`` object."""
    metadata: DocMetadataCore
    business_purpose: str = ""
    key_kpis: list[str] = field(default_factory=list)
    data_sources_summary: list[str] = field(default_factory=list)
    refresh_schedule: Optional[str] = None
    security_overview: str = ""
    architecture_overview: str = ""
    model_statistics: dict[str, int] = field(default_factory=dict)
    report_statistics: dict[str, int] = field(default_factory=dict)
    business_value: str = ""
    known_risks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    maintenance_overview: str = ""
    future_recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
