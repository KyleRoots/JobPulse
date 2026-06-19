# Scout Genius™ - AI-Powered Job Feed Automation Platform

## Overview
Scout Genius is a Flask-based web application designed to automate XML job feed processing and synchronize job listings with Bullhorn ATS/CRM. It provides AI-powered candidate vetting, streamlines application workflows, and enhances recruitment efficiency by maintaining accurate, real-time job listings. The platform aims to be a multi-tenant SaaS solution, transforming recruitment operations through automated job feed generation, Bullhorn integration, and advanced AI-driven candidate screening.

## User Preferences
- **Communication style**: Simple, everyday language.
- **Deployment workflow**: Always confirm deployment requirements at the end of any changes or updates.
- **Development Approval Process**: Before executing any development task, always provide a "stack recommendation" with (a) Autonomy level (Light/Economy/Power), (b) brief rationale. Wait for user approval before proceeding. Light tier: single-file, low-blast-radius work (doc edits, config flips, one-line fixes with no logic change).
- **Task Plans**: Every project task plan must include the recommended autonomy level (Light/Economy/Power) and a one-line rationale at the top.
- **TL;DR Required**: Lead all work, minor or major, with a TL;DR using the **Problem → Fix → Benefit** pattern (or compact summary equivalent for non-build outputs). TL;DR comes BEFORE the deep-dive.
- **Source of Truth**: GitHub repository (KyleRoots/Scout Genius) — main branch.
- **Dev Admin Credentials**: username=`admin`, password=`MyticasXML2025!`
- **Post-Deploy Checkpoints**: After every production deploy, schedule a 24–48h follow-up health check covering: (1) workflow logs for new errors, (2) AI cost telemetry vs daily threshold, (3) pipeline throughput (vetting logs, matches, parsed_emails), (4) feature-specific success metrics, and (5) any "watch-items" called out in the original deploy summary. Agent must proactively bring this up at the next session.

## Open Watch-Items (clear once resolved)

> **Resolved/historical detail archived**: `docs/archive-2026-05.md` (May ships, stuck-revet bug, L2 cutover playbook), `docs/archive-2026-06.md` (June 1 Cost Lab full eval + cost-spike, mailbox-pull incident, inbound noise-gate deploy detail), `docs/may-2026-cost-batch.md` (original May cost batch), `docs/cost-capacity-model-2026-05.md` (unit economics).

### Active — Cost Optimization (screening PAUSED for June)
- **🛑 June budget pause (2026-06-08): screening OFF for the rest of June** — June MTD (~$2,800 telemetry) exceeded the ~$2,500 cap. `vetting_enabled=false` + `screening_audit_enabled=false` (`/screening`); A/B shadow retired (`SHADOW_LOGGING_DISABLED=true` + republish). Verified 2026-06-08: scoring spend cliff **$36/hr → $0.51/hr**; only cheap inbound parsing continues (~$12–20/day) with `mailbox_pull_enabled=true` (applicants still reach Bullhorn, no scoring). **Resume 2026-07-01** — see the July-1 Re-enable Checklist below.
- **Mini-model cheap-first routing — Enforce LIVE 2026-06-05, baked in for July**: cheap-first gate (gpt-4.1-mini first-pass; mini<40 → reject/skip gpt-5.4; else escalate to gpt-5.4 as SOLE qualification authority) saved **~$94/day** with 0 false-negatives over the Jun 5–8 window before the budget pause. Settings already set for re-enable: `screening_routing_mode=enforce`, `layer2_model=gpt-4.1-mini`, `cheap_first_reject_threshold=40` (via `/screening` dropdown). A/B shadow already retired (saved ~$1,950/mo monitoring overhead). Full rollout play-by-play + numeric-crash fixes in `docs/archive-2026-06.md`; design in `.agents/memory/cheaper-model-scoring-experiment.md`.
- **Re-vet dedupe — queued (`Economy`)**: ~28% of scoring calls re-score the same (candidate, job); the self-screen cooldown doesn't cover the inbound/auditor/scheduled re-vet paths. Add an **idempotent cooldown guard** (does NOT disable legitimate re-vets) → ~$70–80/day. Measure via match→vetting_log (telemetry entity fields are empty for scoring). Complementary to routing (cuts call *count* vs per-call *cost*). Reinforced by the observed ~46% skip rate (below the ~74.5% design target) from repeated mid-band re-scores.
- **Customer-sizing cost calculator — in progress**: unit economics in `docs/cost-capacity-model-2026-05.md`; calculator wiring queued.
- **Pricing-fix verify (July re-enable)**: confirm new `openai_call_log` rows show ~$0.040/scoring-call and the dashboard ≈ OpenAI bill ~1:1. (June 1 Cost Lab concluded — format micro-opts both failed parity, audit flags OFF; full eval in `docs/archive-2026-06.md`.)

