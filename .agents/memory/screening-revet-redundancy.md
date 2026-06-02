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
