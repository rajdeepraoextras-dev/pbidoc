"""Microsoft Entra ID (Azure AD) sign-in — OIDC authorization-code flow (Day 23, §7.3).

The audience is Power BI users, so "Sign in with Microsoft" is the
lowest-friction path and the on-ramp to enterprise SSO later. Implemented as a
standard OIDC **authorization-code flow with PKCE**, confidential client.

**Zero new dependencies, on purpose** (same architecture call as Day 21's
scrypt and Day 22's smtplib): the token exchange is a stdlib
``urllib.request`` POST over verified TLS, and the ID token's claims are read
by base64url-decoding its payload — *not* by verifying its RS256 signature
against JWKS (which would need a crypto library). That is spec-sanctioned
**for this flow specifically**: OpenID Connect Core §3.1.3.7 allows a client
that obtains the ID token by **direct** communication with the token endpoint
(which an auth-code confidential client does — server-to-server, TLS-verified,
authenticated with the client secret) to rely on that TLS channel instead of
validating the token signature. We still validate issuer, audience, expiry,
and the anti-replay ``nonce``. A deployment that wants JWKS signature
verification on top can add it behind a crypto extra later without changing
this flow.

Nothing here is imported unless ``PBICOMPASS_OIDC_*`` is configured — an
install that never sets those env vars pulls in nothing new and the
``/auth/oidc/*`` routes report 404 (feature absent).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

STATE_TTL_SECONDS = 60 * 10  # 10 min — an auth round-trip is quick
_CLOCK_SKEW_SECONDS = 60
# Tenant values that are *multi*-tenant: the issuer in a returned token is the
# signing-in user's real tenant GUID, not one of these placeholders, so we
# can't require an exact issuer match for them.
_MULTI_TENANT = {"common", "organizations", "consumers"}


class OIDCError(Exception):
    """Any failure in the OIDC flow (bad token response, failed validation).
    Carries a short, content-free message safe to surface to a user."""


@dataclass
class OIDCConfig:
    tenant: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list[str] = field(default_factory=lambda: ["openid", "email", "profile"])

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant}"

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.authority}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.authority}/oauth2/v2.0/token"

    @property
    def issuer(self) -> str:
        return f"{self.authority}/v2.0"

    @classmethod
    def from_env(cls, env=None, public_url: str = "") -> "OIDCConfig | None":
        """Build from ``PBICOMPASS_OIDC_*``. Returns ``None`` (feature
        disabled) unless client id/secret, tenant, and a resolvable redirect
        URI are all present — the redirect URI is taken from
        ``PBICOMPASS_OIDC_REDIRECT_URI`` or derived from ``public_url`` +
        ``/auth/oidc/callback``."""
        import os
        env = env if env is not None else os.environ
        client_id = (env.get("PBICOMPASS_OIDC_CLIENT_ID") or "").strip()
        client_secret = (env.get("PBICOMPASS_OIDC_CLIENT_SECRET") or "").strip()
        tenant = (env.get("PBICOMPASS_OIDC_TENANT") or "common").strip()
        redirect = (env.get("PBICOMPASS_OIDC_REDIRECT_URI") or "").strip()
        if not redirect and public_url:
            redirect = public_url.rstrip("/") + "/auth/oidc/callback"
        if not (client_id and client_secret and redirect):
            return None
        return cls(tenant=tenant, client_id=client_id, client_secret=client_secret,
                   redirect_uri=redirect)


def generate_pkce() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` for PKCE S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(config: OIDCConfig, state: str, nonce: str, code_challenge: str) -> str:
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri,
        "response_mode": "query",
        "scope": " ".join(config.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return config.authorize_endpoint + "?" + urllib.parse.urlencode(params)


def exchange_code(config: OIDCConfig, code: str, code_verifier: str) -> dict:
    """POST the authorization code to the token endpoint (stdlib urllib, over
    the default verified-TLS context) and return the parsed token response.
    Raises :class:`OIDCError` on a transport or HTTP error."""
    data = urllib.parse.urlencode({
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
        "code_verifier": code_verifier,
        "scope": " ".join(config.scopes),
    }).encode("ascii")
    req = urllib.request.Request(
        config.token_endpoint, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # urllib.error.*, socket, JSON, ...
        raise OIDCError(f"Token exchange failed ({type(exc).__name__}).") from exc
    if "id_token" not in payload:
        raise OIDCError("Token response did not include an id_token.")
    return payload


def decode_id_token_claims(id_token: str) -> dict:
    """Base64url-decode the JWT payload (middle segment). Does **not** verify
    the signature — see the module docstring for why that's sound for this
    specific flow."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise OIDCError("Malformed id_token.")
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise OIDCError(f"Could not decode id_token claims ({type(exc).__name__}).") from exc


def validate_claims(claims: dict, config: OIDCConfig, expected_nonce: str, now: float | None = None) -> None:
    """Validate audience, expiry, nonce, and issuer. Raises :class:`OIDCError`
    on any failure."""
    now = time.time() if now is None else now
    aud = claims.get("aud")
    if aud != config.client_id:
        raise OIDCError("id_token audience mismatch.")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or now > exp + _CLOCK_SKEW_SECONDS:
        raise OIDCError("id_token is expired.")
    if not expected_nonce or claims.get("nonce") != expected_nonce:
        raise OIDCError("id_token nonce mismatch (possible replay).")
    iss = claims.get("iss") or ""
    if config.tenant in _MULTI_TENANT:
        # Multi-tenant app: the issuer is the user's own tenant GUID; require
        # the Microsoft issuer shape and a tenant id claim rather than an
        # exact match against the "common"/"organizations" placeholder.
        if not iss.startswith("https://login.microsoftonline.com/") or not claims.get("tid"):
            raise OIDCError("id_token issuer not recognized.")
    elif iss != config.issuer:
        raise OIDCError("id_token issuer mismatch.")


def email_from_claims(claims: dict) -> str | None:
    """Best-effort email extraction across the claim names Entra may use."""
    for key in ("email", "preferred_username", "upn"):
        value = claims.get(key)
        if value and "@" in value:
            return value.strip().lower()
    return None


def name_from_claims(claims: dict) -> str:
    return (claims.get("name") or "").strip()
