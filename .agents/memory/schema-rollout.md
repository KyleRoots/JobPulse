---
name: Schema rollout convention (add a column/index)
description: How DB schema changes actually reach dev + prod in this Flask app — Alembic is NOT the live boot path.
---

# Adding a column or index

The live boot-time schema mechanism is **NOT** `alembic upgrade head`. At startup
`app.py` runs `db.create_all()` (creates *new tables* only — never adds columns or
indexes to existing tables) and `seed_database()` runs `seeding/migrations.py
run_schema_migrations(db)`, which applies **idempotent ALTERs**: a list of
`(table, column, col_type)` tuples (each checked against `information_schema` first),
plus hand-written `try/except` blocks for special cases (reserved words like `"user"`,
`CREATE INDEX IF NOT EXISTS`).

**Convention when adding a column/index — do all three:**
1. Add it to the model (`models/*.py`) so `create_all` covers fresh DBs.
2. Add it to `seeding/migrations.py` (tuple list for the column; explicit
   `CREATE INDEX IF NOT EXISTS` block for any index) — this is what actually patches
   existing dev/prod DBs at boot.
3. Add an Alembic migration chained off the current head for parity/record.

**Why:** `create_all` won't alter existing tables, and the app doesn't auto-run
`alembic upgrade`, so skipping step 2 means the column silently never appears in any
already-existing DB. Index names in step 2 must match what SQLAlchemy auto-generates
(`ix_<table>_<column>`) so the create_all path and the ALTER path converge on one index.

**How to apply:** any new column/index on an existing table. `col_type` in the tuple
list must match regex `^[A-Z()0-9 ]+$` (e.g. `VARCHAR(32)`).
