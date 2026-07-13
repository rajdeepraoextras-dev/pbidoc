"""Shared HTML page shell — doctype, fonts, CSS, sidebar/TOC, header card,
KPI strip, and the scroll-spy script — reused by every document-type HTML
renderer (technical, audit, and future executive/user-guide renderers).

Extracted out of ``html.py`` so new HTML renderers don't re-author a full
HTML document from scratch; they only need to build their own section body
HTML and hand it to :func:`page_shell`.
"""

from __future__ import annotations

import json

from ._shared import html_e as _e
from ._logo import LOGO_DATA_URI
from ._poppins_font import POPPINS_FONT_FACES_CSS, POPPINS_FONT_STACK
from ._vendor_svg_pan_zoom import SVG_PAN_ZOOM_JS

_CSS = POPPINS_FONT_FACES_CSS + """
:root {
  --font-sans: """ + POPPINS_FONT_STACK + """;
  --bg-main: #f8fafc;
  --bg-card: #ffffff;
  --bg-hover: #fafbfd;
  --bg-code-inline: #f1f5f9;
  --text-main: #0f172a;
  --text-muted: #475569;
  --text-faint: #64748b;
  --border-color: #e2e8f0;
  --primary: #124fed;
  --primary-hover: #0e3db8;
  --primary-light: #eef2fd;
  --brand-text: var(--primary);
  --logo-filter: none;
  --secondary: #0ea5e9;
  --success: #10b981;
  --success-light: #ecfdf5;
  --warning: #f59e0b;
  --warning-light: #fef3c7;
  --danger: #ef4444;
  --danger-light: #fef2f2;
  --code-bg: #0f172a;
  --code-text: #e2e8f0;
  --sidebar-w: 280px;
}

/* Dark mode: system preference by default, explicit override via the
   sidebar toggle (persisted to localStorage as data-theme on <html>). Print
   always forces light regardless (see the @media print block below). */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg-main: #0b1220;
    --bg-card: #16213a;
    --bg-hover: #1c2942;
    --bg-code-inline: #1c2942;
    --text-main: #e2e8f0;
    --text-muted: #94a3b8;
    --text-faint: #64748b;
    --border-color: #2a3a56;
    --code-text: #e2e8f0;
    --brand-text: #ffffff;
    --logo-filter: brightness(0) invert(1);
  }
}
:root[data-theme="dark"] {
  --bg-main: #0b1220;
  --bg-card: #16213a;
  --bg-hover: #1c2942;
  --bg-code-inline: #1c2942;
  --text-main: #e2e8f0;
  --text-muted: #94a3b8;
  --text-faint: #64748b;
  --border-color: #2a3a56;
  --code-text: #e2e8f0;
  --brand-text: #ffffff;
  --logo-filter: brightness(0) invert(1);
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  color: var(--text-main);
  background-color: var(--bg-main);
  line-height: 1.6;
  display: flex;
  min-height: 100vh;
  /* D3 (mobile 390px, no horizontal page scroll): narrative prose can
     legitimately embed a long unbreakable token — a hardcoded file path,
     a URL — inherited onto every descendant so a single sentence with a
     literal Dropbox path can't force the whole page wider than the
     viewport the way normal word-wrapping text never would. */
  overflow-wrap: break-word;
}

.visually-hidden {
  position: absolute;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.skip-link {
  position: absolute;
  top: -50px;
  left: 12px;
  z-index: 300;
  background: var(--primary);
  color: #ffffff;
  padding: 10px 16px;
  border-radius: 0 0 8px 8px;
  text-decoration: none;
  font-weight: 600;
  font-size: 0.85rem;
  transition: top 0.15s ease;
}
.skip-link:focus {
  top: 0;
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
  color: var(--brand-text);
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 28px;
  letter-spacing: -0.02em;
}
.sidebar-logo img {
  height: 28px;
  width: auto;
  display: block;
  filter: var(--logo-filter);
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

/* Sidebar search */
.search-box {
  position: relative;
  margin-bottom: 16px;
}
.search-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-main);
  color: var(--text-main);
  font-size: 0.82rem;
  font-family: inherit;
}
.search-input:focus {
  outline: 2px solid var(--primary);
  outline-offset: 1px;
}
.search-results {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 250;
  list-style: none;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  max-height: 320px;
  overflow-y: auto;
}
.search-result {
  padding: 8px 12px;
  cursor: pointer;
  font-size: 0.82rem;
  color: var(--text-main);
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
}
.search-result .type {
  font-size: 0.68rem;
  text-transform: uppercase;
  color: var(--text-faint);
  letter-spacing: 0.04em;
  flex-shrink: 0;
}
.search-result:hover, .search-result.active {
  background: var(--primary-light);
  color: var(--primary);
}
.search-empty {
  padding: 8px 12px;
  font-size: 0.8rem;
  color: var(--text-faint);
}

/* Doc-switcher — links to sibling documents (and the hub) generated in the
   same job. Only rendered when there are siblings to link to. */
.doc-switcher {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-color);
}
.doc-switcher a {
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--text-muted);
  text-decoration: none;
}
.doc-switcher a:hover {
  background: var(--primary-light);
  color: var(--primary);
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
  background: linear-gradient(135deg, #1a2740 0%, #124fed 100%);
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
  background: radial-gradient(circle, rgba(18, 79, 237, 0.35) 0%, rgba(0,0,0,0) 70%);
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

/* Tables — wrapped in .table-scroll at runtime (see _SCRIPT) so a wide
   table (long file paths, unbroken DAX in a cell) scrolls within its own
   box instead of forcing the whole page wider than the viewport. Table
   layout is auto (browser sizes columns to content), so the table itself
   can't self-limit to its 100% width without this outer scroll boundary. */
.table-scroll {
  overflow-x: auto;
  margin: 16px 0 24px;
}
table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  margin: 0;
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
  background-color: var(--bg-main);
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
  background-color: var(--bg-hover);
}
td.num {
  font-family: monospace;
  font-weight: 500;
}

/* Code & Pre */
pre {
  background: var(--code-bg);
  color: var(--code-text);
  border-radius: 8px;
  padding: 14px;
  overflow-x: auto;
  margin: 12px 0 20px;
}
code {
  font-family: Consolas, "SF Mono", Menlo, monospace;
  font-size: 0.82rem;
  background: var(--bg-code-inline);
  color: var(--text-main);
  padding: 2px 6px;
  border-radius: 4px;
}
pre code {
  background: transparent;
  color: inherit;
  padding: 0;
}

/* DAX/M syntax highlighting — fixed palette against the always-dark
   --code-bg, independent of page theme. */
.tok-keyword { color: #c4b5fd; font-weight: 700; }
.tok-string { color: #86efac; }
.tok-number { color: #fca5a5; }
.tok-ref { color: #7dd3fc; }
.tok-comment { color: #64748b; font-style: italic; }

/* Copy-to-clipboard button on each code block */
.code-block {
  position: relative;
}
.code-block .copy-btn {
  position: absolute;
  top: 10px;
  right: 10px;
  background: rgba(255, 255, 255, 0.08);
  color: #e2e8f0;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
}
.code-block .copy-btn:hover {
  background: rgba(255, 255, 255, 0.18);
}

/* Collapsible long content (>10-line DAX, full M queries, unused-asset
   groups) — collapsed by default on screen; forced open for print/PDF via
   JS (window.onbeforeprint) with a no-JS CSS fallback below. */
details.collapsible {
  border: 1px solid var(--border-color);
  border-radius: 8px;
  margin: 12px 0 20px;
  background: var(--bg-card);
}
details.collapsible > summary {
  cursor: pointer;
  padding: 10px 14px;
  color: var(--text-main);
  font-size: 0.86rem;
  font-weight: 600;
  list-style: none;
}
details.collapsible > summary::-webkit-details-marker {
  display: none;
}
details.collapsible > summary::before {
  content: '▶ ';
  color: var(--text-faint);
}
details.collapsible[open] > summary::before {
  content: '▼ ';
}
details.collapsible > summary:hover {
  color: var(--primary);
}
details.collapsible > .code-block,
details.collapsible > .collapsible-body {
  margin: 0 14px 14px;
}
details.collapsible > .code-block pre {
  margin: 0;
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
.pill.low { background: var(--bg-code-inline); color: var(--text-muted); }
.pill.extracted { background: #e0f2fe; color: #0369a1; text-transform: none; }
.pill.ai-inferred { background: #fae8ff; color: #a21caf; text-transform: none; }
.pill.human-provided { background: #dcfce7; color: #15803d; text-transform: none; }
.pill.rule-id {
  background: var(--bg-code-inline);
  color: var(--text-faint);
  font-family: Consolas, "SF Mono", Menlo, monospace;
  text-transform: none;
  letter-spacing: 0;
  margin-left: 8px;
}
.pill.suppressed { background: var(--bg-code-inline); color: var(--text-faint); text-transform: none; }

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
  background: var(--bg-code-inline);
  border-left: 3px solid var(--text-faint);
  padding: 6px 12px;
  border-radius: 0 4px 4px 0;
  margin: 8px 0;
}

/* Diagram styling. The SVG's own box/text fills stay light-theme (readable
   against the white canvas below) regardless of page theme — the canvas
   itself is intentionally kept white so the diagram never needs a second
   themed color set. */
.diagram {
  background: #ffffff;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  margin: 16px 0;
  overflow: hidden;
}
.diagram svg {
  cursor: grab;
  touch-action: none;
  user-select: none; /* a pan drag must never also text-select the page underneath it */
}
.diagram svg text {
  font-family: 'Poppins', sans-serif !important;
}
.diagram svg:active {
  cursor: grabbing;
}
.dm-node {
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.dm-edge {
  transition: opacity 0.15s ease;
}
.dm-node.dimmed, .dm-edge.dimmed {
  opacity: 0.2;
}
.dm-edge.highlighted line {
  stroke-width: 3;
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
.diagram-hint {
  width: 100%;
  margin-top: 4px;
  opacity: 0.8;
}
/* Page wireframe + lineage (v6 "Studio", 2026-07-11) — a white card per
   node with a layered soft shadow, gradient icon chip, and hover-lift.
   Hover feedback lives entirely here (a .wf-node class) instead of a
   per-rect style=/onmouseover= attribute. Fixed light hex (not shell CSS
   variables) so wireframe/lineage cards never theme-flip in dark mode —
   same rule as the always-light canvas underneath them. */
.wf-node {
  cursor: pointer;
  transition: transform 0.18s ease, filter 0.18s ease, opacity 0.18s ease;
  filter: drop-shadow(0 1px 1.5px rgba(31,36,51,.06)) drop-shadow(0 2px 6px rgba(31,36,51,.05));
}
.wf-node:hover {
  transform: translateY(-2px);
  filter: drop-shadow(0 2px 3px rgba(31,36,51,.08)) drop-shadow(0 8px 18px rgba(31,36,51,.12));
}
/* Lineage hover-connect: the shell script dims everything not connected to
   the hovered node and thickens its own gradient edges. */
.wf-node.dimmed { opacity: 0.22; }
.lg-edge { transition: opacity 0.18s ease; }
.lg-edge path { transition: stroke-width 0.18s ease, opacity 0.18s ease; }
.lg-edge.dimmed { opacity: 0.08; }
.lg-edge.hl path { opacity: 1; stroke-width: 2.4; }
/* Wireframe page-tab bar: sibling-page ghost tabs tint on hover. */
.wf-tab text { transition: fill 0.15s ease; }
.wf-tab:hover text, a.wf-tab:focus-visible text { fill: #4f6ef7; }
/* Hover/keyboard-focus border tint, per category — a fixed approximation
   of a 40% accent/edge blend (not CSS color-mix(), for older print/PDF
   engine safety). .wf-node:hover alone covers both linked (data/slicer,
   wrapped in <a>) and unlinked (nav/decorative) cards; :focus-visible only
   ever applies to the linked ones, since a plain <g> isn't tabbable. */
.wf-node.cat-data:hover .wf-card-bg, a:focus-visible > .wf-node.cat-data .wf-card-bg { stroke: #aab8f5; stroke-width: 1.4; }
.wf-node.cat-slicer:hover .wf-card-bg, a:focus-visible > .wf-node.cat-slicer .wf-card-bg { stroke: #edcc96; stroke-width: 1.4; }
.wf-node.cat-nav:hover .wf-card-bg, a:focus-visible > .wf-node.cat-nav .wf-card-bg { stroke: #91d6c5; stroke-width: 1.4; }
.wf-node.cat-decorative:hover .wf-card-bg, a:focus-visible > .wf-node.cat-decorative .wf-card-bg { stroke: #c2b1f4; stroke-width: 1.4; }
.wf-node.cat-source:hover .wf-card-bg, a:focus-visible > .wf-node.cat-source .wf-card-bg { stroke: #c2b1f4; stroke-width: 1.4; }
.wf-node.cat-table:hover .wf-card-bg, a:focus-visible > .wf-node.cat-table .wf-card-bg { stroke: #aab8f5; stroke-width: 1.4; }
.wf-node.cat-measure:hover .wf-card-bg, a:focus-visible > .wf-node.cat-measure .wf-card-bg { stroke: #edcc96; stroke-width: 1.4; }
.wf-node.cat-page:hover .wf-card-bg, a:focus-visible > .wf-node.cat-page .wf-card-bg { stroke: #91d6c5; stroke-width: 1.4; }
/* Dimension tag (real box pixel size) — hidden until the card is hovered
   or keyboard-focused, matching v4's own hover-reveal treatment. */
.wf-tag {
  opacity: 0;
  transition: opacity 0.18s ease;
}
.wf-node:hover .wf-tag, a:focus-visible > .wf-node .wf-tag {
  opacity: 1;
}
.wf-footer {
  font-size: 0.72rem;
  color: var(--text-muted);
  margin-top: 6px;
  opacity: 0.8;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
/* Day 6: the callout list under a wireframe for visuals that render as a
   ghost outline + numbered chip on the canvas because another, larger
   visual sits on top of them in the actual report layout. */
.wf-occluded-list {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-color);
}
.wf-occluded-title {
  font-size: 0.74rem;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 4px;
}
.wf-occluded-list ol {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 0.78rem;
}
.wf-occluded-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 3px;
  margin-right: 6px;
  border-radius: 999px;
  background: var(--bg-code-inline);
  color: var(--text-muted);
  font-size: 0.68rem;
  font-weight: 700;
}
.wf-occluded-list a {
  color: var(--primary);
  text-decoration: none;
}
.wf-occluded-list a:hover {
  text-decoration: underline;
}
/* Uppercase legend for the wireframe/lineage only (a modifier, so the
   shared model/nav-map/measure-deps legends keep their normal case). */
.legend--upper {
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
/* Rounded pill "chips" (v4) for the wireframe/lineage legend, replacing a
   plain swatch square — one shared style, reused by both diagrams since
   they carry the same four accent colors (source/decorative = purple,
   table/data = blue, measure/slicer = amber, page/nav = green). */
.wf-legend {
  gap: 8px;
}
.wf-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  background: #ffffff;
  border: 1px solid #e7eaf3;
  border-radius: 999px;
  padding: 5px 12px;
  box-shadow: 0 1px 2px rgba(31,36,51,.05);
}
.wf-chip-dot {
  width: 7px;
  height: 7px;
  border-radius: 2px;
  display: inline-block;
}
.wf-chip-dot--data, .wf-chip-dot--table { background: #4f6ef7; }
.wf-chip-dot--slicer, .wf-chip-dot--measure { background: #f59e0b; }
.wf-chip-dot--nav, .wf-chip-dot--page { background: #10b981; }
.wf-chip-dot--deco, .wf-chip-dot--source { background: #8b5cf6; }

/* Print cover page + watermark — hidden on screen entirely; only exist for
   @media print / print-to-PDF (2.8). */
.print-cover, .print-watermark {
  display: none;
}
.print-cover-mark {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 800;
  font-size: 1.1rem;
  letter-spacing: -0.02em;
  color: var(--primary);
}
.print-cover-mark img {
  height: 30px;
  width: auto;
}
.print-cover h1 {
  font-size: 2.4rem;
  font-weight: 800;
  margin: 32px 0 8px;
}
.print-cover-subtitle {
  color: var(--text-muted);
  font-size: 1rem;
  margin-bottom: 40px;
}
.print-cover-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 32px;
  max-width: 480px;
  font-size: 0.92rem;
}
.print-cover-meta dt {
  color: var(--text-faint);
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.print-cover-meta dd {
  color: var(--text-main);
  font-weight: 600;
}
.print-cover-classification {
  display: inline-block;
  margin-top: 32px;
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 0.8rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  background: var(--danger-light);
  color: #991b1b;
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

/* Executive doc: compact health-score band chip + component mini-bars
   (Day 5 boardroom-grade pass) — a condensed version of the audit report's
   score-hero, scannable in a few seconds rather than a full table. */
.band-chip {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.band-chip.excellent, .band-chip.good { background: var(--success-light); color: #067647; }
.band-chip.fair { background: var(--warning-light); color: #b45309; }
.band-chip.poor { background: var(--danger-light); color: #b42318; }

.health-mini {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
  margin: 8px 0 18px;
}
.mini-bars {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-width: 240px;
}
.mini-bar-row {
  display: grid;
  grid-template-columns: 140px 1fr 34px;
  align-items: center;
  gap: 10px;
  font-size: 0.8rem;
}
.mini-bar-label { color: var(--text-muted); }
.mini-bar-track {
  background: var(--bg-code-inline);
  height: 7px;
  border-radius: 4px;
  overflow: hidden;
}
.mini-bar-fill { height: 100%; border-radius: 4px; }
.mini-bar-value { text-align: right; font-weight: 700; color: var(--text-main); }

/* A missing governance field (owner/steward/classification) rendered as an
   open action item instead of an easy-to-miss bare "not specified". */
.action-chip {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
}
.action-chip.warn { background: var(--warning-light); color: #b45309; }
.action-chip.muted { background: var(--bg-code-inline); color: var(--text-faint); font-weight: 500; }

/* "Report at a glance" wireframe thumbnail grid (Day 5) — 25%-ish scale
   cards reusing the same wireframe SVG the technical doc/user guide render
   full-size; screen-only (see .no-print), so it never grows the exec doc's
   printed-page count. */
.thumb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 14px;
  margin: 12px 0;
}
.thumb-card { text-align: center; }
.thumb-card .diagram { pointer-events: none; }
.thumb-card svg {
  width: 100%;
  height: auto;
  border-radius: 8px;
  border: 1px solid var(--border-color);
}
.thumb-caption {
  display: block;
  margin-top: 6px;
  font-size: 0.76rem;
  font-weight: 600;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.thumb-more {
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  color: var(--text-faint);
  font-size: 0.8rem;
  font-weight: 600;
  min-height: 80px;
}

/* Mobile TOC toggle — hidden on desktop, shown as a fixed hamburger button
   below the 1024px breakpoint where the sidebar becomes an overlay. */
.mobile-toc-toggle {
  display: none;
  position: fixed;
  top: 16px;
  left: 16px;
  z-index: 200;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
  color: var(--text-main);
  font-size: 1.2rem;
  line-height: 1;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}

/* Theme toggle — a two-option segmented pill (Light / Dark) under the
   sidebar logo, both icons always visible so the current mode is obvious
   at a glance rather than inferred from a single swapping icon. */
.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  width: fit-content;
  margin-bottom: 20px;
  padding: 3px;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: var(--bg-main);
}
.theme-opt {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--text-faint);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.theme-opt svg {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.theme-opt:hover {
  color: var(--primary);
}
.theme-opt[aria-pressed="true"] {
  background: var(--primary-light);
  color: var(--primary);
}

/* Responsiveness & Print settings */
@media (max-width: 1024px) {
  .mobile-toc-toggle {
    display: block;
  }
  .sidebar {
    display: none;
    position: fixed;
    top: 0; bottom: 0; left: 0;
    z-index: 150;
    box-shadow: 4px 0 24px rgba(0, 0, 0, 0.25);
  }
  .sidebar.open {
    display: block;
  }
  .sidebar-scrim {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.45);
    z-index: 140;
  }
  .sidebar-scrim.open {
    display: block;
  }
  .content-wrapper {
    margin-left: 0;
    max-width: 100%;
    padding: 32px 24px 32px 76px;
  }
}

@page {
  margin: 2cm;
}

@media print {
  /* Force light regardless of on-screen theme (system preference or the
     sidebar toggle) — a printed/PDF page is always read on paper. */
  :root {
    --bg-main: #f8fafc !important;
    --bg-card: #ffffff !important;
    --bg-hover: #fafbfd !important;
    --bg-code-inline: #f1f5f9 !important;
    --text-main: #0f172a !important;
    --text-muted: #475569 !important;
    --text-faint: #94a3b8 !important;
    --border-color: #e2e8f0 !important;
    --code-text: #e2e8f0 !important;
  }
  body {
    background-color: #ffffff;
    display: block;
  }
  .sidebar, .theme-toggle, .mobile-toc-toggle, .skip-link, .copy-btn {
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
  pre, table, .table-scroll, .measure, .diagram, .card-section {
    page-break-inside: avoid;
  }
  /* No-JS fallback: force every collapsed <details> open for print, even
     if the onbeforeprint handler below didn't run (script disabled). */
  details.collapsible > summary::before {
    content: '';
  }
  details.collapsible > .code-block,
  details.collapsible > .collapsible-body {
    display: block !important;
  }
  /* An on-screen-only visual with no print equivalent (e.g. the executive
     doc's wireframe-thumbnail grid) — keeps a rich screen view without
     blowing past the doc's printed-page budget. */
  .no-print {
    display: none !important;
  }

  /* Cover page — a full page ahead of section 1, print-only. */
  .print-cover {
    display: flex;
    flex-direction: column;
    justify-content: center;
    min-height: 90vh;
    page-break-after: always;
  }
  /* Diagonal CONFIDENTIAL/RESTRICTED watermark, print-only, behind content. */
  .print-watermark {
    display: block;
    position: fixed;
    top: 45%;
    left: 50%;
    transform: translate(-50%, -50%) rotate(-35deg);
    font-size: 5rem;
    font-weight: 800;
    letter-spacing: 0.1em;
    color: rgba(180, 30, 30, 0.16);
    z-index: -1;
    pointer-events: none;
    white-space: nowrap;
  }
}
"""

