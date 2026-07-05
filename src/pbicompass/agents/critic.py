from __future__ import annotations

import json
from typing import Optional
from ..llm import LLMClient

STYLE_RULES = """
Editorial guidelines for clean enterprise documentation:
1. Avoid banned marketing buzzwords: revolutionary, disruptive, next-gen, synergy, state-of-the-art, paradigm shift.
2. Avoid generic name-echo prose (e.g. 'Total Sales calculates the total sales' or 'Active users shows active users'). Explain *how* or *why*.
3. Do not include duplicated sentences back-to-back.
4. Verify that any objects mentioned (measures, tables, pages) exist in the model.
"""

CRITIC_SYSTEM = f"""You are an expert technical editor. Review the generated documentation text against the style rules and output any violations.
For each violation, provide the exact quote to be replaced and a suggested fix that resolves the issue.

{STYLE_RULES}
"""

CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "violations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "quote": {"type": "string"},
                    "rule": {"type": "string"},
                    "suggested_fix": {"type": "string"}
                },
                "required": ["quote", "rule", "suggested_fix"]
            }
        }
    },
    "required": ["violations"]
}


def run_critic_pass(doc_text: str, client: Optional[LLMClient]) -> str:
    """Run LLM critic pass over the rendered document text to detect and auto-fix style issues.
    
    If offline (client is None), skips silently.
    """
    if client is None:
        return doc_text

    try:
        # Call the LLM to identify style violations
        # We pass the doc_text as the user prompt
        response = client.complete_json(CRITIC_SYSTEM, doc_text, CRITIC_SCHEMA)
        if not response or "violations" not in response:
            return doc_text

        violations = response.get("violations", [])
        modified_text = doc_text
        for v in violations:
            quote = v.get("quote", "").strip()
            fix = v.get("suggested_fix", "").strip()
            if quote and quote in modified_text:
                modified_text = modified_text.replace(quote, fix)
        return modified_text
    except Exception:
        # Safe fallback: return original text if the critic fails
        return doc_text
