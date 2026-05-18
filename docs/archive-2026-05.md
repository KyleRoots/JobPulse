# Archive — May 2026 Watch-Items (resolved)

Items archived from `replit.md` once their 24-48h verification windows closed clean. Kept here as historical context for incident review and root-cause reference.

---

## 2026-05-14 ships (verified clean by 2026-05-15 PM checkpoint)

- **Canadian Clearance Inference Enforcement**: Verification routed to recruiter-inbox feedback + auditor dashboard rather than SQL-queryable JSON instrumentation. SQL-queryable instrumentation deferred (low ROI — three of four verification sub-checks are observable through existing channels). Original verification line was: `SELECT match_score, ai_response::jsonb->'canadian_clearance_analysis'->>'triggered', ai_response::jsonb->'canadian_clearance_analysis'->>'score_adjustment' FROM candidate_match WHERE created_at > '2026-05-14 18:00' AND ai_response::jsonb->'canadian_clearance_analysis'->>'triggered' = 'true' ORDER BY created_at DESC LIMIT 20;`
- **Per-Recruiter Location-Review Toggle**: 1 opt-in observed; zero complaints. Verified.
- **Recruiter Transparency batch markers**: `📌 Applied-job context` and `📎 Multi-recruiter resume` log markers verified in production at expected frequency.
- **Auditor stuck-row fix verification** (commits `862888b0` + `7deab384`, deploys `d346b9b3` + `b7af5bf0`): 56/56 resolved. Verified.

---

## Stuck Revet Rows — Bug A + Bug B (shipped 2026-05-15 PM, verified 100% resolution)

