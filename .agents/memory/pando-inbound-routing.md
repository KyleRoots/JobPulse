---
name: PandoLogic inbound feed routing
description: Why feed=pando owner/source routing silently never fired on the inbound side, and the two structural traps behind it.
---

# PandoLogic inbound routing — two silent traps

The apply form correctly tags `feed=pando`, but inbound owner/source routing
produced **zero** `"PandoLogic feed detected"` logs in prod for a long window.
Two independent structural bugs, both silent (no errors):

1. **Inbound parses the HTML body, not plain text.** Feed detection keyed off a
   `Feed:\s*value` regex, but the apply email is HTML where the `Feed:` label and
   its value live in **separate `<td>` cells**, so the regex never matched.
   **Rule:** any regex that scrapes a label→value pair out of the inbound email
   must tolerate HTML (strip tags + retry), because processing uses the HTML body
   when present.

2. **Returning candidates skip source/owner entirely.** Dup/recovery re-applies
   route through `_build_enrichment_update`, whose `enrichable_fields` list
   **intentionally excludes `source` and `owner`** (enrichment only fills blanks,
   never overwrites). So even with feed detected, returning applicants kept stale
   source/owner. Pando attribution needs an **explicit** branch in enrichment.

**Why:** both failures are invisible — the feature looks wired end-to-end but the
HTML-vs-text body and the enrich-only contract quietly drop the signal.

**How to apply:** the pando signal into enrichment is an explicit `is_pando` flag
(derived from the detected feed at the call site), NOT the presence of
`new_data['owner']` — otherwise source correction breaks whenever
`pandologic_api_user_id` is unset (no owner target). Owner reassignment must be
guarded: only when a pando-owner target exists AND the existing owner is None or
in `VettingConfig.api_user_ids` — never reassign away from a human recruiter.
Note: some automated/system Bullhorn accounts (e.g. a "Parser" or office account)
may NOT be in `api_user_ids`; whether to treat them as reassignable is a
config/judgment call, not covered by the list.
