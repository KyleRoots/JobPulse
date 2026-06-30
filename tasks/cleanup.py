import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def activity_retention_cleanup():
    """Clean up BullhornActivity records older than 15 days"""
    from app import app
    from extensions import db
    with app.app_context():
        try:
            from models import BullhornActivity
            cutoff_date = datetime.utcnow() - timedelta(days=15)

            old_activities = BullhornActivity.query.filter(
                BullhornActivity.created_at < cutoff_date
            ).count()

            if old_activities > 0:
                deleted_count = BullhornActivity.query.filter(
                    BullhornActivity.created_at < cutoff_date
                ).delete()

                db.session.commit()
                app.logger.info(f"Activity cleanup: Removed {deleted_count} activity records older than 15 days")

                cleanup_activity = BullhornActivity(
                    monitor_id=None,
                    activity_type='system_cleanup',
                    details=f"Removed {deleted_count} activity records older than 15 days",
                    notification_sent=False,
                    created_at=datetime.utcnow()
                )
                db.session.add(cleanup_activity)
                db.session.commit()
            else:
                app.logger.info("Activity cleanup: No old activities to remove")

        except Exception as e:
            app.logger.error(f"Activity cleanup error: {str(e)}")
            db.session.rollback()


def log_monitoring_cycle():
    """Run log monitoring cycle - fetches Render logs, analyzes for issues, auto-fixes or escalates."""
    from app import app
    with app.app_context():
        try:
            from log_monitoring_service import run_log_monitoring_cycle
            result = run_log_monitoring_cycle()
            app.logger.info(f"Log monitoring cycle complete: {result['logs_analyzed']} logs, "
                          f"{result['issues_found']} issues found, {result['auto_fixed']} auto-fixed, "
                          f"{result['escalated']} escalated")
        except ImportError as e:
            app.logger.warning(f"Log monitoring service not available: {e}")
        except Exception as e:
            app.logger.error(f"Log monitoring error: {e}")


