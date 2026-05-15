# May 2026 — Cost-Optimization & Reliability Batch (Archived)

Archived from `replit.md` on 2026-05-15. All items below are shipped, tested, and in production. Operational knobs (env vars, admin URLs, config keys) preserved verbatim. This document is the canonical reference for the May 2026 cost-optimization push targeting 40–50% off the $4,700/mo OpenAI baseline plus operational hardening.

## Scope
Bundled push targeting 40–50% off the $4,700/mo OpenAI baseline plus operational hardening.

## Shipped Items

### Module-Based AI Cost Forecaster
`services/cost_forecaster.py` maps 9 modules (Inbound, Screening, Vetting, Scout Support, Search/Recruit/Prospector, Job Automation, Resume Parsing, Fuzzy Duplicate Detection, Embeddings) to a `primary_site` representing one unit of work. `derive_unit_costs(window_days)` returns per-module USD/unit (low-confidence flag <30 primary calls, insufficient_data at 0); `project_monthly_cost(...)` returns per-module + total + annual projection. Super-admin page `/admin/ai-cost/forecast` provides a form-driven scenario builder, manual unit-cost overrides (`cost_forecast_override`), and named saved scenarios (`cost_forecast_scenario`). Alembic migration `o9i0j1k2l3m4`.

### AI Cost Telemetry + Phase 1 Model Downgrades
`services/openai_helper.py` exposes `resolve_model(site_id, default)` (per-site `MODEL_TIER_OVERRIDE_<SITE>` env override, dot/dash → underscore) and fire-and-forget `log_call(...)` writing to `openai_call_log` (tokens, USD cost from central PRICING table, daemon thread, never raises). All 38 SDK-based OpenAI call sites instrumented; **vetting_audit_service** patched May 12 2026 to use the SDK (was raw `httpx.post('https://api.openai.com/v1/chat/completions')` — bypassed both the SDK and telemetry, hiding up to ~1,920 gpt-5.4 calls/day from `/admin/ai-cost`). New site_id `vetting_audit` honors `MODEL_TIER_OVERRIDE_VETTING_AUDIT`. 13 non-critical sites downgraded to `gpt-4.1-mini` (job_classification, email_inbound resume_parse + dedup_validate, resume_parser format_html, scout_support platform_reply + classify_reply + admin_handling_intent + platform_intake + failure_analysis, automation title_extract, screening years_recheck, scout_prospector refine, fuzzy_duplicate_matcher). Flagship sites stay on `gpt-5.4` (scout_vetting questions/reply_intent/outcome/followup_email; scout_support understanding/clarification/retry/admin_question/admin_refine/draft_generation/reopen_analysis; screening requirements_extract/zero_recheck/scoring; scout_screening optimize_reqs; scout_prospector web_search). Vision OCR consolidated to `gpt-4.1-mini`. Super-admin dashboard at `/admin/ai-cost` (1h/24h/7d/30d windows); System Health `tile_ai_cost_24h` with green/amber/red at $80/$200 daily spend.

### Screening Skip Gates — Loop Killer Batch
Three layered gates in `screening/dedup.py` + `screening/note_builder.py` stop the duplicate-vetting loop bug.

1. **Self-Screen Cooldown** (`_self_screen_cooldown_active`) blocks any re-screen within `self_screen_cooldown_minutes` of the candidate's most recent vetting_log row, regardless of `applied_job_id`. Configurable 0–720 (default 120, 0 disables). Sandbox + Quality-Auditor revets bypass via `reset_candidate_for_revet`.
2. **Recruiter-Decisioned Skip** (`_is_paused_by_recruiter_decision`) skips the (candidate × job) re-screen when the pair has been screened before AND a human-authored Bullhorn note exists after the most recent "Scout Screen" note. Brand-new pairs always proceed. Killswitch `recruiter_decision_skip_enabled` (default true). Fail-open on errors.
3. **Note-Dedupe Rejection Counter** (`_DEDUPE_REJECTION_COUNTER`) emits `event=note_dedupe_blocked counter=N …` for Sentry/Datadog grep.

Admin form at `/vetting/settings` exposes both config keys with 0–720 cooldown cap and audit-log coverage.

