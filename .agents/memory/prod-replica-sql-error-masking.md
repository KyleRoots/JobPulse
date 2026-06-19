---
name: Prod read-replica masks SQL errors
description: How executeSql(environment=production) surfaces a failed query, so a bad column isn't mistaken for a replica outage.
---

When `executeSql({environment:"production"})` runs a query that ERRORS on the
read replica (e.g. references a non-existent column), the output is just:

```
START TRANSACTION
ROLLBACK
```

with no error message. A query that SUCCEEDS but returns zero rows instead
prints a clean CSV header with no data rows.

**Why it matters:** "START TRANSACTION / ROLLBACK" looks like a transient
replica/connection hiccup, but it is almost always a *malformed query* —
most often a wrong/missing column name. Do NOT retry the same query hoping the
"replica recovers"; fix the SQL.

**How to apply:** if a prod query returns only the transaction markers, diff the
column names against the actual model before blaming infra. Concrete trap hit
once: `parsed_email` has `received_at` / `processed_at` / `created_at` but NO
`updated_at` column (the `updated_at`+`onupdate` column lives on a *different*
model in the same file) — every query referencing `parsed_email.updated_at`
returned the bare markers. Use `received_at`/`processed_at` for recency on
parsed_email.
