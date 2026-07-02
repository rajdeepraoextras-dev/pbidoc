"""Shared helpers used by every document generator."""

from __future__ import annotations

import json
from typing import Callable, Optional

from ...schemas.model import SemanticModel
from ...schemas.shared import DocMetadataCore
from ..llm import LLMClient

Warn = Callable[[str], None]


def call_llm(client: LLMClient, system: str, payload: dict, schema: dict,
             warn: Warn, name: str) -> Optional[dict]:
    """Call ``client.complete_json``; on any failure, warn and return ``None``
    so the caller can fall back to its deterministic path."""
    try:
        return client.complete_json(system, json.dumps(payload, ensure_ascii=False), schema)
    except Exception as exc:  # any failure -> deterministic fallback
        warn(f"{name}: LLM call failed, using deterministic fallback ({exc})")
        return None


def build_core_metadata(
    model: SemanticModel,
    document_type: str,
    *,
    default_audience: str,
    owner: Optional[str] = None,
    audience: Optional[str] = None,
    refresh: Optional[str] = None,
    version: Optional[str] = None,
    status: Optional[str] = None,
) -> DocMetadataCore:
    """Assemble the small metadata contract shared by the non-technical
    document types (audit, executive, user guide)."""
    return DocMetadataCore(
        report_name=model.report_name,
        document_type=document_type,
        owner=owner,
        refresh_schedule=refresh,
        target_audience=audience or default_audience,
        source_format=model.meta.source_format,
        generated_at=model.meta.generated_at,
        version=version,
        status=status,
    )
