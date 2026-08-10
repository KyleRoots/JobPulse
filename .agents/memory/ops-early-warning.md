---
name: Ops early-warning (Phase 1)
description: Health signals + Kyle email with fingerprint/cooldown. Maps existing monitors; blast-radius (what never auto-fix); Cursor Automation investigate→PR only.
---

# Ops early-warning — Phase 1

**Status:** Phase 1 shipped (signals + email). No auto-merge, no deploy self-heal, no credential rotation.

## Goal

Catch high-blast-radius silent failures **before** a human notices on `/admin/health`, without giving automation authority to "fix" production. Alert Kyle (`kroots@myticas.com`, env-overridable) when warn/critical signals fire; suppress repeats via fingerprint + cooldown (same pattern as OpenAI spend alerting).

## Existing monitors (map — extend, don't duplicate)

| Surface | What it already covers | Gap Phase 1 closes |
|---|---|---|
| `tasks.run_vetting_health_check` (10 min) | Bullhorn/OpenAI/DB/scheduler connectivity; consecutive-failure email | Stays green during **successful-but-wrong** paths (silent completed inbound, spend loops, stalled queue while APScheduler reports running) |
| `services.ai_cost_monitor` (30 min) | 24h OpenAI $ warn/critical + cooldown + escalation | Spend only — not inbound/write or job-miss |
| `services.admin_health_service` `/admin/health` | Tiles: DB, scheduler running, BH/OpenAI from last health row, inflight/failed vetting, SFTP, OneDrive, AI cost, skip gates, monthly report | HTTP-only; nobody is paged when a tile turns red |
| `EnvironmentStatus` / `EnvironmentAlert` | Prod URL up/down ping + email | Host reachability only |
| `GlobalSettings.scheduler_last_run_{job_id}` (APScheduler listeners in `app.py`) | Per-job last success/fail stamp for Automation Hub UI | Not compared to expected interval; protected jobs can miss N cycles unnoticed |
| [inbound-bullhorn-outage-signal](inbound-bullhorn-outage-signal.md) | Documented: `status='completed'` + NULL `bullhorn_candidate_id` is the true outage signature | Was documentation/SQL only — now a scheduled metric |
| [bullhorn-outage-automation-impact](bullhorn-outage-automation-impact.md) | Which jobs stall vs keep running | Informs which protected jobs we watch |

Phase 1 **reuses** SendGrid + `health_alert_email` fallback, VettingConfig state stamps (like `ai_cost_alert_last_*`), admin-health thresholds for screening stall, and `scheduler_last_run_*` stamps. It does **not** replace vetting health check or AI-cost alerting.

## Phase 1 signals (highest ROI)

1. **Inbound NULL Bullhorn ID rate** — Among recent `ParsedEmail` with `status='completed'`, elevated share with `bullhorn_candidate_id IS NULL` vs healthy baseline (~6–12% null / ~88–94% write). Outage cliff ≈ ~0% writes while completed volume stays normal. See inbound-bullhorn-outage-signal.md.
2. **Screening stall** — High inflight age, elevated 24h failed rate, or zero completed progress while `vetting_enabled` and scheduler claim healthy (extends `/admin/health` inflight/failed tiles into email). **Zombie guard:** when `completed_last_Nm > 0` and every inflight row is older than `ops_early_warning_stall_zombie_age_min` (default 24h), age/zero-progress do not CRITICAL.
3. **Protected scheduler misses** — `process_bullhorn_monitors`, `candidate_vetting_cycle`, `vetting_health_check`: last-run age exceeds expected interval × N. **Absurd-stamp guard:** ages ≥ `ops_early_warning_miss_stamp_absurd_max_min` (default 24h) are treated as frozen metadata, not live misses. Boot grace for missing stamps.
4. **Optional / included if cheap:** SFTP freshness when automated uploads are enabled (`last_sftp_upload_time` stale) — warn default **90m** (30m upload cadence; 60m was noisy), critical 360m.

Implementation: `services/ops_early_warning.py`, job id `ops_early_warning` (every 15 min), tile `ops_early_warning` on `/admin/health`.

## Alerting contract

