"""Shared HTML page shell — doctype, fonts, CSS, sidebar/TOC, header card,
KPI strip, and the scroll-spy script — reused by every document-type HTML
renderer (technical, audit, and future executive/user-guide renderers).

Extracted out of ``html.py`` so new HTML renderers don't re-author a full
HTML document from scratch; they only need to build their own section body
HTML and hand it to :func:`page_shell`.
"""

from __future__ import annotations

from ._shared import html_e as _e

_CSS = """
:root {
  --font-sans: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  --bg-main: #f8fafc;
  --bg-card: #ffffff;
  --text-main: #0f172a;
  --text-muted: #475569;
  --text-faint: #94a3b8;
  --border-color: #e2e8f0;
  --primary: #4f46e5;
  --primary-hover: #4338ca;
  --primary-light: #eef2ff;
  --secondary: #0ea5e9;
  --success: #10b981;
  --success-light: #ecfdf5;
  --warning: #f59e0b;
  --warning-light: #fef3c7;
  --danger: #ef4444;
  --danger-light: #fef2f2;
  --code-bg: #0f172a;
  --sidebar-w: 280px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  color: var(--text-main);
  background-color: var(--bg-main);
  line-height: 1.6;
  display: flex;
  min-height: 100vh;
}

/* Sidebar styling */
.sidebar {
  width: var(--sidebar-w);
  background: var(--bg-card);
  border-right: 1px solid var(--border-color);
  padding: 32px 20px;
  position: fixed;
  top: 0;
  bottom: 0;
  left: 0;
  overflow-y: auto;
  z-index: 100;
}
.sidebar-logo {
  font-weight: 800;
  font-size: 1.3rem;
  color: var(--primary);
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 28px;
  letter-spacing: -0.02em;
}
.sidebar-logo svg {
  width: 26px;
  height: 26px;
  fill: currentColor;
}
.toc-list {
  list-style: none;
}
.toc-item {
  margin-bottom: 4px;
}
.toc-link {
  display: block;
  padding: 8px 12px;
  color: var(--text-muted);
  text-decoration: none;
  font-size: 0.85rem;
  font-weight: 500;
  border-radius: 6px;
  transition: all 0.15s ease;
}
.toc-link:hover {
  background: var(--primary-light);
  color: var(--primary);
}
.toc-link.active {
  background: var(--primary-light);
  color: var(--primary);
  font-weight: 600;
}

/* Main Content Area */
.content-wrapper {
  margin-left: var(--sidebar-w);
  flex-grow: 1;
  padding: 48px 56px;
  max-width: calc(100vw - var(--sidebar-w));
}
.main-content {
  max-width: 900px;
  margin: 0 auto;
}

/* Header Cards */
.header-card {
  background: linear-gradient(135deg, #1e1b4b 0%, #311042 100%);
  color: #ffffff;
  border-radius: 16px;
  padding: 44px;
  margin-bottom: 32px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.05);
}
.header-card::before {
  content: '';
  position: absolute;
  top: -50%;
  right: -20%;
  width: 350px;
  height: 350px;
  background: radial-gradient(circle, rgba(79, 70, 229, 0.3) 0%, rgba(0,0,0,0) 70%);
  border-radius: 50%;
  pointer-events: none;
}
.header-card h1 {
  font-size: 2.2rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  margin-bottom: 8px;
  line-height: 1.2;
}
.header-card .subtitle {
  color: rgba(255, 255, 255, 0.75);
  font-size: 0.98rem;
  margin: 0;
  font-weight: 400;
}

/* KPIs / Stats grid */
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 16px;
  margin-bottom: 36px;
}
.kpi {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  text-align: left;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.kpi:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}
.kpi .n {
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--primary);
  line-height: 1.2;
}
.kpi .l {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-top: 4px;
}

/* Typography & Section Styles */
h2 {
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--text-main);
  margin: 44px 0 18px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--border-color);
  letter-spacing: -0.02em;
  scroll-margin-top: 24px;
}
h3 {
  font-size: 1.08rem;
  font-weight: 600;
  color: var(--text-main);
  margin: 24px 0 12px;
}
p {
  margin-bottom: 16px;
  color: var(--text-muted);
  font-size: 0.94rem;
}
ul, ol {
  margin-left: 20px;
  margin-bottom: 16px;
  color: var(--text-muted);
  font-size: 0.94rem;
}
li {
  margin-bottom: 6px;
}

/* Card-style Containers */
.card-section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 24px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.02);
}

/* Tables */
table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  margin: 16px 0 24px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
}
th, td {
  padding: 10px 14px;
  text-align: left;
  vertical-align: middle;
  font-size: 0.86rem;
}
th {
  background-color: #f8fafc;
  font-weight: 600;
  color: var(--text-main);
  border-bottom: 1px solid var(--border-color);
  text-transform: uppercase;
  font-size: 0.72rem;
  letter-spacing: 0.05em;
}
td {
  border-bottom: 1px solid var(--border-color);
  color: var(--text-muted);
  background: var(--bg-card);
}
tr:last-child td {
  border-bottom: none;
}
tr:hover td {
  background-color: #fafbfd;
}
td.num {
  font-family: monospace;
  font-weight: 500;
}

/* Code & Pre */
pre {
  background: var(--code-bg);
  color: #e2e8f0;
  border-radius: 8px;
  padding: 14px;
  overflow-x: auto;
  margin: 12px 0 20px;
}
code {
  font-family: Consolas, "SF Mono", Menlo, monospace;
  font-size: 0.82rem;
  background: #f1f5f9;
  color: #0f172a;
  padding: 2px 6px;
  border-radius: 4px;
}
pre code {
  background: transparent;
  color: inherit;
  padding: 0;
}

/* Badges & Pills */
.pill {
  display: inline-block;
  background: var(--primary-light);
  color: var(--primary);
  font-size: 0.7rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 12px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  vertical-align: middle;
  margin-left: 6px;
}
.pill.pass { background: var(--success-light); color: #067647; }
.pill.fail { background: var(--danger-light); color: #b42318; }
.pill.critical { background: var(--danger-light); color: #b42318; }
.pill.high { background: var(--warning-light); color: #b45309; }
.pill.medium { background: var(--primary-light); color: var(--primary); }
.pill.low { background: #f1f5f9; color: var(--text-muted); }

/* Todo items */
.todo {
  border: 1px dashed #fbbf24;
  background-color: #fffbeb;
  color: #b45309;
  border-radius: 8px;
  padding: 12px 16px;
  font-size: 0.86rem;
  margin: 16px 0;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.todo b {
  font-weight: 700;
}

/* Risk / Warning Alerts */
.risk {
  background-color: #fef2f2;
  border-left: 4px solid var(--danger);
  border-radius: 4px;
  padding: 12px 16px;
  margin: 12px 0;
  font-size: 0.86rem;
  color: #991b1b;
}

/* Caveat / Notes */
.caveat {
  font-size: 0.82rem;
  color: var(--text-muted);
  background: #f1f5f9;
  border-left: 3px solid var(--text-faint);
  padding: 6px 12px;
  border-radius: 0 4px 4px 0;
  margin: 8px 0;
}

/* Diagram styling */
.diagram {
  background: #ffffff;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  margin: 16px 0;
}
.legend {
  font-size: 0.72rem;
  color: var(--text-muted);
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 12px;
}
.legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.swatch {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  display: inline-block;
}

/* Measure catalog entries */
.measure {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 20px;
  margin-bottom: 20px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.01);
}
.measure h3 {
  margin: 0 0 10px;
  font-size: 1.1rem;
}
.usedon {
  font-size: 0.76rem;
  color: var(--text-faint);
  margin-top: 6px;
  margin-bottom: 12px;
}

/* Score ring / big number (audit report) */
.score-hero {
  display: flex;
  align-items: center;
  gap: 28px;
  flex-wrap: wrap;
}
.score-big {
  font-size: 3.2rem;
  font-weight: 800;
  line-height: 1;
  color: var(--primary);
}
.score-band {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* Responsiveness & Print settings */
@media (max-width: 1024px) {
  .sidebar {
    display: none;
  }
  .content-wrapper {
    margin-left: 0;
    max-width: 100%;
    padding: 32px 24px;
  }
}

@media print {
  body {
    background-color: #ffffff;
    display: block;
  }
  .sidebar {
    display: none;
  }
  .content-wrapper {
    margin-left: 0;
    padding: 0;
    max-width: 100%;
  }
  h2 {
    page-break-before: always;
  }
  h2:first-of-type {
    page-break-before: avoid;
  }
  pre, table, .measure, .diagram, .card-section {
    page-break-inside: avoid;
  }
}
"""

