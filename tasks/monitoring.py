import os
import logging
import traceback
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def check_monitor_health():
    """Lightweight health check for manual workflow - job counting focus"""
    from app import app
    from extensions import db
    with app.app_context():
        try:
            from models import BullhornMonitor
            app.logger.info("Starting periodic health check for manual workflow...")

            active_monitors = BullhornMonitor.query.filter_by(is_active=True).count()
            app.logger.info(f"Manual workflow health check: {active_monitors} active monitors for job counting")

            recent_activity = BullhornMonitor.query.filter(
                BullhornMonitor.last_check > datetime.utcnow() - timedelta(hours=6)
            ).count()

            if recent_activity > 0:
                app.logger.info(f"Job counting active: {recent_activity} monitors updated in last 6 hours")
            else:
                app.logger.warning(f"Job counting may be stale: no monitor updates in 6+ hours (manual workflow)")

            return

            if False:  # Disabled - functionality integrated
                if result['overdue_count'] > 0:
                    app.logger.warning(f"Health check found {result['overdue_count']} overdue monitors")
                    if result['notification_sent']:
                        app.logger.info("Overdue monitor notification sent successfully")
                else:
                    app.logger.info("All monitors are healthy")

                if result['corrected_count'] > 0:
                    app.logger.info(f"Auto-corrected {result['corrected_count']} monitors")
            else:
                app.logger.error(f"Health check failed: {result.get('error', 'Unknown error')}")

        except Exception as e:
            app.logger.error(f"Monitor health check error: {str(e)}")
            app.logger.error(traceback.format_exc())


def check_environment_status():
    """Check production environment status and send alerts on status changes"""
    from app import app
    from extensions import db
    import requests
    with app.app_context():
        try:
            from models import EnvironmentStatus, EnvironmentAlert

            env_status = EnvironmentStatus.query.filter_by(environment_name='production').first()
            if not env_status:
                env_status = EnvironmentStatus(
                    environment_name='production',
                    environment_url='https://app.scoutgenius.ai',
                    current_status='unknown',
                    alert_email='kroots@myticas.com'
                )
                db.session.add(env_status)
                db.session.commit()
                app.logger.info("Created initial environment status record for production monitoring")

            previous_status = env_status.current_status
            current_time = datetime.utcnow()

            try:
                app.logger.info(f"Checking environment status for: {env_status.environment_url}")
                response = requests.get(
                    env_status.environment_url + '/health',
                    timeout=env_status.timeout_seconds,
                    headers={'User-Agent': 'ScoutGenius-Environment-Monitor/1.0'}
                )

                if response.status_code == 200:
                    new_status = 'up'
                    env_status.consecutive_failures = 0
                    app.logger.info(f"Environment check successful: {response.status_code}")
                else:
                    new_status = 'down'
                    env_status.consecutive_failures += 1
                    app.logger.warning(f"Environment check failed: HTTP {response.status_code}")

            except requests.exceptions.Timeout:
                new_status = 'down'
                env_status.consecutive_failures += 1
                error_msg = f"Request timeout after {env_status.timeout_seconds} seconds"
                app.logger.error(f"Environment check failed: {error_msg}")

            except requests.exceptions.ConnectionError:
                new_status = 'down'
                env_status.consecutive_failures += 1
                error_msg = "Connection error - server may be down"
                app.logger.error(f"Environment check failed: {error_msg}")

            except Exception as e:
                new_status = 'down'
                env_status.consecutive_failures += 1
                error_msg = f"Unexpected error: {str(e)}"
                app.logger.error(f"Environment check failed: {error_msg}")

            env_status.current_status = new_status
            env_status.last_check_time = current_time

            status_changed = (previous_status != new_status and previous_status != 'unknown')

            if status_changed:
                env_status.last_status_change = current_time
                app.logger.info(f"Environment status changed: {previous_status} -> {new_status}")

                downtime_minutes = None
                if new_status == 'up' and previous_status == 'down':
                    if env_status.last_status_change:
                        last_down_change = EnvironmentAlert.query.filter_by(
                            environment_status_id=env_status.id,
                            alert_type='down'
                        ).order_by(EnvironmentAlert.sent_at.desc()).first()

                        if last_down_change:
                            downtime_delta = current_time - last_down_change.sent_at
                            downtime_minutes = round(downtime_delta.total_seconds() / 60, 2)
                            env_status.total_downtime_minutes += downtime_minutes

                alert_sent = False
                if ((new_status == 'down' and env_status.alert_on_down) or
                    (new_status == 'up' and env_status.alert_on_recovery)):

                    try:
                        alert_sent = send_environment_alert(env_status, new_status, previous_status, downtime_minutes)
                    except Exception as alert_error:
                        app.logger.error(f"Failed to send environment alert: {str(alert_error)}")

            db.session.commit()

            if new_status == 'up':
                app.logger.info(f"Environment monitoring: {env_status.environment_name} is UP (consecutive failures: {env_status.consecutive_failures})")
            else:
                app.logger.warning(f"Environment monitoring: {env_status.environment_name} is DOWN (consecutive failures: {env_status.consecutive_failures})")

        except Exception as e:
            app.logger.error(f"Environment status check error: {str(e)}")
            db.session.rollback()
            app.logger.error(traceback.format_exc())


