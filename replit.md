# Scout Genius™ - AI-Powered Job Feed Automation Platform

## Overview
Scout Genius is a Flask-based web application that automates XML job feed processing and synchronizes job listings with Bullhorn ATS/CRM. It provides AI-powered candidate vetting, aiming to maintain accurate, real-time job listings, streamline application workflows, and enhance recruitment efficiency. The platform intends to evolve into a multi-tenant SaaS solution, transforming recruitment operations through automated job feed generation, Bullhorn integration, and advanced AI-driven candidate screening (Scout Vetting).

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.
Development Approval Process: Before executing any development task, always provide "stack recommendation" including:
  - Autonomy level (Economy/Power)
  - Brief rationale for the choice
  - Wait for user approval before proceeding
Task Plans: Every project task plan must include the recommended autonomy level (Economy/Power) and a one-line rationale at the top, so the user can set the correct mode before the task starts.
Source of Truth: GitHub repository (KyleRoots/Scout Genius) — main branch.
Dev Admin Credentials: username=`admin`, password=`MyticasXML2025!`

## System Architecture

### UI/UX Decisions
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme).
- **Client-side**: Vanilla JavaScript.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain Architecture**: Supports `app.scoutgenius.ai`, `apply.myticas.com` / `apply.stsigroup.com`, and `support.myticas.com` / `support.stsigroup.com`.
- **Microsoft SSO**: `support.myticas.com` uses Microsoft Entra ID (Office 365) single sign-on via OAuth 2.0.

