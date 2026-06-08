---
name: Start Fresh net-new cutover
description: How the "Start Fresh" action draws a clean line so re-enabling screening scores only net-new applicants, and why both detection floors must move together.
---

There are TWO independent detection floors that decide what the vetting cycle scores:

1. **Inbound / ParsedEmail path** — gated by `vetting_cutoff_date` (VettingConfig,
   stored `'%Y-%m-%d %H:%M:%S'`, parsed by `_resolve_vetting_cutoff`). Has a UI field.
2. **Bullhorn-direct detectors** (`detect_new_applicants`, `detect_pandologic_candidates`,
   `detect_matador_candidates`, `detect_pandologic_note_candidates`) — gated by
   `last_run_timestamp` (VettingConfig, stored `isoformat()`). Used as a **hard floor
   with NO clamp**. No UI field. If falsy it falls back to a ~5-min lookback.

**Why this matters:** while vetting is disabled the cycle exits early and never advances
`last_run_timestamp`, so it freezes at whenever vetting last ran. On re-enable the
Bullhorn detectors query `dateLastModified:[frozen_ts TO *]` and re-pull the entire
backlog — `vetting_cutoff_date` does NOT protect this path. So a clean "net-new only"
re-enable must move BOTH floors.

**How to apply:** the `start_fresh()` handler (`/screening/start-fresh`) sets both
`vetting_cutoff_date` and `last_run_timestamp` to "now" in one commit, then enqueues a
cycle. Operationally: enable vetting (and set Routing Mode = Enforce) first, then click
**Start Fresh** at the moment you want the line drawn — that instant becomes the floor.
`detect_pending_revet_candidates` is independent (7-day VettingAuditLog lookback) and is
intentionally not reset.
