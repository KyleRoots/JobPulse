---
name: Cheaper-model scoring experiment (gpt-4.1-mini routing)
description: Design + operational gotchas for routing clear-non-match candidates to gpt-4.1-mini instead of gpt-5.4 to cut the OpenAI scoring bill.
---

# Cheaper-model scoring routing

Goal: most scored candidates are obvious rejects (live data: ~70% score <40, only ~1.2% qualify at the ~80 bar), so paying gpt-5.4 ($15/Mtok out) on all of them is waste. gpt-4.1-mini is ~9x cheaper.

## Chosen design: "cheap-first with escalation" (NOT similarity-triage, NOT trust-mini-directly)
Run gpt-4.1-mini first on every candidate; if mini's score >= an escalation line (~40-50), re-score on gpt-5.4 and use gpt-5.4's verdict. Mini may only ever CONFIRM a clear no; gpt-5.4 stays the sole authority on anyone with potential.

**Why:** historical paired shadow data (gpt-5.4 prod vs mini shadow) shows mini **over-qualifies badly** (e.g. 81 vs 18 "qualified" in one ~1,150-row sample; many false positives) AND compresses toward the middle (pushes weak candidates UP, genuine highs DOWN). So:
- trusting mini's verdict directly → floods recruiters with false positives AND drops real hires (false negatives). NEVER do this.
- mini's upward bias on weak candidates is a SAFETY FEATURE for escalation: anyone with a shred of potential gets pushed past the escalation line and re-checked by gpt-5.4, so the dangerous false-negative (a real qualifier hidden below the line) is near-zero.
- similarity-triage was rejected: embedding similarity isn't logged for *passed* (scored) candidates, and it's a weak predictor of final score.

**Preliminary sizing (n~1,150, ONE 2-day window, only 18 qualifiers — too thin to commit on):** escalate@50 → ~68% of calls stay on mini, 0 real qualifiers missed; escalate@40 → ~50% stay, 0 missed. Implies ~35-50% off the OpenAI bill (~$1,300-1,900/mo). Validate at n>=6,000 across varied days/jobs before any cutover.

## Operational gotcha: shared killswitch
`SHADOW_LOGGING_DISABLED` (default `'true'`) is the master gate for BOTH the scoring model-swap shadow AND the embedding shadow. Each also has its own enable (`SCREENING_AB_SHADOW_ENABLED`, `EMBEDDING_AB_SHADOW_ENABLED`). Flipping the master to `false` will restart the embedding shadow too if its enable is truthy — set `EMBEDDING_AB_SHADOW_ENABLED=false` explicitly when running ONLY the scoring experiment, or you pay for two experiments.

**How to apply (run the measurement shadow):** these flags live in the SECRET store (agent can't read/set; user flips + republishes). Want: `SHADOW_LOGGING_DISABLED=false`, `SCREENING_AB_SHADOW_ENABLED=true`, `EMBEDDING_AB_SHADOW_ENABLED=false`, audits stay off (`SCREENING_SCHEMA_AUDIT_ENABLED=false`, `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=false`). Cap via `SCREENING_AB_SHADOW_MAX_CALLS_PER_HOUR` (default 100/hr). Shadow is fail-soft, never affects prod; mini cost is trivial. Paired rows land in `screening_ab_log` (prod_model='gpt-5.4', shadow_model='gpt-4.1-mini'); shadow_qualified_inferred = raw mini score >= 80 (no prod post-processing).
