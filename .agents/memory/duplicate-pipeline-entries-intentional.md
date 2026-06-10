---
name: Duplicate pipeline entries are intentional (not a dedup bug)
description: Same candidate appearing multiple times in a job's Pipeline is acceptable by design; only duplicate CANDIDATE records are a problem.
---

# Duplicate JobSubmissions (pipeline rows) are intentional — do NOT add a (candidate, job) guard

When a job's Pipeline shows the same person several times (e.g. job 35155: "Jonathan Pineda" ×3, "Unknown Unknown" ×3), check the **candidate ID**, not the name. In the investigated case every repeated name mapped to a SINGLE candidate record (Jonathan ×3 = cand 4664764; Unknown ×3 = cand 3838957; etc.). So these are duplicate **JobSubmissions**, not duplicate candidates.

**Decision (user, 2026-06-10):** duplicate pipeline entries are ACCEPTABLE and wanted — they show how many times a person applied to a position. Only duplicate **candidate records** are a problem. Do not build a submission-level dedup/guard unless the user reverses this.

**Why:** candidate dedup is the real integrity concern; repeat-apply count is useful recruiter signal.

**How to apply:** if asked to "stop duplicates in the pipeline," first confirm whether they mean duplicate candidate RECORDS (act) or duplicate pipeline ROWS for the same candidate (leave alone per this decision).

**Mechanics (for context, not a defect):** `create_job_submission` (bullhorn_service/candidates.py) creates a submission unconditionally — no (candidate, job) existing-submission check; the inbound apply pipeline calls it whenever it has candidate_id + job_id. `get_candidate_submissions` exists but is only used by the merge tool, not as a pre-create guard. The "returning applicant" flag only changes a log line, it doesn't block. Repeat rows come from the same application being delivered/processed more than once (LinkedIn/PandoLogic re-sends, multiple webhook deliveries, or re-applies).

**Side note:** "Unknown Unknown" rows can be an OLD nameless record (e.g. cand 3838957 from 2020) that a new applicant correctly matched to by PHONE — that's dedup working, not failing; the only issue is the legacy record never had a name.
