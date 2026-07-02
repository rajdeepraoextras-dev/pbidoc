"""LLM client layer.

``LLMClient`` is the minimal contract the orchestrator depends on:
``complete_json(system, user, schema) -> dict``. The concrete implementation
calls Claude through the official ``anthropic`` SDK with structured outputs
(``output_config.format``), so responses are schema-valid JSON.

The ``anthropic`` import is lazy — the rest of ``pbicompass`` (and the deterministic
pipeline) works without the dependency installed.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Protocol


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        """Return a JSON object conforming to ``schema``."""
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

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined the request (stop_reason=refusal).")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise RuntimeError("Claude returned no text content.")
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

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
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


_DEFAULT_MODEL = {"anthropic": "claude-opus-4-8", "gemini": "gemini-3.5-flash"}


def get_client(provider: Optional[str], **kwargs: Any) -> Optional[LLMClient]:
    """Resolve a provider name to a client (or ``None`` for the offline engine).

    ``None`` / ``"none"`` / ``"offline"`` -> deterministic pipeline (no client).
    ``"anthropic"`` / ``"claude"``        -> :class:`AnthropicClient`.
    ``"gemini"`` / ``"google"``           -> :class:`GeminiClient`.
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
    raise ValueError(f"Unknown LLM provider: {provider!r}")
