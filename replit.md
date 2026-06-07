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

### Active — Cost Optimization (current focus)
- **Mini-model cheap-first routing — CANARY LIVE in prod since 2026-06-05 ~19:11 UTC (validating, target ~3 days through ~Jun 8), UI-toggle gated (`Power`)**: validated on the gpt-4.1-mini A/B shadow (6,302 head-to-head scorings vs gpt-5.4 since 2026-05-14): **97.8% qualify/reject agreement**, mini +8.5 generous (rarely false-rejects), base rate ~0.7% qualify (43/6,302); a cheap-first gate (mini scores everyone; mini < 40 → auto-reject/skip gpt-5.4; else escalate to gpt-5.4 as authoritative scorer) routes **~74.5% cheap with 0/43 qualified lost** → ~62% off the scoring line → **~$2,000–2,600/mo ongoing**. **Now implemented** behind **VettingConfig DB keys** (set per-environment; prod DB is separate): `layer2_model` (`gpt-4.1-mini` turns mini on as first-pass; while `gpt-5.4` the router is a hard no-op), `screening_routing_mode` (`off` default | `canary` = gate logged but gpt-5.4 still runs on everyone, $0 savings | `enforce` = mini<thr skips gpt-5.4), `cheap_first_reject_threshold` (default 40). gpt-5.4 stays the SOLE qualification authority — even on an escalation infra failure (canary/enforce re-raise → job recorded 0/error → eligible for the zero-score gpt-5.4 reverify; never qualifies on mini alone). Tests: `tests/test_cheap_first_routing.py` (13). **Rollout:** now set via the `/screening` Routing Mode dropdown (no DB edit; republish needed if card missing). **Canary went LIVE in prod 2026-06-05 ~19:11 UTC** (logs confirm `🧭 Cheap-first routing: mode=canary` + escalations of both above- and below-threshold candidates to gpt-5.4, 0 false-negatives at start). Validate ~3 days (through ~Jun 8): logs must show **0** `🚨 CANARY false-negative` lines AND a solid sample of mini<40 rejects exercised → then flip dropdown to **Enforce** for savings. Agent to pull logs at ~24h and ~3-day mark. Keep the mini A/B shadow ON (`SCREENING_AB_SHADOW_ENABLED=true` + `SHADOW_LOGGING_DISABLED=false`, NOT audit flags) as monitor; retire shadow after cutover. Design + full rollout detail in `.agents/memory/cheaper-model-scoring-experiment.md`. **Mini numeric-field crash — fixed in TWO passes (BOTH now deployed + verified in prod 2026-06-05 ~20:38 UTC; recency gate runs clean, e.g. job 35041 went NoneType-crash → `📉 Recency hard gate` clean, 0 AI analysis errors in post-deploy batch):** mini sometimes returns numeric fields as prose ("Not explicitly stated…") or explicit JSON null where gpt-5.4 always sent numbers → `float()`/`int()`/`>` crashed the analysis → `AI analysis error: ... NoneType` / `could not convert string to float` → match_score 0 (appeared ONLY after canary went live; zero before). **Pass 1 (republished 2026-06-05 ~20:1x):** years fields (`required_years`/`estimated_years`/`total_professional_years`) hardened via `_safe_float` in `post_processing.py` (`_compute_shortfalls`, `enforce_experience_floor`) + re-check guard in `prompt_builder.py`; unparseable estimate **skips that skill's years gate** (never manufactures a shortfall → no Enforce false-reject). **BUT the SAME `'>' NoneType and int` crash recurred post-republish from two OTHER sites** → **Pass 2 (deployed + verified 2026-06-05 ~20:38 UTC):** hardened `enforce_recency_hard_gate` (`months_since_relevant_work`/`penalty_applied` → `int(_safe_float(...))`, null relevance flags default to relevant = no false penalty) and `coerce_scores` (`match_score`/`technical_score` → `int(_safe_float(...))`, the linchpin that also protects every downstream `match_score` comparison and the router reads in `processing.py`). Root trap: `dict.get(k, 0)` returns None on an EXPLICIT null (default only covers a MISSING key). Tests: `tests/test_years_parsing_hardening.py` (11) + `tests/test_recency_coerce_hardening.py` (8). **Separate observed issue, left unfixed on purpose:** mini occasionally emits truncated JSON (`AI analysis error: Unterminated string …`) — caught by outer handler → escalates to gpt-5.4 (canary safety net), no candidate lost. **NOTE: canary GO criterion ("0 false-negative lines") does NOT catch these — they're an analysis-error class, watch `AI analysis error` count too.** **Pass-2 republish DONE 2026-06-05 ~20:38 UTC → RESETS the 3-day canary clock: new validation window starts now, target ~3 days through ~Jun 8–9. Agent to pull logs at ~24h (~Jun 6 ~20:38) and ~3-day mark before any Enforce flip.** **Known pre-existing follow-up (separate, not this build):** zero-score reverify recomputes `is_qualified` without the location-barrier guard (`processing.py` ~750) — architect-confirmed pre-existing/out-of-scope.
- **Re-vet dedupe — queued (`Economy`)**: ~28% of scoring calls re-score the same (candidate, job); the self-screen cooldown doesn't cover the inbound/auditor/scheduled re-vet paths. Add an **idempotent cooldown guard** (does NOT disable legitimate re-vets) → ~$70–80/day. Measure via match→vetting_log (telemetry entity fields are empty for scoring). Complementary to routing (cuts call *count* vs per-call *cost*).
- **June 1 Cost Lab — CONCLUDED**: format micro-opts (schema/output-token diet + L2 cache layout) BOTH failed score parity; audit flags `SCREENING_SCHEMA_AUDIT_ENABLED` + `SCREENING_PROMPT_CACHE_AUDIT_ENABLED` set OFF + republished. Prompt-cache already ~85%. Full eval in `docs/archive-2026-06.md`.
- **Backlog**: scoring ~7,200–8,400/day vs ~2,400–3,000 baseline — post-outage (May 11–Jun 1) catch-up draining, flat not spiking. Billing-true ≈ $554/day now (telemetry × 1.6); steady-state ≈ $110–140/day. **Re-measure steady-state ~2026-06-09.**
- **Customer-sizing cost calculator — in progress**: unit economics in `docs/cost-capacity-model-2026-05.md`; calculator wiring queued.
- **Pricing-fix verify**: confirm new `openai_call_log` rows show ~$0.040/scoring-call and the dashboard ≈ OpenAI bill ~1:1.

