---
name: Screening output-token glance
description: How to check avg screening.scoring output tokens after the Aug 10 note-compression deploy (no finish_reason column).
---

# Screening output-token glance (post Aug 10 compression)

**Deploy baseline:** `29cba024` live on Railway JobPulse (compact caps 1800/1200 + clear-reject / related prose tighten).

**Admin UI:** `/admin/ai-cost?hours=168` — per-site row for `screening.scoring` shows **sum** `output_tokens` and `calls`. Avg out ≈ sum ÷ calls. Column labeled "Avg / Call" is **avg $**, not avg tokens.

**SQL (preferred for a precise week glance):**

```sql
SELECT
  COUNT(*) AS calls,
  ROUND(AVG(output_tokens)::numeric, 1) AS avg_out,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY output_tokens) AS p50_out,
  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY output_tokens) AS p95_out,
  MAX(output_tokens) AS max_out
FROM openai_call_log
WHERE call_site_id = 'screening.scoring'
  AND created_at >= NOW() - INTERVAL '7 days';
```

**finish=length:** `openai_call_log` has **no** `finish_reason` column. Truncation shows up in app logs / failed match summaries (`finish_reason=length`), not as a first-class telemetry field. Soft proxy: share of calls with `output_tokens` near the active cap (escalate ~1800, related brief ~1200).

**Do not flip for this check:** Terra shadow stays off (`SCREENING_AB_SHADOW_ENABLED=false`). NeverBounce/`fraud_contact_validation` unrelated.