**Root cause (combined #2 + #4 investigation):**

- **Bug A — non-parsed_email revet path leak**: `RevetMixin._trigger_revet` filtered `CandidateVettingLog` by `parsed_email_id`, so PandoLogic-note + Matador candidates (no `parsed_email_id`) had their old vlog survive the cascade — and the upstream detectors only look back 5–10 min, so the candidate was never re-discovered. Audit row stayed `revet_triggered` / `revet_new_score=NULL` forever. Explained 6242, 6251, 6387 + 4 newly-discovered stuck rows (6707, 6722, 6745, 6777).
- **Bug B — auditor job-id mismatch**: When no `CandidateJobMatch.is_applied_job=True` row existed, the auditor fell back to the candidate's highest-scoring match and stamped THAT job_id onto the audit row. The next re-vet's `CandidateJobMatch` is keyed to the actual applied job — so `backfill_revet_new_score` could never align them. Explained row 5918 (audit job_id=34967 vs applied=34708).

**Fix A shipped (Power-tier)** — 5 files:
- `vetting_audit_service/helpers.py` — new `clear_candidate_vetting_state(candidate_id)` filters by `bullhorn_candidate_id` (not `parsed_email_id`); cascades EmbeddingFilterLog/EscalationLog/CandidateJobMatch/CandidateVettingLog deletes + resets `parsed_email.vetted_at`. Includes FK-safety pre-check that raises `RuntimeError` if ANY `ScoutVettingSession` (active OR terminal) references the candidate's vetting logs (FK is NOT NULL without CASCADE — would raise IntegrityError at commit).
- `vetting_audit_service/revet_mixin.py` — `_trigger_revet` now delegates to the helper.
- `vetting_audit_service/__init__.py` — exports `clear_candidate_vetting_state`.
- `screening/detection.py` — new `detect_pending_revet_candidates(lookback_days=7, max_candidates=10)` uses `VettingAuditLog` itself as the durable revet queue. Skips rows with post-audit vlog (Bug B cases). Calls helper to bypass `_self_screen_cooldown_active`.
- `candidate_vetting_service/cycle.py` — wires the new detector into `run_vetting_cycle` after the pando-note step with id-dedup merge.

**Fix B shipped (Economy-tier)** — 1 file:
- `vetting_audit_service/orchestration_mixin.py` — sanity guard: if `applied_match.bullhorn_job_id != vetting_log.applied_job_id` AND both are set, reclassify `revet_triggered` → `revet_skipped_job_mismatch` and append `[Auditor]` note to `audit_finding`. Bumps `summary['revets_skipped_job_mismatch']` counter for telemetry.

**Dry-run before deploy** — detector picked up 8 stuck rows / 7 candidates / 5 finding_types on first prod cycle:

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

**Outcome**: All 8 stuck rows auto-resolved (5918 too — manual SQL was a no-op, returned 0 rows). Cluster resolution rate moved from 89.6% → **100% (95/95)** since 2026-05-13. No detector loops observed.

---

## 2026-05-15 PM ships (verified clean 2026-05-18, 72h checkpoint)

All four items below cleared their watch-windows with 100% pass rate. Archived from `replit.md` 2026-05-18.

### Task A — Scoring shadow killswitch (Economy)
**Files**: `embedding_service.py::_shadow_enabled()`, `screening/prompt_builder.py::_shadow_screening_enabled()`.
Both now check env `SHADOW_LOGGING_DISABLED` (default `'true'`). When disabled (default), both shadow paths are off regardless of legacy `EMBEDDING_AB_SHADOW_ENABLED` / `SCREENING_AB_SHADOW_ENABLED` flags. Embedding A/B reached 55,961 comparisons (85.1% agreement, 0.7% false-neg); scoring shadow accumulated sufficient data — further accumulation was just $278/mo of cost.
**To restore for periodic regression checks**: `SHADOW_LOGGING_DISABLED=false` in deployment secrets.
**72h verification (2026-05-18)**: Last shadow call 2026-05-17 18:37 UTC. Zero shadow calls on 2026-05-18. Cost line decaying through the 24h trailing window as expected. ✅

### Task B — Prompt-cache audit harness (Power)
**File**: `screening/prompt_builder.py`.
Added `build_scoring_user_prompt(layout=...)` with two semantically-equivalent layouts: `legacy` (date at start — current production) and `cache_optimized` (date at end, `location_instruction` moved AFTER stable JOB DETAILS, so the cacheable prefix grows from system_message-only [~3K tokens] to system_message + per-job content [~4.5K tokens]). Active layout chosen by env `SCREENING_PROMPT_LAYOUT` (default `legacy`). Cache-audit shadow gated by env `SCREENING_PROMPT_CACHE_AUDIT_ENABLED` (default off, independent of `SHADOW_LOGGING_DISABLED`); when on, fires fail-soft same-model alt-layout call and logs to `screening_ab_log` with `prod_model`/`shadow_model` tagged `{model}|{layout}` (e.g., `gpt-5.4|legacy` vs `gpt-5.4|cache_optimized`).
**72h verification (2026-05-18)**: Audit currently PAUSED (env flag false, set 2026-05-17 after OpenAI $2K cap hit). Will be re-enabled in June for full L2 validation — see active L2 reminder in `replit.md`.

### Task #95 — Recruiter notification ledger (Economy)
**Files**: `models/vetting.py`, `models/__init__.py`, `alembic/versions/s3m4n5o6p7q8_add_recruiter_notification_ledger.py`, `screening/notification.py`, `tests/test_recruiter_notification_dedupe.py`.
New `RecruiterNotificationLedger` table living OUTSIDE the auditor cascade, keyed on `(bullhorn_candidate_id, bullhorn_job_id, notification_type)` with a 24h dedupe window per type (qualified / prestige / location_review get separate namespaces). Three module-level helpers (`_ledger_recently_sent_pairs`, `_record_ledger_sent`, `_filter_matches_by_ledger`) applied at all three send sites. Suppressed matches still get `notification_sent=True` so flag-based dedupe continues. Emits `event=recruiter_email_suppressed_already_sent` log marker.
**Root cause**: auditor's `clear_candidate_vetting_state` cascade (Bug A fix) deletes `CandidateJobMatch` rows that carried `notification_sent=True`; next cycle creates fresh matches with `notification_sent=False`; note path correctly de-dupes via Bullhorn-side check, but email path had no equivalent durable check. Symptom: Justin Chuang 4660264 received two "Qualified Candidate Match" emails minutes apart, only one Bullhorn note.
**Tests**: 9 new pass; 18 existing email-enhancement + 5 email-dedup tests still pass.
**72h verification (2026-05-18)**: 56 lifetime ledger rows (43 qualified + 13 location_review). Growing as expected. No recruiter complaints about duplicate or missing emails. ✅

### Task #98 L3 — requirements_extract cache layout (Economy, HYPOTHESIS DISPROVED 2026-05-17)
**File**: `screening/prompt_builder.py::extract_job_requirements`.
Moved all static instruction content (focus areas, anti-hallucination rules, format spec, exclusions) BEFORE variable per-job content (`JOB TITLE` + `JOB DESCRIPTION`). Added anti-injection footer. 11/11 tests pass. Deployed 2026-05-15 PM.
**Post-deploy result — FAILED**: Cache hit rate still **0.0%** across 5 days (May 13-17), including 2 full days post-deploy. **Root cause**: avg input tokens = 965 (min 680, max 1611), but OpenAI prompt caching requires ≥1024-token static prefix to be eligible. Our static prefix (~70-token system_message + ~600-token instructions) = ~670 tokens, below threshold even on calls where total prompt exceeds 1024 tokens. Layout reorganization was correct in principle but irrelevant in practice — prefix too short.
**Decision**: Accept L3 as a no-op. `screening.requirements_extract` is only $44/30d (6.6% of spend); hypothetical 30% cache hit would save only ~$13/mo. Cost-cut energy redirected to L2.
**Do not re-test L3 unless the prompt grows materially** (e.g., if anti-hallucination rules expand to push static prefix past 1024 tokens).

### L2 — screening.scoring cache cutover playbook (referenced by active June reminder in `replit.md`)

When re-enabling audit in June, follow this validation playbook BEFORE flipping `SCREENING_PROMPT_LAYOUT=cache_optimized`:

1. Set `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=true` (leave `SCREENING_PROMPT_LAYOUT=legacy` so prod is unaffected).
2. After 24h target ≥200 **valid** comparison rows (shadow call succeeded). Query: `SELECT COUNT(*) AS valid_pairs, AVG(score_delta) AS mean_delta, STDDEV(score_delta) AS stddev_delta, AVG(ABS(score_delta)) AS mean_abs_delta FROM screening_ab_log WHERE prod_model LIKE '%|legacy' AND shadow_model LIKE '%|cache_optimized' AND shadow_error IS NULL AND shadow_score IS NOT NULL AND created_at > NOW() - INTERVAL '48 hours';`
   Shadow-error rate check: `SELECT COUNT(*) FILTER (WHERE shadow_error IS NOT NULL)*100.0/COUNT(*) AS err_pct FROM screening_ab_log WHERE prod_model LIKE '%|legacy' AND shadow_model LIKE '%|cache_optimized' AND created_at > NOW() - INTERVAL '48 hours';` — investigate if >5%.
3. **Cutover criteria** (ALL must pass):
   - Mean `score_delta` within ±2 points (no systematic bias).
   - Stddev `score_delta` ≤ 5 points (low noise from reordering).
   - `qualified_inferred` flip rate ≤ 3% (≤6 of 200 candidates change qualified/not status).
   - Zero rows where prod_qualified=true and shadow_qualified_inferred=false on candidates with prod_score ≥ 90.
   - Cache-hit telemetry: cache_optimized arm shows ≥70% cache-hit on 2nd+ call within same job batch (vs current 43.8% baseline).
   - **Location-specific regression check**: `SELECT job_title, prod_score, shadow_score, score_delta, bullhorn_candidate_id, bullhorn_job_id FROM screening_ab_log WHERE prod_model LIKE '%|legacy' AND shadow_model LIKE '%|cache_optimized' AND shadow_error IS NULL AND ABS(score_delta) >= 10 ORDER BY ABS(score_delta) DESC LIMIT 30;` — manually inspect ≥10pt deltas; require zero unexplained location-flip cases.
4. If criteria met → `SCREENING_PROMPT_LAYOUT=cache_optimized` + leave audit on for 7d post-cutover (now `prod=cache_optimized` vs `shadow=legacy` for inverse confirmation).
5. If criteria fail → keep legacy, document failure mode, consider whether `location_instruction` ordering specifically caused drift (it's the only semantic-position change; date-to-end is purely lexical).
6. Disable audit when done: `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=false`.

**Expected upside if criteria pass**: ~15-25% reduction on `screening.scoring` (~$12-20/day savings at current throughput, ~$360-600/mo).
