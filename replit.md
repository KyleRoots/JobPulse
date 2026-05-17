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

> **Resolved batches archived to `docs/archive-2026-05.md`**: 2026-05-14 ships (Canadian Clearance, Per-Recruiter Location-Review Toggle, Recruiter Transparency markers, Auditor stuck-row fix) and Stuck Revet Rows Bug A + Bug B. All verified clean by 2026-05-15 PM (100% resolution rate on the revet cluster). Canadian-clearance verification was routed to recruiter-inbox feedback + auditor dashboard rather than building SQL-queryable JSON instrumentation (low ROI).

### Active — 2026-05-15 PM ships (24-48h verification window)

**Task A — Scoring shadow killswitch (Economy, shipped 2026-05-15 PM)** — 2 files:
- `embedding_service.py::_shadow_enabled()` and `screening/prompt_builder.py::_shadow_screening_enabled()` — both now check env `SHADOW_LOGGING_DISABLED` (default `'true'`). When disabled (default), both shadow paths are off regardless of legacy `EMBEDDING_AB_SHADOW_ENABLED` / `SCREENING_AB_SHADOW_ENABLED` flags. Embedding A/B reached 55,961 comparisons (85.1% agreement, 0.7% false-neg) and scoring shadow accumulated enough data — further accumulation was just $278/mo of cost. To restore for periodic regression checks: `SHADOW_LOGGING_DISABLED=false` in deployment secrets.
- **Watch-items (24-48h)**: (1) verify `screening.scoring.shadow` cost line drops to ~$0/24h on `/admin/ai-cost`. Query: `SELECT site_id, SUM(estimated_cost_usd) FROM openai_call_log WHERE created_at > NOW() - INTERVAL '24 hours' AND site_id IN ('screening.scoring.shadow','embedding.shadow') GROUP BY site_id;` — expect zero rows or trivial trailing volume. (2) total 24h cost should drop ~$9-10/day (`screening.scoring.shadow` was $9.11/24h pre-deploy).

**Task B — Prompt-cache audit harness (Power, shipped 2026-05-15 PM)** — 1 file:
- `screening/prompt_builder.py` — added `build_scoring_user_prompt(layout=...)` with two semantically-equivalent layouts: `legacy` (date at start — current production) and `cache_optimized` (date at end, location_instruction moved AFTER stable JOB DETAILS, so the cacheable prefix grows from system_message-only [~3K tokens] to system_message + per-job content [~4.5K tokens]). Active layout chosen by env `SCREENING_PROMPT_LAYOUT` (default `legacy` — production unchanged). Cache-audit shadow gated by env `SCREENING_PROMPT_CACHE_AUDIT_ENABLED` (default off, independent of `SHADOW_LOGGING_DISABLED`); when on, fires a fail-soft same-model alt-layout call and logs to existing `screening_ab_log` with `prod_model`/`shadow_model` tagged `{model}|{layout}` (e.g., `gpt-5.4|legacy` vs `gpt-5.4|cache_optimized`) so audit rows are distinguishable from model-A/B rows.

