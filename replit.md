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
- **XML Processing**: Custom `lxml` processor for generating dual XML feeds (`myticas-job-feed-v2.xml` and `myticas-job-feed-pando.xml`) every 30 minutes.
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
- **Seeding Package Layout**: Modularized `seed_database.py` with specific seeding logic moved to the `seeding/` package for better organization and maintainability.
- **Vetting Routes Package Layout**: Modularized `routes/vetting.py` (2,274 → 56 lines) into a thin orchestrator + `routes/vetting_handlers/` package (settings, dispatch, diagnostics, email, job_requirements, embedding_audit) sharing a single `vetting_bp` blueprint. All 30 `vetting.*` endpoint names preserved.
- **Bullhorn Service Package Layout**: Modularized `bullhorn_service.py` (2,610 lines, 58 methods) into a `bullhorn_service/` package composed from focused mixins: `_BullhornCore` (constants + `__init__` + low-level helpers), `AuthMixin` (OAuth/REST auth + connection test), `JobsMixin` (JobOrder retrieval/search/comparison), `TearsheetsMixin` (tearsheet CRUD + member management), `CandidatesMixin` (candidate CRUD, file upload, work history, education), `NotesMixin` (candidate note retrieval/creation), and `EntitiesMixin` (generic CRUD + meta/options/settings). Public import surface preserved (`from bullhorn_service import BullhornService`). `requests` re-exported at package top-level for legacy test patch paths (`mock.patch('bullhorn_service.requests.get')`).
- **XML Integration Service Package Layout**: Modularized `xml_integration_service.py` (2,241 lines, 33 methods) into an `xml_integration_service/` package composed from focused mixins: `_XMLCore` (`__init__` + static LinkedIn recruiter-tag formatters), `MappingMixin` (Bullhorn job → XML field mapping + cleaners + `_format_date` + recruiter extraction), `ValidationMixin` (`_validate_job_data` + `_verify_*` + `_check_if_update_needed` + `_compare_job_fields`), `FileOpsMixin` (`_safe_write_xml`, `sort_xml_jobs_by_date`, `_cleanup_old_backups`, `_clean_extra_whitespace`), `JobsMixin` (`add_job_to_xml`, `update_job_in_xml`, `remove_job_from_xml`, `regenerate_xml_from_jobs`, `perform_comprehensive_field_sync`), and `SyncMixin` (`sync_xml_with_bullhorn_jobs`, `detect_orphaned_jobs`, `remove_orphaned_jobs`). Public import surface preserved (`from xml_integration_service import XMLIntegrationService`).
- **Vetting Audit Service Package Layout**: Modularized `vetting_audit_service.py` (1,428 lines, 12 methods + 268 lines of module-level helpers) into a `vetting_audit_service/` package composed from focused mixins: `_AuditCore` (`__init__`), `OrchestrationMixin` (`run_audit_cycle`, `_fetch_qualified_audit_sample`, `_commit_audit_log`, `_is_duplicate_audit_log_error` [@staticmethod], `_process_candidate_audit`), `RevetMixin` (`_check_revet_caps_and_stability`, `_trigger_revet`), `HeuristicsMixin` (`_run_heuristic_checks`, `_run_false_positive_checks`), `AIAuditMixin` (`_run_ai_audit`), and `NotificationMixin` (`_send_audit_summary_email`). Module-level constants (`DEFAULT_PLATFORM_AGE_CEILINGS`, `DEFAULT_AUDITOR_MODEL`, `DEFAULT_QUALIFIED_SAMPLE_RATE`, `DEFAULT_REVET_CAP_PER_24H`, `DEFAULT_REVET_SCORE_TOLERANCE`, `PLATFORM_AGE_CEILINGS`, `DOMAIN_KEYWORDS`) and module-level functions (`get_platform_age_ceilings`, `get_auditor_model`, `backfill_revet_new_score`, `get_qualified_sample_rate`, `get_revet_cap_per_24h`, `get_revet_score_tolerance`) live in `vetting_audit_service/helpers.py`. Each mixin imports only the helpers it actually uses to keep dependencies explicit. Public import surface preserved (`from vetting_audit_service import VettingAuditService, backfill_revet_new_score, DEFAULT_PLATFORM_AGE_CEILINGS, ...`); all 5 callers (`scheduler_setup.py`, `candidate_vetting_service.py`, `seeding/settings.py`, `tests/test_quality_auditor.py`, `automation_service/matching_mixin.py`) and the 111-test `tests/test_quality_auditor.py` regression suite verified working.
- **Automation Service Package Layout**: Modularized `automation_service.py` (1,839 lines, 34 methods) into an `automation_service/` package composed from focused mixins: `_AutomationCore` (`__init__`, lazy `bullhorn` property, `_bh_headers`, `_bh_url`), `TasksMixin` (CRUD on AutomationTask/AutomationLog: `get_all_logs`, `get_tasks`, `get_task`, `get_task_logs`, `delete_task`, `update_task_status`), `DispatchMixin` (entry points `run_builtin`, `run_builtin_background` + shared Bullhorn helpers `_get_recent_candidates`, `_get_candidate_entity_notes`, `_soft_delete_note`), `NotesMixin` (`AI_ACTION_PATTERNS` class attr + `_builtin_cleanup_ai_notes`, `_builtin_cleanup_duplicate_notes`), `MatchingMixin` (`_builtin_find_zero_match`, `_builtin_export_qualified`, `_builtin_incomplete_rescreen`, `_builtin_salesrep_sync`, `_builtin_update_field_bulk`, `_builtin_screening_audit`), `ResumeMixin` (`_GARBLED_PATTERNS` class attr + `_is_garbled_description`, `_builtin_resume_reparser`, `_download_and_extract_text`, `_plain_text_to_html`, `_builtin_email_extractor`, `_builtin_occupation_extractor`, `_download_and_extract_resume_raw_text`, `_extract_title_from_resume_text`), and `NotificationsMixin` (`_builtin_retry_recruiter_notifications`, `_builtin_duplicate_merge_scan`, `_send_bulk_scan_notification`). Module-level constants (`LONG_RUNNING_BUILTINS`, `NO_BULLHORN_BUILTINS`) live in `automation_service/constants.py` so `DispatchMixin` can import them directly without a circular dependency on `__init__.py`. Public import surface preserved (`from automation_service import AutomationService, LONG_RUNNING_BUILTINS, NO_BULLHORN_BUILTINS`).
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