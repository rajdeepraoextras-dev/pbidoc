"""Shared plumbing for the publish targets (C3).

Every publisher is built on the stdlib ``urllib`` — no new third-party HTTP
dependency, consistent with the parser layer's zero-extra-dependency ethos.
Content a user chooses to publish is *their* action to *their* destination; the
tool never publishes without an explicit command + configured credentials, and
nothing about a publish is logged with document content.
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class PublishError(Exception):
    """A publish attempt failed (bad config, auth, network, or API error)."""


@dataclass
class PublishResult:
    target: str
    detail: str
    urls: list[str] = field(default_factory=list)
    count: int = 0

    def summary(self) -> str:
        head = f"Published {self.count} document(s) to {self.target}"
        if self.urls:
            head += ":\n  " + "\n  ".join(self.urls)
        elif self.detail:
            head += f" — {self.detail}"
        return head


@dataclass
class Document:
    """One publishable artifact loaded from disk."""
    name: str          # base name without extension, e.g. "technical"
    title: str         # human title, e.g. "Technical"
    kind: str          # "html" | "md"
    text: str


_KIND_EXT = {"html": ".html", "md": ".md"}
# Bundle members that are data/aux, not documents to publish as pages.
_SKIP_STEMS = {"model", "enrichment", "index"}


def _title_from_name(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").strip().title()


def collect_documents(path: Path, *, prefer: str = "html") -> list[Document]:
    """Load the documents to publish from ``path``.

    ``path`` may be a single ``.html``/``.md`` file, or a bundle directory (in
    which case every top-level document of the preferred kind is collected,
    skipping data files like ``model.json`` and the ``index.html`` hub).
    """
    ext = _KIND_EXT.get(prefer)
    if ext is None:
        raise PublishError(f"Unsupported document kind {prefer!r} (use 'html' or 'md').")
    path = Path(path)
    if not path.exists():
        raise PublishError(f"Path not found: {path}")

    files: list[Path]
    if path.is_file():
        if path.suffix.lower() not in _KIND_EXT.values():
            raise PublishError(f"{path.name} is not a publishable .html/.md document.")
        files = [path]
    else:
        files = sorted(p for p in path.glob(f"*{ext}") if p.stem.lower() not in _SKIP_STEMS)
        if not files:
            raise PublishError(f"No *{ext} documents found in {path}.")

    docs = []
    for f in files:
        docs.append(Document(
            name=f.stem,
            title=_title_from_name(f.stem),
            kind=prefer,
            text=f.read_text(encoding="utf-8"),
        ))
    return docs


def http_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    data: Optional[bytes] = None,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """Issue an HTTP request, returning ``(status, body_text)``.

    Raises :class:`PublishError` on transport failure. An HTTP error status
    (4xx/5xx) is returned rather than raised so callers can surface the API's
    own error body — except a total transport failure, which has no status.
    """
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, body
    except urllib.error.URLError as exc:
        raise PublishError(f"Network error reaching {url}: {exc.reason}") from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise PublishError(f"Request to {url} failed: {exc}") from exc


def json_body(obj) -> bytes:
    return _json.dumps(obj).encode("utf-8")