def email_parsing_timeout_cleanup():
    """Reap stuck email-parsing records (status='processing' past the timeout).

    A row stays 'processing' when the worker died mid-pipeline (e.g. a gunicorn
    crash). Two behaviors, selected by the `email_processing_auto_recovery_enabled`
    DB flag:

    - OFF (default — current behavior): auto-FAIL the stuck rows. A failed row
      never reaches Bullhorn, so the applicant is silently dropped until someone
      runs the manual outage-recovery endpoint.
    - ON: auto-RECOVER. Clear the stuck rows' message_id and run ONE bounded
      mailbox backfill that re-fetches + reprocesses them through the full
      pipeline (same mechanism as the manual outage-recovery, coordinated by the
      SAME cross-worker marker so the two can never overlap and double-submit).
      Poison protection caps how many times the same message is re-driven —
      counted via the 'recovery_superseded' breadcrumbs each re-drive leaves —
      so a message that crashes the worker every time can't loop forever: once
      exhausted it is failed + logged for a human, and its intact message_id
      keeps the backfill from re-fetching it.
    """
    from app import app
    from extensions import db
    with app.app_context():
        try:
            from models import ParsedEmail, VettingConfig

            def _int_cfg(key, default):
                try:
                    return int(VettingConfig.get_value(key, str(default)))
                except (TypeError, ValueError):
                    return default

            timeout_min = max(1, _int_cfg(
                'email_processing_recovery_timeout_minutes', 10))
            timeout_threshold = datetime.utcnow() - timedelta(minutes=timeout_min)

            stuck_records = ParsedEmail.query.filter(
                ParsedEmail.status == 'processing',
                ParsedEmail.created_at < timeout_threshold
            ).order_by(ParsedEmail.received_at.asc()).all()

            if not stuck_records:
                return

            auto_recover = (VettingConfig.get_value(
                'email_processing_auto_recovery_enabled', 'false')
                or 'false').strip().lower() == 'true'

            # ── Default behavior: auto-FAIL stuck rows ───────────────────────
            if not auto_recover:
                for record in stuck_records:
                    record.status = 'failed'
                    record.processing_notes = (
                        f"Auto-failed: Processing timeout after {timeout_min} "
                        f"minutes (started at {record.created_at})")
                    record.processed_at = datetime.utcnow()
                    app.logger.warning(
                        f"Auto-failed stuck email parsing record ID {record.id} "
                        f"(candidate: {record.candidate_name or 'Unknown'})")
                db.session.commit()
                app.logger.info(
                    f"Email parsing cleanup: Auto-failed {len(stuck_records)} "
                    f"stuck records")
                return

            # ── Auto-recovery behavior: RE-DRIVE stuck rows ──────────────────
            max_retries = max(1, _int_cfg(
                'email_processing_recovery_max_retries', 2))
            backfill_limit = max(1, _int_cfg(
                'email_processing_recovery_backfill_limit', 100))
            # Window for counting prior re-drive attempts of the SAME message.
            # Auto-scales with the cycle so one poison sequence is fully counted.
            poison_window_min = max(60, timeout_min * (max_retries + 2))
            poison_cutoff = datetime.utcnow() - timedelta(minutes=poison_window_min)

            to_redrive = []
            exhausted = []
            no_key = []
            for record in stuck_records:
                # Auto-recovery re-drives by RE-FETCHING the email from the
                # mailbox; the message_id is the dedupe key that links the
                # re-fetched copy back and prevents double-submission. A stuck row
                # with NO message_id cannot be safely auto-recovered (same reason
                # the manual outage-recovery requires message_id IS NOT NULL), so
                # it is failed visibly for manual review instead of superseded.
                if not record.message_id:
                    no_key.append(record)
                    continue
                # Count prior re-drive attempts of the SAME logical email. The
                # STABLE identity is the Message-ID: every re-drive re-fetches the
                # email under the same Message-ID and the breadcrumb preserves it
                # in recovery_message_id, so the count accumulates across the
                # successor-row churn (a per-row id/subject/email would not — each
                # re-fetch is a brand-new row, so those reset the count and could
                # loop a malformed/blank-subject poison email forever). Only a
                # message that keeps CRASHING accrues 'recovery_superseded'
                # breadcrumbs; a transient backfill outage is reverted (below)
                # without leaving one, so it never trips this guard.
                poison_q = ParsedEmail.query.filter(
                    ParsedEmail.status == 'recovery_superseded',
                    ParsedEmail.created_at >= poison_cutoff,
                    ParsedEmail.recovery_message_id == record.message_id,
                )
                if poison_q.count() >= max_retries:
                    exhausted.append(record)
                else:
                    to_redrive.append(record)

            # Poison-exhausted → fail + admin-visible error. Leave message_id
            # INTACT so the backfill re-fetch dedupes it (stops the crash loop).
            for record in exhausted:
                record.status = 'failed'
                record.processed_at = datetime.utcnow()
                record.processing_notes = (
                    (record.processing_notes or '')
                    + f" | Auto-recovery: gave up after {max_retries} re-drive "
                      f"attempts (poison-message protection); left for manual "
                      f"review."
                )[:2000]
                app.logger.error(
                    f"🛑 Email auto-recovery GAVE UP on stuck record ID "
                    f"{record.id} (subject={record.subject!r}, candidate="
                    f"{record.candidate_name or 'Unknown'}) after {max_retries} "
                    f"re-drive attempts — needs manual review.")
            # No dedupe key → cannot auto-recover; fail visibly (never silently
            # superseded, which would terminally strand the applicant).
            for record in no_key:
                record.status = 'failed'
                record.processed_at = datetime.utcnow()
                record.processing_notes = (
                    (record.processing_notes or '')
                    + " | Auto-recovery: no message_id (no dedupe key); cannot "
                      "auto-recover — needs manual review."
                )[:2000]
                app.logger.error(
                    f"🛑 Email auto-recovery cannot recover stuck record ID "
                    f"{record.id} (subject={record.subject!r}, candidate="
                    f"{record.candidate_name or 'Unknown'}) — no message_id; "
                    f"needs manual review.")
            if exhausted or no_key:
                db.session.commit()

            if not to_redrive:
                return

            # Cross-worker single-flight: never overlap with a manual
            # outage-recovery run (two concurrent backfills = double submit).
            from routes.email import (
                _acquire_recovery_marker, _release_recovery_marker)
            if not _acquire_recovery_marker():
                app.logger.info(
                    "Email auto-recovery: an outage-recovery run is already in "
                    "progress; deferring this cycle.")
                return

            try:
                earliest = min(
                    (r.received_at or r.created_at) for r in to_redrive)
                stamp = datetime.utcnow().isoformat()
                # Remember each row's original message_id BEFORE clearing it, so
                # after the backfill we can tell whether it was actually
                # re-fetched + reprocessed (a new row now holds that message_id)
                # and, if not, put it back so a real applicant is never stranded.
                originals = [(r, r.message_id) for r in to_redrive]
                redrive_ids = []
                for record in to_redrive:
                    redrive_ids.append(record.id)
                    # Preserve the original Message-ID as the STABLE poison
                    # identity before clearing message_id. Every crashing copy of
                    # this email is re-fetched under the same Message-ID, so the
                    # poison cap counts superseded breadcrumbs by this field and
                    # terminates the retry loop even across successor-row churn.
                    record.recovery_message_id = record.message_id
                    record.message_id = None
                    record.status = 'recovery_superseded'
                    record.vetting_retry_count = (
                        record.vetting_retry_count or 0) + 1
                    record.processing_notes = (
                        (record.processing_notes or '')
                        + f" | Auto-recovery {stamp}Z: stuck >{timeout_min}m; "
                          f"message_id cleared, superseded by mailbox-backfill "
                          f"re-drive."
                    )[:2000]
                db.session.commit()

                since_iso = (
                    (earliest - timedelta(minutes=1))
                    .replace(microsecond=0).isoformat() + 'Z')
                app.logger.warning(
                    f"📥 Email auto-recovery: re-driving {len(redrive_ids)} "
                    f"stuck records (ids={redrive_ids[:20]}) via mailbox "
                    f"backfill since {since_iso} (limit={backfill_limit}).")

                from tasks import run_mailbox_backfill
                try:
                    summary = run_mailbox_backfill(
                        app, since_iso, limit=backfill_limit)
                    app.logger.info(
                        f"📥 Email auto-recovery backfill complete: {summary}")
                except Exception as bf_err:
                    # Coerce a raised exception into a failure-signal summary so
                    # the reconciliation block below always runs. Without this,
                    # an exception here jumps directly to the outer except and
                    # rows committed as recovery_superseded with message_id=NULL
                    # can be permanently stranded (no reconciliation = no restore).
                    summary = {'error': str(bf_err), 'fetched': 0}
                    app.logger.error(
                        f"📥 Email auto-recovery: backfill raised {bf_err!r}; "
                        f"reconciling to restore stranded rows.")

                # ── Reconcile: never strand a real applicant ─────────────────
                # The backfill can error, fetch nothing, fail per-message, or hit
                # its limit before reaching every reset row. For each reset row we
                # look for a SUCCESSOR (a row now holding its original message_id):
                #   • successor that actually reached Bullhorn (bullhorn_candidate_id
                #     set) OR was intentionally collapsed (duplicate/ignored/
                #     skipped-submitted) → genuinely handled; keep the superseded
                #     breadcrumb.
                #   • successor that exists but did NOT reach Bullhorn (e.g. the
                #     re-fetch failed mid-pipeline) → it holds the message_id and is
                #     not 'processing', so the dedupe would block any future retry.
                #     Re-arm it to 'processing' so the reaper re-drives it next
                #     cycle (bounded by the poison cap).
                #   • NO successor AND a backfill failure signal → restore the
                #     original row to 'processing' with its message_id (conflict-
                #     safe: no row holds that key) so the next cycle retries.
                #   • NO successor after a clean, complete backfill → the same email
                #     was reprocessed under a different transport's message_id
                #     (already handled); leave the breadcrumb superseded.
                # This is what makes auto-recovery safe to leave running unattended,
                # unlike the human-watched manual recovery.
                if not isinstance(summary, dict):
                    summary = {}
                fetched = summary.get('fetched', 0) or 0
                failure_signal = (
                    bool(summary.get('error'))
                    or fetched == 0
                    or (summary.get('failed', 0) or 0) > 0
                    or fetched >= backfill_limit
                )
                handled_statuses = (
                    'duplicate', 'ignored', 'recovery_skipped_submitted')
                requeued = []          # originals restored to 'processing'
                rearmed = []           # failed successors re-armed to 'processing'
                for record, orig_mid in originals:
                    if not orig_mid:
                        continue  # no key to reconcile on; leave superseded
                    successor = ParsedEmail.query.filter(
                        ParsedEmail.message_id == orig_mid,
                        ParsedEmail.id != record.id,
                    ).first()
                    if successor is not None:
                        succeeded = (
                            successor.bullhorn_candidate_id is not None
                            or successor.status in handled_statuses)
                        if succeeded:
                            continue  # genuinely handled → keep superseded
                        # Successor exists but never reached Bullhorn: it owns the
                        # message_id, so the original cannot be restored. Re-arm the
                        # successor for the reaper to re-drive next cycle.
                        if successor.status != 'processing':
                            successor.status = 'processing'
                            successor.processed_at = None
                            rearmed.append(successor.id)
                        continue
                    if failure_signal:
                        record.status = 'processing'
                        record.message_id = orig_mid
                        record.processed_at = None
                        requeued.append(record.id)
                if requeued or rearmed:
                    db.session.commit()
                    app.logger.error(
                        f"📥 Email auto-recovery: backfill did NOT fully "
                        f"reprocess — restored {len(requeued)} original row(s) "
                        f"(ids={requeued[:20]}) and re-armed {len(rearmed)} failed "
                        f"successor(s) (ids={rearmed[:20]}) to 'processing' for "
                        f"retry next cycle. summary={summary}")
            finally:
                _release_recovery_marker(app)

        except Exception as e:
            app.logger.error(f"Email parsing timeout cleanup error: {str(e)}")
            db.session.rollback()


