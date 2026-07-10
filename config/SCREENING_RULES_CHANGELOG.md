# Scout Screening Rules Changelog

Each production screening result stores `screening_rules_version` so audits can
identify which rule set produced a score.

## 2026.07.10 — Phase A compliance hardening

- Added apply-page AI screening notice (Myticas + STSI apply forms).
- Added recruiter advisory policy banner on Screening settings.
- Added compliance metadata stamping on every completed vetting log:
  - `screening_rules_version`
  - `screening_model_used`
  - `screening_prompt_profile`
  - `screening_rules_json`
- Added mandatory compliance guardrails to global screening prompt:
  - No protected-characteristic inference
  - Proxy rules are job-requirement gap analysis only (never sole disqualifier)
  - Recruiters retain final decision authority
- Added weekly compliance metrics endpoint (`/screening/compliance-metrics`).
- Documented recommended resume retention: 24 months (cleanup enforcement TBD).

**Approved for:** Phase A pre-reactivation hardening  
**Models unchanged:** Layer 2 default remains `gpt-5.4` (quality preserved)

## How to bump the version

1. Edit prompts, thresholds, or post-processing guardrails.
2. Increment `SCREENING_RULES_VERSION` in `screening/compliance.py`.
3. Add an entry to this file (what changed, why, who approved).
4. Deploy before re-enabling screening in production.
