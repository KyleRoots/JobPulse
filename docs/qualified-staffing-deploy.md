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

## Duplicate merge

Qualified does not auto-merge or auto-archive candidates. The hourly job and the manual bulk scan both no-op on this service. Set `DUPLICATE_MERGE_ENABLED=true` only after a later decision to turn it back on. Myticas/STSI keep the hourly merge.

## Application snapshot notes

Qualified recruiters asked to see the applied job on **Notes / Overview**, not only under More → Pipeline → Response.

On this service, Scout writes one **Application Received** note when it knows the job:

- Mailbox inbound (Zip, LinkedIn, Indeed email, Scout apply form)
- Native Indeed Apply follow-up (same 5-minute enrich cycle)
- A Qualified refresh on that same cycle, for existing notes from Scout board sources (Zip, LinkedIn, Indeed, Dice, and apply-form board tags). Notes missing job links or missing the résumé summary are updated; leftover **AI Resume Summary** notes are removed after the combined note is written. Career-site **Corporate Website** applies are not rewritten. Extra Application Received notes for the **same job and same apply time** (Scout duplicates, not a new apply) are soft-deleted across career-site and board sources. A later apply to the same job, or an apply to a different job, keeps its own note. The note with the résumé summary is kept when collapsing true duplicates. Scout apply-form and Pando notes are written at ingest, not by this scan.

The note names the job, company, recruiter, time, and source first (for example Indeed Applicant vs ZipRecruiter Applicant vs Web Applicant). The AI résumé summary is included under that, not as a second note. The job title and company names link to those records in Bullhorn. The recruiter name is shown in bold only. Novo has no OpenWindow page for an internal user, so that name is not a hyperlink.

Career-site / staffing-portal applies that never reach Scout still need the same note written in that project. Do not have the portal rewrite Zip/LinkedIn/Indeed mailbox notes. Qualified Zip Apply webhook deliveries that create a candidate go through the same note path as mailbox apply.

## ZipRecruiter Apply webhook

Live URL: `POST https://qualified.scoutgenius.ai/api/ziprecruiter/apply`

Content-Type must be `application/json`. On Qualified, the JSON is adapted into the same intake path as mail to `apply@q-staffing.com`. A résumé file is uploaded and parsed into the candidate Resume field. When the applicant did not attach a file, the Zip profile (work history and summary) is parsed the same way. The Zip `job_id` is matched to the current feed reference, then to a retired reference kept when that number was replaced. The resolved Bullhorn job id is passed straight into the submission, including ids shorter than 4 digits. A real Zip or Indeed email still reads only a 4- to 6-digit id from the subject. Posted fields whose names start with `_scout_` are removed on `/api/email/inbound` before processing, so only this Zip ingest path can set the job id. If the reference does not map, the person stays unmatched. Admins review that list at `/scout-inbound/zip-unmatched`. More than 10 unmatched, or more than 100 deliveries, in one hour emails the health-alert address. A job taken off the feed keeps those numbers, so a late application can still attach while the Bullhorn job exists. A `200` means the person was written, or this `response_id` already landed. A `503` means processing failed and Zip should send the same body again. The receipt row stores ids and field presence only, not the résumé. Deliveries stored before this write path cannot be replayed. The XML feed still publishes a new reference on the existing refresh. Myticas/STSI do not create candidates from this route.

Zip notification emails to `apply@q-staffing.com` are still parsed. After a live webhook applicant lands on the right job, set `ZIP_EMAIL_INTAKE_ENABLED=false` on JobPulse-Qualified to ignore those Zip emails. Indeed, LinkedIn, and other mailbox applies are unchanged. Leave the variable unset until then. The webhook payload is marked so this switch does not drop it.

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
Qualified `BH_UI_*` (tearsheet **2**, private label `51284`). Publish payloads use
`BH_CAREER_PORTAL_JOB_URL_TEMPLATE` or the default
`https://jobs.q-staffing.com/jobs/{job_id}` (ID-only path; portal redirects to the
SEO slug). Publishing goes through Universal Login and Bullhorn's
`services/JobPublish/careerPortal` and `/indeed` REST calls (the old `cls45`
JobBoard CFC is retired). Publish Job's Indeed Publication Status is a separate
read-only `GET services/JobPublish/boards?jobPostingID={id}`
(`BullhornUIClient.get_publication_status()`). The Indeed account's
`isPublished` flag is the live status. `jobBoardList` and `isJobcastPublished`
are not a substitute.

Qualified has no free Indeed posting. `INDEED_LEAK_ENFORCEMENT_MODE` defaults
to `report`. The hourly leak sweep, the daily 3:00 AM ET closed-job sweep
(`indeed_leak_deep_sweep`: non-deleted `isOpen=false` jobs closed or last
modified within 180 days, not on tearsheet 2), and the Monday 8:00 AM ET
digest (`indeed_leak_sweep`, `indeed_leak_digest`, first digest November 2,
2026) use that status call. JobPublish has no list of every Indeed-published
job, so the closed-job query is the sweep's source. `enforce` emails the assigned recruiters and, after
`INDEED_LEAK_GRACE_HOURS` (default 24), DELETEs Indeed only. Closed, Hold,
Filled, and Canceled jobs are removed immediately. Exempt ids and departments
are `INDEED_LEAK_EXEMPT_JOB_IDS` and `INDEED_LEAK_EXEMPT_DEPARTMENTS`. A broken
tearsheet read, an empty tearsheet, a membership drop of more than half, or
status-call failures above 20% stop the run and email
`INDEED_LEAK_NOTIFY_EMAIL` (default `kroots@myticas.com`). Detections persist
in `indeed_leak_state`. The sweep, the deep sweep, the Monday digest, and the
fast path take that state document one at a time. The fast path skips while a
sweep holds the lock. Its lookback is 20 minutes, which covers a sweep of
about 6 minutes plus the 5-minute cycle that was skipped. The 180-day dry run
checked 2,209 jobs in 322 seconds. A sweep waits up to two minutes, then skips
without saving, including when an old and a new container overlap during a
deploy (Postgres advisory lock). The 3:00 AM closed-job sweep retries every
10 minutes until 6:00 AM ET if the lock is still held, then emails ops once
that it did not run that day. The Monday 8:00 AM digest retries every 10
minutes until 12:00 PM ET, then emails ops once that the digest did not go out. Leak-checker REST
calls send `BhRestToken` as a
header. Exception text saved, emailed, or logged from that check is the
exception type and HTTP status, with token and password values redacted.