def run_data_retention_cleanup():
    """
    Clean up old data to keep the database optimized.
    Retention periods:
    - Log monitoring runs/issues: 30 days
    - Vetting health checks: 7 days
    - Environment alerts: 30 days
    """
    from app import app
    from extensions import db
    with app.app_context():
        try:
            from models import LogMonitoringRun, LogMonitoringIssue, VettingHealthCheck, EnvironmentAlert
            from sqlalchemy import and_

            total_deleted = 0

            log_retention_date = datetime.utcnow() - timedelta(days=30)
            old_runs = LogMonitoringRun.query.filter(
                LogMonitoringRun.run_time < log_retention_date
            ).all()

            if old_runs:
                for run in old_runs:
                    db.session.delete(run)  # Cascade deletes issues
                total_deleted += len(old_runs)
                app.logger.info(f"Data cleanup: Deleted {len(old_runs)} log monitoring runs older than 30 days")

            health_retention_date = datetime.utcnow() - timedelta(days=7)
            old_health_checks = VettingHealthCheck.query.filter(
                VettingHealthCheck.check_time < health_retention_date
            ).delete(synchronize_session=False)

            if old_health_checks:
                total_deleted += old_health_checks
                app.logger.info(f"Data cleanup: Deleted {old_health_checks} vetting health checks older than 7 days")

            alert_retention_date = datetime.utcnow() - timedelta(days=30)
            old_alerts = EnvironmentAlert.query.filter(
                EnvironmentAlert.sent_at < alert_retention_date
            ).delete(synchronize_session=False)

            if old_alerts:
                total_deleted += old_alerts
                app.logger.info(f"Data cleanup: Deleted {old_alerts} environment alerts older than 30 days")

            from models import PasswordResetToken
            expired_tokens = PasswordResetToken.query.filter(
                (PasswordResetToken.expires_at < datetime.utcnow()) |
                (PasswordResetToken.used == True)
            ).delete(synchronize_session=False)
            if expired_tokens:
                total_deleted += expired_tokens
                app.logger.info(f"Data cleanup: Deleted {expired_tokens} expired/used password reset tokens")

            if total_deleted > 0:
                db.session.commit()
                app.logger.info(f"Data retention cleanup complete: {total_deleted} total records cleaned")

        except Exception as e:
            app.logger.error(f"Data retention cleanup error: {str(e)}")
            db.session.rollback()