### Active — Fraud / Fake-Candidate Detection (LIVE since 2026-06-01)
- Deterministic ($0 AI) advisory layer (`fraud_detection_enabled=true`), thresholds 40/75. Surfaced via recruiter-portal badge + all-band advisory email banner (green Clear / amber Review / red High-Risk) + vendor-neutral Bullhorn notes. Baseline 2026-06-01: 160 clear / 4 review / 0 high-risk (~2.4% review). Advisory contract: NEVER skips/blocks screening (isolated DB sessions + fail-soft hook, verified by tests).
- **Note toggles** (in `/screening`, both OFF during observation): `fraud_bullhorn_note_enabled` (master, High-Risk only by default), `fraud_note_all_bands_enabled` (widens to all bands when master on). **Next step**: enable High-Risk notes once thresholds look right — deferred to the 2026-07-01 re-enable (fraud assessment runs inside the vetting pipeline, paused for June).
- **JD-mirror verbatim evidence — deployed 2026-06-02, verify pending**: captures the copied passage from résumé + JD, shown as an "In résumé / In job posting" block in the email banner (all bands) + appended to the Bullhorn note (rides note gating). No scoring/banding change. Verify: eyeball the first real verbatim-flagged email renders correctly (highlighted, escaped).
- Phase 1 boundaries (not defects): cross-candidate signals compare only vs Scout Genius local history (not the full Bullhorn DB); email-reuse only (no phone column persisted). Phase 2 (light-AI signals, repeat-offender alerting, auto-gating) deferred.

