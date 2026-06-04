---
name: Inbound raw-MIME fallback contract
description: How the SendGrid inbound webhook recovers empty parsed payloads, and the two non-obvious SendGrid field traps.
---

# Inbound raw-MIME fail-soft fallback

The inbound webhook (`routes/email.py::email_inbound_webhook`) can receive emails where SendGrid's pre-parsed form fields (`from/subject/text/html/attachments`) are all empty — e.g. when Inbound Parse is in "POST raw MIME" mode, or a host's parse settings drift. Without a fallback, a real candidate is silently dropped by the noise gate. The fix reconstructs the payload from the raw RFC822 message (`payload['email']`, or `request.get_data()` for non-form posts) and merges recovered values **only into empty keys** so a normally-parsed payload is never altered.

## Two SendGrid field traps (both cost real debugging)

1. **`attachments` is a COUNT field, not data.** SendGrid sends `attachments='0'` (a string count), and this codebase's downstream `_extract_attachments` *also* accepts an `'attachments'` JSON-list shape. So `'attachments'` can be present-but-useless.
   - **Trap A — detection:** `_has_parsed_email_fields` must NOT treat mere presence of `'attachments'` as "usable parsed fields", or `attachments='0'` wrongly suppresses the fallback on the exact incident shape. Require a parseable non-empty JSON list (or positive numeric count).
   - **Trap B — merge:** the "fill empty keys only" rule treats `'0'` as non-empty (truthy string), so a recovered attachment list would never overwrite it and the resume is lost *even though the fallback engaged*. The `attachments` key must be special-cased in the merge: replace unless the existing value is already a usable JSON list (`_has_usable_attachments`).

2. **Flask stream order:** read `request.form` / `request.files` BEFORE `request.get_data()`. Only fall back to `get_data()` when both form and files are empty (non-form body); otherwise form parsing has already consumed the stream.

**Why:** live incident — every inbound POST arrived with blank from/subject + `attachments='0'`; both traps had to be fixed for recovery to actually deliver the attachment.
**How to apply:** any change to the inbound webhook payload normalization must preserve both: count-aware attachment detection AND count-aware attachment merge. The `'attachments'` JSON-list shape emitted by the fallback must match `_extract_attachments` (`{filename, content(base64), type}`).
