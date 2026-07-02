# Security

PBICompass's core design goal is **zero data leakage**: it extracts Power BI
*metadata* (schema, DAX, relationships, report layout) and never materializes
row-level business data. This document explains the security model and how to
report a problem.

## Data-handling model

- **Metadata only, by construction.** The `.pbip` parser reads plain-text
  TMDL/PBIR definitions — there is no code path that reads row data, because
  the format never contains any. The `.pbix` adapter uses `pbixray` to read
  schema/DAX/relationships from the compressed `DataModel` part and
  deliberately never calls its row-level `get_table()`/`get_dataframe()`
  methods.
- **Connection strings are redacted.** Data-source detection keeps the
  connector type and server/database name; credentials embedded in M queries
  are stripped before they reach `model.json`.
- **Zero-retention web service.** Each upload is processed inside a per-job
  sandbox (`service/sandbox.py`) that is deleted in a `finally` block —
  success or failure — the moment rendering finishes. Only the rendered
  output survives, in memory, for a short TTL (`service/jobs.py`).
- **No metadata in logs.** Errors and log lines are content-free (job IDs and
  exception types, never model content) — see the `_FRIENDLY` messages in
  `service/worker.py`.
- **Accounts store the minimum.** When auth is enabled, the SQLite accounts
  database holds a hashed API key, tenant, plan, and per-day usage *counts*
  only (`service/accounts.py`) — never anything about a customer's report.

## Deploying safely

- Set `PBICOMPASS_SANDBOX_ROOT` to a tmpfs (RAM) mount in production so uploads
  never touch a physical disk (see `DEPLOYMENT.md`).
- Enable `PBICOMPASS_REQUIRE_AUTH=1` before exposing an instance beyond a private
  smoke test — the open `public` tenant has no rate limit.
- Treat `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` like any other secret: set them
  as platform environment variables, never commit them (see `.env.example`
  and `.gitignore`).
- BYOK provider keys (pasted into the web UI or sent as `provider_api_key`)
  are used for a single job only and are never logged or persisted.

## Reporting a vulnerability

If you find a security issue (a way to exfiltrate row-level data, bypass
tenant isolation, escape the job sandbox, etc.), please open a GitHub issue
marked `security` or contact the maintainer directly rather than filing a
public exploit write-up. Include repro steps and the affected version/commit.
