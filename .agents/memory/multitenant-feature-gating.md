---
name: Multi-tenant feature gating for tenant-specific writes
description: How to add per-environment feature toggles whose action writes tenant-specific Bullhorn custom fields, without breaking Myticas or clobbering a new tenant.
---

# Multi-tenant feature gating (writes to tenant-specific custom fields)

When a feature WRITES to a Bullhorn custom field whose meaning differs per tenant
(e.g. the Sales Rep sync writing customText3→customText6), gate it per
environment with a NULL tri-state flag on `BullhornEnvironment`:

- `*_enabled IS NULL` → enabled ONLY for the default env (`is_default`), disabled
  for new tenants. Explicit True/False overrides.
- Field-name columns return `None` when unset so the **service constants stay the
  single source of truth** for the default mapping.

**Why:** customTextN slots mean different things in different Bullhorn instances.
Running a sync with Myticas's field names against a new tenant would silently
overwrite the wrong field. Off-by-default is a deliberate data-safety gate, not
just a config nicety — enable a new tenant only AFTER its field names are set.

**How to apply:** put the enable check + per-env field resolution in the
per-environment scheduler loop (`for_each_active_environment`), not in the
service. Keep the service backward-compatible via optional kwargs that fall back
to the module defaults, so the manual Automation-Hub trigger (no env context) and
the legacy `env=None` path keep historical behavior. Mirrors the
default-env→historical-behavior invariant in `per-brand-screening-profiles.md`,
and the model+seeding-ALTER+Alembic parity rule in `schema-rollout.md`.
