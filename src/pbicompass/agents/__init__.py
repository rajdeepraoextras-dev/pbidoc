"""AI orchestration layer — turns a ``SemanticModel`` into a ``Document``.

Three LLM-backed agents (Business Analyst, DAX Translator, Data Modeler) plus a
deterministic Auditor. Each LLM agent has a deterministic offline counterpart,
so the whole pipeline runs and is testable without any API key. Provide an
``LLMClient`` (e.g. the Anthropic one) to upgrade the prose-generating agents.
"""

from .orchestrator import generate_document
from .llm import LLMClient, AnthropicClient, GeminiClient, get_client

__all__ = ["generate_document", "LLMClient", "AnthropicClient", "GeminiClient", "get_client"]
