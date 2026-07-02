"""The ``user_guide_document.json`` contract — teaches a business user how to
use the report without needing the developer.

Audience: business users, written as if explaining to a new employee. No
"table", "DAX", or "semantic model" — the deterministic generator and the
optional LLM prompt both enforce this explicitly.

``bookmarks`` and ``tooltips`` on :class:`PageGuide` are always empty:
today's ``model.json`` has no bookmark or tooltip data at all (that's a
future parser enhancement, out of scope here) — the renderer omits those
subsections entirely rather than showing a misleading "None" row that
implies the check actually ran against real data.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .shared import DocMetadataCore


@dataclass
class GlossaryTerm:
    term: str
    plain_definition: str
    example: Optional[str] = None


@dataclass
class PageGuide:
    page_title: str
    purpose: str = ""
    main_kpis: list[str] = field(default_factory=list)
    visual_descriptions: list[dict[str, str]] = field(default_factory=list)  # {visual, what_it_shows}
    filters: list[str] = field(default_factory=list)
    navigation_tips: list[str] = field(default_factory=list)
    business_questions_answered: list[str] = field(default_factory=list)
    drillthrough_actions: list[str] = field(default_factory=list)
    bookmarks: list[str] = field(default_factory=list)
    tooltips: list[str] = field(default_factory=list)
    common_scenarios: list[str] = field(default_factory=list)


@dataclass
class UserGuideDocument:
    """Top-level ``user_guide_document.json`` object."""
    metadata: DocMetadataCore
    introduction: str = ""
    pages: list[PageGuide] = field(default_factory=list)
    glossary: list[GlossaryTerm] = field(default_factory=list)
    getting_started: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