_THEME_INIT_SCRIPT = """
<script>
(function () {
  try {
    var saved = localStorage.getItem('pbicompass-theme');
    if (saved === 'dark' || saved === 'light') {
      document.documentElement.setAttribute('data-theme', saved);
    }
  } catch (e) {}
})();
</script>
"""

_SCRIPT = """
<script>
document.addEventListener('DOMContentLoaded', () => {
  // D3 (mobile 390px): tables use the browser's automatic column-sizing
  // algorithm, so a wide cell (a long file path, an unwrapped DAX
  // expression) can force a table past its 100% width instead of
  // shrinking to fit — wrapping every table in its own horizontal-scroll
  // box keeps that overflow local instead of widening the whole page.
  document.querySelectorAll('table').forEach((table) => {
    if (table.parentElement && table.parentElement.classList.contains('table-scroll')) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'table-scroll';
    table.parentNode.insertBefore(wrapper, table);
    wrapper.appendChild(table);
  });

  const links = document.querySelectorAll('.toc-link');
  const sections = document.querySelectorAll('h2[id]');

  function changeActiveLink() {
    let index = sections.length;
    while(--index && window.scrollY + 100 < sections[index].offsetTop) {}
    links.forEach((link) => {
      link.classList.remove('active');
      link.removeAttribute('aria-current');
    });
    if (sections[index]) {
      const activeLink = document.querySelector(`.toc-link[href="#${sections[index].id}"]`);
      if (activeLink) {
        activeLink.classList.add('active');
        activeLink.setAttribute('aria-current', 'true');
      }
    }
  }

  changeActiveLink();
  window.addEventListener('scroll', changeActiveLink);

  // Client-side search — substring + prefix ranking over the embedded
  // {title, type, anchor} index, no CDN/no lunr. ~work fully offline.
  (function () {
    const input = document.querySelector('.search-input');
    const resultsEl = document.querySelector('.search-results');
    const indexScript = document.getElementById('search-index');
    if (!input || !resultsEl || !indexScript) return;
    let entries = [];
    try { entries = JSON.parse(indexScript.textContent); } catch (e) { entries = []; }
    let active = -1;
    let shown = [];

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[c]));
    }

    function render(matches) {
      shown = matches;
      active = -1;
      if (!matches.length) {
        resultsEl.innerHTML = '<li class="search-empty">No matches</li>';
        resultsEl.hidden = false;
        return;
      }
      resultsEl.innerHTML = matches.map((m, i) =>
        `<li class="search-result" data-index="${i}" data-anchor="${escapeHtml(m.anchor)}">` +
        `<span>${escapeHtml(m.title)}</span><span class="type">${escapeHtml(m.type)}</span></li>`
      ).join('');
      resultsEl.hidden = false;
    }

    function search(query) {
      const q = query.trim().toLowerCase();
      if (!q) { resultsEl.hidden = true; resultsEl.innerHTML = ''; return; }
      const scored = [];
      for (const e of entries) {
        const t = e.title.toLowerCase();
        const idx = t.indexOf(q);
        if (idx === -1) continue;
        scored.push({ entry: e, rank: idx === 0 ? 0 : 1, idx });
      }
      scored.sort((a, b) => a.rank - b.rank || a.idx - b.idx || a.entry.title.length - b.entry.title.length);
      render(scored.slice(0, 20).map((s) => s.entry));
    }

    function goTo(anchor) {
      const target = document.getElementById(anchor);
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      resultsEl.hidden = true;
      input.value = '';
    }

    function setActive(i) {
      active = i;
      resultsEl.querySelectorAll('.search-result').forEach((el, idx) => {
        el.classList.toggle('active', idx === active);
      });
    }

    input.addEventListener('input', () => search(input.value));
    input.addEventListener('keydown', (e) => {
      if (resultsEl.hidden) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActive(Math.min(active + 1, shown.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive(Math.max(active - 1, 0));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (active >= 0 && shown[active]) goTo(shown[active].anchor);
        else if (shown.length) goTo(shown[0].anchor);
      } else if (e.key === 'Escape') {
        resultsEl.hidden = true;
      }
    });
    resultsEl.addEventListener('click', (e) => {
      const li = e.target.closest('.search-result');
      if (li) goTo(li.getAttribute('data-anchor'));
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.search-box')) resultsEl.hidden = true;
    });
  })();

  // Theme toggle — segmented Light/Dark pill; each option sets its theme
  // explicitly rather than swapping a single icon, so the current mode is
  // always visible without reading state off the button.
  const themeOpts = document.querySelectorAll('.theme-opt');
  if (themeOpts.length) {
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    function currentTheme() {
      const attr = document.documentElement.getAttribute('data-theme');
      if (attr) return attr;
      return prefersDark ? 'dark' : 'light';
    }
    function updatePressed() {
      const active = currentTheme();
      themeOpts.forEach((opt) => {
        opt.setAttribute('aria-pressed', String(opt.getAttribute('data-theme-choice') === active));
      });
    }
    updatePressed();
    themeOpts.forEach((opt) => {
      opt.addEventListener('click', () => {
        const next = opt.getAttribute('data-theme-choice');
        document.documentElement.setAttribute('data-theme', next);
        try { localStorage.setItem('pbicompass-theme', next); } catch (e) {}
        updatePressed();
      });
    });
  }

  // Mobile TOC overlay
  const mobileToggle = document.querySelector('.mobile-toc-toggle');
  const sidebar = document.querySelector('.sidebar');
  const scrim = document.querySelector('.sidebar-scrim');
  function closeSidebar() {
    if (sidebar) sidebar.classList.remove('open');
    if (scrim) scrim.classList.remove('open');
  }
  if (mobileToggle && sidebar) {
    mobileToggle.addEventListener('click', () => {
      sidebar.classList.toggle('open');
      if (scrim) scrim.classList.toggle('open');
    });
  }
  if (scrim) scrim.addEventListener('click', closeSidebar);
  links.forEach((link) => link.addEventListener('click', closeSidebar));

  // Copy-to-clipboard for code blocks — one delegated listener covers every
  // .copy-btn added by any renderer, present or future.
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const pre = btn.parentElement && btn.parentElement.querySelector('pre');
    if (!pre || !navigator.clipboard) return;
    navigator.clipboard.writeText(pre.textContent).then(() => {
      const original = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = original; }, 1500);
    }).catch(() => {});
  });

  // Force every collapsed <details> open for print/PDF, then restore
  // whatever was open before once printing finishes.
  let detailsClosedBeforePrint = [];
  window.addEventListener('beforeprint', () => {
    detailsClosedBeforePrint = Array.from(document.querySelectorAll('details.collapsible:not([open])'));
    detailsClosedBeforePrint.forEach((d) => { d.open = true; });
  });
  window.addEventListener('afterprint', () => {
    detailsClosedBeforePrint.forEach((d) => { d.open = false; });
    detailsClosedBeforePrint = [];
  });

  // Interactive model diagram: wheel/pinch zoom, drag to pan, hover to
  // highlight a table's relationships, click to jump to its row. A no-op on
  // any doc without a diagram (querySelectorAll finds nothing); print/DOCX
  // always show the static, un-panned/zoomed view since these only run on
  // user interaction.
  function slugify(s) {
    return (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'x';
  }
  // Day 6: pan/zoom on every diagram canvas via the vendored svg-pan-zoom
  // (mouse drag, scroll-wheel zoom, and — the hand-rolled version's real
  // gap — actual pinch-to-zoom/touch panning on mobile). ``moved`` still
  // guards link clicks the same way the hand-rolled version did: svg-pan-
  // zoom manages pan/zoom only, never touches click events on children,
  // so a drag ending on top of a linked node would otherwise navigate.
  // ``isDown`` gates it: ``beforePan`` also fires for *programmatic* pans
  // (e.g. this file's own ``.reset()`` before printing) with no user
  // mousedown/touchstart active — without the gate, printing would leave
  // ``moved`` permanently stuck true (no mouseup ever follows a print) and
  // silently swallow the very next click on every diagram link.
  const panZoomInstances = [];
  document.querySelectorAll('.diagram svg').forEach((svg) => {
    if (typeof svgPanZoom !== 'function') return; // vendor script failed to parse — degrade to static, never throw
    // svg-pan-zoom drives pan/zoom via an explicit pixel width/height that
    // it reads once at init and then bakes in (it discards the original
    // viewBox-driven auto-sizing entirely). Without an explicit height set
    // *before* init, the SVG's pre-init intrinsic height is the UA's
    // replaced-element default (150px, since width="100%" leaves no
    // intrinsic ratio for the browser to size height from) — and pan-zoom
    // permanently fits/centers the diagram into that sliver. Compute the
    // real height from the diagram's own viewBox aspect ratio and its
    // current rendered width before handing control to svg-pan-zoom, and
    // keep recomputing it on resize/print so every viewport gets a
    // correctly proportioned diagram, not just the one active at load.
    const vb = svg.viewBox && svg.viewBox.baseVal;
    function sizeToAspect() {
      if (!vb || !vb.width || !vb.height) return;
      // Measure against the container's current width, not the SVG's own
      // — once this has run once, the SVG carries a fixed pixel `width`
      // attribute (set below), so getBoundingClientRect() on the svg
      // itself would just echo that stale value back on every later call
      // (resize, print) instead of reflecting the container's new size.
      svg.style.width = '100%';
      const w = svg.getBoundingClientRect().width || vb.width;
      svg.style.width = '';
      if (!w) return;
      svg.setAttribute('width', w);
      svg.setAttribute('height', w * (vb.height / vb.width));
    }
    sizeToAspect();
    let isDown = false, moved = false;
    const instance = svgPanZoom(svg, {
      zoomEnabled: true, panEnabled: true, controlIconsEnabled: true,
      fit: true, center: true, zoomScaleSensitivity: 0.3, minZoom: 0.5, maxZoom: 8,
      beforePan: () => { if (isDown) moved = true; },
    });
    panZoomInstances.push({ instance, sizeToAspect });
    svg.addEventListener('mousedown', () => { isDown = true; });
    svg.addEventListener('touchstart', () => { isDown = true; });
    svg.addEventListener('click', (e) => {
      if (moved) { e.preventDefault(); e.stopPropagation(); }
    }, true);
    svg.addEventListener('mouseup', () => { isDown = false; setTimeout(() => { moved = false; }, 0); });
    svg.addEventListener('touchend', () => { isDown = false; setTimeout(() => { moved = false; }, 0); });

    // Lineage hover-connect: hovering a node highlights its own edges,
    // keeps its neighbors lit, and dims every unrelated node/edge. Nodes
    // carry data-node, edge groups data-from/data-to (layer-prefixed
    // slugs) — a no-op on diagrams without them (e.g. the wireframes).
    const lgNodes = svg.querySelectorAll('[data-node]');
    const lgEdges = svg.querySelectorAll('.lg-edge');
    if (lgNodes.length && lgEdges.length) {
      lgNodes.forEach((node) => {
        const id = node.getAttribute('data-node');
        node.addEventListener('mouseenter', () => {
          const connected = new Set([id]);
          lgEdges.forEach((ed) => {
            const f = ed.getAttribute('data-from'), t = ed.getAttribute('data-to');
            if (f === id || t === id) { ed.classList.add('hl'); connected.add(f); connected.add(t); }
            else ed.classList.add('dimmed');
          });
          lgNodes.forEach((n) => {
            if (!connected.has(n.getAttribute('data-node'))) n.classList.add('dimmed');
          });
        });
        node.addEventListener('mouseleave', () => {
          lgEdges.forEach((ed) => ed.classList.remove('hl', 'dimmed'));
          lgNodes.forEach((n) => n.classList.remove('dimmed'));
        });
      });
    }

    const nodes = svg.querySelectorAll('.dm-node');
    const dimEdges = svg.querySelectorAll('.dm-edge');
    nodes.forEach((node) => {
      const table = node.getAttribute('data-table');
      node.addEventListener('mouseenter', () => {
        nodes.forEach((n) => n.classList.add('dimmed'));
        dimEdges.forEach((ed) => ed.classList.add('dimmed'));
        node.classList.remove('dimmed');
        dimEdges.forEach((ed) => {
          if (ed.getAttribute('data-from') !== table && ed.getAttribute('data-to') !== table) return;
          ed.classList.remove('dimmed');
          ed.classList.add('highlighted');
          const other = ed.getAttribute('data-from') === table ? ed.getAttribute('data-to') : ed.getAttribute('data-from');
          nodes.forEach((n) => { if (n.getAttribute('data-table') === other) n.classList.remove('dimmed'); });
        });
      });
      node.addEventListener('mouseleave', () => {
        nodes.forEach((n) => n.classList.remove('dimmed'));
        dimEdges.forEach((ed) => { ed.classList.remove('dimmed'); ed.classList.remove('highlighted'); });
      });
      node.addEventListener('click', () => {
        const target = document.getElementById(`table-${slugify(table)}`);
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    });
  });

  // Print/PDF fallback: whatever pan/zoom state a reader left a diagram
  // in on screen must never clip the printed page — recompute each
  // diagram's height for the print layout's width, then reset to its
  // fitted, centered default right before printing (Ctrl+P or
  // window.print()), then restore the reader's on-screen view afterward.
  if (panZoomInstances.length) {
    window.addEventListener('beforeprint', () => {
      panZoomInstances.forEach(({ instance, sizeToAspect }) => {
        sizeToAspect();
        instance.resize();
        instance.fit();
        instance.center();
      });
    });

    // Keep every diagram's aspect ratio correct across viewport changes
    // (orientation flip, window resize) instead of only at initial load.
    let resizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        panZoomInstances.forEach(({ instance, sizeToAspect }) => {
          sizeToAspect();
          instance.resize();
          instance.fit();
          instance.center();
        });
      }, 150);
    });
  }
});
</script>
"""

