# XML Job Feed Reference Number Updater

## 📋 Overview

A comprehensive Flask-based web application that automates XML job feed processing, reference number management, and synchronization with Bullhorn ATS/CRM. The system ensures accurate job listings, maintains real-time synchronization, and streamlines application workflows to enhance job visibility and operational efficiency.

### Primary Capabilities
- **Automated XML Job Feed Updates**: 30-minute cycle with SFTP uploads
- **Database-Backed Reference Number Preservation**: Persistent reference numbers across all cycles
- **Bullhorn ATS Integration**: Real-time job data synchronization from multiple tearsheets
- **Production Monitoring**: Health checks with email alerts to kroots@myticas.com
- **Job Application Forms**: Public-facing responsive forms with resume parsing
- **Intelligent Job Classification**: Keyword-based categorization system

---

## 🏗️ System Architecture

### Backend Stack
- **Web Framework**: Flask (Python 3.x)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Authentication**: Flask-Login for secure user sessions
- **Background Processing**: APScheduler for automated tasks
- **XML Processing**: lxml library with CDATA formatting
- **Email Service**: SendGrid for notifications
- **SFTP Service**: Built-in secure file transfer
- **ATS Integration**: Bullhorn API with tearsheet monitoring

### Frontend Stack
- **Template Engine**: Jinja2
- **UI Framework**: Bootstrap 5 (dark theme)
- **Icons**: Font Awesome 6.0
- **Client-side**: Vanilla JavaScript for interactive features

### Infrastructure
- **Session Management**: Flask sessions with secure keys
- **Proxy Support**: ProxyFix middleware for HTTPS
- **File Handling**: Secure temporary storage with auto-cleanup
- **Production Hosting**: Railway (`scout-genius` project, JobPulse service) — deploys from `main`
- **Dual-Domain Setup**:
  - `app.scoutgenius.ai` — main Scout Genius app
  - `apply.myticas.com` — Myticas job applications
  - `apply.stsigroup.com` — STSI job applications (web form only; email intake + privacy mailto is `apply@myticas.com` — `apply@stsigroup.com` is not provisioned)

---

## 🚀 Core Features

### 1. Automated Upload System (30-Minute Cycle) - Toggle-Based
- **Scheduler-Backed Automation**: APScheduler runs upload cycle every 30 minutes when enabled
- **Settings Control**: Requires BOTH `automated_uploads_enabled=true` AND `sftp_enabled=true`
- **Fresh XML Generation**: Pulls from Bullhorn tearsheets (see `feeds/feed_config.py` and `tearsheet_config.py`)
- **Tri-Feed STSI Output** (July 2026): LinkedIn (`myticas-job-feed-v2.xml`), Indeed (`stsi-job-feed-indeed.xml`), ZipRecruiter (`stsi-job-feed-ziprecruiter.xml`) — non-prod uses `-dev` suffix
- **Reference Number Preservation**: Database-backed persistence ensures no reversion
- **SFTP Upload**: Secure automated uploads to production server (when automation is enabled)
- **Manual Workflow Alternative**: Can be disabled for manual-only downloads via settings toggle

### 1b. Scout Screening (AI Candidate Vetting)
- **Automated vetting cycle**: Scores inbound applicants against open jobs; recruiters make final decisions
- **Cheap-first routing**: `screening_routing_mode` (`off` | `canary` | `enforce`) — Enforce skips GPT-5.4 on clear rejects
- **Compliance (Phase A, July 2026)**: Apply-form AI notices, rules version stamping (`screening/compliance.py`), guardrailed global prompt, compliance metrics endpoint. Privacy mailto on Myticas and STSI apply forms is `apply@myticas.com` (shared EXO intake; not `apply@stsigroup.com`)
- **Rules changelog**: `config/SCREENING_RULES_CHANGELOG.md`

### 2. Database-Backed Reference Number System ✨ NEW
**Problem Solved (October 2025)**: Live XML URL returns 403 Forbidden, causing reference numbers to revert to old values.

**Solution Architecture**:
- `JobReferenceNumber` database table stores all reference numbers persistently
- Manual refresh saves reference numbers to database after generation
- 30-minute automated upload reads from database instead of protected URL
- Result: Reference numbers remain consistent across all cycles

**How It Works**:
1. **Manual Refresh**: Generates fresh XML → Applies reference numbers → Saves to database → Uploads to production
2. **Automated Upload (when enabled)**: Generates fresh XML → Loads references from database → Applies to XML → Saves to database → Uploads to production
3. **Fallback**: If database read fails, attempts to read from published XML file
4. **Result**: Reference numbers persist across all cycles with no reversion

**Database Schema**:
- Table: `JobReferenceNumber`
- Fields: job_id (unique), reference_number, last_updated
- Purpose: Persistent storage of all job reference numbers

### 3. Bullhorn ATS Integration
- **Fresh Data Generation**: Pulls from tearsheets 1256, 1264, 1499, 1556 on-demand
- **Multi-Tearsheet Support**: Comprehensive job data from all configured sources
- **HTML Parsing**: Proper tag closure with lxml for description fields
- **CDATA Wrapping**: All XML fields properly wrapped for data integrity
- **Company Name Formatting**: "STSI (Staffing Technical Services Inc.)" for tearsheet 1556
- **Real-time Processing**: Generates XML when manual refresh or automated cycle executes

### 4. Production Environment Monitoring
- **Health Check Endpoints**: `/health`, `/ready`, `/alive`, `/ping`
- **Email Notifications**: Automated alerts to kroots@myticas.com for:
  - Production environment downtime
  - Recovery notifications
  - Upload status updates
- **Optimized Monitoring**: Health checks every 2 hours (optimized for manual workflow)
- **Scheduler Auto-Restart**: Automatic recovery with timeout protection

