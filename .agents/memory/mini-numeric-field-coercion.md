---
name: Mini returns prose/null for numeric AI fields
description: gpt-4.1-mini sometimes emits prose/null where gpt-5.4 always gave numbers; coerce defensively and skip-gate on unparseable, never coerce-to-0.
---

# Mini emits prose/null for fields gpt-5.4 always returned numeric

When `gpt-4.1-mini` is the scorer (cheap-first router first-pass), it sometimes
returns years-of-experience fields — `required_years`, `estimated_years`,
`total_professional_years` — as **prose** (e.g. `"Not explicitly stated but
strong hands-on required"`) or **null**, where `gpt-5.4` reliably returned a
number. Any `float()` / `>` math written against the old (gpt-5.4-only) output
will crash on mini.

**Symptom:** `screening.prompt_builder - ERROR - AI analysis error for job N:
'>' not supported between instances of 'NoneType' and 'int'` /
`could not convert string to float: '...'`. The outer handler swallows it and
returns `match_score=0` ("Analysis failed"). These appeared the moment the
canary went live and were absent before.

**This is whack-a-mole — it recurred after the first fix.** The first pass only
covered the years gate (`_compute_shortfalls` / `enforce_experience_floor`). The
SAME `'>' NoneType and int` error kept firing in prod from TWO other sites that
also read mini numeric fields: (1) `enforce_recency_hard_gate` —
`months_since_relevant_work` + `penalty_applied`; (2) `coerce_scores` —
`match_score` / `technical_score` (`int(None)`/`int("prose")`). Beyond the years
fields, mini also nulls/proses nested recency/gap fields and the top-level
scores. Hardening `coerce_scores` is the linchpin: once `match_score` is always
a real int, every downstream `result['match_score'] > N` comparison AND the
router reads (`mini_score`/`gpt4o_score`/`reverify_score` in
`candidate_vetting_service/processing.py`) are protected for free.

**The actual trap (durable):** `dict.get(key, default)` returns `None` when the
key is PRESENT but explicitly `null` in the JSON — the default ONLY applies to a
MISSING key. So `recency.get('months_since_relevant_work', 0)` returns `None`
(not 0) when mini emits `"months_since_relevant_work": null`. Never trust a
`.get(k, 0)` default to protect against null; coerce the result.

**Separate, NOT a coercion bug:** mini also sometimes returns truncated/malformed
JSON (`AI analysis error: Unterminated string ...`). That's an output-quality
issue, not None>int. It's caught by the outer handler → escalates to gpt-5.4 via
the canary safety net, so no candidate is lost; left unfixed on purpose.

**Rule:** Treat ANY AI-provided numeric field as untrusted when a non-gpt-5.4
model can produce it. Coerce through a tolerant helper (`_safe_float` in
`screening/post_processing.py`) before arithmetic/comparison.

**Why the unparseable→skip (not →0) decision matters:** If an `estimated_years`
is unparseable, do NOT coerce it to 0 — that fabricates a large shortfall, which
would wrongly penalize and could become a **false-negative under Enforce mode**
(the candidate gets auto-rejected before gpt-5.4 ever sees them). Instead skip
that skill's years gate. A genuine numeric `0` is still a real shortfall, so use
`default=None` to distinguish "unparseable" from "zero".

**How to apply:** Any new gate/penalty that reads AI years/score fields must go
through the tolerant coercion and adopt the same "unknown → don't penalize"
stance, consistent with the documented "mini is generous, never false-rejects"
contract.

**Blind spot:** The canary GO criterion "0 `🚨 CANARY false-negative` lines"
does NOT cover this — analysis crashes are a separate error class. When
validating a mini cutover, also watch the `AI analysis error` count.
