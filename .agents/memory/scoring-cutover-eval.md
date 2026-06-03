---
name: Scoring cost-cutover eval result
description: Why the two queued screening.scoring cost experiments (output-token/schema diet, L2 prompt-cache layout) both failed their cutover criteria and should not be flipped.
---

# Screening.scoring cost-cutover eval — both levers FAIL (evaluated 2026-06-02)

Evaluated on the prod read-replica from `screening_ab_log` (~1,640 schema pairs, ~6,120 cache pairs).

## Verdict: do NOT cut over either; stop the audits.

- **Schema / output-token diet** (`{model}|loose` vs `|strict`): the strict JSON-Schema arm only trims output ~9% (1,932 vs 2,133 tok — far short of the ~45% target) AND drifts scores (mean delta +4.56 vs ±1 bar, stddev 9.4 vs ≤4 bar, ~34% of pairs swing ≥10 pts, systematically higher). Barely saves, fails parity.
- **L2 prompt-cache layout** (`|legacy` vs `|cache_optimized`): the cost premise is already obsolete — prod `legacy` already runs at **85.6% prompt-cache hit** (the opt was meant to lift a 43.8% baseline to ≥70%). So negligible incremental savings, and it also drifts scores (mean +3.67 vs ±2, stddev 9.7 vs ≤5).

## Why (durable lesson)
gpt-5.4 scoring is **highly non-deterministic**: two near-identical scoring calls differ by ~7 pt mean-abs / ~9-10 sd, with a small systematic upward bias when the prompt/response is reformatted. **Format micro-optimizations fight the model's own noise** — they can't clear tight ±1/≤4 parity bars no matter what, and the realizable cost savings are small. Real screening cost is dominated by call *volume*, not per-call shape.

**Why it matters:** before proposing any future "trim the scoring prompt/schema to save tokens" lever, weigh it against this — expect high variance, modest token savings, and remember prompt-cache is already ~85%+. Bigger savings need a product decision (e.g., route low-confidence candidates to gpt-4.1-mini), not reformatting.

## How to apply
- The audits cost ~$80-130/day billing-true (the `screening.scoring.shadow` A/B second call). Once an eval concludes, turn them OFF: `SCREENING_SCHEMA_AUDIT_ENABLED=false` + `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=false` + republish (these live in the SECRET store; user-only).
- Practical qualification impact of both arms was tiny (flip <1%, zero high-score ≥90 demotes), so the failure is about cost-not-worth-the-risk, not a safety incident.
