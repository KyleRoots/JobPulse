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
- **XML Processing**: Custom `lxml` processor.
- **Email Service**: SendGrid for notifications.
- **AI/LLM Integration**: OpenAI GPT-5.4 is central to candidate vetting, screening, resume formatting, quality auditing, job requirements extraction, duplicate detection, job classification, Scout Support, Scout Prospector research, and occupation/title extraction.
- **Embedding Service**: OpenAI `text-embedding-3-large` for similarity-based pre-filtering in candidate-job matching.
- **Error Tracking**: Sentry SDK integration.
- **Screening Engine**: Modular mixin package with orchestrator, handling AI prompts, Bullhorn note formatting, notifications, and batch-optimized candidate processing.
- **Dual XML Feed System**: Generates `myticas-job-feed-v2.xml` and `myticas-job-feed-pando.xml` every 30 minutes.
- **Job Application Forms**: Public-facing forms with multi-brand support, resume parsing, and Bullhorn integration.
- **AI Vision OCR**: GPT-4.1-mini vision processes image-based/scanned PDF resumes when text extraction fails.
- **Scout Vetting**: AI-powered candidate screening with embedding pre-filtering, experience-level classification, two-phase scoring, work authorization/security clearance inference, and configurable prompts.
- **Inline-Editable AI Requirements**: Recruiters can directly edit AI-extracted job requirements.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter dashboard displaying AI match results, scores, and qualification status.
- **Scout Screening Quality Auditor**: Background AI audit of both Not-Qualified and Qualified results with auto-trigger re-vets, preventing thrashing via re-vet caps and score-stability acceptance rules.
- **NUL-Byte Sanitization**: Strips PostgreSQL-incompatible NUL bytes from all text/varchar fields.
- **Zero-Score Verification**: Automated re-verification of 0% scores for top 3 jobs using GPT-4.1-mini.
- **AI-Output XSS Hardening**: Platform-wide XSS hardening for dynamically generated AI content.
- **Inbound Email Candidate Extraction**: Multi-layer defense chain for extracting candidate information from job-board email forwards.
- **Automated Duplicate Candidate Merge**: System for merging duplicate candidate records with an audit trail.
- **Candidate Data Cleanup**: Scheduled background job for AI-driven extraction of missing emails, re-parsing empty descriptions, and filling missing occupation/title fields.
- **Activity Log**: Super-admin visibility for tracking login history, module usage, email delivery, and active users.
- **Vetting Sandbox**: 5-stage wizard for manually testing the AI vetting pipeline.
- **Scout Support**: AI-powered internal ATS support ticket module with two-tier approval, AI intake, clarification, solution proposals, and Bullhorn API execution.
- **Platform Support**: User feedback creates support tickets with a simplified workflow.
- **Manual Screening Dispatch**: Helper to enqueue vetting tasks immediately in the background.
- **Post-Merge Setup Hook**: Automatically runs `pip install -r requirements.txt` and `alembic upgrade head` after background task-agent merges.
- **Phone-Search Trigram Index**: Functional GIN trigram index on normalized phone numbers for efficient recruiter phone substring lookups.
- **Dedup Match Discovery — Parallel Email/Phone Search**: Improved duplicate detection by running email and phone searches independently and using a shared `seen_ids` set.
- **Resume Name Hardening — Work-Authorization Blocklist**: Multi-layered fix for incorrect name extraction from resumes, including blocklists for work authorization terms and improved parsing heuristics.
- **AI Fuzzy Duplicate Matcher**: Two-pass deduplication system using email/phone for exact matches and AI embeddings (OpenAI `text-embedding-3-large` and GPT-5.4 confidence scoring) for fuzzy matches. Includes caching, pre-filtering, and a persistent `FuzzyEvaluationQueue` for overflow.
- **Location Review Tier**: When a candidate's technical fit meets or exceeds the match threshold but a small location penalty (≤ 10 pts, hard-coded constant `LOCATION_NEAR_MISS_PENALTY_CAP` in `screening/location_review.py`) knocks the final score below threshold, the candidate is treated as a recruiter judgment call rather than auto-rejected. Generates a distinct `📍 SCOUT SCREENING - LOCATION REVIEW REQUIRED` Bullhorn note (action `Scout Screen - Location Review`) and triggers a recruiter email with subject `📍 Location Review: {candidate} — {tech}% Technical Fit`. Honors the same `send_recruiter_emails` opt-in toggle as Qualified notifications. Forward-only — no backdating of historical records.
- **PandoLogic Note-Based Re-Applicant Detector**: Fourth detection channel (`detect_pandologic_note_candidates` in `screening/detection.py`) that watches for fresh Notes authored by the PandoLogic API CorporateUser. Closes a blind spot in the owner-based detector: when an existing Bullhorn candidate re-applies via PandoLogic, the parent Candidate.owner does NOT flip to "Pandologic API" (only brand-new candidates get that owner), so re-applicants would otherwise fall through every channel (no email forward, no status flip, no owner change). Auto-resolves the PandoLogic CorporateUser ID once via `search/CorporateUser?query=name:"Pandologic API"` and caches it in VettingConfig as `pandologic_api_user_id`. Same dedup + recruiter-activity gate as other detectors.
- **Prestige Notification Threshold Gate**: The +5 prestige bump for candidates at Tier-1 firms (Capgemini, KPMG, etc.) is a courtesy boost. `_send_prestige_review_notification` in `screening/notification.py` now ONLY notifies the recruiter when the bumped final score actually meets or exceeds the qualifying threshold (per-job aware). Candidates whose post-bump score is still below threshold (e.g. 0% raw → 5% bumped) no longer trigger a recruiter email — they are treated as Not-Recommended like any other below-threshold candidate.

### Bullhorn Note Creation — Critical Requirements
- **`personReference`**: Must point to a Person entity (Candidate or ClientContact).
- **Entity-Specific Payloads**: Defines `personReference`, association method, and action type for various entities.
- **To-Many Fields**: `candidates` array can be set during PUT; other arrays require `NoteEntity` to link after creation.
- **Standard Fields**: `commentingPerson` (API user's CorporateUser ID: 1147490), `action` (registered Note Action type), and `isDeleted` (always `False`).

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL (Neon-hosted), SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini Vision.