"""Microsoft Teams publish target (C3).

Teams is a *notification* destination, not a document store: this posts a
compact card to an Incoming Webhook announcing that documentation was
regenerated, with the document list and an optional link to wherever the docs
actually live (a Confluence page, a SharePoint library, a repo). Document
content itself is never pushed into a chat channel — only the notice.

Config: ``PBICOMPASS_TEAMS_WEBHOOK`` (an Incoming Webhook URL).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import PublishError, PublishResult, collect_documents, http_request, json_body


class TeamsPublisher:
    def __init__(self, *, webhook: str, link: Optional[str] = None,
                 title: str = "PBICompass documentation updated",
                 prefer: str = "html") -> None:
        if not webhook:
            raise PublishError("Teams target requires a webhook URL "
                               "(--webhook or PBICOMPASS_TEAMS_WEBHOOK).")
        if not webhook.lower().startswith("https://"):
            raise PublishError("Teams webhook URL must be https.")
        self.webhook = webhook
        self.link = link
        self.title = title
        self.prefer = prefer

    def _card(self, names: list[str]) -> dict:
        facts = [{"name": "Documents", "value": ", ".join(names) or "none"},
                 {"name": "Count", "value": str(len(names))}]
        card = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": self.title,
            "themeColor": "2F6FEB",
            "title": self.title,
            "sections": [{"facts": facts, "markdown": False}],
        }
        if self.link:
            card["potentialAction"] = [{
                "@type": "OpenUri", "name": "Open documentation",
                "targets": [{"os": "default", "uri": self.link}],
            }]
        return card

    def publish(self, source: Path) -> PublishResult:
        docs = collect_documents(Path(source), prefer=self.prefer)
        names = [d.title for d in docs]
        status, body = http_request("POST", self.webhook,
                                    headers={"Content-Type": "application/json"},
                                    data=json_body(self._card(names)))
        if status >= 400:
            raise PublishError(f"Teams webhook post failed ({status}): {body[:200]}")
        return PublishResult(target="Teams", detail="notification posted to channel",
                             count=len(names))
