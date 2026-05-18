# Scout Genius™ - AI-Powered Job Feed Automation Platform

## Overview
Scout Genius is a Flask-based web application designed to automate XML job feed processing and synchronize job listings with Bullhorn ATS/CRM. It provides AI-powered candidate vetting, streamlines application workflows, and enhances recruitment efficiency by maintaining accurate, real-time job listings. The platform aims to be a multi-tenant SaaS solution, transforming recruitment operations through automated job feed generation, Bullhorn integration, and advanced AI-driven candidate screening.

## User Preferences
- **Communication style**: Simple, everyday language.
- **Deployment workflow**: Always confirm deployment requirements at the end of any changes or updates.
- **Development Approval Process**: Before executing any development task, always provide a "stack recommendation" with (a) Autonomy level (Economy/Power), (b) brief rationale. Wait for user approval before proceeding.
- **Task Plans**: Every project task plan must include the recommended autonomy level (Economy/Power) and a one-line rationale at the top.
- **TL;DR Required**: Lead substantial outputs (analysis, recommendations, multi-step plans, build summaries, post-deploy reports) with a TL;DR using the **Problem → Fix → Benefit** pattern (or compact summary equivalent for non-build outputs). TL;DR comes BEFORE the deep-dive.
- **Source of Truth**: GitHub repository (KyleRoots/Scout Genius) — main branch.
- **Dev Admin Credentials**: username=`admin`, password=`MyticasXML2025!`
- **Post-Deploy Checkpoints**: After every production deploy, schedule a 24–48h follow-up health check covering: (1) workflow logs for new errors, (2) AI cost telemetry vs daily threshold, (3) pipeline throughput (vetting logs, matches, parsed_emails), (4) feature-specific success metrics, and (5) any "watch-items" called out in the original deploy summary. Agent must proactively bring this up at the next session.

## Open Watch-Items (clear once resolved)

