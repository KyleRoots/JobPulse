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
- **Owner Reassignment Root-Cause Investigation** *(April 30, 2026)*: Confirmed via repo-wide audit (3 independent explorers + targeted recon) that **no Scout Genius code re-stamps Candidate.owner back to the API user** — the only writer of `Candidate.owner` in the entire codebase is `tasks/owner_reassignment.py:1024` itself. No webhook handlers, no salesrep sync (touches ClientCorporation only), no inbound-email path (`email_inbound_service/resume_mixin.py::map_to_bullhorn_fields` deliberately omits owner/userId), no automation mixin sets owner. The "thousands of candidates re-evaluated every 5-min cycle" symptom is a `dateLastModified` surfacing loop on a stable backlog of Pandologic-owned candidates with no recruiter activity yet (so `_find_first_human_interactor` returns None → `no_human_activity` → no-op). Two amplifiers keep bumping their `dateLastModified` so they re-enter the search window: (1) hourly `cleanup_linkedin_source` PATCHing `source` on every LinkedIn-sourced Candidate (author already noted this side-effect at `tasks/bullhorn_maintenance.py:145-147`), and (2) Pandologic itself adding Notes for re-applicants without changing owner (already documented in `screening/detection.py:383-388`, which is why `detect_pandologic_note_candidates` exists). **The cooldown bandage is therefore the architecturally correct fix**, not a workaround — it's a correctness-preserving cache that absorbs unavoidable workload while still picking up candidates the moment a recruiter actually interacts (cleared by `_clear_cooldown_for_candidate` on successful reassign). Optional future perf nit: drop `cleanup_linkedin_source` cadence from hourly to daily to reduce Bullhorn-spend and dateLastModified churn.
- **Owner Reassignment Per-Candidate Cooldown** *(bandage, April 30, 2026)*: A persistent `OwnerReassignmentCooldown` table (BigInteger PK on `candidate_id`, indexed `last_evaluated_at`, `last_outcome`, `evaluation_count`) remembers no-op outcomes (`no_human_activity`, `already_correct`) for a configurable window so the 5-minute cycle stops re-paying the Bullhorn-Notes-search cost on the same Pandologic candidates over and over. Two `VettingConfig` keys drive it: `owner_reassignment_cooldown_enabled` (kill switch, default `'true'`) and `owner_reassignment_cooldown_hours` (window, default `'24'`, clamped to 1–720). Filter is fail-open — any DB error returns an empty set so reassignment never blocks. Flush uses PostgreSQL `INSERT ... ON CONFLICT` to bump `last_evaluated_at` and increment `evaluation_count`; the upsert dedupes by `candidate_id` first (last outcome wins) to avoid PG's `cannot affect row a second time` error. Kill switch fully disables both reads *and* writes — turning it off does not silently populate the table. Successful reassigns clear the candidate's row so the next cycle isn't blocked. `cooldown_skipped` count surfaces in the result dict, the `_write_run_history` summary string, and the run details JSON for operator visibility. `preview_reassign_candidates` reports `in_cooldown` per candidate plus `cooldown_enabled` / `cooldown_window_hours` so the dry-run shows what would be skipped. **Production verification (April 30, 2026):** post-deploy cycle 1 paid the full Bullhorn-Notes-search cost for 4,962 candidates one final time and populated the cooldown table; cycle 3 (next undisrupted cycle) completed in **<1 second** vs. the prior **~15 minute** cycle, confirming the bandage works as designed.
- **Owner Reassignment Cooldown — Visibility & Stale-Connection Hardening** *(April 30, 2026)*: Three small follow-up fixes after cycle-1 production observation surfaced edge cases. (1) **Capture-count-before-flush** — the cooldown flush log used to read `len(cooldown_outcomes)` *after* `_flush_cooldown_outcomes()` had already iterated/mutated the list, producing misleading "recorded 2 no-op outcome(s)" lines while the DB actually got 4,962 rows; now snapshots the count up front. (2) **Stale-connection guard before the cooldown IN-query** — the Bullhorn pagination loop above the cooldown filter can take 30+ seconds and may invalidate the request-scoped session's underlying connection; we now `db.session.remove()` immediately before `_fetch_active_cooldown_ids` so the 4,962-element IN clause runs on a fresh connection (mirrors the pattern at the cooldown flush site and `scheduler_setup.py` long-running jobs). (3) **Explicit early-return when cooldown empties the batch** — when the filter eliminates every candidate, the cycle now short-circuits with a canonical `complete —` log + `_write_run_history` row instead of falling through to the diagnostics block on an empty list (cleaner operator view; the prior fall-through happened to log correctly too, but did unnecessary work). (4) Demoted the "no active cooldown rows touched this batch" log from DEBUG → INFO so operators can confirm the filter ran on cycles where the table happens to be cold.
- **Scout Vetting Pre-Launch Hardening**: Implemented staggered outreach for vetting sessions, a global toggle for Scout Vetting, cross-session availability answer sharing, mid-conversation requirements-change flag, and improved test coverage for inbound replies.
- **Bullhorn Note Creation**: Critical requirements for Bullhorn note creation include `personReference`, entity-specific payloads, handling to-many fields, and standard fields like `commentingPerson`, `action`, and `isDeleted`.

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL, SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry, Microsoft OneDrive.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini Vision.