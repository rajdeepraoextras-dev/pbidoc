"""Document renderers.

Pure-Python (no external tools): Markdown, HTML, and DOCX.
Optional Pandoc adapter: PDF (and Pandoc-quality DOCX).

``render_markdown``/``render_html``/``render_docx`` are the technical
document's renderers (original, unchanged). Other document types (audit, and
more to follow) get their own small dedicated renderer modules — see
``registry`` for the document-type -> renderer-set lookup used by the CLI.
"""

from .markdown import render_markdown
from .html import render_html
from .docx import render_docx
from . import pandoc
from . import registry
from .audit import render_markdown as render_audit_markdown
from .audit import render_html as render_audit_html
from .audit import render_docx as render_audit_docx
from .executive import render_markdown as render_executive_markdown
from .executive import render_html as render_executive_html
from .executive import render_docx as render_executive_docx
from .user_guide import render_markdown as render_user_guide_markdown
from .user_guide import render_html as render_user_guide_html
from .user_guide import render_docx as render_user_guide_docx

__all__ = [
    "render_markdown", "render_html", "render_docx", "pandoc",
    "registry", "render_audit_markdown", "render_audit_html", "render_audit_docx",
    "render_executive_markdown", "render_executive_html", "render_executive_docx",
    "render_user_guide_markdown", "render_user_guide_html", "render_user_guide_docx",
]