### 5. Job Application System
- **Resume Parsing**: Extracts contact info from Word/PDF formats
- **Auto-Population**: Candidate fields automatically filled from resume
- **Bullhorn Integration**: Direct job ID integration
- **Responsive Design**: Mobile-optimized application forms
- **Unique Branding**: Customizable for client-specific needs

### 6. Internal Job Classification
- **Keyword-Based System**: Instant categorization without external APIs
- **Classification Fields**: jobfunction, jobindustries, senioritylevel
- **Reliable & Fast**: No API dependencies or rate limits

### 7. Intelligent File Management
- **Automated Consolidation**: Merges and optimizes XML files
- **Duplicate Detection**: Prevents job pollution with orphan prevention
- **Temporary File Cleanup**: Automatic storage optimization
- **Secure File Handling**: Validated uploads with size constraints (max 50MB)

---

## 📊 Database Schema

### Core Models

#### JobReferenceNumber (NEW - October 2025)
```python
class JobReferenceNumber(db.Model):
    id = Integer (Primary Key)
    job_id = String(255) (Unique, Indexed)
    reference_number = String(50)
    last_updated = DateTime
```
Stores reference numbers persistently to prevent reversion issues.

#### GlobalSettings
Stores system configuration:
- SFTP credentials (hostname, username, password, directory, port)
- Email settings (notifications enabled, default email)
- Automation settings (uploads enabled/disabled)

#### User
Flask-Login authentication model:
- User credentials and session management

#### ActivityLog
Tracks all system activities:
- Upload events, refresh operations, errors
- Timestamp tracking for audit trail

#### UploadSchedule
Manages scheduled upload configurations

---

## 🔧 Technical Implementation

### XML Processing Engine
- **Root Element**: Requires 'source' element
- **Required Fields**: title, company, date, referencenumber
- **CDATA Formatting**: All fields wrapped for proper data handling
- **HTML Consistency**: lxml parser ensures proper tag closure
- **Reference Preservation**: Database-backed lookup system

### Reference Number Generation
```python
# Manual Refresh Flow:
1. Generate fresh XML from Bullhorn
2. Apply reference number refresh
3. Save to JobReferenceNumber table
4. Upload to production

# Automated Upload Flow (30 min):
1. Generate fresh XML from Bullhorn
2. Load reference numbers from database
3. Apply to XML content
4. Upload to production
```

### Upload Workflow
- **File Upload Constraints**: XML only, max 50MB
- **Temporary Storage**: Secure filename handling with auto-cleanup
- **SFTP Protocol**: Thread-safe uploads to production server
- **Environment Detection**: Auto-detects dev/production for correct filename

### Error Handling
- **XML Syntax Validation**: Comprehensive error catching
- **User-Friendly Messages**: Non-technical error reporting
- **Server-Side Logging**: Detailed debug information
- **Client-Side Validation**: Real-time form validation

---

## 📦 Installation & Setup

### Prerequisites
- Python 3.x
- PostgreSQL database
- SFTP server credentials
- SendGrid API key (for email notifications)
- Bullhorn credentials

### Environment Variables

```bash
DATABASE_URL=postgresql://user:pass@host:port/dbname
SESSION_SECRET=your-secret-key
BULLHORN_PASSWORD=your-bullhorn-password
SENDGRID_API_KEY=your-sendgrid-key

# Optional fraud contact validation (Screening Config toggle OFF until smoke-tested)
NEVERBOUNCE_API_KEY=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=

# Indeed native publish (Plan B) — tearsheet 1640 → Bullhorn JobBoard CFC
# Keep DISABLED until UI login + a single test job (e.g. 35233) is verified.
INDEED_TEARSHEET_PUBLISH_ENABLED=false
BH_UI_USERNAME=service.user
BH_UI_PASSWORD=...
BH_UI_BASE_URL=https://cls45.bullhornstaffing.com
BH_UI_PRIVATE_LABEL_ID=52989
BH_UI_ENCRYPTION_KEY=novo
BH_UI_CURRENT_USER_ID=   # optional; auto-resolved when possible
BH_CAREER_PORTAL_JOB_URL_TEMPLATE=https://myticas.com/jobs/{job_id}
INDEED_TEARSHEET_PUBLISH_NOTIFY_EMAIL=kroots@myticas.com

# Scout new AI requirement-spec create notify (sanity-check email; default ON)
# REQUIREMENTS_SPEC_NOTIFY_ENABLED=true
# REQUIREMENTS_SPEC_NOTIFY_EMAIL=kroots@myticas.com

# Native Indeed Apply inbound field remap (New Lead/Indeed/Unassigned →
# Online Applicant/Indeed Job Board/Myticas API User). Default ON.
# Default lookback 0 = full source backlog (no date floor); set >0 to narrow.
# INDEED_INBOUND_REMAP_ENABLED=true
# INDEED_INBOUND_REMAP_LOOKBACK_HOURS=0
```

Optional overrides: `ENVIRONMENT_HEALTH_URL` / `SCOUTGENIUS_PUBLIC_URL` for env-monitor probe URL.

### Python Dependencies
```bash
apscheduler
email-validator
flask
flask-dance
flask-login
flask-sqlalchemy
gunicorn
lxml
oauthlib
openpyxl
pandas
paramiko
psycopg2-binary
pyjwt
pypdf2
python-docx
requests
sendgrid
sqlalchemy
werkzeug
```

### Database Initialization
```bash
# Database tables are created automatically on first run
# JobReferenceNumber table added October 2025
python main.py
```

### Running the Application
```bash
# Development
gunicorn --bind 0.0.0.0:5000 --reuse-port --reload main:app

# The application listens on port 5000
# Access at: http://localhost:5000
```

