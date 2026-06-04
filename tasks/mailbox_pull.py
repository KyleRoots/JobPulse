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


def _reset_candidate_for_revet(db, candidate_id):
    """Clear a candidate's existing screening records so the next vetting cycle
    re-scores them with the now-attached résumé. Mirrors the manual
    /screening/revet-candidate reset. Returns True if anything was reset."""
    from models import (
        ParsedEmail, CandidateVettingLog, CandidateJobMatch,
        EmbeddingFilterLog, EscalationLog,
    )

    parsed_emails = ParsedEmail.query.filter(
        ParsedEmail.bullhorn_candidate_id == candidate_id,
        ParsedEmail.status == 'completed',
    ).all()
    if not parsed_emails:
        return False

    pe_ids = [pe.id for pe in parsed_emails]
    vetting_logs = CandidateVettingLog.query.filter(
        CandidateVettingLog.parsed_email_id.in_(pe_ids)
    ).all()
    log_ids = [vl.id for vl in vetting_logs]

    reset_any = False
    if log_ids:
        EmbeddingFilterLog.query.filter(
            EmbeddingFilterLog.vetting_log_id.in_(log_ids)
        ).delete(synchronize_session=False)
        EscalationLog.query.filter(
            EscalationLog.vetting_log_id.in_(log_ids)
        ).delete(synchronize_session=False)
        CandidateJobMatch.query.filter(
            CandidateJobMatch.vetting_log_id.in_(log_ids)
        ).delete(synchronize_session=False)
        CandidateVettingLog.query.filter(
            CandidateVettingLog.id.in_(log_ids)
        ).delete(synchronize_session=False)
        reset_any = True

    for pe in parsed_emails:
        if pe.vetted_at is not None:
            reset_any = True
        pe.vetted_at = None
    return reset_any


def run_resume_recovery(app, since_hours=24, limit=50):
    """Repair applicants that were ingested WITHOUT their résumé.

    Targets completed ParsedEmail rows that have a Bullhorn candidate but no
    résumé file (``resume_file_id IS NULL``) within the last ``since_hours``. For
    each, re-fetch the original mailbox message by its internetMessageId,
    re-extract + AI-parse the résumé, enrich the EXISTING Bullhorn candidate,
    attach the résumé file, set resume_file_id/resume_filename, and reset the
    candidate for re-vetting. Triggers one vetting cycle at the end if any
    candidate was reset.

    Safe + idempotent: it never creates a candidate or job submission, only acts
    when a résumé is actually found in the mailbox, and once resume_file_id is set
    a row is skipped — so re-running only retries rows still missing a résumé.

    Returns a summary dict.
    """
    summary = {
        'candidates': 0, 'recovered': 0, 'enriched': 0,
        'no_resume': 0, 'not_found': 0, 'failed': 0,
        'reset_for_revet': 0, 'vetting_enqueued': False,
        'since_hours': since_hours, 'limit': limit, 'details': [],
    }
    with app.app_context():
        from app import db
        from models import ParsedEmail, VettingConfig
        from email_inbound_service import EmailInboundService

        lock_held = False
        try:
            # Single-flight guard: prevent two concurrent recovery runs from
            # both selecting the same résumé-less rows and double-uploading.
            in_progress = (VettingConfig.get_value(
                'resume_recovery_in_progress', 'false') or 'false').lower() == 'true'
            if in_progress:
                summary['error'] = 'Another résumé recovery run is already in progress.'
                app.logger.warning("🩹 Résumé recovery skipped: already in progress")
                return summary
            VettingConfig.set_value('resume_recovery_in_progress', 'true',
                                    'Résumé recovery run currently executing')
            lock_held = True

            cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(since_hours)))
            cutoff_naive = cutoff.replace(tzinfo=None)

            rows = (
                ParsedEmail.query.filter(
                    ParsedEmail.status == 'completed',
                    ParsedEmail.bullhorn_candidate_id.isnot(None),
                    ParsedEmail.resume_file_id.is_(None),
                    ParsedEmail.message_id.isnot(None),
                    ParsedEmail.created_at >= cutoff_naive,
                )
                .order_by(ParsedEmail.created_at.asc())
                .limit(max(1, min(int(limit), 500)))
                .all()
            )
            summary['candidates'] = len(rows)
            if not rows:
                app.logger.info("🩹 Résumé recovery: no missing-résumé rows in window")
                return summary

            from graph_mail_service import GraphMailService
            service = GraphMailService()
            svc = EmailInboundService()

            any_reset = False
            for pe in rows:
                candidate_id = pe.bullhorn_candidate_id
                detail = {'parsed_email_id': pe.id, 'candidate_id': candidate_id}
                try:
                    msg = service.get_message_by_internet_id(pe.message_id)
                    if not msg:
                        summary['not_found'] += 1
                        detail['outcome'] = 'message_not_found'
                        summary['details'].append(detail)
                        continue

                    attachments = []
                    if msg.get('hasAttachments'):
                        attachments = service.get_attachments(msg['id'])
                    payload = service.to_payload(msg, attachments)

                    res = svc.recover_resume_for_existing_candidate(
                        payload, candidate_id
                    )

                    if not res.get('success'):
                        msg_text = res.get('message', '')
                        if 'No résumé attachment' in msg_text:
                            summary['no_resume'] += 1
                            detail['outcome'] = 'no_resume_in_mailbox'
                        else:
                            summary['failed'] += 1
                            detail['outcome'] = f"failed: {msg_text[:120]}"
                        summary['details'].append(detail)
                        continue

                    # Persist the attached-résumé marker FIRST and on its own
                    # commit. The Bullhorn upload already happened, so committing
                    # resume_file_id immediately ensures a later failure can't
                    # leave the row NULL and trigger a duplicate re-upload on
                    # re-run.
                    pe.resume_file_id = res.get('resume_file_id')
                    if res.get('resume_filename'):
                        pe.resume_filename = res['resume_filename']
                    db.session.commit()
                    summary['recovered'] += 1
                    if res.get('enriched_fields'):
                        summary['enriched'] += 1

                    # Reset for re-vet so the score reflects the real résumé.
                    if _reset_candidate_for_revet(db, candidate_id):
                        db.session.commit()
                        summary['reset_for_revet'] += 1
                        any_reset = True

                    detail['outcome'] = 'recovered'
                    detail['resume_filename'] = res.get('resume_filename')
                    summary['details'].append(detail)
                    app.logger.info(
                        f"🩹 Résumé recovery: candidate {candidate_id} "
                        f"(ParsedEmail {pe.id}) recovered — "
                        f"{res.get('resume_filename')}"
                    )
                except Exception as e:  # noqa: BLE001
                    db.session.rollback()
                    summary['failed'] += 1
                    detail['outcome'] = f"exception: {str(e)[:120]}"
                    summary['details'].append(detail)
                    app.logger.error(
                        f"🩹 Résumé recovery error on ParsedEmail {pe.id} "
                        f"(candidate {candidate_id}): {e}", exc_info=True
                    )

            if any_reset:
                try:
                    from utils.screening_dispatch import enqueue_vetting_now
                    enq = enqueue_vetting_now(reason='resume_recovery')
                    summary['vetting_enqueued'] = bool(enq.get('enqueued'))
                except Exception as e:  # noqa: BLE001
                    app.logger.warning(
                        f"🩹 Résumé recovery: could not enqueue re-vet cycle: {e}"
                    )

            app.logger.info(f"🩹 Résumé recovery complete: {summary}")
            return summary
        except Exception as e:  # noqa: BLE001
            app.logger.error(f"🩹 Résumé recovery fatal error: {e}", exc_info=True)
            summary['error'] = str(e)
            return summary
        finally:
            if lock_held:
                try:
                    VettingConfig.set_value('resume_recovery_in_progress', 'false',
                                            'Résumé recovery run finished')
                except Exception as e:  # noqa: BLE001
                    app.logger.error(
                        f"🩹 Résumé recovery: failed to release lock: {e}")
            db.session.remove()


