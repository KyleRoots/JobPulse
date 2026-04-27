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
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme) and vanilla JavaScript for client-side interactions.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain Architecture**: Supports `app.scoutgenius.ai`, `apply.myticas.com` / `apply.stsigroup.com`, and `support.myticas.com` / `support.stsigroup.com`.
- **Microsoft SSO**: `support.myticas.com` utilizes Microsoft Entra ID (Office 365) single sign-on via OAuth 2.0.

### Technical Implementations
- **Web Framework**: Flask (Python 3.11) with modular route blueprints.
- **Database**: PostgreSQL with SQLAlchemy ORM and Alembic for migrations.
- **Authentication/Authorization**: Flask-Login for user management, including username/email login and password reset, and granular module-based access control.
- **Background Processing**: APScheduler manages automated tasks such as tearsheet monitoring, SFTP uploads, and Scout Vetting.
- **XML Processing**: Custom `lxml` processor for robust data handling.
- **Email Service**: SendGrid for notifications.
- **AI/LLM Integration**: OpenAI GPT-5.4 is central to various functions including candidate vetting, screening, resume formatting, quality auditing, job requirements extraction, duplicate candidate detection, job classification, Scout Support, Scout Prospector research, and occupation/title extraction.
- **Embedding Service**: OpenAI `text-embedding-3-large` is used for similarity-based pre-filtering in candidate-job matching.
- **Error Tracking**: Sentry SDK integration.
- **Screening Engine**: Modular mixin package with an orchestrator, handling AI prompts, Bullhorn note formatting, notifications, and batch-optimized candidate processing using `ThreadPoolExecutor`.
- **Dual XML Feed System**: Generates `myticas-job-feed-v2.xml` and `myticas-job-feed-pando.xml` every 30 minutes.
- **Job Application Forms**: Public-facing forms with multi-brand support, resume parsing, and Bullhorn integration (duplicate detection, profile enrichment).
- **AI Vision OCR**: GPT-4.1-mini vision automatically processes image-based/scanned PDF resumes when text extraction fails.
- **Scout Vetting**: AI-powered candidate screening with embedding pre-filtering, experience-level classification, two-phase scoring, work authorization/security clearance inference, and configurable prompts.
- **Inline-Editable AI Requirements**: Recruiters can directly edit AI-extracted job requirements.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter dashboard displaying AI match results, scores, and qualification status.
- **Scout Screening Quality Auditor**: Background AI audit of both Not-Qualified and Qualified results, with auto-trigger re-vets. Per-(candidate, job) re-vet cap (default 2 in any rolling 24h window, configurable via `auditor_revet_cap_per_24h`) and a score-stability acceptance rule (skip further re-vets when the prior re-vet's new score landed within ±5 points of its original; tolerance configurable via `auditor_revet_score_tolerance`) prevent the auditor from thrashing the same candidate. Each cycle logs how many re-vets were skipped by each rule.
- **NUL-Byte Sanitization**: Shared helper `utils/text_sanitization.sanitize_text()` strips PostgreSQL-incompatible NUL bytes from all text/varchar fields sourced from Bullhorn and OpenAI before persistence.
- **Zero-Score Verification**: Automated re-verification of 0% scores for top 3 jobs using GPT-4.1-mini.
- **AI-Output XSS Hardening**: Platform-wide XSS hardening using `window.AIOutput.escapeHtml` and other safe-render primitives for dynamically generated AI content.
- **Inbound Email Candidate Extraction**: Multi-layer defense chain for extracting candidate information from job-board email forwards.
- **Automated Duplicate Candidate Merge**: System for merging duplicate candidate records with an audit trail.
- **Candidate Data Cleanup**: Scheduled background job for AI-driven extraction of missing emails, re-parsing empty descriptions, and filling missing occupation/title fields.
- **Activity Log**: Super-admin visibility for tracking login history, module usage, email delivery, and active users.
- **Vetting Sandbox**: 5-stage wizard for manually testing the AI vetting pipeline.
- **Scout Support**: AI-powered internal ATS support ticket module with two-tier approval, AI intake, clarification, solution proposals, and Bullhorn API execution.
- **Platform Support**: User feedback creates support tickets with a simplified workflow.
- **Manual Screening Dispatch**: Helper `utils/screening_dispatch.enqueue_vetting_now(reason)` enqueues vetting tasks to run immediately in the background, preventing gunicorn timeouts for large batches.
- **Post-Merge Setup Hook**: `scripts/post-merge.sh` automatically runs `pip install -r requirements.txt` and `alembic upgrade head` after every background task-agent merge to keep the environment in sync.
- **Phone-Search Trigram Index**: Functional GIN trigram index on normalized phone numbers for efficient recruiter phone substring lookups.
- **Dedup Match Discovery — Parallel Email/Phone Search**: `duplicate_merge_service._find_matches_for_candidate` runs the email-search and phone-search paths independently (no longer gated behind "email returned nothing"), with a shared `seen_ids` set deduplicating combined results. The phone search now includes both `phone` and `mobile` numbers as separate query terms when both are ≥10 digits. Closes the historical gap where a duplicate that shared a phone but had a different email was never surfaced.

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

## Future Enhancements (Backlog)

- **Expand Incomplete Candidate Fallback Coverage (priority: short-term)**: The `_builtin_incomplete_rescreen` job (automation_service.py:769) currently only heals candidates sourced from inbound email tearsheet parsing (`ParsedEmail` table, received on/after the Mar 5 2026 cutoff). User asked to broaden coverage to (a) corporate website applicants from the Matador API path, (b) candidates pulled in by recruiters from LinkedIn/other sources, and (c) candidates with no resume file at all (currently counted as `no_resume_file` and skipped — could trigger an auto-outreach request-resume flow). Goal: ensure every Incomplete-status candidate has an automated path back to a real screening result regardless of source. Stack rec when scoped: **Power** (touches automation_service, scheduler, UI toggles, possibly outbound email templates).
- **Keep Platform Age Ceilings Fresh (priority: medium-term)**: `DEFAULT_PLATFORM_AGE_CEILINGS` in `vetting_audit_service.py` is a static dict of platform → max-years used by the Quality Auditor's heuristic pre-checks. Years do not auto-increment; new platforms do not auto-register. Soft failure mode (stale = degraded catch rate, not bad outcomes — auditor only flags, never blocks). User flagged risk of pigeonholing the list. Three implementation options ordered by effort: (1) annual recurring task to bump every entry by +1.0 and append any new platforms from that year — lowest effort, ~90% of staleness solved; (2) quarterly background job using GPT-5.4 to suggest list updates against its training cutoff — preferred automated path since user has unlimited-LLM contract (cost is not a constraint); (3) periodic pull from Wikidata "first released" dates for known platforms — most accurate but more engineering. Stack rec when scoped: **Economy** (option 1 is a one-shot config edit; option 2 is the recommended automated path now that LLM cost is a non-issue). Strategic note: as Scout Genius expands beyond software/tech recruiting into other industrial engineering verticals (mechanical, civil, electrical, etc.), software platform names become a shrinking share of what the auditor evaluates — each new vertical needs its own heuristics (e.g., AutoCAD versions, ASME certifications, FEA tools). Re-scope this work in the broader context of multi-vertical heuristics rather than as a software-list-only refresh.