---

## 🚀 Deployment

### Publishing to Production

#### Step 1: Configure Settings
Navigate to Settings page and configure:
- **SFTP Credentials**: hostname, username, password, directory, port
- **Email Notifications**: Enable notifications, set default email (kroots@myticas.com)
- **Automation Toggle**: Enable/disable automated uploads as needed

#### Step 2: Enable Automated Uploads (Optional)
**Required for 30-minute automation**:
1. ✅ Check "Enable SFTP Uploads" 
2. ✅ Check "Enable Automated Uploads"
3. ✅ Save settings

**Note**: Both toggles must be ON for automated uploads to run. If either is OFF, the system runs in manual-only mode.

#### Step 3: Populate Reference Number Database
1. Click "Refresh All Reference Numbers" button on dashboard
2. Verify success message
3. Confirms JobReferenceNumber table is populated

#### Step 4: Deploy Application
Deploy to Railway production:

```bash
railway up --service JobPulse --environment production
```

Health check: `/health` (configured in `railway.toml`).

#### Step 5: Verify Automation Status
Check dashboard for automation status:
- **"Active"** = Automation running, next upload scheduled
- **"Inactive"** = Manual-only mode (check settings toggles)

### Post-Deployment Verification
- ✅ Check automation status on dashboard (should show "Active")
- ✅ Verify SFTP credentials are working
- ✅ Confirm email notifications are being received
- ✅ Test manual refresh and verify database persistence
- ✅ Monitor next 30-minute cycle for reference preservation

---

## 🔍 Troubleshooting Guide

### Reference Numbers Keep Reverting
**Problem**: Reference numbers change back to old values like "EXZNVDOWMS"

**Solution**: ✅ SOLVED (October 2025)
- Database-backed preservation system prevents reversion
- Manual refresh populates JobReferenceNumber table
- Automated uploads read from database, not protected URL

### Automated Uploads Not Working
**Symptoms**: Dashboard shows "Inactive" status

**Root Cause**: Automation requires BOTH settings to be enabled

**Solutions**:
1. **Verify Settings Toggles**:
   - Go to Settings page
   - ✅ Check "Enable SFTP Uploads" is ON
   - ✅ Check "Enable Automated Uploads" is ON
   - Save settings
   
2. **Verify SFTP Configuration**:
   - Hostname, username, password must be filled
   - Directory path must be valid
   - Port must be correct (default: 2222)

3. **Check Scheduler Status**:
   - Logs should show: "📤 Scheduled automated uploads every 30 minutes"
   - If missing, restart application to reinitialize scheduler

4. **Manual Test**:
   - Click "Refresh All" button to test SFTP connection
   - If successful, automation should work on next cycle

**Note**: The scheduler ALWAYS runs every 30 minutes, but skips execution if settings are disabled. Check logs for: "📋 Automated uploads disabled in settings, skipping upload cycle"

### Email Notifications Not Sending
**Check**:
1. Settings → Email Notifications Enabled = ON
2. Default notification email is set to kroots@myticas.com
3. SendGrid API key is configured
4. Check email service logs for delivery status

### Production Monitoring Alerts
**Email Alerts Sent For**:
- Production environment becomes unreachable
- Production environment recovers
- Automated upload success/failure

**Health Endpoints**:
- `/health` - Overall system health
- `/ready` - Database connectivity check
- `/alive` - Basic application responsiveness
- `/ping` - Ultra-fast availability check

### Database Connection Issues
**Error**: "Database connection failed"

**Solutions**:
1. Verify DATABASE_URL environment variable
2. Check PostgreSQL is running
3. Confirm database credentials are correct
4. Review connection pool settings (300s recycle, pre-ping enabled)

### SFTP Upload Failures
**Common Issues**:
- Incorrect hostname/port (default: 2222)
- Invalid credentials
- Target directory permissions
- Network connectivity

**Debug Steps**:
1. Test SFTP connection manually
2. Verify directory path exists
3. Check application logs for detailed error
4. Confirm SFTP mode is enabled (not FTP)

### XML Generation Errors
**Validation Errors**:
- Missing required fields (title, company, date, referencenumber)
- Invalid root element (must be 'source')
- Unclosed HTML tags → Auto-fixed by lxml parser
- CDATA formatting issues → Auto-wrapped by system

---

## 📈 Recent Major Updates

### August 2026: Main-branch Railway + Render cleanup
- **Deploy source**: Production JobPulse on Railway now tracks `main` (promoted from the long-lived `cursor/railway-entra-graph-auth-b09e` deploy branch).
- **Render removed**: Dropped Render API log monitoring (scheduler job, service, admin UI, and credential-bearing workflow docs). Railway logging/observability unchanged. Legacy `log_monitoring_*` DB tables remain for retention cleanup only.

