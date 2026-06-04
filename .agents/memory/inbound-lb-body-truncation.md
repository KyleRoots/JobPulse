---
name: Inbound webhook body truncation (infra LB)
description: Production load balancer truncates inbound POST bodies to 0 or exactly 4096 bytes; systematic, not intermittent; retries don't help.
---

# Inbound webhook body truncation at the infra layer

The deployed `/api/email/inbound` webhook receives POST bodies that are
truncated **before reaching the gunicorn worker**. The `Content-Length` header
is preserved and correct (e.g. 114906, 143580), but the bytes actually readable
from the WSGI input stream are **either 0 or exactly 4096** (one page / common
proxy buffer). Proven NOT an app/gunicorn/--preload bug: the identical request +
gunicorn config reads the full body 100% of the time locally.

**Why it matters / what was learned:**
- The truncation is **systematic, not intermittent** as first hypothesized. Once
  the 503-retry workaround went live, SendGrid retried the same email every
  ~30-60s and **every retry truncated identically** (body_len stayed 0/4096).
  So retry-based recovery does NOT rescue these candidates — it only prevents
  corruption.
- The `body_len in {0, 4096}` ceiling is the smoking gun for an upstream proxy
  body-buffer / request-size limit (GCE deployment). This is the single best
  piece of evidence for the Replit support ticket.

**The two distinct symptoms it produces (don't confuse them):**
- Large `Content-Length` + `body_len` 0/4096 → real candidate, body lost in
  transit → the 503 guard catches it (returns 503, no fake record created).
- `Content-Length` 0 from the start → genuinely empty/noise email → the noise
  gate records `status='ignored'` "None None". This is correct behavior, a
  different path, and was an EARLIER fix — not the truncation bug.

**Workaround in place (does its job, but is not a cure):** webhook returns 503
when `len(raw_body) < content_length` so SendGrid retries instead of the app
creating a résumé-less / "None None" candidate. Prevents data corruption; does
NOT make truncated candidates flow.

**Real fix options (require user approval, not yet built):**
- Replit support must lift/fix the infra body-buffer limit (primary path).
- Alternative ingestion: the candidate emails arrive INTACT in the O365 mailbox
  (apply@myticas.com) WITH attachments. Pulling via Microsoft Graph (mailbox
  poll) bypasses the truncating LB entirely. Bigger build; propose before doing.
