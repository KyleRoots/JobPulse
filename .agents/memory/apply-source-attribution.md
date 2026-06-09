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

**Non-source referrers** deliberately return '' (fall through to next signal):
our own domains (myticas/stsigroup/scoutgenius) AND **PandoLogic** — it's a
redirect middle-man that masks the true origin, so mapping it to a "source"
would be misleading.

**PandoLogic-distributed apply traffic (feed=pando) decision:** ~27% of
applicants reach our own apply form via PandoLogic distribution (e.g.
TheJobNetwork referrer) carrying the hardcoded `?source=LinkedIn`. The reliable
discriminator is `feed=pando` (NOT the referrer host or source string). When the
inbound mapper sees feed=pando it sets Bullhorn **source="Corporate Website"**
(they applied on our form) + **owner = PandoLogic API user** (id from
`VettingConfig.pandologic_api_user_id`), capturing the channel in the owner.
**Why:** genuine recognized referrers (LinkedIn/Indeed/Facebook) do NOT carry
feed=pando in the data, so keying off feed never mislabels real referrers; and
owner alone is mutable (auto-reassign hands it to recruiters), so source carries
the durable "applied on our site" fact. The source value is a single constant
(`_InboundCore.PANDO_FEED_SOURCE`) — flip to 'Vendor/3rd Party' if preferred.
**How to apply:** override lives in `map_to_bullhorn_fields`; enrichment/recovery
paths never stomp an existing candidate's owner/source (neither is in the
enrichment allowlist).

**Integrity:** at submit time, prefer the referrer/utm we persisted server-side
at the GET first-touch (looked up by `visit_token` in `apply_page_visit`) over
the round-tripped hidden form fields, which a client could tamper with.

**How to apply:** all attribution writes/reads are fail-soft — they must never
block or alter the public apply flow or the inbound pipeline. `apply_page_visit`
stores IP/UA/email/referrer (sensitive telemetry) — a retention/TTL policy is
still owed (deferred).
