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
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme) for a responsive and modern user interface.
- **Client-side**: Vanilla JavaScript for interactive elements.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain Architecture**: Supports `app.scoutgenius.ai` (main application) and `apply.myticas.com` / `apply.stsigroup.com` (job application forms).

### Technical Implementations
- **Web Framework**: Flask (Python 3.11) utilizing modular route blueprints.
- **Database**: PostgreSQL with SQLAlchemy ORM and Alembic for migrations.
- **Authentication**: Flask-Login for user management, supporting username or email login, with password reset.
- **Authorization**: Granular module-based access control with route guards.
- **User Management**: Admin interface for user CRUD, module subscriptions, Bullhorn User ID assignment, and account activation/reset. Includes "View As" impersonation.
- **Background Processing**: APScheduler manages automated tasks (e.g., tearsheet monitoring, SFTP uploads, `enforce_tearsheet_jobs_public`, Scout Vetting cycle, `candidate_data_cleanup`). Critical scheduled jobs use `misfire_grace_time=300` and `coalesce=False`.
- **XML Processing**: Custom `lxml` processor for data handling, reference number generation, and HTML consistency.
- **Email Service**: SendGrid for notifications and delivery logging.
- **AI/LLM Integration**: OpenAI GPT-4o for candidate vetting, job classification, and resume formatting.
- **Embedding Service**: Used for similarity-based pre-filtering in candidate-job matching.
- **Error Tracking**: Sentry SDK integration.
- **Testing**: Comprehensive pytest suite.
- **Proxy Support**: `ProxyFix` middleware for reverse proxy environments.

