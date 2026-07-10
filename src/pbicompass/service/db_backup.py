"""File-based backup/restore for the accounts store (Day 20, §9/§12).

Wraps :meth:`AccountStore.dump`/:meth:`AccountStore.restore` with a plain
JSON file on disk, so an operator (or a cron job) can do:

    pbicompass account backup --out accounts-2026-08-04.json
    pbicompass account restore --in accounts-2026-08-04.json

against either the SQLite or the Postgres backend, without needing the
``pg_dump``/``pg_restore`` client binaries installed anywhere. This is a
lightweight, portable *complement* to whatever automated snapshotting a
managed Postgres provider (Neon/Supabase/RDS) already does at the
infrastructure level — see DEPLOYMENT.md's "Backups & restore drill" section
for how the two fit together, and for the actual restore-drill procedure
(restore into a **scratch** database and verify, before ever pointing
production at a restored snapshot).
"""

from __future__ import annotations

import json
from pathlib import Path

from .accounts import AccountStore


def backup_to_file(store: AccountStore, path: Path) -> int:
    """Write a snapshot to ``path``. Returns the number of account rows
    backed up (a quick sanity number for the caller to print/log)."""
    snapshot = store.dump()
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return len(snapshot["accounts"])


def restore_from_file(store: AccountStore, path: Path) -> int:
    """Restore a snapshot written by :func:`backup_to_file` into ``store``.
    Returns the number of account rows restored."""
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    store.restore(snapshot)
    return len(snapshot.get("accounts", []))
