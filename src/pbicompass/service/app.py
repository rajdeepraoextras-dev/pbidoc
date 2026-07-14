"""FastAPI application: upload -> async job -> status -> download.

Serves a single-page web UI at ``/`` and a small JSON API. All processing runs
inside a per-job sandbox via :func:`process_job`; the app itself never persists
uploads or extracted metadata.

Multi-tenancy (Phase 5): when auth is enabled, requests must carry an
``Authorization: Bearer <value>`` (or ``X-API-Key``) — either a
``pbicompass_sk_...`` API key (the original programmatic path) or a
Supabase-issued JWT (Day 29; identity/signup/login is handled by Supabase
Auth, not this app — see ``supabase_auth.py``). Jobs are tagged with the
caller's tenant and only that tenant can read/download them; per-plan daily
quotas implement the freemium tier. With auth disabled (the local/self-hosted
default) everything runs as the ``public`` tenant with no limits.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import sys
import threading
import uuid
from pathlib import Path

from fastapi import (BackgroundTasks, FastAPI, File, Form, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import supabase_auth
from ..agents import get_client
from ..agents.assist import ASSIST_FIELDS, build_report_summary, fill_field, format_text
from .accounts import PLAN_LIMITS, PLAN_PRICES, AccountStore
from .admin import AdminGuard, verify_admin_token
from .supabase_auth import SupabaseAuthConfig
from .ingest import ingest_to_model
from .jobs import JobStatus, JobStore
from .logging_config import configure_logging, job_id_var, request_id_var
from .metrics import MetricsRegistry
from .output_store import output_store_from_env
from .ratelimit import RateLimiter
from .sandbox import JobSandbox
from .sentry_config import init_sentry
from .visits import VisitStore, visitor_hash
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


def _assist_rate_limit() -> int:
    return int(os.environ.get("PBICOMPASS_ASSIST_RATE_LIMIT", "20"))


def _assist_rate_window_seconds() -> float:
    return float(os.environ.get("PBICOMPASS_ASSIST_RATE_WINDOW_SECONDS", "60"))


def _assist_client():
    """The intake form's "AI Fill"/"Format" buttons always run on MeshAPI,
    independent of whatever engine the eventual job uses -- a deliberate
    product decision to keep this free-form drafting aid on one fixed, cheap
    engine rather than tying it to the caller's job-engine choice. ``None``
    when the server holds no MeshAPI key (self-host without one configured),
    which the route turns into a 503 rather than a crash."""
    if not os.environ.get("MESHAPI_API_KEY"):
        return None
    try:
        return get_client("mesh")
    except Exception:
        return None


def _byok_ui_enabled() -> bool:
    # Off by default (Day 31): the hosted product runs every job on the
    # server's own provider key -- a visitor is never asked for their own
    # Claude/Gemini/etc. key. A self-host deployment that still wants
    # per-job BYOK (the pre-Day-31 default) opts back in explicitly.
    return os.environ.get("PBICOMPASS_BYOK_UI", "").strip().lower() in ("1", "true", "yes")


# AI engines the generator can offer, in display order. The offline engine
# ("none") is always available and is deliberately not listed here. Each entry
# names the env var(s) that supply a server-side key, so a provider with no key
# defaults to "currently unavailable" on a hosted (non-BYOK) deployment.
AI_PROVIDERS = (
    {"id": "anthropic", "label": "Claude (Opus 4.8)", "keys": ("ANTHROPIC_API_KEY",)},
    {"id": "gemini", "label": "Gemini (3.5 Flash)", "keys": ("GEMINI_API_KEY", "GOOGLE_API_KEY")},
    {"id": "cohere", "label": "Cohere (Command A)", "keys": ("COHERE_API_KEY", "CO_API_KEY")},
    {"id": "meshapi", "label": "MeshAPI (1000+ models)", "keys": ("MESHAPI_API_KEY",)},
)
AI_PROVIDER_IDS = frozenset(p["id"] for p in AI_PROVIDERS)


def _provider_has_key(provider: dict) -> bool:
    """True if the server itself holds a usable key for this provider."""
    return any(os.environ.get(k) for k in provider["keys"])


def _provider_default_enabled(provider: dict) -> bool:
    """Availability before any admin override: usable if the server has its
    key, or if BYOK is on (the user supplies the key per job)."""
    return _byok_ui_enabled() or _provider_has_key(provider)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _forwarded_client_ip(request: Request) -> str:
    """Like :func:`_client_ip`, but honors ``X-Forwarded-For`` -- visit
    tracking needs the real caller's IP for accurate unique-visitor counts,
    which ``request.client.host`` alone would report as the reverse proxy's
    IP on a deployment fronted by nginx/Caddy (e.g. the duckdns host)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return _client_ip(request)


