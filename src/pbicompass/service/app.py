"""FastAPI application: upload -> async job -> status -> download.

Serves a single-page web UI at ``/`` and a small JSON API. All processing runs
inside a per-job sandbox via :func:`process_job`; the app itself never persists
uploads or extracted metadata.

Multi-tenancy (Phase 5): when auth is enabled, requests must carry an API key
(``Authorization: Bearer <key>`` or ``X-API-Key``). Jobs are tagged with the
caller's tenant and only that tenant can read/download them; per-plan daily
quotas implement the freemium tier. With auth disabled (the local/self-hosted
default) everything runs as the ``public`` tenant with no limits.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import secrets
import uuid
from pathlib import Path

from fastapi import (BackgroundTasks, FastAPI, File, Form, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)

from . import oidc as oidc_mod
from .accounts import (RESET_TOKEN_TTL_SECONDS, SESSION_TTL_SECONDS,
                       VERIFY_TOKEN_TTL_SECONDS, AccountStore)
from .admin import AdminGuard, verify_admin_token
from .email import (EmailBackend, build_email_backend, password_reset_email,
                    public_url, verification_email)
from .oidc import OIDCConfig
from .jobs import JobStatus, JobStore
from .logging_config import configure_logging, job_id_var, request_id_var
from .metrics import MetricsRegistry
from .ratelimit import RateLimiter
from .sandbox import JobSandbox
from .sentry_config import init_sentry
from .worker import process_job

log = logging.getLogger("pbicompass.service.app")

_ALLOWED_SUFFIXES = {".pbix", ".zip", ".pbip"}
_CONTENT_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "yaml": "application/x-yaml; charset=utf-8",
}
_STATIC = Path(__file__).parent / "static"
_SESSION_COOKIE = "pbicompass_session"
_CSRF_COOKIE = "pbicompass_csrf"


def _max_upload_bytes() -> int:
    return int(os.environ.get("PBICOMPASS_MAX_UPLOAD_MB", "100")) * 1024 * 1024


def _job_timeout_seconds() -> int:
    return int(os.environ.get("PBICOMPASS_JOB_TIMEOUT_SECONDS", "600"))


def _queue_mode() -> str:
    return (os.environ.get("PBICOMPASS_QUEUE") or "inline").strip().lower()


def _upload_rate_limit() -> int:
    return int(os.environ.get("PBICOMPASS_UPLOAD_RATE_LIMIT", "20"))


def _upload_rate_window_seconds() -> float:
    return float(os.environ.get("PBICOMPASS_UPLOAD_RATE_WINDOW_SECONDS", "60"))


def _auth_rate_limit() -> int:
    return int(os.environ.get("PBICOMPASS_AUTH_RATE_LIMIT", "10"))


def _auth_rate_window_seconds() -> float:
    return float(os.environ.get("PBICOMPASS_AUTH_RATE_WINDOW_SECONDS", "60"))


def _session_ttl_seconds() -> int:
    return int(os.environ.get("PBICOMPASS_SESSION_TTL_SECONDS", str(SESSION_TTL_SECONDS)))


def _cookie_secure() -> bool:
    # Default on (HTTPS-only cookies) — every deployment guide in this repo
    # (DEPLOYMENT.md) puts TLS in front of the app. Only disable for a plain
    # http local-dev session, never in production.
    return os.environ.get("PBICOMPASS_COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no")


def _require_email_verification() -> bool:
    # Off by default so a fresh self-host isn't locked out of its own login
    # before any email provider is configured. A hosted SaaS turns this on.
    return os.environ.get("PBICOMPASS_REQUIRE_EMAIL_VERIFICATION", "").strip().lower() in ("1", "true", "yes")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _safe_basename(filename: str) -> str:
    stem = Path(filename or "documentation").stem
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return cleaned or "documentation"


def _api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def _html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))


def _auth_result_page(title: str, message: str) -> str:
    """A deliberately minimal, dependency-free result page for the one-click
    verify link and the reset flow. Not the styled account UI (Day 25) — just
    enough to give a human clicking an emailed link a clear outcome."""
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_html_escape(title)} — PBICompass</title>"
        "<div style='max-width:34rem;margin:4rem auto;font-family:system-ui,sans-serif;"
        "padding:0 1rem;color:#1e293b'>"
        f"<h1 style='font-size:1.4rem'>{_html_escape(title)}</h1>"
        f"<p style='color:#475569'>{_html_escape(message)}</p>"
        "<p><a href='/' style='color:#4f46e5'>← Back to PBICompass</a></p></div>"
    )


def _reset_form_page(token: str) -> str:
    safe_token = _html_escape(token)
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Reset your password — PBICompass</title>"
        "<div style='max-width:34rem;margin:4rem auto;font-family:system-ui,sans-serif;"
        "padding:0 1rem;color:#1e293b'>"
        "<h1 style='font-size:1.4rem'>Choose a new password</h1>"
        "<form method='post' action='/auth/reset' style='display:flex;flex-direction:column;gap:.75rem'>"
        f"<input type='hidden' name='token' value='{safe_token}'>"
        "<input type='password' name='password' required minlength='8' "
        "placeholder='New password (min 8 characters)' "
        "style='padding:.6rem;border:1px solid #cbd5e1;border-radius:.4rem'>"
        "<button type='submit' style='padding:.6rem;background:#4f46e5;color:#fff;"
        "border:0;border-radius:.4rem;cursor:pointer'>Update password</button>"
        "</form></div>"
    )


async def _read_token_and_password(request: Request) -> tuple[str, str]:
    """Accept the reset payload as either JSON (API clients) or a classic
    form post (the /auth/reset landing page's own form)."""
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        body = await request.json()
        return (body.get("token") or "").strip(), body.get("password") or ""
    form = await request.form()
    return (form.get("token") or "").strip(), form.get("password") or ""


def create_app(
    store: JobStore | None = None,
    *,
    sandbox_root: str | None = None,
    account_store: AccountStore | None = None,
    require_auth: bool | None = None,
    admin_token: str | None = None,
    admin_guard: AdminGuard | None = None,
    email_backend: EmailBackend | None = None,
    oidc_config: OIDCConfig | None = None,
) -> FastAPI:
    configure_logging()
    if init_sentry():
        log.info("sentry error tracking enabled")

    app = FastAPI(title="PBICompass — Power BI Documentation Generator", version="0.1.0")
    if require_auth is None:
        require_auth = os.environ.get("PBICOMPASS_REQUIRE_AUTH", "").lower() in ("1", "true", "yes")
    if admin_token is None:
        admin_token = os.environ.get("PBICOMPASS_ADMIN_TOKEN") or None
    admin_guard = admin_guard or AdminGuard()
    # Snapshotted once at startup (like require_auth), not read per-request:
    # a deployment sets it once, and this keeps it deterministic/injectable.
    require_email_verification = _require_email_verification()
    # Day 22: transactional email (verify/reset). Default = console backend
    # (logs the link, needs no provider) so the flow works on a bare
    # self-host; env selects SMTP for real delivery. Injectable for tests.
    email_backend = email_backend or build_email_backend()
    app.state.email_backend = email_backend
    # Day 23: "Sign in with Microsoft" (Entra ID OIDC). None unless
    # PBICOMPASS_OIDC_* is configured — the /auth/oidc/* routes are 404 when
    # absent, so an install that never sets it sees no new surface at all.
    if oidc_config is None:
        oidc_config = OIDCConfig.from_env(public_url=public_url())
    app.state.oidc_config = oidc_config

    # The account store backs both end-user auth (when require_auth is on)
    # and the admin panel (which needs it to mint/list/revoke accounts even
    # before auth is enforced, so an operator can set up keys first).
    owns_account_store = account_store is None and (require_auth or bool(admin_token))
    if owns_account_store:
        account_store = AccountStore(os.environ.get("PBICOMPASS_DB", "pbicompass.db"))

    # Day 20 (§9/§11): one registry per app instance backs the /metrics
    # endpoint (jobs/min, failure rate, token-cost proxy, 429 rate) — see
    # metrics.py's own docstring for why this is naturally per-process/
    # per-instance, matching how every other in-memory piece of this
    # service already scopes.
    metrics = MetricsRegistry()
    app.state.metrics = metrics
    upload_rate_limiter = RateLimiter(_upload_rate_limit(), _upload_rate_window_seconds())
    # Day 21 (§7.5): "rate-limit all auth routes" — a separate limiter
    # instance/budget from the upload one, since login/signup abuse and
    # upload abuse are different threats with different acceptable volumes.
    auth_rate_limiter = RateLimiter(_auth_rate_limit(), _auth_rate_window_seconds())
    # Reuses admin.py's brute-force-lockout *pattern* (a distinct instance —
    # a bad admin-token guess and a bad login attempt are unrelated events)
    # per §7.5's explicit instruction.
    login_guard = AdminGuard()

    # Persistent by default (A2-1): a file path (ideally on a mounted volume,
    # per DEPLOYMENT.md) so an in-flight/finished job survives a worker
    # restart instead of 404ing on the next poll. Tests that construct their
    # own ``JobStore()`` keep today's in-memory behavior unchanged.
    owns_job_store = store is None
    if owns_job_store:
        store = JobStore(
            os.environ.get("PBICOMPASS_JOBS_DB", "pbicompass_jobs.db"),
            processing_timeout_seconds=_job_timeout_seconds(),
        )
    # Attach this app's metrics registry to whichever store it ends up using
    # (freshly constructed above, or explicitly passed in — e.g. by tests)
    # so job counts are tracked either way; never overrides a store that
    # already has its own registry wired.
    if store.metrics is None:
        store.metrics = metrics
    app.state.store = store
    app.state.accounts = account_store
    app.state.require_auth = require_auth
    index_html = (_STATIC / "index.html").read_text(encoding="utf-8")
    admin_html = (_STATIC / "admin.html").read_text(encoding="utf-8")
    app_html = (_STATIC / "app.html").read_text(encoding="utf-8")

    if owns_account_store:
        @app.on_event("shutdown")
        def _close_account_store() -> None:
            account_store.close()

    if owns_job_store:
        @app.on_event("shutdown")
        def _close_job_store() -> None:
            store.close()

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        # Correlates every log line emitted while handling this request
        # (see logging_config.py) — content-free, just an opaque id.
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = rid
        return response

    def resolve_tenant(request: Request) -> tuple[str, str, bool]:
        """Return ``(tenant, plan, via_session)``. Raises 401 when auth is
        required and absent. Checks a Bearer/X-API-Key header first (the
        programmatic/API path, unchanged); falls back to a signed-in
        browser session cookie (Day 25, §7.6/§10) — the signed-in upload UI
        has no API key of its own to send, only its session. ``via_session``
        tells a state-changing caller (``POST /jobs``) whether it needs the
        CSRF double-submit check: a Bearer/API-key header is never ambient
        (a cross-site page can't attach a header it doesn't already have),
        a session cookie is."""
        key = _api_key(request)
        if account_store and key:
            acct = account_store.verify(key)
            if acct:
                return acct.tenant, acct.plan, False
            # An explicitly supplied key that doesn't verify fails as
            # itself — it must never silently fall back to an ambient
            # session cookie that happens to be present on the same
            # browser/client (e.g. a dashboard user testing a revoked key
            # while still signed in). Session fallback is only for
            # requests that sent no key at all.
        elif account_store:
            session_token = request.cookies.get(_SESSION_COOKIE)
            if session_token:
                info = account_store.verify_session(session_token)
                if info:
                    acct = account_store.account_for_user(info.user.id)
                    if acct:
                        return acct.tenant, acct.plan, True
        if require_auth:
            raise HTTPException(
                status_code=401,
                detail="A valid API key or a signed-in session is required. Send "
                       "'Authorization: Bearer <key>' or sign in at /app.",
            )
        return "public", "free", False

    def _set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
        max_age = _session_ttl_seconds()
        secure = _cookie_secure()
        # HttpOnly: never readable by page JS (the bearer credential itself).
        response.set_cookie(_SESSION_COOKIE, session_token, httponly=True, secure=secure,
                            samesite="lax", max_age=max_age, path="/")
        # Deliberately NOT HttpOnly: the double-submit CSRF pattern requires
        # same-site page JS to read this cookie and echo it back as a header
        # on state-changing requests — its own security property depends on
        # cross-site pages being unable to read another origin's cookies,
        # not on this one being hidden from same-site JS.
        response.set_cookie(_CSRF_COOKIE, csrf_token, httponly=False, secure=secure,
                            samesite="lax", max_age=max_age, path="/")

    def _clear_auth_cookies(response: Response) -> None:
        response.delete_cookie(_SESSION_COOKIE, path="/")
        response.delete_cookie(_CSRF_COOKIE, path="/")

    def _require_csrf(request: Request) -> None:
        """Double-submit CSRF check for a state-changing, session-cookie-
        authenticated request (Bearer/API-key requests never need this — a
        cross-site page can't make a browser attach an Authorization header
        it doesn't already have, so there's no ambient credential for CSRF
        to exploit there)."""
        cookie_csrf = request.cookies.get(_CSRF_COOKIE)
        header_csrf = request.headers.get("x-csrf-token")
        if not cookie_csrf or not header_csrf or not secrets.compare_digest(cookie_csrf, header_csrf):
            raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")

    def _require_user(request: Request):
        """Resolve the signed-in user from the session cookie, or 401. This is
        the account-dashboard's auth (Day 24) — session-based, no admin token
        — returning ``(user, account)``. Distinct from ``resolve_tenant``
        (API-key auth for programmatic /jobs)."""
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        token = request.cookies.get(_SESSION_COOKIE)
        info = account_store.verify_session(token) if token else None
        if not info:
            raise HTTPException(status_code=401, detail="Not signed in.")
        acct = account_store.account_for_user(info.user.id)
        if not acct:
            # A user with no account shouldn't happen (signup always makes
            # one), but fail closed rather than hand back a half-state.
            raise HTTPException(status_code=401, detail="Account not found for this session.")
        return info.user, acct

    def _require_admin(request: Request) -> None:
        """Gate an admin endpoint. 503 if the panel isn't configured at all,
        429 if this client is locked out, 401 on a bad/missing token."""
        if not admin_token:
            raise HTTPException(
                status_code=503,
                detail="Admin panel is not configured. Set PBICOMPASS_ADMIN_TOKEN.",
            )
        client_id = request.client.host if request.client else "unknown"
        if admin_guard.is_locked(client_id):
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
        supplied = request.headers.get("x-admin-token")
        if not verify_admin_token(admin_token, supplied):
            admin_guard.record_failure(client_id)
            raise HTTPException(status_code=401, detail="Invalid admin token.")
        admin_guard.record_success(client_id)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return index_html

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page() -> str:
        return admin_html

    @app.get("/app", response_class=HTMLResponse)
    def app_page() -> str:
        """The account dashboard (Day 24, §7.6). The page is served to anyone;
        its JS calls /app/api/me and shows either a sign-in form or the
        dashboard depending on the 200/401 — so it's self-contained and needs
        no separate login page."""
        return app_html

    @app.post("/admin/api/verify")
    def admin_verify(request: Request) -> dict:
        _require_admin(request)
        return {"ok": True}

    @app.get("/admin/api/accounts")
    def admin_list_accounts(request: Request) -> dict:
        _require_admin(request)
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        accounts = []
        for acct in account_store.list_accounts():
            limit = account_store.limit_for(acct.plan)
            used = account_store.usage_today(acct.tenant)
            accounts.append({
                "id": acct.id, "tenant": acct.tenant, "name": acct.name,
                "plan": acct.plan, "created_at": acct.created_at,
                "used_today": used, "daily_limit": limit,
            })
        return {"accounts": accounts}

    @app.post("/admin/api/accounts")
    async def admin_create_account(request: Request) -> dict:
        _require_admin(request)
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        body = await request.json()
        tenant = (body.get("tenant") or "").strip()
        if not tenant:
            raise HTTPException(status_code=400, detail="'tenant' is required.")
        name = (body.get("name") or "").strip()
        plan = (body.get("plan") or "free").strip()
        try:
            acct, key = account_store.create_account(tenant, name=name, plan=plan)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "account": {
                "id": acct.id, "tenant": acct.tenant, "name": acct.name,
                "plan": acct.plan, "created_at": acct.created_at,
            },
            "api_key": key,
        }

    @app.delete("/admin/api/accounts/{account_id}")
    def admin_revoke_account(account_id: str, request: Request) -> dict:
        _require_admin(request)
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        if not account_store.revoke_account(account_id):
            raise HTTPException(status_code=404, detail="Account not found.")
        return {"ok": True}

    def _check_queue(timeout: float = 1.5) -> bool:
        """Reachability probe for the Celery broker — only meaningful in
        ``celery`` queue mode (inline mode has no external dependency, so
        it's trivially healthy). Bounded by an explicit wall-clock deadline
        in a worker thread rather than trusting the driver's own socket
        timeout: observed directly in development, a plain redis-py
        ``socket_connect_timeout`` did not reliably bound a connect attempt
        to an unreachable host, so this never blocks the response past
        ``timeout`` regardless of what the underlying client does."""
        if _queue_mode() != "celery":
            return True

        def _probe() -> bool:
            from .celery_app import celery_app
            with celery_app.connection() as conn:
                conn.ensure_connection(max_retries=0, timeout=1)
            return True

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            return pool.submit(_probe).result(timeout=timeout)
        except Exception:
            return False
        finally:
            pool.shutdown(wait=False)  # never wait on a hung probe thread

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        checks: dict[str, bool] = {}
        try:
            app.state.store.get("__healthz_probe__")  # cheap DB round-trip, no-op result
            checks["jobs_db"] = True
        except Exception:
            log.warning("healthz: jobs_db check failed")
            checks["jobs_db"] = False
        if account_store is not None:
            try:
                account_store.usage_today("__healthz_probe__")
                checks["accounts_db"] = True
            except Exception:
                log.warning("healthz: accounts_db check failed")
                checks["accounts_db"] = False
        checks["queue"] = _check_queue()
        ok = all(checks.values())
        return JSONResponse({"ok": ok, "checks": checks}, status_code=200 if ok else 503)

    @app.get("/metrics")
    def metrics_endpoint(request: Request, format: str = Query("json")) -> Response:
        """Operational metrics (Day 20, §9/§11) — jobs/min, failure rate, a
        token-count cost proxy, and 429 rate. Gated by the same admin token
        as the rest of the operator surface (a Prometheus scrape config can
        supply ``Authorization``/``X-Admin-Token`` just as easily as a
        browser can); content-free (counts and integer token numbers only,
        never report data)."""
        _require_admin(request)
        if format == "prometheus":
            return PlainTextResponse(app.state.metrics.to_prometheus_text())
        return JSONResponse(app.state.metrics.snapshot())

    @app.get("/me")
    def me(request: Request) -> dict:
        tenant, plan, _via_session = resolve_tenant(request)
        out = {"tenant": tenant, "plan": plan, "auth_required": require_auth}
        if account_store and tenant != "public":
            used, limit = account_store.usage_today(tenant), account_store.limit_for(plan)
            out.update(used_today=used, daily_limit=limit, remaining=max(0, limit - used))
        return out

    def _user_payload(user) -> dict:
        return {"id": user.id, "email": user.email, "email_verified": user.email_verified}

    def _auth_link(path: str, token: str) -> str:
        # Absolute when PBICOMPASS_PUBLIC_URL is set (prod), a bare path
        # otherwise — still usable by an operator reading the console backend.
        base = public_url()
        return f"{base}{path}?token={token}" if base else f"{path}?token={token}"

    def _send_verification_email(user) -> None:
        token = account_store.create_email_token(user.id, "verify", VERIFY_TOKEN_TTL_SECONDS)
        link = _auth_link("/auth/verify", token)
        email_backend.send(verification_email(user.email, link))

    @app.post("/auth/signup")
    async def auth_signup(request: Request, response: Response) -> dict:
        """Self-serve signup (Day 21, §7.1/§7.5): creates a user, a new
        account/tenant they own, and an API key on it — then logs them in
        (session + CSRF cookies), matching the common "auto-login after
        signup" UX, and sends an email-verification link (Day 22). Session-
        based ``/jobs`` access is still deferred to the account-dashboard/
        upload-UI work in Days 24-25 (which is also where a session's CSRF
        story for state-changing, non-auth routes gets decided) — today,
        the API-key returned here is this user's path to `/jobs` until then.
        """
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        if not auth_rate_limiter.allow(_client_ip(request)):
            metrics.record_rate_limited()
            raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
        body = await request.json()
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        name = (body.get("name") or "").strip()
        try:
            user, acct, api_key = account_store.create_user(email, password, name=name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _send_verification_email(user)
        raw_session, csrf_token = account_store.create_session(user.id, ttl_seconds=_session_ttl_seconds())
        _set_auth_cookies(response, raw_session, csrf_token)
        return {
            "user": _user_payload(user),
            "tenant": acct.tenant, "plan": acct.plan,
            "api_key": api_key,  # shown once, same convention as an admin-created account
            "verification_email_sent": True,
        }

    @app.post("/auth/login")
    async def auth_login(request: Request, response: Response) -> dict:
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        if not auth_rate_limiter.allow(_client_ip(request)):
            metrics.record_rate_limited()
            raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
        client_id = _client_ip(request)
        if login_guard.is_locked(client_id):
            raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")
        body = await request.json()
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        user = account_store.authenticate(email, password)
        if not user:
            login_guard.record_failure(client_id)
            # Deliberately identical for "no such user" and "wrong password"
            # (AccountStore.authenticate already collapses this) so a failed
            # attempt can't be used to enumerate registered emails.
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        login_guard.record_success(client_id)
        # Day 22 gate: when email verification is required, an unverified
        # user can't complete login — and we (re)send a fresh verification
        # link so a user who lost the first one can still get in, rather than
        # dead-ending them. The credentials were already correct at this
        # point, so this is not an enumeration vector.
        if require_email_verification and not user.email_verified:
            _send_verification_email(user)
            raise HTTPException(
                status_code=403,
                detail="Please verify your email address. We've sent you a new verification link.",
            )
        acct = account_store.account_for_user(user.id)
        raw_session, csrf_token = account_store.create_session(user.id, ttl_seconds=_session_ttl_seconds())
        _set_auth_cookies(response, raw_session, csrf_token)
        return {
            "user": _user_payload(user),
            "tenant": acct.tenant if acct else None,
            "plan": acct.plan if acct else None,
        }

    @app.post("/auth/logout")
    def auth_logout(request: Request, response: Response) -> dict:
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        session_token = request.cookies.get(_SESSION_COOKIE)
        if not session_token:
            raise HTTPException(status_code=401, detail="Not logged in.")
        _require_csrf(request)
        account_store.delete_session(session_token)
        _clear_auth_cookies(response)
        return {"ok": True}

    @app.get("/auth/verify", response_class=HTMLResponse)
    def auth_verify(request: Request, token: str = Query(...)) -> HTMLResponse:
        """One-click email verification (a human opens this from their
        inbox), so it returns a small HTML page, not JSON. Single-use token;
        rate-limited like the rest of /auth/*."""
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        if not auth_rate_limiter.allow(_client_ip(request)):
            metrics.record_rate_limited()
            raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
        user_id = account_store.consume_email_token(token, "verify")
        if not user_id:
            return HTMLResponse(_auth_result_page(
                "Verification link invalid or expired",
                "This link has already been used or has expired. Log in and request a new one.",
            ), status_code=400)
        account_store.mark_email_verified(user_id)
        return HTMLResponse(_auth_result_page(
            "Email verified",
            "Thanks — your email address is confirmed. You can close this tab and sign in.",
        ))

    @app.post("/auth/reset-request")
    async def auth_reset_request(request: Request) -> dict:
        """Start a password reset. **Always returns 200** whether or not the
        email is registered, so it can't be used to enumerate accounts — the
        email is only actually sent if a matching user exists."""
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        if not auth_rate_limiter.allow(_client_ip(request)):
            metrics.record_rate_limited()
            raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
        body = await request.json()
        email = (body.get("email") or "").strip()
        user = account_store.get_user_by_email(email)
        if user:
            token = account_store.create_email_token(user.id, "reset", RESET_TOKEN_TTL_SECONDS)
            link = _auth_link("/auth/reset", token)
            email_backend.send(password_reset_email(user.email, link))
        return {"ok": True, "message": "If that email is registered, a reset link is on its way."}

    @app.get("/auth/reset", response_class=HTMLResponse)
    def auth_reset_form(token: str = Query(...)) -> HTMLResponse:
        """Minimal landing page for the emailed reset link — a form that
        POSTs the new password back to /auth/reset. Intentionally tiny: the
        real, styled account UI is Day 25; this is the least that makes the
        emailed link actually usable end-to-end today."""
        return HTMLResponse(_reset_form_page(token))

    @app.post("/auth/reset")
    async def auth_reset(request: Request) -> dict:
        """Complete a reset: consume the token, set the new password, and
        invalidate every existing session for that user (done inside
        AccountStore.set_password)."""
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        if not auth_rate_limiter.allow(_client_ip(request)):
            metrics.record_rate_limited()
            raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
        # Accept either JSON (API callers) or a form post (the /auth/reset
        # landing page above).
        token, password = await _read_token_and_password(request)
        user_id = account_store.consume_email_token(token, "reset")
        if not user_id:
            raise HTTPException(status_code=400, detail="Reset link invalid or expired. Request a new one.")
        try:
            account_store.set_password(user_id, password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "message": "Password updated. You can now sign in with your new password."}

    @app.get("/auth/oidc/login")
    def auth_oidc_login(request: Request) -> RedirectResponse:
        """Start the "Sign in with Microsoft" flow (Day 23, §7.3): mint a
        state+nonce+PKCE pair, stash them server-side, and 302 the browser to
        Entra's authorize endpoint. 404 when OIDC isn't configured, so an
        install that never sets PBICOMPASS_OIDC_* exposes no such surface."""
        if not account_store or not oidc_config:
            raise HTTPException(status_code=404, detail="Microsoft sign-in is not enabled.")
        if not auth_rate_limiter.allow(_client_ip(request)):
            metrics.record_rate_limited()
            raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
        nonce = secrets.token_urlsafe(24)
        verifier, challenge = oidc_mod.generate_pkce()
        state = account_store.create_oidc_state(nonce, verifier, oidc_mod.STATE_TTL_SECONDS)
        url = oidc_mod.build_authorize_url(oidc_config, state, nonce, challenge)
        return RedirectResponse(url, status_code=302)

    @app.get("/auth/oidc/callback")
    def auth_oidc_callback(request: Request, code: str | None = Query(None),
                           state: str | None = Query(None),
                           error: str | None = Query(None)) -> Response:
        """Entra redirects here with ?code&state. Validate state (CSRF),
        exchange the code for tokens over TLS, validate the id_token's
        claims, find-or-create the user, open a session, and 302 home."""
        if not account_store or not oidc_config:
            raise HTTPException(status_code=404, detail="Microsoft sign-in is not enabled.")
        if not auth_rate_limiter.allow(_client_ip(request)):
            metrics.record_rate_limited()
            raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
        if error:
            # The user cancelled, or Entra returned an error — show a calm
            # page rather than a stack trace. (content-free: Entra's error
            # slug only, never anything about our own state.)
            return HTMLResponse(_auth_result_page(
                "Sign-in didn't complete",
                "Microsoft sign-in was cancelled or failed. You can try again.",
            ), status_code=400)
        if not code or not state:
            raise HTTPException(status_code=400, detail="Missing authorization code or state.")
        stashed = account_store.consume_oidc_state(state)
        if not stashed:
            raise HTTPException(status_code=400, detail="Sign-in session expired or invalid. Please try again.")
        nonce, verifier = stashed
        try:
            tokens = oidc_mod.exchange_code(oidc_config, code, verifier)
            claims = oidc_mod.decode_id_token_claims(tokens["id_token"])
            oidc_mod.validate_claims(claims, oidc_config, nonce)
        except oidc_mod.OIDCError as exc:
            log.warning("oidc callback rejected: %s", type(exc).__name__)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        email = oidc_mod.email_from_claims(claims)
        if not email:
            raise HTTPException(status_code=400, detail="Microsoft didn't return an email address for this account.")
        user, _acct = account_store.get_or_create_sso_user(email, name=oidc_mod.name_from_claims(claims))
        raw_session, csrf_token = account_store.create_session(user.id, ttl_seconds=_session_ttl_seconds())
        response = RedirectResponse("/", status_code=302)
        _set_auth_cookies(response, raw_session, csrf_token)
        return response

    # -- Account dashboard API (Day 24, §7.6) — session-authenticated -------
    @app.get("/app/api/config")
    def app_config() -> dict:
        """Public (unauthenticated) — lets the dashboard's sign-in view know
        whether to show the "Sign in with Microsoft" button before anyone is
        logged in."""
        return {"oidc_enabled": bool(oidc_config), "accounts_enabled": bool(account_store)}

    @app.get("/app/api/me")
    def app_me(request: Request) -> dict:
        user, acct = _require_user(request)
        used = account_store.usage_today(acct.tenant)
        limit = account_store.limit_for(acct.plan)
        return {
            "email": user.email,
            "email_verified": user.email_verified,
            "tenant": acct.tenant,
            "plan": acct.plan,
            "used_today": used,
            "daily_limit": limit,
            "remaining": max(0, limit - used),
            "oidc_enabled": bool(oidc_config),
        }

    @app.get("/app/api/keys")
    def app_list_keys(request: Request) -> dict:
        _user, acct = _require_user(request)
        return {"keys": [
            {"id": k.id, "name": k.name, "created_at": k.created_at, "is_primary": k.is_primary}
            for k in account_store.list_api_keys(acct.id)
        ]}

    @app.post("/app/api/keys")
    async def app_create_key(request: Request) -> dict:
        _user, acct = _require_user(request)
        _require_csrf(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = (body.get("name") or "").strip() if isinstance(body, dict) else ""
        try:
            info, raw_key = account_store.create_api_key(acct.id, name=name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # The raw key is shown exactly once — the dashboard must surface it now.
        return {"id": info.id, "name": info.name, "created_at": info.created_at, "api_key": raw_key}

    @app.delete("/app/api/keys/{key_id}")
    def app_revoke_key(key_id: str, request: Request) -> dict:
        _user, acct = _require_user(request)
        _require_csrf(request)
        if not account_store.revoke_api_key(acct.id, key_id):
            raise HTTPException(status_code=404, detail="API key not found.")
        return {"ok": True}

    @app.get("/app/api/jobs")
    def app_jobs(request: Request) -> dict:
        _user, acct = _require_user(request)
        # Status/timestamps only — the Job record never holds report content
        # (zero-retention preserved). Reuses the store's own public() shape.
        jobs = app.state.store.list_for_tenant(acct.tenant, limit=50)
        return {"jobs": [app.state.store.public(j) for j in jobs]}

    @app.post("/jobs")
    async def create_job(
        request: Request,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        rules_file: UploadFile | None = File(None),
        enrichment_file: UploadFile | None = File(None),
        provider: str = Form("none"),
        model: str = Form("claude-opus-4-8"),
        effort: str = Form("high"),
        provider_api_key: str | None = Form(None),
        document_types: str | None = Form(None),
        owner: str | None = Form(None),
        audience: str | None = Form(None),
        refresh: str | None = Form(None),
        version: str | None = Form(None),
        status: str | None = Form(None),
        author: str | None = Form(None),
        reviewer: str | None = Form(None),
        classification: str | None = Form(None),
        business_decision: str | None = Form(None),
        requirements: str | None = Form(None),
        security_notes: str | None = Form(None),
        refresh_notes: str | None = Form(None),
        deployment_notes: str | None = Form(None),
        access_notes: str | None = Form(None),
        glossary: str | None = Form(None),
        assumptions: str | None = Form(None),
        support_notes: str | None = Form(None),
    ) -> dict:
        # Per-IP rate limit (Day 20, §9) — independent of and ahead of auth/
        # quota: protects the endpoint from a single address hammering it
        # even when unauthenticated (the "public" tenant has no daily quota
        # to fall back on otherwise).
        if not upload_rate_limiter.allow(_client_ip(request)):
            metrics.record_rate_limited()
            raise HTTPException(
                status_code=429,
                detail="Too many upload requests from this address. Try again shortly.",
            )

        tenant, plan, via_session = resolve_tenant(request)
        if via_session:
            # A signed-in browser session (Day 25) is an ambient credential
            # (unlike a Bearer/API-key header) — require the double-submit
            # CSRF check on this state-changing upload, same as the
            # dashboard's key-management routes.
            _require_csrf(request)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{suffix or '?'}'. Upload a .pbix or a .zip of a .pbip project.",
            )

        # Freemium quota — enforced for authenticated tenants only.
        if account_store and tenant != "public":
            allowed, _used, limit = account_store.try_consume(tenant, plan)
            if not allowed:
                metrics.record_quota_rejected()
                raise HTTPException(
                    status_code=429,
                    detail=f"Daily quota reached ({limit}/{limit} on the '{plan}' plan). Try again tomorrow or upgrade.",
                )

        job = app.state.store.create(file.filename or f"upload{suffix}", tenant=tenant)
        sandbox = JobSandbox(job.id, root=sandbox_root)
        upload_path = sandbox.path(f"upload{suffix}")
        cap = _max_upload_bytes()
        size = 0
        try:
            with open(upload_path, "wb") as out:
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(status_code=413, detail="Upload exceeds the size limit.")
                    out.write(chunk)
        except HTTPException:
            sandbox.cleanup()
            app.state.store.mark_failed(job.id, "Upload exceeded the size limit.")
            raise

        # Optional per-job rule-suppression/severity/threshold config (4.3 /
        # J.A.3). Saved into this job's own sandbox — shredded with
        # everything else in JobSandbox.cleanup(), never persisted.
        rules_file_path: str | None = None
        if rules_file is not None and rules_file.filename:
            rules_path = sandbox.path("rules.toml")
            with open(rules_path, "wb") as out:
                out.write(await rules_file.read())
            rules_file_path = str(rules_path)

        # Optional enrichment file (5.1) — same sandbox-scoped, shredded-on-
        # cleanup handling as rules_file above. Unlike the CLI, the service
        # never bootstraps a skeleton here: a job either brings its own
        # enrichment file or doesn't, since there's no persistent path to
        # write one back to between jobs.
        enrichment_file_path: str | None = None
        if enrichment_file is not None and enrichment_file.filename:
            enrichment_path = sandbox.path("enrichment.yaml")
            with open(enrichment_path, "wb") as out:
                out.write(await enrichment_file.read())
            enrichment_file_path = str(enrichment_path)

        options = {
            "rules_file_path": rules_file_path,
            "enrichment_file_path": enrichment_file_path,
            # §4.0 cost policy: reasoning depth is never clamped by plan —
            # ``plan`` here only gates the daily job quota (above) and the
            # AI fix-snippets paid feature, not effort.
            "plan": plan,
            "provider": provider, "model": model, "effort": effort,
            # BYOK: the caller's own provider key for this job only — never
            # logged, never persisted (the sandbox and job record hold no
            # trace of it once process_job returns).
            "provider_api_key": (provider_api_key or "").strip() or None,
            # Omitted (None) -> "technical" only, for exact API back-compat with
            # callers that predate multi-document support. "all" or a
            # comma-separated list opts in to more document types.
            "document_types": document_types or "technical",
            "owner": owner, "audience": audience, "refresh": refresh,
            "version": version, "status": status, "author": author, "reviewer": reviewer,
            "classification": classification, "business_decision": business_decision,
            "requirements": requirements, "security_notes": security_notes,
            "refresh_notes": refresh_notes, "deployment_notes": deployment_notes,
            "access_notes": access_notes, "glossary": glossary,
            "assumptions": assumptions, "support_notes": support_notes,
        }
        queue_mode = _queue_mode()
        if queue_mode == "celery":
            # A separate Celery worker process reconstructs its own
            # JobStore/JobSandbox handles from plain paths (Python objects
            # can't cross the broker) — so this only works when the job
            # store is file-backed and shared with that worker (§ Day 18 in
            # DEPLOYMENT.md). An in-memory store here would silently strand
            # every job at "queued" forever (the worker's own fresh
            # in-memory DB would never be seen by this process's pollers).
            if app.state.store.db_path == ":memory:":
                sandbox.cleanup()
                app.state.store.mark_failed(
                    job.id, "Server misconfiguration: PBICOMPASS_QUEUE=celery needs a "
                    "file-backed PBICOMPASS_JOBS_DB, not the in-memory default.",
                )
                raise HTTPException(
                    status_code=500,
                    detail="PBICOMPASS_QUEUE=celery requires a file-backed PBICOMPASS_JOBS_DB.",
                )
            from .celery_app import process_job_task
            process_job_task.delay(job.id, str(upload_path), str(sandbox.dir),
                                   app.state.store.db_path, options)
        else:
            background_tasks.add_task(process_job, app.state.store, job.id, upload_path, sandbox, options)
        return {"job_id": job.id, "status_url": f"/jobs/{job.id}"}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str, request: Request) -> JSONResponse:
        tenant, _plan, _via_session = resolve_tenant(request)
        job = app.state.store.get(job_id)
        if job is None or job.tenant != tenant:
            raise HTTPException(status_code=404, detail="Job not found or expired.")
        return JSONResponse(app.state.store.public(job))

    @app.get("/jobs/{job_id}/download")
    def download(job_id: str, request: Request, format: str = Query(...)) -> Response:
        tenant, _plan, _via_session = resolve_tenant(request)
        job = app.state.store.get(job_id)
        if job is None or job.tenant != tenant:
            raise HTTPException(status_code=404, detail="Job not found or expired.")
        if job.status is JobStatus.FAILED:
            raise HTTPException(status_code=409, detail=job.error or "Job failed.")
        if job.status is not JobStatus.DONE:
            raise HTTPException(status_code=409, detail="Job is not finished yet.")
        # ``format`` is either a flat key ("html") or, when more than one
        # document type was requested, a composite "type.format" key
        # ("audit.html") — the actual file extension is always the suffix.
        file_ext = format.rsplit(".", 1)[-1]
        if file_ext not in _CONTENT_TYPES:
            raise HTTPException(status_code=400, detail=f"Unknown format '{format}'.")
        data = app.state.store.get_output(job_id, format)
        if data is None:
            raise HTTPException(status_code=404, detail="Output not available or expired.")
        filename = f"{_safe_basename(job.filename)}.{format}"
        return Response(
            content=data,
            media_type=_CONTENT_TYPES[file_ext],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


app = create_app()
