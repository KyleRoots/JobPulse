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
| Graph / Entra vars | Same pattern as Myticas, scoped to the Qualified mailbox tenant |

After first successful seed, live Bullhorn auth is stored in **this service’s** `global_settings` DB (not Railway env alone). Update via ATS Integration Settings → Save if rotating.

## Screening

Use the **`light_industrial`** screening profile on the Qualified `BullhornEnvironment` (Admin onboarding or environment settings). Do not leave it on the Myticas `standard` profile.

## Feeds / Indeed / tearsheets

Hold until Qualified provides tearsheet IDs and names. Then map:

- LinkedIn / Indeed / Zip XML tearsheet IDs in feed config (Qualified-only service copy or env-gated config)
- Indeed native publish tearsheet + `BH_UI_*` for **this** corp only
- SFTP host/path if Qualified uses a different WP Engine (or other) destination

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
