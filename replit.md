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
Post-Deploy Checkpoints: After every production deploy, schedule a 24–48h follow-up health check covering: (1) workflow logs for new errors, (2) AI cost telemetry vs daily threshold, (3) pipeline throughput (vetting logs, matches, parsed_emails), (4) feature-specific success metrics (e.g. stuck-row counts for the May 2026 auditor fix), and (5) any "watch-items" called out in the original deploy summary. The agent must proactively bring this up at the next session rather than waiting for the user to ask.

## Open Watch-Items (clear once resolved)
- **2026-05-15 follow-up — Canadian Clearance Inference Enforcement (shipped 2026-05-14)**: Three-file change adding structural enforcement to RULE 2 of `config/global_screening_prompt.txt` (which already defined 5/10/15 yr tiers for Reliability/Secret/Top Secret but wasn't being reliably applied). Touched: `screening/system_prompt.py` (new CANADIAN SECURITY CLEARANCE EVIDENCE EXTRACTION block + mandatory `canadian_clearance_analysis` JSON section), `vetting_audit_service/ai_audit_mixin.py` (auditor distinguishes hard-fail vs inference-eligible; cites exact thresholds), `screening/prompt_builder.py` (fixed truncation bug — zero-recheck path was sending only first 500 chars of 8.4k-char global prompt, silently stripping RULE 2). User-trigger: ALEX RYBIN (Toronto) penalized for missing Enhanced Reliability on Job #34761. At 24h post-deploy verify: (1) `canadian_clearance_analysis.triggered=true` block present in scoring JSON for at least one real candidate on a clearance-required job; (2) `score_adjustment` field shows "No penalty applied" for candidates meeting the tier threshold; (3) zero recruiter complaints about good Canadian candidates being downgraded for "missing clearance"; (4) auditor revet-recommendation rate on clearance jobs drops vs prior baseline (no more counter-flagging of RULE 2-compliant scores). Production query: `SELECT match_score, ai_response::jsonb->'canadian_clearance_analysis'->>'triggered', ai_response::jsonb->'canadian_clearance_analysis'->>'score_adjustment' FROM candidate_match WHERE created_at > '2026-05-14 18:00' AND ai_response::jsonb->'canadian_clearance_analysis'->>'triggered' = 'true' ORDER BY created_at DESC LIMIT 20;` Optional follow-up (deferred): regression tests for Secret/Top Secret tiers + zero-recheck full-prompt assertion.

- **2026-05-15 follow-up — Per-Recruiter Location-Review Toggle (shipped 2026-05-14)**: New model `RecruiterNotificationPref` + toggle column on `/scout-screening` "Job-Level Settings" table + filter in `screening/notification.py` `_send_location_review_notification`. Default behavior ON; only explicit OFF rows persist. At 24h post-deploy verify: (1) any `event=notification_pref_updated` markers in production logs (recruiters trying it out); (2) any `event=location_review_pref_filtered` markers (filter actually engaged on a real candidate); (3) zero recruiter complaints about missing Location-Review emails on jobs they did NOT opt out of (regression check). Production query: `SELECT COUNT(*), enabled FROM recruiter_notification_pref WHERE notification_type='location_review' GROUP BY enabled;`

- **2026-05-15 follow-up — stuck revet rows**: Of the original 6 `revet_triggered` rows from 2026-05-13/14, 2 self-resolved (5833 → new_score 18; 6297 → new_score 68) at the first post-deploy check on 2026-05-14. Remaining 4 to verify:
  - **ID 5918** (cand 4659539, original score 56, ~20h old at last check) — closest to 24h SLA, primary concern; if still null past 24h = edge case the new guard missed
  - **ID 6163** (cand 4660006, score 61, ~7h old) — still in window
  - **IDs 6242, 6251** (cands 4660050, 4660054, ~1.5–1.8h old) — recent, likely fine
  Goal: each row either has `revet_new_score` populated OR is reclassified to `revet_skipped_pre_cutoff`.
- **2026-05-15 follow-up — AI cost trend**: 24h spend was **$112.82** at the 2026-05-14 post-deploy check (amber band on System Health tile, baseline $80 green / $200 red). Still ~28% off the $4,700/mo target so not urgent, but trending up from the prior day's $109. **Threshold to investigate: $130/24h sustained** — if tomorrow's check shows ≥ $130 it warrants a per-site drill-down on `screening.scoring`, `screening.scoring.shadow`, and any new top spenders to identify the driver. If $130 is a one-day blip, just note and move on.
- **2026-05-15 follow-up — Recruiter Transparency batch markers**: At first post-deploy check there were 0 qualified matches (≥75%) in the prior 15min so the new `📌 Applied-job context` and `📎 Multi-recruiter resume` log markers had not yet fired. Validate at 24h that they appear in production logs on real candidates, and capture frequency of the "WITHOUT resume attachment" warning — that's the data we deployed observability to gather.
- **2026-05-15 follow-up — Confirm both 2026-05-14 internal-feedback ships are sticking**: Today (2026-05-14) shipped two distinct user-feedback items via separate deploys. Tomorrow explicitly re-verify both are still healthy in production:
  1. **Auditor stuck-row fix** (commits `862888b0` + `7deab384`, deploys `d346b9b3` + `b7af5bf0`) — confirm no new `revet_triggered`-without-resolution entries since the deploy (separate from the 4 historical rows already on watch above). Check `vetting_audit_log` for any rows created after 2026-05-14 14:45 UTC where `action_taken='revet_triggered'` and `revet_new_score IS NULL` past 24h. Zero is the goal.
  2. **Recruiter Transparency** (commit `f49ddf53`, deploy `2fa30078`) — covered by the marker check above; no separate query needed.
  Going forward: this "did our user-feedback ships hold" verification pattern should be a standard part of every post-deploy 24-48h checkpoint when the batch was driven by user feedback, not just internal hardening.

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
### May 2026 — Cost-Optimization & Reliability Batch
Bundled push targeting 40–50% off the $4,700/mo OpenAI baseline plus operational hardening. Each item below is shipped, tested, and in production. Operational knobs (env vars, admin URLs, config keys) are preserved verbatim.

- **Module-Based AI Cost Forecaster**: `services/cost_forecaster.py` maps 9 modules (Inbound, Screening, Vetting, Scout Support, Search/Recruit/Prospector, Job Automation, Resume Parsing, Fuzzy Duplicate Detection, Embeddings) to a `primary_site` representing one unit of work. `derive_unit_costs(window_days)` returns per-module USD/unit (low-confidence flag <30 primary calls, insufficient_data at 0); `project_monthly_cost(...)` returns per-module + total + annual projection. Super-admin page `/admin/ai-cost/forecast` provides a form-driven scenario builder, manual unit-cost overrides (`cost_forecast_override`), and named saved scenarios (`cost_forecast_scenario`). Alembic migration `o9i0j1k2l3m4`.
- **AI Cost Telemetry + Phase 1 Model Downgrades**: `services/openai_helper.py` exposes `resolve_model(site_id, default)` (per-site `MODEL_TIER_OVERRIDE_<SITE>` env override, dot/dash → underscore) and fire-and-forget `log_call(...)` writing to `openai_call_log` (tokens, USD cost from central PRICING table, daemon thread, never raises). All 38 SDK-based OpenAI call sites instrumented; **vetting_audit_service** patched May 12 2026 to use the SDK (was raw `httpx.post('https://api.openai.com/v1/chat/completions')` — bypassed both the SDK and telemetry, hiding up to ~1,920 gpt-5.4 calls/day from `/admin/ai-cost`). New site_id `vetting_audit` honors `MODEL_TIER_OVERRIDE_VETTING_AUDIT`. 13 non-critical sites downgraded to `gpt-4.1-mini` (job_classification, email_inbound resume_parse + dedup_validate, resume_parser format_html, scout_support platform_reply + classify_reply + admin_handling_intent + platform_intake + failure_analysis, automation title_extract, screening years_recheck, scout_prospector refine, fuzzy_duplicate_matcher). Flagship sites stay on `gpt-5.4` (scout_vetting questions/reply_intent/outcome/followup_email; scout_support understanding/clarification/retry/admin_question/admin_refine/draft_generation/reopen_analysis; screening requirements_extract/zero_recheck/scoring; scout_screening optimize_reqs; scout_prospector web_search). Vision OCR consolidated to `gpt-4.1-mini`. Super-admin dashboard at `/admin/ai-cost` (1h/24h/7d/30d windows); System Health `tile_ai_cost_24h` with green/amber/red at $80/$200 daily spend.
- **Screening Skip Gates — Loop Killer Batch**: Three layered gates in `screening/dedup.py` + `screening/note_builder.py` stop the duplicate-vetting loop bug.
  (1) **Self-Screen Cooldown** (`_self_screen_cooldown_active`) blocks any re-screen within `self_screen_cooldown_minutes` of the candidate's most recent vetting_log row, regardless of `applied_job_id`. Configurable 0–720 (default 120, 0 disables). Sandbox + Quality-Auditor revets bypass via `reset_candidate_for_revet`.
  (2) **Recruiter-Decisioned Skip** (`_is_paused_by_recruiter_decision`) skips the (candidate × job) re-screen when the pair has been screened before AND a human-authored Bullhorn note exists after the most recent "Scout Screen" note. Brand-new pairs always proceed. Killswitch `recruiter_decision_skip_enabled` (default true). Fail-open on errors.
  (3) **Note-Dedupe Rejection Counter** (`_DEDUPE_REJECTION_COUNTER`) emits `event=note_dedupe_blocked counter=N …` for Sentry/Datadog grep.
  Admin form at `/vetting/settings` exposes both config keys with 0–720 cooldown cap and audit-log coverage.
- **Recruiter Email Enhancements — Job-Aware Subject + Resume Attachment**: `screening/notification.py`. Subject now `Scout: {Name} — {Top Job Title} (Job #{ID})` (single match) or `… +{N-1} more` (multi-match), top match by highest `match_score`. Best-effort resume attachment via `_fetch_resume_attachment` (`_RESUME_ATTACHMENT_MAX_BYTES=10MB`, sanitized filename, MIME inference for pdf/doc/docx/rtf/txt/odt with octet-stream fallback). Fully fail-open — email always sends.
- **Cost-Savings Day-0 Batch — S1 + Phase A Embedding A/B Shadow**:
  **S1**: `self_screen_cooldown_minutes` seeded default 60 → 120 (admins still tune via `/vetting/settings`). Alembic migration `p0j1k2l3m4n5` (also creates `embedding_ab_log` schema).
  **S3 Phase A — Shadow Infra**: `embedding_ab_log` table + `EmbeddingABLog` model. `embedding_service.py:filter_relevant_jobs` gains a fail-soft shadow path gated by env var `EMBEDDING_AB_SHADOW_ENABLED` (default off); per-call cost cap via `EMBEDDING_AB_SHADOW_MAX_JOBS` (default 25, 0=unlimited). Shadow path uses isolated `db.engine.begin()` transaction so AB log failures cannot rollback caller's ORM session. Shadow spend logged under cost-telemetry site_id `embedding_service.shadow`. Super-admin page `/admin/ai-cost/embedding-ab` shows concordance/FN/FP/Pearson, threshold sweep (0.15→0.35; "recommended" only when FN ≤ 2%, otherwise none), top-25 flagged FNs, and cutover controls. **Cutover mechanism**: set production secret `MODEL_TIER_OVERRIDE_EMBEDDING_SERVICE_CANDIDATE=text-embedding-3-small` (revert by deleting). Decision rule: concordance ≥95%, FN ≤2%, no specialty-cluster failure.
- **Workflow + Skip-Gate Observability (O1+O2)**:
  **O1**: Gunicorn `--reload` removed from production workflow command (was causing CPU stat() loops + brief request drops on file changes; dev-mode convenience flag).
  **O2**: `tile_skip_gates` on `/admin/health` (in `services/admin_health_service.py`) surfaces `_COOLDOWN_BLOCK_COUNTER`, `_RECRUITER_DECISION_BLOCK_COUNTER`, `_DEDUPE_REJECTION_COUNTER` with structured log lines (`event=cooldown_blocked|recruiter_decision_blocked|note_dedupe_blocked counter=N …`). Status: **red** if cooldown ≤ 0 (killswitch off), **amber** if any counter > 100 since worker boot, **green** otherwise. Counters are per-worker (sampled, not aggregated) — for absolute counts, grep prod logs for the `event=*` markers.
- **Scout Support / Quality Auditor — Hardening Batch**: Two-layer concurrency guard on ticket execution (in-process RLock + Postgres advisory lock) prevents duplicate Bullhorn writes across workers (C1); transactional ticket-deletion with rollback (C3); exponential-backoff retry on transient 5xx/timeout for Bullhorn entity updates + note creation (C4); Postgres advisory lock around `initiate_vetting` active-session count (C6); hardened user-reply commit, clarification null-analysis logging, ticket-number race retry, 10K-char reply length cap, distinct `api_failure` audit type, unbiased `random.sample` audit pool, `revet_skipped_stable` back-fill in pending lookups, full audit trail of vetting-settings changes (I1–I8).

### Code Organization
- **Models Package Split**: The monolithic `models.py` was decomposed into a 10-module `models/` package along clear domain boundaries, with backward compatibility.
- **Database Stats Hygiene**: Implemented `ANALYZE` and autovacuum tuning for `candidate_profile_embedding` to ensure accurate query planning.

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL, SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry, Microsoft OneDrive.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini Vision.