**Task #95 — Recruiter notification ledger (Economy, merged 2026-05-15 PM)** — 5 files:
- `models/vetting.py` + `models/__init__.py` — new `RecruiterNotificationLedger` table living OUTSIDE the auditor cascade, keyed on `(bullhorn_candidate_id, bullhorn_job_id, notification_type)` with a 24h dedupe window per type (qualified / prestige / location_review get separate namespaces).
- `alembic/versions/s3m4n5o6p7q8_add_recruiter_notification_ledger.py` — migration creating the table + indexes (chained off `r2l3m4n5o6p7`). Already applied — deployment startup logs confirm all columns exist (seeding.migrations auto-handles on boot).
- `screening/notification.py` — three module-level helpers (`_ledger_recently_sent_pairs`, `_record_ledger_sent`, `_filter_matches_by_ledger`) applied at all three send sites (`send_recruiter_notifications`, `_send_prestige_review_notification`, `_send_location_review_notification`). Suppressed matches still get `notification_sent=True` so the regular flag-based dedupe continues working in subsequent cycles. Emits `event=recruiter_email_suppressed_already_sent` log marker.
- Root cause: auditor's `clear_candidate_vetting_state` cascade (Bug A fix) deletes `CandidateJobMatch` rows that carried `notification_sent=True`; next cycle creates fresh matches with `notification_sent=False`; note path correctly de-dupes via Bullhorn-side check, but email path had no equivalent durable check. Symptom: Justin Chuang 4660264 received two "Qualified Candidate Match" emails minutes apart, only one Bullhorn note.
- Tests: 9 new tests pass (`tests/test_recruiter_notification_dedupe.py`); 18 existing email-enhancement tests + 5 email-dedup tests still pass.
- **Watch-items (24-48h)**: (1) zero duplicate qualified/prestige/location_review emails within 24h for any (candidate, job) pair. Recruiter inbox = primary signal; secondary check via log markers. Query: `SELECT COUNT(*), notification_type FROM recruiter_notification_ledger WHERE created_at > NOW() - INTERVAL '24 hours' GROUP BY notification_type;` — should grow steadily. (2) `event=recruiter_email_suppressed_already_sent` markers should appear when re-vets occur (proves the guard is firing). Grep `/tmp/logs/Start_application_*.log` or production log stream. (3) zero recruiter complaints about *missing* emails on legitimately new (candidate, job) pairings — namespace-per-type ensures a prestige email doesn't suppress a qualified email.

**Validation playbook for cache_optimized cutover** (DO NOT skip — prompt reordering can subtly affect AI scoring):
1. Enable audit on staging or for a 24-48h prod window: set `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=true` (leave `SCREENING_PROMPT_LAYOUT=legacy` so prod is unaffected).
2. After 24h target ≥200 **valid** comparison rows (shadow call succeeded). Query: `SELECT COUNT(*) AS valid_pairs, AVG(score_delta) AS mean_delta, STDDEV(score_delta) AS stddev_delta, AVG(ABS(score_delta)) AS mean_abs_delta FROM screening_ab_log WHERE prod_model LIKE '%|legacy' AND shadow_model LIKE '%|cache_optimized' AND shadow_error IS NULL AND shadow_score IS NOT NULL AND created_at > NOW() - INTERVAL '48 hours';` Also check shadow-error rate (fail-soft expectation): `SELECT COUNT(*) FILTER (WHERE shadow_error IS NOT NULL)*100.0/COUNT(*) AS err_pct FROM screening_ab_log WHERE prod_model LIKE '%|legacy' AND shadow_model LIKE '%|cache_optimized' AND created_at > NOW() - INTERVAL '48 hours';` — investigate if >5%.
3. **Cutover criteria** (ALL must pass):
   - Mean `score_delta` within ±2 points (no systematic bias).
   - Stddev `score_delta` ≤ 5 points (low noise from reordering).
   - `qualified_inferred` flip rate ≤ 3% (≤6 of 200 candidates change qualified/not status).
   - Zero rows where prod_qualified=true and shadow_qualified_inferred=false on candidates with prod_score ≥ 90 (no high-confidence regressions).
   - Audit cache-hit telemetry (existing `💰 Cache:` log lines): cache_optimized arm shows ≥70% cache-hit on 2nd+ call within same job batch (vs current 43.8% baseline).
   - **Location-specific regression check** (since location_instruction is the only semantic-position move): pull deltas for cross-country pairings and remote-vs-onsite mixes. Query: `SELECT job_title, prod_score, shadow_score, score_delta, bullhorn_candidate_id, bullhorn_job_id FROM screening_ab_log WHERE prod_model LIKE '%|legacy' AND shadow_model LIKE '%|cache_optimized' AND shadow_error IS NULL AND ABS(score_delta) >= 10 ORDER BY ABS(score_delta) DESC LIMIT 30;` — manually inspect ≥10pt deltas for location-driven divergence; require zero unexplained location-flip cases (e.g., legacy=remote-OK shadow=remote-penalized).
