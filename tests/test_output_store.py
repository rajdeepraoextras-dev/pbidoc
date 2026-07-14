from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from pbicompass.service.output_store import (
    FilesystemOutputStore,
    MemoryOutputStore,
    SupabaseStorageOutputStore,
)


class MemoryOutputStoreTest(unittest.TestCase):
    def test_expires_outputs(self):
        store = MemoryOutputStore()
        store.put_many("job1", {"md": b"# Report"}, time.time() - 1)
        self.assertIsNone(store.get("job1", "md"))


class FilesystemOutputStoreTest(unittest.TestCase):
    def test_roundtrip_and_delete(self):
        with tempfile.TemporaryDirectory() as td:
            store = FilesystemOutputStore(Path(td))
            store.put_many("job1", {"audit.md": b"ok"}, time.time() + 60)
            self.assertEqual(store.get("job1", "audit.md"), b"ok")
            store.delete_job("job1", ["audit.md"])
            self.assertIsNone(store.get("job1", "audit.md"))


class SupabaseStorageOutputStoreTest(unittest.TestCase):
    def test_upload_download_and_batch_delete_request_shape(self):
        calls = []

        class FakeResponse:
            def __init__(self, body: bytes = b"{}") -> None:
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self.body

        def fake_urlopen(req, timeout=0):
            calls.append({
                "method": req.get_method(),
                "url": req.full_url,
                "body": req.data,
                "headers": dict(req.header_items()),
                "timeout": timeout,
            })
            return FakeResponse(b"downloaded" if req.get_method() == "GET" else b"{}")

        store = SupabaseStorageOutputStore(
            "https://example.supabase.co", "service-role", "pbicompass-outputs",
            prefix="outputs", timeout=12,
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            store.put_many("job1", {"audit.md": b"hello"}, time.time() + 60)
            self.assertEqual(store.get("job1", "audit.md"), b"downloaded")
            store.delete_job("job1", ["audit.md", "zip"])

        self.assertEqual(calls[0]["method"], "POST")
        self.assertIn("/storage/v1/object/pbicompass-outputs/outputs/job1/audit.md", calls[0]["url"])
        self.assertEqual(calls[0]["body"], b"hello")
        self.assertEqual(calls[1]["method"], "GET")
        self.assertEqual(calls[2]["method"], "DELETE")
        self.assertEqual(
            json.loads(calls[2]["body"].decode("utf-8")),
            {"prefixes": ["outputs/job1/audit.md", "outputs/job1/zip"]},
        )
        self.assertEqual(calls[2]["timeout"], 12)


if __name__ == "__main__":
    unittest.main()
