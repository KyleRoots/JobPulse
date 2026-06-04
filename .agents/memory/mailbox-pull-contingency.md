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

## Résumé-recovery tool invariants (the repair path for the above)
- Recovery must ENRICH the existing Bullhorn candidate, never create one — re-fetch the original message by `internetMessageId`, re-parse, `upload_candidate_file` + enrichment update only. No `create_candidate`/`create_job_submission`.
- **Idempotency hinges on the resume-file marker, and ordering matters**: the Bullhorn file upload is an external side effect that happens BEFORE the local DB marker. Commit `ParsedEmail.resume_file_id` on its OWN commit immediately after a successful upload, separate from the re-vet reset — otherwise a later commit failure leaves the row NULL and a re-run re-uploads a duplicate résumé to Bullhorn. The `resume_file_id IS NULL` filter is the skip/idempotency gate, so it must be the first thing persisted.
- Serialize runs with a DB single-flight flag (`resume_recovery_in_progress` in VettingConfig, released in `finally`); two concurrent runs would both select the same NULL rows and double-upload.
- After attaching, reset for re-vet (mirror `/screening/revet-candidate`: clear EmbeddingFilterLog/EscalationLog/CandidateJobMatch by vetting_log_id + CandidateVettingLog + null `vetted_at`) so the score reflects the real résumé, then enqueue one cycle.

## Recovery boundary: only apply@-mailbox messages are recoverable (2026-06-04 run)
- Recovery resolves each row by looking its `internetMessageId` up IN the apply@ mailbox. It therefore ONLY repairs rows whose original message actually lives there — i.e. the mailbox-pull incident applicants. Verified: all 10 mailbox-pull victims recovered; 5 older rows did NOT and that's correct, not a bug.
- The un-recoverable rows were `source_platform='LinkedIn Job Board'` forwards from info@ (not delivered to apply@), so their stored Message-ID doesn't resolve in apply@; one even had a malformed Message-ID (captured header text). Don't chase these as failures — they have no retrievable source email. Real leftover applicants must be re-uploaded manually in Bullhorn.
- **How to apply:** when a recovery run leaves rows still NULL, first check `source_platform` / `sender_email` / `message_id` shape before assuming the tool failed — non-apply@ origins are expected misses.
