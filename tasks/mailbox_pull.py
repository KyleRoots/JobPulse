"""
Mailbox-Pull Ingestion — scheduled poller + one-time backlog backfill.

EMERGENCY CONTINGENCY: pulls applicant emails from the connected apply@ Office
365 mailbox via Microsoft Graph and feeds each one into the EXISTING inbound
pipeline (EmailInboundService.process_email) unchanged. This bypasses the broken
SendGrid Inbound Parse path (load-balancer body truncation) entirely.

Both functions are fully fail-soft: any error is logged and swallowed so a Graph
outage can never crash the scheduler or block other jobs. Duplicate protection is
guaranteed by ParsedEmail.message_id (UNIQUE), populated from internetMessageId —
so the poller and any residual webhook deliveries can never double-create.

Runtime control lives in VettingConfig (DB-backed, so it can be toggled in
production WITHOUT a republish):
    mailbox_pull_enabled        'true'/'false'  master switch (default false)
    mailbox_pull_high_water     ISO8601 UTC     last processed receivedDateTime
    mailbox_pull_batch_size     int             messages per cycle (default 25)
    mailbox_pull_last_run       ISO8601 UTC     telemetry: last cycle time
    mailbox_pull_last_count     int             telemetry: processed last cycle
    mailbox_pull_last_error     str             telemetry: last error (or '')
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_DEFAULT_BATCH = 25
_DEFAULT_BACKFILL_HOURS = 24


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _process_one(service, msg) -> dict:
    """Adapt a single Graph message and run it through the existing pipeline.

    Returns the process_email result dict (or a synthesized one on error).
    """
    from email_inbound_service import EmailInboundService

    attachments = []
    if msg.get("hasAttachments"):
        attachments = service.get_attachments(msg["id"])
    payload = service.to_payload(msg, attachments)
    return EmailInboundService().process_email(payload)


def run_mailbox_pull_cycle(app):
    """Scheduler entrypoint: poll the applicant mailbox once and process any new
    messages. Gated by the `mailbox_pull_enabled` DB flag; fully fail-soft."""
    with app.app_context():
        from app import db
        from models import VettingConfig

        try:
            enabled = (VettingConfig.get_value('mailbox_pull_enabled', 'false') or 'false')
            if enabled.lower() != 'true':
                return

            try:
                batch = int(VettingConfig.get_value('mailbox_pull_batch_size', _DEFAULT_BATCH))
            except (ValueError, TypeError):
                batch = _DEFAULT_BATCH
            batch = max(1, min(batch, 100))

            high_water = VettingConfig.get_value('mailbox_pull_high_water', None)

            from graph_mail_service import GraphMailService
            service = GraphMailService()

            # First-ever run with no high-water: anchor to (now - backfill_hours)
            # so flipping the toggle ON automatically drains the recent backlog
            # (the applicants lost during the outage) over subsequent cycles —
            # bounded per cycle by batch size and dedupe-protected — without
            # stampeding the entire historical inbox. Operator can widen the
            # window via `mailbox_pull_backfill_hours` before enabling.
            if not high_water:
                try:
                    backfill_hours = int(VettingConfig.get_value(
                        'mailbox_pull_backfill_hours', _DEFAULT_BACKFILL_HOURS))
                except (ValueError, TypeError):
                    backfill_hours = _DEFAULT_BACKFILL_HOURS
                backfill_hours = max(0, min(backfill_hours, 720))  # cap 30 days
                anchor = _iso(
                    datetime.now(timezone.utc) - timedelta(hours=backfill_hours)
                )
                VettingConfig.set_value(
                    'mailbox_pull_high_water', anchor,
                    'Mailbox-pull last processed receivedDateTime (UTC ISO8601)'
                )
                app.logger.info(
                    f"📥 Mailbox-pull: initialized high-water to {anchor} "
                    f"(draining last {backfill_hours}h of mail)"
                )
                high_water = anchor

            messages = service.list_messages(since_iso=high_water, limit=batch)
            if not messages:
                VettingConfig.set_value('mailbox_pull_last_run', _utcnow_iso())
                VettingConfig.set_value('mailbox_pull_last_count', '0')
                VettingConfig.set_value('mailbox_pull_last_error', '')
                return

            processed = 0
            duplicates = 0
            failures = 0
            # Advance the durable cursor ONLY through the unbroken run of
            # successfully-processed (or duplicate / intentionally-ignored)
            # messages from the start of the batch. The first HARD failure stops
            # the cursor there so the next cycle retries that applicant instead of
            # silently skipping them — losing real applicants is the exact failure
            # we are guarding against. This does NOT cause a permanent wedge:
            # process_email persists the message_id row before its heavy work and
            # marks it 'failed' on error, so a re-fetched failed message dedupes
            # (counts as a duplicate) on the next cycle and the cursor moves on.
            high_water_advance = high_water
            contiguous_ok = True

            for msg in messages:
                received = msg.get("receivedDateTime") or ""
                ok = False
                try:
                    result = _process_one(service, msg)
                    if result.get('duplicate') or result.get('is_duplicate'):
                        duplicates += 1
                    if result.get('success') or result.get('ignored'):
                        processed += 1
                        ok = True
                    else:
                        failures += 1
                except Exception as e:  # noqa: BLE001
                    failures += 1
                    app.logger.error(
                        f"📥 Mailbox-pull: error processing message "
                        f"{msg.get('id', '')[:40]}: {e}"
                    )

                if not ok:
                    contiguous_ok = False
                elif contiguous_ok and received and received > (high_water_advance or ''):
                    high_water_advance = received

            if high_water_advance and high_water_advance != high_water:
                VettingConfig.set_value('mailbox_pull_high_water', high_water_advance)
            max_received = high_water_advance

            VettingConfig.set_value('mailbox_pull_last_run', _utcnow_iso())
            VettingConfig.set_value('mailbox_pull_last_count', str(processed))
            VettingConfig.set_value('mailbox_pull_last_error', '')

            app.logger.info(
                f"📥 Mailbox-pull cycle: {processed} processed "
                f"({duplicates} dup, {failures} failed) of {len(messages)} fetched; "
                f"high-water → {max_received}"
            )
        except Exception as e:  # noqa: BLE001
            app.logger.error(f"📥 Mailbox-pull cycle error: {e}", exc_info=True)
            try:
                VettingConfig.set_value('mailbox_pull_last_error', str(e)[:480])
                VettingConfig.set_value('mailbox_pull_last_run', _utcnow_iso())
            except Exception:  # noqa: BLE001
                pass
        finally:
            db.session.remove()


def run_mailbox_backfill(app, since_iso, limit=500):
    """One-time, bounded backlog recovery for the outage window.

    Processes inbox messages with receivedDateTime >= since_iso, oldest-first, up
    to `limit` messages, through the existing pipeline. message_id dedupe makes
    this safe to re-run and prevents double-submission to Bullhorn. Does NOT touch
    the live poller high-water mark.

    Returns a summary dict.
    """
    summary = {
        'fetched': 0, 'processed': 0, 'duplicates': 0,
        'failed': 0, 'since': since_iso, 'limit': limit,
    }
    with app.app_context():
        from app import db
        try:
            from graph_mail_service import GraphMailService
            service = GraphMailService()

            remaining = max(1, int(limit))
            seen_ids = set()
            # Walk the window with Graph's server-side @odata.nextLink paging
            # instead of a re-issued timestamp filter. This guarantees the whole
            # window is traversed completely and in order even when many messages
            # share the same receivedDateTime at a page boundary (a timestamp-only
            # cursor can stall there). message_id dedupe still protects against
            # any double-submission to Bullhorn.
            next_link = None
            first = True

            while remaining > 0:
                page_size = min(remaining, 50)
                page, next_link = service.list_messages_page(
                    since_iso=(since_iso if first else None),
                    page_size=page_size,
                    next_link=next_link,
                )
                first = False
                if not page:
                    break

                for msg in page:
                    mid = msg.get("id")
                    if mid and mid in seen_ids:
                        continue
                    if mid:
                        seen_ids.add(mid)
                    summary['fetched'] += 1
                    try:
                        result = _process_one(service, msg)
                        if result.get('duplicate') or result.get('is_duplicate'):
                            summary['duplicates'] += 1
                        if result.get('success') or result.get('ignored'):
                            summary['processed'] += 1
                        else:
                            summary['failed'] += 1
                    except Exception as e:  # noqa: BLE001
                        summary['failed'] += 1
                        app.logger.error(
                            f"📥 Backfill: error on message "
                            f"{msg.get('id', '')[:40]}: {e}"
                        )
                    remaining -= 1
                    if remaining <= 0:
                        break

                if not next_link:
                    break

            app.logger.info(f"📥 Mailbox backfill complete: {summary}")
            return summary
        except Exception as e:  # noqa: BLE001
            app.logger.error(f"📥 Mailbox backfill error: {e}", exc_info=True)
            summary['error'] = str(e)
            return summary
        finally:
            db.session.remove()
