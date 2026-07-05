from __future__ import annotations
import re

def _strip_m_comments(m_expression: str) -> str:
    """Remove // line comments and /* block */ comments in one linear pass.

    Tracks quote state so a `//` or `/*` inside a string literal (e.g. a URL
    in ``Web.Contents("http://...")``) is left alone. A naive two-pass regex
    approach (stripping comments before splitting on quotes) both corrupts
    such strings and is quadratic on adversarial unterminated `/*` input, so
    this does both jobs — quote-awareness and linear-time scanning — in a
    single pass instead.
    """
    out: list[str] = []
    i = 0
    n = len(m_expression)
    in_quotes = False
    while i < n:
        char = m_expression[i]
        if in_quotes:
            out.append(char)
            if char == '"':
                in_quotes = False
            i += 1
            continue
        if char == '"':
            in_quotes = True
            out.append(char)
            i += 1
        elif char == "/" and i + 1 < n and m_expression[i + 1] == "/":
            i += 2
            while i < n and m_expression[i] != "\n":
                i += 1
        elif char == "/" and i + 1 < n and m_expression[i + 1] == "*":
            i += 2
            while i + 1 < n and not (m_expression[i] == "*" and m_expression[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
        else:
            out.append(char)
            i += 1
    return "".join(out)

def split_m_steps(m_expression: str) -> list[tuple[str, str]]:
    """Parse a Power Query let...in block and return a list of (step_name, step_expression)."""
    if not m_expression:
        return []

    m_clean = _strip_m_comments(m_expression).strip()
    if not m_clean.lower().startswith("let"):
        return []
    
    in_match = list(re.finditer(r"\bin\b", m_clean, re.IGNORECASE))
    if not in_match:
        return []
        
    last_in = in_match[-1]
    let_body = m_clean[3:last_in.start()].strip()
    
    steps = []
    current_step_expr = []
    
    i = 0
    in_quotes = False
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    
    while i < len(let_body):
        char = let_body[i]
        if char == '"':
            in_quotes = not in_quotes
        elif not in_quotes:
            if char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth = max(0, paren_depth - 1)
            elif char == '[':
                bracket_depth += 1
            elif char == ']':
                bracket_depth = max(0, bracket_depth - 1)
            elif char == '{':
                brace_depth += 1
            elif char == '}':
                brace_depth = max(0, brace_depth - 1)
            elif char == ',' and paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
                step_str = "".join(current_step_expr).strip()
                if "=" in step_str:
                    name, expr = step_str.split("=", 1)
                    steps.append((name.strip().strip('#"'), expr.strip()))
                current_step_expr = []
                i += 1
                continue
        current_step_expr.append(char)
        i += 1
        
    step_str = "".join(current_step_expr).strip()
    if "=" in step_str:
        name, expr = step_str.split("=", 1)
        steps.append((name.strip().strip('#"'), expr.strip()))
        
    return steps

def classify_step_function(step_expr: str) -> str:
    """Classify a step by its function."""
    expr_upper = step_expr.upper()
    
    if any(k in expr_upper for k in ("SQL.DATABASE", "EXCEL.WORKBOOK", "CSV.DOCUMENT", "WEB.CONTENTS", "FILE.CONTENTS", "FOLDER.FILES")):
        return "Source Connection"
    if any(k in expr_upper for k in ("{[SCHEMA=", "ITEM=", "NAVIGATION")):
        return "Navigation / Selection"
    if "TABLE.TRANSFORMCOLUMNTYPES" in expr_upper:
        return "Changed Column Types"
    if "TABLE.REMOVECOLUMNS" in expr_upper:
        return "Removed Columns"
    if "TABLE.SELECTROWS" in expr_upper:
        return "Filtered Rows"
    if "TABLE.RENAMECOLUMNS" in expr_upper:
        return "Renamed Columns"
    if "TABLE.ADDCOLUMN" in expr_upper:
        return "Added Custom Column"
    if any(k in expr_upper for k in ("TABLE.NESTEDJOIN", "TABLE.JOIN")):
        return "Merged Queries / Joins"
    if "TABLE.EXPANDTABLECOLUMN" in expr_upper:
        return "Expanded Table Column"
    if "TABLE.GROUP" in expr_upper:
        return "Grouped Rows"
    if "TABLE.REPLACEVALUE" in expr_upper:
        return "Replaced Value"
    if "TABLE.DUPLICATECOLUMN" in expr_upper:
        return "Duplicated Column"
    
    return "Custom Transformation"
