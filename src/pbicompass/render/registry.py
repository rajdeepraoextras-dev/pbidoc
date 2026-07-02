"""Renderer registry — maps a document type to its Markdown/HTML/DOCX render
functions, so the CLI (and, in later phases, the web service) can dispatch
without hand-written if/elif chains scattered across multiple call sites.

Every renderer set exposes the same three-callable shape (``md``, ``html``,
``docx``), so adding a new document type here is the only place that needs
touching to make it available end-to-end.
"""

from __future__ import annotations

from typing import Any, Callable

from . import audit as _audit_render
from . import docx as _technical_docx
from . import executive as _executive_render
from . import html as _technical_html
from . import markdown as _technical_markdown
from . import user_guide as _user_guide_render

RENDERERS: dict[str, dict[str, Callable[..., Any]]] = {
    "technical": {
        "md": _technical_markdown.render_markdown,
        "html": _technical_html.render_html,
        "docx": _technical_docx.render_docx,
    },
    "audit": {
        "md": _audit_render.render_markdown,
        "html": _audit_render.render_html,
        "docx": _audit_render.render_docx,
    },
    "executive": {
        "md": _executive_render.render_markdown,
        "html": _executive_render.render_html,
        "docx": _executive_render.render_docx,
    },
    "user-guide": {
        "md": _user_guide_render.render_markdown,
        "html": _user_guide_render.render_html,
        "docx": _user_guide_render.render_docx,
    },
}


def markdown_text(document_type: str, doc: Any) -> str:
    """Render ``doc`` to Markdown text regardless of document type — the
    common input the PDF path (Pandoc) needs, since ``pandoc.to_pdf`` only
    ever takes Markdown text, never a document object."""
    return RENDERERS[document_type]["md"](doc)
