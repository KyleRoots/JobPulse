---
name: Screening re-vet redundancy
description: Empirical prod finding — same (candidate, job) re-scored repeatedly; the self-screen cooldown gate doesn't cover the inbound/auditor/scheduled re-vet paths.
---

# Screening re-vet redundancy (empirical, prod read-replica)

~28% of scoring calls over a 30-day window (18% in a 2-day June sample) are repeat
scores of the SAME (bullhorn_candidate_id, bullhorn_job_id) pair. Measured by joining
`candidate_job_match` → `candidate_vetting_log` (the scoring call site doesn't populate
`openai_call_log.entity_id`, so telemetry alone can't see this — use these tables).

## What's legitimate vs waste
- ~63% of redundancy is the candidate genuinely re-applying (2+ distinct non-null
  `parsed_email_id` for the pair) — probably fine.
- ~37% (~4,200 calls/30d ≈ ~$150/mo billing-true @ ~$0.040/call) is the SAME application
  (single/null `parsed_email_id`) re-scored — the actionable waste. ~30% of all re-scores
  fire <1h after the prior one and ~32% return the IDENTICAL match_score (no new info).

## Two distinct mechanisms (from heavy-repeat timelines)
1. **Tight-cluster loops** — same pair scored many times minutes apart (seen: 11× in
   20 min, ~2-min cadence) with sequential/duplicate `parsed_email_id` and a fresh
   vetting_log each time. Smells like the same inbound email re-ingested as multiple
   ParsedEmail rows, or an auditor cascade firing repeatedly. Scores bounce around the
   same value.
2. **Daily scheduled re-vets** — same pair scored ~once/day at the same wall-clock time,
   `parsed_email_id` often NULL. A scheduled/auditor sweep re-scoring already-completed
   candidates.

## Why the existing gate misses it
All re-vet vetting_logs show `status='completed'` and `retry_count=0` (so retry_count is
NOT the cascade counter). The May-2026 skip-gate (`screening.dedup`,
`self_screen_cooldown_minutes` default 60, surfaced in admin_health `tile_skip_gates`)
guards the recruiter "self-screen" path — it does NOT key on / cover the inbound +
auditor/scheduled re-vet paths, so those bypass cooldown.

**How to apply:** before assuming screening volume is irreducible, check this. A fix should
broaden the dedup/cooldown to key on (candidate, job, last-score recency) across the inbound
+ auditor + scheduled paths, while still allowing a genuine re-application (materially changed
resume / new parsed_email) through. Don't suppress legitimate re-applies.

## CORRECTION (2026-06-02) — do NOT add the candidate cooldown to the ParsedEmail detector
Attempted the "obvious" fix: wire `_self_screen_cooldown_active` (candidate-level) into
`detect_unvetted_applications` (the one detector of five that omits it). REVERTED — it is
wrong, and the omission is **by design**, not a bug:
- `tests/test_returning_applicant.py` explicitly asserts the ParsedEmail path MUST re-queue
  (a) a returning applicant with a NEW `parsed_email_id` (even same job) and (b) a different
  job for the same candidate — even when a vetting_log was created ~30 min ago. A candidate
  cooldown (60–120 min) blocks both → breaks intended behavior. The path dedups by
  `parsed_email_id`, NOT by candidate recency.
- The asymmetry is intentional: the 4 Bullhorn-sourced detectors carry the cooldown because
  they re-fire on *spurious* Bullhorn activity (status/owner/note changes) with no new
  application. The ParsedEmail path fires on a *real inbound application email* — each
  distinct email is a genuine candidate action worth screening.
- Re-measured: of the ~509 within-cooldown June re-scores, only ~51 share the same/null
  `parsed_email_id`; ~458 are genuinely distinct applications (intended). The truly-blockable
  waste here is ~$1/day — not worth suppressing real candidate screening.
- Auditor re-vet path is HEALTHY (verified prod replica: guards firing, max ~2/pair in 7d).
**Bottom line:** there is no safe candidate-cooldown fix in the inbound ParsedEmail path. Any
future dedup must be CONTENT/duplicate-email based (same candidate+job+near-identical email
within minutes), not a candidate-recency cooldown. Real cost levers stay the output-token
diet + audit/shadow-off (June cutover), not re-vet suppression.
**Why:** the platform's core value is screening real applicants; a false skip (dropping a
genuine re-application or new-job application) is far costlier than the tiny AI spend saved.

## RE-CONFIRMED 2026-06-06 + Enforce overlap (do not re-pitch this as a big lever)
Fresh 7-day prod numbers: 32.1% of scorings (5,434/16,937) are re-scores of the same
(candidate, job). Split: **~68% (3,710) are LEGITIMATE** distinct-application re-scores
(distinct `parsed_email_id`); only **~32% of the redundancy (1,729 ≈ ~250/day) is same/null-email**
re-scoring — and per the 2026-06-02 correction above most of THAT is still intended (auditor path
healthy; inbound ParsedEmail safe-to-block waste ≈ $1/day). **Enforce (cheap-first mini routing,
live 2026-06-06) further guts the dollar case:** the redundant re-scores that are rejects now route
to mini (~$0.005) instead of gpt-5.4 (~$0.038), ~8× cheaper, so dedupe's marginal $ dropped from the
old gross "~$70-80/day" headline to ~single digits/day. **Conclusion: re-vet dedupe is NOT
low-hanging fruit.** The headline 32% is mostly real candidate activity, the safe slice is tiny, and
Enforce already discounted the rest. Any future work here must be content/duplicate-email based (not a
candidate cooldown) and is low priority vs monitoring Enforce + letting the backlog drain.
