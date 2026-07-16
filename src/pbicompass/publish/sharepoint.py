"""SharePoint / OneDrive publish target (C3), via the Microsoft Graph API.

Uploads the generated files **verbatim** into a document library folder — full
fidelity, so the styled HTML, DOCX, PDF and diagrams all land intact. This is
the natural destination when a team's documentation already lives in SharePoint.

Token boundary (deliberate and honest): this publisher takes an **already-issued
Graph access token** rather than embedding an OAuth flow. Acquiring a token is
an org-specific concern (device code, client credentials, or an existing SSO
session) and baking one flow in would be wrong for most tenants. Supply it via
``PBICOMPASS_SHAREPOINT_TOKEN``; the token is used for the request and never
logged or persisted.

Config: ``PBICOMPASS_SHAREPOINT_TOKEN``, ``PBICOMPASS_SHAREPOINT_DRIVE_ID``,
optional ``PBICOMPASS_SHAREPOINT_FOLDER`` (default ``PBICompass``).

Uses Graph's simple upload (``PUT /content``), which covers files up to 4 MB —
comfortably above a generated document. Larger files would need an upload
session; that is flagged rather than silently truncated.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from .base import PublishError, PublishResult, http_request

_GRAPH = "https://graph.microsoft.com/v1.0"
# Graph's simple PUT upload limit.
_SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024

_CONTENT_TYPES = {
    ".html": "text/html", ".md": "text/markdown", ".json": "application/json",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".yaml": "application/x-yaml",
}


class SharePointPublisher:
    def __init__(self, *, token: str, drive_id: str, folder: str = "PBICompass") -> None:
        missing = [n for n, v in (("token", token), ("drive_id", drive_id)) if not v]
        if missing:
            raise PublishError("SharePoint target missing config: " + ", ".join(missing)
                               + " (set PBICOMPASS_SHAREPOINT_* env vars or pass flags).")
        self.token = token
        self.drive_id = drive_id
        self.folder = folder.strip("/")

    def _upload(self, path: Path) -> str:
        data = path.read_bytes()
        if len(data) > _SIMPLE_UPLOAD_LIMIT:
            raise PublishError(
                f"{path.name} is {len(data) // 1024} KB — above Graph's 4 MB simple-upload "
                "limit. Upload sessions are not implemented; publish this file another way.")
        target = f"{self.folder}/{path.name}" if self.folder else path.name
        url = (f"{_GRAPH}/drives/{urllib.parse.quote(self.drive_id)}"
               f"/root:/{urllib.parse.quote(target)}:/content")
        status, body = http_request(
            "PUT", url,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": _CONTENT_TYPES.get(path.suffix.lower(),
                                                        "application/octet-stream")},
            data=data, timeout=60.0)
        if status == 401:
            raise PublishError("SharePoint auth failed (401) — the Graph token is invalid or expired.")
        if status >= 400:
            raise PublishError(f"SharePoint upload of {path.name} failed ({status}): {body[:200]}")
        return target

    def publish(self, source: Path) -> PublishResult:
        source = Path(source)
        if not source.exists():
            raise PublishError(f"Source path not found: {source}")
        files = [source] if source.is_file() else sorted(
            p for p in source.iterdir() if p.is_file())
        if not files:
            raise PublishError(f"No files to publish in {source}.")
        uploaded = [self._upload(f) for f in files]
        return PublishResult(target="SharePoint",
                             detail=f"drive {self.drive_id}, folder /{self.folder}",
                             urls=uploaded[:8], count=len(uploaded))
