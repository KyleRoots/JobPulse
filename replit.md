# Scout Genius™ - AI-Powered Job Feed Automation Platform

## Overview
Scout Genius is a Flask-based web application designed to automate XML job feed processing, synchronize job listings with Bullhorn ATS/CRM, and provide AI-powered candidate vetting (Scout Vetting). Its primary purpose is to maintain accurate, real-time job listings, streamline application workflows, and enhance recruitment efficiency through automation. The project aims to become a multi-tenant SaaS platform, revolutionizing recruitment operations.

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.
Development Approval Process: Before executing any development task, always provide "stack recommendation" including:
  - Autonomy level (Economy/Power)
  - Brief rationale for the choice
  - Wait for user approval before proceeding
Source of Truth: GitHub repository (KyleRoots/Scout Genius) — main branch.
Dev Admin Credentials: username=`admin`, password=`MyticasXML2025!`

## System Architecture

### UI/UX Decisions
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme) for a responsive and modern user interface.
- **Client-side**: Vanilla JavaScript for interactive elements.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain Architecture**: Supports `app.scoutgenius.ai` (main application) and `apply.myticas.com` / `apply.stsigroup.com` (job application forms).

### Technical Implementations
- **Web Framework**: Flask (Python 3.11) utilizing modular route blueprints.
- **Database**: PostgreSQL (Neon-hosted) with SQLAlchemy ORM and Alembic for migrations.
- **Authentication**: Flask-Login for user management, supporting username or email login, with a secure password reset flow.
- **Authorization**: Granular module-based access control (`subscribed_modules` on User model) with route guards.
- **User Management**: Admin interface for user CRUD, module subscriptions, Bullhorn User ID assignment, password management, and account activation/reset emails. Includes "View As" impersonation for admins.
- **Background Processing**: APScheduler manages automated tasks for monitoring and health checks. **APScheduler best practice (MANDATORY)**: Any critical scheduled job (especially one that must survive alongside long-running jobs like the 5-minute tearsheet monitor) MUST be registered with `misfire_grace_time=300` (5 minutes) and `coalesce=False`. The global scheduler default of `misfire_grace_time=30` is too short — the tearsheet monitor takes ~36 seconds, and when a 30-minute upload job fires within that window, the 30-second grace period expires before the thread pool has capacity, causing APScheduler to silently drop the execution. Without `coalesce=False`, subsequent queued firings are collapsed into one. Root cause of the March 2026 SFTP upload misfire bug. All future long-running or time-sensitive jobs must specify these parameters explicitly.
- **XML Processing**: Custom `lxml` processor for data handling, reference number generation, and HTML consistency.
- **Email Service**: SendGrid for notifications and delivery logging.
- **AI/LLM Integration**: OpenAI GPT-4o for candidate vetting, job classification, and resume formatting. Claude Opus 4 powers the Product Expert Workbench (dev-only).
- **Embedding Service**: Used for similarity-based pre-filtering in candidate-job matching.
- **Error Tracking**: Sentry SDK integration for optional error monitoring.
- **Testing**: Comprehensive pytest suite.
- **Proxy Support**: `ProxyFix` middleware for reverse proxy environments.

