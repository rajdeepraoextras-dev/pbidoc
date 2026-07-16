"""Tests for the C3 publish targets.

Every network target is exercised against a stubbed ``http_request`` — no real
request ever leaves this suite. The filesystem target runs for real against a
temp directory (including a real ``git init`` repo when git is available).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pbicompass.publish import PublishError, get_publisher
from pbicompass.publish import confluence as confluence_mod
from pbicompass.publish import sharepoint as sharepoint_mod
from pbicompass.publish import teams as teams_mod
from pbicompass.publish.base import collect_documents
from pbicompass.publish.confluence import ConfluencePublisher, html_to_storage
from pbicompass.publish.filesystem import FilesystemPublisher
from pbicompass.publish.sharepoint import SharePointPublisher
from pbicompass.publish.teams import TeamsPublisher

_HTML = """<!doctype html><html><head><style>body{color:red}</style>
<script>alert(1)</script></head><body>
<h1 class="t" style="color:blue">Technical</h1>
<p id="x">Body text</p><table><tr><td>A</td></tr></table>
<svg><title>diagram</title></svg><br>
</body></html>"""


def _bundle(dirpath: Path) -> Path:
    (dirpath / "technical.html").write_text(_HTML, encoding="utf-8")
    (dirpath / "executive.html").write_text(_HTML.replace("Technical", "Executive"),
                                            encoding="utf-8")
    (dirpath / "technical.md").write_text("# Technical\n\nBody", encoding="utf-8")
    # data/aux files that must never be published as pages
    (dirpath / "model.json").write_text("{}", encoding="utf-8")
    (dirpath / "index.html").write_text("<html>hub</html>", encoding="utf-8")
    return dirpath


class _Recorder:
    """Stands in for base.http_request; records calls, returns canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, *, headers=None, data=None, timeout=30.0):
        self.calls.append({"method": method, "url": url, "headers": headers or {},
                           "data": data, "timeout": timeout})
        return self.responses.pop(0) if self.responses else (200, "{}")


class CollectDocumentsTest(unittest.TestCase):
    def test_bundle_skips_data_and_hub_files(self):
        with tempfile.TemporaryDirectory() as td:
            docs = collect_documents(_bundle(Path(td)), prefer="html")
            self.assertEqual(sorted(d.name for d in docs), ["executive", "technical"])
            self.assertEqual(sorted(d.title for d in docs), ["Executive", "Technical"])

    def test_prefers_markdown_when_asked(self):
        with tempfile.TemporaryDirectory() as td:
            docs = collect_documents(_bundle(Path(td)), prefer="md")
            self.assertEqual([d.name for d in docs], ["technical"])

    def test_single_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = _bundle(Path(td)) / "technical.html"
            self.assertEqual([d.name for d in collect_documents(p)], ["technical"])

    def test_rejects_non_document_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PublishError):
                collect_documents(_bundle(Path(td)) / "model.json")
            with self.assertRaises(PublishError):
                collect_documents(Path(td) / "nope")


class HtmlToStorageTest(unittest.TestCase):
    def test_strips_head_style_script_svg_and_attrs(self):
        s = html_to_storage(_HTML)
        for banned in ("<style", "<script", "<svg", "<!doctype", "<head",
                       'class="', 'style="', 'id="'):
            self.assertNotIn(banned, s.lower())

    def test_keeps_content_tags(self):
        s = html_to_storage(_HTML)
        self.assertIn("<h1", s)
        self.assertIn("Technical", s)
        self.assertIn("<table>", s)
        self.assertIn("Body text", s)

    def test_closes_void_elements(self):
        self.assertIn("<br/>", html_to_storage(_HTML))


