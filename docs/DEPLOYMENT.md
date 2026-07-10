# Deploying PBICompass to production

## TL;DR — what you need (and don't)

**You do NOT need Supabase, Redis, or Postgres.** The app is self-contained: a
single Python process, with accounts in a **SQLite file**. To go live you only
need:

1. **Somewhere to run one container** (a PaaS like Render/Railway/Fly.io, or any
   small VM — 1 vCPU / 512 MB–1 GB RAM is plenty to start).
2. **A persistent volume** mounted at `/data` (so the accounts SQLite DB
   survives restarts).
3. **HTTPS** — every option below gives you this for free.
4. *(optional)* an **`ANTHROPIC_API_KEY`** — only if you want the Claude engine.
   The offline engine needs nothing.

That's it. No managed database, no message broker, no third-party auth provider.

> **One constraint for v1:** job status and rendered outputs live in a local
> SQLite file (`PBICOMPASS_JOBS_DB`), so run **a single instance / single
> worker**. A restart or redeploy no longer loses in-flight/finished jobs as
> long as that file is on a persistent volume — but two concurrent instances
> still can't share one job (instance A's job 404s on instance B). It
> comfortably handles real traffic (jobs are short and I/O-bound).
> **Accounts** (`PBICOMPASS_DB`) can now optionally live in managed Postgres
> instead of SQLite (below) — that half of the multi-instance constraint is
> solved; the jobs half still needs the object-store/Postgres swap for
> `PBICOMPASS_JOBS_DB` itself. True horizontal scale is completed by the
> Celery/Redis async-worker option (also below) — the worker is already
> written to be queue-agnostic.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PBICOMPASS_REQUIRE_AUTH` | _(off)_ | Set `1` to require API keys (hosted SaaS). Off = open `public` tenant. |
| `PBICOMPASS_DB` | `pbicompass.db` | Accounts store. A plain path is SQLite (point at the persistent volume, e.g. `/data/pbicompass.db`); a `postgres://`/`postgresql://` URL uses managed Postgres instead (install the `postgres` extra — see below). |
| `PBICOMPASS_JOBS_DB` | `pbicompass_jobs.db` | SQLite path for job status + rendered outputs (TTL-swept). Point at the same persistent volume, e.g. `/data/pbicompass_jobs.db`. |
| `PBICOMPASS_QUEUE` | `inline` | `inline` runs jobs via FastAPI `BackgroundTasks` (default, no extra infra). `celery` enqueues onto Celery/Redis instead (see below). |
| `PBICOMPASS_BROKER_URL` | — | Redis URL for the Celery broker, e.g. `redis://localhost:6379/0`. Required when `PBICOMPASS_QUEUE=celery`. |
| `PBICOMPASS_RESULT_BACKEND` | same as broker | Celery result backend URL. Optional — this app polls job status via its own DB, not Celery results, so it's only used for Celery's own bookkeeping. |
| `PBICOMPASS_LOG_LEVEL` | `INFO` | Level for the structured JSON stdout logs. |
| `SENTRY_DSN` | _(off)_ | Enables Sentry error tracking (install the `observability` extra too). Unset = no Sentry dependency touched at all. |
| `PBICOMPASS_ENV` | `production` | Free-text environment label attached to Sentry events (e.g. `staging`). |
| `PBICOMPASS_SANDBOX_ROOT` | system temp | Per-job working dir. Point at a tmpfs (RAM) for strict zero-retention. |
| `PBICOMPASS_MAX_UPLOAD_MB` | `100` | Max upload size. |
| `PBICOMPASS_JOB_TIMEOUT_SECONDS` | `600` | Watchdog: force-fail a job stuck in "processing" longer than this (hung LLM call, oversized file). |
| `PBICOMPASS_ADMIN_TOKEN` | _(off)_ | Enables the `/admin` panel (create/list/revoke accounts from the browser) **and** the `/metrics` endpoint (Day 20) — both gated by the same token. Unset = both disabled. |
| `PBICOMPASS_UPLOAD_RATE_LIMIT` | `20` | Max `POST /jobs` requests per IP within the window below (Day 20). Independent of the per-plan daily quota — applies even to the unauthenticated `public` tenant. |
| `PBICOMPASS_UPLOAD_RATE_WINDOW_SECONDS` | `60` | Trailing window (seconds) the upload rate limit is measured over. |
| `PBICOMPASS_SESSION_TTL_SECONDS` | `2592000` (30 days) | How long a self-serve login session (Day 21) stays valid. |
| `PBICOMPASS_COOKIE_SECURE` | `1` | Session/CSRF cookies are `Secure` (HTTPS-only) by default. Set `0` only for a plain-http local dev session, never in production. |
| `PBICOMPASS_AUTH_RATE_LIMIT` / `PBICOMPASS_AUTH_RATE_WINDOW_SECONDS` | `10` / `60` | Per-IP rate limit on `/auth/*` — a separate budget from the upload limiter. |
| `PBICOMPASS_EMAIL_BACKEND` | `console` | `console` logs verify/reset links (no provider needed); `smtp` sends them for real via `PBICOMPASS_SMTP_*` (Day 22). |
| `PBICOMPASS_PUBLIC_URL` | _(empty)_ | Base URL the emailed verify/reset links point at, e.g. `https://docs.example.com`. Unset → bare relative paths. |
| `PBICOMPASS_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_FROM` / `_TLS` | — / `587` / — / — / — / `1` | SMTP credentials from your transactional provider, used only when `PBICOMPASS_EMAIL_BACKEND=smtp`. |
| `PBICOMPASS_REQUIRE_EMAIL_VERIFICATION` | _(off)_ | When `1`, an unverified user can't log in (403, and a fresh verify link is re-sent). Off by default so a fresh self-host isn't locked out before email is configured. |
| `PBICOMPASS_OIDC_CLIENT_ID` / `_CLIENT_SECRET` | — | Entra ID app registration credentials (Day 23). Both required to enable "Sign in with Microsoft"; unset → `/auth/oidc/*` is 404. |
| `PBICOMPASS_OIDC_TENANT` | `common` | Entra tenant: a specific tenant GUID (single-tenant), or `common`/`organizations`/`consumers` (multi-tenant). |
| `PBICOMPASS_OIDC_REDIRECT_URI` | _(derived)_ | The registered redirect URI. Defaults to `PBICOMPASS_PUBLIC_URL` + `/auth/oidc/callback`. |
| `ANTHROPIC_API_KEY` | — | Enables the Claude engine (install the `agents` extra too). |
| `GEMINI_API_KEY` | — | Enables the Gemini engine (install the `agents` extra too; `GOOGLE_API_KEY` also works). |
| `COHERE_API_KEY` | — | Enables the Cohere engine (install the `agents` extra too; `CO_API_KEY` also works). |

