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

> **Resolved/historical detail archived**: May 2026 ships in `docs/archive-2026-05.md` (2026-05-14 ships, Stuck Revet Rows Bug A+B, 2026-05-15 PM ships, full L2 cutover playbook). June 2026 in `docs/archive-2026-06.md` (executed June 1 Cost Lab turn-on playbook + full telemetry, May budget-freeze closure, Bullhorn Search API stale-index reference pattern).

### Active — Fraud / Fake-Candidate Detection (LIVE in prod since 2026-06-01)
- **Status**: Deterministic ($0 AI) advisory layer, enabled in production 2026-06-01 (`fraud_detection_enabled=true`). Three surfacing channels: (a) recruiter-portal badge + audit table, (b) all-band advisory **email banner** (green Clear / amber Review / red High-Risk) on all three recruiter-email surfaces (qualified, prestige-review, location-review), fully fail-soft, and (c) vendor-neutral **Bullhorn notes**. Thresholds 40/75.
- **Note toggles** (both default OFF, currently OFF — observation window): `fraud_bullhorn_note_enabled` (master switch for notes; High-Risk only by default) and `fraud_note_all_bands_enabled` (widens notes to Review/Clear when the master is also on). Edit in `/vetting/settings`.
- **Calibration baseline (2026-06-01)**: 160 clear / 4 review / 0 high-risk (~2.4% review, no false-positive storm). Watch recruiter-portal badges + `candidate_fraud_assessment` rows; tune `fraud_high_risk_threshold` up if badge false-positive storms appear. Advisory contract: screening must NEVER skip/block — engine uses isolated DB sessions + fail-soft hook (verified by tests).
- **Next step (~2026-06-08)**: after thresholds look right, enable `fraud_bullhorn_note_enabled` (High-Risk notes first); optionally flip `fraud_note_all_bands_enabled` for all-band notes. **Phase 1 coverage boundaries** (not defects): cross-candidate signals compare only against Scout Genius vetting history (local Postgres), NOT the full Bullhorn DB; phone-reuse-across-identities inactive (no phone column persisted) — email reuse only. Phase 2 (light-AI signals, repeat-offender alerting, auto-gating) deferred.
- **Verbatim JD-mirror evidence — SHIPPED + deployed 2026-06-02, pending prod verify**: the `jd_mirror` signal now captures the actual copied passage + context from BOTH resume and JD (separate `copied_text`/`jd_passage`, original casing/punctuation), surfaced as an "In résumé / In job posting" highlighted block in the email banner on ALL bands incl. green Clear, and additively appended into the Bullhorn note (rides existing note gating — only writes when notes are enabled). **NO scoring/banding/threshold/stance change** (22/100 stays green Clear). Prod boot clean, no errors. **Post-deploy verify (24–48h, ~2026-06-03)**: eyeball the FIRST real verbatim-flagged recruiter email — confirm the resume-vs-posting block renders correctly (highlighted, escaped, readable). Logs can't verify rendering; needs a human look. Also re-confirm $0-AI (no cost/throughput change).

