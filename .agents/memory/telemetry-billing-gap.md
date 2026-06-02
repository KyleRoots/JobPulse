---
name: Telemetry dollar gap = stale pricing table
description: Why /admin/ai-cost dollars under-report the OpenAI bill, and the method to reconcile it
---

# Telemetry under-reports dollars because the PRICING table is stale, NOT because usage is hidden

The historic "6-7x multiplier" between our `openai_call_log` dollars and the real
OpenAI bill is NOT unmonitored/hidden spend. **Token counts in telemetry are
accurate** (pulled straight from the OpenAI response `usage` via `_extract_usage`,
including reasoning tokens, which ride in `completion_tokens`/`output_tokens`).
The dollar gap is entirely the per-token **rate** config in
`services/openai_helper.py::PRICING` lagging OpenAI's published rates.

**Why:** `estimate_cost` is correct (billable_input = prompt − cached, cached and
output priced separately) and matches stored cost to the cent. So any bill-vs-
telemetry gap reduces to wrong rate constants.

**How to reconcile (definitive method):** pull our token sums for one model over
the SAME window as the OpenAI usage dashboard, then derive OpenAI's *actual* rate
per category = (dashboard category $) ÷ (our token sum in millions):
- billable-input rate = dash "input $" ÷ (sum(input_tokens) − sum(cached))
- cached rate         = dash "cached input $" ÷ sum(cached_input_tokens)
- output rate         = dash "output $" ÷ sum(output_tokens)
Compare to the PRICING tuple `(input, cached, output)` per 1M tokens.

**Finding as of 2026-06-02:** for `gpt-5.4` the implied actual rates were ~
(2.49 input, 0.249 cached, 14.93 output) i.e. effectively (2.50, 0.25, 15.00),
while PRICING had (1.25, 0.125, 10.00) — almost exactly 2x on input/cached and
1.5x on output, producing a ~1.59x whole-bill undercount. The cached:input 1:10
ratio held in both. Re-derive rates the same way before trusting any future
dollar figure; don't hardcode these numbers as permanent truth.

**Implication:** when quoting spend/run-rate to the user from telemetry, multiply
by the current bill/telemetry ratio (or fix PRICING) — the raw dashboard number
is a floor, not the bill.
