---
name: Verifying a deploy against the prod read replica
description: What does and does NOT replicate when you check production via executeSql(environment="production")
---

# Verifying a deploy against the production read replica

`executeSql({ environment: "production" })` runs against a **read replica**, not the primary.

**Trust these — they replicate and are authoritative for confirming a deploy landed:**
- Catalog facts: table/column existence (`information_schema.columns`), indexes (`pg_indexes`), and per-table `reloptions` on `pg_class` (including TOAST reloptions via `pg_class t JOIN pg_class c ON t.reltoastrelid=c.oid`).
- Actual row data (`count(*)`, SELECTs) — data is streamed from the primary.

**Do NOT trust these on the replica — they are replica-local and read zero/`never`:**
- `pg_stat_user_tables` counters: `last_analyze`, `last_autoanalyze`, `last_autovacuum`, `autovacuum_count`, `analyze_count`, `n_live_tup`, `n_dead_tup`, `n_mod_since_analyze`.

**Why:** these stat counters track activity on the node you're querying. The replica doesn't run autovacuum/analyze on user tables, so they show `never`/0 even when the primary has healthy stats. A `last_analyze = never` reading here is an artifact, NOT proof the primary's stats are stale — don't raise it as an alarm.

**How to apply:** to confirm autovacuum tuning took effect, verify the `reloptions` are present (they replicate). The primary's autovacuum will then use those thresholds; you can't observe its run from the replica.