def run_resume_recovery_sweep(app):
    """Scheduled auto-heal pass for applicants ingested without their résumé.

    Thin wrapper around :func:`run_resume_recovery` for unattended scheduler use.
    Unlike the operator-triggered button (which uses a wide, user-chosen window
    for deliberate incident recovery), this sweep uses a SHORT rolling window so
    it heals fresh post-commit failures automatically without endlessly
    re-fetching permanently-unrecoverable rows (e.g. job-board forwards that
    never carried a résumé) — those simply age out of the window.

    Gated by the ``resume_recovery_sweep_enabled`` DB flag (default ON) so it can
    be toggled in production without a republish. Window/limit are configurable
    via ``resume_recovery_sweep_window_hours`` / ``resume_recovery_sweep_limit``.
    Independent of ``mailbox_pull_enabled`` so it can still heal while steady
    polling is paused. Fail-soft: never raises.
    """
    try:
        with app.app_context():
            from models import VettingConfig

            enabled = (VettingConfig.get_value(
                'resume_recovery_sweep_enabled', 'true') or 'true').lower() == 'true'
            if not enabled:
                return

            try:
                window = int(VettingConfig.get_value(
                    'resume_recovery_sweep_window_hours', '6') or 6)
            except (TypeError, ValueError):
                window = 6
            window = max(1, min(window, 72))

            try:
                limit = int(VettingConfig.get_value(
                    'resume_recovery_sweep_limit', '25') or 25)
            except (TypeError, ValueError):
                limit = 25
            limit = max(1, min(limit, 200))

        summary = run_resume_recovery(app, since_hours=window, limit=limit)
        # Only log when the sweep actually touched something — keeps the steady
        # "nothing to do" case quiet.
        if summary.get('candidates'):
            app.logger.info(f"🩹 Résumé recovery sweep: {summary}")
    except Exception as e:  # noqa: BLE001
        app.logger.error(f"🩹 Résumé recovery sweep error: {e}", exc_info=True)
