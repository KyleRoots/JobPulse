# Archive — June 2026 Watch-Items (resolved / historical)

Items archived from `replit.md` once their playbooks were executed or their
verification windows closed. Kept here as historical context for incident
review and root-cause reference. Still-open threads remain in `replit.md`.

---

## June 1, 2026 Cost Lab — turn-on playbook (EXECUTED 2026-06-01)

Consolidated three independent investigations (schema audit, L2 cache cutover
audit, telemetry-vs-billing reconciliation) into one clean June 1 turn-on event
so they shared a single diagnostic surface and a fresh billing baseline.

**Context**: May 2026 OpenAI budget cap ($2,450) was hit on 2026-05-19. User
disabled the screening module to ride out the rest of May on inbound-only
skeleton mode.

**TURN-ON RESULTS (2026-06-01, verified via prod read-replica)**: Screening
re-enabled + both audit secrets republished. Both audits firing cleanly —
`screening_ab_log` since 2026-06-01: `|strict` (schema audit) 105 rows,
`|cache_optimized` (L2 cache audit) 33 rows. The playbook's "strong prior"
zero-rows failure mode did NOT materialize. Step-8 telemetry gap RESOLVED:
`screening.scoring` ($29.19, 1059 rows) and `screening.scoring.shadow` ($3.78,
138 rows) writing to `openai_call_log` again (were zero since 2026-05-11) —
telemetry now captures the dominant cost surface. Cost standing: ~$35
telemetry-true in first ~13.5h → ~$62/24h projected (GREEN, under $130/24h
investigation threshold); shadow ~$6.7/24h (under $15/24h killswitch, but
running hotter than the original schema-only $5-10/48-72h estimate now both
audits run). Fraud detection (enabled this turn): 160 clear / 4 review / 0
high-risk, ~2.4% review rate, no false-positive storm. The
`🔎 schema-audit boot-check:` log line was NOT retrievable from the fetchable
deployment-log window (rolled off) — DB row counts are the authoritative
confirmation and supersede it.

**Pre-flight (done in May)**:
- `SCREENING_SCHEMA_AUDIT_ENABLED=true` confirmed in deployment secrets (visual verification 2026-05-19).
- `SCREENING_PROMPT_CACHE_AUDIT_ENABLED` already in deployment secrets (flipped to `true` at turn-on).
- Schema audit code (T001-T003), strict schema, 10 tests, harness extension — merged to `main`.
- Diagnostic boot-check + promoted WARNING handler — deployed in commit `b120aadd` / publish `aeca434a`.

**Turn-on sequence (executed in order)**:
1. Re-enable screening module (user-facing toggle).
2. Publish/restart deployment.
3. Within 60s, grep deployment logs for `🔎 schema-audit boot-check:`
   - Expected: `SCREENING_SCHEMA_AUDIT_ENABLED='true' (enabled=True)`.
   - If `'<unset>'`/`enabled=False` → secret wiped during freeze; re-add + republish.
   - If line missing → publish didn't include latest code; force fresh publish from main.
4. Within 30 min, query `SELECT COUNT(*) FROM screening_ab_log WHERE created_at > '2026-06-01 00:00:00' AND shadow_model LIKE '%|strict'` — expect ≥1 row per ~4 scoring calls (25% sampler).
5. If still empty after 30 min, grep deployment logs for `🔎 Schema-audit invocation suppressed` — promoted WARNING + `exc_info=True` shows the exact traceback. (Strong prior: call-site exception was the most likely failure mode based on May 19 diagnostic.)
6. Flip L2 cache cutover audit ON: `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=true` in deployment secrets, republish. Rows tag differently in `screening_ab_log` (`|legacy` vs `|cache_optimized` for cache; `|loose` vs `|strict` for schema) so they don't collide.
7. **48-72h after turn-on**: evaluate both audits against their 6-criterion cutover playbooks (schema in `.local/tasks/output-token-diet.md`; L2 cache in `docs/archive-2026-05.md`). — *Still open at archive time; tracked in replit.md.*
8. **Billing-vs-telemetry reconciliation**: with June 1 as a clean billing baseline, attribute the telemetry-vs-OpenAI-bill gap. May 19 deep-dive showed `screening.scoring` + `.shadow` hadn't written to `openai_call_log` since 2026-05-11 — entire surfaces missing, NOT just the previously-suspected 6-7x mispricing. Investigate: (a) `log_call` thread/app-context handling in `services/openai_helper.py`, (b) silent import failures in `models/openai_telemetry.py`, (c) which call sites silently drop vs write (compare against the working trio: `embedding_service.candidate`, `screening.requirements_extract`, `fuzzy_duplicate_matcher`). — *Telemetry gap resolved on the re-enable; final bill confirmation still open; tracked in replit.md.*

