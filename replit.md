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
- **AI/LLM Integration**: OpenAI GPT-5.4 for candidate vetting, screening, resume formatting, quality auditing, job requirements extraction, duplicate detection, job classification, support, research, and occupation/title extraction.
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
- **Fresh-Prod-DB Guard**: Prevents accidental reseeding of production databases.
- **Phone-Search Trigram Index**: GIN trigram index on normalized phone numbers for efficient substring lookups.
- **Resume Name Hardening**: Multi-layered fix for incorrect name extraction from resumes.
- **Location Review Tier**: Candidates with small location penalties are flagged for recruiter judgment.
- **PandoLogic Note-Based Re-Applicant Detector**: Detects re-applicants via PandoLogic API notes.
- **Prestige Notification Threshold Gate**: Notifies recruiters of prestige boosts only if the boosted score meets qualifying thresholds.
- **Nightly Database Backup**: Automated daily PostgreSQL backup to OneDrive with 30-day retention.
- **API User → Recruiter Ownership Reassignment**: Scheduled task to reassign candidate ownership in Bullhorn from API users to human recruiters. Includes cooldown mechanism.
- **Screening Human-Owner Skip**: Once a candidate's `owner.id` is NOT an API user, the screening cycle skips them.
- **Owner Reassignment — Concurrency Guard**: Threading lock prevents overlapping runs of `reassign_api_user_candidates`.
- **Duplicate Note Cleanup Tool**: Database-wide tool to find and remove duplicate Bullhorn notes on candidate records.
- **Owner Reassignment — Entity Association Note Lookup**: Improved lookup for human interactor notes using Bullhorn's entity association for reliability.
- **Screening Recruiter-Activity Gate — Entity Association Note Lookup**: Enhanced detection of recent recruiter activity to prevent re-screening of actively worked candidates.
- **Screening Recruiter-Activity Gate — Multi-API-User Exclusion**: Corrected logic to exclude notes from various API users when determining human recruiter activity.
- **Module-Based AI Cost Forecaster (May 2026)**: New `services/cost_forecaster.py` defines a 9-module map (Inbound, Screening, Vetting, Scout Support, Search/Recruit/Prospector, Job Automation, Resume Parsing, Fuzzy Duplicate Detection, Embeddings) with a `primary_site` per module representing one logical unit of work (one parsed candidate, one screened candidate, one vetting session, etc.). `derive_unit_costs(window_days)` computes per-module USD-per-unit by summing cost across all module sites and dividing by primary-site call count over the window; modules with <30 primary calls are flagged `low` confidence and modules with 0 primary calls are flagged `insufficient_data`. `project_monthly_cost(active_modules, unit_costs, overrides)` returns per-module + total + annual projection. New super-admin page at `/admin/ai-cost/forecast` provides a form-driven scenario builder (toggle modules + set monthly volume → projected cost), inline manual unit-cost overrides for modules without enough telemetry (e.g. Screening during cost-recuperation windows), persistent override storage in `cost_forecast_override`, and named saved scenarios in `cost_forecast_scenario` for one-click recall ("Customer A profile", "Internal current pace", etc.). Powers internal cost-to-serve modeling and future per-customer pricing conversations. 17 new tests + Alembic migration `o9i0j1k2l3m4`.
- **AI Cost Telemetry + Phase 1 Downgrades (May 2026)**: New `services/openai_helper.py` exposes `resolve_model(site_id, default)` (per-site `MODEL_TIER_OVERRIDE_<SITE>` env override, dot/dash → underscore normalization) and fire-and-forget `log_call(site_id, model, response, ...)` that records every OpenAI invocation to `openai_call_log` (model, input/output/cached tokens, estimated USD cost from a central PRICING table, optional entity attribution) on a daemon thread inside a short-lived app context — never raises. All 36 OpenAI call sites instrumented. Phase 1 downgrades: 13 non-critical sites flipped to default `gpt-4.1-mini` (job_classification, email_inbound resume_parse + dedup_validate, resume_parser format_html, scout_support platform_reply + classify_reply + admin_handling_intent + platform_intake + failure_analysis, automation title_extract, screening years_recheck, scout_prospector refine, fuzzy_duplicate_matcher); flagship sites kept on `gpt-5.4` (scout_vetting questions/reply_intent/outcome/followup_email; scout_support understanding/clarification/retry/admin_question/admin_refine/draft_generation/reopen_analysis; screening requirements_extract/zero_recheck/scoring; scout_screening optimize_reqs; scout_prospector web_search). Vision OCR consolidated to `gpt-4.1-mini` only. New super-admin dashboard at `/admin/ai-cost` with 1h/24h/7d/30d windows showing per-site spend breakdown; new `tile_ai_cost_24h` on the System Health dashboard with green/amber/red thresholds at $80/$200 daily spend. Targets ~$1,000/mo savings against the prior $4,700/mo OpenAI bill.
- **Scout Support / Quality Auditor — Hardening Batch (May 2026)**: Two-layer concurrency guard on ticket execution — in-process RLock + Postgres advisory lock — prevents duplicate Bullhorn writes across all gunicorn workers (C1); transactional ticket-deletion with rollback (C3); exponential-backoff retry on transient 5xx/timeout for Bullhorn entity updates and note creation (C4); Postgres advisory lock around `initiate_vetting` active-session count to prevent over-cap races (C6); hardened user-reply commit, clarification null-analysis logging, ticket-number race retry, 10K-char reply length cap, distinct `api_failure` audit finding type, unbiased `random.sample` audit pool draw, `revet_skipped_stable` back-fill in pending lookups, and full audit trail of vetting-settings changes (I1–I8).

### Code Organization
- **Models Package Split**: The monolithic `models.py` was decomposed into a 10-module `models/` package along clear domain boundaries, with backward compatibility.
- **Database Stats Hygiene**: Implemented `ANALYZE` and autovacuum tuning for `candidate_profile_embedding` to ensure accurate query planning.

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL, SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry, Microsoft OneDrive.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini Vision.