Tearsheet publish reads membership in pages of 200 and treats the read as
failed when the row count does not match `total` or the call errors. A failed
read does not unpublish and does not replace the saved membership. A job is
removed only after two consecutive successful reads miss it, and the job's
`JobOrder/{id}/tearsheets` association still does not include this tearsheet.
Bullhorn rejects `JobOrder/{id}/tearsheets/{tearsheet}` as a bad command
(HTTP 400) for members and non-members, so a 404 counts as absent only when
the body shows the association is empty. Any other 404 skips the DELETE. One read
that drops more than 10% of the previous membership (at least 5 jobs) skips
every removal that cycle and emails ops at most once a day. Additions and
republishes still run. Once a day, tearsheet 2 jobs whose Publish Job status
shows Indeed not published, and that have a description and a response user,
are sent `ADDCHANGE` again. One resend per job per day. Jobs still not
published two days later are reported to ops; those are for Bullhorn support
(the Bullhorn-to-Indeed connection), not another Scout delete. Publish errors
keep a reason only when Scout wrote it, such as an empty description, a missing
published contact, a campaign-tag reason, or a REST auth failure. A Bullhorn
response body, or any other message, is reduced to the exception type and HTTP
status. URL and token redaction still applies to a kept reason.

An Indeed Job Board application on a job that is not on tearsheet 2, more than
48 hours after Bullhorn last showed Indeed as published (or when Bullhorn has
never shown it published), is a mismatch: Indeed is still delivering
applicants and Bullhorn says it is not published. Report mode records it.
Enforce mode re-sends the Indeed-only DELETE at most once a day. The Monday
digest has two extra sections: "Live on Indeed but Bullhorn says unpublished"
(unpublish time and application times, for a Bullhorn ticket) and "On
Sponsored - Indeed but not live on Indeed" (tearsheet members whose status is
not Indeed-live, with the reason). Grace notices and per-recruiter digests BCC
`INDEED_LEAK_BCC_EMAIL` (default `kroots@myticas.com`). An empty value disables
the BCC. One grace notice is addressed to every recipient on that job, and the
BCC is omitted when that address is already in To. The delivery log stores the
BCC in `changes_summary`.

When native Indeed publish is on, the Indeed XML upload is empty so native
publish and Scout XML do not dual-list. Campaign tags map from `correlatedCustomText1` (see go-live step 7).
Qualified published categories follow the job title (Warehouse, Manufacturing,
Customer Service, Logistics, Food Services, and similar). Unmatched Qualified
jobs fall back to Manufacturing, not IT/Software Development. Myticas `#INDShow`
/ tearsheet 1640 still fall back to IT/Software Development.

## Confirmed picklists (19 Sep 2026)

Pulled from the live Qualified corp. Stored value and label are the same string. Scout already sends these exact values, so no rename is required before login:

| Scout writes | Qualified dropdown | Result |
|---|---|---|
| `LinkedIn Job Board` | Candidate source | Present. Do not use the shorter `LinkedIn` option. |
| `Indeed Job Board` | Candidate source | Present |
| `ZipRecruiter Job Board` | Candidate source | Present |
| `Corporate Website` | Candidate source | Present. The career portal (`jobs.q-staffing.com`) also uses this when source is blank, and does not overwrite a source already set. |
| `Online Applicant` | Candidate status | Present on the picklist. Myticas/STSI Scout ingest still uses this. On Qualified, board/email ingest uses **New Lead** instead (aligned with the career portal). |
| `New Lead` | Candidate status | Present. Qualified: all new candidates (portal + Zip/Indeed/LinkedIn board ingest). |

Job statuses that take a job off the feed already match Scout’s shared
`INELIGIBLE_STATUSES` (including Closed, Filled, Lost variants, Hold, Qualifying,
Archive, etc.). `Accepting Candidates` and `Accepting Candidates - Interviewing`
stay on the feed. The 5-minute incremental monitor auto-removes ineligible jobs
from Qualified tearsheets **2** (Indeed), **3** (ZipRecruiter), and **4**
(LinkedIn) so Bullhorn membership counts track the XML. Auto-remove from
tearsheet **2** also triggers Indeed native unpublish so checkmarks clear. The
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
   so native publish and XML do not dual-list the same jobs (LinkedIn/Zip XML stay).
   Campaign groups come from `correlatedCustomText1` (department → `#INDMary`,
   `#INDWin` for Internal, `#INDLiv` for Southfield, etc.). Appleton and
   Appleton (WI) are the exception: the description is published as saved, and
   Scout does not add or strip a hashtag. `#INDShow` is stripped on republish
   for every other department. Blank/unmapped department skips that job.
8. Unpublish UAT: remove from tearsheet **2**, or set status to Closed / any
   `INELIGIBLE_STATUSES` value (auto-remove then native unpublish). Confirm Indeed
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