**Audit-window cost expectation**: ~$5-10 total over 48-72h for schema-audit
shadow calls at 25% sample × ~750 daily scoring × extra same-model round-trip.
Disable via env flip if `/admin/ai-cost` shows `screening.scoring.shadow` >
$15/24h.

**Watch-out**: If the screening module is disabled again, the diagnostic
boot-check log line is harmless to leave in place — costs nothing and gives a
one-line truth oracle on every restart.

---

## May 2026 Budget Freeze — closure record

OpenAI monthly cap of $2,450 hit on 2026-05-19; screening disabled by user
through end of May, inbound email parsing kept active. Freeze lifted
2026-06-01 — screening re-enabled, both audits + fraud detection turned on
(see the June 1 Cost Lab turn-on record above). New June billing baseline
accruing. As of 2026-05-18 billing-true monthly was ~$2,300 (capped); the L2
cutover targets a further $360-600/mo reduction.

---

## Bullhorn Search API stale-index pattern (reference, 2026-05-15)

Bullhorn's Search API has a recurring lag vs. its Entity API on tearsheet
membership. Not a defect on our side, but it causes (1) brief feed /
Active-Monitors-UI count discrepancies, and (2) the screening pipeline
re-extracting requirements every cycle for any zombie in Search (~$2-4/day per
zombie). Two sub-patterns:

- **Pattern A — short lag on closed jobs (~5-6 min)**: When a job goes
  `isOpen=False` or is removed via DELETE, Entity API updates immediately but
  Search API can serve the stale row briefly. Auto-removal handles this
  correctly when the row stays in Search.
- **Pattern B — long-lived ghost on `isDeleted=True` jobs (months)**: When a
  JobOrder is hard-deleted, Bullhorn hides it from global UI search BUT Search
  API can keep returning it as a tearsheet member indefinitely. **Diagnostic
  signature**: search bar in Bullhorn UI returns nothing OR a same-numbered
  Company/Candidate; direct `entity/JobOrder/{id}` REST call returns
  `isDeleted: true`. **Resolution**: targeted
  `DELETE entity/Tearsheet/{ts}/jobOrders/{id}` clears instantly (index-only
  op). Example: tearsheet 1531 / JobOrder 34128 cleared 2026-05-15.

**Future task candidate (Economy-tier)**: defensive cooldown in
`incremental_monitoring_service` to skip BOTH DELETE retry + AI requirements
re-extraction for jobs auto-removed in the last N hours, plus a Sentry alert
for "auto-removal repeat offenders" ≥5 cycles AND a daily diff job that flags
any tearsheet where Search count > Entity count for >24h (Pattern B detector).

---

## Mailbox-Pull Ingestion + Résumé Recovery (EMERGENCY, 2026-06-04 — RESOLVED)

**Why**: Replit's GCE load balancer truncates inbound POST bodies (≤4096B), so
the SendGrid Inbound Parse webhook silently lost applicant emails. Fix = PULL
applicant emails from the `apply@myticas.com` O365 shared mailbox via Microsoft
Graph into the EXISTING `process_email` pipeline (DB-flag `mailbox_pull_enabled`,
toggle in `/vetting/settings`, no republish).

**Graph attachment bug — FIXED 2026-06-04**: `get_attachments` used
`$select=...,contentBytes`, but the attachments collection is polymorphic so
Graph returned **400** (fail-soft → `[]`), so ~10 applicants (ParsedEmail ids
11503–11512, since 2026-06-04 07:24:20 UTC) ingested to Bullhorn WITHOUT their
résumé (status=completed, bullhorn_candidate_id set, resume_file_id/filename
NULL). Fixed by removing `$select`.

**Résumé Recovery tool**: "Recover Missing Résumés" button on `/vetting/settings`
(24h/48h/72h/7d selector, default 72h) → re-fetches each affected applicant's
original email by `internetMessageId`, re-parses + attaches the résumé to the
**EXISTING** Bullhorn candidate (NEVER creates a candidate/submission), sets
resume_file_id, then resets them for re-vet + enqueues one cycle. Idempotent:
skips rows that already have a résumé (`resume_file_id IS NULL` gate), commits the
résumé marker on its own commit right after upload, and serialized by a
`resume_recovery_in_progress` single-flight flag.

**Resolution**: republished (Graph `$select` fix + recovery tool), recovery run
for the incident window, and the recovery logic was later generalized into a
scheduled auto-recovery sweep (see the 2026-06-04 closing batch) so post-commit
partial failures self-heal with no human action.

## 2026-06-05 trim — archived June 1 Cost Lab eval, cost-spike, and inbound noise-gate deploy detail

