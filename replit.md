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

### Active — 2026-05-14 ships (24-48h verification window)
- **Canadian Clearance Inference Enforcement**: Verify (1) `canadian_clearance_analysis.triggered=true` block appears in scoring JSON for ≥1 real candidate on a clearance-required job; (2) `score_adjustment="No penalty applied"` for candidates meeting tier; (3) zero recruiter complaints about good Canadian candidates downgraded; (4) auditor revet-recommendation rate on clearance jobs drops vs prior baseline. Query: `SELECT match_score, ai_response::jsonb->'canadian_clearance_analysis'->>'triggered', ai_response::jsonb->'canadian_clearance_analysis'->>'score_adjustment' FROM candidate_match WHERE created_at > '2026-05-14 18:00' AND ai_response::jsonb->'canadian_clearance_analysis'->>'triggered' = 'true' ORDER BY created_at DESC LIMIT 20;`
- **Per-Recruiter Location-Review Toggle**: Verify (1) any `event=notification_pref_updated` markers (recruiters trying it out); (2) any `event=location_review_pref_filtered` markers (filter actually engaged); (3) zero complaints about missing Location-Review emails on opt-IN jobs. Query: `SELECT COUNT(*), enabled FROM recruiter_notification_pref WHERE notification_type='location_review' GROUP BY enabled;`
- **Recruiter Transparency batch markers**: Validate `📌 Applied-job context` and `📎 Multi-recruiter resume` log markers appear in production on real qualified matches (≥75%). Capture frequency of "WITHOUT resume attachment" warning — that's the data we deployed observability to gather.
- **Auditor stuck-row fix verification** (commits `862888b0` + `7deab384`, deploys `d346b9b3` + `b7af5bf0`): No new `revet_triggered`-without-resolution since deploy. Query: `SELECT COUNT(*) FROM vetting_audit_log WHERE created_at > '2026-05-14 14:45' AND action_taken='revet_triggered' AND revet_new_score IS NULL;` — goal is zero past 24h.

### Active — Stuck Revet Rows (Bug A + Bug B fix shipping 2026-05-15 PM)