### Feature Specifications
- **Dual XML Feed System**: Every 30-minute upload cycle generates two XML files: `myticas-job-feed-v2.xml` (STSI tearsheet 1531 capped at 10 most-recently-added jobs via `TearsheetJobHistory.timestamp`) and `myticas-job-feed-pando.xml` (all jobs from all tearsheets, no cap). Both share the same `JobReferenceNumber` database. The 120-hour reference refresh runs against the full (uncapped) job set to ensure all jobs get numbers. `SimplifiedXMLGenerator.generate_fresh_xml()` accepts an optional `tearsheet_caps` dict (e.g., `{1531: 10}`). Dashboard shows dual feed stats via `GlobalSettings` key `dual_feed_last_result`. Email notifications include both feed job counts.
- **Dual-Cycle Monitoring**: 5-minute tearsheet monitoring and 30-minute automated SFTP upload cycles with smart cleanup of ineligible jobs.
- **Enforce Tearsheet Jobs Public**: Scheduled job running every 30 minutes (`enforce_tearsheet_jobs_public` in `tasks.py`). Queries Bullhorn for all jobs in monitored tearsheets where `isPublic=false`, filters out ineligible statuses (Filled, Cancelled, Archive, etc.), and sets qualifying jobs to `isPublic=true` via the Bullhorn REST API. Now also handles monitors with `tearsheet_id=0` via `last_job_snapshot`. Stores run results (`succeeded` count + sample IDs) to `GlobalSettings` key `enforce_public_last_result` for display on the Automation Hub. Uses the mandatory thread-safe standalone `requests` pattern. Registered with `misfire_grace_time=300, coalesce=False`.
- **Vetting Scope Alignment**: `get_active_jobs_from_tearsheets()` in `candidate_vetting_service.py` no longer applies a redundant `INELIGIBLE_STATUSES` filter. The 5-minute monitoring cycle already auto-removes ineligible jobs from Bullhorn tearsheets, so the vetting service now returns all jobs from monitored tearsheets — matching the monitoring service's scope exactly.
- **ATS Credentials Page**: `ats_integration_settings.html` now extends `base_layout.html` (neutral System styling). Accessible via "ATS Credentials" link in the System sidebar section (admin-only). Route: `/ats-integration/settings`, `active_page='ats_settings'`.
- **Feedback Email Sender**: Changed from `noreply@lyntrix.ai` to `noreply@scoutgenius.ai` (SendGrid verified domain).
- **Real-Time Notifications**: Email alerts for new jobs on monitored tearsheets.
- **Environment Isolation**: Separate development and production configurations.
- **Database-First Reference Numbers**: `JobReferenceNumber` table as the single source of truth.
- **Job Application Forms**: Public forms with multi-brand support, resume parsing (Word/PDF), and Bullhorn integration.
- **Resume HTML Formatting**: Three-layer process: extraction, normalization, and GPT-4o formatting.
- **AI Job Classification**: Classifies jobs based on LinkedIn taxonomy.
- **Zero-Job Detection Safeguard**: Prevents XML corruption from empty API responses.
- **Zero-Touch Deployment**: Environment-aware database seeding.
- **Scout Vetting**: AI-powered candidate screening using GPT-4o with embedding pre-filtering, experience-level classification, location-aware scoring, work authorization/security clearance inference, and configurable global screening prompts.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter-facing dashboard displaying AI match results, including scores, qualification status, and AI notes, with per-job threshold and override capabilities.
- **Module Switcher**: UI component for non-admin users subscribed to multiple modules. Pill navigation in top navbar, visible only when user has 2+ modules.
- **Company Admin Role**: A specialized role (`is_company_admin=True`) with `is_any_admin`, `can_view_all_users`, and `effective_role` properties. Gets only their own subscribed modules (not all modules like super-admin). Grantable via admin user settings UI. `effective_role` values: `super_admin`, `company_admin`, `user`.
- **Multi-Company Support**: `company` field on User model (default: `Myticas Consulting`). Company admins see only users within their own company via `get_visible_users()`. Super-admins see all. Impersonation is company-scoped for company admins. Company dropdown in admin user management UI. Company name displayed in sidebar. Supported companies: `Myticas Consulting`, `STSI Group`. Settings user list has pill filter toggle (All / Myticas / STSI) matching the Support Directory UX.
- **Product Expert Workbench**: Claude Opus 4-powered chat interface for building custom Bullhorn automations, including built-in automations and execution logging. Super-admin only. Reliability hardened: mandatory Bullhorn connection pre-flight on session start (verifies corporation name and REST URL before accepting any task), revised system prompt enforcing no-fabrication, task anchoring, verify-after-write, and strict planning-vs-execution separation.
- **Scout Automation Module (Planned — Next Phase)**: Separate production-facing automation module for non-technical users. Distinct from the Workbench. Full specification:
  - Plain English AI output only — no code blocks, no raw JSON, no markdown headers, no API jargon
  - Real-time progress notifications during long-running tasks (polling-based, every 3s) — UX mirrors the Replit agent style: visible step-by-step progress cards updated live, showing records processed / total, current batch number, and estimated time remaining
  - Long-running operations run in a background thread (server-side, same pattern as Workbench `LONG_RUNNING_BUILTINS`); the frontend polls for completion and streams visible progress cards rather than waiting on a single HTTP response
  - **Thread-safety rule (MANDATORY)**: All Bullhorn HTTP calls made from background threads must use standalone `requests.get()` / `requests.post()` with `self._bh_headers()` — never `BullhornService.session.*`. The shared `requests.Session` is not thread-safe; using it from a background thread causes silent write failures where Bullhorn returns `changeType: UPDATE` but data never persists. This was the root cause of the `update_field_bulk` silent-failure bug (March 2026). All existing long-running built-ins follow this pattern and must continue to do so.
  - Post-task summary card with actual record counts, sample IDs (3–5), and before/after field values displayed in an amber-bordered card
  - Drag-and-drop screenshot upload zone — images base64-encoded and passed to Claude vision for analysis
  - Separate system prompt and service (`scout_automation_service.py`) from the Workbench
  - New blueprint: `routes/scout_automation.py`; new template: `templates/scout_automation.html`
  - Subscribed module key: `scout_automation`; admin users get access automatically
  - Reuses `AutomationChat` and `AutomationTask` DB tables with a `module_type` discriminator column (`workbench` vs `scout`)
- **Activity Log (Super-Admin Only)**: System-wide admin visibility page at `/activity-log` under the System sidebar section. Three tabbed views: Login History (tracks every login with user, timestamp, IP, browser/OS), Module Usage (session-based tracking of which modules users navigate to — deduplicates within a session), and Email Delivery (unified view of welcome, password reset, and screening recommendation emails from `EmailDeliveryLog`). Summary metric cards show 7-day totals. Filterable by user and date range (7/30/90 days). Blueprint: `routes/activity_log.py`, template: `templates/activity_log.html`. DB model: `UserActivityLog` (user_id, activity_type, details JSON, ip_address, created_at). Login tracking hooks into `routes/auth.py` login route. Module tracking via `@app.before_request` hook with `_MODULE_MAP` prefix matching. Welcome and password reset emails now logged to `EmailDeliveryLog` (types: `welcome_email`, `password_reset_email`).
- **Vetting Sandbox (Super-Admin Only)**: 5-stage wizard at `/vetting-sandbox` for manually testing the AI vetting pipeline (screening → outreach generation → email sending → reply simulation → finalization). Uses real AI services but isolates data with `is_sandbox=True` flag on `CandidateVettingLog` and `ScoutVettingSession`. Outreach emails routed to admin-specified test address only. No Bullhorn notes or recruiter notifications created. Sidebar entry under Scout Vetting section with amber flask icon. Blueprint: `routes/vetting_sandbox.py`, template: `templates/vetting_sandbox.html`.

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL (Neon-hosted), SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry.
- **AI Models**: OpenAI GPT-4o, Anthropic Claude Opus 4.