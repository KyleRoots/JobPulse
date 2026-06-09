---
name: Bullhorn candidate base size
description: The real Bullhorn ATS Candidate base is ~900k+ (≈984k via search/Candidate); an earlier "~9k" finding here was WRONG.
---

# Bullhorn candidate base is ~900k+ (NOT ~9k)

A live read-only count (2026-06-09) against production Bullhorn `search/Candidate` returned:
- `id:[1 TO *]` total: **~984,201**
- `isDeleted:0` total: ~984,143

This matches the Bullhorn UI Candidates screen badge (**Bullhorn (924,729)** + "of 924729" pager) — i.e. the *main* Bullhorn tab itself holds ~900k+ records, not a small ATS core.

**CORRECTION:** An earlier version of this note claimed the base was ~9k and that the ~1M was only the "Job Boards" external tab. That was wrong — the selected **Bullhorn** tab itself reads ~924k–984k. Do not trust the old ~9k figure for capacity/cost/feasibility math.

**How to verify the true total fast:** authenticate `BullhornService()` and GET `search/Candidate?query=id:[1 TO *]&fields=id&count=1`, read the `total` field. (Run inside `app.app_context()`; `bh.authenticate()` then use `bh.base_url` + `BhRestToken` header.)

**Feasibility implication (big):** any per-candidate sweep (e.g. resume-freshness-sync's base scan doing one fileAttachments GET per candidate) over the full base is ~900k+ API calls — days of runtime + Bullhorn rate limits. Such tools MUST be scoped (targeted candidate IDs, a dateLastModified/dateAdded window, or by querying recently-added FileAttachments) rather than sweeping everyone.