---

## Option A — Managed platform (recommended, freemium-friendly)

Works on **Render**, **Railway**, or **Fly.io** (all have free/hobby tiers). The
repo already has a `Dockerfile`.

1. Push this repo to GitHub.
2. Create a new **Web Service from a Dockerfile** on your platform.
3. Add a **persistent disk/volume** mounted at `/data` (1 GB is plenty).
4. Set environment variables:
   - `PBICOMPASS_DB=/data/pbicompass.db`
   - `PBICOMPASS_JOBS_DB=/data/pbicompass_jobs.db`
   - `PBICOMPASS_ADMIN_TOKEN=...` — a long random secret (`python -c "import secrets; print(secrets.token_urlsafe(32))"`). Enables `/admin`.
   - `PBICOMPASS_REQUIRE_AUTH=1` (for a hosted SaaS) — or leave unset to run open.
   - `ANTHROPIC_API_KEY=...` (optional, for Claude).
5. The platform builds the image, runs the `CMD`, assigns a URL, and terminates
   TLS for you. Health check path: `/healthz`.
6. Open `https://<your-url>/admin`, paste the admin token, and create your
   first account (see *Enabling auth* below) — no shell access needed.

Fly.io example: ensure the service listens on `0.0.0.0:8000` (it does) and add a
volume:
```bash
fly launch --no-deploy            # detects the Dockerfile
fly volumes create data --size 1
# in fly.toml: [mounts] source="data" destination="/data"
fly secrets set PBICOMPASS_REQUIRE_AUTH=1 PBICOMPASS_ADMIN_TOKEN=... ANTHROPIC_API_KEY=sk-...
fly deploy
```

**Google Cloud Run:** it's the same Dockerfile, but Cloud Run's defaults are
actively hostile to this app's current state and MUST be overridden:

```bash
gcloud run deploy pbicompass \
  --source . \
  --max-instances=1 \
  --no-cpu-throttling \
  --set-env-vars PBICOMPASS_REQUIRE_AUTH=1,PBICOMPASS_ADMIN_TOKEN=...
```

- `--max-instances=1` — the job store and accounts DB are SQLite-on-local-disk
  (see the single-instance constraint above). If Cloud Run scales to a 2nd
  instance, a job created on instance A 404s when polled on instance B, and
  each instance has its own (different) jobs/accounts DB.
- `--no-cpu-throttling` ("CPU always allocated") — by default Cloud Run
  throttles CPU to ~zero outside of an active request. Documentation jobs run
  in a `BackgroundTasks` coroutine *after* the upload request returns, so a
  throttled instance only makes progress during the brief CPU windows opened
  by the browser's status-polling requests — the same failure class as the
  hang the job-timeout watchdog was added for. Check current Cloud Run
  billing before enabling this: it can reduce free-tier coverage. If you must
  keep throttling for cost reasons, accept poll-driven (slower, but
  watchdog-bounded) progress instead.
- Without a persistent volume, `PBICOMPASS_DB`/`PBICOMPASS_JOBS_DB` and any
  sandbox files on Cloud Run's container filesystem are wiped on every
  redeploy/restart — mount a volume (Cloud Run volume mounts, or point them at
  Cloud SQL/managed Postgres once that backend lands) if accounts and jobs
  need to survive redeploys.

---

## Option B — Your own VM with Docker + Caddy (auto-HTTPS)

