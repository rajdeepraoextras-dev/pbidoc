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
| `PBICOMPASS_JOBS_DB` | `pbicompass_jobs.db` | Job metadata store. A plain path is SQLite; a `postgres://`/`postgresql://` URL uses managed Postgres. For Cloud Run, set this to the same Supabase Postgres URL as `PBICOMPASS_DB`. |
| `PBICOMPASS_OUTPUT_STORE` | `memory` | Rendered download-byte backend: `memory` (local default), `filesystem` (single-host durable path), or `supabase` (private Supabase Storage bucket for Cloud Run/scale-out). |
| `PBICOMPASS_OUTPUT_BUCKET` | `pbicompass-outputs` | Supabase Storage bucket used when `PBICOMPASS_OUTPUT_STORE=supabase`. Keep it private. |
| `PBICOMPASS_OUTPUT_PREFIX` | `outputs` | Object-key prefix inside the output bucket. |
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
| `PBICOMPASS_UPLOAD_RATE_LIMIT` | `20` | Max `POST /jobs` requests per IP within the window below (Day 20). Independent of the per-plan monthly quota — applies even to the unauthenticated `public` tenant. |
| `PBICOMPASS_UPLOAD_RATE_WINDOW_SECONDS` | `60` | Trailing window (seconds) the upload rate limit is measured over. |
| `SUPABASE_URL` | — | Enables Supabase Auth (Day 26-32) — signup/login/"Sign in with Microsoft" via Supabase, not this app. Unset = API-key-only, no new dependency pulled in. Install the `auth` extra too. |
| `SUPABASE_ANON_KEY` | — | The project's public/browser key — safe to expose, handed to the frontend via `GET /app/api/config`. |
| `SUPABASE_SERVICE_ROLE_KEY` | — | Server-only secret, used to resolve a user id by email for the admin-bootstrap step. Never sent to the frontend. |
| `SUPABASE_JWT_SECRET` | — | Legacy HS256 fallback only — a current Supabase project verifies via JWKS automatically and needs this unset. |
| `SUPABASE_JWT_AUD` | `authenticated` | Expected `aud` claim on a Supabase access token. |
| `PBICOMPASS_BOOTSTRAP_ADMIN_EMAIL` | — | Grants that email's Supabase user admin rights on startup (Sprint 8). |
| `PBICOMPASS_BYOK_UI` | _(off)_ | Show the "Engine API Key" (BYOK) field on the hosted upload form. Off by default (Day 31) — jobs use the provider key(s) configured below instead. |
| `PBICOMPASS_EMAIL_BACKEND` | `console` | Transactional email backend (`console` logs, `smtp` sends via `PBICOMPASS_SMTP_*`) — currently unused pending the billing work (payment-failure/receipt notices); identity email (verify/reset) is Supabase's own job now. |
| `PBICOMPASS_PUBLIC_URL` | _(empty)_ | Base URL for any future emailed links, e.g. `https://docs.example.com`. |
| `PBICOMPASS_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_FROM` / `_TLS` | — / `587` / — / — / — / `1` | SMTP credentials from your transactional provider, used only when `PBICOMPASS_EMAIL_BACKEND=smtp`. |
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

**Current recommended Cloud Run setup (Supabase DB + Supabase Storage):** if
`PBICOMPASS_DB` and `PBICOMPASS_JOBS_DB` both point at your Supabase Postgres
URL, and `PBICOMPASS_OUTPUT_STORE=supabase`, Cloud Run no longer needs a
persistent volume and completed downloads are not tied to one container's
memory.

Create a private Supabase Storage bucket first:

```sql
insert into storage.buckets (id, name, public)
values ('pbicompass-outputs', 'pbicompass-outputs', false)
on conflict (id) do nothing;
```

Then deploy:

```bash
gcloud run deploy pbicompass \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --concurrency 1 \
  --timeout 1800 \
  --max-instances 1 \
  --min-instances 0 \
  --cpu-boost \
  --set-env-vars PBICOMPASS_REQUIRE_AUTH=1,PBICOMPASS_QUEUE=inline,PBICOMPASS_OUTPUT_STORE=supabase,PBICOMPASS_OUTPUT_BUCKET=pbicompass-outputs,PBICOMPASS_MAX_UPLOAD_MB=75,PBICOMPASS_JOB_TIMEOUT_SECONDS=900,SUPABASE_URL=https://YOUR_PROJECT.supabase.co,SUPABASE_ANON_KEY=YOUR_ANON_KEY,PBICOMPASS_BOOTSTRAP_ADMIN_EMAIL=you@example.com \
  --set-secrets PBICOMPASS_DB=PBICOMPASS_DB:latest,PBICOMPASS_JOBS_DB=PBICOMPASS_DB:latest,SUPABASE_SERVICE_ROLE_KEY=SUPABASE_SERVICE_ROLE_KEY:latest,PBICOMPASS_ADMIN_TOKEN=PBICOMPASS_ADMIN_TOKEN:latest,MESHAPI_API_KEY=MESHAPI_API_KEY:latest
```

Keep `--max-instances 1` and `--concurrency 1` for the first cutover because
the default inline worker still does heavy job execution inside the web
container. The shared output store makes downloads restart-safe; Celery/Redis
or Cloud Run Jobs are still the next step before raising instance count.

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

Plans and monthly quotas, mirroring `/#pricing`: `free` 1, `pro` 10, `business`
30 docs/month. Users paste their key into the web UI's "Account API Key"
field, or send it as a header to the API. Tenants only ever see their own jobs.

**Securing the admin token:** treat it like a root password — it's a single
shared secret with no per-admin identity. Generate a long random value (32+
bytes), set it only as a platform environment variable (never commit it),
and rotate it (redeploy with a new value) if you suspect it leaked. Repeated
wrong-token attempts from the same client are locked out for 15 minutes after
8 failures, but that's a backstop, not a substitute for a strong token.

---

## Supabase Auth — signup, login, sessions (Day 26-32)

**Days 21-25 shipped a fully hand-rolled auth system** (scrypt passwords,
signed session cookies, CSRF double-submit, email verify/reset tokens, a
hand-rolled Microsoft OIDC flow). **That system was retired and replaced with
Supabase Auth** in Days 26-32: identity — signup, login, email verification,
password reset, "Sign in with Microsoft" — is now Supabase's job, not this
app's. `service/oidc.py` and `service/passwords.py` are deleted; the old
`/auth/*` routes are gone. What this app still owns: the tenant/plan/quota/
API-key entity (`accounts`, `usage`, `api_keys` — unchanged) and a thin
`account_users` mapping from a Supabase user id to one of those accounts.

**Why:** a from-scratch, well-tested auth system is real engineering effort to
keep maintaining (session fixation, CSRF, token rotation, SMTP deliverability,
OIDC edge cases); Supabase's hosted product does all of that, with its own
dashboard for browsing signed-up users, at no extra infrastructure cost for a
small/medium deployment.

**Setup:**
1. Create a project at [supabase.com](https://supabase.com) (free tier is fine
   to start). Note the **Project URL** and **anon/public key** (Project
   Settings → API).
2. Authentication → Providers: **Email** is on by default. To keep "Sign in
   with Microsoft," enable the **Azure** provider using the same Entra app
   registration client id/secret the old hand-rolled OIDC flow used (redirect
   URI is now whatever Supabase's dashboard shows for that provider, not
   `/auth/oidc/callback`).
3. Authentication → Settings → **configure a custom SMTP sender** before real
   users sign up — Supabase's free-tier built-in mailer is low-volume and will
   silently queue/drop verification emails under real traffic. Reuse whatever
   SMTP credentials you already have.
4. (Recommended) Project Settings → Database → grab the **Postgres connection
   string** and point `PBICOMPASS_DB` at it (see the managed-Postgres section
   below) — this app's own tables and Supabase's `auth.*` tables then live in
   one database, which is what makes admin user-search efficient later.