### August 2026: Divergent résumé versions fraud advisory
- **Problem**: Multi-submission claim drift only compares identity claims across applies — it does not catch clearly different Resume-typed files on one Bullhorn profile (e.g. [Ocean Towne](https://cls45.bullhornstaffing.com/BullhornStaffing/OpenWindow.cfm?Entity=Candidate&id=4309619) ML vs marketing/CRM versions).
- **Fix**: Free / BH-local signal `divergent_resume_versions` in `FraudSignalEngine` — newest ≤5 Resume-typed/named entityFiles, near-identical content collapsed (hash / Jaccard ≥0.90), Review-band soft advisory (40 pts) when min pairwise word-token Jaccard &lt;0.40. Fail-soft on Bullhorn fetch; no OCR/NeverBounce.

### August 2026: Recruiter email attaches newest Bullhorn résumé
- **Problem**: Scout recruiter emails renamed the attachment to `{Name}_Resume.docx` but fetched the **first** entityFiles hit whose type/name contained "resume" (often oldest-first). Screening preferred Candidate.description (already refreshed from the newest file), so emails could attach a stale tailored résumé while the body pitched skills from a newer version ([Ocean Towne](https://cls45.bullhornstaffing.com/BullhornStaffing/OpenWindow.cfm?Entity=Candidate&id=4309619) — marketing `Resume_CV…` attached vs `ML__E.docx` scored).
- **Fix**: `select_newest_resume_file` in `screening/candidate_data.py` — pick newest by `dateAdded` among Resume-typed/named files (then doc extensions). Same helper used by `get_candidate_resume` for attachments and screening file fallback.

### August 2026: Reject job-title-as-name on inbound
- **Problem**: LinkedIn apply-form submissions sometimes put the candidate's occupation in `firstName`/`lastName` (e.g. "Senior" / "Business Analyst"). Inbound preferred that email/subject name over the AI résumé parse, creating Bullhorn records with the title as the candidate name (e.g. #4673968 — real name Uday Vasireddy was on the résumé and in the filename).
- **Fix**: `is_job_title_phrase` in `utils/candidate_name_extraction.py` (wired into `is_valid_name`), Title-Reject Guard in inbound processing (prefer résumé AI name; fall back to filename with title-suffix stripping), and overwrite (not `or`) when recovering over an invalid name.

### August 2026: Indeed inbound field remap + Scout Screening coverage
- **STSI privacy mailto (Aug 7)**: Apply-form AI screening notice on `apply.stsigroup.com` now mailto `apply@myticas.com` (real EXO/Graph intake). `apply@stsigroup.com` was never provisioned and bounced 550 5.4.1.
- **New requirement-spec notify (Aug 5)**: When Scout first creates an AI `JobVettingRequirements` row (not updates/regens) **and the job is on the Scout Screening list** (`BullhornMonitor.last_job_snapshot` — same source as My Matches & Jobs / Job-Level Settings), JobPulse emails `REQUIREMENTS_SPEC_NOTIFY_EMAIL` (default `kroots@myticas.com`) with job title/ID, Bullhorn deep link, and an interpreted-requirements excerpt for sanity-check vs the JD. Specs saved before the job appears in the snapshot are deferred until the next snapshot refresh (idempotent via `spec_create_notified_at`). Fail-soft; toggle with `REQUIREMENTS_SPEC_NOTIFY_ENABLED` (default ON).
- **Closed applied jobs never qualify (Aug 4)**: After Indeed remap backlog bumped `dateLastModified` on old Online Applicants, Scout re-screened candidates against closed applied jobs, marked them Qualified, and emailed assigned recruiters (incident: [Luke Duwel](https://cls45.bullhornstaffing.com/BullhornStaffing/OpenWindow.cfm?Entity=Candidate&id=4657295) × [Procurement Specialist #34990](https://cls45.bullhornstaffing.com/BullhornStaffing/OpenWindow.cfm?Entity=JobOrder&id=34990) Lost - Competition → Christine Carter). Fix: `job_can_qualify()` — closed/ineligible jobs never set `is_qualified`; prior screen of the same closed applied job short-circuits re-processing.
- **Indeed inbound field remap (Aug 4)**: Scheduled `indeed_inbound_remap` (every 5 min, `INDEED_INBOUND_REMAP_ENABLED` default ON) remaps native Indeed Apply candidates from **New Lead / source Indeed / Unassigned User** → **Online Applicant / Indeed Job Board / Myticas API User (1147490)**. Exact `source:Indeed` only (skips `Indeed Resume Search` and already-`Indeed Job Board`). Owner is set to Myticas API User **only when still Unassigned** — never overwrites a human owner. **Backlog included**: default Lucene query has **no date floor** (like LinkedIn source cleanup) so existing still-wrong Indeed records are remapped across cycles (200/run); remapped rows leave the query and later runs stay cheap. Optional `INDEED_INBOUND_REMAP_LOOKBACK_HOURS` (>0) narrows by `dateLastModified`. Mirrors LinkedIn/email inbound so Owner Reassignment can later claim from recruiter activity.
- **Problem**: Native Indeed Apply (Plan B) creates Bullhorn candidates as **New Lead + Unassigned + source Indeed** with a JobSubmission, but never creates a ParsedEmail and never sets status to Online Applicant — so Scout’s detectors never saw them (LinkedIn Job Board email inbound continued to screen normally).
- **Fix**: `detect_indeed_applicants` in `screening/detection.py` — source-based Lucene search (`Indeed` / `Indeed Job Board`), JobSubmission gate, Unassigned-eligible human-owner skip, merged into the 1-minute vetting cycle (same pattern as Matador).

### July 2026: Railway Production, STSI Channel Feeds & Screening Compliance
- **Railway deployment**: Production on Railway (`gunicorn` via `railway.toml`); Entra/Graph mailbox-pull auth for inbound applicants
- **Split Microsoft Entra credentials (Jul 28)**: Support Portal SSO uses `SUPPORT_MICROSOFT_CLIENT_ID` / `SUPPORT_MICROSOFT_CLIENT_SECRET` / `SUPPORT_MICROSOFT_TENANT_ID` (Myticas Support Portal app). Graph mailbox-pull keeps `MICROSOFT_CLIENT_*` + `MICROSOFT_TENANT_ID` on the **mail** app (`Mail.Read` application permission for `Apply@myticas.com`). Do not point both at the Support Portal app — that yields Graph `403` and stops applicant intake.
- **STSI channel feeds**: Separate Indeed (tearsheet 1640) and ZipRecruiter (1641) XML feeds; LinkedIn v2 unchanged (tearsheet 1531); apply URLs use `apply.stsigroup.com` with `?source=` params; channel feed publisher header is **STSI** / `https://www.stsigroup.com`
- **120h reference refresh covers all feeds (Jul 27)**: The scheduled reference-number refresh (and manual “Refresh All Reference Numbers”) now rotates refs across **v2 + Indeed + ZipRecruiter** tearsheets into `JobReferenceNumber` — not v2-only. The 30-minute upload cycle still publishes each file; manual refresh regenerates and uploads all three feed files after the DB save.
- **Feed config centralization**: `feeds/feed_config.py` for prod/dev filenames and channel constants
- **Screening Phase A compliance**: AI disclosure on apply forms (Myticas + STSI), recruiter advisory policy in Screening Settings, `screening_rules_version` on vetting logs, compliance guardrails in global prompt, `/screening/compliance-metrics` endpoint
- **Screening reactivation**: `vetting_enabled` with cheap-first **Enforce** routing for cost control
- **Applied-job transparency (Jul 14)**: Always inject/score the job a candidate applied to, even when Bullhorn marks `isOpen=false` while status is still Accepting Candidates (prevents related-only notes missing APPLIED POSITION)
- **Closed applied jobs never qualify (Aug 4)**: Injected closed/ineligible applied jobs are still scored for APPLIED POSITION note context, but `job_can_qualify` blocks `is_qualified` + recruiter email. Remap/`dateLastModified` bumps that re-detect old Online Applicants against a previously screened closed applied job short-circuit without GPT (Luke Duwel 4657295 × job 34990 Lost - Competition).
- **Note/email outcome sync (Jul 15)**: Re-screens that flip Qualified ↔ Not Qualified supersede the prior 6h Scout note so Bullhorn notes match recruiter emails after auditor re-vets
- **Re-vet note clarity (Jul 17)**: Auditor re-screen notes now separate historical “why second look” context from the current recommendation, include score deltas, fix at-threshold wording, and call out best-job changes when the top match shifts
- **LinkedIn seat mapping sync (Jul 22)**: Recruiter→`#LI-*` mappings aligned to LinkedIn seat report; Reena/`Myticas Recruiter`→`#LI-RS1`; Lisa aliases→`#LI-LM1`; Rachel→`#LI-RM1`; obsolete seats pruned on seed
- **Inbound Parse door quieted (Jul 22)**: Unconfigured SendGrid Inbound Parse webhook now returns **200 disabled** (stops retry storms) instead of 503; Graph mailbox-pull remains the authoritative apply@ intake path. Re-enable with `SENDGRID_INBOUND_WEBHOOK_SECRET` + optional `SENDGRID_INBOUND_PARSE_ENABLED`
- **Inbound enrich / phone-dedupe harden (Jul 22)**: Blank primary `email` is now filled on returning-applicant enrich; phone-only duplicate hits with conflicting names require AI identity confirmation (blocks junk-shell collisions like Happy Friday vs a real applicant)
- **Zip Easy Apply email integrity (Jul 23; patched Jul 26)**: Board/relay addresses (`noreply@ziprecruiter.com`, `@indeedemail.com`, …) and **owned intake mailboxes** (`apply@myticas.com`, `info@myticas.com`, …) are skipped when choosing candidate email; résumé contact wins over notification-body greets. Prevents Zip Easy Apply from collapsing every applicant onto the Bullhorn record that owns `apply@` (prod: Candidate 4380273). Zip `Great Match:` / `New candidate:` subjects still yield the applicant name. **Repair:** `scripts/repair_zip_apply_collapse.py` (+ pass2) split collapsed applicants into real candidates/submissions, neutralized 4380273 (kept historical sub 620809), and queued Scout re-screen.
- **LinkedIn-as-contact (Jul 26)**: A personal LinkedIn `/in/` URL on the résumé (or email body) counts as recruiter-reachable contact alongside email/phone — name + LinkedIn alone can create a Bullhorn candidate. URL is written to Bullhorn **customText9** (LinkedIn field). Company pages (`/company/…`) are ignored.
- **Indeed native Publish Plan B (Jul 23)**: Tearsheet **1640** (`Sponsored - STSI - Indeed`) can drive Bullhorn’s JobBoard CFC Publish/Unpublish (Corporate + Indeed) — category fuzzy-map, first assigned recruiter as Published Contact, auto-republish on relevant edits, full unpublish on remove (including monitor auto-remove). **Off by default** (`INDEED_TEARSHEET_PUBLISH_ENABLED=false`) until UI login is verified. Requires `BH_UI_USERNAME` / `BH_UI_PASSWORD` (and related `BH_UI_*` vars). Failures email `INDEED_TEARSHEET_PUBLISH_NOTIFY_EMAIL` (default `kroots@myticas.com`). XML Indeed feed remains separate — avoid dual syndication once native is live.
- **Indeed Unpublish fix (Jul 23)**: Native unpublish now uses CFC `method=Publish` + `operation=UNPUBLISH` (the UI’s real shape); fingerprints no longer include `dateLastModified` (stops every-cycle republish thrash). Tearsheet **adds** use `operation=REPUBLISH` — Bullhorn’s `PUBLISH` operation can return “will be removed…” and leave the job unpublished after a prior Unpublish.
- **Indeed pending unpublish (Jul 31)**: Failed tearsheet-1640 unpublishes are retained in sync state (`pending_unpublish`) and retried every cycle until success — previously `_save_state` always wrote current membership and forgot failed removals forever (jobs stayed Indeed-Published in Bullhorn while Manage Tearsheets looked clean). Recovery: `python scripts/recover_indeed_unpublish.py <job_ids> --apply`.
- **Indeed `#INDShow` tag (Jul 31)**: Tearsheet **1640** native Publish/REPUBLISH appends `   #INDShow` (three spaces + tag) to the CFC description when missing, and persists the tagged text to Bullhorn `publicDescription` (or `description` when that was the publish source). Fingerprints use the tagged description so the first tagged publish does not thrash. Unpublish / tearsheet remove does **not** strip the tag.
- **Garbled PDF extract / location false DQ (Jul 24)**: Detect broken-font / ToUnicode PDF gibberish (`utils/resume_text_quality.py`), force vision OCR on inbound + vetting extract, and skip garbled Bullhorn `description` during screening so location (e.g. Atlanta, GA) is read from the real résumé. Area-code examples now include 404/470/678 → Atlanta.
- **Configure Screening bullets (Jul 27)**: Requirements in the Configure modal (open / save / reset / Refine with AI) and newly persisted AI extracts are normalized to `- ` bullet lines via `utils/requirements_format.py`, so prose extracts still display as a point list.
- **Rule 14 soft-relevance tighten (Jul 27/28)**: Soft CS/communication alone no longer marks a current role domain-relevant (Debbie James Crossing Guard → Admin Assistant). Justification enforcer requires a concrete duty/tool; rules version `2026.07.28` then `2026.07.29`.
- **Ops hardening (Jul 28)**: OpenAI auth failures → incomplete retry (not fake 0% NQ); auditor no longer crashes after re-vet deletes the vetting log; undated-tenure years gaps use UNVERIFIED TENURE wording; Indeed XML parks empty while native Plan B is on; Sales Rep `_get_headers` restored; environment_status duplicate rows deduped.
- **Env monitor + Sales Rep (Jul 28)**: Environment monitor probes `https://app.scoutgenius.ai` (auto-migrates stale lyntrix URLs — domain was never moved). Sales Rep Sync uses Bullhorn **search** (not query) for ClientCorporation `customText` fields — BQL `<> ''` caused residual HTTP 400 after BhRestToken fix; scan errors no longer log the full request URL/token.
- **Fraud notifier differentiators (Jul 28)**: Multi-submission claim drift; suggested verification questions on Review/High-Risk emails + Bullhorn notes; PDF Author/Producer fingerprint reuse; optional NeverBounce/Twilio contact validation (Qualified candidates only; toggle + env keys); soft LinkedIn URL cross-check (never High-Risk alone); weekly calibration sample API + `scripts/fraud_calibration_report.py`.
- **Divergent résumé file versions (Aug 6)**: Soft Review advisory (`divergent_resume_versions`, 40 pts) when ≥2 Resume-typed/named Bullhorn files have clearly divergent content after near-identical dedupe (Jaccard &lt;0.40). Free/BH-local; fail-soft.
- **Years from dated roles, not summary claims (Aug 6)**: Scout `estimated_years` must come from dated work-history arithmetic scoped to the JD skill/domain (e.g. AI/ML years ≠ all Python years). Résumé summary phrases like "3+ years shipping production AI" are claims only — rules version `2026.08.06`. Note language sanitized when it says "resume explicitly shows N+ years" despite a dated shortfall. **Qualify gate (`2026.08.06b`)**: clear dated shortfalls (outside close band: ≤0.75yr short or ≥85% of required) set `is_qualified=False` so recruiters are not emailed a Qualified match; close cases may still qualify with a `YEARS CLOSE` caveat.
- **Qualified-only contact validation (Jul 30)**: NeverBounce/Twilio no longer run on every screen. Free fraud signals still assess all applicants; paid contact checks enrich the fraud assessment only after `is_qualified` is true, cutting credit burn to the qualify rate.
- **Related-job scoring brevity (Jul 30)**: Non-applied tearsheet matches instruct the model to keep short complete prose when score &lt; 70 (scores unchanged); applied role keeps normal near-miss detail.
- **OTHER TOP MATCHES note hygiene (Jul 31)**: Not-recommended notes keep a full write-up for the top 2 related roles; trailing related roles below 60% are omitted (no mid-sentence `…` gap cuts — Saitharun / 4673413). Trailing near-misses (≥60) use one complete gap clause only. Prompt brevity also forbids unfinished sentences.
- **Clear-reject brevity floor 60 (Jul 30)**: Applied-role short prose now applies below 60% (was 50%), aligning with recruiter practice that second looks cluster near 75–80%, not the 50–60 band. Related-job brevity (&lt;70) unchanged.
- **Rule 14 soft-skill relevance bar (Jul 28)**: Soft communication / generic customer-service overlap alone no longer counts as domain-relevant for recency (Debbie James / Crossing Guard → Admin Assistant regression). Prompt + justification enforcer require a concrete functional duty/tool; rules version `2026.07.28`.
- **Requirements re-extract cost fix (Jul 29)**: Job-requirements maintenance now gates AI re-extraction on a SHA-256 of the Bullhorn job description (`source_description_hash`), not only `dateLastModified`. Metadata-only Bullhorn bumps (recruiter assignment, status flips) no longer re-burn gpt-5.4 extraction tokens every 5-minute cycle.
- **Re-vet loop guard (Jul 29)**: The pending-revet detector now terminates audit rows that can never back-fill instead of re-screening their candidate every cycle. A row is closed as `revet_skipped_job_mismatch` when a completed re-vet landed without scoring the audited job (job closed, dropped from the tearsheet, or filtered out by the embedding pre-filter), or when it has gone un-scored past `max_attempt_hours` (default 12h). Audit row 18513 had been re-screening one candidate every ~3 minutes for a day, a ~$5.7k/mo run-rate of avoidable OpenAI spend.
- **Requirements churn fix (Jul 29)**: Tearsheet-absence cleanup is now debounced via `JobVettingRequirements.tearsheet_absent_since` (`utils/requirements_pruning.py`, 24h grace). Auto-removal and requirements maintenance were disagreeing about the same ~7 jobs every 5 minutes — cleanup deleted their requirements, maintenance immediately re-extracted them with gpt-5.4 (~88 calls/hr, ~$275/mo). A row is now dropped only after continuous absence past the grace window, and the stamp clears as soon as the job is seen active again.
- **Bullhorn stale tearsheet reconciliation (Jul 29)**: Root cause of the seven-job disagreement was Bullhorn's `search/JobOrder?query=tearsheets.id:*` index retaining removed memberships while the current Entity API correctly showed no association. Tearsheet reads now suppress only Search-only jobs that are both absent from complete Entity membership and already ineligible (closed/on hold); eligible Search-only jobs remain visible to tolerate Entity lag after legitimate additions. Requirements maintenance independently rejects ineligible jobs before AI extraction.
- **OpenAI spend alerting (Jul 29)**: `services/ai_cost_monitor.py` runs the 24h `openai_call_log` rollup on a 30-minute schedule (`ai_cost_alert` job, primary worker only) and e-mails through the existing SendGrid health-alert path. This closes the detection gap the loops above exposed: `AdminHealthService.tile_ai_cost_24h` already classified spend as amber/red, but only into an HTTP response, so six days of runaway spend notified nobody — and the one scheduled health check tests OpenAI *connectivity*, which stays green while a loop makes successful calls. Warn at $150/24h, critical at $250/24h, 6h cooldown, with escalation warning→critical always breaking through. The e-mail includes the top call sites and the scoring-calls-per-screening-run ratio, which distinguishes a runaway loop (~46:1 during the Jul 24 incident) from a genuine applicant-volume spike (~10:1). Observability only — it never throttles or blocks a call. Configurable via `ai_cost_alert_enabled`, `ai_cost_alert_warn_usd_24h`, `ai_cost_alert_critical_usd_24h`, `ai_cost_alert_cooldown_hours`, `ai_cost_alert_email` (falls back to `health_alert_email`).
- **SFTP upload resilience (Jul 30)**: An upload cycle now opens **one** authenticated SFTP connection for all feeds (`FTPService.sftp_session()`) instead of a fresh connect-and-auth per file, and retries transient connect failures three times with growing backoff (2s, 6s). On Jul 30 the 08:29 EDT cycle uploaded `myticas-job-feed-v2.xml` successfully, then had both STSI feeds rejected with `Authentication failed` one second later — the server tarpitted the third connection for 21s before rejecting it. Credentials were unchanged and the 08:59 cycle succeeded, so this was remote-side throttling of three handshakes in quick succession, not a credential problem. Feeds are full republishes every 30 minutes, so the missed cycle self-healed with no lasting impact. Upload failures now also report the underlying reason (auth rejected, timeout, bad path) instead of `Upload returned False`.
- **Bullhorn 401 retry fix (Jul 30)**: `BullhornService.authenticate()` returns `True` immediately when a token is already set, so the common "got a 401, re-authenticate and retry once" recovery was a silent no-op at five call sites that did not clear `rest_token` first — the retry re-sent the token the server had just rejected. `owner_reassignment` had been logging a 401, "retrying once", then an identical 401 every 35 minutes for days, with the retry completing in ~130ms because no login ever happened. Fixed in `tasks/owner_reassignment.py`, `bullhorn_service/entities.py` (3 sites) and `bullhorn_service/candidates.py`; `tests/test_bullhorn_reauth_on_401.py` audits the whole tree so a sixth site cannot reintroduce it.
- **Candidate country normalization (Jul 30)**: Some job boards omit country while sending city/state, causing Bullhorn to retain its `countryID=1` United States default (observed on candidate 4673235: Toronto, ON and a Canadian resume, but United States in the address). New inbound records now send canonical `countryID`/`countryCode`/`countryName`, and `candidate_country_normalization` scans a bounded 48-hour batch every 15 minutes. It corrects or populates country only when the parsed resume corroborates the Bullhorn city/state and the region uniquely maps to one country; citizenship, passport, employer, and education references are not accepted as residence evidence. Ambiguous codes such as WA/IN/CA require an explicit country on the same resume location line. Every write preserves the other address fields, is read-after-write verified, and is recorded in `candidate_country_correction_log`. The job clamps its high-water cursor to the live lookback window and discards stale cursors (a year-2000 mark made Bullhorn return zero hits and disabled later cycles). A phased backfill (`candidate_country_backfill_*`) also walks Canadian-province wrong-US records first, then older US-default candidates for overseas mappings already in the supported set; Bullhorn `/options/Country` supplies tenant IDs. Configure with `candidate_country_normalization_enabled`, `candidate_country_normalization_lookback_hours`, `candidate_country_normalization_batch_size`, `candidate_country_backfill_enabled`, `candidate_country_backfill_phase`, and `candidate_country_backfill_batch_size`.
- **Screening compact scoring output (Jul 30)**: Scoring responses now use the Task #99 json_schema shape by default (`SCREENING_COMPACT_OUTPUT=true`) so unread blocks (`requirement_evidence`, `work_authorization_analysis`, `canadian_clearance_analysis`) are no longer emitted, and weak rejects (`match_score` < 60) are instructed to write short prose only. Scores and post-processing enforcers are unchanged; the goal is lower output-token spend on the dominant `screening.scoring` call site. Set `SCREENING_COMPACT_OUTPUT=false` to revert to loose `json_object` + the prior 3750 completion-token cap without a code rollback.
- **Clear-reject brevity floor 60 (Jul 30)**: Applied-role short prose now applies below 60% (was 50%), aligning with recruiter practice that second looks cluster near 75–80%, not the 50–60 band. Related-job brevity (&lt;70) unchanged.
- **Terra shadow canary (Jul 31)**: Sampled fail-soft dual-run of `gpt-5.6-terra` against escalate `gpt-5.4` only (default ~8% sample, 40 calls/hr cap). gpt-5.4 remains sole production authority; pairs land in `screening_ab_log`. Enable with `SHADOW_LOGGING_DISABLED=false` + `SCREENING_AB_SHADOW_ENABLED=true` (keep `EMBEDDING_AB_SHADOW_ENABLED=false`). Challenger override: `SCREENING_AB_SHADOW_MODEL`.
- **Terra shadow token ceiling (Aug 3)**: Compact shadow calls use `max_completion_tokens=4000` by default (prod escalate stays at 2200) so `gpt-5.6-terra` reasoning no longer truncates to `empty_response (finish=length)`. Tune via `SCREENING_AB_SHADOW_MAX_COMPLETION_TOKENS` (code default is enough — no required Railway var).
### October 2025: Database-Backed Reference Number Preservation ✨
- **Problem Identified**: Live XML URL returns 403 Forbidden, causing reference number reversion
- **Solution Implemented**: JobReferenceNumber database table for persistent storage
- **Impact**: Reference numbers now preserved across all cycles (manual and automated)
- **Manual Refresh Workflow**: Generates XML → Saves reference numbers to database → Uploads
- **Automated Upload Workflow**: Generates XML → Loads from database → Applies references → Saves → Uploads
- **Fallback Logic**: Reads from published XML file if database read fails

### September 2025: Toggle-Based Automation Architecture
- **30-Minute Upload Cycle**: APScheduler-backed automation with settings control
- **Dual Toggle System**: Requires both `automated_uploads_enabled` AND `sftp_enabled`
- **Manual Workflow Support**: Can be fully disabled for manual-only operations
- **Production Monitoring**: Health checks every 2 hours with email alerts to kroots@myticas.com
- **Dashboard Enhancement**: Real-time automation status display (Active/Inactive)

### September 2025: Enhanced XML Processing
- **CDATA Wrapping**: All XML fields properly formatted
- **HTML Parsing**: lxml integration for proper tag closure
- **Multi-Tearsheet Support**: Pulls from all Bullhorn sources
- **Company Name Formatting**: Proper STSI branding

### User Experience Improvements
- **Login Redirect**: Changed from ATS Monitoring to main Dashboard
- **Manual Workflow**: 30-minute automation optimized for manual downloads
- **Change Notifications**: Email alerts only during actual downloads
- **Dashboard Status**: Accurate "Active/Inactive" automation display

---

## 🔐 Security Features

- **OAuth Authentication**: Secure user login with Flask-Login
- **Session Management**: Encrypted session keys
- **SFTP Protocol**: Secure file transfers (not FTP)
- **Password Hashing**: Werkzeug security for user passwords
- **Secret Management**: Environment-based secret storage
- **ProxyFix Middleware**: Proper HTTPS handling

---

## 📞 Support & Monitoring

### System Administrator
**Email**: kroots@myticas.com

**Receives Notifications For**:
- Production environment downtime/recovery
- Automated upload status (success/failure)
- Critical system errors

### Application URLs
- **Main Application**: https://app.scoutgenius.ai
- **Myticas Apply Forms**: https://apply.myticas.com
- **STSI Apply Forms**: https://apply.stsigroup.com

### Health Monitoring
Access health endpoints for status checks:
- `https://app.scoutgenius.ai/health`
- `https://app.scoutgenius.ai/ready`
- `https://app.scoutgenius.ai/alive`
- `https://app.scoutgenius.ai/ping`

---

## 📝 Development Notes

### Code Conventions
- **Framework**: Flask with Jinja2 templates
- **Database ORM**: SQLAlchemy with declarative base
- **XML Library**: lxml for robust parsing
- **Background Jobs**: APScheduler with interval triggers
- **Email Service**: SendGrid Python SDK

### File Structure
```
├── app.py                          # Main Flask application
├── main.py                         # Application entry point
├── models.py                       # Database models
├── email_service.py                # SendGrid email integration
├── ftp_service.py                  # SFTP upload service
├── bullhorn_service.py             # Bullhorn ATS integration
├── simplified_xml_generator.py     # XML generation engine
├── lightweight_reference_refresh.py # Reference number management
├── templates/                      # Jinja2 templates
│   ├── dashboard.html
│   ├── settings.html
│   └── ...
└── static/                         # CSS, JS, assets
```

### Key Design Decisions
1. **Database-Backed References**: Prevents reversion when live URL is protected
2. **30-Minute Automation**: Balances freshness with system load
3. **Manual Workflow Focus**: Optimized for user-initiated downloads
4. **SFTP Over FTP**: Security and thread-safety requirements
5. **Dual-Domain Architecture**: Separates main app from application forms

---

## 🎯 Future Enhancements

### Planned Features
- Enhanced analytics dashboard with job trend visualization
- Advanced filtering and search capabilities
- Multi-tenant support for different clients
- API endpoints for third-party integrations
- Automated reporting system

### Optimization Opportunities
- Redis caching for frequently accessed data
- Celery for distributed task processing
- GraphQL API for flexible data queries
- Real-time WebSocket updates for dashboard

---

## 📄 License & Credits

**Created**: 2025  
**Maintained By**: Development Team  
**Contact**: kroots@myticas.com

---

**Last Updated**: August 7, 2026
**Version**: 2.9 (Main-branch Railway deploy; Render log monitoring removed)
