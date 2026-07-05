"""Optional Pandoc adapter for PDF (and Pandoc-quality DOCX) output.

Pandoc is an external binary, isolated here so the rest of the package never
depends on it. ``md``/``html``/``docx`` all have pure-Python renderers; this
adapter only matters for PDF, which needs Pandoc plus a PDF engine. When the
toolchain is missing, callers get a clear, actionable error rather than a crash.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

# Preference order for Pandoc PDF engines (first one found wins).
_PDF_ENGINES = ("tectonic", "xelatex", "lualatex", "pdflatex", "wkhtmltopdf", "weasyprint")


class PandocError(RuntimeError):
    """Raised when Pandoc (or a required PDF engine) is unavailable or fails."""


def pandoc_available() -> bool:
    return shutil.which("pandoc") is not None


def find_pdf_engine() -> Optional[str]:
    for engine in _PDF_ENGINES:
        if shutil.which(engine):
            return engine
    return None


def _run(args: list[str], stdin_text: str) -> None:
    try:
        proc = subprocess.run(
            args, input=stdin_text.encode("utf-8"),
            capture_output=True, check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on environment
        raise PandocError("pandoc executable not found on PATH.") from exc
    if proc.returncode != 0:
        raise PandocError(proc.stderr.decode("utf-8", "replace").strip() or "pandoc failed.")


def to_docx(markdown_text: str, out_path, *, reference_doc: Optional[str] = None) -> Path:
    """Convert Markdown to DOCX via Pandoc (alternative to the pure-Python writer)."""
    if not pandoc_available():
        raise PandocError(
            "Pandoc is not installed. Use the built-in DOCX writer (no Pandoc "
            "needed) or install Pandoc from https://pandoc.org/install.html."
        )
    out_path = Path(out_path)
    args = ["pandoc", "-f", "gfm", "-o", str(out_path)]
    if reference_doc:
        args += ["--reference-doc", reference_doc]
    _run(args, markdown_text)
    return out_path


def _yaml_scalar(value: str) -> str:
    """A YAML double-quoted scalar, safe for arbitrary title/author text
    (escapes backslashes and double quotes; YAML double-quoted strings don't
    otherwise treat Markdown/DAX special characters specially)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _title_block(title: Optional[str], author: Optional[str], date: Optional[str]) -> str:
    """A Pandoc YAML metadata block (2.8's "Markdown title block"). LaTeX-
    family engines (tectonic/xelatex/lualatex/pdflatex) render this as a
    ``\\maketitle`` cover page via Pandoc's default template; HTML-family
    engines (wkhtmltopdf/weasyprint) only use it for the document's
    ``<title>`` — a harmless no-op there, not a regression."""
    if not title:
        return ""
    lines = ["---", f'title: "{_yaml_scalar(title)}"']
    if author:
        lines.append(f'author: "{_yaml_scalar(author)}"')
    if date:
        lines.append(f'date: "{_yaml_scalar(date)}"')
    lines.append("---\n")
    return "\n".join(lines)


def to_pdf(
    markdown_text: str, out_path, *, engine: Optional[str] = None,
    title: Optional[str] = None, author: Optional[str] = None, date: Optional[str] = None,
) -> Path:
    """Convert Markdown to PDF via Pandoc + a PDF engine. ``title``/``author``/
    ``date`` prepend a YAML metadata block so PDF engines that render one get
    a cover/title page (2.8) — omit them for byte-identical output to before."""
    if not pandoc_available():
        raise PandocError(
            "PDF output needs Pandoc, which is not installed. Install it from "
            "https://pandoc.org/install.html -- or generate HTML (--format html) "
            "and use your browser's 'Print > Save as PDF'."
        )
    engine = engine or find_pdf_engine()
    if not engine:
        raise PandocError(
            "Pandoc is installed but no PDF engine was found. Install one of "
            f"{', '.join(_PDF_ENGINES)} -- or generate HTML (--format html) and "
            "use your browser's 'Print > Save as PDF'."
        )
    out_path = Path(out_path)
    content = _title_block(title, author, date) + markdown_text
    _run(["pandoc", "-f", "gfm", f"--pdf-engine={engine}", "-o", str(out_path)], content)
    return out_path
