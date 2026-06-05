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

**VALIDATED 2026-06-05 (n=6,302, gpt-4.1-mini shadow vs gpt-5.4, 2026-05-14→06-05):** 97.8% qualify/reject agreement; mean delta +8.54 (mini generous, sd 14.74); base rate ~0.68% qualify (43/6,302). Auto-reject threshold sweep (skip gpt-5.4 when mini < T): T=40 → 74.5% routed cheap, **0/43 qualified lost**; T=35 → 69.3%, 0 lost; T=45 → 82.5% but 1/43 lost. Design confirmed at escalate@40. Per-candidate scoring blend ≈ mini($0.0048) + 0.255×gpt5.4($0.038) ≈ $0.0145 vs $0.038 = ~62% cut on the scoring line → **~$2,000-2,600/mo ongoing** (more transiently while post-outage backlog drains). Caveat: only 43 positives, so ship behind a flag with a canary-confirm phase (run gate but still score gpt-5.4 in parallel a few days, confirm 0 live false-neg) before flipping to savings mode; keep the mini A/B on as ongoing monitor. gpt-5.4 stays sole authority on every escalated candidate.

## Layer-1 model choice: keep it CHEAP (mid-tier backfires)
The layer-1 model runs on 100% of candidates, so its per-call cost dominates the blend; escalation only adds flagship cost on the ~32% that escalate. So a true mid-tier (gpt-4.1, out $8/Mtok ≈ $0.027/call) yields only ~18% savings vs ~56% for a cheap layer — it costs more on everyone than it saves by escalating less. **Decision: round-1 layer-1 = gpt-4.1-mini** (out $1.60; ~$0.005/call; zero code change — harness hardcodes it; 1,150 historical rows for continuity). **Fallback = gpt-5-mini** (out $2.00 — nearly same price but newer/smarter gpt-5 family → likely fewer escalations/near-misses) — use only if mini's escalation rate or near-miss count is marginal at a safe line. Testing a non-mini layer-1 needs a small code knob: the shadow model is hardcoded in `_shadow_screening_pick_model` and `_run_screening_shadow` deliberately bypasses env override.

## Operational gotcha: shared killswitch
`SHADOW_LOGGING_DISABLED` (default `'true'`) is the master gate for BOTH the scoring model-swap shadow AND the embedding shadow. Each also has its own enable (`SCREENING_AB_SHADOW_ENABLED`, `EMBEDDING_AB_SHADOW_ENABLED`). Flipping the master to `false` will restart the embedding shadow too if its enable is truthy — set `EMBEDDING_AB_SHADOW_ENABLED=false` explicitly when running ONLY the scoring experiment, or you pay for two experiments.

**How to apply (run the measurement shadow):** these flags live in the SECRET store (agent can't read/set; user flips + republishes). Want: `SHADOW_LOGGING_DISABLED=false`, `SCREENING_AB_SHADOW_ENABLED=true`, `EMBEDDING_AB_SHADOW_ENABLED=false`, audits stay off (`SCREENING_SCHEMA_AUDIT_ENABLED=false`, `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=false`). Cap via `SCREENING_AB_SHADOW_MAX_CALLS_PER_HOUR` (default 100/hr). Shadow is fail-soft, never affects prod; mini cost is trivial. Paired rows land in `screening_ab_log` (prod_model='gpt-5.4', shadow_model='gpt-4.1-mini'); shadow_qualified_inferred = raw mini score >= 80 (no prod post-processing).

## BUILT 2026-06-05 — live router (flag-gated, off by default)
The cheap-first router is implemented. Control is via **VettingConfig DB keys** (NOT env secrets — set in each environment's DB, prod is separate):
- `layer2_model` — set `'gpt-4.1-mini'` to make mini the first-pass scorer. While this is `'gpt-5.4'` the router is a hard no-op (off), regardless of mode.
- `screening_routing_mode` — `off` (default) | `canary` (gate computed + logged, gpt-5.4 STILL runs on everyone — savings = $0) | `enforce` (mini < threshold skips gpt-5.4 = the savings).
- `cheap_first_reject_threshold` — default `40` (validated sweet spot; T45 lost 1/43).
- The qualify floor the gate protects subtracts the prestige boost (5 pts) on boost-eligible jobs so a cheap-reject can never have been boost-qualified.

**Rollout order:** set `layer2_model='gpt-4.1-mini'` + `screening_routing_mode='canary'` → watch logs for `🚨 CANARY false-negative` (must be 0 over a few days) → flip to `enforce`. Keep the mini A/B shadow ON as ongoing monitor. Router is fail-soft EXCEPT: in canary/enforce, if the gpt-5.4 *escalation call itself* fails, we now RE-RAISE (record job as 0/error, eligible for the zero-score gpt-5.4 reverify) rather than letting the mini analysis qualify — gpt-5.4 stays the sole qualification authority even on infra failures. In off mode the legacy keep-mini fail-soft is preserved byte-identically. Pure logic lives in `candidate_vetting_service/config.py` (`_get_screening_routing_mode`, `_get_cheap_first_reject_threshold`, `_cheap_first_route`, `_cheap_first_qualify_floor`, `_suppress_mini_on_escalation_failure`); wired in `processing.py::analyze_single_job`; covered by `tests/test_cheap_first_routing.py` (13 tests).

**Known pre-existing follow-up (NOT this build):** the zero-score reverify recomputes `is_qualified = new_score >= job_threshold` without the location-barrier guard (`processing.py` ~line 750). Pre-existing on baseline HEAD, architect-confirmed out of scope for the routing change; track separately.
