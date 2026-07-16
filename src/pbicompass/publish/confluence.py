"""Confluence Cloud publish target (C3).

Creates or updates a Confluence page per document, under a configured space and
optional parent. Confluence stores pages in its own "storage format" (a subset
of XHTML), so the generated HTML is reduced to that subset: ``<head>``,
``<style>``, ``<script>`` and ``<svg>`` blocks are dropped and inline
style/class attributes stripped. The textual documentation — headings, tables,
lists, code, links — carries over faithfully; interactive diagrams do not (they
remain in the HTML/PDF bundle). Publishing is idempotent: an existing page with
the same title in the space is updated in place, not duplicated.

Config (env or explicit): ``PBICOMPASS_CONFLUENCE_URL`` (e.g.
``https://your-site.atlassian.net/wiki``), ``PBICOMPASS_CONFLUENCE_EMAIL``,
``PBICOMPASS_CONFLUENCE_TOKEN`` (an Atlassian API token),
``PBICOMPASS_CONFLUENCE_SPACE``, optional ``PBICOMPASS_CONFLUENCE_PARENT_ID``.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from pathlib import Path
from typing import Optional

from .base import Document, PublishError, PublishResult, collect_documents, http_request, json_body

_BLOCK_RE = re.compile(r"(?is)<(script|style|head|svg)\b.*?</\1>")
_BODY_RE = re.compile(r"(?is)<body[^>]*>(.*)</body>")
_ATTR_RE = re.compile(r'(?is)\s(?:style|class|id|data-[\w-]+|aria-[\w-]+|role)="[^"]*"')
_COMMENT_RE = re.compile(r"(?is)<!--.*?-->")
_DOCTYPE_RE = re.compile(r"(?is)<!doctype[^>]*>")


def html_to_storage(html: str) -> str:
    """Reduce a full HTML document to Confluence storage-format body XHTML."""
    html = _DOCTYPE_RE.sub("", html)
    html = _COMMENT_RE.sub("", html)
    html = _BLOCK_RE.sub("", html)
    m = _BODY_RE.search(html)
    body = m.group(1) if m else html
    body = _ATTR_RE.sub("", body)
    # Storage format wants void elements closed; normalise the common ones.
    body = re.sub(r"(?i)<(br|hr|img)([^>]*?)>", r"<\1\2/>", body)
    body = re.sub(r"(?i)<(br|hr|img)([^>]*?)//>", r"<\1\2/>", body)
    return body.strip()


class ConfluencePublisher:
    def __init__(self, *, url: str, email: str, token: str, space: str,
                 parent_id: Optional[str] = None, prefer: str = "html") -> None:
        missing = [n for n, v in (("url", url), ("email", email),
                                  ("token", token), ("space", space)) if not v]
        if missing:
            raise PublishError("Confluence target missing config: " + ", ".join(missing)
                               + " (set PBICOMPASS_CONFLUENCE_* env vars or pass flags).")
        self.base = url.rstrip("/")
        self.space = space
        self.parent_id = parent_id
        self.prefer = prefer
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.headers = {"Authorization": f"Basic {auth}",
                        "Content-Type": "application/json", "Accept": "application/json"}

    # -- REST helpers --------------------------------------------------------
    def _find_page(self, title: str) -> Optional[dict]:
        q = urllib.parse.urlencode({"spaceKey": self.space, "title": title,
                                    "expand": "version"})
        status, body = http_request("GET", f"{self.base}/rest/api/content?{q}",
                                    headers=self.headers)
        if status == 401:
            raise PublishError("Confluence auth failed (401) — check email/API token.")
        if status >= 400:
            raise PublishError(f"Confluence lookup failed ({status}): {body[:200]}")
        results = (json.loads(body).get("results") if body else None) or []
        return results[0] if results else None

    def _page_payload(self, title: str, storage: str) -> dict:
        payload = {
            "type": "page", "title": title,
            "space": {"key": self.space},
            "body": {"storage": {"value": storage, "representation": "storage"}},
        }
        if self.parent_id:
            payload["ancestors"] = [{"id": str(self.parent_id)}]
        return payload

    def _page_url(self, resp_body: str) -> str:
        try:
            data = json.loads(resp_body)
            links = data.get("_links", {})
            return (links.get("base", self.base) + links.get("webui", "")) or self.base
        except Exception:
            return self.base

    def _publish_one(self, doc: Document) -> str:
        storage = html_to_storage(doc.text) if doc.kind == "html" else (
            "<pre>" + doc.text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>")
        existing = self._find_page(doc.title)
        payload = self._page_payload(doc.title, storage)
        if existing:
            payload["id"] = existing["id"]
            payload["version"] = {"number": existing["version"]["number"] + 1}
            status, body = http_request("PUT",
                                        f"{self.base}/rest/api/content/{existing['id']}",
                                        headers=self.headers, data=json_body(payload))
        else:
            status, body = http_request("POST", f"{self.base}/rest/api/content",
                                        headers=self.headers, data=json_body(payload))
        if status >= 400:
            raise PublishError(f"Confluence publish of '{doc.title}' failed ({status}): {body[:200]}")
        return self._page_url(body)

    # -- entry point ---------------------------------------------------------
    def publish(self, source: Path) -> PublishResult:
        docs = collect_documents(Path(source), prefer=self.prefer)
        urls = [self._publish_one(d) for d in docs]
        return PublishResult(target="Confluence", detail=f"space {self.space}",
                             urls=urls, count=len(urls))