# Page paths counted as a "visit" -- deliberately excludes API/asset/health
# routes so the counter reflects real page loads, not polling or asset
# fetches. Kept as a plain prefix set rather than sniffing content-type so it
# stays correct even if a route later changes its response shape.
_TRACKED_PAGE_PATHS = frozenset({
    "/", "/app", "/admin", "/pricing", "/privacy", "/terms", "/refund",
})


def _visits_salt() -> str:
    # Reuses the admin token (or a dedicated override) purely as hashing
    # material -- it never needs to be verified here, just unpredictable so a
    # stored visitor_hash can't be reversed back to an IP.
    return os.environ.get("PBICOMPASS_VISITS_SALT") or os.environ.get("PBICOMPASS_ADMIN_TOKEN") or "pbicompass"


def _safe_basename(filename: str) -> str:
    stem = Path(filename or "documentation").stem
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return cleaned or "documentation"


def _api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def _onboarding_fields(claims: "supabase_auth.SupabaseClaims") -> dict:
    """Extract the Day 33 onboarding fields (name/company/role/plan) a
    signup form stashed in Supabase's own ``user_metadata`` at
    ``supabase.auth.signUp()`` time -- they ride along in the verified JWT
    with no extra round trip and no dependency on email-confirmation
    timing. Only consulted by AccountStore on account *creation*; a
    returning user's metadata is never re-applied over their own later
    self-serve changes (e.g. a plan change from the Profile page)."""
    metadata = claims.raw.get("user_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "name": str(metadata.get("name") or "").strip(),
        "company": str(metadata.get("company") or "").strip(),
        "role": str(metadata.get("role") or "").strip(),
        "plan": str(metadata.get("plan") or "").strip(),
    }