5. Set the env vars:
   ```bash
   SUPABASE_URL=https://<project-ref>.supabase.co
   SUPABASE_ANON_KEY=<anon/public key>
   SUPABASE_SERVICE_ROLE_KEY=<service role key>   # server-only, never exposed to the frontend
   ```
   `SUPABASE_JWT_SECRET` is only needed as a legacy HS256 fallback for an
   older Supabase project still on a shared signing secret — a current
   project verifies via JWKS automatically and needs nothing extra.
6. `pip install "pbicompass[auth]"` (adds `PyJWT[crypto]`, the only new
   dependency this migration introduces).

**How it works end-to-end:** the frontend (`static/index.html`/`static/app.html`)
loads a vendored `supabase-js` (`static/vendor/supabase.js` — a real npm
package build, not a CDN `<script>` tag, so a CDN outage can't block sign-in)
and drives signup/login/OAuth/logout directly against Supabase. Every call
back to this app's own API then carries `Authorization: Bearer <supabase
access token>`. `resolve_tenant()` tells that token apart from a
`pbicompass_sk_...` API key by shape (three dot-separated segments) and
verifies it (`service/supabase_auth.py`, JWKS-based, cached, refetches once on
an unrecognized `kid`). **A user's very first authenticated request
JIT-provisions their account** — no Supabase webhook needed — via
`AccountStore.get_or_create_account_for_supabase_user()`, the same tenant/
plan/API-key setup self-serve signup always created.

```bash
# Prove it end-to-end: sign up/log in through the Supabase JS SDK in a
# browser (not curl -- there's no server-side signup endpoint anymore), grab
# the access token from the browser's dev tools, then:
curl https://your-host/app/api/me -H "Authorization: Bearer <supabase access token>"
curl https://your-host/jobs/... -H "Authorization: Bearer <supabase access token>"
```

**Bearer auth needs no CSRF token** (of either kind — API key or Supabase
JWT): unlike the retired session cookie, it's never an ambient browser
credential a cross-site page can attach on your behalf.

**The "Engine API Key" (BYOK) field is now hidden by default** on the hosted
upload form (`PBICOMPASS_BYOK_UI=0`) — a signed-in visitor's job runs on
whatever provider key *you* set server-side (`ANTHROPIC_API_KEY` etc.), never
one they type in. Set `PBICOMPASS_BYOK_UI=1` to bring the field back for a
self-host deployment that still wants per-job BYOK (the pre-Day-31 default).

**Migrating an existing deployment.** If you already had real users signed up
under the old password system, their passwords cannot transfer to
Supabase — send each one a Supabase "invite" or password-reset email so they
set a Supabase password once. (If this deployment never had real self-serve
signups yet, there's nothing to migrate.) The old `users`/`sessions`/
`email_tokens`/`oidc_states`/`memberships` tables are simply no longer read or
written by this app — they are **not** dropped automatically (no silent-data-
loss migration runs on startup). To reclaim the space once you've confirmed
you don't need them:
```sql
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS email_tokens;
DROP TABLE IF EXISTS oidc_states;
DROP TABLE IF EXISTS memberships;
```

**Account dashboard — `/app`.** Signed-in users still manage themselves here
— plan/usage, **API-key management** (list/create/revoke, unchanged from Day
24), and job history — now authenticated by a Supabase Bearer token instead
of a session cookie; the operator `/admin` panel remains for provisioning/
support (`GET /app`, `GET /app/api/config`, `GET /app/api/me`, `GET/POST
/app/api/keys`, `DELETE /app/api/keys/{id}`, `GET /app/api/jobs`).

**Self-host without Supabase.** Leave `SUPABASE_URL` unset entirely and
nothing changes: the app stays on the API-key-only path exactly as it worked
before Day 26 (`pbicompass account create`/the admin panel), zero new
dependencies pulled in, `/app`'s sign-in form shows a "not configured" note
instead of a login form.

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
no per-plan monthly quota to fall back on. A client over the limit gets a 429
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
