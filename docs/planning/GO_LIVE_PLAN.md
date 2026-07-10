# Go-Live Plan: Supabase Auth + Stripe Quota Billing + Full Admin App

> **Status (2026-07-10): Sprint 6 (Days 26–32, Supabase Auth migration) is
> done.** See `ROADMAP_PROGRESS.md`'s "Sprint 6 — Supabase Auth migration"
> entry for what shipped and how it was verified (606 passed, 2 pre-existing
> unrelated failures). Sprints 7–9 (Stripe billing, full admin app,
> hardening/launch) below are **not started**. This file was originally
> approved as a Claude Code plan-mode plan; copied into the repo so the
> day-by-day detail survives a multi-tool handoff (Claude ↔ Antigravity/
> Gemini), per this project's standing convention.

## Context

PBICompass's auth/account foundation was built over the last several days (Sprints 1–5 of `docs/planning/PRODUCTION_ROADMAP.md`) and, as of 2026-07-10, **Sprint 5 is fully done and tested (653 passing tests)**: hand-rolled signup/login, email verification, password reset, Microsoft sign-in, sessions, and a working self-serve dashboard where a user can already upload and get output using nothing but their browser session — no API key needed. That part of "make it live for normal users" is closer to done than it looked from the outside.

Two things are genuinely missing, and one thing needs to be un-done:
1. **No payment/billing exists anywhere in the code.** Signup always creates a free account; there's no Stripe integration; today's "quota" is a *daily* cap per plan, not a purchasable tier like "10 reports" or "50 reports."
2. **The admin panel is a single shared-secret token page** — account CRUD only, no user search, no job browsing, no revenue view.
3. **The upload form still asks every visitor for their own AI provider key** ("Engine API Key"). The backend can already fall back to a key set server-side — the UI just isn't wired to hide the field and use that by default.

You've decided to **fully migrate identity to Supabase Auth** (replacing the hand-rolled auth built in Sprints in place), add **Stripe monthly-subscription billing** for quota tiers, and build a **full admin app with real admin logins**. Enterprise features (orgs/teams, RBAC, seats, SSO/SCIM, audit log — `PRODUCTION_ROADMAP.md` §8) stay explicitly deferred and untouched, as do the AI-quality work (Sprints 1–3, already done), the "Ask about this report" feature, and the wireframe/landing design push — none of that is in scope here.

This plan **supersedes** `PRODUCTION_ROADMAP.md`'s old Sprint 6 (which assumed the hand-rolled auth being replaced here). `PRODUCTION_ROADMAP.md` §8 (Enterprise) is left alone.

---

## The end-state flow

**Normal user:**
1. Visits the site, clicks Sign Up. Signs up with email+password or "Sign in with Microsoft" — both handled by Supabase Auth (its hosted UI/SDK, not our own forms). Supabase sends the verification email itself.
2. Logs in. Lands back on the site with a session (a Supabase JWT held client-side).
3. Picks a monthly plan — Free / Starter (10 docs/mo) / Growth (50 docs/mo) — or stays free. Paid tiers go through Stripe Checkout; card details never touch our server.
4. Uploads a `.pbix`/`.pbip` straight from the landing page. **No API key field.** The job runs using the AI provider key *you* configured server-side (env var) — never the user's.
5. Downloads the four generated docs. Usage/quota and "Manage billing" (Stripe customer portal, for cancel/upgrade/invoices) are visible on the account dashboard.

**You (admin):**
- Log into `/admin` with your own Supabase account (granted admin via a one-time bootstrap step). See a searchable user list, per-user detail (plan/usage/jobs), a manual plan/quota override, all jobs across every tenant, and a revenue/MRR summary once billing data exists. A break-glass shared-token fallback stays available in case Supabase itself has an outage.

**Programmatic/API users:** completely unaffected — the existing `Authorization: Bearer pbicompass_sk_...` API-key path is untouched, byte-for-byte, throughout this whole plan.

---

## Locked-in decisions

