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

import os
import re
from pathlib import Path

from fastapi import (BackgroundTasks, FastAPI, File, Form, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .accounts import AccountStore
from .admin import AdminGuard, verify_admin_token
from .jobs import JobStatus, JobStore
from .sandbox import JobSandbox
from .worker import process_job

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


def _safe_basename(filename: str) -> str:
    stem = Path(filename or "documentation").stem
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return cleaned or "documentation"


def _api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def create_app(
    store: JobStore | None = None,
    *,
    sandbox_root: str | None = None,
    account_store: AccountStore | None = None,
    require_auth: bool | None = None,
    admin_token: str | None = None,
    admin_guard: AdminGuard | None = None,
) -> FastAPI:
    app = FastAPI(title="PBICompass — Power BI Documentation Generator", version="0.1.0")
    if require_auth is None:
        require_auth = os.environ.get("PBICOMPASS_REQUIRE_AUTH", "").lower() in ("1", "true", "yes")
    if admin_token is None:
        admin_token = os.environ.get("PBICOMPASS_ADMIN_TOKEN") or None
    admin_guard = admin_guard or AdminGuard()

    # The account store backs both end-user auth (when require_auth is on)
    # and the admin panel (which needs it to mint/list/revoke accounts even
    # before auth is enforced, so an operator can set up keys first).
    owns_account_store = account_store is None and (require_auth or bool(admin_token))
    if owns_account_store:
        account_store = AccountStore(os.environ.get("PBICOMPASS_DB", "pbicompass.db"))

    app.state.store = store or JobStore(processing_timeout_seconds=_job_timeout_seconds())
    app.state.accounts = account_store
    app.state.require_auth = require_auth
    index_html = (_STATIC / "index.html").read_text(encoding="utf-8")
    admin_html = (_STATIC / "admin.html").read_text(encoding="utf-8")

    if owns_account_store:
        @app.on_event("shutdown")
        def _close_account_store() -> None:
            account_store.close()

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def resolve_tenant(request: Request) -> tuple[str, str]:
        """Return (tenant, plan). Raises 401 when auth is required and absent."""
        key = _api_key(request)
        if account_store and key:
            acct = account_store.verify(key)
            if acct:
                return acct.tenant, acct.plan
        if require_auth:
            raise HTTPException(
                status_code=401,
                detail="A valid API key is required. Send 'Authorization: Bearer <key>'.",
            )
        return "public", "free"

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

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/me")
    def me(request: Request) -> dict:
        tenant, plan = resolve_tenant(request)
        out = {"tenant": tenant, "plan": plan, "auth_required": require_auth}
        if account_store and tenant != "public":
            used, limit = account_store.usage_today(tenant), account_store.limit_for(plan)
            out.update(used_today=used, daily_limit=limit, remaining=max(0, limit - used))
        return out

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
        tenant, plan = resolve_tenant(request)
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
        background_tasks.add_task(process_job, app.state.store, job.id, upload_path, sandbox, options)
        return {"job_id": job.id, "status_url": f"/jobs/{job.id}"}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str, request: Request) -> JSONResponse:
        tenant, _ = resolve_tenant(request)
        job = app.state.store.get(job_id)
        if job is None or job.tenant != tenant:
            raise HTTPException(status_code=404, detail="Job not found or expired.")
        return JSONResponse(app.state.store.public(job))

    @app.get("/jobs/{job_id}/download")
    def download(job_id: str, request: Request, format: str = Query(...)) -> Response:
        tenant, _ = resolve_tenant(request)
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