_SUN_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="5"/>'
    '<line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>'
    '<line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>'
    '<line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>'
    '<line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>'
)
_MOON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'
)


_WATERMARK_CLASSIFICATIONS = {"confidential", "restricted"}


def page_shell(
    *,
    title: str,
    subtitle: str,
    toc: list[tuple[str, str]],
    kpis: list[tuple[str, str]],
    body_html: str,
    search_index: list[dict] | None = None,
    doc_links: list[tuple[str, str]] | None = None,
    owner: str | None = None,
    version: str | None = None,
    status: str | None = None,
    classification: str | None = None,
    completeness: tuple[int, int, list[str]] | None = None,
) -> str:
    """Wrap ``body_html`` (a renderer's own section content) in the full HTML
    document: doctype, head/fonts/CSS, sidebar TOC + search, header card with
    title/subtitle/KPIs, a print-only cover page, and the closing scroll-spy
    script.

    ``toc`` is a list of ``(anchor_id, label)`` pairs. ``kpis`` is a list of
    ``(label, value)`` pairs — pass an empty list to omit the KPI strip.
    ``search_index`` is a list of ``{"title", "type", "anchor"}`` dicts for
    the sidebar search box; defaults to one entry per ``toc`` section when a
    renderer doesn't build a richer one (measures, tables, findings, ...).
    ``doc_links`` is a list of ``(label, href)`` pairs — sibling documents
    (and the hub) generated in the same job — rendered as a doc-switcher
    block above the TOC. Plain relative hrefs, so they work unzipped on disk
    with no web server; omit (or pass ``None``) for a single-document run,
    where there's nothing valid to link to.
    ``owner``/``version``/``status``/``classification`` populate the
    print-only cover page (2.8); a diagonal watermark is added automatically
    when ``classification`` is "Confidential" or "Restricted" (case-
    insensitive) — never shown on screen, only when printed/exported to PDF.
    """
    if search_index is None:
        search_index = [{"title": sec_title, "type": "section", "anchor": sec_id} for sec_id, sec_title in toc]

    o: list[str] = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">']
    o.append(f"<title>{_e(title)} — Documentation</title>")
    o.append(f'<link rel="icon" type="image/png" href="{LOGO_DATA_URI}">')
    o.append(_THEME_INIT_SCRIPT)
    o.append(f"<style>{_CSS}</style></head><body>")

    o.append('<a href="#main-content" class="skip-link">Skip to content</a>')
    o.append('<button type="button" class="mobile-toc-toggle" aria-label="Open table of contents" '
             'aria-expanded="false">&#9776;</button>')
    o.append('<div class="sidebar-scrim"></div>')

    o.append('<div class="print-cover">')
    o.append(f'<div class="print-cover-mark"><img src="{LOGO_DATA_URI}" alt=""><span>PBICompass</span></div>')
    o.append(f"<h1>{_e(title)}</h1>")
    o.append(f'<p class="print-cover-subtitle">{_e(subtitle)}</p>')
    o.append('<dl class="print-cover-meta">')
    for label, value in (("Version", version), ("Status", status), ("Owner", owner)):
        o.append(f"<dt>{_e(label)}</dt><dd>{_e(value) if value else 'Not specified'}</dd>")
    o.append("</dl>")
    if classification:
        o.append(f'<div class="print-cover-classification">{_e(classification)}</div>')
    o.append("</div>")
    if classification and classification.strip().lower() in _WATERMARK_CLASSIFICATIONS:
        o.append(f'<div class="print-watermark">{_e(classification.upper())}</div>')

    index_json = json.dumps(search_index, ensure_ascii=False).replace("<", "\\u003c")
    o.append(f'<script type="application/json" id="search-index">{index_json}</script>')

    o.append('<nav class="sidebar" aria-label="Table of contents">')
    o.append('<div class="sidebar-logo">')
    o.append(f'<img src="{LOGO_DATA_URI}" alt="PBICompass">')
    o.append('<span>PBICompass</span>')
    o.append('</div>')
    o.append('<div class="theme-toggle" role="group" aria-label="Theme">')
    o.append('<button type="button" class="theme-opt" data-theme-choice="light" '
             'aria-pressed="true" title="Light mode" aria-label="Light mode">' + _SUN_SVG + '</button>')
    o.append('<button type="button" class="theme-opt" data-theme-choice="dark" '
             'aria-pressed="false" title="Dark mode" aria-label="Dark mode">' + _MOON_SVG + '</button>')
    o.append('</div>')
    if doc_links:
        o.append('<nav class="doc-switcher" aria-label="Other documents in this job">')
        for label, href in doc_links:
            o.append(f'<a href="{_e(href)}">{_e(label)}</a>')
        o.append('</nav>')
    o.append('<div class="search-box">')
    o.append('<input type="text" class="search-input" placeholder="Search this document…" '
             'aria-label="Search this document" autocomplete="off">')
    o.append('<ul class="search-results" hidden></ul>')
    o.append('</div>')
    o.append('<ul class="toc-list">')
    for sec_id, sec_title in toc:
        o.append(f'<li class="toc-item"><a href="#{sec_id}" class="toc-link">{_e(sec_title)}</a></li>')
    o.append('</ul></nav>')

    o.append('<div class="content-wrapper">')
    o.append('<main class="main-content" id="main-content">')

    o.append('<div class="header-card">')
    o.append(f"<h1>{_e(title)}</h1>")
    o.append(f'<p class="subtitle">{_e(subtitle)}</p>')
    if completeness:
        pct, missing_count, missing_fields = completeness
        o.append('<div class="completeness-bar-container" style="margin-top:10px; font-size:0.85em;">')
        o.append('<div style="display:flex; justify-content:space-between; margin-bottom:4px;">')
        o.append(f'<span>Documentation Completeness: <strong>{pct}%</strong></span>')
        o.append(f'<span>{missing_count} fields awaiting input</span>')
        o.append('</div>')
        o.append('<div class="progress-bar-bg" style="background:#e2e8f0; height:6px; border-radius:3px; overflow:hidden; position:relative;">')
        o.append(f'<div class="progress-bar-fill" style="background:var(--primary); width:{pct}%; height:100%;"></div>')
        o.append('</div>')
        if missing_fields:
            readable = [f.replace("_", " ").title() for f in missing_fields[:5]]
            if len(missing_fields) > 5:
                readable.append(f"+{len(missing_fields)-5} more")
            o.append(f'<p style="margin-top:4px; color:#64748b;">Missing: {", ".join(readable)}</p>')
        o.append('</div>')
    o.append("</div>")

    if kpis:
        o.append('<div class="kpis">')
        for label, value in kpis:
            o.append(f'<div class="kpi"><div class="n">{_e(value)}</div><div class="l">{_e(label)}</div></div>')
        o.append("</div>")

    o.append(body_html)

    o.append("</main></div>")
    # Day 6: vendored svg-pan-zoom, inlined (not a <script src>, and no
    # CDN) so a downloaded, offline-opened HTML file still gets working
    # pan/zoom on every diagram — must load before ``_SCRIPT``, which
    # calls ``svgPanZoom(...)`` in its DOMContentLoaded handler. Only shipped
    # when ``body_html`` actually contains a diagram (e.g. the audit doc has
    # none) — otherwise it's ~9KB of vendor JS wired up for nothing.
    # ``_SCRIPT`` itself always loads: its pan/zoom wiring already no-ops
    # when ``svgPanZoom`` isn't defined, and it also carries every doc's
    # unrelated (non-diagram) chrome — search, theme toggle, scroll-spy,
    # mobile TOC, copy-to-clipboard, print handling.
    if '<div class="diagram"' in body_html:
        o.append(f"<script>{SVG_PAN_ZOOM_JS}</script>")
    o.append(_SCRIPT)
    o.append("</body></html>")
    return "\n".join(o)
