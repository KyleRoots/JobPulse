# DB Reconciliation — 2026-06-22

**TL;DR — Problem → Fix → Benefit**
- **Problem:** The Alembic version table lagged 7 revisions behind the code (stamp `u5p6q7r8s9t0`), while `db.create_all()` had already built almost all of the head schema out-of-band. `alembic upgrade head` would have *failed* (e.g. `ADD COLUMN candidate_phone` already exists), and the only genuine gap was 9 missing `environment_id` foreign keys.
- **Fix:** Added the 9 missing FKs production-safely, then `alembic stamp head` to mark the DB at `b4c5d6e7f8a9`.
- **Benefit:** Production schema now fully matches the intended migration state, and Alembic is once again an accurate record of where the DB is.

## What was done (in order)
1. **Rollback/reference record captured** (read-only): see files below.
2. **Read-only FK data validation:** 0 orphan rows across all 9 tables (`environment_id` is either NULL or `= bullhorn_environment.id = 1`). Safe to add FKs.
3. **Added 9 `environment_id` FKs** using `ADD CONSTRAINT … NOT VALID` then `VALIDATE CONSTRAINT` (avoids a long `ACCESS EXCLUSIVE` lock on large tables such as `candidate_profile_embedding`, 13,649 rows). Final constraints are identical in name/definition to what migration `x8t9u0v1w2x3` would have produced. All 9 verified `convalidated = true`.
4. **`alembic stamp head`** — moved version `u5p6q7r8s9t0` → `b4c5d6e7f8a9 (head)`. No DDL run by Alembic itself.

## Files in this folder
| File | Purpose |
|---|---|
| `alembic_version.before.txt` | Stamp before reconciliation (`u5p6q7r8s9t0`) |
| `alembic_version.after.txt` | Stamp after (`b4c5d6e7f8a9`) |
| `pg_dump_schema.before.sql` | Authoritative `pg_dump --schema-only` (v16) of all affected tables, pre-change |
| `affected_tables_schema.before.sql` | Human-readable catalog reconstruction of the same, pre-change |
| `existing_fks.before.txt` | FKs present on the 9 env tables before (only the unrelated `candidate_job_match_vetting_log_id_fkey`) |
| `added_fks.after.txt` | The 9 `fk_<table>_environment_id` FKs added |

## The 9 FKs added
Each: `FOREIGN KEY (environment_id) REFERENCES bullhorn_environment(id)`, named `fk_<table>_environment_id`, on:
`candidate_vetting_log`, `candidate_job_match`, `job_vetting_requirements`, `parsed_email`,
`bullhorn_monitor`, `candidate_fraud_assessment`, `job_embedding`, `candidate_profile_embedding`,
`recruiter_notification_ledger`.

## Rollback procedure (if ever needed)
```sql
-- 1. Re-set the Alembic stamp to its prior value
UPDATE alembic_version SET version_num = 'u5p6q7r8s9t0';

-- 2. Drop the 9 added FKs (names are stable; see added_fks.after.txt)
ALTER TABLE candidate_vetting_log         DROP CONSTRAINT fk_candidate_vetting_log_environment_id;
ALTER TABLE candidate_job_match           DROP CONSTRAINT fk_candidate_job_match_environment_id;
ALTER TABLE job_vetting_requirements      DROP CONSTRAINT fk_job_vetting_requirements_environment_id;
ALTER TABLE parsed_email                  DROP CONSTRAINT fk_parsed_email_environment_id;
ALTER TABLE bullhorn_monitor              DROP CONSTRAINT fk_bullhorn_monitor_environment_id;
ALTER TABLE candidate_fraud_assessment    DROP CONSTRAINT fk_candidate_fraud_assessment_environment_id;
ALTER TABLE job_embedding                 DROP CONSTRAINT fk_job_embedding_environment_id;
ALTER TABLE candidate_profile_embedding   DROP CONSTRAINT fk_candidate_profile_embedding_environment_id;
ALTER TABLE recruiter_notification_ledger DROP CONSTRAINT fk_recruiter_notification_ledger_environment_id;
```
Note: the stamp change is the only thing that affects Alembic's view; the FKs are pure additions
that match the model intent, so dropping them is only needed for a full revert.