_SCRIPT = """
<script>
document.addEventListener('DOMContentLoaded', () => {
  const links = document.querySelectorAll('.toc-link');
  const sections = document.querySelectorAll('h2[id]');

  function changeActiveLink() {
    let index = sections.length;
    while(--index && window.scrollY + 100 < sections[index].offsetTop) {}
    links.forEach((link) => link.classList.remove('active'));
    if (sections[index]) {
      const activeLink = document.querySelector(`.toc-link[href="#${sections[index].id}"]`);
      if (activeLink) activeLink.classList.add('active');
    }
  }

  changeActiveLink();
  window.addEventListener('scroll', changeActiveLink);
});
</script>
"""

_LOGO_SVG = (
    '<svg viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2z'
    'm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z"/></svg>'
)


def page_shell(
    *,
    title: str,
    subtitle: str,
    toc: list[tuple[str, str]],
    kpis: list[tuple[str, str]],
    body_html: str,
) -> str:
    """Wrap ``body_html`` (a renderer's own section content) in the full HTML
    document: doctype, head/fonts/CSS, sidebar TOC, header card with
    title/subtitle/KPIs, and the closing scroll-spy script.

    ``toc`` is a list of ``(anchor_id, label)`` pairs. ``kpis`` is a list of
    ``(label, value)`` pairs — pass an empty list to omit the KPI strip.
    """
    o: list[str] = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">']
    o.append(f"<title>{_e(title)} — Documentation</title>")
    o.append('<link rel="preconnect" href="https://fonts.googleapis.com">')
    o.append('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
    o.append('<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">')
    o.append(f"<style>{_CSS}</style></head><body>")

    o.append('<div class="sidebar">')
    o.append('<div class="sidebar-logo">')
    o.append(_LOGO_SVG)
    o.append('<span>PBICompass</span>')
    o.append('</div>')
    o.append('<ul class="toc-list">')
    for sec_id, sec_title in toc:
        o.append(f'<li class="toc-item"><a href="#{sec_id}" class="toc-link">{_e(sec_title)}</a></li>')
    o.append('</ul></div>')

    o.append('<div class="content-wrapper">')
    o.append('<div class="main-content">')

    o.append('<div class="header-card">')
    o.append(f"<h1>{_e(title)}</h1>")
    o.append(f'<p class="subtitle">{_e(subtitle)}</p>')
    o.append("</div>")

    if kpis:
        o.append('<div class="kpis">')
        for label, value in kpis:
            o.append(f'<div class="kpi"><div class="n">{_e(value)}</div><div class="l">{_e(label)}</div></div>')
        o.append("</div>")

    o.append(body_html)

    o.append("</div></div>")
    o.append(_SCRIPT)
    o.append("</body></html>")
    return "\n".join(o)
