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