class ConfluenceTest(unittest.TestCase):
    def setUp(self):
        self._orig = confluence_mod.http_request

    def tearDown(self):
        confluence_mod.http_request = self._orig

    def _pub(self, responses):
        rec = _Recorder(responses)
        confluence_mod.http_request = rec
        return ConfluencePublisher(url="https://s.atlassian.net/wiki/", email="a@b.c",
                                   token="tok", space="BI"), rec

    def test_creates_page_when_absent(self):
        created = json.dumps({"_links": {"base": "https://s.atlassian.net/wiki",
                                         "webui": "/pages/1"}})
        pub, rec = self._pub([(200, json.dumps({"results": []})), (200, created)])
        with tempfile.TemporaryDirectory() as td:
            p = _bundle(Path(td)) / "technical.html"
            result = pub.publish(p)
        self.assertEqual(result.count, 1)
        self.assertEqual(rec.calls[1]["method"], "POST")
        payload = json.loads(rec.calls[1]["data"])
        self.assertEqual(payload["space"]["key"], "BI")
        self.assertEqual(payload["body"]["storage"]["representation"], "storage")
        self.assertNotIn("<script", payload["body"]["storage"]["value"])
        self.assertEqual(result.urls, ["https://s.atlassian.net/wiki/pages/1"])

    def test_updates_existing_page_in_place(self):
        existing = json.dumps({"results": [{"id": "42", "version": {"number": 7}}]})
        pub, rec = self._pub([(200, existing), (200, json.dumps({"_links": {}}))])
        with tempfile.TemporaryDirectory() as td:
            pub.publish(_bundle(Path(td)) / "technical.html")
        self.assertEqual(rec.calls[1]["method"], "PUT")
        self.assertIn("/rest/api/content/42", rec.calls[1]["url"])
        payload = json.loads(rec.calls[1]["data"])
        self.assertEqual(payload["version"]["number"], 8)  # bumped, not duplicated

    def test_parent_id_becomes_ancestor(self):
        rec = _Recorder([(200, json.dumps({"results": []})), (200, "{}")])
        confluence_mod.http_request = rec
        pub = ConfluencePublisher(url="https://s/wiki", email="e", token="t",
                                  space="BI", parent_id="99")
        with tempfile.TemporaryDirectory() as td:
            pub.publish(_bundle(Path(td)) / "technical.html")
        self.assertEqual(json.loads(rec.calls[1]["data"])["ancestors"], [{"id": "99"}])

    def test_auth_header_is_basic(self):
        pub, rec = self._pub([(200, json.dumps({"results": []})), (200, "{}")])
        with tempfile.TemporaryDirectory() as td:
            pub.publish(_bundle(Path(td)) / "technical.html")
        self.assertTrue(rec.calls[0]["headers"]["Authorization"].startswith("Basic "))

    def test_401_is_a_clear_error(self):
        pub, _ = self._pub([(401, "nope")])
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PublishError) as cm:
                pub.publish(_bundle(Path(td)) / "technical.html")
        self.assertIn("auth failed", str(cm.exception))

    def test_api_error_surfaces_body(self):
        pub, _ = self._pub([(200, json.dumps({"results": []})), (400, "bad space")])
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PublishError) as cm:
                pub.publish(_bundle(Path(td)) / "technical.html")
        self.assertIn("bad space", str(cm.exception))

    def test_missing_config_is_rejected(self):
        with self.assertRaises(PublishError):
            ConfluencePublisher(url="", email="e", token="t", space="BI")


class TeamsTest(unittest.TestCase):
    def setUp(self):
        self._orig = teams_mod.http_request

    def tearDown(self):
        teams_mod.http_request = self._orig

    def test_posts_card_with_document_names(self):
        rec = _Recorder([(200, "1")])
        teams_mod.http_request = rec
        pub = TeamsPublisher(webhook="https://outlook.office.com/hook",
                             link="https://wiki/docs")
        with tempfile.TemporaryDirectory() as td:
            result = pub.publish(_bundle(Path(td)))
        card = json.loads(rec.calls[0]["data"])
        self.assertEqual(card["@type"], "MessageCard")
        facts = {f["name"]: f["value"] for f in card["sections"][0]["facts"]}
        self.assertIn("Technical", facts["Documents"])
        self.assertEqual(facts["Count"], "2")
        self.assertEqual(card["potentialAction"][0]["targets"][0]["uri"], "https://wiki/docs")
        self.assertEqual(result.count, 2)

    def test_document_body_is_never_sent(self):
        rec = _Recorder([(200, "1")])
        teams_mod.http_request = rec
        with tempfile.TemporaryDirectory() as td:
            TeamsPublisher(webhook="https://h").publish(_bundle(Path(td)))
        self.assertNotIn("Body text", rec.calls[0]["data"].decode())

    def test_requires_https_webhook(self):
        with self.assertRaises(PublishError):
            TeamsPublisher(webhook="http://insecure")
        with self.assertRaises(PublishError):
            TeamsPublisher(webhook="")

    def test_failure_surfaces(self):
        teams_mod.http_request = _Recorder([(404, "gone")])
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PublishError):
                TeamsPublisher(webhook="https://h").publish(_bundle(Path(td)))


