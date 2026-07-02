"""Metadata shared across the non-technical document types (audit, executive,
user guide). Deliberately independent from :class:`~pbicompass.schemas.document.
DocumentMetadata` — that dataclass belongs to the technical document and stays
untouched for backward compatibility. This is a small, separate contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DocMetadataCore:
    report_name: str
    document_type: str
    owner: Optional[str] = None
    refresh_schedule: Optional[str] = None
    target_audience: Optional[str] = None
    source_format: Optional[str] = None
    generated_at: Optional[str] = None
    version: Optional[str] = None
    status: Optional[str] = None
