---
name: Inbound stuck-row auto-recovery design
description: Invariants for the email_parsing_timeout reaper's auto-recovery path so a real applicant is never dropped or looped forever.
---

The stuck-`processing` inbound reaper can RE-DRIVE a stuck row (supersede it +
re-fetch the email via mailbox-backfill) instead of only failing it. It is
gated by a default-OFF flag; flag-OFF must stay byte-equivalent to legacy
fail-only. These invariants kept it safe (took several architect rounds):

- **Poison-loop identity must survive successor-row churn.** Each re-drive
  re-fetches the email as a BRAND-NEW row, so any per-row identity
  (id/subject/candidate_email) resets the retry count and can loop a
  malformed/blank-subject poison email forever. The stable identity is the
  email's **Message-ID**: every crashing copy is re-fetched under the same
  Message-ID. Preserve it on the superseded breadcrumb in a dedicated column
  (`recovery_message_id`, stamped BEFORE clearing `message_id`) and count the
  poison cap by it.
  **Why:** subject/email are frequently blank on the exact poison messages.
  **How to apply:** if you ever change the supersede step, keep stamping the
  stable id, or the cap silently stops bounding the loop.

- **A stuck row with NULL message_id cannot be auto-recovered.** No dedupe key
  → the re-fetch can't be linked back, so it would double-submit. Fail it
  visibly for manual review; NEVER supersede it (that strands the applicant with
  no reconcile path). Mirrors the manual outage-recovery, which also requires
  `message_id IS NOT NULL`.

- **Never confirm "handled" without Bullhorn evidence.** After backfill,
  a successor row counts as genuinely handled only if it reached Bullhorn
  (`bullhorn_candidate_id` set) OR was intentionally collapsed
  (`duplicate`/`ignored`/skipped-submitted). A successor that exists but FAILED
  owns the Message-ID (blocks the original's restore) → re-arm the SUCCESSOR to
  `processing` so the reaper retries it (bounded by the poison cap). No
  successor + a backfill failure signal → restore the ORIGINAL to `processing`.

- **A fail-open advisory lock MUST roll back on error.** The cross-route
  candidate-identity `pg_advisory_xact_lock` fails open, but a real DBAPI error
  leaves the txn aborted; without `db.session.rollback()` the next query raises
  and intake is BLOCKED — the opposite of fail-open. No Bullhorn write happens
  before the lock, so rollback can't undo external work.
