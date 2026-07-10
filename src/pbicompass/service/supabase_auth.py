"""Supabase Auth — verifying a Supabase-issued access token (Day 27, Sprint 6).

Identity (signup, login, email verification, password reset, "Sign in with
Microsoft") is handled by Supabase's own hosted Auth product, not this app.
The frontend talks to Supabase directly (via ``supabase-js``) and sends the
resulting access token to us as a normal ``Authorization: Bearer <token>``
header — this module's only job is to verify that token and read its claims.

Verification prefers **JWKS** (RS256/ES256 — what a current Supabase project
signs with): ``jwt.PyJWKClient`` fetches and caches
``{SUPABASE_URL}/auth/v1/.well-known/jwks.json`` and transparently refetches
once if an unrecognized ``kid`` shows up (a key rotation), without looping
forever on a persistently-bad one. A shared ``SUPABASE_JWT_SECRET`` (HS256) is
supported only as a fallback for an older Supabase project still on a shared
signing secret, and only when the token's own header says ``HS256`` — we never
let a caller pick the verification algorithm.

Nothing here is imported unless ``SUPABASE_URL`` is configured (mirrors
``oidc.py``'s ``from_env`` pattern) — an install that never sets it stays on
the Bearer-API-key-only path with zero new dependencies pulled in.
"""

from __future__ import annotations

from dataclasses import dataclass


class SupabaseAuthError(Exception):
    """Any failure verifying a Supabase-issued token — bad signature, expired,
    wrong audience, malformed, or a JWKS fetch problem. Carries a short,
    content-free message safe to surface to a caller. Never lets a forged or
    otherwise-invalid token through silently."""


@dataclass
class SupabaseAuthConfig:
    url: str
    anon_key: str = ""
    service_role_key: str = ""
    jwt_secret: str = ""
    jwt_aud: str = "authenticated"

    @property
    def jwks_url(self) -> str:
        return self.url.rstrip("/") + "/auth/v1/.well-known/jwks.json"

    @property
    def issuer(self) -> str:
        return self.url.rstrip("/") + "/auth/v1"

    @classmethod
    def from_env(cls, env=None) -> "SupabaseAuthConfig | None":
        """Build from ``SUPABASE_*``. Returns ``None`` (feature disabled)
        unless ``SUPABASE_URL`` is set — everything else is optional (the
        service-role key and legacy HS256 secret aren't needed for a normal
        JWKS-verified request)."""
        import os
        env = env if env is not None else os.environ
        url = (env.get("SUPABASE_URL") or "").strip()
        if not url:
            return None
        return cls(
            url=url,
            anon_key=(env.get("SUPABASE_ANON_KEY") or "").strip(),
            service_role_key=(env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip(),
            jwt_secret=(env.get("SUPABASE_JWT_SECRET") or "").strip(),
            jwt_aud=(env.get("SUPABASE_JWT_AUD") or "authenticated").strip(),
        )


@dataclass
class SupabaseClaims:
    sub: str
    email: str | None
    email_verified: bool
    raw: dict


_JWKS_CACHE_LIFESPAN_SECONDS = 900  # 15 min
_jwks_clients: dict[str, object] = {}  # jwks_url -> jwt.PyJWKClient, one per configured project


def _get_jwks_client(config: SupabaseAuthConfig, jwt_module):
    client = _jwks_clients.get(config.jwks_url)
    if client is None:
        client = jwt_module.PyJWKClient(
            config.jwks_url, cache_keys=True, lifespan=_JWKS_CACHE_LIFESPAN_SECONDS
        )
        _jwks_clients[config.jwks_url] = client
    return client


def looks_like_jwt(value: str) -> bool:
    """Cheap shape check used by ``resolve_tenant()`` to dispatch an
    ``Authorization: Bearer`` value to Supabase-JWT verification instead of
    the API-key path — three non-empty, dot-separated segments. This is only
    a routing hint, never a security boundary; :func:`verify_jwt` does the
    real check."""
    parts = value.split(".")
    return len(parts) == 3 and all(parts)


def verify_jwt(token: str, config: SupabaseAuthConfig) -> SupabaseClaims:
    """Verify a Supabase-issued access token and return its claims. Raises
    :class:`SupabaseAuthError` on any failure — bad signature, expiry, wrong
    audience/issuer, or a malformed/unparseable token. Fails closed: there is
    no code path here that returns claims for a token that didn't fully
    verify."""
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - exercised via a fake module in tests
        raise RuntimeError(
            "Supabase JWT verification needs the 'auth' extra: "
            "pip install \"pbicompass[auth]\""
        ) from exc

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise SupabaseAuthError(f"Malformed token ({type(exc).__name__}).") from exc

    try:
        if header.get("alg") == "HS256":
            if not config.jwt_secret:
                raise SupabaseAuthError(
                    "Token is HS256 but no SUPABASE_JWT_SECRET is configured."
                )
            payload = jwt.decode(
                token, config.jwt_secret, algorithms=["HS256"],
                audience=config.jwt_aud, issuer=config.issuer,
            )
        else:
            # Asymmetric (RS256/ES256) — the normal case for a current
            # Supabase project. The allowed-algorithms list is fixed here,
            # not taken from the token's own header, so a token can't talk
            # its way into a different verification algorithm.
            client = _get_jwks_client(config, jwt)
            signing_key = client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token, signing_key.key, algorithms=["RS256", "ES256"],
                audience=config.jwt_aud, issuer=config.issuer,
            )
    except SupabaseAuthError:
        raise
    except Exception as exc:  # jwt.PyJWTError subclasses, PyJWKClientError, network errors, ...
        raise SupabaseAuthError(f"Token verification failed ({type(exc).__name__}).") from exc

    sub = payload.get("sub")
    if not sub:
        raise SupabaseAuthError("Token has no subject claim.")
    email = payload.get("email")
    email_verified = bool(
        payload.get("email_verified")
        or (payload.get("user_metadata") or {}).get("email_verified")
    )
    return SupabaseClaims(sub=sub, email=email, email_verified=email_verified, raw=payload)
