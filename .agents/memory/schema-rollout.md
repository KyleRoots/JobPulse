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

# Workspace shell DDL lands in DEV, and DEV schema auto-syncs to PROD on Publish

Any DDL run from the workspace shell (including by an external coding agent like
Claude Code that a user accidentally invokes) hits the **development** database
(`DATABASE_URL`), never prod directly — prod is a separate managed DB only reachable
read-only via `executeSql(environment:"production")`. So a tool that *thinks* it
edited "production" (e.g. because `APP_ENV=production`) actually only touched dev.

**Why this matters:** Replit's Publish flow diffs the **dev** schema against prod and
applies the diff to prod. So an accidental dev schema change (e.g. added FKs) will
silently propagate to prod on the next Publish unless reverted first. Before
keeping/reverting such a change, verify prod data won't reject it (e.g. orphan-row
check before adding a FK that would VALIDATE on prod).
**How to apply:** whenever you find unexpected schema in dev (stray FKs, columns,
alembic stamp moves) — confirm it's dev-only, check it matches the models, and decide
keep-vs-revert *with the user* because "keep" means it ships to prod at next publish.
