"""Publish targets (C3): push generated documentation to where a team's docs
actually live — a Git repo or share, Confluence, SharePoint — or announce it in
Teams.

Documentation nobody can find isn't documentation. Every target is explicit and
credential-gated: nothing is ever published without the user running
``pbicompass publish`` and supplying that destination's own credentials. Each
publisher is stdlib-only (``urllib``), so this adds no dependency.

Fidelity differs by destination, by design:

* ``filesystem`` / ``sharepoint`` — files are copied/uploaded **verbatim**, so
  styled HTML, DOCX, PDF and diagrams all survive intact.
* ``confluence`` — converted to Confluence storage format; text, tables and
  code carry over, interactive diagrams do not.
* ``teams`` — a notification card only; document content never enters a chat.
"""

from __future__ import annotations

import os
from typing import Any

from .base import Document, PublishError, PublishResult, collect_documents
from .confluence import ConfluencePublisher
from .filesystem import FilesystemPublisher
from .sharepoint import SharePointPublisher
from .teams import TeamsPublisher

TARGETS = ("filesystem", "confluence", "sharepoint", "teams")

__all__ = [
    "TARGETS", "PublishError", "PublishResult", "Document", "collect_documents",
    "ConfluencePublisher", "FilesystemPublisher", "SharePointPublisher",
    "TeamsPublisher", "get_publisher",
]


def _env(name: str, override: Any = None) -> Any:
    """An explicit flag wins; otherwise fall back to the env var."""
    if override:
        return override
    return os.environ.get(name) or None


def get_publisher(target: str, **opts: Any):
    """Resolve a target name to a configured publisher.

    Config comes from explicit ``opts`` first, then ``PBICOMPASS_*`` env vars,
    so credentials can stay out of shell history.
    """
    target = (target or "").strip().lower()
    if target == "filesystem":
        return FilesystemPublisher(
            dest=_env("PBICOMPASS_PUBLISH_DEST", opts.get("dest")),
            git=bool(opts.get("git")), git_push=bool(opts.get("git_push")),
            commit_message=opts.get("commit_message") or "Update PBICompass documentation",
        )
    if target == "confluence":
        return ConfluencePublisher(
            url=_env("PBICOMPASS_CONFLUENCE_URL", opts.get("url")),
            email=_env("PBICOMPASS_CONFLUENCE_EMAIL", opts.get("email")),
            token=_env("PBICOMPASS_CONFLUENCE_TOKEN", opts.get("token")),
            space=_env("PBICOMPASS_CONFLUENCE_SPACE", opts.get("space")),
            parent_id=_env("PBICOMPASS_CONFLUENCE_PARENT_ID", opts.get("parent_id")),
            prefer=opts.get("prefer") or "html",
        )
    if target == "sharepoint":
        return SharePointPublisher(
            token=_env("PBICOMPASS_SHAREPOINT_TOKEN", opts.get("token")),
            drive_id=_env("PBICOMPASS_SHAREPOINT_DRIVE_ID", opts.get("drive_id")),
            folder=_env("PBICOMPASS_SHAREPOINT_FOLDER", opts.get("folder")) or "PBICompass",
        )
    if target == "teams":
        return TeamsPublisher(
            webhook=_env("PBICOMPASS_TEAMS_WEBHOOK", opts.get("webhook")),
            link=_env("PBICOMPASS_TEAMS_LINK", opts.get("link")),
            title=opts.get("title") or "PBICompass documentation updated",
            prefer=opts.get("prefer") or "html",
        )
    raise PublishError(f"Unknown publish target {target!r}. Choose from: {', '.join(TARGETS)}.")