- Recipient: `OPS_EARLY_WARNING_EMAIL` env → `ops_early_warning_email` VettingConfig → `health_alert_email` → default `kroots@myticas.com`.
- Severity bands: `none` / `warning` / `critical` (overall = max of firing signals).
- **Fingerprint**: sorted `signal_key:severity` for all non-none signals. Same fingerprint inside cooldown → suppress. New/changed fingerprint → send. Escalation warning→critical → send immediately (ai_cost pattern).
- Fail-soft: never raises into the scheduler; failed SendGrid does **not** stamp state (retry next cycle).
- Observe-only: never throttles screening, never writes Bullhorn, never rotates secrets.

## Blast radius — never auto-fix

These must stay **human-only**. Phase 1 alerts only. Future phases may open PRs; they must not merge/deploy or mutate these without a human.

| Never auto | Why |
|---|---|
| **Bullhorn credential rotate / unlock** | Shared API user across apps ([bullhorn-shared-api-user](bullhorn-shared-api-user.md)); wrong rotate locks everyone |
| **Qualify / note rewrite bulk** | Changes recruiter-visible ATS truth; false qualify emails are irreversible customer harm |
| **Auto-merge / auto-deploy** | Deploy from `main` is Railway-gated; false green after bad merge is worse than a stalled PR |
| **Inbound outage row mass-reset** | Recovery supersede bypasses Message-ID dedupe; wrong window re-creates / double-submits candidates |
| **Terra / NeverBounce / Twilio paid paths** | Cost + compliance; `fraud_contact_validation` stays off unless explicitly enabled |
| **Production SECRET / env flips that gate screening** | e.g. `vetting_enabled`, audit flags — ops flips, not bots |

Safe future automation (still not Phase 1): investigate logs → draft PR → **human merge**; cite Railway deploy evidence before claiming "live".

## Phase 2+ (not built)

- Cursor Automation enabled for investigate→PR only (draft instruction file ships with Phase 1; **not** enabled as auto-merge).
- Deeper feed XML freshness beyond SFTP stamp.
- Multi-tenant per-`environment_id` inbound cliffs.
- Slack/PagerDuty channels.
- Auto-open outage-recovery dry_run ticket (still human to run live).

## How to test

```bash
pytest tests/test_ops_early_warning.py -q
# Manual: Admin → /admin/health → "Ops Early Warning" tile
# Force cycle (primary worker): trigger job ops_early_warning from Automation Hub if exposed, or wait ≤15m
```

Config knobs (VettingConfig, defaults in `seeding/settings.py`): `ops_early_warning_enabled`, thresholds, `ops_early_warning_cooldown_hours`, email.

## Residual gaps

- Quiet nights with few inbound rows: min-sample gate avoids false alarms; a true outage with zero inbound won't fire inbound signal (scheduler/SFTP/screening still can).
- `scheduler_last_run_*` absent until first post-boot execution — grace window after process start (`ops_early_warning_miss_boot_grace_min`, default 10m).
- Admin tile is a snapshot of last evaluation stamp, not a live re-query of every signal on page load (page load runs collectors; email path is the scheduled job).
- No multi-channel page; email only.

## Aug 10 2026 false-positive incident (Phase 1 day-1)

Kyle received CRITICAL emails (~10:11 / ~10:26 ET) while production was healthy:

| Signal | Looked like | Actual |
|---|---|---|
| `screening_stall` CRITICAL (`inflight=1`, `oldest_age_min≈146k`) | Queue stuck | One Apr 30 `processing` zombie (`candidate_vetting_log.id=1165`); completions still flowing |
| `scheduler_missed` CRITICAL (~34d since last run) | Protected jobs dead | Jobs executing in Railway logs; duplicate `global_settings.scheduler_last_run_*` rows (no unique index) froze stamps at ~Jul 7 |
| `sftp_freshness` WARNING (~66m) | Upload lag | Noisy: `automated_upload` is every 30m; warn was 60m |

**Remediation shipped:** mark zombie failed; dedupe `global_settings` + restore unique on `setting_key`; harden `GlobalSettings.get/set_value` to prefer newest and collapse twins; stall ignores zombie-only inflight when recent completions > 0; miss signal ignores absurd frozen stamps; SFTP warn default 90m. Next email cycle should be quiet (or fingerprint-recover) unless a real cliff appears.

Hard stops unchanged: never auto BH rotate / qualify rewrite / auto-merge; Terra/NB stay off.
