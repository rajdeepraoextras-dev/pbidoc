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
| `ANTHROPIC_API_KEY` | — | Enables the Claude engine (install the `agents` extra too). |
| `GEMINI_API_KEY` | — | Enables the Gemini engine (install the `agents` extra too; `GOOGLE_API_KEY` also works). |

---

## Option A — Managed platform (recommended, freemium-friendly)

Works on **Render**, **Railway**, or **Fly.io** (all have free/hobby tiers). The
repo already has a `Dockerfile`.

1. Push this repo to GitHub.
2. Create a new **Web Service from a Dockerfile** on your platform.
3. Add a **persistent disk/volume** mounted at `/data` (1 GB is plenty).
4. Set environment variables:
   - `PBICOMPASS_DB=/data/pbicompass.db`
   - `PBICOMPASS_REQUIRE_AUTH=1` (for a hosted SaaS) — or leave unset to run open.
   - `ANTHROPIC_API_KEY=...` (optional, for Claude).
5. The platform builds the image, runs the `CMD`, assigns a URL, and terminates
   TLS for you. Health check path: `/healthz`.
6. Create your first account (see *Enabling auth* below) via the platform's
   shell/exec, then hand out the API key.

Fly.io example: ensure the service listens on `0.0.0.0:8000` (it does) and add a
volume:
```bash
fly launch --no-deploy            # detects the Dockerfile
fly volumes create data --size 1
# in fly.toml: [mounts] source="data" destination="/data"
fly secrets set PBICOMPASS_REQUIRE_AUTH=1 ANTHROPIC_API_KEY=sk-...
fly deploy
```

---

## Option B — Your own VM with Docker + Caddy (auto-HTTPS)

On any VPS (Hetzner, DigitalOcean, EC2, …) with a domain pointed at it:

```bash
# 1. Build
git clone <your-repo> pbicompass && cd pbicompass
docker build -t pbicompass .

# 2. Run (persistent /data volume, tmpfs sandbox for RAM-only zero-retention)
docker run -d --name pbicompass \
  -p 127.0.0.1:8000:8000 \
  -v pbicompass-data:/data \
  --tmpfs /tmp/pbicompass:rw,size=512m \
  -e PBICOMPASS_DB=/data/pbicompass.db \
  -e PBICOMPASS_REQUIRE_AUTH=1 \
  -e ANTHROPIC_API_KEY=sk-...   `# optional` \
  --restart unless-stopped \
  pbicompass
```

Put **Caddy** in front for automatic HTTPS (one file, `Caddyfile`):
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
Caddy fetches and renews a Let's Encrypt certificate automatically. Done.

> Prefer systemd over Docker? Install with `pip install ".[service]"` into a venv
> and run `uvicorn pbicompass.service.app:app --host 127.0.0.1 --port 8000` under a
> systemd unit, with Caddy/nginx in front. Same env vars apply.

---

## Enabling auth & creating accounts

With `PBICOMPASS_REQUIRE_AUTH=1`, every request needs `Authorization: Bearer <key>`.
Create accounts with the CLI (runs inside the container/host, same `PBICOMPASS_DB`):

```bash
# inside the running container (e.g. `docker exec -it pbicompass bash`)
pbicompass account create --tenant acme --name "Acme BI" --plan pro
#   -> prints the API key ONCE — copy it and give it to the customer
pbicompass account list
```

Plans and daily quotas: `free` 10, `pro` 200, `enterprise` 100k docs/day. Users
paste their key into the web UI's "API key" field, or send it as a header to the
API. Tenants only ever see their own jobs.

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
- [ ] Auth: `PBICOMPASS_REQUIRE_AUTH=1` set, and at least one account created.
- [ ] Upload the bundled `tests/fixtures/SampleSales` (zipped) through the UI and
      download the HTML — confirms the full pipeline end-to-end.
- [ ] Single instance / `--workers 1` (until the Celery/Redis swap).
