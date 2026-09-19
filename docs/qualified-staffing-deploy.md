# Qualified Staffing — Scout Genius deploy

Separate Bullhorn corporation (light-industrial staffing) runs as a **second Railway service** in the `scout-genius` project. It does **not** share the Myticas/STSI Bullhorn corp, database, or apply mailbox.

## Why a second service

Myticas and STSI are Brands on one Bullhorn instance. Qualified Staffing is a different ATS (different candidate/job IDs, OAuth app, and API user). Putting it in the Myticas process would risk writing applicants into the wrong corp until full multi-env inbound/mailbox/feeds are finished.

## Railway resources (production)

| Resource | Name | Notes |
|---|---|---|
| App service | `JobPulse-Qualified` | Same GitHub repo `KyleRoots/JobPulse` @ `main` |
| Database | `Postgres-Qualified` | Dedicated Postgres; do **not** point at Myticas `Postgres` |
| Project | `scout-genius` | Shared Railway project, isolated services |

## Mailbox (inbound)

- Target apply address: **`apply@q-staffing.com`** (confirm with Qualified IT; Graph UPN must match).
- Set `GRAPH_MAILBOX_UPN` on `JobPulse-Qualified` once Entra / Graph access for that mailbox is ready.
- **Do not** route Qualified LinkedIn/Zip/Indeed Easy Apply into `apply@myticas.com`. One mailbox cannot safely split applicants across two Bullhorn corps with today’s inbound path (routing and Bullhorn writes are still keyed to the default/Myticas client in a shared process). Worst case is wrong-ATS writes or dropped routing, not “good enough shared inbox.”

## Env vars checklist

### Already scaffolded on `JobPulse-Qualified`

| Variable | Intent |
|---|---|
| `APP_ENV` | `production` |
| `DATABASE_URL` | `${{Postgres-Qualified.DATABASE_URL}}` |
| `GRAPH_MAILBOX_UPN` | `apply@q-staffing.com` (until mailbox exists) |
| `OAUTH_REDIRECT_BASE_URL` | Placeholder `https://qualified.scoutgenius.ai` — update when domain is live |
| `BULLHORN_USE_NEW_API` | `true` (revisit if login discovery differs) |
| `SCOUT_TENANT` / `SCOUT_TENANT_DISPLAY_NAME` | Ops labels (`qualified_staffing` / `Qualified Staffing`) |
| `SCREENING_PROFILE` | Ops reminder: `light_industrial` (set live profile via Admin / onboarding after first boot) |

### You must add (do not put in git or chat)

| Variable | Notes |
|---|---|
| `BULLHORN_CLIENT_ID` | Qualified OAuth app |
| `BULLHORN_CLIENT_SECRET` | Qualified OAuth app |
| `BULLHORN_USERNAME` | Qualified API user (e.g. `qualifiedstaffing2.api`) |
| `BULLHORN_PASSWORD` | Qualified API password |
| `SESSION_SECRET` | Required in production (≥16 chars); generate uniquely for this service |
| `ADMIN_PASSWORD` | Initial admin login (12+ chars) |
| `OPENAI_API_KEY` | Can reuse org key or a dedicated key |
| `SENDGRID_API_KEY` | Notifications |
| `MICROSOFT_CLIENT_ID` | Entra **mail** app for Qualified Graph pull (not Support Portal SSO) |
| `MICROSOFT_CLIENT_SECRET` | Secret for that mail app |
| `MICROSOFT_TENANT_ID` | Qualified Microsoft 365 / Entra tenant ID |
| `GRAPH_MAILBOX_UPN` | Must be `apply@q-staffing.com` (already scaffolded; confirm after mailbox exists) |
| `GRAPH_AUTH_MODE` | Set to `entra` on this service |

Do **not** use `SUPPORT_MICROSOFT_*` for mailbox pull. Those are Support Portal SSO only. Pointing Graph at the wrong app yields `403` and stops applicant intake.

After first successful seed, live Bullhorn auth is stored in **this service’s** `global_settings` DB (not Railway env alone). Update via ATS Integration Settings → Save if rotating.

## Screening

Qualified is **not** using Scout Screening yet. The apply template
(`apply_qualified.html`) intentionally **omits** the “we use AI / Scout
Screening” candidate notice. Use the **`light_industrial`** screening profile
on the Qualified `BullhornEnvironment` only when screening is turned on later
(Admin onboarding or environment settings). Do not leave it on the Myticas
`standard` profile if you enable screening.

## Apply landing page branding

