"""Shared helpers used by every document generator."""

from __future__ import annotations

import json
import random
import time
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


def call_llm_with_retry(
    client: LLMClient, system: str, payload: dict, schema: dict,
    *, retries: int = 1, backoff_range: tuple[float, float] = (2.0, 5.0),
) -> Optional[dict]:
    """Like :func:`call_llm`, but retries once (after a jittered delay)
    before giving up, and never warns itself.

    Built for batched callers (a page batch, a page-guide batch): a lone
    failed/invalid batch response is often a transient blip (rate limit,
    network hiccup), so it's retried silently first. Only on the final
    failure does this return ``None`` — the caller knows exactly which
    objects (pages, measures, ...) that batch covered and can produce a far
    more specific warning than this function could, so it does that instead
    of warning here.
    """
    attempt = 0
    while True:
        try:
            return client.complete_json(system, json.dumps(payload, ensure_ascii=False), schema)
        except Exception:
            if attempt >= retries:
                return None
            time.sleep(random.uniform(*backoff_range))
            attempt += 1


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
