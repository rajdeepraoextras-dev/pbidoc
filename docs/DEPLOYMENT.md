# PBICompass Production Runbook

This is the production deployment guide for the current hosted setup:

- **Compute:** Google Cloud Run
- **Database:** Supabase Postgres
- **Generated outputs:** private Supabase Storage bucket
- **Auth:** Supabase Auth plus the PBICompass account/quota tables
- **Secrets:** Google Secret Manager
- **Public service URL:** `https://pbicompass-3phuqyy4ba-uc.a.run.app`
- **Custom domain:** `pbicompass.com` and `www.pbicompass.com` are mapped to the
  same Cloud Run service through Google-managed domain mapping. DNS propagation
  and certificate provisioning happen automatically after the records are in
  place.

Older VM/Caddy/Render/Railway/Fly instructions were removed from this runbook.
The VM path is not the production target anymore.

---

## Production Architecture

```text
Browser
  |
  | HTTPS / HTTP/2 upload
  v
Cloud Run: pbicompass
  |
  | metadata, accounts, jobs
  v
Supabase Postgres
  |
  | rendered download files
  v
Supabase Storage: pbicompass-outputs
```

Uploads are processed in a per-job sandbox and then deleted. The app stores
job/account metadata in Postgres and stores only generated download bytes in
private Supabase Storage. A five-minute Cloud Scheduler job invokes the
authenticated Storage-API sweep. Access expires after the 55-minute TTL;
physical deletion is retried if Supabase Storage is temporarily unavailable.

---

## Current Production Values

| Item | Value |
|---|---|
| GCP project | `gold-atlas-501305-t1` |
| Cloud Run service | `pbicompass` |
| Region | `us-central1` |
| Active public URL | `https://pbicompass-3phuqyy4ba-uc.a.run.app` |
| Health endpoint | `/app/api/health` |
| Supabase project ref | `pxruqotkfozeadkenbth` |
| Supabase Storage bucket | `pbicompass-outputs` |

### Custom Domain DNS

For Domain India, the records currently used for `pbicompass.com` are:

| Type | Host | Value |
|---|---|---|
| A | `@` | `216.239.32.21` |
| A | `@` | `216.239.34.21` |
| A | `@` | `216.239.36.21` |
| A | `@` | `216.239.38.21` |
| AAAA | `@` | `2001:4860:4802:32:0:0:0:15` |
| AAAA | `@` | `2001:4860:4802:34:0:0:0:15` |
| AAAA | `@` | `2001:4860:4802:36:0:0:0:15` |
| AAAA | `@` | `2001:4860:4802:38:0:0:0:15` |
| CNAME | `www` | `ghs.googlehosted.com` |

Keep the existing `google-site-verification` TXT record in place. After DNS
propagates, Google finishes the HTTPS certificate automatically. Until the
certificate is ready, the domain mapping can look closed or pending even though
the records are correct.

---

## Required Local Tools

Use the Google Cloud SDK installed on this machine:

```powershell
$env:CLOUDSDK_PYTHON = "C:\Python314\python.exe"
$gcloud = "C:\Users\resod\google-cloud-cli\google-cloud-sdk\bin\gcloud.cmd"
& $gcloud auth login
& $gcloud config set project gold-atlas-501305-t1
```

The `CLOUDSDK_PYTHON` line avoids the Windows Microsoft Store Python alias
breaking `gcloud`.

---

## One-Time GCP Setup

Enable required APIs:

```powershell
& $gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com --project gold-atlas-501305-t1
```

Give the Cloud Run runtime service account access to Secret Manager:

```powershell
& $gcloud projects add-iam-policy-binding gold-atlas-501305-t1 `
  --member "serviceAccount:797249441522-compute@developer.gserviceaccount.com" `
  --role "roles/secretmanager.secretAccessor"
```

---

## Required Secrets

These live in Google Secret Manager:

| Secret | Purpose |
|---|---|
| `PBICOMPASS_DB` | Supabase Postgres connection string. Used for accounts, jobs, usage, visits. |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only Supabase key for privileged auth/storage operations. |
| `SUPABASE_ANON_KEY` | Public browser key, still stored as a secret for deploy hygiene. |
| `PBICOMPASS_ADMIN_TOKEN` | Break-glass admin token for `/admin`, `/metrics`, and admin APIs. |
| `PBICOMPASS_MAINTENANCE_TOKEN` | Narrow token used only by the scheduled output-expiry sweep. |
| `MESHAPI_API_KEY` | Optional. Enables MeshAPI provider. |
| `ANTHROPIC_API_KEY` | Optional. Enables Claude provider. |
| `GEMINI_API_KEY` | Optional. Enables Gemini provider. |
| `COHERE_API_KEY` | Optional. Enables Cohere provider. |