4. If criteria met → `SCREENING_PROMPT_LAYOUT=cache_optimized` + leave audit on for 7d post-cutover (now `prod=cache_optimized` vs `shadow=legacy` for inverse confirmation).
5. If criteria fail → keep legacy, document the failure mode, consider whether the `location_instruction` ordering specifically caused drift (it's the only semantic-position change; date moved to end is purely lexical).
6. Disable audit when done: `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=false`.
- **Watch-items (24-48h)**: (1) confirm zero impact on prod scoring while audit is OFF (default). (2) once audit is enabled for validation, confirm shadow rows accumulate and `shadow_error` rate stays <2%. (3) post-cutover, confirm `screening.scoring` cost drops 15-25% from improved cache hit rate (~$12-20/day savings if hits go 44%→70%).

**Task #98 L3 — requirements_extract cache layout (Economy, shipped 2026-05-15 PM)** — 1 file:
- `screening/prompt_builder.py::extract_job_requirements` — moved all static instruction content (focus areas, anti-hallucination rules, format spec, exclusions) BEFORE variable per-job content (`JOB TITLE` + `JOB DESCRIPTION`). Added anti-injection footer. 11/11 tests pass. Deployed to `app.scoutgenius.ai` 2026-05-15 PM.
- **Watch-items (24-48h)**: (1) `screening.requirements_extract` cache hit rate climbs from 0% → 30%+ in `openai_call_log`. Query: `SELECT COUNT(*) AS calls, SUM(cached_input_tokens) AS cached_tokens, SUM(prompt_tokens) AS total_prompt_tokens, ROUND(SUM(cached_input_tokens)::numeric / NULLIF(SUM(prompt_tokens), 0) * 100, 1) AS cache_hit_pct FROM openai_call_log WHERE site_id = 'screening.requirements_extract' AND created_at > NOW() - INTERVAL '24 hours';` — expect `cache_hit_pct` ≥ 30. (2) `requirements_extract` cost per call should drop proportionally — check `/admin/ai-cost` 24h vs prior baseline. (3) Zero regressions in extracted requirements quality — recruiter feedback or auditor re-vet rate as secondary signal.

**Task #98 L2 — screening.scoring cache cutover audit (Power, audit PAUSED 2026-05-17)** — env flag only:
- `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=false` (set 2026-05-17 PM in Replit deployment secrets to stop duplicate scoring calls after OpenAI $2K monthly cap was hit). The L2 cutover validation was paused after only ~2 days of partial data accumulation — insufficient to meet the 200-valid-pair threshold required by the 6-criterion cutover playbook above.
- **JUNE 2026 ACTION ITEM (PROACTIVELY RAISE AT FIRST SESSION OF JUNE)**: Re-enable `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=true` in deployment secrets and republish, to resume shadow row accumulation against the cache_optimized layout. Target: 200+ valid pairs within first 7 days of June, then execute the full 6-criterion cutover playbook. If cutover criteria pass → flip `SCREENING_PROMPT_LAYOUT=cache_optimized` for the estimated 15-25% cost reduction on `screening.scoring` (~$12-20/day savings at current throughput).
- **Prior state (May 15-17, kept for context)**: `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=true` was set in Replit deployment secrets. Shadow comparison rows accumulated in `screening_ab_log` tagged `{model}|legacy` vs `{model}|cache_optimized`. Prod scoring was unaffected (`SCREENING_PROMPT_LAYOUT` left at default `legacy`).
- **Watch-items (24-48h)**: (1) shadow rows accumulating — Query: `SELECT COUNT(*) AS pairs, AVG(score_delta) AS mean_delta, STDDEV(score_delta) AS stddev_delta FROM screening_ab_log WHERE prod_model LIKE '%|legacy' AND shadow_model LIKE '%|cache_optimized' AND created_at > NOW() - INTERVAL '24 hours';` — expect pairs growing, mean_delta near 0. (2) shadow_error rate: `SELECT COUNT(*) FILTER (WHERE shadow_error IS NOT NULL)*100.0/NULLIF(COUNT(*),0) AS err_pct FROM screening_ab_log WHERE shadow_model LIKE '%|cache_optimized' AND created_at > NOW() - INTERVAL '24 hours';` — investigate if >5%. (3) At ≥200 valid pairs, run the full 6-criterion cutover playbook (above). If all pass → set `SCREENING_PROMPT_LAYOUT=cache_optimized`.
- **Note**: If shadow rows are NOT accumulating after 24h, confirm `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=true` is set correctly in Replit deployment secrets and the deployment restarted with it active.

### Active — Operational
- **AI cost trend**: Last check **$99.23/24h** (2026-05-15 PM checkpoint, GREEN band, baseline $80 green / $200 red, $4,700/mo target). Down from $112.82 prior check; flat vs $98.23 day-prior (+1%). 30d run-rate ~$2,977. Top spenders: `screening.scoring` $83.48 (84%), `screening.scoring.shadow` $9.64 (10% — holding stable). **Investigation threshold: $130/24h sustained** — at or above, drill into `screening.scoring`, `screening.scoring.shadow`, and any new top spenders. One-day blip → note and move on.
- **Bullhorn Search API stale-index pattern (2026-05-15)**: Bullhorn's Search API has a recurring lag vs. its Entity API on tearsheet membership. Two distinct sub-patterns observed:
  - **Pattern A — short lag on closed jobs (~5-6 min)**: When a job goes `isOpen=False` or is removed via DELETE, Entity API updates immediately but Search API can serve the stale row briefly. Example: tearsheet 1231 jobs 34629 + 34952 (resolved by recruiter removal ~12:10 UTC; Search API caught up by 12:16). Auto-removal handles this correctly when the row stays in Search.
  - **Pattern B — long-lived ghost on `isDeleted=True` jobs (months)**: When a JobOrder is hard-deleted (`isDeleted=True`), Bullhorn hides it from global UI search BUT the Search API can keep returning it as a tearsheet member indefinitely until something forces a reindex. Example: tearsheet 1531 STSI ghost was JobOrder 34128 ("Southeast Regional Sales Representative", company 32671 Advanced Food Equipment, owner Tray Prewitt, deleted ~2026-01-27). **Diagnostic signature**: search bar in Bullhorn UI returns nothing OR returns a same-numbered Company/Candidate (different entity namespace); direct `entity/JobOrder/{id}` REST call returns `isDeleted: true`. **Resolution**: targeted `DELETE entity/Tearsheet/{ts}/jobOrders/{id}` clears it instantly (no lag — index-only op). Done 2026-05-15 ~12:55 UTC for 1531/34128.
  - **Not a defect on our side**, but two operational side effects: (1) brief feed/Active-Monitors-UI count discrepancy; (2) screening pipeline re-extracts requirements every cycle for any zombie in Search (~$2-4/day per zombie at gpt-5.4 rates).
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

### Code Organization
- **Models Package**: Decomposed monolithic `models.py` into a 10-module `models/` package along clear domain boundaries; backward compatible.
- **Modularized Services**: Seeding, Vetting Routes, Bullhorn Service, XML Integration Service, Vetting Audit Service, Automation Service, Email Service, Inbound Email Service.
- **Database Stats Hygiene**: `ANALYZE` + autovacuum tuning for `candidate_profile_embedding`.

## May 2026 Cost-Optimization & Reliability Batch
Shipped batch targeting 40–50% off the $4,700/mo OpenAI baseline plus operational hardening. Full details (Cost Forecaster, Telemetry, Skip Gates, Email Enhancements, Embedding A/B Shadow, Workflow Observability, Scout Support Hardening) archived in **`docs/may-2026-cost-batch.md`**.

Key admin URLs from that batch:
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