def create_app(
    store: JobStore | None = None,
    *,
    sandbox_root: str | None = None,
    account_store: AccountStore | None = None,
    require_auth: bool | None = None,
    admin_token: str | None = None,
    admin_guard: AdminGuard | None = None,
    supabase_config: SupabaseAuthConfig | None = None,
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
    # Bootstrap admin (Day 34): the email auto-granted admin on its first
    # signed-in request, so the very first admin exists without a manual DB
    # touch — after that, admins are granted by an existing admin. Empty ⇒ no
    # bootstrap. This is what makes the in-app Admin panel reachable for the
    # operator without ever typing the break-glass ops token in the browser.
    bootstrap_admin_email = (os.environ.get("PBICOMPASS_BOOTSTRAP_ADMIN_EMAIL") or "").strip().lower()
    # Day 29: identity (signup/login/email-verify/password-reset/"Sign in
    # with Microsoft") is handled by Supabase Auth, not this app -- None
    # unless SUPABASE_URL is configured, in which case Authorization: Bearer
    # accepts a Supabase-issued JWT alongside the original pbicompass_sk_...
    # API key (see resolve_tenant). An install that never sets it stays on
    # the API-key-only path with zero new dependencies pulled in.
    if supabase_config is None:
        supabase_config = SupabaseAuthConfig.from_env()
    app.state.supabase_config = supabase_config

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
    assist_rate_limiter = RateLimiter(_assist_rate_limit(), _assist_rate_window_seconds())

    # Visitor/page-view counter (Day 38) -- always on, independent of the
    # account/admin-token features above, so even a plain self-host without
    # auth configured gets a visit count. Shares whichever DB the accounts
    # store already uses (its own table, own connection) rather than adding
    # a whole second env var for a single small table; falls back to
    # in-memory when neither is set, matching every test's implicit
    # no-disk-writes expectation for a bare create_app() call.
    visits_db_path = os.environ.get("PBICOMPASS_VISITS_DB") or os.environ.get("PBICOMPASS_DB") or ":memory:"
    visit_store = VisitStore(visits_db_path)
    app.state.visits = visit_store

    @app.on_event("shutdown")
    def _close_visit_store() -> None:
        visit_store.close()

    # Persistent by default (A2-1): a file path (ideally on a mounted volume,
    # per DEPLOYMENT.md) so an in-flight/finished job survives a worker
    # restart instead of 404ing on the next poll. Tests that construct their
    # own ``JobStore()`` keep today's in-memory behavior unchanged.
    owns_job_store = store is None
    if owns_job_store:
        store = JobStore(
            os.environ.get("PBICOMPASS_JOBS_DB", "pbicompass_jobs.db"),
            processing_timeout_seconds=_job_timeout_seconds(),
            output_store=output_store_from_env(),
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
    # Pricing + legal pages required to link Paddle billing (Day 35/36):
    # read-once-into-memory, same pattern as the other static pages above.
    pricing_html = (_STATIC / "pricing.html").read_text(encoding="utf-8")
    privacy_html = (_STATIC / "privacy.html").read_text(encoding="utf-8")
    terms_html = (_STATIC / "terms.html").read_text(encoding="utf-8")
    refund_html = (_STATIC / "refund.html").read_text(encoding="utf-8")
    # Shared design system (Day 33) — one stylesheet for / and /app so they
    # render as one product instead of two visually unrelated pages. Same
    # read-once-into-memory pattern as the HTML pages above.
    theme_css = (_STATIC / "theme.css").read_text(encoding="utf-8")
    # Vendored (not CDN-loaded) supabase-js (Day 30, §2) -- a CDN outage
    # shouldn't be able to block sign-in. Read once at startup, same
    # in-memory-string pattern as the HTML pages above.
    vendor_supabase_js = (_STATIC / "vendor" / "supabase.js").read_text(encoding="utf-8")

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

    @app.middleware("http")
    async def _track_visit(request: Request, call_next):
        response = await call_next(request)
        if request.method == "GET" and response.status_code < 400 \
                and request.url.path in _TRACKED_PAGE_PATHS:
            try:
                ip = _forwarded_client_ip(request)
                ua = request.headers.get("user-agent", "")
                visit_store.record(request.url.path, visitor_hash(_visits_salt(), ip, ua))
            except Exception:
                log.warning("visit tracking failed", exc_info=True)
        return response

    def _reject_if_blocked(acct) -> None:
        """A suspended account (admin-blocked, Day 35) is refused service —
        403, so it reads as 'not allowed' rather than a transient error. Its
        record and sign-in still work (so it can see it's suspended); it just
        can't consume the service."""
        if getattr(acct, "blocked", False):
            raise HTTPException(status_code=403,
                                detail="This account has been suspended. Contact support.")

    def resolve_tenant(request: Request) -> tuple[str, str]:
        """Return ``(tenant, plan)``. Raises 401 when auth is required and
        absent. ``Authorization: Bearer <value>`` (or ``X-API-Key``) is
        either a ``pbicompass_sk_...`` API key (the original programmatic
        path, byte-for-byte unchanged) or a Supabase-issued JWT (Day 29,
        identity now lives in Supabase, not this app) — disambiguated by
        shape via :func:`supabase_auth.looks_like_jwt` before verification.
        A *supplied* credential that fails to verify fails as itself; it
        never falls through to try the other auth method or a different
        identity. Bearer auth (of either kind) is never an ambient browser
        credential, so unlike the retired session-cookie model, no CSRF
        check is needed on top of it."""
        key = _api_key(request)
        if account_store and key:
            if supabase_config and supabase_auth.looks_like_jwt(key):
                try:
                    claims = supabase_auth.verify_jwt(key, supabase_config)
                except supabase_auth.SupabaseAuthError:
                    claims = None
                if claims is not None:
                    acct = account_store.get_or_create_account_for_supabase_user(
                        claims.sub, claims.email or "", **_onboarding_fields(claims)
                    )
                    _reject_if_blocked(acct)
                    return acct.tenant, acct.plan
            else:
                acct = account_store.verify(key)
                if acct:
                    _reject_if_blocked(acct)
                    return acct.tenant, acct.plan
            # Falls through to the require_auth/public floor below, same as
            # an unrecognized API key always has — see the module docstring.
        if require_auth:
            raise HTTPException(
                status_code=401,
                detail="A valid API key or a signed-in session is required. Send "
                       "'Authorization: Bearer <key>'.",
            )
        return "public", "free"

    def _require_user(request: Request):
        """Resolve the signed-in caller from a Supabase JWT (Day 29), or
        401. This is the account-dashboard's auth — returning
        ``(claims, account)``. Distinct from ``resolve_tenant`` only in
        that it also 401s when Supabase/accounts aren't configured at all,
        rather than degrading to the public tenant."""
        if not account_store:
            raise HTTPException(status_code=503, detail="Accounts are not configured.")
        if not supabase_config:
            raise HTTPException(status_code=503, detail="Sign-in is not configured.")
        token = _api_key(request)
        if not token:
            raise HTTPException(status_code=401, detail="Not signed in.")
        try:
            claims = supabase_auth.verify_jwt(token, supabase_config)
        except supabase_auth.SupabaseAuthError as exc:
            raise HTTPException(status_code=401, detail="Not signed in.") from exc
        acct = account_store.get_or_create_account_for_supabase_user(
            claims.sub, claims.email or "", **_onboarding_fields(claims)
        )
        # Self-provision the bootstrap admin on first sign-in (idempotent).
        if bootstrap_admin_email and (claims.email or "").strip().lower() == bootstrap_admin_email \
                and not account_store.is_admin(claims.sub):
            account_store.grant_admin(claims.sub)
        return claims, acct

    def _require_admin_user(request: Request):
        """Gate an in-app admin endpoint by the SIGNED-IN user's admin status
        (not the break-glass ops token). 403 for a signed-in non-admin. This
        is what the Admin view inside /app uses, so an admin manages the
        product from the same UI, never typing PBICOMPASS_ADMIN_TOKEN."""
        claims, acct = _require_user(request)
        if not account_store.is_admin(claims.sub):
            raise HTTPException(status_code=403, detail="Admin access required.")
        return claims, acct

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

    @app.get("/vendor/supabase.js")
    def vendor_supabase_js_route() -> Response:
        return Response(content=vendor_supabase_js, media_type="application/javascript")

    @app.get("/theme.css")
    def theme_css_route() -> Response:
        return Response(content=theme_css, media_type="text/css")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return index_html

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page() -> str:
        return admin_html

    @app.get("/pricing", response_class=HTMLResponse)
    def pricing_page() -> str:
        return pricing_html

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy_page() -> str:
        return privacy_html

    @app.get("/terms", response_class=HTMLResponse)
    def terms_page() -> str:
        return terms_html

    @app.get("/refund", response_class=HTMLResponse)
    def refund_page() -> str:
        return refund_html

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
            used = account_store.usage_this_month(acct.tenant)
            accounts.append({
                "id": acct.id, "tenant": acct.tenant, "name": acct.name,
                "plan": acct.plan, "created_at": acct.created_at,
                "used_this_month": used, "monthly_limit": limit,
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
    @app.get("/app/api/health")
    def healthz() -> JSONResponse:
        # Bounded probes (lock acquired with a timeout, one trivial query):
        # when the shared store lock is wedged behind a stuck DB call this
        # returns 503 within seconds instead of hanging with the rest of the
        # app — which is what lets a container HEALTHCHECK/watchdog restart
        # a wedged process automatically (2026-07-13 production hang).
        checks: dict[str, bool] = {}
        checks["jobs_db"] = app.state.store.healthcheck()
        if not checks["jobs_db"]:
            log.warning("healthz: jobs_db check failed")
        if account_store is not None:
            checks["accounts_db"] = account_store.healthcheck()
            if not checks["accounts_db"]:
                log.warning("healthz: accounts_db check failed")
        checks["queue"] = _check_queue()
        ok = all(checks.values())
        return JSONResponse({"ok": ok, "checks": checks}, status_code=200 if ok else 503)

    @app.get("/debug/threads")
    def debug_threads(request: Request) -> PlainTextResponse:
        """Dump every thread's current stack (admin-token gated). The
        first-responder tool for a wedged process: shows exactly which call
        every thread is blocked in. Deliberately touches no store/DB so it
        still answers while the store lock is wedged."""
        _require_admin(request)
        import traceback
        frames = sys._current_frames()
        lines: list[str] = []
        for t in threading.enumerate():
            lines.append(f"--- {t.name} (ident={t.ident}, daemon={t.daemon}) ---\n")
            frame = frames.get(t.ident)
            if frame is not None:
                lines.extend(traceback.format_stack(frame))
            lines.append("\n")
        return PlainTextResponse("".join(lines))

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
        tenant, plan = resolve_tenant(request)
        out = {"tenant": tenant, "plan": plan, "auth_required": require_auth}
        if account_store and tenant != "public":
            used, limit = account_store.usage_this_month(tenant), account_store.limit_for(plan)
            out.update(used_this_month=used, monthly_limit=limit, remaining=max(0, limit - used))
        return out

    # -- Account dashboard API (Day 24, §7.6; Day 29 -- Supabase-JWT-authenticated) -------
    def _provider_overrides() -> dict:
        return account_store.get_provider_overrides() if account_store else {}

    def _provider_enabled(provider_id: str) -> bool:
        """Effective availability of an AI engine: an admin override wins, else
        the key-based default. Unknown ids (incl. the always-on offline engine)
        are treated as available."""
        provider = next((p for p in AI_PROVIDERS if p["id"] == provider_id), None)
        if provider is None:
            return True
        return _provider_overrides().get(provider_id, _provider_default_enabled(provider))

    def _provider_catalog() -> list[dict]:
        """AI engines with their current enabled/disabled state for the
        generator's engine picker. Offline is added by the frontend."""
        overrides = _provider_overrides()
        return [
            {"id": p["id"], "label": p["label"],
             "enabled": overrides.get(p["id"], _provider_default_enabled(p))}
            for p in AI_PROVIDERS
        ]

    @app.get("/app/api/config")
    def app_config() -> dict:
        """Public (unauthenticated) — lets the frontend construct a
        ``supabase-js`` client (Day 30/31) before anyone is signed in.
        ``supabase_anon_key`` is safe to expose (it's the public/browser
        key by design — Supabase's row-level security, not secrecy, is
        what protects data behind it)."""
        return {
            "accounts_enabled": bool(account_store),
            "supabase_enabled": bool(supabase_config),
            "supabase_url": supabase_config.url if supabase_config else None,
            "supabase_anon_key": supabase_config.anon_key if supabase_config else None,
            "byok_enabled": _byok_ui_enabled(),
            # Day 33: lets the signup/profile plan picker render real quota
            # numbers instead of hardcoding them in the frontend.
            "plan_limits": PLAN_LIMITS,
            # Day 36: which AI engines the picker may offer, and whether each is
            # currently available (admin-controlled). Offline is always usable.
            "providers": _provider_catalog(),
        }

    @app.get("/app/api/me")
    def app_me(request: Request) -> dict:
        claims, acct = _require_user(request)
        used = account_store.usage_this_month(acct.tenant)
        limit = account_store.limit_for(acct.plan, acct.quota_override)
        return {
            "email": claims.email,
            "email_verified": claims.email_verified,
            "tenant": acct.tenant,
            "plan": acct.plan,
            "company": acct.company,
            "role": acct.role,
            "used_this_month": used,
            "monthly_limit": limit,
            "remaining": max(0, limit - used),
            "is_admin": account_store.is_admin(claims.sub),
            "blocked": acct.blocked,
        }

    @app.post("/app/api/plan")
    async def app_set_plan(request: Request) -> dict:
        """Self-serve plan change (Day 33) — trust-based, no payment step;
        billing stays out of scope until a later sprint. Restricted to
        ``free`` for now (Day 38): paid plans have no checkout behind them
        yet, so self-serve upgrading to one would hand out real quota for
        nothing. An admin can still move any account onto a paid plan from
        the admin portal (``app_admin_set_plan`` below), unaffected."""
        _claims, acct = _require_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        plan = (body.get("plan") or "").strip() if isinstance(body, dict) else ""
        if plan != "free":
            raise HTTPException(
                status_code=400,
                detail="Paid plans aren't self-serve yet — billing is coming soon.",
            )
        try:
            account_store.set_plan(acct.id, plan)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"plan": plan}

    @app.get("/app/api/keys")
    def app_list_keys(request: Request) -> dict:
        _claims, acct = _require_user(request)
        return {"keys": [
            {"id": k.id, "name": k.name, "created_at": k.created_at, "is_primary": k.is_primary}
            for k in account_store.list_api_keys(acct.id)
        ]}

    @app.post("/app/api/keys")
    async def app_create_key(request: Request) -> dict:
        _claims, acct = _require_user(request)
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
        _claims, acct = _require_user(request)
        if not account_store.revoke_api_key(acct.id, key_id):
            raise HTTPException(status_code=404, detail="API key not found.")
        return {"ok": True}

    @app.get("/app/api/jobs")
    def app_jobs(request: Request) -> dict:
        _claims, acct = _require_user(request)
        # Status/timestamps only — the Job record never holds report content
        # (zero-retention preserved). Reuses the store's own public() shape.
        jobs = app.state.store.list_for_tenant(acct.tenant, limit=50)
        return {"jobs": [app.state.store.public(j) for j in jobs]}

    # -- in-app admin panel (Day 34) ---------------------------------------
    # Rendered inside /app on the shared theme and gated by the SIGNED-IN
    # user's admin status (_require_admin_user), so an admin runs the product
    # from the same UI and never types the break-glass PBICOMPASS_ADMIN_TOKEN
    # in a browser. The token-gated /admin/api/* routes stay as the
    # out-of-band operator fallback.
    def _admin_account_dict(a) -> dict:
        user_id = account_store.primary_user_id(a.id)
        return {
            "id": a.id, "tenant": a.tenant, "name": a.name, "email": a.email,
            "company": a.company, "role": a.role, "plan": a.plan,
            "quota_override": a.quota_override, "created_at": a.created_at,
            "blocked": a.blocked, "monthly_price": PLAN_PRICES.get(a.plan, 0),
            "used_this_month": account_store.usage_this_month(a.tenant),
            "monthly_limit": account_store.limit_for(a.plan, a.quota_override),
            "user_id": user_id,
            "is_admin": bool(user_id) and account_store.is_admin(user_id),
        }

    @app.get("/app/api/admin/accounts")
    def app_admin_accounts(request: Request) -> dict:
        _require_admin_user(request)
        return {"accounts": [_admin_account_dict(a) for a in account_store.list_accounts()]}

    @app.get("/app/api/admin/stats")
    def app_admin_stats(request: Request) -> dict:
        """Portal overview: user/plan counts, activity, and *estimated* MRR
        (plan list price x active, non-blocked accounts — a projection until
        Stripe billing supplies real numbers)."""
        _require_admin_user(request)
        accounts = account_store.list_accounts()
        by_plan = {p: 0 for p in PLAN_LIMITS}
        mrr = 0
        blocked = 0
        for a in accounts:
            by_plan[a.plan] = by_plan.get(a.plan, 0) + 1
            if a.blocked:
                blocked += 1
            else:
                mrr += PLAN_PRICES.get(a.plan, 0)
        return {
            "total_accounts": len(accounts),
            "blocked_accounts": blocked,
            "active_accounts": len(accounts) - blocked,
            "by_plan": by_plan,
            "plan_prices": PLAN_PRICES,
            "estimated_mrr": mrr,
            "docs_this_month": account_store.total_usage_this_month(),
            "docs_all_time": account_store.total_usage_all_time(),
            "visits_today": visit_store.views_today(),
            "unique_visitors_today": visit_store.unique_visitors_today(),
            "visits_all_time": visit_store.views_all_time(),
            "visits_last_14_days": [
                {"day": d.day, "views": d.views, "unique_visitors": d.unique_visitors}
                for d in visit_store.daily_breakdown(14)
            ],
        }

    @app.post("/app/api/admin/accounts/{account_id}/block")
    async def app_admin_block(account_id: str, request: Request) -> dict:
        _require_admin_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        blocked = bool(body.get("blocked", True)) if isinstance(body, dict) else True
        if not account_store.set_blocked(account_id, blocked):
            raise HTTPException(status_code=404, detail="Account not found.")
        return {"account_id": account_id, "blocked": blocked}

    @app.post("/app/api/admin/accounts/{account_id}/admin")
    async def app_admin_toggle_admin(account_id: str, request: Request) -> dict:
        """Grant/revoke admin on the account's owning Supabase user."""
        claims, _acct = _require_admin_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        make_admin = bool(body.get("is_admin", True)) if isinstance(body, dict) else True
        user_id = account_store.primary_user_id(account_id)
        if not user_id:
            raise HTTPException(status_code=404,
                                detail="Account has no Supabase user to grant admin to.")
        if not make_admin and user_id == claims.sub:
            raise HTTPException(status_code=400, detail="You can't revoke your own admin access.")
        if make_admin:
            account_store.grant_admin(user_id)
        else:
            account_store.revoke_admin(user_id)
        return {"account_id": account_id, "is_admin": make_admin}

    @app.delete("/app/api/admin/accounts/{account_id}")
    def app_admin_delete_account(account_id: str, request: Request) -> dict:
        claims, _acct = _require_admin_user(request)
        # Guard against an admin deleting their own account out from under
        # themselves mid-session.
        if account_store.primary_user_id(account_id) == claims.sub:
            raise HTTPException(status_code=400, detail="You can't delete your own account here.")
        if not account_store.revoke_account(account_id):
            raise HTTPException(status_code=404, detail="Account not found.")
        return {"account_id": account_id, "deleted": True}

    @app.post("/app/api/admin/accounts/{account_id}/plan")
    async def app_admin_set_plan(account_id: str, request: Request) -> dict:
        _require_admin_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        plan = (body.get("plan") or "").strip() if isinstance(body, dict) else ""
        try:
            existed = account_store.set_plan(account_id, plan)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not existed:
            raise HTTPException(status_code=404, detail="Account not found.")
        return {"account_id": account_id, "plan": plan}

    @app.post("/app/api/admin/accounts/{account_id}/quota")
    async def app_admin_set_quota(account_id: str, request: Request) -> dict:
        _require_admin_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        raw = body.get("quota_override") if isinstance(body, dict) else None
        limit: int | None = None
        if raw is not None and str(raw).strip() != "":
            try:
                limit = int(raw)
            except (TypeError, ValueError):
                limit = -1
            if limit < 0:
                raise HTTPException(status_code=400,
                                    detail="quota_override must be a non-negative integer, or null to clear.")
        if not account_store.set_quota_override(account_id, limit):
            raise HTTPException(status_code=404, detail="Account not found.")
        return {"account_id": account_id, "quota_override": limit}

    @app.get("/app/api/admin/providers")
    def app_admin_providers(request: Request) -> dict:
        """AI-engine availability controls for the admin portal. ``has_key``
        flags engines the server can't run itself (they'd fall back to offline
        if enabled without BYOK) so the operator toggles with eyes open."""
        _require_admin_user(request)
        overrides = _provider_overrides()
        return {
            "byok_enabled": _byok_ui_enabled(),
            "providers": [
                {"id": p["id"], "label": p["label"],
                 "has_key": _provider_has_key(p),
                 "enabled": overrides.get(p["id"], _provider_default_enabled(p)),
                 "overridden": p["id"] in overrides}
                for p in AI_PROVIDERS
            ],
        }

    @app.post("/app/api/admin/providers/{provider}")
    async def app_admin_set_provider(provider: str, request: Request) -> dict:
        _require_admin_user(request)
        if provider not in AI_PROVIDER_IDS:
            raise HTTPException(status_code=404, detail="Unknown AI engine.")
        try:
            body = await request.json()
        except Exception:
            body = {}
        enabled = bool(body.get("enabled", True)) if isinstance(body, dict) else True
        account_store.set_provider_enabled(provider, enabled)
        return {"provider": provider, "enabled": enabled}

    @app.get("/app/api/admin/jobs")
    def app_admin_jobs(request: Request, tenant: str | None = Query(None),
                       limit: int = Query(100)) -> dict:
        _require_admin_user(request)
        jobs = app.state.store.list_all(limit=max(1, min(limit, 500)), tenant=tenant)
        out = []
        for j in jobs:
            payload = app.state.store.public(j)
            payload["tenant"] = j.tenant  # whose job it is (admin-only field)
            out.append(payload)
        return {"jobs": out}

    @app.get("/app/api/admin/feedback")
    def app_admin_feedback(request: Request, limit: int = Query(100)) -> dict:
        _require_admin_user(request)
        entries = app.state.store.list_feedback(limit=max(1, min(limit, 500)))
        return {"feedback": [app.state.store.public_feedback(fb) for fb in entries]}

    @app.post("/app/api/assist/fill")
    async def assist_fill(
        request: Request,
        file: UploadFile = File(...),
        field: str = Form(...),
        current_text: str | None = Form(None),
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
        """Draft one Notes-tab field from the uploaded report's own structure
        (see ``agents/assist.py``) plus whatever else the user already typed
        on the form. Always MeshAPI (see ``_assist_client``), independent of
        the job engine picked on tab 1 -- this runs before any job exists."""
        if field not in ASSIST_FIELDS:
            raise HTTPException(status_code=400, detail="Unknown field.")
        if not assist_rate_limiter.allow(_client_ip(request)):
            raise HTTPException(
                status_code=429,
                detail="Too many AI-assist requests from this address. Try again shortly.",
            )
        resolve_tenant(request)  # same auth gate as /jobs; tenant itself unused here

        client = _assist_client()
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="AI assist is not configured on this server (MeshAPI key missing).",
            )

        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{suffix or '?'}'. Upload a .pbix or a .zip of a .pbip project.",
            )

        sandbox = JobSandbox(uuid.uuid4().hex, root=sandbox_root)
        try:
            upload_path = sandbox.path(f"upload{suffix}")
            cap = _max_upload_bytes()
            size = 0
            with open(upload_path, "wb") as out:
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(status_code=413, detail="Upload exceeds the size limit.")
                    out.write(chunk)

            try:
                model = ingest_to_model(upload_path, sandbox.dir)
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="Could not read the uploaded file. Ensure it is a valid .pbix or a .zip of a .pbip project.",
                )

            form_context = {
                "owner": owner, "audience": audience, "refresh": refresh, "version": version,
                "status": status, "author": author, "reviewer": reviewer, "classification": classification,
                "business_decision": business_decision, "requirements": requirements,
                "security_notes": security_notes, "refresh_notes": refresh_notes,
                "deployment_notes": deployment_notes, "access_notes": access_notes,
                "glossary": glossary, "assumptions": assumptions, "support_notes": support_notes,
            }
            try:
                text = fill_field(client, field, build_report_summary(model), form_context, current_text)
            except Exception:
                raise HTTPException(status_code=502, detail="AI assist is temporarily unavailable. Try again.")
            if not text:
                raise HTTPException(status_code=502, detail="AI assist returned no text. Try again.")
            return {"text": text}
        finally:
            sandbox.cleanup()

    @app.post("/app/api/assist/format")
    async def assist_format(request: Request) -> dict:
        """Grammar/punctuation cleanup of whatever the user already typed
        into a Notes-tab field -- no report file involved. Always MeshAPI,
        same as ``assist_fill`` above."""
        if not assist_rate_limiter.allow(_client_ip(request)):
            raise HTTPException(
                status_code=429,
                detail="Too many AI-assist requests from this address. Try again shortly.",
            )
        resolve_tenant(request)

        try:
            body = await request.json()
        except Exception:
            body = {}
        text = str(body.get("text") or "").strip() if isinstance(body, dict) else ""
        if not text:
            raise HTTPException(status_code=400, detail="No text to format.")
        if len(text) > 8000:
            raise HTTPException(status_code=400, detail="Text is too long to format.")

        client = _assist_client()
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="AI assist is not configured on this server (MeshAPI key missing).",
            )
        try:
            formatted = format_text(client, text)
        except Exception:
            raise HTTPException(status_code=502, detail="AI assist is temporarily unavailable. Try again.")
        return {"text": formatted or text}

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
        # even when unauthenticated (the "public" tenant has no monthly quota
        # to fall back on otherwise).
        if not upload_rate_limiter.allow(_client_ip(request)):
            metrics.record_rate_limited()
            raise HTTPException(
                status_code=429,
                detail="Too many upload requests from this address. Try again shortly.",
            )

        tenant, plan = resolve_tenant(request)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{suffix or '?'}'. Upload a .pbix or a .zip of a .pbip project.",
            )

        # Day 36: honour the admin's AI-engine availability toggles server-side
        # too, so an API caller can't pick an engine the UI marks unavailable.
        # Only jobs relying on the *server's* key are blocked — a caller that
        # brings its own key (BYOK) runs at its own expense — and the offline
        # engine ("none") is always allowed.
        if (provider in AI_PROVIDER_IDS
                and not (provider_api_key or "").strip()
                and not _provider_enabled(provider)):
            raise HTTPException(
                status_code=400,
                detail="That AI engine is currently unavailable. Choose another engine or the offline engine.",
            )

        # Freemium quota — enforced for authenticated tenants only.
        if account_store and tenant != "public":
            allowed, _used, limit = account_store.try_consume(tenant, plan)
            if not allowed:
                metrics.record_quota_rejected()
                raise HTTPException(
                    status_code=429,
                    detail=f"Monthly quota reached ({limit}/{limit} on the '{plan}' plan). Try again next month or upgrade.",
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
        tenant, _plan = resolve_tenant(request)
        job = app.state.store.get(job_id)
        if job is None or job.tenant != tenant:
            raise HTTPException(status_code=404, detail="Job not found or expired.")
        return JSONResponse(app.state.store.public(job))

    @app.post("/jobs/{job_id}/feedback")
    async def submit_feedback(job_id: str, request: Request) -> dict:
        tenant, _plan = resolve_tenant(request)
        job = app.state.store.get(job_id)
        if job is None or job.tenant != tenant:
            raise HTTPException(status_code=404, detail="Job not found or expired.")
        try:
            body = await request.json()
        except Exception:
            body = {}
        message = (body.get("message") or "").strip() if isinstance(body, dict) else ""
        if not message:
            raise HTTPException(status_code=400, detail="Feedback message is required.")
        if len(message) > 4000:
            message = message[:4000]
        fb = app.state.store.add_feedback(job_id, tenant, message)
        return {"feedback": app.state.store.public_feedback(fb)}

    @app.get("/jobs/{job_id}/download")
    def download(job_id: str, request: Request, format: str = Query(...)) -> Response:
        tenant, _plan = resolve_tenant(request)
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
        # Composite HTML keys ("technical.html", "audit.html", ..., and the
        # hub "index.html") are the exact, fixed names worker.py bakes the
        # doc-switcher/hub links into every sibling document's sidebar with
        # (see html_filenames there) -- unlike every other format, whose
        # download filename is purely cosmetic. Renaming one to the upload-
        # derived name below (as every other format does) would leave the
        # links every sibling file actually points at referring to a
        # filename nothing was ever saved under, breaking navigation the
        # moment a user downloads more than one HTML document individually
        # instead of via the zip bundle (where the fixed names are always
        # used together and everything resolves).
        if file_ext == "html" and format != "html":
            filename = format
        else:
            filename = f"{_safe_basename(job.filename)}.{format}"
        return Response(
            content=data,
            media_type=_CONTENT_TYPES[file_ext],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


app = create_app()