### Active — June 1 Cost Lab (audits live, evaluation pending)
- **Status (2026-06-01, verified via prod read-replica)**: Screening re-enabled; both audits firing in `screening_ab_log` (schema audit `|strict`, L2 cache audit `|cache_optimized`). **Telemetry gap RESOLVED** — `screening.scoring` + `.shadow` writing to `openai_call_log` again (were zero since 2026-05-11), so telemetry now captures the dominant cost surface. Full turn-on record + playbook in `docs/archive-2026-06.md`.
- **Cost standing (billing-true, corrected 2026-06-02)**: telemetry dashboard had been under-reporting $ by ~1.6x due to a stale `gpt-5.4` PRICING tuple (was 1.25/0.125/10.00; real OpenAI rates ~2.50/0.25/15.00). FIXED in `services/openai_helper.py` (affects NEW rows only; historical dashboard $ stay undercounted). Billing-true June run-rate while audits on ≈ **$370-400/day** (telemetry ≈ $240-260/day × 1.6). Top spender `screening.scoring` (~70%); **output tokens are ~78% of per-scoring-call cost** (avg ~2,080 out tok/call @ $15/Mtok); prompt-cache hit ~85%.
- **Billing reconciliation — RESOLVED 2026-06-02**: the historic 6-7x multiplier is gone; the residual ~1.6x was purely the stale pricing table (token counts were always accurate). Method + finding in `.agents/memory/telemetry-billing-gap.md`.
- **Still pending**: **pricing-fix verify** — after republish, confirm new `openai_call_log` rows show ~$0.040/scoring-call and the dashboard matches the OpenAI bill ~1:1.
- **Cutover eval EXECUTED 2026-06-02 (prod read-replica; ~1,640 schema + ~6,120 cache valid pairs) — BOTH EXPERIMENTS FAIL → recommend STOP, no cutover**:
  - (A) **Schema/output-token diet (`loose`→`strict`)**: mean score_delta **+4.56** (criterion ±1 ✗), stddev **9.39** (≤4 ✗), **~34% of pairs swing ≥10 pts**, strict arm scores systematically HIGHER; output tokens only **1,932 vs 2,133** prod = **~9% cut** (target ~45% ✗). Flip rate 0.79% ✓, hi-score (≥90) demotes 0 ✓, parse errors 0 ✓. Net: barely trims tokens AND fails score parity → **DO NOT flip `SCREENING_RESPONSE_FORMAT=strict`**.
  - (B) **L2 prompt-cache layout (`legacy`→`cache_optimized`)**: mean **+3.67** (±2 ✗), stddev **9.74** (≤5 ✗), ~32% swing ≥10 pts; AND the cache upside is **already captured** — prod `legacy` layout already shows **85.6% prompt-cache hit** (vs the 43.8% baseline the opt was meant to fix; target ≥70%) → negligible incremental savings. Flip 0.52% ✓, demotes 0 ✓, shadow-err 0.15% ✓ → **DO NOT flip `SCREENING_PROMPT_LAYOUT=cache_optimized`**.
  - **Root cause**: gpt-5.4 scoring has high inherent run-to-run variance (~7 pt mean-abs, ~9-10 sd between two near-identical calls) + a small systematic upward bias from reformatting. Format micro-optimizations fight model noise for little/no $. Practical qualification impact is tiny (flip <1%, zero high-score demotes), but neither pays off on cost.
  - **Action (USER — saves ~$80-130/day billing-true immediately, ZERO quality impact)**: set **`SCREENING_SCHEMA_AUDIT_ENABLED=false`** AND **`SCREENING_PROMPT_CACHE_AUDIT_ENABLED=false`** + republish to stop the shadow A/B scoring calls. The eval is done; no reason to keep paying for it. Remaining June spend is real screening volume + transient backlog catch-up (self-resolving), not waste. Prompt-cache already excellent (85.6%); proven safe levers are exhausted — further squeezing needs a product decision (e.g., gpt-4.1-mini routing) and would risk quality.
- **Cost-spike investigation (2026-06-02, prod read-replica)**: user flagged a June spend spike (OpenAI dash showed ~$497 by 2:21pm Jun 2). Diagnosed: NOT a bug. Billing-true daily (est_cost ×1.6): Jun 1 ≈ **$194**, Jun 2 pace ≈ **$650-850/day**. Drivers: (a) **real screening volume nearly doubled** day-over-day (vetted 311→512, matches 2,205→4,684) — amplified by post-outage **backlog catch-up** (screening was OFF May 11–Jun 1; ~3,400 ParsedEmail still pending unvetted, though cutoff filter may exclude the oldest); per-scoring-call cost is **normal** (~$0.04, ~2,100 out tok/call) — volume, not per-call inflation; (b) the **audit shadow** (`screening.scoring.shadow`, the A/B second-scoring call) adds **~20-30% on top of scoring ≈ $80-130/day billing-true**, purely to feed the cutover eval — controlled by `SCREENING_SCHEMA_AUDIT_ENABLED` / `SCREENING_PROMPT_CACHE_AUDIT_ENABLED` secrets + republish. **User decision (2026-06-02)**: leave everything as-is (the ~20% experiment cost is critical); will return in **~2 days (~June 4) for a cost checkpoint** where the agent should re-evaluate what can be tweaked/turned off for savings. **Agent: proactively raise this at the next session.**