Works on any VPS (Hetzner, DigitalOcean, EC2, …) with a domain pointed at it.
The steps below are identical regardless of provider — only VM provisioning
differs.

### Provisioning a free VM — Google Cloud Compute Engine (Always Free)

Google's Always Free tier includes one **e2-micro** instance (2 shared vCPU,
1 GB RAM) + 30 GB standard persistent disk, forever free, with no time limit
— comfortably enough for this app (jobs are short and I/O-bound).

1. Sign up at console.cloud.google.com and create a project.
2. **Compute Engine → VM instances → Create Instance.**
3. **Region:** must be one of the three Always-Free-eligible regions —
   `us-west1`, `us-central1`, or `us-east1`. Any other region is billed.
4. **Machine type:** series **E2**, type **e2-micro**.
5. **Boot disk:** Ubuntu 22.04 LTS, disk type **Standard persistent disk**
   (not SSD/Balanced — those aren't free-tier eligible), size **30 GB**.
6. **Firewall:** check **Allow HTTP traffic** and **Allow HTTPS traffic**.
7. Create, then note the **External IP**. Connect via the **SSH** button in
   the console (browser-based, no key setup needed), or `gcloud compute ssh`.
8. `sudo apt-get update && sudo apt-get install -y docker.io git` (or follow
   Docker's official install docs), then continue with the steps below.
9. **Add ~2 GB of swap** — e2-micro's 1 GB RAM is tight for a Python process
   doing parsing + multi-format rendering in memory; swap is cheap insurance
   against an OOM kill (which `--restart unless-stopped` would silently
   "fix" by restarting the container and wiping the in-memory job store —
   surfacing to users as a job that never finishes):
   ```bash
   sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
   sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

Point your domain's DNS `A` record at the External IP before running Caddy,
so it can issue a Let's Encrypt certificate.

> **Real-world gotcha:** the "Allow HTTP traffic" checkbox creates a firewall
> rule scoped to a network tag, and in practice this didn't always end up
> applied to the instance — symptom was `ERR_CONNECTION_REFUSED` from an
> external browser even though `curl http://localhost/healthz` worked fine
> *on* the VM (i.e. the app was healthy, only external routing was broken).
> If you hit this, skip debugging the auto-created rule and just add your
> own: **VPC network → Firewall → Create Firewall Rule** → Targets: **All
> instances in the network** → Source: `0.0.0.0/0` → Protocol/ports: `tcp:80,443`.

> **e2-micro has only 1 GB RAM.** The `--tmpfs ... size=512m` sandbox below
> only consumes RAM for what's actually uploaded (not reserved upfront), and
> the default `PBICOMPASS_MAX_UPLOAD_MB=100` keeps any single job well under
> that — fine for typical use. If you raise the upload limit substantially,
> lower the tmpfs size, add more swap, or upgrade the machine type.

### Build and run

```bash
# 1. Build
git clone <your-repo> pbicompass && cd pbicompass
docker build -t pbicompass .

# 2. Run (persistent /data volume, tmpfs sandbox for RAM-only zero-retention)
#    PBICOMPASS_SANDBOX_ROOT must match the --tmpfs mount path below, or the
#    app silently falls back to the container's regular (disk-backed) /tmp —
#    it'll still work, just without the RAM-only zero-retention guarantee.
docker run -d --name pbicompass \
  -p 127.0.0.1:8000:8000 \
  -v pbicompass-data:/data \
  --tmpfs /tmp/pbicompass:rw,size=512m \
  -e PBICOMPASS_DB=/data/pbicompass.db \
  -e PBICOMPASS_JOBS_DB=/data/pbicompass_jobs.db \
  -e PBICOMPASS_SANDBOX_ROOT=/tmp/pbicompass \
  -e PBICOMPASS_ADMIN_TOKEN=...  \
  -e PBICOMPASS_REQUIRE_AUTH=1 \
  -e ANTHROPIC_API_KEY=sk-...   `# optional` \
  --restart unless-stopped \
  pbicompass
```

**No domain yet?** DuckDNS gives a free subdomain (`yourname.duckdns.org`)
in about 2 minutes at duckdns.org (sign in with GitHub/Google, add a
subdomain, point its "current ip" at your VM's external IP) — Caddy's
auto-HTTPS works with it identically to a paid domain.

Put **Caddy** in front for automatic HTTPS. Quickest option, no config file
needed (Caddy's "zero-config" mode) — good enough for most deployments:
```bash
docker run -d --name caddy --restart unless-stopped \
  --network host \
  caddy caddy reverse-proxy --from yourdomain.com --to localhost:8000
```

Or with a `Caddyfile` if you want more control (multiple sites, custom headers, etc.):
```
docs.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
}
```
```bash
docker run -d --name caddy --restart unless-stopped \
  --network host \
  -v $PWD/Caddyfile:/etc/caddy/Caddyfile \
  -v caddy-data:/data caddy
```
Either way Caddy fetches and renews a Let's Encrypt certificate automatically.

> **Port conflict gotcha:** Caddy (run with `--network host`) binds host ports
> 80 and 443 directly. If `pbicompass` is *also* bound to host port 80 (e.g.
> from testing over plain HTTP before you had a domain), Caddy crash-loops
> with `bind: address already in use`. Make sure `pbicompass` is bound to
> `127.0.0.1:8000` only (as in the `docker run` above) before starting Caddy
> — Caddy should be the only thing publicly bound to 80/443.

> Prefer systemd over Docker? Install with `pip install ".[service,agents]"` into a venv
> and run `uvicorn pbicompass.service.app:app --host 127.0.0.1 --port 8000` under a
> systemd unit, with Caddy/nginx in front. Same env vars apply.

---

## Enabling auth & creating accounts

With `PBICOMPASS_REQUIRE_AUTH=1`, every request needs `Authorization: Bearer <key>`.

**Admin panel (recommended):** set `PBICOMPASS_ADMIN_TOKEN`, then open
`https://<your-url>/admin` in a browser. Paste the token to unlock, fill in
tenant / name / plan, and **Create account** — the API key is shown once;
copy it and hand it to the user. The same page lists every account with
today's usage and lets you **Revoke** a key instantly (e.g. if one leaks).
Setting `PBICOMPASS_ADMIN_TOKEN` alone (without `PBICOMPASS_REQUIRE_AUTH`)
lets you provision accounts ahead of time — `/jobs` stays open until you also
flip auth on.

**CLI (alternative):** runs inside the container/host, same `PBICOMPASS_DB`:

```bash
# inside the running container (e.g. `docker exec -it pbicompass bash`)
pbicompass account create --tenant acme --name "Acme BI" --plan pro
#   -> prints the API key ONCE — copy it and give it to the customer
pbicompass account list      # shows id, tenant, plan, name
pbicompass account revoke --id <id>
```

Plans and daily quotas: `free` 10, `pro` 200, `enterprise` 100k docs/day. Users
paste their key into the web UI's "Account API Key" field, or send it as a
header to the API. Tenants only ever see their own jobs.

**Securing the admin token:** treat it like a root password — it's a single
shared secret with no per-admin identity. Generate a long random value (32+
bytes), set it only as a platform environment variable (never commit it),
and rotate it (redeploy with a new value) if you suspect it leaked. Repeated
wrong-token attempts from the same client are locked out for 15 minutes after
8 failures, but that's a backstop, not a substitute for a strong token.

---

## Self-serve signup & sessions (Day 21)

Alongside admin-provisioned accounts, users can now create their own account
directly:

```bash
curl -c cookies.txt -X POST https://your-host/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "a@example.com", "password": "at least 8 characters", "name": "Ada"}'
# -> {"user": {...}, "tenant": "u-...", "plan": "free", "api_key": "pbicompass_sk_..."}

curl -b cookies.txt -c cookies.txt -X POST https://your-host/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "a@example.com", "password": "at least 8 characters"}'

curl -b cookies.txt -X POST https://your-host/auth/logout \
  -H "X-CSRF-Token: <value of the pbicompass_csrf cookie>"
```

- Signup creates a **user**, a brand-new **account/tenant they own** (so
  every self-serve user still rides the exact same tenant-isolated, quota-
  enforced path an admin-created account already uses), and an **API key**
  on that account — shown once in the response, same convention as an
  admin-created account. It also logs them in immediately (auto-login UX).
- **Passwords** are hashed with `hashlib.scrypt` (stdlib, no new
  dependency) — not argon2/bcrypt as literally named in the roadmap,
  a deliberate substitution to keep password hashing (not optional, once
  auth is enabled) from requiring a new mandatory dependency, in a project
  that otherwise keeps everything past the parsing core as a lazy-imported
  extra. Same security class as bcrypt/argon2 for this purpose.
- **Sessions** are a signed-for-you-by-the-server, `HttpOnly`, `Secure`,
  `SameSite=Lax` cookie (`pbicompass_session`) — an opaque high-entropy
  token verified by hash, the same reasoning as an API key. Default TTL 30
  days (`PBICOMPASS_SESSION_TTL_SECONDS`).
- **CSRF**: a second, non-`HttpOnly` cookie (`pbicompass_csrf`) is set
  alongside the session. Any state-changing, session-authenticated request
  (today: `POST /auth/logout`) must echo that cookie's value back as an
  `X-CSRF-Token` header — the standard double-submit pattern. Bearer/API-key
  requests never need this (there's no ambient browser credential for CSRF
  to exploit on that path).
- **Rate limiting & lockout**: `/auth/signup`/`/auth/login`/`/auth/logout`
  share a per-IP rate limit (`PBICOMPASS_AUTH_RATE_LIMIT`/
  `PBICOMPASS_AUTH_RATE_WINDOW_SECONDS`, default 10/60s) — a separate budget
  from the upload limiter (Day 20). Repeated failed logins from the same IP
  are locked out for 15 minutes after 8 failures, reusing `admin.py`'s
  brute-force-lockout class (a separate instance from the admin panel's).
- Only available once an accounts store is configured (`PBICOMPASS_REQUIRE_AUTH=1`
  or `PBICOMPASS_ADMIN_TOKEN` set) — same precondition as the admin panel;
  `/auth/*` returns 503 otherwise.

**Scope note (honest, not hidden):** a session cookie is not yet accepted by
`POST /jobs` itself — only the API key signup returns works there today. A
signed-in browser session driving `/jobs` directly (and the accompanying
question of how far the CSRF story extends to that route) is deferred to the
account-dashboard/upload-UI work (Days 24-25), which is also where the
"Sign in with Microsoft" OIDC option (Day 23) lands.

---

## Email verification & password reset (Day 22)

Signup now sends a verification email, and users can reset a forgotten
password — all without a third-party auth provider.

```bash
# Verification: signup emails a link to GET /auth/verify?token=... — one click
# marks the address verified (single-use, 24h token).

# Password reset:
curl -X POST https://your-host/auth/reset-request \
  -H "Content-Type: application/json" -d '{"email": "a@example.com"}'
# -> always 200 (never reveals whether the email is registered); if it is, a
#    reset link to /auth/reset?token=... is emailed (single-use, 1h token).
# The link opens a minimal form that POSTs {token, password} to /auth/reset,
# which sets the new password AND invalidates every existing session.
```

**Email delivery — `PBICOMPASS_EMAIL_BACKEND`:**
- `console` (default) — **logs** the verify/reset link instead of sending it.
  The whole flow works end-to-end on a fresh self-host with no provider at
  all: read the link out of the logs (`jq 'select(.logger=="pbicompass.service.email")'`).
- `smtp` — sends for real through **any** transactional provider's SMTP
  interface (Resend / Postmark / Amazon SES / …) using stdlib `smtplib` — no
  vendor SDK, no new dependency. Configure `PBICOMPASS_SMTP_HOST`/`_PORT`/
  `_USER`/`_PASSWORD`/`_FROM`/`_TLS` and `PBICOMPASS_PUBLIC_URL` (so the
  emailed links are absolute). If `smtp` is selected but host/from aren't set,
  it safely falls back to the console backend rather than crashing.

**Emails are content-free w.r.t. report data by construction** — they only
ever contain an auth link and the recipient's own address, never model
metadata. A transient SMTP failure is logged (type name only) and swallowed,
never failing the signup/reset request itself.

**Gating unverified users — `PBICOMPASS_REQUIRE_EMAIL_VERIFICATION`:** off by
default (so a self-host isn't locked out before email is configured). Set `1`
for a hosted SaaS: an unverified user's login is refused with a 403 and a
fresh verification link is automatically re-sent (so a user who lost the first
email isn't dead-ended). The credentials are validated *before* this check, so
it's not an email-enumeration vector.

**Scope note:** the verify result page and the reset form are intentionally
minimal, unstyled HTML — just enough to make the emailed links usable
end-to-end. The real, branded account UI is Day 25.

---

## "Sign in with Microsoft" — Entra ID SSO (Day 23)

The audience is Power BI users, so signing in with a Microsoft (Entra ID /
Azure AD) account is the lowest-friction path — and the stepping stone to
full enterprise SSO later. Standard OIDC **authorization-code + PKCE** flow,
implemented with **zero new dependencies** (stdlib `urllib` for the token
exchange; the ID token's claims are read directly — see the security note
below).

**Setup (Azure portal):**
1. **Entra ID → App registrations → New registration.**
2. Add a **Web** redirect URI: `https://<your-host>/auth/oidc/callback`.
3. **Certificates & secrets → New client secret** — copy the value.
4. Set the env vars:
   ```bash
   PBICOMPASS_OIDC_CLIENT_ID=<application (client) id>
   PBICOMPASS_OIDC_CLIENT_SECRET=<the secret value>
   PBICOMPASS_OIDC_TENANT=<your tenant GUID, or "common" for multi-tenant>
   PBICOMPASS_PUBLIC_URL=https://<your-host>     # so the redirect URI derives
   ```
5. A **"Sign in with Microsoft"** button just needs to link to
   `GET /auth/oidc/login`, which 302s the user to Microsoft; they come back
   to `/auth/oidc/callback` already signed in (a session cookie is set and
   they're redirected to `/`).

**Account linking.** A Microsoft sign-in is matched to an existing account
**by email** — so a user who signed up with email+password and later clicks
"Sign in with Microsoft" (same address) lands in the *same* account, now with
their email marked verified. A brand-new Microsoft user gets the same
tenant/account/API-key setup as a password signup, with a random unusable
password (they use SSO; they can set a password later via reset if they want
one). This is the same account model enterprise SSO/SCIM will extend — no
migration needed later.

**Security note (honest, not hidden).** This flow reads the ID token's claims
by base64url-decoding its payload; it does **not** verify the token's RS256
signature against Entra's JWKS. That is sound *for this flow specifically*:
OpenID Connect Core §3.1.3.7 permits a confidential client that obtains the ID
token by **direct** server-to-server communication with the token endpoint
(which this does — over TLS, authenticated with the client secret) to rely on
that TLS channel in place of signature validation. We still validate the
audience, expiry, anti-replay `nonce`, and issuer, and the `state` parameter
(stored server-side, single-use) protects the redirect against CSRF. A
deployment that additionally wants JWKS signature verification can add it
behind a crypto extra without changing the flow. There is **no live-Entra
smoke test in this repo** (no Entra tenant in the CI sandbox) — the flow is
proven end-to-end against a crafted-token stand-in that exercises the real
validation path; a one-time real sign-in should be done once against an
actual app registration.

---

## Account dashboard — `/app` (Day 24)

Once accounts are configured, signed-in users manage themselves at **`/app`**
— **no admin token needed**. This is the replacement for the shared-admin-token
flow for end users; the operator `/admin` panel remains for provisioning/support.

The dashboard (a single self-contained page — sign-in form when logged out,
account view when logged in) shows:
- **Plan + usage vs quota** — today's document count against the daily limit.
- **API-key management** — list, **create** (the new key is shown once), and
  **revoke** individual keys. A user can hold several keys (e.g. one per
  machine/CI), each revocable independently; the original key minted at signup
  appears as "Default". This is real revocation — `api_keys` is now the
  authoritative key store that `verify()` consults, so a revoked key stops
  working immediately.
- **Job history** — recent jobs for the account, **status and timestamps
  only** (the job record has never held report content — zero-retention is
  preserved).

Endpoints (all session-authenticated via the login cookie, not the admin
token; state-changing ones require the double-submit `X-CSRF-Token`):
`GET /app`, `GET /app/api/config` (public — tells the sign-in view whether to
show the Microsoft button), `GET /app/api/me`, `GET/POST /app/api/keys`,
`DELETE /app/api/keys/{id}`, `GET /app/api/jobs`.

**Note on the key store change.** As of Day 24, API keys live in a dedicated
`api_keys` table (multiple keys per account) rather than a single column on
the account. Existing deployments need **no migration step** — on first
startup after upgrade, each account's existing key is backfilled into
`api_keys` automatically (idempotent), and the logical backup/restore
(`pbicompass account backup/restore`) now includes keys too (snapshot
`version` bumped to 2; older `version: 1` snapshots still restore, they just
carry no extra keys).

**Scope note:** the `/app` page is intentionally functional-but-plain
(indigo/slate, no framework). The polished, brand-integrated signed-in
product surface — including the upload page itself — is Day 25.

---

## Managed Postgres for accounts (optional, Day 17)

By default `PBICOMPASS_DB` is a SQLite file — fine for a single instance with
a persistent volume. Point it at managed Postgres instead (Supabase/Neon free
tier, RDS, Cloud SQL, ...) when you want accounts/keys/quotas to:
- survive a redeploy **without** a mounted volume, or
- be shared by **more than one running instance** (SQLite-on-local-disk can't
  do this; each instance would otherwise see a different accounts DB).

```bash
pip install "pbicompass[postgres]"     # or bake into the Dockerfile
export PBICOMPASS_DB=postgresql://user:password@host:5432/pbicompass
```

No schema migration step needed — `AccountStore` creates its two tables
(`accounts`, `usage`) with `CREATE TABLE IF NOT EXISTS` on first connect,
identically to the SQLite path. Nothing else about setup changes; the
`/admin` panel and `pbicompass account ...` CLI work the same either way.

**Scope note:** this backend swap covers `PBICOMPASS_DB` (accounts) only.
`PBICOMPASS_JOBS_DB` (job status + rendered outputs) is still SQLite — that
piece of the multi-instance constraint is unchanged until the jobs store gets
its own Postgres/object-store swap. Also, this uses one shared connection
(matching the existing SQLite pattern) rather than a connection pool, which
is enough for today's account/quota write volume but is the next thing to
revisit if write concurrency grows.

## Async worker: Celery + Redis (optional, Day 18)

By default (`PBICOMPASS_QUEUE=inline`, unset) jobs run via FastAPI
`BackgroundTasks` in the same process that accepted the upload — simplest to
run, but subject to Cloud Run's CPU-throttling failure class (see the Cloud
Run note above) and bounded by one process's concurrency.

Switch to Celery + Redis to run jobs on a separate worker process/pool
instead:

```bash
pip install "pbicompass[queue]"        # or bake into the Dockerfile
export PBICOMPASS_QUEUE=celery
export PBICOMPASS_BROKER_URL=redis://localhost:6379/0

# terminal 1 — the API (enqueues jobs, never runs them itself once this is set)
uvicorn pbicompass.service.app:app --host 0.0.0.0 --port 8000

# terminal 2 — one or more workers (this is what actually calls process_job)
celery -A pbicompass.service.celery_app worker --loglevel=info
```

`process_job` itself is unchanged — the same function FastAPI's
`BackgroundTasks` calls directly, a Celery task now calls identically (this
is the "queue-agnostic worker" the code has been documented as since Day 16).
The watchdog (`PBICOMPASS_JOB_TIMEOUT_SECONDS`) still force-fails a stalled
job on the next status poll regardless of which executor is running it.

**Requirement: a shared filesystem between the API and worker processes.**
The API writes the upload into a per-job sandbox directory and the Celery
worker (a separate process, possibly a separate container) must be able to
read that same path — mount the same volume (or the same `PBICOMPASS_SANDBOX_ROOT`)
into both, or run them as separate processes on the same host. This is the
same constraint `PBICOMPASS_JOBS_DB` already has for multi-instance sharing;
neither is solved by adding Celery alone.

**Known gap (honest, not hidden):** no live Redis/Celery smoke test — no
Redis server is available in this sandbox, so wiring is tested with Celery's
`task_always_eager` mode (runs the task synchronously in-process, no broker
needed) rather than a real worker picking up a real queued message. That
final "does a separate `celery worker` process actually pick this up over a
real Redis" check needs a session with Redis available.

## Observability (optional, Day 19)

**Structured logs.** Every log line is one JSON object on stdout —
timestamp, level, logger name, message, plus `request_id`/`job_id` so every
line belonging to one HTTP request or one job can be grepped together (e.g.
`jq 'select(.job_id=="<id>")'`). Deliberately excludes raw exception
messages/tracebacks — only the exception's *type name* is recorded, matching
this app's standing content-free-logging convention (an uncontrolled
exception string could, in principle, echo a fragment of parsed report
data). No setup needed; this is on by default.

**Sentry (opt-in).** Set `SENTRY_DSN` and install the extra to get error
tracking:

```bash
pip install "pbicompass[observability]"
export SENTRY_DSN=https://...@o0.ingest.sentry.io/0
export PBICOMPASS_ENV=production
```

Content-free by construction: `send_default_pii=False`, no local variables
or source-code context are ever attached to an event, and a `before_send`
hook scrubs every exception's own message text down to just its type name
before it leaves the process — the same protection the JSON logs get, so an
error you triage in Sentry never contains a fragment of a customer's model.

**Readiness (`/healthz`).** Now a real readiness check, not an unconditional
`{"ok": true}`: it verifies the job store (and the accounts store, when
configured) can actually be queried, and — only in `PBICOMPASS_QUEUE=celery`
mode — that the broker is reachable. Returns `200 {"ok": true, "checks":
{...}}` when healthy, `503` with the same shape (showing which check failed)
otherwise. The broker probe runs with a hard 1.5s wall-clock deadline in a
background thread rather than trusting the driver's own socket timeout —
worth knowing: in this project's own dev sandbox, a plain `redis-py` connect
attempt with a 0.5s `socket_connect_timeout` was observed to take ~15s
before failing against an unreachable host, so don't assume a fast fail
without testing on your actual platform.

## Metrics & rate limiting (Day 20)

**`GET /metrics`** — jobs/min, failure rate, a token-count cost proxy, and
429 rate, gated by the same `PBICOMPASS_ADMIN_TOKEN` as `/admin` (a
Prometheus scrape config can supply `Authorization`/`X-Admin-Token` just as
easily as a browser):

```bash
curl -H "X-Admin-Token: $PBICOMPASS_ADMIN_TOKEN" https://your-host/metrics
curl -H "X-Admin-Token: $PBICOMPASS_ADMIN_TOKEN" "https://your-host/metrics?format=prometheus"
```

JSON shape: `jobs_created`, `jobs_done`, `jobs_failed`, `jobs_per_minute`
(trailing 60s), `failure_rate`, `quota_rejected_total`, `rate_limited_total`,
`http_429_total`, `avg_input_tokens_per_job`, `avg_output_tokens_per_job`,
`avg_llm_calls_per_job`. Deliberately reports **token counts, not a dollar
figure** — per-token pricing varies by provider/model and changes over time,
so multiply by your own provider's current rate rather than trust a
hard-coded price table baked into the app.

Wire the Prometheus-format endpoint into Grafana/Prometheus/Datadog with a
standard scrape job pointed at `/metrics?format=prometheus`; alert on
`pbicompass_failure_rate` and `pbicompass_http_429_total` climbing, and on
`pbicompass_jobs_per_minute` dropping to zero while jobs are being created
(a stalled worker).

**Scope note:** metrics are per-process/per-instance (an in-memory
registry, same scoping as `JobStore`/`AccountStore` before their Postgres
options) — a multi-instance deployment aggregates by scraping each instance
separately, the standard Prometheus pattern, not by this app summing across
instances itself.

**Per-IP upload rate limiting.** `POST /jobs` is capped at
`PBICOMPASS_UPLOAD_RATE_LIMIT` requests per `PBICOMPASS_UPLOAD_RATE_WINDOW_SECONDS`
per client IP (defaults: 20 per 60s), checked *before* auth/quota — so it
also protects an open (`PBICOMPASS_REQUIRE_AUTH` unset) deployment, which has
no per-plan daily quota to fall back on. A client over the limit gets a 429
(counted separately from quota 429s in `/metrics` as `rate_limited_total`
vs. `quota_rejected_total`).

## Secrets management

Every secret this app uses is an environment variable — there is no secrets
file baked into the image, and none should ever be committed (`.env.example`
ships only empty placeholders; keep your real `.env` out of git, it's
already in `.gitignore`). The actual secrets to move into your platform's
secret store (Fly `fly secrets set`, Render's "Secret" environment group,
Cloud Run's `--set-secrets` backed by Secret Manager, Docker/Compose
`secrets:`, or your CI's encrypted-vars store) rather than a plain
`--set-env-vars`/committed env file:

- `PBICOMPASS_ADMIN_TOKEN` — gates `/admin` and `/metrics`; treat like a root password (see "Securing the admin token" above).
- `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) / `COHERE_API_KEY` — LLM provider keys.
- `PBICOMPASS_DB` — when it's a `postgres://user:password@...` URL, the credentials are embedded in the URL itself.
- `PBICOMPASS_BROKER_URL` / `PBICOMPASS_RESULT_BACKEND` — when Redis requires auth (`redis://:password@host:6379/0`).
- `SENTRY_DSN` — not secret in the sense of granting access, but still worth keeping out of a public repo (it can be used to submit spurious events to your project).

None of these are ever logged: the structured JSON logger (Day 19) never
serializes raw exception text (only `type(exc).__name__`), a caller's BYOK
`provider_api_key` is used in-memory for one job and never persisted or
logged (see `worker.py::_make_client`), and this is guarded by a regression
test (`tests/test_logging_config.py::SecretsNeverLoggedTest`) that drives a
real request with a wrong admin-token guess and a real BYOK key through a
failing job and asserts neither ever appears in the log stream — not just
documented as a design intent.

## Backups & restore drill

**First line of defense: your platform's own automated backups.** Managed
Postgres providers (Neon, Supabase, RDS, Cloud SQL) all offer automated
point-in-time snapshots on their free/hobby tiers — enable that first; it
needs no code here.

**Second line: a portable, stdlib-only logical backup**, for a restore path
that doesn't depend on the `pg_dump`/`pg_restore` client binaries being
installed on whatever platform runs the app (or on SQLite, which has no such
tooling at all):

```bash
# Back up accounts/keys/quotas (works against either PBICOMPASS_DB backend)
pbicompass account backup --out accounts-2026-08-04.json --db "$PBICOMPASS_DB"

# Restore drill: point --db at a SCRATCH database, never production directly
pbicompass account restore --in accounts-2026-08-04.json --db "$SCRATCH_PBICOMPASS_DB"
pbicompass account list --db "$SCRATCH_PBICOMPASS_DB"   # verify the restored rows are actually there
```

The dump is a plain JSON file (account rows + per-day usage counts — never
report data, since none of that ever lives in this store) and restoring is
an upsert, so re-running it is safe. **Run the restore step against a
scratch/staging database as an actual drill on a schedule** (e.g. monthly) —
a backup you've never restored is not a verified backup. `PBICOMPASS_JOBS_DB`
itself isn't in scope for this (it's TTL-swept short-lived job status/output
data, not the durable state worth a restore drill).

**Known gap (honest, not hidden):** the restore-drill mechanism above is
exercised end-to-end in CI against the real SQLite backend
(`tests/test_db_backup.py`) and against the Postgres code path via the same
fake-`psycopg`-module technique `tests/test_accounts_postgres.py` already
established — but not against an actual live Postgres server, the same class
of gap flagged on Days 17/18 for lack of a reachable Postgres/Redis instance
in this sandbox.

## Zero-retention in production

- Set `PBICOMPASS_SANDBOX_ROOT` to a **tmpfs** mount (RAM) so uploads never touch a
  physical disk. The per-job sandbox is shredded in a `finally` block regardless.
- The persisted state is **accounts + per-day usage counts** (`PBICOMPASS_DB`)
  and **job status + rendered outputs** (`PBICOMPASS_JOBS_DB`) — never the
  uploaded `.pbix`/`.pbip` or extracted model metadata. Generated documents
  (the user's own output, not source data) are TTL-swept from `PBICOMPASS_JOBS_DB`
  the same as they previously expired from memory — this only changes *where*
  that short-lived data lives, not what's retained or for how long.
- Logs contain job IDs and statuses only — no model content.

---

## Post-deploy checklist

- [ ] `GET /healthz` returns `{"ok": true}` through your HTTPS domain.
- [ ] `/data` is a **persistent** volume (accounts survive a restart/redeploy).
- [ ] `PBICOMPASS_SANDBOX_ROOT` is a tmpfs (recommended) and has room for `PBICOMPASS_MAX_UPLOAD_MB`.
- [ ] `PBICOMPASS_ADMIN_TOKEN` set to a long random value; `/admin` unlocks with it and `/admin/api/*` 401s without it.
- [ ] Auth: `PBICOMPASS_REQUIRE_AUTH=1` set, and at least one account created via `/admin`.
- [ ] Upload the bundled `tests/fixtures/SampleSales` (zipped) through the UI and
      download the HTML — confirms the full pipeline end-to-end.
- [ ] Single instance / `--workers 1` — unless you've adopted managed Postgres
      (`PBICOMPASS_DB=postgres://...`) for accounts **and** Celery/Redis
      (`PBICOMPASS_QUEUE=celery`) for the worker; `PBICOMPASS_JOBS_DB` is
      still SQLite-single-instance either way, until it gets the same swap.
