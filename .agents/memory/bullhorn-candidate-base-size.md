---
name: Bullhorn candidate base size & "1M" scare
description: The real Bullhorn ATS candidate count is ~9k, not ~1M; how to verify and why the Job Boards tab confuses this.
---

# Bullhorn candidate base is ~9k, not "~1 million"

A live read-only count (2026-06-04) against the production Bullhorn returned:
- All candidates (incl archived+deleted): ~8,934
- Live (excl archive & deleted): ~8,669
- Archived (status=Archive): ~260
- Deleted (isDeleted:1): ~9

**The real candidate base is ~9k.** A user once feared a drop "from ~1M to 8,666" — that was a misread, not data loss.

**Why "~1M" appears:** the Bullhorn Candidates screen has a toggle **"Bullhorn (N) | Job Boards"**. The *Bullhorn* tab = records actually in the ATS (~9k). The *Job Boards* tab = external candidates searchable across LinkedIn/job boards (can be hundreds of thousands+). People conflate the two.

**Why this rules out our tool as a deleter/archiver:**
- Only ~9 deleted records exist in ALL of Bullhorn — a mass purge would be in the hundreds of thousands.
- Only ~260 archived total; ~187 of those are our all-time duplicate-merges (`candidate_merge_log` rows). Merge ARCHIVES (status=Archive) one record at a time, never deletes.
- The only bulk archive/delete capability is in the **Scout Support** module: two-tier human approval, acts on explicit entity-ID lists, not wired to any scheduler — cannot fire autonomously.

**How to verify the true total fast:** authenticate `BullhornService()` and GET `search/Candidate?query=id:[0 TO *]&fields=id&count=1`, read the `total` field. Vary the query for live (`AND -isDeleted:1 AND -status:Archive`), archived (`status:Archive`), deleted (`isDeleted:1`).

**Decision relevance:** capacity/cost models should assume a ~9k active candidate base, not 1M.
