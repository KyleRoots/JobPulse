# Cursor Automation — Ops early-warning investigate → PR (DRAFT)

**Status:** Draft only. Not enabled. Human merge required. Never auto-merge / auto-deploy.

Use this instruction text if/when enabling a Cursor Automation that reacts to
ops early-warning signals (or related Railway/log pages). Phase 1 already emails
Kyle; this automation is optional Phase 2+ assist.

## Mission

When an ops early-warning alert (or equivalent log/page) indicates a warn/critical
signal for JobPulse / Scout Genius:

1. **Investigate** the signal using repo memory (`.agents/memory/ops-early-warning.md`,
   `inbound-bullhorn-outage-signal.md`, `bullhorn-outage-automation-impact.md`) and
   production-safe read tools (logs, Railway status, admin health evidence).
2. **Propose a fix** as a focused PR on a feature branch when the root cause is
   clear and the change is additive / low blast-radius.
3. **Stop at PR.** A human merges. Deploy is from `main` via Railway — do not
   claim production is fixed without Railway deploy SUCCESS evidence.

## Hard stops (never do)

- Never auto-merge or auto-deploy.
- Never rotate / unlock Bullhorn credentials (shared API user).
- Never bulk-rewrite qualify notes or force `is_qualified`.
- Never run live inbound outage recovery (`outage_recovery` non-dry-run) without
  explicit human approval.
- Never enable Terra / NeverBounce / Twilio paid paths or flip
  `fraud_contact_validation` on.
- Never claim "deployed" / "live in prod" without Railway deployment SUCCESS for
  the commit on `main`.

## Output expectations

- Short root-cause summary tied to the firing fingerprint/signals.
- PR link when a code change is warranted; otherwise a triage note only.
- Explicit residual risk and what still needs a human.
