---
name: Inbound parse-failure alert noise gate
description: Why non-candidate inbound emails must be gated before firing admin "Candidate Parse Failure" alerts.
---

# Inbound parse-failure alert noise gate

The SendGrid inbound webhook treats EVERY inbound email as a candidate
submission attempt. Junk/automated traffic (bounces, auto-replies, delivery
receipts, malformed webhook posts) therefore reaches the parse-failure path
and fires a `[Scout Genius] Candidate Parse Failure — None None` admin email
each time.

**Rule:** an inbound message with **no attachment**, **no extractable
name/contact**, AND a **blank sender or blank subject** is NOT a candidate
submission — record it for audit (`ParsedEmail.status='ignored'`) but DO NOT
send an admin alert.

**Why:** Jun 2026 flood — failures jumped from ~0–1/day to 22+/day; 29/31 in
one 24h window shared the exact fingerprint (blank `sender_email`, blank
`subject`, `source_platform='Other'`, no attachment, nothing extractable). A
real forwarded candidate email always has a sender; the "None None" alert
carries zero actionable data (nothing to create manually).

**How to apply:** keep the alert for any message that has an attachment (real
candidate whose resume couldn't be read — recruiter can follow up) or that has
BOTH a real sender and a real subject (some signal it might be real). The
gate uses sender-OR-subject-blank intentionally, so blank-either alone is
treated as noise. Post-deploy, watch `status='ignored'` volume vs alert
volume to confirm no real-candidate loss before tightening further.

## Empty-POST flood at the public ingress (verified Jun 2026, prod)
Root cause of the noise: the public, unauthenticated webhook `/api/email/inbound`
accepts and PERSISTS a `ParsedEmail` row for EVERY POST (always returns 200,
spawns a bg thread), including totally-empty payloads (no sender, no recipient,
no subject, NULL `message_id`, no attachment, source defaults to 'Other'). Prod
saw ~400-450 such empty POSTs/day as a steady around-the-clock drip — likely a
bot/scanner probing the public URL, an uptime monitor, or a misconfigured
SendGrid route. The `status='ignored'` gate is only a DOWNSTREAM band-aid; it
still creates a DB row and burns a thread per hit and clutters the
`/email-parsing` Processed Emails view. **Verified safe:** of 916 ignored rows
in 72h, ZERO had a resume / candidate email / phone / Bullhorn id — no real
candidate was ever ignored. Real root-cause options: (a) early-reject empty
payloads at ingress before persisting, (b) actually enforce SendGrid signature
verification (the code comments claim it but it isn't implemented), (c) UI:
hide `ignored` from the default Processed Emails view.
