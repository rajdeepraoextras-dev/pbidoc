"""Filesystem / Git publish target (C3).

Copies the generated documents verbatim into a destination directory — the
natural home when a team keeps its BI documentation in a Git repo or a synced
wiki/network share. Full fidelity: HTML, DOCX, PDF, and the diagrams all carry
over untouched. Optionally stages and commits the copy when the destination is
a Git working tree (push stays opt-in — an outward action).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import PublishError, PublishResult


class FilesystemPublisher:
    def __init__(self, dest: str, *, git: bool = False, git_push: bool = False,
                 commit_message: str = "Update PBICompass documentation") -> None:
        if not dest:
            raise PublishError("Filesystem target requires a destination directory "
                               "(--dest or PBICOMPASS_PUBLISH_DEST).")
        self.dest = Path(dest)
        self.git = git or git_push
        self.git_push = git_push
        self.commit_message = commit_message

    def publish(self, source: Path) -> PublishResult:
        source = Path(source)
        if not source.exists():
            raise PublishError(f"Source path not found: {source}")
        self.dest.mkdir(parents=True, exist_ok=True)

        copied = []
        files = [source] if source.is_file() else sorted(
            p for p in source.iterdir() if p.is_file())
        for f in files:
            shutil.copy2(f, self.dest / f.name)
            copied.append(f.name)

        detail = f"{self.dest}"
        if self.git:
            detail += self._git_commit()
        return PublishResult(target="filesystem", detail=detail, count=len(copied),
                             urls=[str(self.dest / n) for n in copied[:8]])

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.dest), *args],
                              capture_output=True, text=True)

    def _git_commit(self) -> str:
        inside = self._git("rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            raise PublishError(f"{self.dest} is not inside a Git working tree "
                               "(omit --git, or point --dest at a checked-out repo).")
        self._git("add", "-A")
        status = self._git("status", "--porcelain")
        if not status.stdout.strip():
            return " (git: nothing to commit)"
        commit = self._git("commit", "-m", self.commit_message)
        if commit.returncode != 0:
            raise PublishError(f"git commit failed: {commit.stderr.strip() or commit.stdout.strip()}")
        note = " (git: committed)"
        if self.git_push:
            push = self._git("push")
            if push.returncode != 0:
                raise PublishError(f"git push failed: {push.stderr.strip() or push.stdout.strip()}")
            note = " (git: committed and pushed)"
        return note