def send_environment_alert(env_status, new_status, previous_status, downtime_minutes=None):
    """Send email alert for environment status change"""
    from app import app
    from extensions import db
    try:
        from models import EnvironmentAlert
        from timezone_utils import format_eastern_time
        from email_service import EmailService

        current_time_eastern = format_eastern_time(datetime.utcnow())

        if new_status == 'down':
            subject = f"ALERT: {env_status.environment_name.title()} Environment is DOWN"
            message = f"""
Environment Monitoring Alert

Environment: {env_status.environment_name.title()}
URL: {env_status.environment_url}
Status: DOWN
Previous Status: {previous_status.title()}
Time: {current_time_eastern}
Consecutive Failures: {env_status.consecutive_failures}

Troubleshooting Steps:
1. Check if the production server is responding
2. Verify DNS resolution for the domain
3. Check for any recent deployments or changes
4. Review server logs for errors
5. Check SSL certificate validity
6. Verify CDN/load balancer status

You will receive another notification when the environment is back online.

This is an automated message from Scout Genius Environment Monitoring.
"""
        else:  # status == 'up'
            subject = f"RECOVERY: {env_status.environment_name.title()} Environment is UP"
            downtime_text = f"Downtime: {downtime_minutes} minutes" if downtime_minutes else "Downtime: Unknown"
            message = f"""
Environment Recovery Notification

Environment: {env_status.environment_name.title()}
URL: {env_status.environment_url}
Status: UP
Previous Status: {previous_status.title()}
Recovery Time: {current_time_eastern}
{downtime_text}

The environment is now accessible and functioning normally.
Current uptime: {env_status.uptime_percentage}%

This is an automated message from Scout Genius Environment Monitoring.
"""

        email_service = EmailService()

        success = email_service.send_notification_email(
            to_email=env_status.alert_email,
            subject=subject,
            message=message,
            notification_type=f'environment_{new_status}'
        )

        alert = EnvironmentAlert(
            environment_status_id=env_status.id,
            alert_type=new_status,
            alert_message=message,
            recipient_email=env_status.alert_email,
            delivery_status='sent' if success else 'failed',
            downtime_duration=downtime_minutes,
            error_details=None if success else "Email sending failed"
        )
        db.session.add(alert)

        if success:
            app.logger.info(f"Environment alert sent successfully: {new_status} notification to {env_status.alert_email}")
        else:
            app.logger.error(f"Failed to send environment alert: {new_status} notification to {env_status.alert_email}")

        return success

    except Exception as e:
        app.logger.error(f"Error sending environment alert: {str(e)}")
        return False


