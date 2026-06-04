---
name: Mailbox-pull ingestion contingency
description: Why applicant ingestion was moved from the SendGrid webhook to a Microsoft Graph mailbox poller, and the dedupe/cursor invariants that keep it lossless.
---

# Mailbox-Pull Ingestion (Graph poller bypass)

**Problem it solves:** Replit's GCE load balancer truncates inbound POST bodies to
~4096 bytes before they reach gunicorn, so the SendGrid Inbound Parse webhook
silently loses real applicant emails (and the application form, which is ALSO sent
as an email to apply@). The same emails arrive 100% intact in the apply@myticas.com
O365 shared mailbox.

**Fix shape:** PULL from the mailbox via Microsoft Graph (Replit Outlook connector,
authed AS apply@ — `/me/...` endpoints, plain Mail.Read) and feed each message into
the EXISTING `EmailInboundService().process_email(payload)` pipeline unchanged. The
Graph layer only adapts a message into the webhook-shaped payload dict.

**Why:** infra-independent ingestion that covers both direct applies and form
submissions with one mechanism, and reuses all existing AI résumé parsing.

## Non-obvious pipeline behavior (don't relearn the hard way)
- `process_email` persists the `ParsedEmail` row (with `message_id`) BEFORE the heavy
  work (AI parse, Bullhorn create), and on exception flips that row to
  `status='failed'`. Consequence: a message that fails AFTER that first commit is
  **never retried** — its `message_id` is recorded, so the next pull dedupes it as a
  duplicate. This is inherited webhook behavior, not the poller's. Treat
  per-message dedupe (`ParsedEmail.message_id` UNIQUE) as the real idempotency gate.
- Dedupe key: `internetMessageId` → mapped into the `headers` blob as `Message-ID:`.
  **Always emit a key** — if `internetMessageId` is ever absent, fall back to the
  Graph message `id` (`graph-id-<id>`), or a re-pull/backfill can double-create a
  Bullhorn candidate.

## Cursor / completeness invariants
- Steady poller advances its durable high-water (`mailbox_pull_high_water`, in
  VettingConfig) ONLY through the contiguous run of success/duplicate/ignored
  messages; it stops at the first HARD failure so the next cycle retries rather than
  skipping an applicant. No permanent wedge because failed rows dedupe next cycle.
- First enable anchors the cursor to `now - mailbox_pull_backfill_hours` (default 24,
  cap 720) so flipping the toggle ON auto-drains the recent outage backlog.
- The one-time backfill walks Graph `@odata.nextLink` (server-side paging), NOT a
  re-issued `receivedDateTime ge` filter — a timestamp-only cursor can stall when
  many messages share the boundary second.
- All DB-backed flags live in VettingConfig (toggle in prod WITHOUT republish):
  `mailbox_pull_enabled`, `mailbox_pull_batch_size`, `mailbox_pull_backfill_hours`.

## Graph /attachments $select gotcha (2026-06-04 prod incident)
- **DO NOT use `$select` on `/me/messages/{id}/attachments` at all.** The collection is polymorphic (base type `microsoft.graph.attachment`); `contentBytes` exists ONLY on the derived `fileAttachment`, so `$select=...,contentBytes` → **400 BadRequest: "Could not find a property named 'contentBytes' on type 'microsoft.graph.attachment'"**. (`@odata.type` in `$select` also 400s — it's an annotation, not a property.) Just list with NO `$select`: Graph returns the full fileAttachment incl. `contentBytes` (for items under the inline limit; larger → `/$value`) and `@odata.type` automatically. Verified live: a 707 KB PDF came back inline with `contentBytes` present.
- **Why it mattered**: the 400 was caught fail-soft (returned []), so applicants still ingested but WITHOUT their résumé attachment — silent quality loss. Burned message_ids (dedupe blocks reprocessing), so recovery isn't a simple re-pull: candidates already exist in Bullhorn (dup risk) and ParsedEmail rows already committed.
