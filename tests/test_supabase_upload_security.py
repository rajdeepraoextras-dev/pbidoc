"""Day 25 -> Day 29 (§7.6/§10): the signed-in upload flow — now Bearer-JWT
(Supabase) auth wired into ``resolve_tenant``/``POST /jobs`` alongside (not
instead of) the existing Bearer-API-key path, plus the roadmap's own §10.7
auth/security test bar, restated for the new model:

- CSRF and session-fixation cases from the old cookie-based test suite are
  **dropped as structurally moot** — Bearer auth (API key or Supabase JWT)
  is never an ambient browser credential, so there is nothing for either
  attack to exploit. That guarantee itself is asserted below
  (``test_api_key_and_jwt_uploads_never_need_a_csrf_header``).
- **Tenant isolation carries over verbatim**, now proven across two
  different Supabase users (different ``sub`` claims) instead of two
  different session cookies.
- **New**: expired/tampered/wrong-audience JWT rejection on the upload path
  itself — the equivalent, Bearer-JWT-shaped guarantee a cookie could no
  longer provide.

Requires the service extras (fastapi/httpx/python-multipart) *and* the auth
extra (PyJWT); skips cleanly without them, same convention as
test_auth.py/test_dashboard.py.
"""

from __future__ import annotations

import io
import json
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest import mock

from pbicompass.service.accounts import AccountStore

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False

try:
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from pbicompass.service.supabase_auth import SupabaseAuthConfig
    _HAVE_AUTH = True
except ImportError:  # pragma: no cover
    _HAVE_AUTH = False

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "SampleSales"


def _zip_fixture() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in FIXTURE_DIR.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(FIXTURE_DIR.parent))
    return buf.getvalue()


@unittest.skipUnless(_HAVE_SERVICE and _HAVE_AUTH,
                     "service + auth extras not installed (pip install \"pbicompass[service,auth]\")")
