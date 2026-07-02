"""Per-job working sandbox with best-effort secure deletion.

Every job gets its own temp directory. Uploaded bytes, extracted project files,
and intermediate render artifacts live here and **only** here, and the whole
tree is shredded when the job ends (success or failure) — the core of the
zero-retention guarantee.

Set ``PBICOMPASS_SANDBOX_ROOT`` to place sandboxes on a RAM-backed filesystem
(tmpfs) in production so nothing touches a physical disk.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# Overwrite files up to this size before unlinking (best-effort shred).
_OVERWRITE_CAP = 64 * 1024 * 1024


class JobSandbox:
    def __init__(self, job_id: str, root: str | None = None) -> None:
        base = root or os.environ.get("PBICOMPASS_SANDBOX_ROOT") or tempfile.gettempdir()
        os.makedirs(base, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix=f"pbicompass_{job_id}_", dir=base))

    def path(self, name: str) -> Path:
        return self.dir / name

    def cleanup(self) -> None:
        """Best-effort secure delete: overwrite small files, then remove the tree."""
        try:
            for p in self.dir.rglob("*"):
                if p.is_file():
                    try:
                        size = p.stat().st_size
                        if 0 < size <= _OVERWRITE_CAP:
                            with open(p, "r+b", buffering=0) as fh:
                                fh.write(b"\0" * size)
                                fh.flush()
                                os.fsync(fh.fileno())
                    except OSError:
                        pass  # locked/unreadable — rmtree below still removes it
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self) -> "JobSandbox":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()