> **Resolved batches archived to `docs/archive-2026-05.md`**: 2026-05-14 ships (Canadian Clearance, Per-Recruiter Location-Review Toggle, Recruiter Transparency markers, Auditor stuck-row fix), Stuck Revet Rows Bug A + Bug B, and the 2026-05-15 PM ships (Task A shadow killswitch, Task B prompt-cache audit harness, Task #95 recruiter notification ledger, Task #98 L3 cache layout disproved). 2026-05-15 PM ships verified clean at 72h checkpoint (2026-05-18). Full L2 cutover playbook also lives in archive for June re-enable.

### Active — June 2026 Action Items (proactively raise at first session of June)

- **L2 cache cutover re-enable**: Set `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=true` in deployment secrets and republish to resume shadow row accumulation against `cache_optimized` layout. Target: 200+ valid pairs within first 7 days of June, then execute the full 6-criterion cutover playbook in `docs/archive-2026-05.md`. If cutover criteria pass → flip `SCREENING_PROMPT_LAYOUT=cache_optimized` for estimated 15-25% reduction on `screening.scoring` (~$12-20/day, ~$360-600/mo).
- **Billing-vs-telemetry reconciliation audit (1hr)**: Production telemetry currently undercounts actual OpenAI billing by ~6-7x ($668/30d telemetry vs ~$2,300/mo billing). Investigate: (a) outdated pricing in `models/openai_telemetry.py`, (b) un-instrumented call paths (background jobs, direct SDK calls bypassing the wrapper), (c) cached-input token mispricing. Closing this gap unlocks accurate unit-economics for the customer-sizing cost calculator.
- **Task #99 output-token diet** — already on radar from May batch; revisit alongside L2.

### Active — Operational

- **AI cost trend**: Last check **$37.40/24h** (2026-05-18, post-shadow-killswitch, GREEN band — 62% drop from prior $99.23). Top spenders: `screening.scoring` $25.92 (69%), `screening.scoring.shadow` $8.34 (decaying residual, should hit $0 within 24h). 30d run-rate (telemetry) ~$685; billing-true is currently capped at $2,300/mo. **Investigation threshold: $130/24h sustained** — at or above, drill into `screening.scoring`, `screening.scoring.shadow`, and any new top spenders. One-day blip → note and move on.
- **Customer-sizing cost calculator (in progress)**: Unit-economics framework documented in `docs/cost-capacity-model-2026-05.md`. Per-event costs (billing-true with 7x buffer until reconciliation): vetted candidate ~$0.95, match evaluated ~$0.069, job monitored ~$1.90/mo, email parsed ~$0.006. Calculator wiring queued for next session.
- **Bullhorn Search API stale-index pattern (2026-05-15)**: Bullhorn's Search API has a recurring lag vs. its Entity API on tearsheet membership. Two sub-patterns:
  - **Pattern A — short lag on closed jobs (~5-6 min)**: When a job goes `isOpen=False` or is removed via DELETE, Entity API updates immediately but Search API can serve the stale row briefly. Auto-removal handles this correctly when the row stays in Search.
  - **Pattern B — long-lived ghost on `isDeleted=True` jobs (months)**: When a JobOrder is hard-deleted, Bullhorn hides it from global UI search BUT Search API can keep returning it as a tearsheet member indefinitely. **Diagnostic signature**: search bar in Bullhorn UI returns nothing OR returns a same-numbered Company/Candidate; direct `entity/JobOrder/{id}` REST call returns `isDeleted: true`. **Resolution**: targeted `DELETE entity/Tearsheet/{ts}/jobOrders/{id}` clears instantly (no lag — index-only op). Example: tearsheet 1531 / JobOrder 34128 cleared 2026-05-15.
  - **Not a defect on our side**, but two operational side effects: (1) brief feed/Active-Monitors-UI count discrepancy; (2) screening pipeline re-extracts requirements every cycle for any zombie in Search (~$2-4/day per zombie).
  - **Future task candidate** (Economy-tier): defensive cooldown in `incremental_monitoring_service` to skip BOTH DELETE retry + AI requirements re-extraction for jobs auto-removed in the last N hours, plus a Sentry alert for "auto-removal repeat offenders" ≥5 cycles AND a daily diff job that flags any tearsheet where Search count > Entity count for >24h (Pattern B detector).

### Process Rule (Standing)
After every user-feedback-driven deploy batch, the next 24-48h checkpoint must explicitly re-verify each shipped feedback item is still healthy in production — not just internal hardening checks.

## System Architecture

### UI/UX
- **Templates**: Jinja2 with Bootstrap 5 (dark theme) + vanilla JavaScript.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain**: `app.scoutgenius.ai`, `apply.myticas.com` / `apply.stsigroup.com`, `support.myticas.com` / `support.stsigroup.com`.
- **Microsoft SSO**: `support.myticas.com` uses Microsoft Entra ID (Office 365) via OAuth 2.0.

### Stack
- **Web**: Flask (Python 3.11) with modular route blueprints.
- **DB**: PostgreSQL + SQLAlchemy ORM + Alembic.
- **Auth**: Flask-Login with granular module-based access control.
- **Background**: APScheduler (tearsheet monitoring, SFTP uploads, Scout Vetting, nightly DB backup to OneDrive with 30-day retention).
- **XML**: Custom `lxml` processor generating dual XML feeds (V2 + Pando).
- **Email**: SendGrid.
- **AI**: OpenAI GPT-5.4 primary; GPT-4.1-mini for vision OCR + non-critical sites.
- **Embeddings**: OpenAI `text-embedding-3-large` for similarity pre-filtering.
- **Errors**: Sentry SDK.

### Major Subsystems
- **Screening Engine**: Modular mixin package — embedding pre-filtering, experience-level classification, two-phase scoring, work-auth/security-clearance inference, configurable prompts, Bullhorn note formatting.
- **Scout Screening Portal**: Recruiter dashboard for AI match results.
- **Quality Auditor**: Background AI audit with auto-trigger re-vets.
- **Scout Support**: Internal AI-powered ATS support ticket module with two-tier approval and Bullhorn API execution.
- **Platform Support**: User feedback → support tickets (simplified flow).
- **Vetting Sandbox**: 5-stage wizard for manually testing the AI vetting pipeline.
- **Job Application Forms**: Public, multi-brand, with resume parsing + Bullhorn integration.
- **Inbound Email**: Multi-layer defense chain extracting candidate info from job-board email forwards.
- **Duplicate Candidate Merge**: Auto-merge with audit trail + AI fuzzy matching.
- **Candidate Data Cleanup**: AI-driven extraction of missing emails, descriptions, occupation/title.
- **Activity Log**: Super-admin visibility — login history, module usage, email delivery.

### Bullhorn Integration Hardening
- **Tearsheet Auto-Removal**: 5-min cycle removes ineligible (`isOpen=False` or status in INELIGIBLE_STATUSES) jobs via `DELETE entity/Tearsheet/{id}/jobOrders/{job_id}`.
- **API User → Recruiter Ownership Reassignment**: Scheduled reassignment with cooldown + threading lock.
- **Screening Human-Owner Skip**: Once `owner.id` is NOT an API user, screening cycle skips.
- **Screening Recruiter-Activity Gate**: Detects recent recruiter activity via Bullhorn entity-association notes; multi-API-user exclusion.
- **Duplicate Note Cleanup Tool**: Database-wide cleanup of duplicate Bullhorn notes.
- **PandoLogic Note-Based Re-Applicant Detector**.
- **Prestige Notification Threshold Gate**: Only notify on prestige boosts that meet qualifying thresholds.

### Other Hardening
- **Data Sanitization**: NUL-byte sanitization + AI-output XSS hardening.
- **Vetting System Health Monitoring**: Bullhorn, OpenAI, DB, scheduler.
- **Inline-Editable AI Requirements**: Recruiters can directly edit AI-extracted job requirements (with edit-preserving guard against auto-removal/re-add wipe).
- **Location Review Tier**: Small location penalties → flagged for recruiter judgment.
- **Resume Name Hardening**: Multi-layered fix for incorrect name extraction.
- **Fresh-Prod-DB Guard**: Prevents accidental reseeding of production.
- **Phone-Search Trigram Index**: GIN trigram index on normalized phone numbers.
- **Recruiter Notification Ledger**: Durable `(candidate, job, type)` dedupe with 24h window per notification namespace; survives auditor cascade.
- **Shadow Logging Killswitch**: `SHADOW_LOGGING_DISABLED` env (default `'true'`) gates both embedding and scoring shadow paths regardless of legacy flags.

### Code Organization
- **Models Package**: Decomposed monolithic `models.py` into a 10-module `models/` package along clear domain boundaries; backward compatible.
- **Modularized Services**: Seeding, Vetting Routes, Bullhorn Service, XML Integration Service, Vetting Audit Service, Automation Service, Email Service, Inbound Email Service.
- **Database Stats Hygiene**: `ANALYZE` + autovacuum tuning for `candidate_profile_embedding`.

## May 2026 Cost-Optimization & Reliability Batch
Shipped batch targeting 40–50% off the $4,700/mo OpenAI baseline plus operational hardening. As of 2026-05-18, billing-true monthly is ~$2,300 (capped); further L2 cutover in June targets another $360-600/mo reduction. Full original-batch details (Cost Forecaster, Telemetry, Skip Gates, Email Enhancements, Embedding A/B Shadow, Workflow Observability, Scout Support Hardening) archived in **`docs/may-2026-cost-batch.md`**. Capacity model and unit economics in **`docs/cost-capacity-model-2026-05.md`**.

Key admin URLs:
- `/admin/ai-cost` — telemetry dashboard (1h/24h/7d/30d)
- `/admin/ai-cost/forecast` — module-based projection + scenario builder
- `/admin/ai-cost/embedding-ab` — embedding A/B comparison + cutover controls
- `/admin/health` — System Health tiles (`tile_ai_cost_24h`, `tile_skip_gates`)
- `/vetting/settings` — skip-gate config (cooldown, recruiter-decision killswitch)

## External Dependencies

- **Python**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend**: Bootstrap 5, Font Awesome 6.
- **Services**: PostgreSQL, SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry, Microsoft OneDrive.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini (vision + non-critical sites).
