---
name: Bullhorn outage — background automation blast radius
description: Which scheduled/background jobs stall vs keep running when the Bullhorn connection is down, and which auto-recover vs need manual catch-up. For outage triage.
---

# Bullhorn outage — background automation blast radius

When the Bullhorn API connection is down (account locked / auth failing), background automations split into three buckets. Jobs are registered in `scheduler_setup.py` and run only on the single primary worker (file lock `/tmp/scoutgenius_scheduler.lock` via `fcntl.flock`; every `add_job` is gated by `is_primary_worker`).

## Bullhorn-dependent — STALL during the outage (but auto-recover on next cycle)
These read/write the Bullhorn API; during an outage they fail-soft (log + skip/rollback), then catch up automatically when Bullhorn returns because they re-scan by `dateLastModified` or by local "pending" records — **no manual catch-up needed**:
- Tearsheet monitor (5 min), Requirements maintenance (5 min), Owner reassignment (5 min + daily 2am), Sales-rep name sync (30 min), Duplicate candidate merge (60 min), LinkedIn source cleanup (hourly), Enforce tearsheet jobs public (30 min), Reference-number refresh (120 hr), Candidate data cleanup (15 min), Incomplete rescreen (15 min), Résumé recovery sweep (30 min).
- **Placement Net Margin poller — every ~10 sec (R/W):** the highest-frequency Bullhorn auth caller; this cadence is the lockout accelerant (a wrong/locked password gets hammered fast). Resilience-hardening target = correct interval + auth-failure backoff.
- **AI Candidate Vetting cycle (1 min) + Screening Quality Audit (15 min) + Incomplete rescreen:** Bullhorn-dependent BUT also separately gated OFF by the June screening pause (`vetting_enabled` / `screening_audit_enabled` false) — so idle regardless of the outage.

## Independent — KEEP RUNNING normally during the outage
DB-only / other-service jobs unaffected by Bullhorn: nightly DB backup→OneDrive (2am), XML feed generation + SFTP upload (30 min, uses cached BH data), log/self-heal monitoring, data + activity retention cleanup (3am), monitor-health & environment-health pings, OneDrive knowledge sync (4 hr), stale-ticket escalation, email-parsing timeout cleanup, active-job-id cache warm. Health checks correctly report Bullhorn as 'down'.

## The ONE exception that does NOT auto-recover — inbound application submissions
Mailbox-pull (60 sec) keeps **ingesting** applicant emails to the local `parsed_email` table (Graph-only, works), but the **Bullhorn submission inside `process_email` fails — silently** (row marked `status='completed'` with NULL `bullhorn_candidate_id`; see [inbound-bullhorn-outage-signal](inbound-bullhorn-outage-signal.md)). The `message_id` dedupe then blocks reprocessing, so these need a **manual recovery pass** (target outage-window rows with NULL `bullhorn_candidate_id`). This is the only path that loses work on a Bullhorn outage.
