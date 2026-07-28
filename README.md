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
- **Production Hosting**: Railway (`scout-genius` project, JobPulse service)
- **Dual-Domain Setup**:
  - `app.scoutgenius.ai` — main Scout Genius app
  - `apply.myticas.com` — Myticas job applications
  - `apply.stsigroup.com` — STSI job applications

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
- **Compliance (Phase A, July 2026)**: Apply-form AI notices, rules version stamping (`screening/compliance.py`), guardrailed global prompt, compliance metrics endpoint
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

### July 2026: Railway Production, STSI Channel Feeds & Screening Compliance
- **Railway deployment**: Production on Railway (`gunicorn` via `railway.toml`); Entra/Graph mailbox-pull auth for inbound applicants
- **STSI channel feeds**: Separate Indeed (tearsheet 1640) and ZipRecruiter (1641) XML feeds; LinkedIn v2 unchanged (tearsheet 1531); apply URLs use `apply.stsigroup.com` with `?source=` params; channel feed publisher header is **STSI** / `https://www.stsigroup.com`
- **120h reference refresh covers all feeds (Jul 27)**: The scheduled reference-number refresh (and manual “Refresh All Reference Numbers”) now rotates refs across **v2 + Indeed + ZipRecruiter** tearsheets into `JobReferenceNumber` — not v2-only. The 30-minute upload cycle still publishes each file; manual refresh regenerates and uploads all three feed files after the DB save.
- **Feed config centralization**: `feeds/feed_config.py` for prod/dev filenames and channel constants
- **Screening Phase A compliance**: AI disclosure on apply forms (Myticas + STSI), recruiter advisory policy in Screening Settings, `screening_rules_version` on vetting logs, compliance guardrails in global prompt, `/screening/compliance-metrics` endpoint
- **Screening reactivation**: `vetting_enabled` with cheap-first **Enforce** routing for cost control
- **Applied-job transparency (Jul 14)**: Always inject/score the job a candidate applied to, even when Bullhorn marks `isOpen=false` while status is still Accepting Candidates (prevents related-only notes missing APPLIED POSITION)
- **Note/email outcome sync (Jul 15)**: Re-screens that flip Qualified ↔ Not Qualified supersede the prior 6h Scout note so Bullhorn notes match recruiter emails after auditor re-vets
- **Re-vet note clarity (Jul 17)**: Auditor re-screen notes now separate historical “why second look” context from the current recommendation, include score deltas, fix at-threshold wording, and call out best-job changes when the top match shifts
- **LinkedIn seat mapping sync (Jul 22)**: Recruiter→`#LI-*` mappings aligned to LinkedIn seat report; Reena/`Myticas Recruiter`→`#LI-RS1`; Lisa aliases→`#LI-LM1`; Rachel→`#LI-RM1`; obsolete seats pruned on seed
- **Inbound Parse door quieted (Jul 22)**: Unconfigured SendGrid Inbound Parse webhook now returns **200 disabled** (stops retry storms) instead of 503; Graph mailbox-pull remains the authoritative apply@ intake path. Re-enable with `SENDGRID_INBOUND_WEBHOOK_SECRET` + optional `SENDGRID_INBOUND_PARSE_ENABLED`
- **Inbound enrich / phone-dedupe harden (Jul 22)**: Blank primary `email` is now filled on returning-applicant enrich; phone-only duplicate hits with conflicting names require AI identity confirmation (blocks junk-shell collisions like Happy Friday vs a real applicant)
- **Zip Easy Apply email integrity (Jul 23; patched Jul 26)**: Board/relay addresses (`noreply@ziprecruiter.com`, `@indeedemail.com`, …) and **owned intake mailboxes** (`apply@myticas.com`, `info@myticas.com`, …) are skipped when choosing candidate email; résumé contact wins over notification-body greets. Prevents Zip Easy Apply from collapsing every applicant onto the Bullhorn record that owns `apply@` (prod: Candidate 4380273). Zip `Great Match:` / `New candidate:` subjects still yield the applicant name. **Repair:** `scripts/repair_zip_apply_collapse.py` (+ pass2) split collapsed applicants into real candidates/submissions, neutralized 4380273 (kept historical sub 620809), and queued Scout re-screen.
- **LinkedIn-as-contact (Jul 26)**: A personal LinkedIn `/in/` URL on the résumé (or email body) counts as recruiter-reachable contact alongside email/phone — name + LinkedIn alone can create a Bullhorn candidate. URL is written to Bullhorn **customText9** (LinkedIn field). Company pages (`/company/…`) are ignored.
- **Indeed native Publish Plan B (Jul 23)**: Tearsheet **1640** (`Sponsored - STSI - Indeed`) can drive Bullhorn’s JobBoard CFC Publish/Unpublish (Corporate + Indeed) — category fuzzy-map, first assigned recruiter as Published Contact, auto-republish on relevant edits, full unpublish on remove (including monitor auto-remove). **Off by default** (`INDEED_TEARSHEET_PUBLISH_ENABLED=false`) until UI login is verified. Requires `BH_UI_USERNAME` / `BH_UI_PASSWORD` (and related `BH_UI_*` vars). Failures email `INDEED_TEARSHEET_PUBLISH_NOTIFY_EMAIL` (default `kroots@myticas.com`). XML Indeed feed remains separate — avoid dual syndication once native is live.
- **Indeed Unpublish fix (Jul 23)**: Native unpublish now uses CFC `method=Publish` + `operation=UNPUBLISH` (the UI’s real shape); fingerprints no longer include `dateLastModified` (stops every-cycle republish thrash). Tearsheet **adds** use `operation=REPUBLISH` — Bullhorn’s `PUBLISH` operation can return “will be removed…” and leave the job unpublished after a prior Unpublish.
- **Garbled PDF extract / location false DQ (Jul 24)**: Detect broken-font / ToUnicode PDF gibberish (`utils/resume_text_quality.py`), force vision OCR on inbound + vetting extract, and skip garbled Bullhorn `description` during screening so location (e.g. Atlanta, GA) is read from the real résumé. Area-code examples now include 404/470/678 → Atlanta.
- **Configure Screening bullets (Jul 27)**: Requirements in the Configure modal (open / save / reset / Refine with AI) and newly persisted AI extracts are normalized to `- ` bullet lines via `utils/requirements_format.py`, so prose extracts still display as a point list.
- **Rule 14 soft-relevance tighten (Jul 27/28)**: Soft CS/communication alone no longer marks a current role domain-relevant (Debbie James Crossing Guard → Admin Assistant). Justification enforcer requires a concrete duty/tool; rules version `2026.07.28` then `2026.07.29`.
- **Ops hardening (Jul 28)**: OpenAI auth failures → incomplete retry (not fake 0% NQ); auditor no longer crashes after re-vet deletes the vetting log; undated-tenure years gaps use UNVERIFIED TENURE wording; Indeed XML parks empty while native Plan B is on; Sales Rep `_get_headers` restored; environment_status duplicate rows deduped.
- **Env monitor + Sales Rep (Jul 28)**: Environment monitor probes `https://app.scoutgenius.ai` (auto-migrates stale lyntrix URLs — domain was never moved). Sales Rep Sync uses Bullhorn **search** (not query) for ClientCorporation `customText` fields — BQL `<> ''` caused residual HTTP 400 after BhRestToken fix; scan errors no longer log the full request URL/token.
- **Fraud notifier differentiators (Jul 28)**: Multi-submission claim drift; suggested verification questions on Review/High-Risk emails + Bullhorn notes; PDF Author/Producer fingerprint reuse; optional NeverBounce/Twilio contact validation (toggle + env keys); soft LinkedIn URL cross-check (never High-Risk alone); weekly calibration sample API + `scripts/fraud_calibration_report.py`.
- **Rule 14 soft-skill relevance bar (Jul 28)**: Soft communication / generic customer-service overlap alone no longer counts as domain-relevant for recency (Debbie James / Crossing Guard → Admin Assistant regression). Prompt + justification enforcer require a concrete functional duty/tool; rules version `2026.07.28`.

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

**Last Updated**: July 26, 2026
**Version**: 2.3 (Railway production, Indeed Plan B, garbled-resume OCR/screening harden)
