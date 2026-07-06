"""LLM client layer.

``LLMClient`` is the minimal contract the orchestrator depends on:
``complete_json(system, user, schema) -> dict``. Four concrete
implementations: :class:`AnthropicClient` (Claude, native structured
outputs), :class:`GeminiClient`, :class:`CohereClient`, and
:class:`MeshAPIClient` — the last routes through https://developers.meshapi.ai,
a single API key giving access to 1000+ models across providers via an
OpenAI-compatible endpoint, for BYOK users who'd rather not manage a
separate key per provider.

Every provider SDK import is lazy — the rest of ``pbicompass`` (and the
deterministic pipeline) works without any of them installed.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Protocol


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema: dict, *, effort: Optional[str] = None) -> dict:
        """Return a JSON object conforming to ``schema``.

        ``effort`` (Phase 0) is a per-call override of the client's own
        thinking-depth default (see ``EFFORT_LEVELS`` below) — ``None`` keeps
        the client's own default. Providers with no such concept (Gemini,
        Cohere) accept and ignore it.
        """
        ...


# Thinking-depth / token-spend levels for output_config.effort, low to high.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


# Hard ceiling on a single LLM call. Without this, a slow/huge prompt or a
# network hiccup on the host can block the calling thread indefinitely — the
# job then sits in "processing" forever since nothing else marks it failed.
# ``call_llm`` (agents/generators/base.py) catches the resulting timeout
# exception and falls back to the deterministic engine for that agent.
_DEFAULT_TIMEOUT_SECONDS = 180.0


class AnthropicClient:
    """Claude-backed client using structured outputs.

    Defaults to Claude Opus 4.8 with adaptive thinking at ``high`` effort — the
    recommended floor for intelligence-sensitive prose (Business Analyst, DAX
    Translator, Data Modeler agents). Pass ``effort="xhigh"`` or ``"max"`` for
    deeper reasoning at the cost of latency. Reads the API key from
    ``ANTHROPIC_API_KEY`` (BYOK) unless one is passed explicitly.
    """

    def __init__(
        self,
        model: str = "claude-opus-4-8",
        *,
        api_key: Optional[str] = None,
        effort: str = "high",
        max_tokens: int = 16000,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if effort not in EFFORT_LEVELS:
            raise ValueError(f"Unknown effort level {effort!r}. Choose from {EFFORT_LEVELS}.")
        try:
            import anthropic  # noqa: PLC0415 (intentional lazy import)
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "The 'anthropic' package is required for the Claude provider. "
                "Install it with: pip install -e \".[agents]\""
            ) from exc
        self._anthropic = anthropic
        self._client = (
            anthropic.Anthropic(api_key=api_key, timeout=timeout)
            if api_key else anthropic.Anthropic(timeout=timeout)
        )
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete_json(self, system: str, user: str, schema: dict, *, effort: Optional[str] = None) -> dict:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort or self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined the request (stop_reason=refusal).")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise RuntimeError("Claude returned no text content.")
        # Content-free spend telemetry (Phase 0): token counts only, never the
        # prompt/response text itself. Read opportunistically by callers
        # (``agents/generators/base.py::call_llm``) via ``getattr``.
        usage = getattr(response, "usage", None)
        self.last_usage = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        }
        return json.loads(text)


def _gemini_schema(schema: dict) -> dict:
    """Strip keys Gemini's ``response_schema`` doesn't accept (additionalProperties)."""
    def clean(node):
        if isinstance(node, dict):
            return {k: clean(v) for k, v in node.items() if k != "additionalProperties"}
        if isinstance(node, list):
            return [clean(x) for x in node]
        return node
    return clean(schema)


class GeminiClient:
    """Google Gemini-backed client using JSON structured output.

    Defaults to Gemini 3.5 Flash. Reads the API key from ``GEMINI_API_KEY`` (or
    ``GOOGLE_API_KEY``) — never hardcode it. Lazy-imports ``google-genai`` so the
    rest of ``pbicompass`` works without the dependency.
    """

    def __init__(self, model: str = "gemini-3.5-flash", *, api_key: Optional[str] = None,
                 max_output_tokens: int = 16000, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        try:
            from google import genai  # noqa: PLC0415 (intentional lazy import)
            from google.genai import types  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "The 'google-genai' package is required for the Gemini provider. "
                "Install it with: pip install -e \".[agents]\""
            ) from exc
        self._genai = genai
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        # HttpOptions.timeout is milliseconds.
        http_options = types.HttpOptions(timeout=int(timeout * 1000))
        self._client = (
            genai.Client(api_key=key, http_options=http_options)
            if key else genai.Client(http_options=http_options)
        )
        self.model = model
        self.max_output_tokens = max_output_tokens

    def complete_json(self, system: str, user: str, schema: dict, *, effort: Optional[str] = None) -> dict:
        # Gemini has no thinking-effort knob equivalent to Claude's — accepted
        # for protocol compatibility and silently ignored.
        from google.genai import types  # noqa: PLC0415
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=_gemini_schema(schema),
            max_output_tokens=self.max_output_tokens,
        )
        response = self._client.models.generate_content(
            model=self.model, contents=user, config=config,
        )
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("Gemini returned no text content.")
        return json.loads(text)


class CohereClient:
    """Cohere-backed client using JSON structured output.

    Defaults to Command A (``command-a-03-2025``), Cohere's text-only flagship —
    it returns a plain answer without the reasoning/thinking token spend of the
    ``command-a-plus``/``command-a-reasoning`` models, so it's markedly faster and
    cheaper for schema-constrained JSON. (Reasoning models still work: their
    content list leads with a ``thinking`` item, and ``complete_json`` skips past
    it to the ``text`` item.) Reads the API key from ``COHERE_API_KEY`` (or
    ``CO_API_KEY``) — never hardcode it. Lazy-imports ``cohere`` so the rest of
    ``pbicompass`` works without the dependency. Uses the v2 chat API with
    ``response_format={"type": "json_object", "schema": ...}``; Cohere accepts
    ``additionalProperties``, so the schemas pass through unmodified.
    """

    # Command A models cap output at 8192 tokens; sending more 400s the request.
    def __init__(self, model: str = "command-a-03-2025", *, api_key: Optional[str] = None,
                 max_tokens: int = 8192, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        try:
            import cohere  # noqa: PLC0415 (intentional lazy import)
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "The 'cohere' package is required for the Cohere provider. "
                "Install it with: pip install -e \".[agents]\""
            ) from exc
        self._cohere = cohere
        key = api_key or os.environ.get("COHERE_API_KEY") or os.environ.get("CO_API_KEY")
        # ClientV2 timeout is in seconds.
        self._client = cohere.ClientV2(api_key=key, timeout=timeout)
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete_json(self, system: str, user: str, schema: dict, *, effort: Optional[str] = None) -> dict:
        # Cohere has no thinking-effort knob — accepted for protocol
        # compatibility and silently ignored.
        response = self._client.chat(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                # Cohere's json_object mode wants the request to explicitly ask
                # for JSON, on top of the enforced schema.
                {"role": "user", "content": f"{user}\n\nRespond with a single JSON object."},
            ],
            response_format={"type": "json_object", "schema": schema},
        )
        # message.content is a list of typed items; reasoning models lead with a
        # 'thinking' item, so pick the 'text' one (mirrors the Anthropic client)
        # rather than blindly taking content[0].
        content = getattr(response.message, "content", None) or []
        text = next(
            (item.text for item in content
             if getattr(item, "type", None) == "text" and getattr(item, "text", None)),
            None,
        )
        if not text:
            raise RuntimeError("Cohere returned no text content.")
        return json.loads(text)


class MeshAPIClient:
    """MeshAPI-backed client (https://developers.meshapi.ai) — a single
    ``rsk_...`` API key routes to 1000+ models across providers through one
    OpenAI-compatible endpoint, so BYOK users no longer need a separate
    ``ANTHROPIC_API_KEY``/``GEMINI_API_KEY``/``COHERE_API_KEY`` per provider.

    Model ids are ``provider/model-name`` (e.g. ``"openai/gpt-4o"``,
    ``"anthropic/claude-opus-4.8"``) — see MeshAPI's model catalog for the
    full list. Note MeshAPI's own catalog uses dot-separated point releases
    for Claude models (``claude-opus-4.8``), unlike Anthropic's native API
    model ids, which use hyphens (``claude-opus-4-8``, as
    :class:`AnthropicClient` expects) — the two are not interchangeable.

    Defaults to ``openai/gpt-4o`` rather than a Claude model: MeshAPI routes
    at least some Anthropic model ids through AWS Bedrock's Converse API,
    which doesn't support the structured-output parameter MeshAPI's
    translation layer attaches for them (every ``complete_json`` call here
    fails with a Bedrock ``ValidationException`` on ``output_config.format``
    for those ids) — MeshAPI's own docs confirm first-class structured-output
    support for OpenAI (and Google Gemini) models, not Anthropic-via-Bedrock.
    Pass an explicit ``model=`` to use a different one once MeshAPI's
    Bedrock-routed structured-output support catches up.

    Implemented with the official ``openai`` SDK pointed at MeshAPI's base
    URL, exactly as MeshAPI's own quickstart recommends ("replace the Base
    URL of any OpenAI-compatible SDK with https://api.meshapi.ai"), rather
    than a bespoke ``meshapi`` package integration. Lazy-imports ``openai``
    so the rest of ``pbicompass`` works without the dependency. Reads the
    API key from ``MESHAPI_API_KEY`` (BYOK) unless passed explicitly.
    """

    _BASE_URL = "https://api.meshapi.ai/v1"

    # MeshAPI's `reasoning_effort` is a 4-level enum (low/medium/high/none) —
    # coarser than this codebase's 5-level EFFORT_LEVELS; xhigh/max both map
    # to its ceiling ("high") since MeshAPI has nothing deeper.
    _EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}

    def __init__(
        self,
        model: str = "openai/gpt-4o",
        *,
        api_key: Optional[str] = None,
        effort: Optional[str] = None,
        max_tokens: int = 16000,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        try:
            import openai  # noqa: PLC0415 (intentional lazy import)
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "The 'openai' package is required for the MeshAPI provider (MeshAPI "
                "exposes an OpenAI-compatible API). Install it with: pip install -e \".[agents]\""
            ) from exc
        key = api_key or os.environ.get("MESHAPI_API_KEY")
        self._client = openai.OpenAI(base_url=self._BASE_URL, api_key=key, timeout=timeout)
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete_json(self, system: str, user: str, schema: dict, *, effort: Optional[str] = None) -> dict:
        resolved_effort = effort if effort is not None else self.effort
        extra: dict[str, Any] = {}
        if resolved_effort is not None:
            extra["reasoning_effort"] = self._EFFORT_MAP.get(resolved_effort, resolved_effort)
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            },
            **extra,
        )
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "content_filter":
            raise RuntimeError("MeshAPI declined the request (finish_reason=content_filter).")
        text = choice.message.content
        if not text:
            raise RuntimeError("MeshAPI returned no text content.")
        usage = getattr(response, "usage", None)
        self.last_usage = {
            "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }
        return json.loads(text)


_DEFAULT_MODEL = {
    "anthropic": "claude-opus-4-8",
    "gemini": "gemini-3.5-flash",
    "cohere": "command-a-03-2025",
    # openai/gpt-4o, not a Claude id — see MeshAPIClient's docstring: MeshAPI
    # routes at least some Anthropic ids through AWS Bedrock's Converse API,
    # which rejects the structured-output parameter every agent here needs.
    "meshapi": "openai/gpt-4o",
}


def get_client(provider: Optional[str], **kwargs: Any) -> Optional[LLMClient]:
    """Resolve a provider name to a client (or ``None`` for the offline engine).

    ``None`` / ``"none"`` / ``"offline"`` -> deterministic pipeline (no client).
    ``"anthropic"`` / ``"claude"``        -> :class:`AnthropicClient`.
    ``"gemini"`` / ``"google"``           -> :class:`GeminiClient`.
    ``"cohere"`` / ``"command"``          -> :class:`CohereClient`.
    ``"meshapi"`` / ``"mesh"``            -> :class:`MeshAPIClient` (one key, 1000+ models).
    """
    if provider in (None, "none", "offline", "deterministic"):
        return None
    model = kwargs.pop("model", None)
    if provider in ("anthropic", "claude"):
        if not model or "claude" not in model:
            model = _DEFAULT_MODEL["anthropic"]
        return AnthropicClient(model=model, **kwargs)
    if provider in ("gemini", "google"):
        if not model or "gemini" not in model:
            model = _DEFAULT_MODEL["gemini"]
        return GeminiClient(model=model, **kwargs)
    if provider in ("cohere", "command"):
        if not model or "command" not in model:
            model = _DEFAULT_MODEL["cohere"]
        return CohereClient(model=model, **kwargs)
    if provider in ("meshapi", "mesh"):
        # MeshAPI model ids are always "provider/model-name" — a bare model
        # id from another provider's default (e.g. the CLI's own
        # "claude-opus-4-8") isn't valid here, so fall back the same way.
        if not model or "/" not in model:
            model = _DEFAULT_MODEL["meshapi"]
        return MeshAPIClient(model=model, **kwargs)
    raise ValueError(f"Unknown LLM provider: {provider!r}")