def run_vetting_health_check():
    """Run health checks on the vetting system components"""
    from app import app, scheduler
    from extensions import db
    with app.app_context():
        try:
            from models import VettingHealthCheck, VettingConfig, CandidateVettingLog
            from sqlalchemy import func

            bullhorn_status = True
            bullhorn_error = None
            openai_status = True
            openai_error = None
            database_status = True
            database_error = None
            scheduler_status = True
            scheduler_error = None

            try:
                from bullhorn_service import BullhornService
                bh = BullhornService()
                if not bh.authenticate():
                    bullhorn_status = False
                    bullhorn_error = "Failed to authenticate with Bullhorn"
            except Exception as e:
                bullhorn_status = False
                bullhorn_error = str(e)[:500]

            try:
                import openai
                client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
                if not os.environ.get('OPENAI_API_KEY'):
                    openai_status = False
                    openai_error = "OPENAI_API_KEY not configured"
            except Exception as e:
                openai_status = False
                openai_error = str(e)[:500]

            try:
                db.session.execute(db.text("SELECT 1"))
            except Exception as e:
                database_status = False
                database_error = str(e)[:500]

            try:
                if not scheduler.running:
                    scheduler_status = False
                    scheduler_error = "Scheduler is not running"
            except Exception as e:
                scheduler_status = False
                scheduler_error = str(e)[:500]

            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

            candidates_processed_today = CandidateVettingLog.query.filter(
                CandidateVettingLog.status == 'completed',
                CandidateVettingLog.created_at >= today_start
            ).count()

            candidates_pending = CandidateVettingLog.query.filter(
                CandidateVettingLog.status.in_(['pending', 'processing'])
            ).count()

            emails_sent_today = db.session.query(func.sum(CandidateVettingLog.notification_count)).filter(
                CandidateVettingLog.created_at >= today_start
            ).scalar() or 0

            last_success = CandidateVettingLog.query.filter_by(status='completed').order_by(
                CandidateVettingLog.analyzed_at.desc()
            ).first()
            last_successful_cycle = last_success.analyzed_at if last_success else None

            is_healthy = bullhorn_status and openai_status and database_status and scheduler_status

            health_check = VettingHealthCheck(
                check_time=datetime.utcnow(),
                bullhorn_status=bullhorn_status,
                openai_status=openai_status,
                database_status=database_status,
                scheduler_status=scheduler_status,
                bullhorn_error=bullhorn_error,
                openai_error=openai_error,
                database_error=database_error,
                scheduler_error=scheduler_error,
                is_healthy=is_healthy,
                candidates_processed_today=candidates_processed_today,
                candidates_pending=candidates_pending,
                emails_sent_today=emails_sent_today,
                last_successful_cycle=last_successful_cycle,
                alert_sent=False
            )
            db.session.add(health_check)
            db.session.commit()

            if not is_healthy:
                send_vetting_health_alert(health_check)

            cleanup_threshold = datetime.utcnow() - timedelta(days=7)
            VettingHealthCheck.query.filter(VettingHealthCheck.check_time < cleanup_threshold).delete()
            db.session.commit()

            app.logger.info(f"Vetting health check: {'Healthy' if is_healthy else 'Issues detected'}")

        except Exception as e:
            app.logger.error(f"Vetting health check error: {str(e)}")


