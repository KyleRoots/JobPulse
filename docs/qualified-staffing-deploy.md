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

Indeed native Plan B is enabled with `INDEED_TEARSHEET_PUBLISH_ENABLED=true` and
Qualified `BH_UI_*` (tearsheet **2**, private label `51284`). When on, the Indeed
XML upload is empty so CFC and XML do not dual-list. Campaign tags map from
`correlatedCustomText1` (see go-live step 7). Myticas `#INDShow` / tearsheet 1640
are unchanged.

## Confirmed picklists (19 Sep 2026)

Pulled from the live Qualified corp. Stored value and label are the same string. Scout already sends these exact values, so no rename is required before login:

| Scout writes | Qualified dropdown | Result |
|---|---|---|
| `LinkedIn Job Board` | Candidate source | Present. Do not use the shorter `LinkedIn` option. |
| `Indeed Job Board` | Candidate source | Present |
| `ZipRecruiter Job Board` | Candidate source | Present |
| `Corporate Website` | Candidate source | Present. The career portal (`jobs.q-staffing.com`) also uses this when source is blank, and does not overwrite a source already set. |
| `Online Applicant` | Candidate status | Present. This is what new email/apply candidates are set to. |
| `New Lead` | Candidate status | Present |

Job statuses that take a job off the feed already match Scout’s shared
`INELIGIBLE_STATUSES` (including Closed, Filled, Lost variants, Hold, Qualifying,
Archive, etc.). `Accepting Candidates` and `Accepting Candidates - Interviewing`
stay on the feed. The 5-minute incremental monitor auto-removes ineligible jobs
from Qualified tearsheets **2** (Indeed), **3** (ZipRecruiter), and **4**
(LinkedIn) so Bullhorn membership counts track the XML. Auto-remove from
tearsheet **2** also triggers Indeed CFC unpublish so checkmarks clear. The
public flag is separate from status. The portal only publishes jobs that are
open, not deleted, and marked public.

`Dice` is not a Qualified source. An unmatched source falls back to `Other`, which is on the list.

## Bullhorn auth (portal-style redirect omit)

Qualified Scout uses the same OAuth client / API user as the career portal
(`qualifiedstaffing2.api`). On `SCOUT_TENANT=qualified_staffing`, authorize and
token exchange **omit** `redirect_uri` (same pattern as
`q-staffing-portal` / `jobs.q-staffing.com`). That lets REST login work before
Bullhorn Support finishes whitelisting
`https://qualified.scoutgenius.ai/bullhorn/oauth/callback`.

- Override: `BULLHORN_OMIT_REDIRECT_URI=true|false` on Railway.
- Myticas / STSI are unchanged (still send the whitelisted callback).
- Sharing one API user with the career portal can cause occasional session
  401s; both sides re-auth. Prefer a dedicated Scout API user later if noise
  rises.
- Keep Sharon’s whitelist case moving; once confirmed, either path works.

## Go-live order

1. Qualified creates `apply@q-staffing.com` and Graph access.
2. Add Bullhorn + `SESSION_SECRET` (+ other secrets) on `JobPulse-Qualified` in Railway.
3. Confirm Postgres-Qualified is healthy; redeploy app.
4. Headless Bullhorn connection test (portal-style auth); confirm `restUrl` ends in `/clp2rd/`.
5. Tearsheet IDs are staged (LinkedIn 4, Indeed 2, ZipRecruiter 3). Enable SFTP + automated XML uploads after a successful login.
6. Enable mailbox pull when inbound applies should write to this corp.
   LinkedIn / Zip / apply-form intake mirrors the job’s Internal Department
   (`correlatedCustomText1`) onto the candidate (`customText3`) for new and
   returning applicants.
7. Indeed checkbox automation: tearsheet **2**, private label `51284`,
   `ADDCHANGE`/`REPUBLISH`. Enable with `INDEED_TEARSHEET_PUBLISH_ENABLED=true`
   after `BH_UI_*` are set. When enabled, the Indeed XML file is uploaded empty
   so CFC Publish and XML do not dual-list the same jobs (LinkedIn/Zip XML stay).
   Campaign groups come from `correlatedCustomText1` (department → `#INDMary`,
   `#IND-WH` for Appleton, `#INDWin` for Internal, `#INDLiv` for Southfield, etc.).
   `#INDShow` is stripped on republish. Blank/unmapped department skips that job.
8. Unpublish UAT: remove from tearsheet **2**, or set status to Closed / any
   `INELIGIBLE_STATUSES` value (auto-remove then CFC unpublish). Confirm Indeed
   checkmarks clear. Accepting-candidates statuses stay published.
9. Optional: Bullhorn Support redirect whitelist (still useful; no longer a hard blocker for REST).

## Isolation rules

- Never copy Myticas `BULLHORN_*` into Qualified (or vice versa).
- Never point `JobPulse-Qualified` at the Myticas `Postgres` service.
- Never share one API username/password across Myticas Scout and Qualified Scout (lockout blast radius).

## Portal branding (tenant-aware UI)

On `SCOUT_TENANT=qualified_staffing`, admin sidebar company, Scheduler / Inbound
Config feed filenames and labels, Automation Hub Indeed tearsheet id + Sales Rep
field copy (`customText7`), and the Email Parsing inbound tip URL all resolve from
tenant helpers (not Myticas/STSI hardcodes). A redeploy runs seed so the admin
`User.company` and default `BullhornEnvironment` display names self-heal to
**Qualified Staffing**.
