---
name: Apply-page dynamic source attribution
description: How the apply page detects the true application channel on our side instead of trusting the vendor param.
---

# Apply-page source is detected on OUR side, not from the vendor param

**The trap:** every published apply URL hardcodes `?source=LinkedIn`
(`xml_integration_service/mapping.py` `_generate_job_application_url`). Only
`&feed=pando` varies (and that drives Bullhorn *ownership* routing, not source).
So the `?source=` param is NOT a per-vendor signal — it's a static default. That
is why ~all apply traffic historically attributed to "LinkedIn Job Board".

**The real signal** is the browser **referrer** captured at the apply-page GET
(`request.referrer` — only available on the GET, not the POST). It tells us the
site the candidate actually clicked Apply from (indeed.com, dice.com, etc.).

**Resolution order** (`source_attribution.resolve_source`): referrer host >
utm_source > explicit `?source=` param > '' (caller keeps its own fallback).
Referrer must WIN over the param precisely because the param is the hardcoded
LinkedIn default. Outputs are canonical Bullhorn picklist values that match
`email_inbound_service/_core.py SOURCE_TO_BULLHORN`.

**Why it flows to Bullhorn for free:** the apply form does NOT create the
Bullhorn record — it EMAILS the application to Apply@myticas.com with subject
"... has applied on {source}". The inbound parser stamps the Bullhorn submission
from that subject. So resolving the real source at submit time and writing it
into that subject means the existing inbound→Bullhorn pipeline carries it
untouched — no Bullhorn-write code changes needed.

**Non-source referrers** that are OUR OWN domains
(myticas/stsigroup/scoutgenius) return '' from `source_from_referrer` (fall
through to next signal). PandoLogic hosts are handled by their OWN branch — see
below.

**PandoLogic-distributed apply traffic decision (two detection paths):** a large
slice of applicants reach our own apply form via PandoLogic distribution carrying
the hardcoded `?source=LinkedIn`. Outcome for ALL of them: Bullhorn
**source="Corporate Website"** (they applied on our form) + **owner = PandoLogic
API user** (id from `VettingConfig.pandologic_api_user_id`) — the distribution
channel is captured in the owner, not the source.
- **Path A — `feed=pando` tag:** the original discriminator. Inbound mapper sees
  `feed=pando` → applies the override in `map_to_bullhorn_fields`. Constant is
  `_InboundCore.PANDO_FEED_SOURCE` (flip to 'Vendor/3rd Party' if preferred).
- **Path B — referrer host (added because Path A never fires in prod):**
  production spot-check (Jun 2026) found `feed=pando` on **0 of ~5,000** visits —
  PandoLogic does NOT preserve our `?feed=pando` query param through its redirect
  network, and the true board (Indeed/Zip/Dice) is masked behind
  `thejobnetwork.com` (TheJobNetwork = PandoLogic's programmatic network, ~28% of
  apply traffic, all previously mislabeled "LinkedIn"). Fix: detect PandoLogic by
  **referrer host** (`thejobnetwork.com`/`pandologic`/`pandolytics`) in
  `source_attribution.is_pando_referrer`. `resolve_source` returns
  `PANDO_SOURCE='Corporate Website'` at TOP priority for these, and
  `job_application_service.submit_application` sets `feed='pando'` so Path A's
  downstream owner-routing fires. Keep `PANDO_SOURCE` in sync with
  `_InboundCore.PANDO_FEED_SOURCE`.
**Why referrer (not the param):** every apply URL hardcodes `?source=LinkedIn`,
and `feed=pando` doesn't survive the PandoLogic click — the referrer host is the
only reliable PandoLogic signal on our side. Genuine LinkedIn/Indeed referrers
never have a pando host, so this never mislabels real referrers.
**How to apply:** override lives in `map_to_bullhorn_fields`; enrichment/recovery
paths never stomp an existing candidate's owner/source (neither is in the
enrichment allowlist). Only affects NEW applications — no retro-relabel of the
~900 already mislabeled.

**Integrity:** at submit time, prefer the referrer/utm we persisted server-side
at the GET first-touch (looked up by `visit_token` in `apply_page_visit`) over
the round-tripped hidden form fields, which a client could tamper with.

**How to apply:** all attribution writes/reads are fail-soft — they must never
block or alter the public apply flow or the inbound pipeline. `apply_page_visit`
stores IP/UA/email/referrer (sensitive telemetry) — a retention/TTL policy is
still owed (deferred).
