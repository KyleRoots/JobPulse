---
name: Phase-2 external AI-detection pilot
description: Why external AI-resume detection was deferred from the fraud add-on and how to approach it later.
---

# Phase-2 external AI-detection pilot

External AI-content detection (e.g. flagging AI-written resumes) was deliberately
deferred out of the deterministic fraud add-on. In Phase 1 the only AI-style
signal shipped is the INFORMATIONAL-only em-dash marker (`evaluate_ai_style_markers`),
which is 0 points and never bands a candidate.

**Why:** AI-text detectors are unreliable on short, templated resume/bullet text —
high false-positive rate, easy to evade, and false accusations against real
candidates are reputationally costly. The deterministic engine's contract is
"$0 AI cost, advisory, never accuses without strong evidence", and a weak
probabilistic detector breaks that.

**How to apply:** If Phase 2 revisits this, run a PILOT first (shadow-only, no
scoring weight) and measure precision on real resume corpus before letting it
contribute points. GPTZero is the preferred candidate vendor to evaluate. Keep
it gated and fail-soft like the rest of the engine; never let it block/skip
screening.
