---
name: Inbound webhook body short-read truncation
description: What body_len≈4096 << content_length means on the SendGrid inbound webhook, and why it's NOT a boundary/raw-MIME problem.
---

# Inbound webhook: 4 KB body truncation = WSGI short read

When the inbound email webhook logs `content_length=<big> body_len=~4096 form_keys=[] file_keys=[]`,
the cause is a **short read of the request body**: a single `read()` on the WSGI input
returns only the first buffered chunk (~4 KB) while the body is still arriving, so the
multipart parser never sees the whole message.

**Symptoms (both from the same cause, intermittent — it's a buffering race):**
- Candidate created but **no résumé file** — the read reached the early text fields
  (sender/subject) but stopped before the attachment, which sits at the END of the body.
- **"None None" / source "Other" / ignored** — the read stopped before even sender/subject.

**Fix:** read the body in a LOOP until `content_length` (or EOF) before parsing — see
`_read_full_request_body` in `routes/email.py`. If it's still short after looping, that's a
distinct, harder failure (upstream/proxy truncation) — a warning is logged for it.

**Why this matters / what NOT to do:** this exact symptom was twice mis-diagnosed first as a
SendGrid "raw MIME payload-shape change" and then as a "body/header multipart **boundary
mismatch**." Both were wrong. The tolerant-multipart + raw-MIME recovery layers were built on
those wrong theories; they're harmless defense-in-depth but they do NOT fix truncation — only
reading the full body does. Don't re-chase boundary/raw-MIME theories when body_len is short.
