---
name: Per-brand screening profiles
description: How per-environment screening config + profiles stay safe for the default (Myticas) brand.
---

Per-brand screening (Task #101) layers a per-`BullhornEnvironment` override map
and a `screening_profile` on top of the global screening config.

**The Myticas byte-for-byte invariant rests on three things — keep all three:**
1. Default environment carries no overrides (`{}`) and no profile (`'standard'`),
   so `get_config_value` and the prompt/floor behave exactly as the old
   global-only path. `_resolve_environment`/profile/override helpers are all
   lazy + cached + fail-soft (any error → None/`{}`/`'standard'`).
2. The STANDARD system-prompt literal is NEVER edited. Non-standard profiles are
   applied as targeted post-render string swaps (`_apply_screening_profile`).
   If a swap marker isn't found, it warns and leaves standard text (fail-safe).
   **Why:** mutating the f-string risks silent drift on the default brand.
   **How to apply:** when adding a new profile, add new marker constants that
   match the RENDERED text (the prompt f-string's `{{N}}` renders to single
   `{N}`), and swap — don't branch inside the literal.
3. `screening_profile` must be resolved ONCE on the main thread in
   `processing.py` and passed into BOTH the mini and escalated scoring calls.
   **Why:** worker threads run without Flask app context, so a DB/env lookup
   there fails. Mirror the `prefetched_*` pattern.

`get_config_value` override precedence is global to the vetting service (not
just screening keys), so an env override map can retune thresholds/routing/model
per brand too — the default env's empty map keeps that inert.

light_industrial relaxes Rule 13b gate (b) from "10+yr senior" → any level
(+sparse-resume framing) and raises the FRESH_GRAD/ENTRY cap 55→70 in BOTH the
prompt instruction line and `enforce_experience_floor` (keep them in lockstep,
or the model self-caps before post-processing matters).
