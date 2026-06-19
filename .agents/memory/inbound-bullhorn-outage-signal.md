---
name: Inbound→Bullhorn outage — true health signal & silent-completed failure
description: During a Bullhorn auth outage, inbound applications get marked 'completed' with NULL bullhorn IDs (silent), not 'failed'. Measure health by non-null bullhorn_candidate_id; recovery can't key on status.
---

# Inbound→Bullhorn outage — the status column lies

**During a Bullhorn auth outage, `status` is NOT a reliable health signal.** When Bullhorn auth is down/locked, inbound emails are mostly NOT marked `status='failed'`. Most are marked **`status='completed'` with NULL `bullhorn_candidate_id` / `bullhorn_submission_id`** — the candidate write is skipped/swallowed without raising, so `process_email` proceeds to "completed." A minority land on `failed`, and a few stick at `processing` (worker hang/timeout). So an outage can look "healthy" if you only count `completed`.

**True health signal = non-null `bullhorn_candidate_id`, NOT `status`.** To measure real inbound→Bullhorn throughput or detect an outage, count `parsed_email` rows with `bullhorn_candidate_id IS NOT NULL` per hour. Baseline: ~88–94% of inbound rows write a candidate. An outage shows a hard cliff to ~0% writes while `total` and `completed` stay normal.
**Observed 2026-06-19:** writes cliffed between 02:00→03:00 UTC (10/12 → 0/12) while `completed` kept being set every hour. (NB: prod `received_at` is UTC; 03:00 UTC ≈ 11pm US-Eastern the prior evening — so a "locked June 18" report can show up as an early-June-19-UTC cliff.)

**Why it matters for recovery (hazard):** the silent-completed rows defeat both auto-recovery paths — the `message_id` dedupe (any existing row → returns `duplicate`, skip) AND a status-keyed backfill both treat them as "done." So neither the normal mailbox-pull retry nor `run_mailbox_backfill` will re-drive them. Recovering applications lost in an outage requires targeting `parsed_email` rows in the outage window with `bullhorn_candidate_id IS NULL` and forcing a reprocess that bypasses the message_id dedupe (or resetting/clearing those rows first). Do NOT assume it auto-heals on unlock.

**How to apply:** when asked "are candidates reaching Bullhorn during an outage," query `parsed_email` hourly for non-null `bullhorn_candidate_id` to find the exact write-stop time and the affected row set; plan recovery off that row set, not off `status`.

## Recovery run — success signals & a benign-looking gotcha
The recovery flow (`POST /api/email/outage-recovery`, dry_run→live) resets stranded rows to `status='recovery_superseded'` + clears `message_id`, then `run_mailbox_backfill(since=outage_start)` re-fetches the window and re-drives each via the normal pipeline.
- **Success = `still_stranded` → 0 AND the `outage_recovery_in_progress` marker (VettingConfig) goes empty.** The marker holds a start timestamp while running and is cleared on graceful finish; empty marker = backfill released the lock cleanly. Don't key success on logs alone.
- **`reset_count` can exceed the unique re-driven rows — this is correct, NOT lost candidates.** 2026-06-19: 69 reset → 50 new `completed` (all with bullhorn_candidate_id) + 12 `duplicate` (all with id) = 62 unique. The ~7 gap is cross-route dups (the dual ingestion doors created 2 ParsedEmail rows for 1 email during the outage); on single-door mailbox re-drive they collapse via Message-ID dedupe. Do NOT alarm that "7 went missing" — `still_stranded=0` is authoritative.
- Backfill is slow (~5–6s/email: AI parse + candidate/work-history/note/file/submission writes); 69 took ~7 min. Re-query a few times; trust the DB counts over impatience.
