# Scout Genius™ - AI-Powered Job Feed Automation Platform

## Overview
Scout Genius is a Flask-based web application designed to automate XML job feed processing and synchronize job listings with Bullhorn ATS/CRM. Its primary purpose is to provide AI-powered candidate vetting, streamline application workflows, and enhance recruitment efficiency by maintaining accurate, real-time job listings. The platform is envisioned as a multi-tenant SaaS solution, aiming to transform recruitment operations through automated job feed generation, Bullhorn integration, and advanced AI-driven candidate screening.

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
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme) and vanilla JavaScript.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain Architecture**: Supports `app.scoutgenius.ai`, `apply.myticas.com` / `apply.stsigroup.com`, and `support.myticas.com` / `support.stsigroup.com`.
- **Microsoft SSO**: `support.myticas.com` utilizes Microsoft Entra ID (Office 365) single sign-on via OAuth 2.0.

### Technical Implementations
- **Web Framework**: Flask (Python 3.11) with modular route blueprints.
- **Database**: PostgreSQL with SQLAlchemy ORM and Alembic for migrations.
- **Authentication/Authorization**: Flask-Login for user management, including granular module-based access control.
- **Background Processing**: APScheduler manages automated tasks (tearsheet monitoring, SFTP uploads, Scout Vetting).
- **XML Processing**: Custom `lxml` processor for generating dual XML feeds every 30 minutes.
- **Email Service**: SendGrid for notifications.
- **AI/LLM Integration**: OpenAI GPT-5.4 is central to candidate vetting, screening, resume formatting, quality auditing, job requirements extraction, duplicate detection, job classification, Scout Support, Scout Prospector research, and occupation/title extraction.
- **Embedding Service**: OpenAI `text-embedding-3-large` for similarity-based pre-filtering in candidate-job matching and fuzzy duplicate detection.
- **Error Tracking**: Sentry SDK integration.
- **Screening Engine**: Modular mixin package for AI-powered candidate screening with embedding pre-filtering, experience-level classification, two-phase scoring, work authorization/security clearance inference, configurable prompts, and Bullhorn note formatting.
- **Job Application Forms**: Public-facing forms with multi-brand support, resume parsing, and Bullhorn integration.
- **AI Vision OCR**: GPT-4.1-mini vision processes image-based/scanned PDF resumes.
- **Inline-Editable AI Requirements**: Recruiters can directly edit AI-extracted job requirements.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter dashboard displaying AI match results, scores, and qualification status.
- **Scout Screening Quality Auditor**: Background AI audit of screening results with auto-trigger re-vets.
- **NUL-Byte Sanitization**: Strips PostgreSQL-incompatible NUL bytes from all text/varchar fields.
- **AI-Output XSS Hardening**: Platform-wide XSS hardening for dynamically generated AI content.
- **Inbound Email Candidate Extraction**: Multi-layer defense chain for extracting candidate information from job-board email forwards.
- **Automated Duplicate Candidate Merge**: System for merging duplicate candidate records with an audit trail, including AI fuzzy matching.
- **Candidate Data Cleanup**: Scheduled background job for AI-driven extraction of missing emails, re-parsing empty descriptions, and filling missing occupation/title fields.
- **Activity Log**: Super-admin visibility for tracking login history, module usage, email delivery, and active users.
- **Vetting Sandbox**: 5-stage wizard for manually testing the AI vetting pipeline.
- **Scout Support**: AI-powered internal ATS support ticket module with two-tier approval, AI intake, clarification, solution proposals, and Bullhorn API execution.
- **Platform Support**: User feedback creates support tickets with a simplified workflow.
- **Modularized Services**: Key services like Seeding, Vetting Routes, Bullhorn Service, XML Integration Service, Vetting Audit Service, Automation Service, Email Service, and Inbound Email Service are modularized into mixin-based packages.
- **Code Refactoring**: Extensive refactoring of monolith files into modular packages for `tasks`, `screening/prompt_builder`, `screening/detection`, `candidate_vetting_service`, and `routes/xml_routes` for improved maintainability and organization.
- **Fresh-Prod-DB Guard**: Prevents accidental reseeding of production databases.
- **Phone-Search Trigram Index**: GIN trigram index on normalized phone numbers for efficient substring lookups.
- **Resume Name Hardening**: Multi-layered fix for incorrect name extraction from resumes.
- **Location Review Tier**: Candidates with small location penalties are flagged for recruiter judgment.
- **PandoLogic Note-Based Re-Applicant Detector**: Detects re-applicants via PandoLogic API notes.
- **Prestige Notification Threshold Gate**: Notifies recruiters of prestige boosts only if the boosted score meets qualifying thresholds.
- **Nightly Database Backup**: Automated daily PostgreSQL backup to OneDrive with 30-day retention.
- **Audit Cooldown**: Quality Auditor skips re-examining the same (candidate, job) pair within a configurable window under specific conditions.
- **API User → Recruiter Ownership Reassignment**: Scheduled task to reassign candidate ownership in Bullhorn from API users to the first human recruiter who interacted with the candidate (via Bullhorn Notes). Includes paginated note search, a preview function, and kill switch. Surfaces every cycle (5-min, daily sweep, manual live batch) in the Automation Hub Run History panel via a lazily-created `AutomationTask` row keyed by `config_json` marker `"builtin_key": "owner_reassignment"`. A noise filter suppresses no-signal 5-min rows (only writes when reassigned > 0, failed > 0, errors, or operator-actionable guard-rail failures like Bullhorn auth/search errors); daily sweep and manual batch always write. Run details include the list of reassigned candidate IDs (capped at first 200, with total + truncation flag) for super-admin spot-checks. Stale-DB-connection guard: long Bullhorn iterations call `db.session.remove()` before any post-iteration commit, and the outer try/except has a `finally` that always disposes the request-scoped session (mirrors the pattern in `scheduler_setup.py` long-running jobs).
- **Scout Vetting Pre-Launch Hardening**: Implemented staggered outreach for vetting sessions, a global toggle for Scout Vetting, cross-session availability answer sharing, mid-conversation requirements-change flag, and improved test coverage for inbound replies.
- **Bullhorn Note Creation**: Critical requirements for Bullhorn note creation include `personReference`, entity-specific payloads, handling to-many fields, and standard fields like `commentingPerson`, `action`, and `isDeleted`.

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL, SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry, Microsoft OneDrive.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini Vision.