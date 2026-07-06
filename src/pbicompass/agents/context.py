"""Shared per-job AI context (Phase 0 of ``AI_NATIVE_PLAN.md``).

Before this module, ``technical.py::_measure_catalog``,
``executive.py::_key_kpis``, and ``user_guide.py::_build_glossary`` each ran
the DAX Translator over every measure independently — up to 3x redundant
spend in a ``--document all`` job. ``build_job_context`` now runs it once;
every generator's ``generate(...)`` takes an optional ``ai_context`` kwarg
so both entry points (``cli.py``, ``service/worker.py``) can build it once
before their doc-type loop and hand the same instance to each generator.

``ai_context=None`` is always a fully-supported input: a generator called
directly (as every existing test does) builds its own context on demand,
so direct-import callers keep working unchanged — the shared-context path is
purely an optimization for multi-document jobs, never a requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import io
from .llm import LLMClient
from ..schemas.model import SemanticModel

Warn = Callable[[str], None]


@dataclass
class JobAIContext:
    """Content-free: holds derived AI results and call telemetry, never raw
    report metadata beyond what the translations/insights already surface."""

    # Measure name -> DAX Translator result (plain_english/calculation_logic/
    # caveats/category/confidence). ``None`` means either offline or the
    # translator produced nothing usable — callers fall back per-measure.
    translations: Optional[dict[str, dict]] = None

    # Populated by Phase 2's report-intelligence pass; ``None`` until then.
    insights: Optional[dict] = None

    # Job-sandbox-scoped LLM response cache path (service only); ``None``
    # means "use the client-wide default" (``LLMResponseCache``'s own
    # ``PBICOMPASS_LLM_CACHE`` env-var lookup, e.g. the CLI's persistent
    # cache). Passed explicitly rather than via env var so concurrent jobs
    # in the same worker process never race on a shared environment
    # variable.
    cache_path: Optional[str] = None

    # Per-agent call/token counters — content-free (names and integers only).
    usage: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, agent: str, *, calls: int = 1, input_tokens: int = 0, output_tokens: int = 0) -> None:
        bucket = self.usage.setdefault(agent, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        bucket["calls"] += calls
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens


def build_job_context(
    model: SemanticModel,
    client: Optional[LLMClient],
    warn: Warn,
    *,
    cache_path: Optional[str] = None,
) -> JobAIContext:
    """Run the DAX Translator once for every measure in ``model`` and stash
    the merged result. Offline (``client is None``) or a fully failed pass
    both degrade to ``translations=None`` — every consumer already has a
    deterministic per-measure fallback for that case."""
    # Local import: ``generators.base`` needs ``io.AGENT_EFFORT``, and
    # ``generators/__init__.py`` (via audit/executive/technical/user_guide,
    # each needing ``JobAIContext`` for their ``generate(...)`` signature)
    # imports this module — a module-level import here would cycle back
    # into a not-yet-defined ``JobAIContext``.
    from .generators.base import call_llm

    ctx = JobAIContext(cache_path=cache_path)
    if client is None:
        return ctx
    merged: dict[str, dict] = {}
    for batch in io.dax_translator_batches(model):
        data = call_llm(
            client, io.DAX_TRANSLATOR_SYSTEM, batch, io.DAX_TRANSLATOR_SCHEMA,
            warn, "DAX Translator", ai_context=ctx,
        )
        if data:
            merged.update({t["name"]: t for t in data.get("translations", [])})
    ctx.translations = merged or None
    return ctx
