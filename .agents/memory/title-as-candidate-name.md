---
name: Title-as-candidate-name failure mode
description: Apply-form / LinkedIn can submit occupation as first/last; inbound must prefer résumé AI name.
---

# Job title leaked into candidate name fields

**Incident (2026-08-06):** Bullhorn #4673968. Apply form (`apply.myticas.com`, source LinkedIn) submitted `firstName=Senior`, `lastName=Business Analyst`. Subject became `… - Senior Business Analyst has applied on LinkedIn Job Board`. AI résumé parse correctly returned `Uday Vasireddy` + `current_title=Senior Business Analyst`, but inbound preferred email/subject name → Bullhorn created as "Senior Business Analyst".

**Root cause (our bug, with bad form input):** email name preference over résumé AI, plus `is_valid_name` accepting title tokens. Resume and filename both had the real name.

**Fix:** `is_job_title_phrase` + Title-Reject Guard (prefer résumé name; filename strips trailing title tokens; overwrite invalid names on recovery). Do not re-prefer email subject when it equals `current_title`.

**Do not:** treat this as "corrupt resume" — résumé text and AI parse were fine.
