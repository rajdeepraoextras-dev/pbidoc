"""Day 25 (§7.6/§10): the signed-in upload flow — session-cookie auth wired
into ``resolve_tenant``/``POST /jobs`` alongside (not instead of) the
existing Bearer-API-key path — plus the roadmap's own §10.7 auth/security
test bar: CSRF on the new session-authenticated upload route, session
fixation, and tenant isolation extended from "another API key" to "another
signed-in user's session".

Requires the service extras (fastapi/httpx/python-multipart); skips cleanly
without them, same convention as test_auth.py / test_dashboard.py. Uses
``base_url="https://testserver"`` for every session-cookie client, matching
the Day 21 finding: a ``Secure`` cookie (the production default) is never
re-sent by httpx's cookie jar over a plain ``http://`` test base URL.
"""

from __future__ import annotations

import io
import time
import unittest
import zipfile
from pathlib import Path

from pbicompass.service.accounts import AccountStore

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "SampleSales"


def _zip_fixture() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in FIXTURE_DIR.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(FIXTURE_DIR.parent))
    return buf.getvalue()


def _csrf(client) -> str:
    return client.cookies.get("pbicompass_csrf")


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class SessionUploadTest(unittest.TestCase):
    def setUp(self):
        self.accounts = AccountStore(":memory:")
        self.addCleanup(self.accounts.close)
        self.store = JobStore()
        self.addCleanup(self.store.close)
        self.app = create_app(
            self.store, require_auth=False, admin_token="t", account_store=self.accounts,
        )
        self.client = TestClient(self.app, base_url="https://testserver")

    def _signup(self, email="uploader@example.com", password="hunter2pass"):
        return self.client.post("/auth/signup", json={"email": email, "password": password})

    def _upload(self, client=None, csrf=True):
        client = client or self.client
        headers = {"X-CSRF-Token": _csrf(client)} if csrf else {}
        return client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none"},
            headers=headers,
        )

    def _wait(self, job_id, client=None, timeout=10.0):
        client = client or self.client
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = client.get(f"/jobs/{job_id}").json()
            if job["status"] in ("done", "failed"):
                return job
            time.sleep(0.05)
        self.fail("job did not finish in time")

    # -- the Day 25 done-when: a signed-in upload flow works ----------------
    def test_signed_in_upload_works_with_session_cookie_only(self):
        # No Authorization header, no manually-pasted API key — only the
        # session cookie signup itself set.
        self._signup()
        res = self._upload()
        self.assertEqual(res.status_code, 200, res.text)
        job_id = res.json()["job_id"]
        job = self._wait(job_id)
        self.assertEqual(job["status"], "done", job)
        # tagged to the signed-in user's own tenant, not "public" — and shows
        # up in their own dashboard job history.
        jobs = self.client.get("/app/api/jobs").json()["jobs"]
        self.assertIn(job_id, [j["job_id"] for j in jobs])

    def test_anonymous_upload_is_completely_unaffected(self):
        # No session, no API key -> still the pre-Day-25 "public" tenant
        # behavior. Guards against the resolve_tenant signature change
        # silently breaking the existing offline/anonymous path.
        anon = TestClient(self.app, base_url="https://testserver")
        res = self._upload(client=anon, csrf=False)
        self.assertEqual(res.status_code, 200, res.text)

    def test_api_key_upload_still_works_and_never_needs_csrf(self):
        # A Bearer/API-key upload is unaffected by the session-auth addition
        # — no CSRF check applies there (never an ambient credential).
        _acct, key = self.accounts.create_account("keyholder")
        client = TestClient(self.app, base_url="https://testserver")
        res = client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none"},
            headers={"Authorization": "Bearer " + key},
        )
        self.assertEqual(res.status_code, 200, res.text)

    # -- CSRF on the new session-authenticated upload path -------------------
    def test_session_upload_without_csrf_token_is_rejected(self):
        self._signup()
        res = self._upload(csrf=False)
        self.assertEqual(res.status_code, 403)

    def test_session_upload_with_wrong_csrf_token_is_rejected(self):
        self._signup()
        res = self.client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none"},
            headers={"X-CSRF-Token": "not-the-real-token"},
        )
        self.assertEqual(res.status_code, 403)

    # -- session fixation ------------------------------------------------------
    def test_login_never_authenticates_an_attacker_preset_session_cookie(self):
        # Session fixation: an attacker who gets a victim's browser to carry
        # a session cookie value *of the attacker's choosing* before the
        # victim logs in must not have that value become valid once the
        # victim actually authenticates. create_session() always mints a
        # brand-new random token on login, regardless of what the request
        # carried in -- proven end-to-end here, not just read from source.
        #
        # The planted cookie is sent via a raw ``Cookie`` header (not
        # ``client.cookies.set(...)``) so it never enters httpx's own
        # cookie jar — that sidesteps a jar/domain quirk where a manually
        # set cookie and the real Set-Cookie response can coexist under
        # different domain scopes and make a later ``.get()`` raise
        # ``CookieConflict``; a raw header is unambiguous either way.
        self._signup(email="victim@example.com", password="hunter2pass")
        self.client.post("/auth/logout", headers={"X-CSRF-Token": _csrf(self.client)})

        planted = "attacker-chosen-session-token"
        planted_cookie = {"Cookie": f"pbicompass_session={planted}"}

        # Before login: the planted value was never valid.
        self.assertEqual(
            TestClient(self.app, base_url="https://testserver")
            .get("/app/api/me", headers=planted_cookie).status_code,
            401,
        )

        # The victim logs in while their browser still carries that same
        # preset cookie value.
        victim = TestClient(self.app, base_url="https://testserver")
        login = victim.post(
            "/auth/login", json={"email": "victim@example.com", "password": "hunter2pass"},
            headers=planted_cookie,
        )
        self.assertEqual(login.status_code, 200, login.text)
        issued = victim.cookies.get("pbicompass_session")
        self.assertIsNotNone(issued)
        self.assertNotEqual(issued, planted)  # a fresh token was minted, not the attacker's

        # After login: the planted value is still dead — the attacker never
        # gained a live session out of having set it in advance.
        self.assertEqual(
            TestClient(self.app, base_url="https://testserver")
            .get("/app/api/me", headers=planted_cookie).status_code,
            401,
        )

    # -- tenant isolation, extended from API keys to sessions ----------------
    def test_another_users_session_cannot_see_this_users_job(self):
        user_a = TestClient(self.app, base_url="https://testserver")
        self._signup_as(user_a, "a@example.com", "hunter2pass-a")
        job_id = self._upload(client=user_a).json()["job_id"]
        self._wait(job_id, client=user_a)

        user_b = TestClient(self.app, base_url="https://testserver")
        self._signup_as(user_b, "b@example.com", "hunter2pass-b")
        self.assertEqual(user_b.get(f"/jobs/{job_id}").status_code, 404)
        self.assertEqual(
            user_b.get(f"/jobs/{job_id}/download", params={"format": "md"}).status_code, 404,
        )
        b_jobs = [j["id"] for j in user_b.get("/app/api/jobs").json()["jobs"]]
        self.assertNotIn(job_id, b_jobs)

    def _signup_as(self, client, email, password):
        return client.post("/auth/signup", json={"email": email, "password": password})


if __name__ == "__main__":
    unittest.main(verbosity=2)
