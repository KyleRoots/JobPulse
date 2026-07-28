# Scout Screening Rules Changelog

Each production screening result stores `screening_rules_version` so audits can
identify which rule set produced a score.

## 2026.07.28 — Rule 14 soft-skill relevance bar

- **Bug:** Candidates with stale domain experience (e.g. admin work ending
  2020) could still clear Qualified when their current unrelated role was
  marked `relevant=yes` on soft communication / generic customer-service
  overlap alone (regression: Debbie James 4672817 / Admin Assistant 35531 —
  School Crossing Guard counted as relevant → 82% Qualified, no recency gate).
- **Fix:** Rule 14 prompt now states soft CS/communication alone is not
  domain-relevant (even when the JD mentions customer service among other
  duties). Justification enforcer treats soft-only justifications as weak
  unless a concrete functional duty/tool is also cited
  (`relevance_justification_is_weak` in `screening/post_processing.py`).
- **Rules version:** `2026.07.28`

## 2026.07.15 — Note supersession on screening outcome change

- **Bug:** After auditor re-vets, recruiter emails reflected the latest
  Qualified/Not Qualified result, but Bullhorn notes stayed on the first
  outcome because the 6h duplicate note safeguard blocked writes
  (regression: candidate 4553046 — Not Qualified note + Qualified email).
- **Fix:** Same-outcome Scout notes within 6h still dedupe. Outcome flips
  (Qualified ↔ Not Qualified / Location Review / Incomplete → complete)
  now supersede with an **UPDATED SCOUT SCREENING RESULT** banner.
- **Models/prompts unchanged.**

## 2026.07.14 — Applied-job injection always scores the applied role

- **Bug:** Candidates who applied to "half-closed" Bullhorn jobs (`isOpen=False`
  while status remained `Accepting Candidates`) were never injected into the
  analysis set. Scout then only scored related open tearsheet jobs — notes
  showed **TOP ANALYSIS RESULTS** with no **APPLIED POSITION** (e.g. candidate
  4671202 / job 35421 screened only against job 35261).
- **Fix:** `_fetch_applied_job` always injects a fetchable JobOrder on the
  applied-job path (with a warning when normally ineligible). Tearsheet
  browsing still uses strict `is_job_eligible`.
- **Note safety net:** Not-recommended notes list **JOB ORIGINALLY APPLIED TO
  (NOT SCORED)** when `applied_job_id` is known but no applied match was stored.
- **Models unchanged:** Layer 2 / Enforce routing unchanged.

**Approved for:** Applied-job transparency fix  
**Rules version:** still `2026.07.10` (prompt/guardrails unchanged; injection behavior only)

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
