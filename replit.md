# Scout Genius™ - AI-Powered Job Feed Automation Platform

## Overview
Scout Genius is a Flask-based web application designed to automate XML job feed processing and synchronize job listings with Bullhorn ATS/CRM. It provides AI-powered candidate vetting, streamlines application workflows, and enhances recruitment efficiency by maintaining accurate, real-time job listings. The platform aims to be a multi-tenant SaaS solution, transforming recruitment operations through automated job feed generation, Bullhorn integration, and advanced AI-driven candidate screening.

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.
Development Approval Process: Before executing any development task, always provide "stack recommendation" including:
  - Autonomy level (Economy/Power)
  - Brief rationale for the choice
  - Wait for user approval before proceeding
Task Plans: Every project task plan must include the recommended autonomy level (Economy/Power) and a one-line rationale at the top, so the user can set the correct mode before the task starts.
TL;DR Required: Whenever the agent produces a thorough output (analysis, recommendation, multi-step plan, build summary, post-deploy report, or anything substantial), it must lead with — or include up front — a TL;DR section using the **Problem → Fix → Benefit** pattern (or equivalent compact summary for non-build outputs). The TL;DR comes BEFORE the deep-dive so the user can grasp intent and direction without reading everything. Apply this to single tasks and multi-task batches alike.
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
- **XML Processing**: Custom `lxml` processor for generating dual XML feeds.
- **Email Service**: SendGrid for notifications.
- **AI/LLM Integration**: OpenAI GPT-5.4 for candidate vetting, screening, resume formatting, quality auditing, job requirements extraction, duplicate detection, job classification, Scout Support, Scout Prospector research, and occupation/title extraction.
- **Embedding Service**: OpenAI `text-embedding-3-large` for similarity-based pre-filtering in candidate-job matching and fuzzy duplicate detection.
- **Error Tracking**: Sentry SDK integration.
- **Screening Engine**: Modular mixin package for AI-powered candidate screening with embedding pre-filtering, experience-level classification, two-phase scoring, work authorization/security clearance inference, configurable prompts, and Bullhorn note formatting.
- **Job Application Forms**: Public-facing forms with multi-brand support, resume parsing, and Bullhorn integration.
- **AI Vision OCR**: GPT-4.1-mini vision processes image-based/scanned PDF resumes.
- **Inline-Editable AI Requirements**: Recruiters can directly edit AI-extracted job requirements.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter dashboard displaying AI match results, scores, and qualification status.
- **Scout Screening Quality Auditor**: Background AI audit of screening results with auto-trigger re-vets.
- **Data Sanitization**: NUL-Byte sanitization and AI-Output XSS hardening.
- **Inbound Email Processing**: Multi-layer defense chain for extracting candidate information from job-board email forwards.
- **Automated Duplicate Candidate Merge**: System for merging duplicate candidate records with an audit trail, including AI fuzzy matching.
- **Candidate Data Cleanup**: Scheduled background job for AI-driven extraction of missing emails, re-parsing empty descriptions, and filling missing occupation/title fields.
- **Activity Log**: Super-admin visibility for tracking login history, module usage, email delivery, and active users.
- **Vetting Sandbox**: 5-stage wizard for manually testing the AI vetting pipeline.
- **Scout Support**: AI-powered internal ATS support ticket module with two-tier approval, AI intake, clarification, solution proposals, and Bullhorn API execution.
- **Platform Support**: User feedback creates support tickets with a simplified workflow.
- **Modularized Services**: Key services (Seeding, Vetting Routes, Bullhorn Service, XML Integration Service, Vetting Audit Service, Automation Service, Email Service, Inbound Email Service) are modularized into mixin-based packages.
- **Code Refactoring**: Extensive refactoring of monolith files into modular packages for improved maintainability.
- **Fresh-Prod-DB Guard**: Prevents accidental reseeding of production databases.
- **Phone-Search Trigram Index**: GIN trigram index on normalized phone numbers for efficient substring lookups.
- **Resume Name Hardening**: Multi-layered fix for incorrect name extraction from resumes.
- **Location Review Tier**: Candidates with small location penalties are flagged for recruiter judgment.
- **PandoLogic Note-Based Re-Applicant Detector**: Detects re-applicants via PandoLogic API notes.
- **Prestige Notification Threshold Gate**: Notifies recruiters of prestige boosts only if the boosted score meets qualifying thresholds.
- **Nightly Database Backup**: Automated daily PostgreSQL backup to OneDrive with 30-day retention.
- **API User → Recruiter Ownership Reassignment**: Scheduled task to reassign candidate ownership in Bullhorn from API users to human recruiters. Includes cooldown mechanism to optimize performance. Cooldown is invalidated by EITHER signal: (1) Bullhorn's `Candidate.dateLastModified` newer than the cooldown row's `last_evaluated_at` — closes the 24h blind spot where a recruiter's mid-window status/owner edit used to be ignored; OR (2) a non-API user added a Note to the candidate after `last_evaluated_at` (bug-#4 note-buster, May 2026) — closes the parallel blind spot where Bullhorn does NOT bump `Candidate.dateLastModified` on note-add (Notes are separate entities with their own `dateAdded`). Note-buster runs as a single-shot paginated `search/Note` call per cycle, capped at 10 pages × 200 notes (fail-open). Run History badge semantics: `error` (red) = real candidate-level failures (`failed > 0`), `warning` (amber) = transient upstream issues only (e.g. Bullhorn HTTP 504), `success` (green) = clean run.
- **Screening Human-Owner Skip**: Once a candidate's `owner.id` is NOT in the configured `api_user_ids` (i.e. a human recruiter has taken ownership), the 5-min screening cycle skips them — closes the race where Bullhorn's note search index lagged ~1 min behind a freshly-added recruiter note and caused re-screens of actively-worked candidates. Gated by VettingConfig kill switch `screening_skip_human_owned` (default ON).
- **Owner Reassignment — Concurrency Guard**: Threading lock (`_RUN_LOCK`) prevents overlapping runs of `reassign_api_user_candidates`. If the manual "Run Live Batch", the 5-min scheduled cycle, or the daily sweep overlap, the second caller skips immediately with a log message. Prevents duplicate Bullhorn notes on candidate records.
- **Duplicate Note Cleanup Tool**: General-purpose Automation Hub tool that finds and removes duplicate notes on candidate records. Duplicates are defined as notes with the same author + identical comments text on the same candidate within a configurable time window (default 60 min). Keeps the newest copy, soft-deletes older ones. Supports optional action type filtering (e.g., "Owner Reassignment" only). Has preview (dry-run) mode and live execution mode.
- **Owner Reassignment — Entity Association Note Lookup (Bug #5, May 2026)**: `_find_first_human_interactor` previously queried Bullhorn's `search/Note?query=personReference.id:X` index, which returned `total=0` in production for candidates whose notes were clearly visible in the UI (ownership flip never fired). Switched to the canonical `entity/Candidate/{id}?fields=notes(id,commentingPerson(...),dateAdded,action)` to-many association lookup, which reads the live association table and is robust to whichever linkage (`personReference` for API-created notes vs `candidates` for UI-created notes) the note creator populated. The cooldown buster (`_find_cooldown_busters_via_notes`) now also matches notes via EITHER `personReference.id` OR any `candidates[].id` for the same reason. Also handles both wrapped (`{'data': [...], 'total': N}`) and bare-list association response shapes defensively. Note: `screening/dedup.py::_has_recent_recruiter_activity` uses the same search-index pattern and may share this blind spot — left unchanged for now (failure mode there is silent fallback to "no recent activity," not a missed action).

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL, SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry, Microsoft OneDrive.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini Vision.