### Active — Fraud / Fake-Candidate Detection (LIVE since 2026-06-01)
- Deterministic ($0 AI) advisory layer (`fraud_detection_enabled=true`), thresholds 40/75. Surfaced via recruiter-portal badge + all-band advisory email banner (green Clear / amber Review / red High-Risk) + vendor-neutral Bullhorn notes. Baseline 2026-06-01: 160 clear / 4 review / 0 high-risk (~2.4% review). Advisory contract: NEVER skips/blocks screening (isolated DB sessions + fail-soft hook, verified by tests).
- **Note toggles** (in `/vetting/settings`, both OFF during observation): `fraud_bullhorn_note_enabled` (master, High-Risk only by default), `fraud_note_all_bands_enabled` (widens to all bands when master on). **Next step ~2026-06-08**: enable High-Risk notes once thresholds look right.
- **JD-mirror verbatim evidence — deployed 2026-06-02, verify pending**: captures the copied passage from résumé + JD, shown as an "In résumé / In job posting" block in the email banner (all bands) + appended to the Bullhorn note (rides note gating). No scoring/banding change. Verify: eyeball the first real verbatim-flagged email renders correctly (highlighted, escaped).
- Phase 1 boundaries (not defects): cross-candidate signals compare only vs Scout Genius local history (not the full Bullhorn DB); email-reuse only (no phone column persisted). Phase 2 (light-AI signals, repeat-offender alerting, auto-gating) deferred.

