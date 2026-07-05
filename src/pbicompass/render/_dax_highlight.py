"""DAX syntax highlighting for ``<pre><code>`` blocks.

Expressions are attacker-controlled text (a hostile ``.pbip`` file could
carry a measure named/expressioned to look like markup), so every raw
substring — matched token or not — is escaped individually before being
placed in the output; nothing here ever wraps unescaped text in a tag.
"""

from __future__ import annotations

import re
from html import escape as _escape

# Common DAX functions worth calling out — not exhaustive, just the ones
# that show up often enough in generated measure catalogs to be worth the
# visual anchor.
_KEYWORDS = {
    "VAR", "RETURN", "CALCULATE", "CALCULATETABLE", "FILTER", "ALL", "ALLEXCEPT",
    "ALLSELECTED", "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "RANKX", "TOPN",
    "DIVIDE", "IF", "SWITCH", "USERELATIONSHIP", "CROSSFILTER", "DISTINCTCOUNT",
    "COUNTROWS", "SELECTEDVALUE", "TOTALYTD", "TOTALQTD", "TOTALMTD", "DATESYTD",
    "DATESQTD", "DATESMTD", "SAMEPERIODLASTYEAR", "DATEADD", "PARALLELPERIOD",
    "RELATED", "RELATEDTABLE", "SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA",
}

_TOKEN_RE = re.compile(
    r"(?P<comment>//[^\n]*|/\*.*?\*/)"
    r"|(?P<string>\"(?:[^\"\\]|\\.)*\")"
    r"|(?P<ref>(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\[[^\]]+\]|\[[^\]]+\])"
    r"|(?P<number>\b\d+(?:\.\d+)?\b)"
    r"|(?P<word>\b[A-Z][A-Z0-9_]*\b)(?=\s*\()",
    re.DOTALL,
)

_TOKEN_CLASS = {
    "comment": "tok-comment", "string": "tok-string", "ref": "tok-ref",
    "number": "tok-number", "word": "tok-keyword",
}


def highlight_dax(expr: str | None) -> str:
    """Escaped, syntax-highlighted HTML for a raw DAX expression. Safe to
    insert directly (already escapes every character of the input)."""
    if not expr:
        return ""
    out: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(expr):
        kind = m.lastgroup
        if kind == "word" and m.group("word") not in _KEYWORDS:
            continue
        if m.start() > pos:
            out.append(_escape(expr[pos:m.start()]))
        out.append(f'<span class="{_TOKEN_CLASS[kind]}">{_escape(m.group())}</span>')
        pos = m.end()
    out.append(_escape(expr[pos:]))
    return "".join(out)
