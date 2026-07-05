"""Documentation hub — one ``index.html`` linking every document type
generated in the same job/run.

Deliberately naming-agnostic: callers supply the actual relative href for
each sibling doc (``entries[i]["href"]``) rather than this module assuming a
fixed filename convention, since the CLI (``{stem}.{type}.html``) and the
hosted service (``{upload-name}.{type}.{fmt}``, via the download endpoint)
name files differently. Only built where the caller can promise those hrefs
stay valid next to each other on disk (today: the CLI's multi-document
``-o`` path; the hosted service needs the zip-bundle work (5.7) before its
per-job download names are fixed enough to link between).
"""

from __future__ import annotations

from ._shared import html_e as _e

_CSS = """
:root {
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  --bg-main: #f8fafc;
  --bg-card: #ffffff;
  --text-main: #0f172a;
  --text-muted: #475569;
  --text-faint: #64748b;
  --border-color: #e2e8f0;
  --primary: #4f46e5;
  --primary-light: #eef2ff;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg-main: #0b1220; --bg-card: #16213a; --text-main: #e2e8f0;
    --text-muted: #94a3b8; --text-faint: #64748b; --border-color: #2a3a56;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  color: var(--text-main);
  background: var(--bg-main);
  line-height: 1.6;
  padding: 48px 24px;
}
.wrap { max-width: 960px; margin: 0 auto; }
.hub-header {
  background: linear-gradient(135deg, #1e1b4b 0%, #311042 100%);
  color: #fff;
  border-radius: 16px;
  padding: 40px 44px;
  margin-bottom: 32px;
}
.hub-header h1 { font-size: 2rem; font-weight: 800; margin-bottom: 6px; }
.hub-header p { color: rgba(255,255,255,0.78); font-size: 0.95rem; }
.health-dial {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  margin-top: 16px;
  background: rgba(255,255,255,0.1);
  border-radius: 10px;
  padding: 10px 16px;
}
.health-dial .score { font-size: 1.8rem; font-weight: 800; }
.health-dial .band { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; color: rgba(255,255,255,0.75); }
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 20px;
}
.card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 14px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  text-decoration: none;
  color: inherit;
  box-shadow: 0 1px 3px rgba(0,0,0,0.03);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.card:hover {
  transform: translateY(-3px);
  box-shadow: 0 8px 20px rgba(0,0,0,0.08);
  border-color: var(--primary);
}
.card h2 { font-size: 1.15rem; color: var(--text-main); }
.card p { font-size: 0.86rem; color: var(--text-muted); }
.card .stats {
  display: flex;
  gap: 16px;
  font-size: 0.78rem;
  color: var(--text-faint);
}
.card .open {
  margin-top: auto;
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--primary);
}
.footer-note {
  margin-top: 32px;
  font-size: 0.78rem;
  color: var(--text-faint);
  text-align: center;
}
"""

_DOC_LABELS = {
    "technical": "Technical Documentation",
    "audit": "Audit & Health Report",
    "executive": "Executive Summary",
    "user-guide": "Business User Guide",
}
_DOC_BLURBS = {
    "technical": "Full data model, DAX dictionary, lineage, and security — for BI developers.",
    "audit": "Health score, best-practice checks, and prioritized recommendations.",
    "executive": "A two-minute read for managers and project owners.",
    "user-guide": "How to use the report — no technical background needed.",
}


def render_hub(
    entries: list[dict],
    *,
    report_name: str,
    generated_at: str,
    health_score: dict | None = None,
) -> str:
    """``entries``: one dict per generated doc type —
    ``{"type": str, "href": str, "stats": [(label, value), ...]}``. Labels
    and one-line blurbs are filled in from the known document types."""
    cards = []
    for e in entries:
        dtype = e["type"]
        label = _DOC_LABELS.get(dtype, dtype.title())
        blurb = _DOC_BLURBS.get(dtype, "")
        stats_html = "".join(f"<span>{_e(v)} {_e(k)}</span>" for k, v in e.get("stats", []))
        cards.append(
            f'<a class="card" href="{_e(e["href"])}">'
            f"<h2>{_e(label)}</h2>"
            f"<p>{_e(blurb)}</p>"
            + (f'<div class="stats">{stats_html}</div>' if stats_html else "")
            + '<span class="open">Open →</span></a>'
        )

    dial = ""
    if health_score:
        dial = (
            '<div class="health-dial">'
            f'<span class="score">{_e(health_score.get("overall", 0))}/100</span>'
            f'<span class="band">{_e(health_score.get("band", ""))}</span>'
            "</div>"
        )

    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        f"<title>{_e(report_name)} — Documentation Hub</title>"
        f"<style>{_CSS}</style></head><body>"
        '<div class="wrap">'
        '<div class="hub-header">'
        f"<h1>{_e(report_name)}</h1>"
        f"<p>Documentation generated {_e(generated_at)}</p>"
        f"{dial}"
        "</div>"
        f'<div class="cards">{"".join(cards)}</div>'
        '<p class="footer-note">Generated by PBICompass — opens fully offline, no external calls.</p>'
        "</div></body></html>"
    )