### Active — Pending Prod Verify (24–48h post-deploy)
- **Dynamic source attribution — deployed 2026-06-05, ✅ VERIFIED 2026-06-07**: apply page detects the true channel via the browser referrer (first-touch `apply_page_visit` table) instead of the hardcoded `?source=LinkedIn`; resolves referrer > utm > param and stamps the "has applied on {source}" email subject → inbound→Bullhorn carries it untouched. Internal/PandoLogic referrers fall through. `tests/test_source_attribution.py` (8 tests). **Prod evidence (2026-06-07):** referrer override confirmed — two real Facebook arrivals (referrer_host `l.facebook.com`/`facebook.com`) resolved to `Facebook` despite carrying the hardcoded `source_param=LinkedIn`; breakdown 609 LinkedIn / 63 blank (internal/PandoLogic fall-through as designed) / 2 Facebook. Remaining spot-check (optional): confirm a matching Bullhorn submission shows the real source. Deferred: retention/TTL for `apply_page_visit` (stores IP/UA/email/referrer).
- **Two-column résumé extraction — shipped, verify still open**: column-aware PDF parsing (`_extract_pdf_page_text`) reads header→left→right→footer; single-column untouched. `tests/test_pdf_column_extraction.py`. **2026-06-07:** no prod parse errors/regressions observed, but the extractor emits no distinct per-page telemetry, so there's no direct evidence a two-column PDF was exercised. Verify: a real two-column PDF parses full sidebar+body, no single-column regression (needs a targeted sample, not log-derivable).
- **Auto-recovery sweep — shipped, ✅ VERIFIED 2026-06-07**: scheduled "Résumé Recovery Sweep (30 min)" re-attaches completed-but-no-résumé rows to the EXISTING candidate (idempotent, single-flight, never creates a candidate/submission). DB flags `resume_recovery_sweep_enabled` (ON), `_window_hours` (6), `_limit` (25); independent of `mailbox_pull_enabled`. **Prod evidence (2026-06-07):** job fires on its 30-min interval and logs "executed successfully" (clean passes, no errors).
- **Legacy `.doc` extraction — fixed 2026-05-30, ✅ VERIFIED 2026-06-07**: pure-Python `olefile` extractor (`_try_olefile_doc`) runs first (antiword EIOs in prod Nix); printable-ratio guard ≥0.80. **Prod evidence (2026-06-07):** real `.doc` parsed cleanly — `✅ olefile extracted 41892 chars`, resume parsing then found first/last name.
- **Inbound noise gate — fixed 2026-06-02, ✅ VERIFIED 2026-06-07**: non-candidate emails recorded as `ParsedEmail.status='ignored'` (no admin alert); real candidates still alert. **Prod evidence (2026-06-07, last 7d):** 1,854 `completed` / 984 `ignored` / 63 `failed` — `ignored` rows accumulating as designed (noise diverted from admin alerts) while real candidates still process. Deploy detail in `docs/archive-2026-06.md`.
- **Clearance wording — seed only**: `config/global_screening_prompt.txt` aligned (thresholds 5/10/15). Live value is the DB (`VettingConfig.global_custom_requirements`); re-apply in prod if wanted live.
- **Mailbox-pull — RESOLVED**: LB body truncation + Graph `$select` attachment bug fixed, recovery run. Steady polling resumes when `mailbox_pull_enabled=true`. History in `docs/archive-2026-06.md`.

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
- **Fraud / Fake-Candidate Detection (Phase 1, advisory)**: Deterministic ($0 AI) candidate-integrity scoring. Pure evaluators in `fraud_detection/signals.py` (disposable email, contact anomalies, work-history impossibilities, resume-content reuse, identity reuse, embedding near-dup, application velocity) → banded by configurable thresholds (default High-Risk≥75, Review 40–74, Clear<40). `fraud_detection/engine.py` (`FraudSignalEngine.assess`) gathers DB facts in **fully isolated SQLAlchemy sessions** (never touches the caller's vetting txn — guarantees advisory/non-blocking), persists `CandidateFraudAssessment`, and writes a band-aware, vendor-neutral Bullhorn note (action="Candidate Risk Review") gated by `fraud_bullhorn_note_enabled`. Surfaced via recruiter-portal badge (latest-assessment-wins) and an all-band advisory email banner (`screening/notification.py::_build_fraud_banner_html`). Pipeline hook in `candidate_vetting_service/processing.py` is gated by `fraud_detection_enabled` and fully fail-soft. Config in `/vetting/settings`.
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
- `/vetting/settings` — skip-gate config (cooldown, recruiter-decision killswitch)

## External Dependencies

- **Python**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend**: Bootstrap 5, Font Awesome 6.
- **Services**: PostgreSQL, SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry, Microsoft OneDrive.
- **AI Models**: OpenAI GPT-5.4 (primary), GPT-4.1-mini (vision + non-critical sites).