Create or update a secret from the local `.env`:

```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
    $name=$matches[1].Trim()
    $value=$matches[2].Trim().Trim('"').Trim("'")
    [Environment]::SetEnvironmentVariable($name,$value,'Process')
  }
}

& $gcloud secrets create PBICOMPASS_DB --project gold-atlas-501305-t1 --replication-policy automatic
$env:PBICOMPASS_DB | & $gcloud secrets versions add PBICOMPASS_DB --project gold-atlas-501305-t1 --data-file=-
```

If the secret already exists, only run the `secrets versions add` command.

Never commit `.env`. `.gcloudignore` also prevents `.env`, local DB files,
output folders, and sample PBIX/PBIP files from being uploaded during
`gcloud run deploy --source .`.

---

## Supabase Setup

### Storage Bucket

The output bucket must exist and stay private:

```sql
insert into storage.buckets (id, name, public)
values ('pbicompass-outputs', 'pbicompass-outputs', false)
on conflict (id) do nothing;
```

Verify:

```sql
select id, name, public
from storage.buckets
where id = 'pbicompass-outputs';
```

### Database Guardrail

Set a timeout so stale pooler transactions cannot block schema changes:

```sql
alter database postgres set idle_in_transaction_session_timeout = '30s';
```

### Check for Stale Transactions

```sql
select pid, state, wait_event_type, wait_event,
       now() - xact_start as xact_age,
       left(query, 160) as query
from pg_stat_activity
where datname = current_database()
  and pid <> pg_backend_pid()
  and state = 'idle in transaction'
order by xact_start nulls last;
```

If an old migration or previous revision left stale sessions, terminate only
clearly stale idle-in-transaction sessions:

```sql
select pg_terminate_backend(pid) as terminated, pid
from pg_stat_activity
where datname = current_database()
  and pid <> pg_backend_pid()
  and state = 'idle in transaction'
  and now() - xact_start > interval '5 minutes';
```

---

## Deploy to Production

Run from the repo root after tests pass:

```powershell
$env:CLOUDSDK_PYTHON = "C:\Python314\python.exe"
$gcloud = "C:\Users\resod\google-cloud-cli\google-cloud-sdk\bin\gcloud.cmd"

Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
    $name=$matches[1].Trim()
    $value=$matches[2].Trim().Trim('"').Trim("'")
    [Environment]::SetEnvironmentVariable($name,$value,'Process')
  }
}

$serviceUrl = "https://pbicompass-3phuqyy4ba-uc.a.run.app"
$envVars = @(
  "PBICOMPASS_ENV=production",
  "PBICOMPASS_REQUIRE_AUTH=1",
  "PBICOMPASS_QUEUE=inline",
  "PBICOMPASS_PUBLIC_URL=$serviceUrl",
  "PBICOMPASS_OUTPUT_STORE=supabase",
  "PBICOMPASS_OUTPUT_BUCKET=pbicompass-outputs",
  "PBICOMPASS_OUTPUT_PREFIX=outputs",
  "PBICOMPASS_OUTPUT_TTL_SECONDS=3300",
  "PBICOMPASS_MAX_UPLOAD_MB=100",
  "PBICOMPASS_MAX_EXTRACTED_MB=512",
  "PBICOMPASS_MAX_ARCHIVE_ENTRIES=20000",
  "PBICOMPASS_MAX_AUX_UPLOAD_KB=2048",
  "PBICOMPASS_JOB_TIMEOUT_SECONDS=900",
  "PBICOMPASS_UPLOAD_RATE_LIMIT=20",
  "PBICOMPASS_UPLOAD_RATE_WINDOW_SECONDS=60",
  "SUPABASE_URL=https://pxruqotkfozeadkenbth.supabase.co"
) -join ","

& $gcloud run deploy pbicompass `
  --source . `
  --project gold-atlas-501305-t1 `
  --region us-central1 `
  --allow-unauthenticated `
  --memory 4Gi `
  --cpu 2 `
  --concurrency 1 `
  --timeout 1800 `
  --max-instances 3 `
  --min-instances 1 `
  --no-cpu-throttling `
  --cpu-boost `
  --use-http2 `
  --set-env-vars $envVars `
  --set-secrets PBICOMPASS_DB=PBICOMPASS_DB:latest,PBICOMPASS_JOBS_DB=PBICOMPASS_DB:latest,SUPABASE_SERVICE_ROLE_KEY=SUPABASE_SERVICE_ROLE_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest,PBICOMPASS_ADMIN_TOKEN=PBICOMPASS_ADMIN_TOKEN:latest,PBICOMPASS_MAINTENANCE_TOKEN=PBICOMPASS_MAINTENANCE_TOKEN:latest