class SharePointTest(unittest.TestCase):
    def setUp(self):
        self._orig = sharepoint_mod.http_request

    def tearDown(self):
        sharepoint_mod.http_request = self._orig

    def test_uploads_every_file_verbatim(self):
        rec = _Recorder([(200, "{}")] * 5)
        sharepoint_mod.http_request = rec
        pub = SharePointPublisher(token="tok", drive_id="drive1", folder="Docs")
        with tempfile.TemporaryDirectory() as td:
            result = pub.publish(_bundle(Path(td)))
        self.assertEqual(result.count, 5)  # incl. model.json + index.html: verbatim
        self.assertTrue(all(c["method"] == "PUT" for c in rec.calls))
        self.assertTrue(all(c["headers"]["Authorization"] == "Bearer tok" for c in rec.calls))
        self.assertIn("/drives/drive1/root:/Docs/", rec.calls[0]["url"])
        # bytes are sent untouched
        sent = {c["url"].rsplit("/", 2)[-2]: c["data"] for c in rec.calls}
        self.assertIn(b"Body text", sent["technical.html:"])

    def test_content_type_by_extension(self):
        rec = _Recorder([(200, "{}")] * 5)
        sharepoint_mod.http_request = rec
        with tempfile.TemporaryDirectory() as td:
            SharePointPublisher(token="t", drive_id="d").publish(_bundle(Path(td)))
        types = {c["headers"]["Content-Type"] for c in rec.calls}
        self.assertIn("text/html", types)
        self.assertIn("application/json", types)

    def test_401_is_clear(self):
        sharepoint_mod.http_request = _Recorder([(401, "no")])
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PublishError) as cm:
                SharePointPublisher(token="t", drive_id="d").publish(Path(td) / "x.html")

    def test_oversize_file_is_flagged_not_truncated(self):
        sharepoint_mod.http_request = _Recorder([(200, "{}")])
        with tempfile.TemporaryDirectory() as td:
            big = Path(td) / "big.html"
            big.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
            with self.assertRaises(PublishError) as cm:
                SharePointPublisher(token="t", drive_id="d").publish(big)
        self.assertIn("simple-upload limit", str(cm.exception))

    def test_missing_config(self):
        with self.assertRaises(PublishError):
            SharePointPublisher(token="", drive_id="d")


class FilesystemTest(unittest.TestCase):
    def test_copies_all_files_verbatim(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            _bundle(Path(src))
            result = FilesystemPublisher(dest=dst).publish(Path(src))
        # dest is inspected before the temp dir goes away
            self.assertEqual(result.count, 5)
            names = sorted(p.name for p in Path(dst).iterdir())
            self.assertEqual(names, ["executive.html", "index.html", "model.json",
                                     "technical.html", "technical.md"])
            self.assertIn("Body text", (Path(dst) / "technical.html").read_text(encoding="utf-8"))

    def test_single_file(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            p = _bundle(Path(src)) / "technical.html"
            result = FilesystemPublisher(dest=dst).publish(p)
            self.assertEqual(result.count, 1)
            self.assertTrue((Path(dst) / "technical.html").exists())

    def test_creates_destination(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            target = Path(dst) / "new" / "nested"
            FilesystemPublisher(dest=str(target)).publish(_bundle(Path(src)))
            self.assertTrue((target / "technical.html").exists())

    def test_requires_dest(self):
        with self.assertRaises(PublishError):
            FilesystemPublisher(dest="")

    def test_git_on_non_repo_is_rejected(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            with self.assertRaises(PublishError) as cm:
                FilesystemPublisher(dest=dst, git=True).publish(_bundle(Path(src)))
            self.assertIn("not inside a Git working tree", str(cm.exception))

    @unittest.skipIf(shutil.which("git") is None, "git not available")
    def test_git_commits_into_a_real_repo(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            for cmd in (["init"], ["config", "user.email", "t@t.t"],
                        ["config", "user.name", "T"]):
                subprocess.run(["git", "-C", dst, *cmd], capture_output=True)
            result = FilesystemPublisher(dest=dst, git=True,
                                         commit_message="docs: update").publish(_bundle(Path(src)))
            self.assertIn("committed", result.detail)
            log = subprocess.run(["git", "-C", dst, "log", "--oneline"],
                                 capture_output=True, text=True)
            self.assertIn("docs: update", log.stdout)


class RegistryTest(unittest.TestCase):
    def test_unknown_target(self):
        with self.assertRaises(PublishError) as cm:
            get_publisher("dropbox")
        self.assertIn("Unknown publish target", str(cm.exception))

    def test_env_config_is_used(self):
        import os
        os.environ["PBICOMPASS_PUBLISH_DEST"] = "/tmp/x"
        try:
            self.assertEqual(str(get_publisher("filesystem").dest), str(Path("/tmp/x")))
        finally:
            del os.environ["PBICOMPASS_PUBLISH_DEST"]

    def test_explicit_opts_win_over_env(self):
        import os
        os.environ["PBICOMPASS_CONFLUENCE_SPACE"] = "FROM_ENV"
        try:
            pub = get_publisher("confluence", url="https://s", email="e",
                                token="t", space="EXPLICIT")
            self.assertEqual(pub.space, "EXPLICIT")
        finally:
            del os.environ["PBICOMPASS_CONFLUENCE_SPACE"]


if __name__ == "__main__":
    unittest.main()
