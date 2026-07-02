"""Shared parsing primitives.

The heart of TMDL parsing is an indentation-aware tokenizer. TMDL is a
tab-indented DSL; we normalise each line to ``(indent, text)`` and provide
helpers to walk indentation blocks and reconstruct multi-line DAX/M bodies.

This is a pragmatic v0 parser that covers the common ~95% of real exports.
Anything it cannot interpret is recorded as a warning rather than raised, so
a single odd construct never aborts a whole document build.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Optional


class Line(NamedTuple):
    indent: int   # indentation depth in "levels" (1 tab == 1 level)
    text: str     # the line with leading indentation removed, right-stripped


def _leading_unit(raw_lines: list[str]) -> tuple[str, int]:
    """Detect indentation style. Returns (kind, space_unit).

    Power BI exports TMDL with tabs; we fall back to spaces if a file happens
    to use them, normalising by the smallest non-zero indent found.
    """
    if any(ln.startswith("\t") for ln in raw_lines):
        return ("tab", 1)
    units = []
    for ln in raw_lines:
        stripped = ln.lstrip(" ")
        n = len(ln) - len(stripped)
        if n and stripped:
            units.append(n)
    unit = min(units) if units else 4
    return ("space", unit or 4)


def tokenize(text: str) -> list[Line]:
    """Turn TMDL/M text into indentation-aware lines.

    Blank lines and ``//`` comments (including ``///`` docs) are dropped — they
    do not affect block structure and keep reconstructed expressions clean.
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kind, unit = _leading_unit(raw)
    out: list[Line] = []
    for ln in raw:
        stripped = ln.strip()
        if not stripped:
            continue
        if stripped.startswith("//") or stripped.startswith("/*"):
            continue
        if kind == "tab":
            body = ln.lstrip("\t")
            indent = len(ln) - len(body)
        else:
            body = ln.lstrip(" ")
            indent = (len(ln) - len(body)) // unit
        out.append(Line(indent, body.rstrip()))
    return out


def block_body(lines: list[Line], start: int) -> tuple[list[Line], int]:
    """Return all lines belonging to the object declared at ``lines[start]``.

    The body is the consecutive run of lines more indented than the header.
    Returns (body_lines, index_after_block).
    """
    base = lines[start].indent
    i = start + 1
    body: list[Line] = []
    while i < len(lines) and lines[i].indent > base:
        body.append(lines[i])
        i += 1
    return body, i


def unquote(name: str) -> str:
    """Strip TMDL single-quote quoting, handling the ``''`` escape."""
    name = name.strip()
    if len(name) >= 2 and name.startswith("'") and name.endswith("'"):
        return name[1:-1].replace("''", "'")
    return name


def parse_decl(text: str, keyword: str) -> tuple[str, Optional[str]]:
    """Parse a declaration header like ``measure 'Total Sales' = SUM(...)``.

    Returns (name, inline_value) where ``inline_value`` is whatever follows
    the first ``=`` (or ``None`` if there is no ``=``). The value may be an
    empty string when the real body is multi-line.
    """
    rest = text[len(keyword):].lstrip()
    if rest.startswith("'"):
        # quoted name: read to the closing quote, honouring '' escapes
        i = 1
        buf = []
        while i < len(rest):
            ch = rest[i]
            if ch == "'":
                if i + 1 < len(rest) and rest[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                i += 1
                break
            buf.append(ch)
            i += 1
        name = "".join(buf)
        remainder = rest[i:].lstrip()
    else:
        j = 0
        while j < len(rest) and rest[j] not in (" ", "\t", "="):
            j += 1
        name = rest[:j]
        remainder = rest[j:].lstrip()

    inline: Optional[str] = None
    if remainder.startswith("="):
        inline = remainder[1:].strip()
    return name, inline


def split_prop(text: str) -> tuple[str, str]:
    """Split a simple property line into (key, value).

    Handles ``key: value``, ``key = value`` and bare boolean flags (``isHidden``).
    """
    # Prefer ':' when it appears before any '='.
    colon = text.find(":")
    eq = text.find("=")
    if colon != -1 and (eq == -1 or colon < eq):
        return text[:colon].strip(), text[colon + 1:].strip()
    if eq != -1:
        return text[:eq].strip(), text[eq + 1:].strip()
    return text.strip(), "true"


def props_at_level(body: list[Line], level: int) -> dict[str, str]:
    """Collect ``key: value`` properties at exactly ``level`` indentation."""
    out: dict[str, str] = {}
    for ln in body:
        if ln.indent == level:
            key, val = split_prop(ln.text)
            if key and key not in out:
                out[key] = val
    return out


def capture_expression(
    body: list[Line], base_indent: int, prop_keys: Iterable[str]
) -> str:
    """Reconstruct a multi-line DAX/M body, skipping known property blocks.

    ``body`` are the lines under an object declared at ``base_indent``. Lines at
    the first child level (``base_indent + 1``) whose key is in ``prop_keys`` —
    together with any deeper lines they own (e.g. a ``formatStringDefinition``
    block) — are skipped. Everything else is the expression, re-indented
    relative to the first child level.
    """
    keys = set(prop_keys)
    child = base_indent + 1
    kept: list[Line] = []
    i = 0
    while i < len(body):
        ln = body[i]
        if ln.indent == child:
            key, _ = split_prop(ln.text)
            if key in keys:
                # skip this property and any sub-block it owns
                i += 1
                while i < len(body) and body[i].indent > child:
                    i += 1
                continue
        kept.append(ln)
        i += 1
    rendered = [("\t" * (ln.indent - child)) + ln.text for ln in kept]
    return "\n".join(rendered).strip()


def to_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes")
