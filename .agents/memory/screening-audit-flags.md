---
name: Screening audit feature flags storage
description: Where the screening audit/cutover feature flags live and how to change them safely.
---

# Screening audit / cutover feature flags

The screening audit + A/B cutover feature flags are stored in the Replit **secret store**, NOT as plain env vars:
`SCREENING_SCHEMA_AUDIT_ENABLED`, `SCREENING_PROMPT_CACHE_AUDIT_ENABLED`, `SCREENING_AB_SHADOW_ENABLED`, `EMBEDDING_AB_SHADOW_ENABLED`.
Code reads them via `os.environ.get(...)` (e.g. `screening/prompt_builder.py`).

**Why it matters / how to apply:**
- `viewEnvVars({type:"env"})` returns nothing for them — they only show up under `viewEnvVars({type:"secret"})`, and only as PRESENT/ABSENT (values are never readable).
- The agent **cannot read or set** secret values. To flip one, have the user set it (e.g. `requestEnvVar` or the Secrets UI). Don't try `setEnvVars` — it can't touch secrets.
- A secret change does **not** reach the live deployment until the user **republishes**. After republish, verify in prod logs via the boot-check line `🔎 schema-audit boot-check: SCREENING_SCHEMA_AUDIT_ENABLED='...' (enabled=...)` and by querying `screening_ab_log` for the audit's row tags.
- Dev and prod use **separate databases**; a dev boot showing `vetting_enabled=false` etc. is just the dev DB, not the production config — verify prod settings against the prod read-replica.