def send_vetting_health_alert(health_check):
    """
    Send email alert for vetting system health issues.

    Threshold-based suppression:
    - Only alerts if the same component has failed in 3 consecutive checks (persistent issue).
    - Single transient failures that self-heal are suppressed.

    Severity levels:
    - Critical: Component down AND 0 candidates processed -> immediate alert.
    - Warning: Component fails but candidates still processing -> suppressed.
    """
    from app import app
    from extensions import db
    try:
        from models import VettingConfig, VettingHealthCheck
        import sendgrid
        from sendgrid.helpers.mail import Mail

        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_alert = VettingHealthCheck.query.filter(
            VettingHealthCheck.alert_sent == True,
            VettingHealthCheck.alert_sent_at >= one_hour_ago
        ).first()

        if recent_alert:
            app.logger.info("Skipping alert - already sent within last hour")
            return

        thirty_min_ago = datetime.utcnow() - timedelta(minutes=30)
        recent_checks = VettingHealthCheck.query \
            .filter(VettingHealthCheck.check_time >= thirty_min_ago) \
            .order_by(VettingHealthCheck.check_time.desc()) \
            .limit(3).all()

        bh_fails = sum(1 for c in recent_checks if not c.bullhorn_status)
        openai_fails = sum(1 for c in recent_checks if not c.openai_status)
        db_fails = sum(1 for c in recent_checks if not c.database_status)
        sched_fails = sum(1 for c in recent_checks if not c.scheduler_status)

        candidates_today = health_check.candidates_processed_today or 0

        is_critical = (
            candidates_today == 0 and
            (bh_fails >= 3 or openai_fails >= 3 or db_fails >= 3 or sched_fails >= 3)
        )

        is_warning = not is_critical and (
            bh_fails >= 3 or openai_fails >= 3 or db_fails >= 3 or sched_fails >= 3
        )

        is_transient = not is_critical and not is_warning

        if is_transient:
            app.logger.info(
                f"Suppressing transient alert — failures not persistent "
                f"(BH:{bh_fails}/3, OpenAI:{openai_fails}/3, DB:{db_fails}/3, Sched:{sched_fails}/3). "
                f"{candidates_today} candidates processed today."
            )
            return

        if is_warning:
            app.logger.info(
                f"Suppressing warning-level alert — component down but {candidates_today} "
                f"candidates processed today. System is still functional."
            )
            return

        health_alert_email = VettingConfig.get_value('health_alert_email', '')
        if not health_alert_email:
            app.logger.info("Health alert email not configured - skipping alert")
            return

        errors = []
        if not health_check.bullhorn_status:
            errors.append(f"Bullhorn: {health_check.bullhorn_error or 'Connection failed'}")
        if not health_check.openai_status:
            errors.append(f"OpenAI: {health_check.openai_error or 'API unavailable'}")
        if not health_check.database_status:
            errors.append(f"Database: {health_check.database_error or 'Connection failed'}")
        if not health_check.scheduler_status:
            errors.append(f"Scheduler: {health_check.scheduler_error or 'Not running'}")

        severity_label = "CRITICAL" if is_critical else "WARNING"

        if candidates_today > 0:
            context_line = f'<div style="background: #d4edda; border-left: 4px solid #28a745; padding: 10px; margin: 10px 0;">Despite this error, <strong>{candidates_today}</strong> candidates were successfully processed today.</div>'
        else:
            context_line = '<div style="background: #f8d7da; border-left: 4px solid #dc3545; padding: 10px; margin: 10px 0;">No candidates have been processed today — vetting may be completely stopped.</div>'

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #dc3545;">{severity_label} Scout Screening System Alert</h2>
            <p>The AI Candidate Vetting system has detected <strong>persistent</strong> issues requiring attention:</p>

            <div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 15px 0;">
                <strong>Issues Detected (3+ consecutive failures):</strong><br>
                {"<br>".join([f"• {e}" for e in errors])}
            </div>

            {context_line}

            <p><strong>System Stats:</strong></p>
            <ul>
                <li>Candidates Processed Today: {candidates_today}</li>
                <li>Candidates Pending: {health_check.candidates_pending}</li>
                <li>Emails Sent Today: {health_check.emails_sent_today}</li>
                <li>Consecutive Failures — BH: {bh_fails}, OpenAI: {openai_fails}, DB: {db_fails}, Scheduler: {sched_fails}</li>
            </ul>

            <p style="color: #666; font-size: 12px;">
                This is an automated alert from Scout Screening. Only sent for persistent critical issues (3+ consecutive failures with 0 candidates processed).
                Check the <a href="https://app.scoutgenius.ai/screening">Vetting Dashboard</a> for more details.
            </p>
        </body>
        </html>
        """

        message = Mail(
            from_email='noreply@myticas.com',
            to_emails=health_alert_email,
            subject=f'{severity_label} Scout Screening System Alert - Persistent Issues',
            html_content=html_content
        )

        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
        response = sg.send(message)

        if response.status_code in [200, 202]:
            health_check.alert_sent = True
            health_check.alert_sent_at = datetime.utcnow()
            db.session.commit()
            app.logger.info(f"CRITICAL health alert sent to {health_alert_email}")
        else:
            app.logger.warning(f"Health alert failed: {response.status_code}")

    except Exception as e:
        app.logger.error(f"Failed to send health alert: {str(e)}")
