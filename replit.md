# Scout Genius™ - AI-Powered Job Feed Automation Platform

## Overview
Scout Genius is a Flask-based web application designed to automate XML job feed processing, synchronize job listings with Bullhorn ATS/CRM, and provide AI-powered candidate vetting. Its primary purpose is to maintain accurate, real-time job listings, streamline application workflows, and enhance recruitment efficiency through automation. The project aims to become a multi-tenant SaaS platform, revolutionizing recruitment operations. Key capabilities include automated job feed generation, Bullhorn integration, and AI-powered candidate screening (Scout Vetting).

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
- **Background Processing**: APScheduler for automated tasks (e.g., tearsheet monitoring, SFTP uploads, Scout Vetting cycle).
- **XML Processing**: Custom `lxml` processor for data handling and HTML consistency.
- **Email Service**: SendGrid for notifications.
- **AI/LLM Integration**: OpenAI GPT-5.4 standardized across all modules for candidate vetting, screening, resume formatting, quality auditing, job requirements extraction, duplicate candidate detection, job classification, Scout Support conversation analysis, Scout Prospector research, and occupation/title extraction.
- **Embedding Service**: OpenAI text-embedding-3-large for similarity-based pre-filtering in candidate-job matching.
- **Error Tracking**: Sentry SDK integration.
- **Testing**: Comprehensive pytest suite.
- **Screening Engine Architecture**: Modular mixin package with an orchestrator for managing AI prompts, Bullhorn note formatting, notifications, candidate detection, job management, and recovery. Batch-optimized processing of candidates (5-at-a-time) using `ThreadPoolExecutor` with thread-safe Bullhorn access and SQLAlchemy session isolation.
- **Dual XML Feed System**: Generates two XML files (`myticas-job-feed-v2.xml` and `myticas-job-feed-pando.xml`) every 30 minutes.
- **Dual-Cycle Monitoring**: 5-minute tearsheet monitoring and 30-minute automated SFTP upload cycles.
- **Job Application Forms**: Public forms with multi-brand support, resume parsing, and Bullhorn integration including duplicate candidate detection and profile enrichment.
- **Resume HTML Formatting**: Three-layer process including GPT-5.4 formatting.
- **AI Vision OCR for Scanned PDFs**: Automatic fallback for image-based/scanned PDF resumes using GPT-4.1-mini vision when text extraction fails.
- **Bullhorn JSON-Enveloped File Unwrapping**: Automatic detection and decoding of Bullhorn's JSON-wrapped file responses.
- **Magic Byte File Format Detection**: Inspects file content bytes for format detection (DOCX/ZIP, DOC, PDF) to ensure correct parsing and fallbacks.
- **AI Job Classification**: Classifies jobs based on LinkedIn taxonomy.
- **Scout Vetting**: AI-powered candidate screening using GPT-5.4 with embedding pre-filtering, experience-level classification, two-phase scoring, work authorization/security clearance inference, configurable prompts, employment gap penalties, AI-native recency relevance, and per-job Employer Prestige Boost. Includes special notifications for below-threshold prestige candidates.
- **Inline-Editable AI Requirements (Configure modal)**: Recruiters can edit AI-extracted requirements directly. Edits are stored in `JobVettingRequirements.edited_requirements` and take priority. Includes a "Reset to AI extraction" link and an "Edited by … on …" badge.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter-facing dashboard for AI match results, scores, and qualification status.
- **Scout Screening Quality Auditor**: Background AI audit to review "Not Qualified" results for scoring errors, with auto-trigger re-vets.
- **Job Eligibility — Single Source of Truth**: `utils/job_status.py` defines `INELIGIBLE_STATUSES` and `is_job_eligible(job)` to ensure consistent job eligibility checks across the system.
- **Matador API Candidate Detection**: Owner-based detection path for corporate-website applicants from Bullhorn with `owner.name='Matador API'` and `status='New Lead'`.
- **Recruiter-Activity Pause Gate (with super-admin UI)**: Auto-screening defers when a human recruiter has interacted with a candidate within a configurable lookback period. Super-admins can manage this gate via the Vetting Settings page.
- **Resilient JobSubmission Lookup (Pandologic + Matador)**: A shared helper `CandidateDetectionMixin._fetch_latest_job_submission` fetches the most recent JobSubmission for a candidate with retry logic for transient failures.
- **Bullhorn Note Duplicate Safeguard**: Prevents stale "Incomplete" or "Analysis failed" notes from blocking successful re-screen results and correctly classifies different types of 0% scores.
- **NUL-Byte Sanitization at Persistence Boundaries**: Shared helper `utils/text_sanitization.sanitize_text()` strips PostgreSQL-incompatible NUL bytes (0x00) from every text/varchar field sourced from Bullhorn (candidate description/firstName/lastName/email, job title/location/tearsheet, recruiter name/email), OpenAI (match summary, skills/experience/gaps), and exception messages before assignment to `CandidateVettingLog` / `CandidateJobMatch`. Prevents the recurring `A string literal cannot contain NUL (0x00) characters` flush failures that silently dropped candidates from screening when their Bullhorn description contained PDF/paste artifacts. Legacy `vetting.resume_utils._sanitize_text` re-exports from the shared module for back-compat. As of M1 (Apr 2026), the audited 16 Bullhorn-sourced fields use `SafeText`/`SafeString` SQLAlchemy `TypeDecorator` column types (`utils/sqlalchemy_types.py`) that automatically call `sanitize_text()` in `process_bind_param`, so the safety property is enforced at the ORM boundary and no longer load-bearing on developer discipline at every call site. Existing manual `sanitize_text()` calls remain as defence in depth. A parametrized regression test (`tests/test_safe_text_columns.py`) fails immediately if any audited field is declared as raw `db.Text`/`db.String`. Underlying SQL column types are unchanged (TEXT / VARCHAR(N)) — no schema migration required.
- **Zero-Score Verification**: Automated re-verification of 0% scores for top 3 jobs using GPT-4.1-mini.
- **Zero-Score Failure Retry Limiter**: Tracks and limits automatic retries for candidates with 0% API failures.
- **Automated Duplicate Candidate Merge**: Two-mode system for merging duplicate candidate records with an audit trail.
- **Candidate Data Cleanup (Scheduled)**: Background job to extract missing emails, reparse empty descriptions, and fill missing occupation/title fields using AI.
- **Activity Log (Super-Admin Only)**: System-wide admin visibility tracking login history, module usage, email delivery, and active users.
- **Vetting Sandbox (Super-Admin Only)**: 5-stage wizard for manually testing the AI vetting pipeline.
- **Scout Support**: AI-powered internal ATS support ticket module with two-tier approval, AI intake, clarification, solution proposals, Bullhorn API execution, and a Knowledge Hub. Features dual feedback, admin takeover, and intelligent retry.
- **Platform Support (Feedback-to-Ticket)**: User feedback creates support tickets with simplified workflow and "My Tickets" page.
- **Send to Agent for Build (Scout product tickets only)**: Admin workflow for queuing feature requests/bug reports, tracking development, and notifying users upon deployment.

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