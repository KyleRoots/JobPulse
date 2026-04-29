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
- **Modularized Services**: Key services like Seeding, Vetting Routes, Bullhorn Service, XML Integration Service, Vetting Audit Service, Automation Service, Email Service, and Inbound Email Service are modularized into mixin-based packages for better organization and maintainability.
- **Tasks Package**: Scheduled task functions split from a 1,561-line monolith (`tasks.py`) into `tasks/` package with 5 domain modules: `monitoring.py` (health checks & alerts), `cleanup.py` (retention & timeout cleanup), `xml_feeds.py` (XML generation, SFTP upload, change monitor), `vetting.py` (AI vetting cycle & requirements maintenance), `bullhorn_maintenance.py` (LinkedIn cleanup, tearsheet enforcement, scheduler). All 17 public functions re-exported from `tasks/__init__.py` preserving the existing import surface.
- **Screening Prompt Builder Package**: The 1,578-line `screening/prompt_builder.py` monolith split into 4 focused modules: `screening/prestige.py` (~52L, prestige employer constants & detection), `screening/system_prompt.py` (~447L, system message & location prompt construction), `screening/post_processing.py` (~482L, defense-in-depth post-processing gates), and the slimmed `screening/prompt_builder.py` (~480L, PromptBuilderMixin with 5 methods requiring `self.openai_client`). All imports re-exported for backward compatibility.
- **Screening Detection Package**: The 1,324-line `screening/detection.py` monolith split into 3 focused modules: `screening/dedup.py` (~268L, CandidateDeduplicationMixin with job-aware dedup & recruiter-activity gating), `screening/candidate_data.py` (~375L, CandidateDataAccessMixin with Bullhorn data fetching, resume handling, and `_resolve_vetting_cutoff`), and the slimmed `screening/detection.py` (~647L, CandidateDetectionMixin inheriting from both sub-mixins with 5 detect_* source methods). All imports re-exported for backward compatibility.
- **Candidate Vetting Service Package**: The 1,318-line `candidate_vetting_service.py` monolith converted to `candidate_vetting_service/` package with 5 modules: `config.py` (~176L, VettingConfigMixin with config/threshold/model/timestamp helpers), `locking.py` (~71L, VettingLockMixin with exclusive lock management), `processing.py` (~654L, CandidateProcessingMixin with the full single-candidate pipeline), `cycle.py` (~244L, VettingCycleMixin with multi-source detection and parallel processing orchestration), and `__init__.py` (~85L, CandidateVettingService class definition composing all mixins). All existing imports (`CandidateVettingService`, `map_work_type`, model names) re-exported for backward compatibility.
- **XML Routes Package**: The 1,296-line `routes/xml_routes.py` monolith converted to `routes/xml_routes/` package with 4 modules: `__init__.py` (~20L, blueprint creation, admin guard, shared constants), `feed_ops.py` (~524L, XML upload/download/validate/reference-refresh routes), `automation.py` (~258L, automation status and manual SFTP upload routes), `test_center.py` (~514L, automation test center routes and demo helper functions). All 13 routes registered on the same `xml_routes_bp` blueprint. Security fix: added missing `@login_required` to `automation_test_action` POST route.
- **Fresh-Prod-DB Guard**: Prevents accidental reseeding of production databases by halting boot if an empty database is detected.
- **Phone-Search Trigram Index**: GIN trigram index on normalized phone numbers for efficient substring lookups.
- **Resume Name Hardening**: Multi-layered fix for incorrect name extraction from resumes, including blocklists for work authorization terms.
- **Location Review Tier**: Candidates with a small location penalty are flagged for recruiter judgment rather than auto-rejected, triggering specific Bullhorn notes and emails.
- **PandoLogic Note-Based Re-Applicant Detector**: Detects re-applicants via PandoLogic API notes to improve deduplication.
- **Prestige Notification Threshold Gate**: Only notifies recruiters of prestige boosts if the boosted score meets the qualifying threshold.
- **Nightly Database Backup**: Automated daily PostgreSQL backup to OneDrive with 30-day retention, failure alerts, and an admin dashboard.

### Bullhorn Note Creation — Critical Requirements
- **`personReference`**: Must point to a Person entity (Candidate or ClientContact).
- **Entity-Specific Payloads**: Defines `personReference`, association method, and action type for various entities.
- **To-Many Fields**: `candidates` array can be set during PUT; other arrays require `NoteEntity` to link after creation.
- **Standard Fields**: `commentingPerson` (API user's CorporateUser ID: 1147490), `action` (registered Note Action type), and `isDeleted` (always `False`).

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL (Replit-hosted), SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry, Microsoft OneDrive (for backups).
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini Vision.