### Active — Operational
- **Customer-sizing cost calculator (in progress)**: Unit-economics in `docs/cost-capacity-model-2026-05.md`. Per-event costs (billing-true, 7x buffer until reconciliation): vetted candidate ~$0.95, match ~$0.069, job ~$1.90/mo, email ~$0.006. Calculator wiring queued.
- **Legacy `.doc` resume extraction — FIXED 2026-05-30, pending prod verify**: pure-Python `olefile` extractor (`_try_olefile_doc` in `utils/doc_extraction.py`) runs FIRST in the OLE2 branch (antiword only a fallback — it EIOs in the prod Nix runtime). Printable-ratio guard (≥0.80) rejects binary garbage. **Post-deploy verify**: confirm a real `.doc` parses (`✅ olefile extracted N chars`); watch for `olefile .doc parse failed` debug lines.
- **Inbound "Parse Failure — None None" alert flood — FIXED 2026-06-02, pending prod verify**: failures spiked ~0–1/day → 22+/day; 29/31 in a 24h window were noise (blank `sender_email`, blank `subject`, `source_platform='Other'`, no attachment, nothing extractable). (A) `email_inbound_service/processing_mixin.py::process_email` now records such non-candidate emails as `ParsedEmail.status='ignored'` (audit kept) and **skips** the admin alert; real candidates (with attachment, or with both sender AND subject) still alert. (B) `resume_parser.py::_extract_docx_with_formatting` null-guards `para.style.name` (was `None` → AttributeError swallowed → whole DOCX returned empty). **Post-deploy verify**: alert volume drops; `status='ignored'` rows appear with the noise fingerprint; spot-check that no real candidate was ignored. **Deferred (Economy hardening)**: narrow the DOCX broad-except so future parser failures are diagnosable instead of silently empty; add an `ignored`-volume telemetry counter.
  - **Deploy status (2026-06-02 ~12:17 UTC)**: shipped live + post-deploy check run. Prod replica confirmed: app healthy (clean boot, scheduler + Bullhorn auth OK), real candidates still completing. 23 noise failures earlier on 2026-06-02 ALL match the gate fingerprint (no attachment, no name/contact, blank sender+subject, source `Other`, `candidate_name='None None'`) and are confirmed in-scope; verified in live code that the gate keys off real extracted name/contact (None here), so `'None None'` doesn't fool it. **Still 0 `ignored` rows** — only because all 23 failures predate cutover (last at 12:06:32) and no noise email has landed since. **Pending live confirmation** (user re-checks ~30–60 min, AND fold into 24–48h check): first post-cutover `ignored` row appears + admin alert volume drops. Verify query: `SELECT created_at,status,(sender_email=''),(subject=''),source_platform FROM parsed_email WHERE created_at > '2026-06-02 12:06:32' ORDER BY created_at;` plus `count(*) FILTER (WHERE status='ignored')` should go > 0.

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
- **Fraud / Fake-Candidate Detection (Phase 1, advisory)**: Deterministic ($0 AI) candidate-integrity scoring. Pure evaluators in `fraud_detection/signals.py` (disposable email, contact anomalies, work-history impossibilities, resume-content reuse, identity reuse, embedding near-dup, application velocity) → banded by configurable thresholds (default High-Risk≥75, Review 40–74, Clear<40). `fraud_detection/engine.py` (`FraudSignalEngine.assess`) gathers DB facts in **fully isolated SQLAlchemy sessions** (never touches the caller's vetting txn — guarantees advisory/non-blocking), persists `CandidateFraudAssessment`, and writes a band-aware, vendor-neutral Bullhorn note (action="Candidate Risk Review") gated by `fraud_bullhorn_note_enabled` — High-Risk only by default, or all bands when `fraud_note_all_bands_enabled` is also on. Surfaced via recruiter-portal badge (latest-assessment-wins) and an all-band advisory email banner (green Clear / amber Review / red High-Risk) shared across all three recruiter-email surfaces (`screening/notification.py::_build_fraud_banner_html`). Pipeline hook in `candidate_vetting_service/processing.py` is gated by `fraud_detection_enabled` and fully fail-soft. Config in `/vetting/settings`.
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
