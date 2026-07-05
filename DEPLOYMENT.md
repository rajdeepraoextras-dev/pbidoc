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

> **One constraint for v1:** the job store is in-process, so run **a single
> instance / single worker**. It comfortably handles real traffic (jobs are
> short and I/O-bound). Horizontal scale comes later via the Celery/Redis swap —
> the worker is already written to be queue-agnostic.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PBICOMPASS_REQUIRE_AUTH` | _(off)_ | Set `1` to require API keys (hosted SaaS). Off = open `public` tenant. |
| `PBICOMPASS_DB` | `pbicompass.db` | SQLite path for accounts. Point at the persistent volume, e.g. `/data/pbicompass.db`. |
| `PBICOMPASS_SANDBOX_ROOT` | system temp | Per-job working dir. Point at a tmpfs (RAM) for strict zero-retention. |
| `PBICOMPASS_MAX_UPLOAD_MB` | `100` | Max upload size. |
| `PBICOMPASS_JOB_TIMEOUT_SECONDS` | `600` | Watchdog: force-fail a job stuck in "processing" longer than this (hung LLM call, oversized file). |
| `PBICOMPASS_ADMIN_TOKEN` | _(off)_ | Enables the `/admin` panel (create/list/revoke accounts from the browser). Unset = panel disabled. |
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

- `--max-instances=1` — the job store and accounts DB are in-process/SQLite-
  on-local-disk (see the single-instance constraint above). If Cloud Run scales
  to a 2nd instance, a job created on instance A 404s when polled on instance
  B, and each instance has its own (different) accounts DB.
- `--no-cpu-throttling` ("CPU always allocated") — by default Cloud Run
  throttles CPU to ~zero outside of an active request. Documentation jobs run
  in a `BackgroundTasks` coroutine *after* the upload request returns, so a
  throttled instance only makes progress during the brief CPU windows opened
  by the browser's status-polling requests — the same failure class as the
  hang the job-timeout watchdog was added for. Check current Cloud Run
  billing before enabling this: it can reduce free-tier coverage. If you must
  keep throttling for cost reasons, accept poll-driven (slower, but
  watchdog-bounded) progress instead.
- Without a persistent volume, `PBICOMPASS_DB` and any sandbox files on
  Cloud Run's container filesystem are wiped on every redeploy/restart — mount
  a volume (Cloud Run volume mounts, or point `PBICOMPASS_DB` at Cloud SQL/
  managed Postgres) if accounts need to survive redeploys.

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

## Zero-retention in production

- Set `PBICOMPASS_SANDBOX_ROOT` to a **tmpfs** mount (RAM) so uploads never touch a
  physical disk. The per-job sandbox is shredded in a `finally` block regardless.
- The only persisted state is **accounts + per-day usage counts** in
  `PBICOMPASS_DB` — never report metadata. Generated documents live in memory with a
  short TTL.
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
- [ ] Single instance / `--workers 1` (until the Celery/Redis swap).
