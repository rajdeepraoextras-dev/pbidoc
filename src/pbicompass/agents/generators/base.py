"""Shared helpers used by every document generator."""

from __future__ import annotations

import json
import random
import time
from typing import TYPE_CHECKING, Callable, Optional

from ...schemas.model import SemanticModel
from ...schemas.shared import DocMetadataCore
from ..io import AGENT_EFFORT
from ..llm import LLMClient

Warn = Callable[[str], None]


from ..cache import LLMResponseCache

if TYPE_CHECKING:
    from ..context import JobAIContext


def _resolve_effort(name: str, effort: Optional[str]) -> Optional[str]:
    """An explicit ``effort=`` always wins; otherwise fall back to the
    agent's tier in ``io.AGENT_EFFORT`` (Phase 0); an agent absent from that
    map keeps the client's own default (``None``)."""
    return effort if effort is not None else AGENT_EFFORT.get(name)


def _record_usage(ai_context: Optional["JobAIContext"], client: LLMClient, name: str) -> None:
    """Content-free spend telemetry: token counts only, read opportunistically
    off whatever the client stashed after its last real (non-cached) call."""
    if ai_context is None:
        return
    usage = getattr(client, "last_usage", None) or {}
    ai_context.record(
        name,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
    )


def call_llm(client: LLMClient, system: str, payload: dict, schema: dict,
             warn: Warn, name: str, *,
             ai_context: Optional["JobAIContext"] = None,
             effort: Optional[str] = None) -> Optional[dict]:
    """Call ``client.complete_json``; on any failure, warn and return ``None``
    so the caller can fall back to its deterministic path.

    ``ai_context`` (Phase 0), when given, supplies the job-scoped cache path
    (falling back to the client-wide default when absent) and collects
    content-free call/token telemetry under ``name``. ``effort`` overrides
    ``io.AGENT_EFFORT``'s tier for ``name``.
    """
    model_id = getattr(client, "model", "unknown")
    effort = _resolve_effort(name, effort)
    cache_path = ai_context.cache_path if ai_context is not None else None
    cache = LLMResponseCache(cache_path)
    try:
        cached = cache.get(system, payload, schema, model_id, effort)
        if cached is not None:
            return cached
        res = client.complete_json(system, json.dumps(payload, ensure_ascii=False), schema, effort=effort)
        if res is not None:
            cache.set(system, payload, schema, model_id, res, effort)
            _record_usage(ai_context, client, name)
        return res
    except Exception as exc:  # any failure -> deterministic fallback
        warn(f"{name}: LLM call failed, using deterministic fallback ({exc})")
        return None
    finally:
        cache.close()


def call_llm_with_retry(
    client: LLMClient, system: str, payload: dict, schema: dict,
    *, retries: int = 1, backoff_range: tuple[float, float] = (2.0, 5.0),
    ai_context: Optional["JobAIContext"] = None,
    effort: Optional[str] = None,
    name: str = "LLM",
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

    ``name`` identifies the agent for the effort tier (``io.AGENT_EFFORT``)
    and telemetry (``ai_context``) — see :func:`call_llm`.
    """
    model_id = getattr(client, "model", "unknown")
    effort = _resolve_effort(name, effort)
    cache_path = ai_context.cache_path if ai_context is not None else None
    cache = LLMResponseCache(cache_path)
    try:
        cached = cache.get(system, payload, schema, model_id, effort)
        if cached is not None:
            return cached
        attempt = 0
        while True:
            try:
                res = client.complete_json(system, json.dumps(payload, ensure_ascii=False), schema, effort=effort)
                if res is not None:
                    cache.set(system, payload, schema, model_id, res, effort)
                    _record_usage(ai_context, client, name)
                return res
            except Exception:
                if attempt >= retries:
                    return None
                attempt += 1
                time.sleep(random.uniform(*backoff_range))
    finally:
        cache.close()


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
    overridden = getattr(model.meta, "overridden_fields", [])
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
        overridden_fields=list(overridden),
    )
