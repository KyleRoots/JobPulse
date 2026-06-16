---
name: multi-tenant query scoping
description: How to scope DB reads/writes by environment without breaking the single-tenant byte-for-byte invariant.
---

# Multi-tenant query scoping

Scout Genius is multi-tenant by `environment_id` (a Bullhorn instance = isolation
boundary), but the live deployment runs a SINGLE environment (Myticas, with
Myticas + STSI brands). The hard invariant: with one active env, ALL behavior is
byte-for-byte unchanged.

**Rule:** any query that filters on `bullhorn_job_id` / `bullhorn_candidate_id`
(NOT globally unique across tenants) must be environment-scoped, for BOTH reads
AND writes (save/reset paths leak cross-tenant edits otherwise).

**How to apply:**
- Request-context paths (routes): wrap reads in the shared scope-query helper. It
  must no-op when active env count <= 1, the user is super-admin (roll-up), or the
  model has no `environment_id` column.
- Background/service paths (no request context): scope by the service's OWN
  resolved env, and ONLY when active env count > 1. Don't rely on request-context
  resolvers in scheduler/service code.

**Why the >1-env gate matters:** legacy rows can carry NULL `environment_id`; a
hard env filter at single-env would drop them and break parity. The count gate
keeps single-tenant lookups unscoped.

**Non-default env Bullhorn creds must fail CLOSED:** if a non-default env lacks a
complete inline credential set, return None — never fall back to the global
GlobalSettings creds (that would corrupt one tenant with another's Bullhorn).
Default/global path is untouched.

**Inbound routing determinism:** `Brand.resolve_environment_id_for_recipient()`
is "first active brand matching `to_email` wins". Provisioning
(`utils/tenant_provisioning.py`) rejects a duplicate active `to_email` so the
match is unambiguous. Keep that guard.