class SupabaseUploadTest(unittest.TestCase):
    KID = "test-kid-upload"
    _URL = "https://project-ref.supabase.co"

    @classmethod
    def setUpClass(cls):
        cls._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls._private_pem = cls._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls._private_key.public_key()))
        jwk["kid"] = cls.KID
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        cls._jwks = {"keys": [jwk]}

    def setUp(self):
        self.accounts = AccountStore(":memory:")
        self.addCleanup(self.accounts.close)
        self.store = JobStore()
        self.addCleanup(self.store.close)
        self.cfg = SupabaseAuthConfig(url=self._URL, anon_key="anon-key")
        self.app = create_app(
            self.store, require_auth=False, admin_token="t",
            account_store=self.accounts, supabase_config=self.cfg,
        )
        self.client = TestClient(self.app, base_url="https://testserver")

        from pbicompass.service import supabase_auth as supabase_auth_mod
        supabase_auth_mod._jwks_clients.clear()
        patcher = mock.patch.object(jwt.PyJWKClient, "fetch_data", return_value=self._jwks)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _token(self, email: str, sub: str | None = None, **claim_overrides) -> str:
        claims = {
            "sub": sub or uuid.uuid4().hex, "email": email, "aud": "authenticated",
            "iss": self.cfg.issuer, "exp": time.time() + 3600,
        }
        claims.update(claim_overrides)
        return jwt.encode(claims, self._private_pem, algorithm="RS256", headers={"kid": self.KID})

    def _headers(self, email="uploader@example.com", sub=None, **claim_overrides):
        return {"Authorization": "Bearer " + self._token(email, sub=sub, **claim_overrides)}

    def _upload(self, client=None, headers=None):
        client = client or self.client
        return client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none"},
            headers=headers or {},
        )

    def _wait(self, job_id, client=None, headers=None, timeout=10.0):
        client = client or self.client
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = client.get(f"/jobs/{job_id}", headers=headers or {}).json()
            if job["status"] in ("done", "failed"):
                return job
            time.sleep(0.05)
        self.fail("job did not finish in time")

    # -- the Day 25/29 done-when: a signed-in upload flow works, no key typed ---
    def test_signed_in_upload_works_with_jwt_only_no_api_key_no_engine_key(self):
        headers = self._headers()
        res = self._upload(headers=headers)
        self.assertEqual(res.status_code, 200, res.text)
        job_id = res.json()["job_id"]
        job = self._wait(job_id, headers=headers)
        self.assertEqual(job["status"], "done", job)
        # tagged to the signed-in user's own tenant, not "public" — and shows
        # up in their own dashboard job history.
        jobs = self.client.get("/app/api/jobs", headers=headers).json()["jobs"]
        self.assertIn(job_id, [j["job_id"] for j in jobs])

    def test_anonymous_upload_is_completely_unaffected(self):
        # No JWT, no API key -> still the pre-Day-25 "public" tenant
        # behavior. Guards against the resolve_tenant signature change
        # silently breaking the existing offline/anonymous path.
        res = self._upload()
        self.assertEqual(res.status_code, 200, res.text)

    def test_api_key_upload_still_works_unchanged(self):
        _acct, key = self.accounts.create_account("keyholder")
        res = self._upload(headers={"Authorization": "Bearer " + key})
        self.assertEqual(res.status_code, 200, res.text)

    def test_api_key_and_jwt_uploads_never_need_a_csrf_header(self):
        # Bearer auth (of either kind) is never an ambient browser credential
        # -- no X-CSRF-Token is sent, and neither path 403s for its absence,
        # unlike the retired session-cookie model.
        _acct, key = self.accounts.create_account("keyholder2")
        self.assertEqual(self._upload(headers={"Authorization": "Bearer " + key}).status_code, 200)
        self.assertEqual(self._upload(headers=self._headers("nocsrf@example.com")).status_code, 200)

    # -- invalid-credential rejection on the upload path itself --------------
    # A *supplied but invalid* credential must never be silently treated as
    # a valid identity -- proven two ways: (a) under require_auth=True it's a
    # hard 401 (never falls back to public); (b) under require_auth=False
    # (this class's default client) it degrades to the anonymous "public"
    # tenant -- the same fail-open-to-anonymous floor an invalid API key has
    # always had -- rather than ever resolving to a real tenant/account.
    def _strict_client(self) -> TestClient:
        app = create_app(self.store, require_auth=True, admin_token="t",
                         account_store=self.accounts, supabase_config=self.cfg)
        return TestClient(app, base_url="https://testserver")

    def test_expired_jwt_upload_is_rejected(self):
        headers = {"Authorization": "Bearer " + self._token("x@example.com", exp=time.time() - 60)}
        self.assertEqual(self._upload(client=self._strict_client(), headers=headers).status_code, 401)
        self.assertEqual(self.client.get("/me", headers=headers).json()["tenant"], "public")

    def test_wrong_audience_jwt_upload_is_rejected(self):
        headers = {"Authorization": "Bearer " + self._token("x@example.com", aud="not-authenticated")}
        self.assertEqual(self._upload(client=self._strict_client(), headers=headers).status_code, 401)
        self.assertEqual(self.client.get("/me", headers=headers).json()["tenant"], "public")

    def test_tampered_jwt_upload_is_rejected(self):
        token = self._token("x@example.com")
        header_b64, payload_b64, sig_b64 = token.split(".")
        tampered_sig = ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
        headers = {"Authorization": f"Bearer {header_b64}.{payload_b64}.{tampered_sig}"}
        self.assertEqual(self._upload(client=self._strict_client(), headers=headers).status_code, 401)
        self.assertEqual(self.client.get("/me", headers=headers).json()["tenant"], "public")

    # -- tenant isolation, extended from API keys to Supabase users ----------
    def test_another_users_jwt_cannot_see_this_users_job(self):
        a_headers = self._headers("a@example.com")
        job_id = self._upload(headers=a_headers).json()["job_id"]
        self._wait(job_id, headers=a_headers)

        b_headers = self._headers("b@example.com")
        self.assertEqual(self.client.get(f"/jobs/{job_id}", headers=b_headers).status_code, 404)
        self.assertEqual(
            self.client.get(f"/jobs/{job_id}/download", params={"format": "md"}, headers=b_headers).status_code,
            404,
        )
        b_jobs = [j["job_id"] for j in self.client.get("/app/api/jobs", headers=b_headers).json()["jobs"]]
        self.assertNotIn(job_id, b_jobs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
