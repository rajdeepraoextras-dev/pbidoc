"""Day 20: accounts backup + restore drill (§9/§12).

A portable, stdlib-only logical backup/restore for ``AccountStore`` — proves
the actual restore-drill mechanism this project can exercise without a real
Postgres server or the ``pg_dump``/``pg_restore`` client binaries (same class
of "no live Postgres in this sandbox" gap as Days 17/18, addressed the same
way: exercised end-to-end against the real SQLite backend, and the identical
code path a Postgres-backed store would take).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pbicompass.service.accounts import AccountStore
from pbicompass.service.db_backup import backup_to_file, restore_from_file


class AccountStoreDumpRestoreTest(unittest.TestCase):
    def test_dump_is_content_free_and_round_trips(self):
        store = AccountStore(":memory:")
        self.addCleanup(store.close)
        with mock.patch.dict("pbicompass.service.accounts.PLAN_LIMITS",
                             {"free": 5, "pro": 200, "enterprise": 100000}, clear=True):
            acct, key = store.create_account("acme", name="Acme BI", plan="pro")
            store.try_consume("acme", "pro")
            store.try_consume("acme", "pro")

            snapshot = store.dump()
            self.assertEqual(len(snapshot["accounts"]), 1)
            self.assertEqual(snapshot["accounts"][0]["tenant"], "acme")
            # never the raw key -- only its hash, same as everywhere else in this store
            blob = json.dumps(snapshot)
            self.assertNotIn(key, blob)
            self.assertIn("key_hash", snapshot["accounts"][0])

            fresh = AccountStore(":memory:")
            self.addCleanup(fresh.close)
            fresh.restore(snapshot)

            restored = fresh.verify(key)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.tenant, "acme")
            self.assertEqual(restored.plan, "pro")
            self.assertEqual(fresh.usage_today("acme"), 2)

    def test_restore_is_idempotent(self):
        store = AccountStore(":memory:")
        self.addCleanup(store.close)
        store.create_account("t1")
        snapshot = store.dump()

        fresh = AccountStore(":memory:")
        self.addCleanup(fresh.close)
        fresh.restore(snapshot)
        fresh.restore(snapshot)  # applying the same snapshot twice must not error or duplicate
        self.assertEqual(len(fresh.list_accounts()), 1)

    def test_empty_store_dumps_and_restores_cleanly(self):
        store = AccountStore(":memory:")
        self.addCleanup(store.close)
        snapshot = store.dump()
        self.assertEqual(snapshot, {"version": 2, "accounts": [], "usage": [], "api_keys": []})

        fresh = AccountStore(":memory:")
        self.addCleanup(fresh.close)
        fresh.restore(snapshot)  # no-op, must not raise
        self.assertEqual(fresh.list_accounts(), [])


class FileBackupRestoreDrillTest(unittest.TestCase):
    """The actual restore-drill shape from DEPLOYMENT.md: back up a live
    store to a file, then restore that file into a brand-new (scratch)
    store and verify the data is genuinely there — not just that no
    exception was raised."""

    def test_backup_to_file_then_restore_into_a_scratch_store(self):
        store = AccountStore(":memory:")
        self.addCleanup(store.close)
        _, key_a = store.create_account("tenant-a", name="A Corp", plan="pro")
        _, key_b = store.create_account("tenant-b", name="B Corp", plan="free")
        store.try_consume("tenant-a", "pro")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            backup_path = Path(tmp) / "accounts-backup.json"
            count = backup_to_file(store, backup_path)
            self.assertEqual(count, 2)
            self.assertTrue(backup_path.exists())

            # the restore drill: a fresh, empty database standing in for a
            # scratch/staging DB -- never restore straight into production.
            scratch = AccountStore(":memory:")
            self.addCleanup(scratch.close)
            restored_count = restore_from_file(scratch, backup_path)
            self.assertEqual(restored_count, 2)

            self.assertEqual(scratch.verify(key_a).tenant, "tenant-a")
            self.assertEqual(scratch.verify(key_b).tenant, "tenant-b")
            self.assertEqual(scratch.usage_today("tenant-a"), 1)
            self.assertEqual(len(scratch.list_accounts()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