### Feature Specifications
- **Dual XML Feed System**: Generates two XML files (`myticas-job-feed-v2.xml` with capped jobs, `myticas-job-feed-pando.xml` with all jobs) every 30 minutes.
- **Dual-Cycle Monitoring**: 5-minute tearsheet monitoring and 30-minute automated SFTP upload cycles with smart cleanup.
- **Enforce Tearsheet Jobs Public**: Scheduled task to ensure jobs in monitored tearsheets are `isPublic=true` in Bullhorn.
- **Vetting Scope Alignment**: Ensures `candidate_vetting_service` job scope matches the monitoring service.
- **ATS Credentials Page**: Admin-only interface for managing ATS integration settings.
- **Feedback Email Sender**: Uses `noreply@scoutgenius.ai`.
- **Real-Time Notifications**: Email alerts for new jobs.
- **Environment Isolation**: Separate development and production configurations.
- **Database-First Reference Numbers**: `JobReferenceNumber` table as the single source of truth.
- **Job Application Forms**: Public forms with multi-brand support, resume parsing, and Bullhorn integration.
- **Resume HTML Formatting**: Three-layer process: extraction, normalization, and GPT-4o formatting.
- **AI Job Classification**: Classifies jobs based on LinkedIn taxonomy.
- **Zero-Job Detection Safeguard**: Prevents XML corruption.
- **Zero-Touch Deployment**: Environment-aware database seeding.
- **Scout Vetting**: AI-powered candidate screening using GPT-4o with embedding pre-filtering, experience-level classification, location-aware scoring, work authorization/security clearance inference, and configurable global screening prompts.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter-facing dashboard for AI match results, scores, and qualification status. Includes per-candidate "Re-screen" button (admin-only) to trigger immediate re-vetting.
- **Scout Screening Quality Auditor**: Background AI audit (every 15 min) that reviews recent Not Qualified results for scoring errors — recency gate misfires, platform age violations, false gap claims, and score-evidence inconsistencies. Uses heuristic pre-checks (no API cost) followed by GPT-4o confirmation for flagged results. High-confidence misfires auto-trigger re-vets with Bullhorn note rewrite. Email summary sent to admin when issues found. Controlled by `screening_audit_enabled` VettingConfig toggle. Service: `vetting_audit_service.py`, model: `VettingAuditLog`, Automation Hub tool: `screening_audit`.
- **Module Switcher**: UI component for non-admin users with multiple module subscriptions.
- **Company Admin Role**: Specialized role for managing users within a specific company.
- **Multi-Company Support**: `company` field on User model; company admins see only users within their company.
- **Automation Hub (Super-Admin Only)**: Management console for scheduled jobs, built-in automation tools (e.g., `cleanup_ai_notes`, `export_qualified`, `email_extractor`, `resume_reparser`, `retry_recruiter_notifications`), and run history. Long-running built-ins execute in background threads. All Bullhorn HTTP calls from background threads use standalone `requests.get()`/`requests.post()` for thread-safety. Email Extractor and Resume Reparser modals support optional `candidate_ids` field for targeted runs.
- **Candidate Data Cleanup (Scheduled)**: Background job (every 30 min, batch=50) that automatically extracts missing emails from resume files and reparses empty candidate descriptions. Controlled by `candidate_cleanup_enabled` GlobalSettings toggle visible in the Automation Hub. Off by default; designed to clear the 97K+ candidate backlog over ~20 days at ~$0.15/cycle.
- **Scout Automation Module (Planned)**: A user-friendly automation module for non-technical users with real-time progress notifications, background processing, and AI vision capabilities.
- **Activity Log (Super-Admin Only)**: System-wide admin visibility page tracking login history, module usage, email delivery, and active users. Includes an **Active Users** tab showing real-time presence with status dots and 30-second auto-refresh (uses `last_active_at` column on User model, throttled to 1-minute DB writes).
- **Vetting Sandbox (Super-Admin Only)**: 5-stage wizard for manually testing the AI vetting pipeline in isolation.
- **Resume Character Limit (Vetting)**: AI vetting reads up to 20,000 characters of resume text (previously 8,000). GAP DESCRIPTION PRECISION prompt: AI must distinguish between a skill being truly absent vs. present-but-insufficient in the resume — eliminates false "no evidence" gap reports.
- **Experience Estimation (Missing Dates)**: When a resume has no employment date ranges, the AI prompt instructs GPT-4o to: (1) if an education end date is present, estimate professional years as (current year − graduation year), treat as inferred and apply conservative scoring (inferred years alone do not satisfy strict 3+ year requirements); (2) if no dates anywhere, default `total_professional_years` to 2.0. The Python-side fallback default is 3.0 (down from 99.0) for the rare case where AI classification parsing fails entirely — prevents false "experienced" assumptions on undatable resumes.
- **DOCX Resume Parsing**: Multi-layer extraction from section headers, body paragraphs, table cells, text boxes (w:drawing XML), and full XML w:t fallback — captures emails and content regardless of resume template layout. Supports `.doc` files via antiword conversion. Garbled text detection with fallback to plain-text-to-HTML conversion.

## Commercial Agreements

### OpenAI — Modified Data Retention Amendment
- **Agreement**: Modified Data Retention Amendment between OpenAI OpCo, LLC and Myticas Consulting ULC.
- **Org ID**: `org-yOsdUJqxqyaR7msNs3M3ISmW`
- **Data Handling**: Zero Data Retention / Modified Abuse Monitoring is active — OpenAI does not log, retain, or use API inputs for training or abuse monitoring for this org.
- **Commercial Terms**: Committed minimum spend agreement in exchange for full, unlimited access to all OpenAI APIs and models.
- **Model Usage Guidance**: Do NOT apply cost-saving model downgrades (e.g. using mini models to save tokens). Use the highest-quality model available (GPT-4o, o1, o3, etc.) whenever better output accuracy, reasoning, or reliability is warranted. The spend commitment removes the need for conservative model selection.

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL (Neon-hosted), SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry.
- **AI Models**: OpenAI GPT-4o (primary). Upgrade to o1/o3 freely when higher reasoning quality is needed — no cost barrier.