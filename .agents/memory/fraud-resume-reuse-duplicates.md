---
name: Fraud resume-reuse vs same-person duplicates
description: How the résumé-reuse fraud signal must treat a same-person duplicate Bullhorn record (suppress, informational only) vs a genuinely different identity (score).
---

# Résumé-reuse signal: duplicates are not fraud

An identical résumé shared across two `candidate_vetting_log` records is only a
fraud indicator when it belongs to a **different person**. A duplicate Bullhorn
candidate record for the **same person** (same normalized name AND same lowercased
email) is benign — it must NOT score points, only surface a 0-point informational
"consider merging" line (the user explicitly wants that line shown).

**Why:** a real candidate (Edmond Vartanian) was flagged Review (40/100) purely
because `POINTS_RESUME_REUSE` (40) equals the Review threshold and the "other
identity" was just his own duplicate record. One benign duplicate should never
reach a risk band.

**How to apply:**
- Classify an other record as a duplicate ONLY when BOTH name and email match the
  current candidate (conservative — when in doubt, treat as genuine and score).
- Genuine reuse → scored signal `resume_reuse`; duplicate → informational
  `resume_duplicate_record` (points=0). Evidence must carry granular proof
  (other name, email, application date), not a bare count.
- When pulling the other record's name/email/date in Postgres, select ONE
  coherent row per `bullhorn_candidate_id` (`DISTINCT ON (...) ORDER BY ...,
  created_at DESC`). A `GROUP BY` with independent `MAX(name)/MAX(email)/MAX(date)`
  can mix fields from different rows and misclassify a duplicate as genuine.
- The advisory contract is unchanged: 0-point signals still display on all bands;
  fraud DB reads stay in isolated sessions; never blocks screening.