1. **Auth → Supabase Auth**, fully replacing the hand-rolled system. Product data (tenant/plan/quota/API keys) stays in our own tables, re-keyed off the Supabase user id instead of a local password record.
2. **Billing → Stripe, monthly subscription tiers** (recurring charge, quota resets each calendar month). Orgs/teams/RBAC/seats/SSO/audit-log stay out of scope.
3. **Admin → full app, real logins.** Admin identity is a Supabase user flagged `is_admin` in our DB. The current shared-secret token (`PBICOMPASS_ADMIN_TOKEN`) is kept as a break-glass fallback, not removed.
4. **No more Engine API Key prompt for hosted users.** Server-side provider key (env var) becomes the default. The BYOK field is kept but hidden by default, re-enable-able via a flag for self-host installs that want it.

---

## Data model changes (`src/pbicompass/service/accounts.py`, same `_Connection` sqlite/Postgres abstraction — no new dialect fork)

| Table | Change |
|---|---|
| `accounts` | keep; add `quota_override INTEGER NULL` (admin manual override); `plan` values become `free`/`starter`/`growth` |
| `usage` | keep, repurpose: the `day` column becomes a generic *period key* (`2026-07-10` in daily/self-host mode, `2026-07` in monthly/hosted mode) — no schema break, no rename |
| `api_keys` | unchanged — this is the entire Bearer-API-key path |
| `users`, `sessions`, `email_tokens`, `oidc_states` | **drop** — identity moves to Supabase's own `auth.users`; explicit one-time `DROP TABLE IF EXISTS` migration step, documented in `DEPLOYMENT.md`, not automatic on boot |
| `memberships` | replaced by `account_users(user_id TEXT PK, account_id TEXT, role TEXT DEFAULT 'owner', created_at REAL)` — `user_id` is now the Supabase UUID; `role` kept unused for future teams work, no logic built on it |
| `admin_users` | **new**: `(user_id TEXT PK, granted_at REAL)` — presence = is-admin |
| `billing_accounts` | **new**: `(account_id TEXT PK, stripe_customer_id, stripe_subscription_id, status, current_period_end, updated_at)` |
| `stripe_events` | **new**: `(event_id TEXT PK, processed_at REAL)` — webhook idempotency |

`AccountStore.dump()`/`.restore()` gets extended to snapshot the new tables (schema version bump).

---

## Implementation sequence

Continues the existing "Day N" numbering from Day 25 (last completed day in `ROADMAP_PROGRESS.md`). Auth must land first (billing and admin both depend on "who is this user"); billing and the non-revenue half of admin can then proceed in parallel — the admin revenue tile is the one piece that needs Stripe data to exist first.

