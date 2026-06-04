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

2. **Flask stream order (UPDATED Jun 4 2026):** read the raw body FIRST — `request.get_data(cache=True, parse_form_data=False)` — then do the PRIMARY parse yourself by running `werkzeug.formparser.parse_form_data` over `BytesIO(raw_body)`. Do NOT touch `request.form`/`request.files` (accessing them after caching the body is moot, and accessing them first would consume the stream so recovery from raw bytes is impossible). This unifies the normal path and the recovery path on the same cached bytes.

## The REAL Jun 3–4 root cause: Werkzeug parses a present body into ZERO parts

The earlier "raw-MIME mode" theory was the WRONG failure mode for this incident. Prod diagnostic logs proved the body was fully present (`content_type=multipart/form-data; boundary=…`, `content_length=143517`) yet Werkzeug 3.1.5 produced `form_keys=[] file_keys=[]` — zero parts, no exception, HTTP 200. **Zero parts (not partial) ⇒ Werkzeug could not find the boundary delimiter at all = the boundary inside the body did not match the declared Content-Type boundary.** A synthetic same-boundary body parses fine on the identical Werkzeug, so it is a real body/header mismatch coming from SendGrid (no deploy happened at the 22:32 break — it was sender-side).

**The durable fix:** when the strict primary parse yields nothing usable, run a tolerant parser (`_parse_multipart_tolerant`) built on stdlib `email.BytesParser`; if the declared boundary still yields no parts, **sniff the actual boundary out of the first lines of the body** (`_sniff_multipart_boundary`) and retry. Keep the raw-MIME (`email` field / non-form body) layer underneath, and if everything fails, log a sanitized body snippet + sniffed boundary (you cannot see the prod raw body any other way).

**Why:** live incident — strict multipart parsers fail CLOSED (empty, not error) on a boundary mismatch, silently dropping every candidate. **How to apply:** never trust a single strict multipart parse on the inbound webhook; always keep raw-bytes + boundary-sniff recovery and self-diagnostic snippet logging. Ruled out (do not re-chase): `before_request` (bails on `/api`), `MAX_CONTENT_LENGTH` (50MB), `max_form_memory_size` (500KB) — body is 143KB.

**Why (attachment traps):** every inbound POST arrived with blank from/subject + `attachments='0'`; both traps had to be fixed for recovery to actually deliver the attachment.
**How to apply (attachment traps):** any change to the inbound webhook payload normalization must preserve both: count-aware attachment detection AND count-aware attachment merge. The `'attachments'` JSON-list shape emitted by the fallback/tolerant parser must match `_extract_attachments` (`{filename, content(base64), type}`).