```

Add provider secrets to `--set-secrets` only after those secrets exist, for
example:

```powershell
--set-secrets MESHAPI_API_KEY=MESHAPI_API_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest
```

### Why These Flags Matter

| Flag | Reason |
|---|---|
| `--use-http2` | Required for large uploads. Cloud Run HTTP/1 requests are limited to 32 MiB; HTTP/2 lets 70+ MiB PBIP ZIPs reach the app. |
| `--concurrency 1` | One heavy document job per container. Prevents memory/CPU contention. |
| `--min-instances 1 --max-instances 3` | Keeps one warm instance and allows three concurrent single-request instances. Hosted production uses shared Postgres job state and private Supabase Storage, so polling and downloads remain visible across instances. |
| `--min-instances 1 --no-cpu-throttling` | Keeps CPU available while inline background tasks finish and always keeps one worker warm. |
| `--timeout 1800` | Allows long report generation requests/status flows without premature platform timeout. |
| `--memory 4Gi --cpu 2` | Gives the parser/renderers enough room for large Power BI projects. |
| `--cpu-boost` | Faster cold starts and startup schema checks. |

The image uses Hypercorn and listens on `$PORT`. Docker `HEALTHCHECK` is
disabled because Cloud Run manages startup probes, and periodic container
health probes should not touch Supabase Postgres.

### Output Retention Schedule

The GitHub deployment workflow creates or updates `pbicompass-output-sweep`
in Cloud Scheduler. For a manual deployment, create the same job after loading
`PBICOMPASS_MAINTENANCE_TOKEN` into the current process:

```powershell
& $gcloud scheduler jobs create http pbicompass-output-sweep `
  --project gold-atlas-501305-t1 `
  --location us-central1 `
  --schedule "*/5 * * * *" `
  --time-zone UTC `
  --uri "$serviceUrl/internal/maintenance/sweep" `
  --http-method POST `
  --headers "X-Maintenance-Token=$env:PBICOMPASS_MAINTENANCE_TOKEN" `
  --attempt-deadline 300s
```

Use `gcloud scheduler jobs update http` instead when the job already exists.
Supabase Storage objects must be deleted through the Storage API; do not delete
rows directly from `storage.objects`, which would orphan the underlying files.

---

## Post-Deploy Verification

### 1. Confirm Active Revision

```powershell
& $gcloud run services describe pbicompass `
  --project gold-atlas-501305-t1 `
  --region us-central1 `
  --format "value(status.url,status.latestReadyRevisionName,status.traffic[0].revisionName)"
```

Expected: the latest ready revision and traffic revision should match.

### 2. Check Health

```powershell
Invoke-WebRequest `
  -Uri "https://pbicompass-3phuqyy4ba-uc.a.run.app/app/api/health" `
  -UseBasicParsing `
  -TimeoutSec 90
```

Expected body:

```json
{"ok":true,"checks":{"jobs_db":true,"accounts_db":true,"queue":true}}
```

`/healthz` still exists inside the app, but `/app/api/health` is the preferred
external health URL for production checks.

### 3. Check Runtime Config

```powershell
Invoke-WebRequest `
  -Uri "https://pbicompass-3phuqyy4ba-uc.a.run.app/app/api/config" `
  -UseBasicParsing
```

Confirm:

- `accounts_enabled: true`
- `supabase_enabled: true`
- provider entries show `enabled: true` only for keys that are configured

### 4. Check Logs

```powershell
& $gcloud run services logs read pbicompass `
  --project gold-atlas-501305-t1 `
  --region us-central1 `
  --limit 120
```

Useful patterns:

