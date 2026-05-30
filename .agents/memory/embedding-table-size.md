---
name: candidate_profile_embedding size is live data, not bloat
description: The ~180MB size of candidate_profile_embedding is legitimate; the "tiny row count" trap is stale statistics.
---

# candidate_profile_embedding is big because of real data

Its ~180MB total is **legitimate live data**, not bloat: ~6.7k rows, each with a
`text-embedding-3-large` vector stored as TEXT (~25KB/row) living in the TOAST
table (the heap + indexes are only a few MB). `count(*)` confirms the row count.

**The trap:** `pg_stat_user_tables.n_live_tup` once showed ~150 rows while the real
count was ~6765 — because the table had **never been analyzed** (`last_analyze`
NULL, `autovacuum_count` 0). A 45x-wrong row estimate gives the planner bad input.
Don't conclude "bloat" from a small `n_live_tup` next to a large on-disk size —
run `ANALYZE` and `count(*)` first, and check `n_dead_tup` (it was ~0 here).

**Why it had never auto-analyzed:** default autovacuum thresholds + a low-traffic
table. Fixed by per-table reloptions (heap + `toast.*`) tuning vacuum/analyze
scale factors to 0.05, applied durably via `seeding/migrations.py` so it reaches
every environment on boot. `toast.*` reloptions live on the TOAST table's own
`pg_class.reloptions`, NOT the main table's — query the toast row to verify them.

**How to apply:** if asked to "shrink" this table, the real lever is the embedding
storage format (TEXT vs pgvector), not vacuuming. Plain VACUUM won't return TOAST
space to the OS (only VACUUM FULL does), and here there's nothing dead to reclaim.