### Active — Pending Prod Verify
- **Cross-route inbound de-dupe — shipped + deployed 2026-06-10, verifying**: the SAME application arrives as two inbox messages with different Message-IDs (one ingestion door = SendGrid inbound webhook, the other = Graph mailbox-pull), so the Message-ID dedupe couldn't link them → double Bullhorn submission/upload/notes (~8,284 dups / 14d). Fix: windowed guard in `email_inbound_service/processing_mixin.py` (`_find_cross_route_sibling`) collapses the second copy when a prior ParsedEmail for the same (bullhorn_candidate_id, bullhorn_job_id) already has a non-null `bullhorn_submission_id` within a config window → status='duplicate', early return (skips submission/upload/notes). DB-config (no republish): `cross_route_dedupe_enabled` (true), `cross_route_dedupe_window_minutes` (30; 0=off; cap 1440). `tests/test_cross_route_dedupe.py` (9, incl. process_email integration). **Verified firing in prod first hour** (`Cross-route duplicate detected: candidate 4665214 → job 35258 … submission 914290`; mailbox-pull `3 processed (3 dup, 0 failed)`). **24–48h checkpoint**: confirm duplicate pipeline rate drops vs ~8,284/14d baseline + no false-collapse (genuine re-applies outside the 30m window still submit). Root cause is the dual ingestion doors, NOT the intake form (form sends exactly ONE email, never writes Bullhorn directly).
- **Single-ingestion-door consolidation — QUEUED (`Economy`), agreed 2026-06-10, pick up after the dedupe checkpoint**: cross-route dups exist only because both ingestion doors run. 14d split: mailbox-pull caught ~16,950 / missed ~224; SendGrid webhook missed ~8,660 (LB body-truncation) and uniquely added only ~224 (~16/day). Real root-cause cleanup = consolidate to mailbox-pull only, then the dedupe becomes a dormant failsafe (KEEP it — idempotency is correct design, not a band-aid). Two-phase, measurement-gated (one-way risk: retiring the webhook can silently drop applicants): **Phase 1** — add mailbox-pull miss observability + tighten cursor/backfill to close the ~224 gap, watch several days of zero misses; **Phase 2** — only then disable the SendGrid inbound webhook. Do NOT do Phase 2 until Phase 1 proves zero misses. Deferred-now rationale: dedupe already protects Bullhorn (harm mitigated), changing ingestion again now would contaminate the dedupe checkpoint measurement, and June is the wrong risk posture (inbound is the only live path to Bullhorn during the screening pause).
- **PandoLogic referrer-based source attribution — built 2026-06-10, deploy + verify pending**: production spot-check (Jun 5–10) found the `feed=pando` discriminator on **0 of ~5,052** apply-page visits — PandoLogic does NOT preserve our `?feed=pando` query param through its redirect network, so the 2026-06-09 feed-based path never fired. The masked PandoLogic traffic arrives via `myticasconsulting.thejobnetwork.com` (TheJobNetwork = PandoLogic's programmatic network): **1,403 visits / 652 completed apps in 6 days, all mislabeled "LinkedIn Job Board."** Fix: detect PandoLogic by **referrer host** (`thejobnetwork.com`/`pandologic`/`pandolytics`) in `source_attribution.is_pando_referrer` → `resolve_source` returns `PANDO_SOURCE='Corporate Website'` at top priority, and `job_application_service.submit_application` sets `feed='pando'` so the existing inbound owner-routing (owner=`VettingConfig.pandologic_api_user_id`=4582033) fires. Genuine LinkedIn/Indeed referrers (no pando host) keep their true source. Only affects NEW applications (no retro-fix of ~900 already mislabeled). `tests/test_source_attribution.py` + `tests/test_pando_source_attribution.py` (34 total, incl. submit_application glue). **Verify**: first real `PandoLogic referrer detected (...): tagging feed=pando` log line + matching Bullhorn candidate (source="Corporate Website", owner=4582033). One-line switch to "Vendor/3rd Party" via `_InboundCore.PANDO_FEED_SOURCE` (keep `source_attribution.PANDO_SOURCE` in sync). (Supersedes the 2026-06-09 feed=pando path, which is retained as a fallback but never fires in prod.)
- **PandoLogic INBOUND routing fix — shipped 2026-06-11, deploy + verify pending**: even with feed=pando flowing from the apply form, prod owner-routing NEVER fired (`"PandoLogic feed detected"` had 0 log hits in a 26h window). Two root-cause bugs: (1) `detect_feed` regex `Feed:\s*value` only matched plain-text bodies, but inbound processes the **HTML** body where `Feed:`/value sit in separate `<td>` cells → fixed to strip tags + retry (`extraction_mixin.py`); (2) returning candidates went through `_build_enrichment_update`, whose `enrichable_fields` intentionally excludes source/owner, so dup/recovery re-applies kept stale source/owner. Fix: `_build_enrichment_update` now takes an explicit `is_pando` flag (call sites compute `feed=detect_feed(body)` once, pass `is_pando=_is_pando_feed(feed)`) → corrects source→`Corporate Website` whenever pando, and reassigns owner→Pando (4582033) ONLY when a pando-owner target exists AND existing owner is None or in `VettingConfig.api_user_ids` (never a human recruiter). `get_candidate` now fetches `owner(...)` so the guard can see it. `tests/test_pando_source_attribution.py` (incl. HTML cell-split detect_feed, owner-guard bands, config-unset robustness). **Backfill done 2026-06-11**: last ~3h of `Corporate Website` ParsedEmails (6 candidates: 4338061, 4665238, 4662744, 4665234, 4107106, 3838957) → owner set to 4582033, source set to Corporate Website (per user: also treated system accounts 66/78 as reassignable, not just configured API users). **Verify post-deploy**: first real `"PandoLogic feed detected"` log line on a live inbound apply + new returning-applicant gets source/owner corrected without stomping a human owner; watch for `"source corrected but pandologic_api_user_id unset"` (config-drift canary).
- **Two-column résumé extraction — shipped, verify still open**: column-aware PDF parsing (`_extract_pdf_page_text`) reads header→left→right→footer; single-column untouched. `tests/test_pdf_column_extraction.py`. No prod parse errors/regressions observed, but no per-page telemetry → no direct evidence a two-column PDF was exercised. Verify: a real two-column PDF parses full sidebar+body, no single-column regression (needs a targeted sample).
- **Clearance wording — seed only**: `config/global_screening_prompt.txt` aligned (thresholds 5/10/15). Live value is the DB (`VettingConfig.global_custom_requirements`); re-apply in prod if wanted live.

### July-1 Re-enable Checklist (agent must raise proactively at next session)
1. `/screening` → enable `vetting_enabled` (+ `screening_audit_enabled` if wanted) → Save → click **Start Fresh** — resets BOTH `vetting_cutoff_date` AND `last_run_timestamp`, so only net-new is scored (no June backlog from the inbound OR Bullhorn-direct path; `tests/test_start_fresh_cutover.py`).
2. Enforce + retired shadow are already baked in (`screening_routing_mode=enforce`, `layer2_model=gpt-4.1-mini`). After 24–48h, confirm **0** `🚨 CANARY false-negative` + 0 `AI analysis error`/numeric-crash lines and qualify-rate parity across the full window.
3. Re-measure scoring steady-state (post-outage backlog should be drained; prior estimate ~$110–140/day billing-true).
4. Enable fraud High-Risk Bullhorn notes once thresholds look right (see Fraud section).
5. Verify pricing: new `openai_call_log` rows ~$0.040/scoring-call, dashboard ≈ OpenAI bill ~1:1.

### Process Rule (Standing)
After every user-feedback-driven deploy batch, the next 24–48h checkpoint must explicitly re-verify each shipped feedback item is still healthy in production — not just internal hardening checks.

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
- **Fraud / Fake-Candidate Detection (Phase 1, advisory)**: Deterministic ($0 AI) candidate-integrity scoring. Pure evaluators in `fraud_detection/signals.py` (disposable email, contact anomalies, work-history impossibilities, resume-content reuse, identity reuse, embedding near-dup, application velocity) → banded by configurable thresholds (default High-Risk≥75, Review 40–74, Clear<40). `fraud_detection/engine.py` (`FraudSignalEngine.assess`) gathers DB facts in **fully isolated SQLAlchemy sessions** (never touches the caller's vetting txn — guarantees advisory/non-blocking), persists `CandidateFraudAssessment`, and writes a band-aware, vendor-neutral Bullhorn note (action="Candidate Risk Review") gated by `fraud_bullhorn_note_enabled`. Surfaced via recruiter-portal badge (latest-assessment-wins) and an all-band advisory email banner (`screening/notification.py::_build_fraud_banner_html`). Pipeline hook in `candidate_vetting_service/processing.py` is gated by `fraud_detection_enabled` and fully fail-soft. Config in `/screening`.
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
Shipped batch targeting 40–50% off the $4,700/mo OpenAI baseline plus operational hardening. Full original-batch details (Cost Forecaster, Telemetry, Skip Gates, Email Enhancements, Embedding A/B Shadow, Workflow Observability, Scout Support Hardening) archived in **`docs/may-2026-cost-batch.md`**. Capacity model and unit economics in **`docs/cost-capacity-model-2026-05.md`**.

Key admin URLs:
- `/admin/ai-cost` — telemetry dashboard (1h/24h/7d/30d)
- `/admin/ai-cost/forecast` — module-based projection + scenario builder
- `/admin/ai-cost/embedding-ab` — embedding A/B comparison + cutover controls
- `/admin/health` — System Health tiles (`tile_ai_cost_24h`, `tile_skip_gates`)
- `/screening` — skip-gate config (cooldown, recruiter-decision killswitch)

## External Dependencies

- **Python**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend**: Bootstrap 5, Font Awesome 6.
- **Services**: PostgreSQL, SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry, Microsoft OneDrive.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini (vision + non-critical sites).