- `POST /jobs` should appear after an upload reaches the app.
- If the browser says upload failed but no `POST /jobs` appears, suspect a
  platform/browser/network rejection before FastAPI.
- `429` means rate limiting or quota rejection.
- `5xx` means app/platform failure; inspect nearby stack/log context.

### 5. Large Upload Smoke Test

The production service was verified with a 73,997,705-byte ZIP over HTTP/2.
Windows `curl.exe` may not support HTTP/2, so use a browser or Python/httpx
with `h2` installed.

Browser test:

1. Open `https://pbicompass-3phuqyy4ba-uc.a.run.app/app`.
2. Hard refresh once with `Ctrl+F5`.
3. Upload the large `.zip`.
4. Confirm the UI moves from upload to generation instead of showing
   `Upload failed`.

---

## Rollback

List revisions:

```powershell
& $gcloud run revisions list `
  --service pbicompass `
  --project gold-atlas-501305-t1 `
  --region us-central1
```

Route all traffic to a previous revision:

```powershell
& $gcloud run services update-traffic pbicompass `
  --project gold-atlas-501305-t1 `
  --region us-central1 `
  --to-revisions PREVIOUS_REVISION=100
```

Verify health after rollback:

```powershell
Invoke-WebRequest -Uri "https://pbicompass-3phuqyy4ba-uc.a.run.app/app/api/health" -UseBasicParsing
```

---

## Common Production Failures

### Upload failed immediately

Likely causes:

- Browser has cached old frontend JS. Hard refresh with `Ctrl+F5`.
- Service is not on an HTTP/2-enabled revision. Check Cloud Run YAML for
  `ports.name: h2c` or redeploy with `--use-http2`.
- File exceeds `PBICOMPASS_MAX_UPLOAD_MB`.
- User has hit monthly quota or IP rate limit.

Check logs for `POST /jobs`. If there is no `POST /jobs`, the request did not
reach FastAPI.

### AI provider disabled

Check `/app/api/config`. If a provider is `enabled:false`, its server key is
missing or disabled by admin settings. Add the provider secret and redeploy.

### Health is 503

The health response tells which dependency failed:

- `jobs_db:false` - Supabase Postgres/job store problem.
- `accounts_db:false` - Supabase Postgres/account store problem.
- `queue:false` - only relevant if `PBICOMPASS_QUEUE=celery`.

Check Cloud Run logs and Supabase session state.

### Rate exceeded / 429

The app has per-IP request limits. Defaults:

- uploads: `PBICOMPASS_UPLOAD_RATE_LIMIT=20` per `60` seconds
- AI assist: separate assist limiter

Wait for the window to clear or temporarily raise the limit and redeploy.

### DuckDNS domain still points to old VM

`pbicompass.duckdns.org` currently points to an IP, while Cloud Run gives a
hostname. Because the old VM was deleted, that DuckDNS name will not reach
Cloud Run directly unless you add an IP-based bridge.

Options:

1. Best product option: buy a normal domain and map it directly to Cloud Run.
2. Keep exact DuckDNS name: run a tiny proxy/load-balancer with a stable IP and
   point DuckDNS to that IP.
3. Use Google HTTPS Load Balancer in front of Cloud Run and point DuckDNS to
   the load balancer IP. This is more production-grade but not free.

---

## Production Checklist

Before calling a deploy good:

- [ ] Full test suite passes: `.\.venv\Scripts\python.exe -m pytest`
- [ ] Deploy command used `--use-http2`
- [ ] Active revision receives 100% traffic
- [ ] `/app/api/health` returns `ok:true`
- [ ] `/app/api/config` shows Supabase enabled
- [ ] Large ZIP upload reaches `POST /jobs`
- [ ] Supabase output bucket smoke test passes
- [ ] Cloud Run logs show no startup errors
- [ ] Secrets are in Secret Manager, not committed
- [ ] `.gcloudignore` excludes `.env`, local DBs, outputs, and sample PBIX/PBIP files
- [ ] New production change is committed to Git

---

## What Not To Use Anymore

Do not use the old VM deployment path for production:

- no Compute Engine app VM
- no Caddy/Nginx app-hosting instructions
- no Docker volume at `/data` for production state
- no Render/Railway/Fly production instructions

Those approaches are fine for experiments, but the supported production path
for this repo is now Cloud Run plus Supabase.
