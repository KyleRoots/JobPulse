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
    """Auto-fail stuck email parsing records after 10 minutes"""
    from app import app
    from extensions import db
    with app.app_context():
        try:
            from models import ParsedEmail

            timeout_threshold = datetime.utcnow() - timedelta(minutes=10)

            stuck_records = ParsedEmail.query.filter(
                ParsedEmail.status == 'processing',
                ParsedEmail.created_at < timeout_threshold
            ).all()

            if stuck_records:
                for record in stuck_records:
                    record.status = 'failed'
                    record.processing_notes = f"Auto-failed: Processing timeout after 10 minutes (started at {record.created_at})"
                    record.processed_at = datetime.utcnow()
                    app.logger.warning(f"Auto-failed stuck email parsing record ID {record.id} (candidate: {record.candidate_name or 'Unknown'})")

                db.session.commit()
                app.logger.info(f"Email parsing cleanup: Auto-failed {len(stuck_records)} stuck records")

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
