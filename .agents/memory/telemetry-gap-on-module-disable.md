---
name: Screening scoring telemetry gap across a module-disable period
description: Why screening.scoring stopped writing openai_call_log rows and what restored it.
---

# Screening scoring telemetry can silently stop writing

`screening.scoring` and `screening.scoring.shadow` wrote ZERO rows to `openai_call_log`
from 2026-05-11 through the May budget freeze, even though scoring was firing — this
looked like a far worse problem than the previously-suspected 6-7x telemetry-vs-billing
mispricing (entire call sites missing, not just mispriced). On the **2026-06-01
screening re-enable + republish**, both call sites resumed writing normally (verified:
$29.19 / 1059 rows for `screening.scoring`, $3.78 / 138 rows for `.shadow` on June 1).

**Why it matters / how to apply:**
- When a known-active call site shows zero telemetry rows, suspect the **write path**
  (thread/app-context in the telemetry logger, or a stale deploy that predates a logging
  fix), NOT the pricing math. The dominant cost surface going dark is the higher-severity
  reading.
- A module disable + later re-enable/republish can clear it — the most likely root cause
  was a deployed build that predated a telemetry write fix, refreshed by the June 1
  publish. If it recurs, diff the deployed commit against `main` first.
- Don't declare the historic telemetry-vs-billing multiplier "resolved" on telemetry
  alone — reconcile against the actual OpenAI invoice once a clean billing baseline
  (June 2026) has accrued.