### Technical Implementations
- **Web Framework**: Flask (Python 3.11) with modular route blueprints.
- **Database**: PostgreSQL with SQLAlchemy ORM and Alembic.
- **Authentication**: Flask-Login for user management, with username/email login and password reset.
- **Authorization**: Granular module-based access control.
- **Background Processing**: APScheduler for automated tasks like tearsheet monitoring, SFTP uploads, and Scout Vetting.
- **XML Processing**: Custom `lxml` processor for data handling and HTML consistency.
- **Email Service**: SendGrid for notifications.
- **AI/LLM Integration**: OpenAI GPT-5.4 is standardized across modules for candidate vetting, screening, resume formatting, quality auditing, job requirements extraction, duplicate candidate detection, job classification, Scout Support, Scout Prospector research, and occupation/title extraction.
- **Embedding Service**: OpenAI `text-embedding-3-large` for similarity-based pre-filtering in candidate-job matching.
- **Error Tracking**: Sentry SDK integration.
- **Testing**: Comprehensive pytest suite.
- **Screening Engine Architecture**: Modular mixin package with an orchestrator for managing AI prompts, Bullhorn note formatting, notifications, candidate detection, job management, and recovery. Batch-optimized processing of candidates using `ThreadPoolExecutor` with thread-safe Bullhorn access and SQLAlchemy session isolation.
- **Dual XML Feed System**: Generates two XML files (`myticas-job-feed-v2.xml` and `myticas-job-feed-pando.xml`) every 30 minutes.
- **Dual-Cycle Monitoring**: 5-minute tearsheet monitoring and 30-minute automated SFTP upload cycles.
- **Job Application Forms**: Public forms with multi-brand support, resume parsing, and Bullhorn integration including duplicate candidate detection and profile enrichment.
- **Resume HTML Formatting**: Three-layer process including GPT-5.4 formatting.
- **AI Vision OCR for Scanned PDFs**: Automatic fallback for image-based/scanned PDF resumes using GPT-4.1-mini vision when text extraction fails.
- **Bullhorn JSON-Enveloped File Unwrapping**: Automatic detection and decoding of Bullhorn's JSON-wrapped file responses.
- **Magic Byte File Format Detection**: Inspects file content bytes for format detection (DOCX/ZIP, DOC, PDF) to ensure correct parsing and fallbacks.
- **AI Job Classification**: Classifies jobs based on LinkedIn taxonomy.
- **Scout Vetting**: AI-powered candidate screening using GPT-5.4 with embedding pre-filtering, experience-level classification, two-phase scoring, work authorization/security clearance inference, configurable prompts, employment gap penalties, AI-native recency relevance, and per-job Employer Prestige Boost.
- **Inline-Editable AI Requirements (Configure modal)**: Recruiters can edit AI-extracted requirements directly, with edits prioritized and options to reset.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter-facing dashboard for AI match results, scores, and qualification status.
- **Scout Screening Quality Auditor**: Background AI audit to review "Not Qualified" results for scoring errors, with auto-trigger re-vets.
- **Job Eligibility — Single Source of Truth**: `utils/job_status.py` defines `INELIGIBLE_STATUSES` and `is_job_eligible(job)` for consistent job eligibility checks.
- **Matador API Candidate Detection**: Owner-based detection path for corporate-website applicants from Bullhorn.
- **Recruiter-Activity Pause Gate**: Auto-screening defers when a human recruiter has interacted with a candidate within a configurable lookback period, manageable via a super-admin UI.
- **Resilient JobSubmission Lookup**: Helper `CandidateDetectionMixin._fetch_latest_job_submission` fetches the most recent JobSubmission for a candidate with retry logic.
- **Bullhorn Note Duplicate Safeguard**: Prevents stale notes from blocking successful re-screen results and correctly classifies 0% scores.
- **NUL-Byte Sanitization at Persistence Boundaries**: Shared helper `utils/text_sanitization.sanitize_text()` strips PostgreSQL-incompatible NUL bytes from all text/varchar fields sourced from Bullhorn and OpenAI before persistence, using `SafeText`/`SafeString` SQLAlchemy `TypeDecorator` column types.
- **Zero-Score Verification**: Automated re-verification of 0% scores for top 3 jobs using GPT-4.1-mini.
- **Zero-Score Failure Retry Limiter**: Tracks and limits automatic retries for candidates with 0% API failures.
- **AI-Output XSS Hardening — Scout Prospector**: Templates consuming AI-generated content use `textContent` and `createElement` for DOM manipulation to prevent XSS.
- **AI-Output XSS Hardening — Platform-wide (L2 Phase B/C)**: Shared helper `static/js/ai_output.js` exposes `window.AIOutput.escapeHtml` and 10 other safe-render primitives, loaded after Bootstrap in `base_layout.html`. Every dynamic `innerHTML` interpolation across hardened templates (apply, apply_stsi, support_request, support_request_stsi, vetting_sandbox, ats_integration, ats_integration_details, log_monitoring, scout_screening, base_layout) routes through `escapeHtml` or a documented safe-pattern allow-list. Locked by enumerative regression test `tests/test_xss_audit.py` (12 tests) plus helper unit tests (13 tests). New entries to the hardened set must abide by the same property.
- **Fool-Proof Inbound Email Candidate Extraction**: Multi-layer defense chain (`email_inbound_service.py` and `utils/candidate_name_extraction.py`) ensures zero silent drops when applicants arrive via job-board email forwards, including multi-token regex, AI parsing, filename parsing, email address parsing, HTML-aware body extraction, and last-resort focused AI extraction.
- **Automated Duplicate Candidate Merge**: Two-mode system for merging duplicate candidate records with an audit trail.
- **Candidate Data Cleanup (Scheduled)**: Background job to extract missing emails, reparse empty descriptions, and fill missing occupation/title fields using AI.
- **Activity Log (Super-Admin Only)**: System-wide admin visibility tracking login history, module usage, email delivery, and active users.
- **Vetting Sandbox (Super-Admin Only)**: 5-stage wizard for manually testing the AI vetting pipeline.
- **Scout Support**: AI-powered internal ATS support ticket module with two-tier approval, AI intake, clarification, solution proposals, Bullhorn API execution, and a Knowledge Hub.
- **Platform Support (Feedback-to-Ticket)**: User feedback creates support tickets with simplified workflow and "My Tickets" page.
- **Send to Agent for Build (Scout product tickets only)**: Admin workflow for queuing feature requests/bug reports, tracking development, and notifying users upon deployment.
- **Manual Screening Dispatch (M3)**: Helper `utils/screening_dispatch.enqueue_vetting_now(reason)` advances the existing periodic `candidate_vetting_cycle` APScheduler job to fire immediately (with a one-shot fallback for non-primary workers). Manual-trigger routes — `POST /screening/run`, `POST /screening/rescreen-recent`, `POST /screening/start-fresh`, `POST /screening/process-backlog` — hand work off to the background scheduler instead of running `vetting_service.run_vetting_cycle()` inline on the gunicorn request thread. Eliminates the silent-failure mode where moderate batches exceeded gunicorn's 300s `--timeout` and SIGKILLed the worker mid-screening. Routes return 302 in <50ms; progress is observable via the M2 System Health dashboard's in-flight/failed-24h tiles. Helper is idempotent for rapid duplicate clicks (fixed one-shot ID + `ConflictingIdError` treated as success). Locked by `tests/test_screening_dispatch.py` (8 tests). The `/vetting-sandbox/screen` route stays synchronous — it analyzes one resume against one job (~10–20s) and the sandbox UX requires immediate results.

### Bullhorn Note Creation — Critical Requirements
- **`personReference` (REQUIRED)**: Must point to a **Person** entity (Candidate or ClientContact).
- **Entity-Specific Payloads**: Defines `personReference`, association method, and action type for various entities.
- **To-Many Fields**: `candidates` array can be set during PUT; other arrays require `NoteEntity` to link after creation.
- **Standard Fields**: `commentingPerson` (API user's CorporateUser ID: 1147490), `action` (registered Note Action type), and `isDeleted` (always `False`).

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL (Neon-hosted), SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini Vision.