### June 1 Cost Lab — full eval (concluded 2026-06-02; recommend STOP; audit flags now OFF)
- **Telemetry gap RESOLVED 2026-06-01**: `screening.scoring` + `.shadow` writing to `openai_call_log` again (were zero since 2026-05-11), so telemetry captures the dominant cost surface.
- **Billing-true correction (2026-06-02)**: dashboard under-reported $ by ~1.6x due to a stale `gpt-5.4` PRICING tuple (was 1.25/0.125/10.00; real OpenAI ~2.50/0.25/15.00). FIXED in `services/openai_helper.py` (new rows only; historical dashboard $ stay undercounted). Method/finding in `.agents/memory/telemetry-billing-gap.md`. The historic 6-7x multiplier is gone; residual ~1.6x was purely the pricing table (token counts were always accurate).
- **Steady-state run-rate (audits off)**: ≈ **$0.06 / candidate-job match**; natural volume ~1,800-2,200 matches/day → ~$110-140/day → ~$3,400-4,200/mo (~$3,800 typical). Below the original ~$4,700/mo baseline.
- **Cutover eval EXECUTED 2026-06-02 (prod read-replica; ~1,640 schema + ~6,120 cache valid pairs) — BOTH EXPERIMENTS FAIL**:
  - (A) **Schema/output-token diet (`loose`→`strict`)**: mean score_delta +4.56 (target ±1 ✗), stddev 9.39 (≤4 ✗), ~34% of pairs swing ≥10 pts, strict scores systematically higher; output tokens 1,932 vs 2,133 prod = ~9% cut (target ~45% ✗). Flip 0.79% ✓, hi-score demotes 0 ✓, parse errors 0 ✓. → DO NOT flip `SCREENING_RESPONSE_FORMAT=strict`.
  - (B) **L2 prompt-cache layout (`legacy`→`cache_optimized`)**: mean +3.67 (±2 ✗), stddev 9.74 (≤5 ✗), ~32% swing ≥10 pts; upside already captured — prod `legacy` already shows 85.6% prompt-cache hit (vs 43.8% baseline the opt was meant to fix). Flip 0.52% ✓, demotes 0 ✓, shadow-err 0.15% ✓. → DO NOT flip `SCREENING_PROMPT_LAYOUT=cache_optimized`.
  - **Root cause**: gpt-5.4 scoring has high inherent run-to-run variance (~7 pt mean-abs, ~9-10 sd between two near-identical calls) + a small systematic upward bias from reformatting. Format micro-optimizations fight model noise for little/no $.
  - **Action taken**: `SCREENING_SCHEMA_AUDIT_ENABLED` + `SCREENING_PROMPT_CACHE_AUDIT_ENABLED` set false + republished (~2026-06-04).
- **Cost-spike investigation (2026-06-02)**: user flagged ~$497 by 2:21pm Jun 2. NOT a bug. Billing-true daily (est_cost ×1.6): Jun 1 ≈ $194, Jun 2 pace ≈ $650-850/day. Drivers: (a) real screening volume nearly doubled day-over-day (vetted 311→512, matches 2,205→4,684) + post-outage backlog catch-up (screening OFF May 11–Jun 1; ~3,400 ParsedEmail pending); per-call cost normal (~$0.04, ~2,100 out tok/call) — volume not per-call inflation; (b) audit shadow (`screening.scoring.shadow`) adds ~20-30% on top of scoring ≈ $80-130/day, purely to feed the eval. Resolved by the eval conclusion above.

### Inbound "Parse Failure — None None" alert flood — deploy detail (FIXED 2026-06-02)
- Failures spiked ~0-1/day → 22+/day; 29/31 in a 24h window were noise (blank `sender_email`, blank `subject`, `source_platform='Other'`, no attachment). Fix (A): `email_inbound_service/processing_mixin.py::process_email` records non-candidate emails as `ParsedEmail.status='ignored'` (audit kept) and skips the admin alert; real candidates (attachment, or both sender AND subject) still alert. Fix (B): `resume_parser.py::_extract_docx_with_formatting` null-guards `para.style.name` (was `None` → AttributeError → whole DOCX returned empty).
- Deploy 2026-06-02 ~12:17 UTC: app healthy; 23 noise failures predate cutover (last 12:06:32). Verify query: `SELECT created_at,status,(sender_email=''),(subject=''),source_platform FROM parsed_email WHERE created_at > '2026-06-02 12:06:32' ORDER BY created_at;` and `count(*) FILTER (WHERE status='ignored')` should go > 0. Deferred (Economy): narrow the DOCX broad-except so future parser failures are diagnosable; add an `ignored`-volume telemetry counter.