**Root cause finally isolated (combined #2+#4 investigation):**
- **Bug A — non-parsed_email revet path leak**: `RevetMixin._trigger_revet` filtered `CandidateVettingLog` by `parsed_email_id`, so PandoLogic-note + Matador candidates (no `parsed_email_id`) had their old vlog survive the cascade — and the upstream detectors only look back 5–10 min, so the candidate was never re-discovered. Audit row stayed `revet_triggered` / `revet_new_score=NULL` forever. Explains 6242, 6251, 6387 + 4 newly-discovered stuck rows (6707, 6722, 6745, 6777).
- **Bug B — auditor job-id mismatch**: When no `CandidateJobMatch.is_applied_job=True` row existed, the auditor fell back to the candidate's highest-scoring match and stamped THAT job_id onto the audit row. The next re-vet's `CandidateJobMatch` is keyed to the actual applied job — so `backfill_revet_new_score` could never align them. Explains row 5918 (audit job_id=34967 vs applied=34708).

**Fix A shipped (Power-tier, 2026-05-15 PM)** — 5 files:
- `vetting_audit_service/helpers.py` — new `clear_candidate_vetting_state(candidate_id)` filters by `bullhorn_candidate_id` (not `parsed_email_id`); cascades EmbeddingFilterLog/EscalationLog/CandidateJobMatch/CandidateVettingLog deletes + resets `parsed_email.vetted_at`. Includes FK-safety pre-check that raises `RuntimeError` if ANY `ScoutVettingSession` (active OR terminal) references the candidate's vetting logs (FK is NOT NULL without CASCADE — would raise IntegrityError at commit). Verified on prod: 0/8 currently-stuck candidates have blocking sessions.
- `vetting_audit_service/revet_mixin.py` — `_trigger_revet` now delegates to the helper (no more parsed_email-id-only filter).
- `vetting_audit_service/__init__.py` — exports `clear_candidate_vetting_state`.
- `screening/detection.py` — new `detect_pending_revet_candidates(lookback_days=7, max_candidates=10)` uses `VettingAuditLog` itself as the durable revet queue. Skips rows that already have a post-audit vlog (those are stuck for a different reason — Bug B). Calls the helper to clear stale state before enqueue (bypasses `_self_screen_cooldown_active`).
- `candidate_vetting_service/cycle.py` — wires the new detector into `run_vetting_cycle` after the pando-note step with id-dedup merge into the candidate list.

**Fix B shipped (Economy-tier, 2026-05-15 PM)** — 1 file:
- `vetting_audit_service/orchestration_mixin.py` — sanity guard: if `applied_match.bullhorn_job_id != vetting_log.applied_job_id` AND both are set, reclassify `revet_triggered` → `revet_skipped_job_mismatch` and append a clear `[Auditor]` note to `audit_finding`. Bumps a new `summary['revets_skipped_job_mismatch']` counter for telemetry.

**Dry-run before deploy** — new detector will pick up **8 stuck rows / 7 candidates / 5 finding_types** on first prod cycle (all show `has_post_audit_vlog=false`):

| Row | Cand | Job | Score | Finding | Audit age (h) |
|---|---|---|---|---|---|
| 5918 | 4659539 Shashank Puli | 34967 | 56 | score_inconsistency | 42.1 |
| 6242 | 4660050 Lekeeta GatlinLewis | 35103 | 8 | employment_gap_misfire | 23.7 |
| 6251 | 4660054 Anila Puli | 35103 | 1 | experience_undercounting | 23.5 |
| 6387 | 4660122 Hugo Hugo | 35077 | 30 | employment_gap_misfire | 19.2 |
| 6707 | 4647505 Ramya Bodapati | 35012 | 41 | score_inconsistency | 9.3 |
| 6722 | 4660183 Deepak Jaiswar | 34839 | 42 | employment_gap_misfire | 8.5 |
| 6745 | 4660185 Balakrishna Nair | 34839 | 9 | false_gap_claim | 8.0 |
| 6777 | 4652892 VIJAYA MADHURI | 34863 | 88 | false_positive_skill_gap | 7.5 |

⚠️ **5918 will be unnecessarily re-vetted once** by the new detector before its `has_post_audit_vlog=true` permanently skips it (Fix B reclassifies *future* mismatches but does not retro-touch row 5918). Cost ~$0.10. **Manual one-line cleanup needed via Render console** to reclassify the existing row:
```sql
UPDATE vetting_audit_log
   SET action_taken='revet_skipped_job_mismatch',
       audit_finding = COALESCE(audit_finding,'') || E'\n\n[Manual reclassify 2026-05-15] Audit job 34967 ≠ applied job 34708; backfill cannot align. Same logic now lands automatically going forward via Fix B.'
 WHERE id=5918 AND action_taken='revet_triggered' AND revet_new_score IS NULL;
```

### Watch-items — 24-48h post-deploy verification window for Fix A + Fix B
- **Fix A — Bug A back-fill**: Within 1–2 vetting cycles (~2–10 min), all 7 currently-stuck non-mismatch rows (6242, 6251, 6387, 6707, 6722, 6745, 6777) should land a fresh `CandidateVettingLog` and `backfill_revet_new_score` should populate `revet_new_score`. Look for log marker `🔁 Pending-revet re-enqueue: candidate {id}`. Query: `SELECT id, finding_type, action_taken, revet_new_score FROM vetting_audit_log WHERE id IN (6242, 6251, 6387, 6707, 6722, 6745, 6777) ORDER BY id;` — goal: zero with `revet_new_score IS NULL` past 30 min.
- **Fix A — overall stuck-row resolution rate**: 24h after deploy, recompute the cluster resolution rate across the May-2026 batch — target ≥97% (vs prior 89.6%). Query: `SELECT COUNT(*) FILTER (WHERE revet_new_score IS NOT NULL OR action_taken LIKE 'revet_skipped_%')*100.0/COUNT(*) AS resolution_pct FROM vetting_audit_log WHERE created_at > '2026-05-13' AND action_taken IN ('revet_triggered','revet_skipped_pre_cutoff','revet_skipped_job_mismatch');`
- **Fix A — no detector loops**: confirm none of the resolved candidates get re-enqueued cycle-after-cycle. Query: `SELECT bullhorn_candidate_id, COUNT(*) FROM candidate_vetting_log WHERE created_at > NOW() - INTERVAL '6 hours' GROUP BY bullhorn_candidate_id HAVING COUNT(*) > 3 ORDER BY 2 DESC LIMIT 10;` — goal: nothing with >3 vlogs in 6h that isn't a known retry pattern.
- **Fix B — new mismatch suppressions**: validate the guard fires on real audits going forward. Query: `SELECT COUNT(*), MAX(created_at) FROM vetting_audit_log WHERE created_at > '2026-05-15 18:00' AND action_taken='revet_skipped_job_mismatch';` — goal: ≥1 within 48h confirming the path is reachable; zero recruiter complaints about "missing" re-vets.
- **AI-cost ripple**: re-vet activity could nudge `screening.scoring` upward 24h after deploy. Threshold unchanged ($130/24h sustained → investigate); a one-day blip from clearing the 7-row backlog (~$1) is expected.
- **Manual SQL pending**: reclassify row 5918 via Render console (one-line SQL above). Until then, 5918 will still appear in `revet_triggered` queries even though the detector won't loop on it.

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