### Sprint 6 — Supabase Auth migration (Days 26–32) ✅ Done
- **D26** — Create/configure the Supabase project (Email provider + Azure/Microsoft OAuth provider using the existing Entra app registration). Decide JWT verification: JWKS (RS256/ES256) primary, `SUPABASE_JWT_SECRET` (HS256) as legacy fallback. New env vars: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_JWT_AUD`. New `pyproject.toml` extra: `auth = ["PyJWT[crypto]"]`.
- **D27** — New `service/supabase_auth.py`: JWKS fetch/cache (keyed by `kid`, ~10–15min TTL, refetch-once on unknown `kid`, never loops on a bad one), `verify_jwt()`. New `tests/test_supabase_auth.py` using a locally-generated keypair as a stand-in JWKS (no live network).
- **D28** — `accounts.py`: add `account_users`/`admin_users`/`quota_override`; new `get_or_create_account_for_supabase_user()` (JIT-provisions an account on a new user's first authenticated request — no Supabase webhook needed). Delete the now-dead user/session/email-token/oidc-state methods.
- **D29** — `app.py`: `resolve_tenant()` gets a third branch — `Authorization: Bearer` is either a `pbicompass_sk_...` API key (unchanged path) or a JWT (3 dot-separated segments) verified via `supabase_auth`. **Fail closed**: a supplied-but-invalid credential of either kind must 401, never silently fall through. `_require_user()` rewritten to resolve from the verified JWT instead of the cookie session.
- **D30** — `static/app.html` rewrite: vendor `supabase-js` under `static/vendor/` (not a CDN tag — first third-party frontend dep, don't let a CDN outage block login). Replace hand-rolled `/auth/*` fetches with `supabase.auth.signUp/signInWithPassword/signInWithOAuth('azure')/resetPasswordForEmail`. Every backend call sends `Authorization: Bearer <access_token>` — drop CSRF cookie logic entirely (Bearer is never ambient).
- **D31** — `static/index.html`: same Supabase-session wiring for the account-strip; **remove the "Engine API Key" field** from the default hosted UI (still available behind a `byok_enabled` config flag for self-host).
- **D32** — Delete `service/oidc.py`, `service/passwords.py`, the old `/auth/*` routes, and their cookie/CSRF helpers in `app.py`. `DROP TABLE` migration for the retired tables (documented, not automatic).

### Sprint 7 — Stripe billing (Days 33–36) — not started
- **D33** — `billing_accounts`/`stripe_events` tables. Monthly-quota mechanism: `AccountStore` gets a `quota_period="day"|"month"` mode via `PBICOMPASS_QUOTA_PERIOD` (self-host default stays `"day"`, zero migration; hosted sets `"month"`). Calendar-month reset for v1 (not billing-cycle-anchor — simpler, matches "extend the existing counter" intent; anchor-based reset is a fast-follow). New extra `billing = ["stripe"]`.
- **D34** — New `service/billing.py`: customer/checkout/portal session creation, price-id↔plan mapping via env, webhook handlers (`checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`) with `stripe_events`-backed idempotency. Rely on Stripe's own invoice/receipt emails rather than building our own.
- **D35** — `app.py`: `POST /billing/checkout`, `GET /billing/portal` (both JWT-authenticated), `POST /billing/webhook` (signature-verified, no auth dependency — Stripe calls it directly). `limit_for(plan)` honors `accounts.quota_override`.
- **D36** — Plan-picker UI on `/app` (Starter/Growth cards + "Manage billing" → portal). New `tests/test_billing.py` with hand-signed Stripe-style webhook fixtures (upgrade/downgrade/payment-failure/duplicate-event/bad-signature).

### Sprint 8 — Admin app rebuild (Days 37–39) — not started
- **D37** — New `service/admin_users.py`: `is_admin`/`grant_admin`/`revoke_admin`, plus `bootstrap_admin_from_env()` (reads `PBICOMPASS_BOOTSTRAP_ADMIN_EMAIL`, resolves the Supabase user id via the Admin API, grants admin idempotently — this is how you become your own first admin). Keep `admin.py`'s `AdminGuard`/token check alive as an explicit break-glass fallback (`_require_admin` = valid admin JWT OR valid break-glass token).
- **D38** — New routes: `GET /admin/api/me`, `GET /admin/api/users?q=` (search), `GET /admin/api/users/{id}` (detail: plan/usage/jobs/billing status), `POST /admin/api/users/{id}/plan` (manual override), `GET /admin/api/jobs?tenant=` (needs a new `JobStore.list_all()` alongside the existing `list_for_tenant`), `GET /admin/api/revenue`. Existing `/admin/api/accounts` CRUD stays as-is for manually-provisioned API-key-only tenants.
- **D39** — `static/admin.html` rewrite: Supabase sign-in (share widget code with `app.html`), user search/detail/override, cross-tenant jobs table, revenue tile, break-glass token entry kept as a secondary path.

### Sprint 9 — Harden, docs, launch (Days 40–42) — not started
- **D40** — Full regression pass; manual smoke against Supabase test project + Stripe test-mode keys: signup → login → checkout → upload → download; admin login → search → override; webhook replay for idempotency.
- **D41** — `docs/DEPLOYMENT.md` rewrite: new env-var table, self-host-without-Supabase/Stripe fallback section explicitly documented, backup/restore drill updated, launch checklist updated (live Stripe keys + one real purchase/refund; **Supabase custom SMTP configured** — its free-tier built-in sender is too low-volume for real signup traffic, reuse the existing `PBICOMPASS_SMTP_*` credentials). Update `PRODUCTION_ROADMAP.md`'s old Sprint 6 section with a pointer to this plan.
- **D42** — `docs/planning/ROADMAP_PROGRESS.md` entries for Days 26–41 (keeps the multi-tool handoff record intact).

---

## Test strategy

| File | Action | Status |
|---|---|---|
| `tests/test_user_auth.py` | delete (password/session system removed) | ✅ Done |
| `tests/test_email_auth.py` | trim → `tests/test_email.py`: keep backend-mechanics tests, drop the `/auth/*` endpoint tests | ✅ Done |
| `tests/test_oidc.py` | delete | ✅ Done |
| `tests/test_dashboard.py` | rewrite: same API-key CRUD coverage, auth fixture moves to a mocked-JWKS Bearer JWT | ✅ Done |
| `tests/test_session_upload_security.py` | rewrite → `tests/test_supabase_upload_security.py`: drop CSRF/fixation cases (moot once auth is Bearer-JWT, no ambient credential); **keep tenant-isolation assertions verbatim**; add expired/tampered/wrong-aud JWT rejection | ✅ Done |
| `tests/test_auth.py` | keep, extend — this is the guardrail proving the Bearer-API-key path stays byte-identical | ✅ Kept unmodified, still green; month-mode quota test deferred to Sprint 7 (billing) since quota_period doesn't exist yet |
| `tests/test_admin.py` | rewrite for the `is_admin` + break-glass dual gate and the new routes | Not started (Sprint 8) |
| `tests/test_supabase_auth.py` | new | ✅ Done (23 tests) |
| `tests/test_billing.py` | new | Not started (Sprint 7) |

Follow the existing `_HAVE_SERVICE`-style optional-import guard so new suites skip cleanly without the `auth`/`billing` extras installed.

---

## What must NOT change

- Zero-retention job pipeline (`sandbox.py`, `worker.py`, `jobs.py`'s `Job` shape) — this plan only ever touches account metadata.
- The deterministic/AI document-generation engine (`agents/`, `render/`) — out of scope, untouched.
- The Bearer-API-key programmatic path — `AccountStore.verify()`, the `pbicompass_sk_...` check, `/jobs*` for API-key callers. Keep the existing `test_api_key_path_is_completely_unchanged`-style test running unmodified through every sprint.
- `PRODUCTION_ROADMAP.md` §8 (Enterprise: orgs/teams/RBAC/seats/SSO/SCIM/audit log) — not touched by this plan.

---

## Flagged risks / assumptions (proceed unless you say otherwise)

1. **No real pre-launch users to migrate.** Since no payment flow has ever existed, assuming there's no real user base whose passwords need transferring to Supabase — a clean cutover (no dual-mode auth flag) rather than a migration script. Flag if this is wrong.
2. Supabase's free-tier email sender is low-volume — plan configures custom SMTP before launch (D41), reusing the SMTP settings already in `.env.example`.
3. Calendar-month quota reset (not exact billing-cycle-anchor) for v1 — simpler, matches "extend the daily counter" pattern; anchor-based reset flagged as a fast-follow.
4. Break-glass admin token is kept alongside the new Supabase-based admin login (cheap insurance against a Supabase outage) rather than removed outright.
5. Admin user-search (`GET /admin/api/users?q=`) does a SQL join against `auth.users`, which needs `PBICOMPASS_DB` pointed at the *same* Postgres instance Supabase provisions for the hosted deployment — self-host installs using Supabase Auth without Supabase's Postgres would need a slower Admin-API-based search fallback instead.
6. Actual tier names/pricing (Starter/Growth $ amounts) are left as placeholders — set later via Stripe dashboard + env vars, not a code decision.

---

## Verification

- Full `pytest` suite green (including the new/rewritten auth, billing, admin suites) at the end of each sprint. **Sprint 6: 606 passed, 2 skipped, only the 2 pre-existing unrelated `test_render.py` failures remain.**
- Manual end-to-end smoke against Supabase test project + Stripe test-mode keys: signup → verify → login → pick a paid tier → checkout → upload with no API key entered → poll → download; confirm the job used the server-side provider key, not a user-supplied one. **(Sprint 6 portion done via a locally-generated-keypair mocked JWKS — no live Supabase project in this sandbox; a real-project smoke test is still owed, same class of gap as Day 23's Entra ID flow.)**
- Confirm the existing API-key-only flow (`test_service.py`, `test_auth.py`) is unaffected — run it before and after each sprint's changes. **Confirmed for Sprint 6.**
- Admin: bootstrap yourself as admin, log into `/admin`, search a test user, override their plan, browse their job, confirm the break-glass token still works as a fallback. (Sprint 8, not started.)
- Zero-retention regression test still passes unchanged after every sprint (no report content ever touches the new tables/logs). **Confirmed for Sprint 6.**
