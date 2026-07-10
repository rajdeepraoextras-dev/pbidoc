"""Day 27: Supabase Auth JWT verification (Sprint 6, §2).

No real Supabase project or network call is used: ``jwt.PyJWKClient.fetch_data``
is monkeypatched to return a JWKS built from a locally-generated RSA keypair,
so ``verify_jwt`` exercises its real signature/expiry/audience/issuer
validation path (and the real library's refetch-once-on-unknown-kid logic)
against a genuine RS256-signed token, not a stub of it.
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from unittest import mock

try:
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    _HAVE_AUTH = True
except ImportError:  # pragma: no cover
    _HAVE_AUTH = False

if _HAVE_AUTH:
    from pbicompass.service import supabase_auth
    from pbicompass.service.supabase_auth import (
        SupabaseAuthConfig,
        SupabaseAuthError,
        looks_like_jwt,
        verify_jwt,
    )

_URL = "https://project-ref.supabase.co"
_SKIP_REASON = "PyJWT[crypto] not installed (pip install \"pbicompass[auth]\")"


def _make_config(**kwargs) -> "SupabaseAuthConfig":
    kwargs.setdefault("jwt_aud", "authenticated")
    return SupabaseAuthConfig(url=_URL, **kwargs)


@unittest.skipUnless(_HAVE_AUTH, _SKIP_REASON)
class SupabaseAuthConfigTest(unittest.TestCase):
    def test_from_env_disabled_without_url(self):
        self.assertIsNone(SupabaseAuthConfig.from_env(env={}))

    def test_from_env_populates_fields(self):
        cfg = SupabaseAuthConfig.from_env(env={
            "SUPABASE_URL": _URL,
            "SUPABASE_ANON_KEY": "anon",
            "SUPABASE_SERVICE_ROLE_KEY": "service",
            "SUPABASE_JWT_SECRET": "shh",
            "SUPABASE_JWT_AUD": "authenticated",
        })
        self.assertEqual(cfg.url, _URL)
        self.assertEqual(cfg.anon_key, "anon")
        self.assertEqual(cfg.service_role_key, "service")
        self.assertEqual(cfg.jwt_secret, "shh")

    def test_jwt_aud_defaults_to_authenticated(self):
        cfg = SupabaseAuthConfig.from_env(env={"SUPABASE_URL": _URL})
        self.assertEqual(cfg.jwt_aud, "authenticated")

    def test_jwks_url_and_issuer_derive_from_project_url(self):
        cfg = _make_config()
        self.assertEqual(cfg.jwks_url, _URL + "/auth/v1/.well-known/jwks.json")
        self.assertEqual(cfg.issuer, _URL + "/auth/v1")

    def test_trailing_slash_on_url_does_not_double_up(self):
        cfg = SupabaseAuthConfig(url=_URL + "/")
        self.assertEqual(cfg.jwks_url, _URL + "/auth/v1/.well-known/jwks.json")


class LooksLikeJwtTest(unittest.TestCase):
    """Pure string-shape logic — no PyJWT needed, always runs."""

    def test_three_segments_is_jwt_shaped(self):
        self.assertTrue(supabase_auth.looks_like_jwt("a.b.c") if _HAVE_AUTH
                         else _looks_like_jwt_standalone("a.b.c"))

    def test_api_key_is_not_jwt_shaped(self):
        fn = looks_like_jwt if _HAVE_AUTH else _looks_like_jwt_standalone
        self.assertFalse(fn("pbicompass_sk_abcdef0123456789"))

    def test_empty_segment_is_not_jwt_shaped(self):
        fn = looks_like_jwt if _HAVE_AUTH else _looks_like_jwt_standalone
        self.assertFalse(fn("a..c"))
        self.assertFalse(fn("a.b"))


def _looks_like_jwt_standalone(value: str) -> bool:  # mirrors supabase_auth.looks_like_jwt
    parts = value.split(".")
    return len(parts) == 3 and all(parts)


@unittest.skipUnless(_HAVE_AUTH, _SKIP_REASON)
class MissingExtraTest(unittest.TestCase):
    def test_missing_pyjwt_raises_clear_install_message(self):
        with mock.patch.dict(sys.modules, {"jwt": None}):
            with self.assertRaises(RuntimeError) as ctx:
                verify_jwt("a.b.c", _make_config())
        self.assertIn("pbicompass[auth]", str(ctx.exception))


@unittest.skipUnless(_HAVE_AUTH, _SKIP_REASON)
class VerifyJwtJwksTest(unittest.TestCase):
    """Exercises the real JWKS-verification path against a locally-generated
    RSA keypair -- jwt.PyJWKClient.fetch_data is the only thing monkeypatched
    (no real HTTP call); everything downstream (signature/exp/aud/iss checks,
    unknown-kid handling) is the real library code."""

    KID = "test-kid-1"

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
        self.config = _make_config()
        supabase_auth._jwks_clients.clear()  # fresh PyJWKClient per test, no cross-test cache bleed
        patcher = mock.patch.object(jwt.PyJWKClient, "fetch_data", return_value=self._jwks)
        self._fetch_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def _claims(self, **overrides) -> dict:
        base = {
            "sub": "supabase-user-123",
            "email": "user@example.com",
            "aud": self.config.jwt_aud,
            "iss": self.config.issuer,
            "exp": time.time() + 3600,
        }
        base.update(overrides)
        return base

    def _token(self, claims: dict, kid: str | None = KID) -> str:
        headers = {"kid": kid} if kid else {}
        return jwt.encode(claims, self._private_pem, algorithm="RS256", headers=headers)

    def test_valid_token_verified(self):
        token = self._token(self._claims())
        claims = verify_jwt(token, self.config)
        self.assertEqual(claims.sub, "supabase-user-123")
        self.assertEqual(claims.email, "user@example.com")

    def test_expired_token_rejected(self):
        token = self._token(self._claims(exp=time.time() - 60))
        with self.assertRaises(SupabaseAuthError):
            verify_jwt(token, self.config)

    def test_wrong_audience_rejected(self):
        token = self._token(self._claims(aud="some-other-audience"))
        with self.assertRaises(SupabaseAuthError):
            verify_jwt(token, self.config)

    def test_wrong_issuer_rejected(self):
        token = self._token(self._claims(iss="https://evil.example.com/auth/v1"))
        with self.assertRaises(SupabaseAuthError):
            verify_jwt(token, self.config)

    def test_tampered_signature_rejected(self):
        token = self._token(self._claims())
        header_b64, payload_b64, sig_b64 = token.split(".")
        tampered_sig = ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
        with self.assertRaises(SupabaseAuthError):
            verify_jwt(f"{header_b64}.{payload_b64}.{tampered_sig}", self.config)

    def test_malformed_token_rejected(self):
        with self.assertRaises(SupabaseAuthError):
            verify_jwt("not-a-jwt-at-all", self.config)

    def test_missing_subject_claim_rejected(self):
        claims = self._claims()
        del claims["sub"]
        token = self._token(claims)
        with self.assertRaises(SupabaseAuthError):
            verify_jwt(token, self.config)

    def test_unknown_kid_refetches_once_then_fails_without_hanging(self):
        # Signed with a kid the (mocked, single-key) JWKS never has --
        # exercises PyJWKClient's real "refresh and retry once" behavior
        # rather than looping or hanging.
        token = self._token(self._claims(), kid="a-kid-not-in-the-jwks")
        with self.assertRaises(SupabaseAuthError):
            verify_jwt(token, self.config)
        self.assertGreaterEqual(self._fetch_mock.call_count, 2)  # initial fetch + one refresh

    def test_email_verified_true_extracted(self):
        token = self._token(self._claims(email_verified=True))
        self.assertTrue(verify_jwt(token, self.config).email_verified)

    def test_email_verified_false_by_default(self):
        token = self._token(self._claims())
        self.assertFalse(verify_jwt(token, self.config).email_verified)

    def test_email_verified_via_user_metadata_fallback(self):
        token = self._token(self._claims(user_metadata={"email_verified": True}))
        self.assertTrue(verify_jwt(token, self.config).email_verified)


@unittest.skipUnless(_HAVE_AUTH, _SKIP_REASON)
class VerifyJwtHs256FallbackTest(unittest.TestCase):
    """The legacy shared-secret path -- only reached when the token's own
    header says HS256, and only accepted when SUPABASE_JWT_SECRET is set."""

    def _claims(self, **overrides) -> dict:
        base = {
            "sub": "supabase-user-456",
            "email": "hs256@example.com",
            "aud": "authenticated",
            "iss": _make_config().issuer,
            "exp": time.time() + 3600,
        }
        base.update(overrides)
        return base

    def test_verified_with_configured_secret(self):
        config = _make_config(jwt_secret="shared-secret")
        token = jwt.encode(self._claims(), "shared-secret", algorithm="HS256")
        claims = verify_jwt(token, config)
        self.assertEqual(claims.sub, "supabase-user-456")

    def test_rejected_without_configured_secret(self):
        config = _make_config()  # no jwt_secret set
        token = jwt.encode(self._claims(), "shared-secret", algorithm="HS256")
        with self.assertRaises(SupabaseAuthError):
            verify_jwt(token, config)

    def test_wrong_secret_rejected(self):
        config = _make_config(jwt_secret="right-secret")
        token = jwt.encode(self._claims(), "wrong-secret", algorithm="HS256")
        with self.assertRaises(SupabaseAuthError):
            verify_jwt(token, config)


if __name__ == "__main__":
    unittest.main()
