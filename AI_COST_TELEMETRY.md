# AI Cost Telemetry — Operational Runbook

**Audience:** Scout Genius super-admins / on-call engineers
**Introduced:** Task #87 (May 2026)

This runbook covers the per-call OpenAI telemetry layer that powers the
`/admin/ai-cost` dashboard, the `tile_ai_cost_24h` System Health tile, and
future per-customer cost forecasting.

---

## Architecture (one-paragraph mental model)

Every OpenAI invocation in the platform routes through two helpers from
`services/openai_helper.py`:

1. `resolve_model(site_id, default_model)` — returns the model string the
   call site should use, applying any environment-variable override.
2. `log_call(site_id, model, response, ...)` — fire-and-forget background
   insert into the `openai_call_log` table. Token usage is read from
   `response.usage`; USD cost is estimated from the central `PRICING` dict.
   The function NEVER raises — wrapping the OpenAI call site cannot
   introduce new failure modes.

Records flow into Postgres on a daemon thread inside a short-lived app
context. The `/admin/ai-cost` dashboard and `tile_ai_cost_24h` tile read
from the same table.

---

## One-site model rollback (no deploy required)

To force any single call site back to a flagship model in production:

1. Identify the `call_site_id`. They follow the pattern
   `<module>.<purpose>` — e.g. `job_classification`,
   `email_inbound.resume_parse`, `screening.years_recheck`. The full list
   is visible on the dashboard.
2. Set the override environment variable:
   ```
   MODEL_TIER_OVERRIDE_<SITE_ID> = gpt-5.4
   ```
   The site name is uppercased and dots/dashes are converted to
   underscores. Example mappings:

   | Call site                       | Env var name                                    |
   |---------------------------------|-------------------------------------------------|
   | `job_classification`            | `MODEL_TIER_OVERRIDE_JOB_CLASSIFICATION`        |
   | `email_inbound.resume_parse`    | `MODEL_TIER_OVERRIDE_EMAIL_INBOUND_RESUME_PARSE`|
   | `screening.years_recheck`       | `MODEL_TIER_OVERRIDE_SCREENING_YEARS_RECHECK`   |
   | `scout_support.platform_reply`  | `MODEL_TIER_OVERRIDE_SCOUT_SUPPORT_PLATFORM_REPLY` |

3. The override applies to the **next call**. No redeploy, no restart
   required (the env var is read on every `resolve_model()` call).
4. Remove the override to revert to the code-default model.

---

## Dashboard interpretation

`/admin/ai-cost` (super-admin only)

- **Window selector** — 1h / 24h / 7d / 30d. Drives the per-site
  breakdown table and the summary cards.
- **Summary cards** — total spend, total API calls, total tokens, average
  cost per call over the selected window.
- **7-day daily trend** — always shown. Columns: day, calls, tokens,
  failures, daily cost. Use this to spot day-over-day spikes regardless
  of the active window.
- **Per-site breakdown** — sorted by spend, descending. Use the model
  color (green = mini/nano, amber = flagship) to spot any site running
  on the wrong tier.

`tile_ai_cost_24h` (System Health)

- **Green** when 24-hour spend is under $80.
- **Amber** when 24-hour spend is between $80 and $200.
- **Red** when 24-hour spend is at or above $200.

---

## Adding a new OpenAI call site

When you introduce a new OpenAI invocation anywhere in the codebase, you
MUST instrument it with both helpers. The drift detector test in
`tests/test_openai_helper_drift.py` will fail CI if you forget.

```python
from services.openai_helper import resolve_model, log_call

def some_new_feature(prompt: str):
    model = resolve_model('my_module.my_purpose', 'gpt-4.1-mini')
    response = openai_client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': prompt}],
    )
    log_call('my_module.my_purpose', model, response,
             entity_type='Candidate', entity_id=candidate_id)
    return response.choices[0].message.content
```

To also log a failed attempt for full observability, wrap the call:

```python
try:
    response = openai_client.chat.completions.create(model=model, ...)
    log_call('my_module.my_purpose', model, response)
except Exception as e:
    log_call('my_module.my_purpose', model, response=None,
             success=False, error_type=type(e).__name__)
    raise
```

---

## Pricing table maintenance

`services/openai_helper.PRICING` maps model name → `(input_rate,
cached_rate, output_rate)` in USD per **1,000,000** tokens. When OpenAI
publishes price changes, update the relevant entry. Models not present in
the table fall back to a conservative default so we never silently
under-report cost.

---

## Schema reference

Table: `openai_call_log` (Alembic revision `n8h9i0j1k2l3`)

| Column                | Type           | Nullable | Notes                                              |
|-----------------------|----------------|----------|----------------------------------------------------|
| `id`                  | BIGSERIAL PK   | no       | Auto-increment                                     |
| `created_at`          | TIMESTAMP      | no       | Indexed                                            |
| `call_site_id`        | VARCHAR(80)    | no       | Indexed; logical site identifier                   |
| `model`               | VARCHAR(80)    | no       | Resolved model after override                      |
| `input_tokens`        | INTEGER        | no       | From `response.usage`                              |
| `output_tokens`       | INTEGER        | no       | From `response.usage`                              |
| `cached_input_tokens` | INTEGER        | no       | From `response.usage.prompt_tokens_details`        |
| `estimated_cost_usd`  | NUMERIC(12,6)  | no       | Computed from `PRICING` dict                       |
| `duration_ms`         | INTEGER        | yes      | Optional latency                                   |
| `tenant_id`           | VARCHAR(80)    | yes      | Indexed; future multi-tenant attribution           |
| `customer_id`         | VARCHAR(80)    | yes      | Indexed; future per-customer pricing dashboard     |
| `entity_type`         | VARCHAR(40)    | yes      | e.g. `Candidate`, `Job`, `SupportTicket`           |
| `entity_id`           | VARCHAR(80)    | yes      | Source-system id of the linked entity              |
| `success`             | BOOLEAN        | no       | `False` for explicitly logged failed attempts      |
| `error_type`          | VARCHAR(120)   | yes      | Exception class name when `success=False`          |

Composite indexes:
- `(call_site_id, created_at)` — per-site time-window rollups
- `(tenant_id, created_at)` — per-tenant rollups
- `(customer_id, created_at)` — per-customer rollups

---

## Known limitations

- **Best-effort durability:** logs are written on a daemon thread; rows
  may be dropped on hard worker shutdown. Acceptable for a cost-tracking
  signal, NOT a billing-ledger.
- **Failure-path coverage is opt-in:** call sites that don't wrap their
  OpenAI invocation in `try/except + log_call(success=False)` will not
  log failed attempts. The dashboard's Failures column reflects only
  explicitly-logged failures.
- **Auto-pruning not yet enabled:** see Task #90 — `openai_call_log`
  will grow unbounded until a retention job is added.