- Template: `templates/apply_qualified.html` (light frame, Qualified logo, no AI screening notice).
- Logo assets: `static/images/qualified_staffing_logo.png` and
  `static/qualified-staffing-logo.png`.
- Host routing: `*q-staffing.com*` and `qualified.scoutgenius*` → Qualified
  template (Brand seed when `SCOUT_TENANT=qualified_staffing`, plus hardcoded
  fallback).
- Privacy mailto: `apply@q-staffing.com`.

## Super-admin access (how to tell tenants apart)

Qualified is a **separate Railway service and database**, not a brand switch inside the Myticas Scout UI.

| | Myticas / STSI | Qualified Staffing |
|---|---|---|
| App URL | `https://app.scoutgenius.ai` (or `jobpulse.lyntrix.ai`) | `https://qualified.scoutgenius.ai` |
| Railway service | `JobPulse` | `JobPulse-Qualified` |
| Postgres | Myticas `Postgres` | `Postgres-Qualified` (or linked DB) |
| `SCOUT_TENANT` | unset / not `qualified_staffing` | `qualified_staffing` |
| Admin login | Myticas admin user / password | Qualified `ADMIN_PASSWORD` (separate user table) |
| Bullhorn | Myticas corp | Qualified corp (when OAuth works) |
| Apply pages | `apply.myticas.com` / `apply.stsigroup.com` | `qualified.scoutgenius.ai/...` |

STSI is a **Brand** on the Myticas service (same login, different apply host). Qualified is its own Scout instance: open the Qualified URL and sign in there.

## SFTP / FPT (XML hosting)

WP Engine / host “FPT” credentials are the SFTP (or FTP) account Scout uses to
upload LinkedIn/Indeed XML. On `JobPulse-Qualified` only:

1. Prefer **Admin → Global Settings** keys: `sftp_enabled`, `sftp_hostname`,
   `sftp_username`, `sftp_password`, `sftp_directory`, `sftp_port` (often `2222`
   for WP Engine SFTP).
2. Or Railway env on the Qualified service: `SFTP_HOSTNAME` / `SFTP_HOST`,
   `SFTP_USERNAME`, `SFTP_PASSWORD`, `SFTP_PORT` (never commit these; do not
   paste into chat).

Do **not** copy Myticas SFTP settings onto Qualified (wrong site / wrong feed
paths). Keep XML uploads **OFF** until the `SCOUT_TENANT` feed selector and
Qualified tearsheet IDs are live.

## Feeds / Indeed / tearsheets

**Keep XML automated uploads and Indeed native publish OFF on `JobPulse-Qualified` until the Bullhorn redirect whitelist works and jobs are on these tearsheets.**

`feeds/feed_config.py` is tenant-aware via `SCOUT_TENANT`:

| `SCOUT_TENANT` | Feed pack |
|---|---|
| unset / other | Myticas + STSI (historical default on `JobPulse`) |
| `qualified_staffing` | Qualified filenames/publisher/apply host and the tearsheets below |

Qualified tearsheets (Novo IDs, confirmed 19 Sep 2026, job counts were 0):

| Name | ID | Feed |
|---|---|---|
| Sponsored - LinkedIn | 4 | `qualified-job-feed-v2.xml` |
| Sponsored - Indeed | 2 | `qualified-job-feed-indeed.xml` |
| Sponsored - ZipRecruiter | 3 | `qualified-job-feed-ziprecruiter.xml` |

Indeed native Plan B stays forced off on this tenant. Do not enable SFTP or automated uploads until the redirect URI whitelist succeeds and these tearsheets actually contain jobs. An upload before that would have nothing to publish.

## Go-live order

1. Qualified creates `apply@q-staffing.com` and Graph access.
2. Add Bullhorn + `SESSION_SECRET` (+ other secrets) on `JobPulse-Qualified` in Railway.
3. Confirm Postgres-Qualified is healthy; redeploy app.
4. Headless Bullhorn connection test; set `light_industrial` profile.
5. Map tearsheet IDs; enable mailbox pull; enable Indeed/XML only after IDs exist.
6. Optional later: public domain (`qualified.scoutgenius.ai` / `apply.q-staffing.com`) and OAuth redirect whitelist.

## Isolation rules

- Never copy Myticas `BULLHORN_*` into Qualified (or vice versa).
- Never point `JobPulse-Qualified` at the Myticas `Postgres` service.
- Never share one API username/password across Myticas Scout and Qualified Scout (lockout blast radius).