### Recruiter Email Enhancements — Job-Aware Subject + Resume Attachment
`screening/notification.py`. Subject now `Scout: {Name} — {Top Job Title} (Job #{ID})` (single match) or `… +{N-1} more` (multi-match), top match by highest `match_score`. Best-effort resume attachment via `_fetch_resume_attachment` (`_RESUME_ATTACHMENT_MAX_BYTES=10MB`, sanitized filename, MIME inference for pdf/doc/docx/rtf/txt/odt with octet-stream fallback). Fully fail-open — email always sends.

### Cost-Savings Day-0 Batch — S1 + Phase A Embedding A/B Shadow
**S1**: `self_screen_cooldown_minutes` seeded default 60 → 120 (admins still tune via `/vetting/settings`). Alembic migration `p0j1k2l3m4n5` (also creates `embedding_ab_log` schema).

**S3 Phase A — Shadow Infra**: `embedding_ab_log` table + `EmbeddingABLog` model. `embedding_service.py:filter_relevant_jobs` gains a fail-soft shadow path gated by env var `EMBEDDING_AB_SHADOW_ENABLED` (default off); per-call cost cap via `EMBEDDING_AB_SHADOW_MAX_JOBS` (default 25, 0=unlimited). Shadow path uses isolated `db.engine.begin()` transaction so AB log failures cannot rollback caller's ORM session. Shadow spend logged under cost-telemetry site_id `embedding_service.shadow`. Super-admin page `/admin/ai-cost/embedding-ab` shows concordance/FN/FP/Pearson, threshold sweep (0.15→0.35; "recommended" only when FN ≤ 2%, otherwise none), top-25 flagged FNs, and cutover controls. **Cutover mechanism**: set production secret `MODEL_TIER_OVERRIDE_EMBEDDING_SERVICE_CANDIDATE=text-embedding-3-small` (revert by deleting). Decision rule: concordance ≥95%, FN ≤2%, no specialty-cluster failure.

### Workflow + Skip-Gate Observability (O1+O2)
**O1**: Gunicorn `--reload` removed from production workflow command (was causing CPU stat() loops + brief request drops on file changes; dev-mode convenience flag).

**O2**: `tile_skip_gates` on `/admin/health` (in `services/admin_health_service.py`) surfaces `_COOLDOWN_BLOCK_COUNTER`, `_RECRUITER_DECISION_BLOCK_COUNTER`, `_DEDUPE_REJECTION_COUNTER` with structured log lines (`event=cooldown_blocked|recruiter_decision_blocked|note_dedupe_blocked counter=N …`). Status: **red** if cooldown ≤ 0 (killswitch off), **amber** if any counter > 100 since worker boot, **green** otherwise. Counters are per-worker (sampled, not aggregated) — for absolute counts, grep prod logs for the `event=*` markers.

### Scout Support / Quality Auditor — Hardening Batch
Two-layer concurrency guard on ticket execution (in-process RLock + Postgres advisory lock) prevents duplicate Bullhorn writes across workers (C1); transactional ticket-deletion with rollback (C3); exponential-backoff retry on transient 5xx/timeout for Bullhorn entity updates + note creation (C4); Postgres advisory lock around `initiate_vetting` active-session count (C6); hardened user-reply commit, clarification null-analysis logging, ticket-number race retry, 10K-char reply length cap, distinct `api_failure` audit type, unbiased `random.sample` audit pool, `revet_skipped_stable` back-fill in pending lookups, full audit trail of vetting-settings changes (I1–I8).

## Resolved Watch-Items (Archived)

### 2026-05-14 ship — A/B Shadow App-Context Fix (validated 2026-05-15)
Single-file surgical fix in `screening/prompt_builder.py` `_save_screening_ab_row` to stop the constant `WARNING - Failed to save screening A/B row: Working outside of application context.` log spam. Root cause: function opened `db.engine.begin()` from APScheduler background threads where no Flask app context is active (warning was pre-existing from commit `bdb903a26` 2026-05-09 Embedding A/B Shadow Phase A, but spam volume made real errors hard to spot AND silently lost shadow A/B data — blocking embedding cutover decisions). Fix uses `has_app_context()` guard to skip push/pop on the request-path and only wraps in `with app.app_context():` for background-thread callers. Architect-approved.

**Validation result (2026-05-15)**: `screening_ab_log` went from 0 rows all-time → 14 rows in minutes post-deploy. Was a 5-day silent data outage (since 2026-05-09), now resolved. Embedding A/B cutover decision is now data-backed.
