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

**Root cause of the RAPID same-minute duplicates (confirmed via prod parsed_email, 2026-06-10, job 35214):** the SAME machine-generated apply email (identical `sender_email`=info@myticas.com, `recipient_email`=Apply@myticas.com, and byte-identical subject "...has applied on...") lands in the Apply@ inbox as TWO messages with TWO DIFFERENT `message_id`s — one stamped by Microsoft/Exchange (`<...@YT1PR01MB...OUTLOOK.COM>`, also keeps the `Myticas Info <info@…>` display name) and one stamped by SendGrid (`<...@geopod-ismtpd-N>`, bare `info@…`). Because dedupe keys on `message_id` (UNIQUE), the two copies never match → both processed → two `bullhorn_submission_id`s. The 2nd row is even correctly flagged `is_duplicate_candidate=true` (candidate-level dedupe recognized the person) but the submission is still created (per the standing "repeat submissions are intentional" decision). Gap is ~20–60s for the rapid cases (the Graph mailbox-pull 60s cycle + Exchange lag), NOT human double-click. Singles on the same job are OUTLOOK-only → the Outlook-routed copy is the always-present one; the SendGrid (geopod) copy is the EXTRA. **This is intake-side duplication, NOT applicants clicking Submit twice.** **Why message_id dedupe misses it:** same logical email, two transport encodings/IDs. **How to apply a fix (if user wants):** (a) root cause — stop the dual outbound route so one application = one inbox message (needs header/send-path confirm on Apply@), or (b) add a WINDOW-BOUNDED content dedupe (normalized subject + candidate_email + job_id within ~a few min) so only rapid twins collapse while genuine re-applies (hours/days apart, e.g. Brenna/Tameka/Tilly 6–30h gaps) are preserved.

**Side note:** "Unknown Unknown" rows can be an OLD nameless record (e.g. cand 3838957 from 2020) that a new applicant correctly matched to by PHONE — that's dedup working, not failing; the only issue is the legacy record never had a name.
