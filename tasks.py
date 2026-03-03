import os
import logging
import traceback
import json
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
            app.logger.info(f"✅ Manual workflow health check: {active_monitors} active monitors for job counting")
            
            recent_activity = BullhornMonitor.query.filter(
                BullhornMonitor.last_check > datetime.utcnow() - timedelta(hours=6)
            ).count()
            
            if recent_activity > 0:
                app.logger.info(f"✅ Job counting active: {recent_activity} monitors updated in last 6 hours")
            else:
                app.logger.warning(f"⚠️ Job counting may be stale: no monitor updates in 6+ hours (manual workflow)")
            
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
                    headers={'User-Agent': 'JobPulse-Environment-Monitor/1.0'}
                )
                
                if response.status_code == 200:
                    new_status = 'up'
                    env_status.consecutive_failures = 0
                    app.logger.info(f"✅ Environment check successful: {response.status_code}")
                else:
                    new_status = 'down'
                    env_status.consecutive_failures += 1
                    app.logger.warning(f"❌ Environment check failed: HTTP {response.status_code}")
                    
            except requests.exceptions.Timeout:
                new_status = 'down'
                env_status.consecutive_failures += 1
                error_msg = f"Request timeout after {env_status.timeout_seconds} seconds"
                app.logger.error(f"❌ Environment check failed: {error_msg}")
                
            except requests.exceptions.ConnectionError:
                new_status = 'down'
                env_status.consecutive_failures += 1
                error_msg = "Connection error - server may be down"
                app.logger.error(f"❌ Environment check failed: {error_msg}")
                
            except Exception as e:
                new_status = 'down'
                env_status.consecutive_failures += 1
                error_msg = f"Unexpected error: {str(e)}"
                app.logger.error(f"❌ Environment check failed: {error_msg}")
            
            env_status.current_status = new_status
            env_status.last_check_time = current_time
            
            status_changed = (previous_status != new_status and previous_status != 'unknown')
            
            if status_changed:
                env_status.last_status_change = current_time
                app.logger.info(f"🔄 Environment status changed: {previous_status} → {new_status}")
                
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
                app.logger.info(f"✅ Environment monitoring: {env_status.environment_name} is UP (consecutive failures: {env_status.consecutive_failures})")
            else:
                app.logger.warning(f"❌ Environment monitoring: {env_status.environment_name} is DOWN (consecutive failures: {env_status.consecutive_failures})")
            
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
            subject = f"🚨 ALERT: {env_status.environment_name.title()} Environment is DOWN"
            message = f"""
Environment Monitoring Alert

Environment: {env_status.environment_name.title()}
URL: {env_status.environment_url}
Status: DOWN ❌
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
            subject = f"✅ RECOVERY: {env_status.environment_name.title()} Environment is UP"
            downtime_text = f"Downtime: {downtime_minutes} minutes" if downtime_minutes else "Downtime: Unknown"
            message = f"""
Environment Recovery Notification

Environment: {env_status.environment_name.title()}
URL: {env_status.environment_url}
Status: UP ✅
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
            app.logger.info(f"📧 Environment alert sent successfully: {new_status} notification to {env_status.alert_email}")
        else:
            app.logger.error(f"📧 Failed to send environment alert: {new_status} notification to {env_status.alert_email}")
        
        return success
        
    except Exception as e:
        app.logger.error(f"Error sending environment alert: {str(e)}")
        return False


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
                app.logger.info(f"🗑️ Activity cleanup: Removed {deleted_count} activity records older than 15 days")
                
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
                app.logger.info("🗑️ Activity cleanup: No old activities to remove")
                
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
            app.logger.info(f"📊 Log monitoring cycle complete: {result['logs_analyzed']} logs, "
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
                    app.logger.warning(f"⏰ Auto-failed stuck email parsing record ID {record.id} (candidate: {record.candidate_name or 'Unknown'})")
                
                db.session.commit()
                app.logger.info(f"⏰ Email parsing cleanup: Auto-failed {len(stuck_records)} stuck records")
            
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
                app.logger.info(f"🧹 Data cleanup: Deleted {len(old_runs)} log monitoring runs older than 30 days")
            
            health_retention_date = datetime.utcnow() - timedelta(days=7)
            old_health_checks = VettingHealthCheck.query.filter(
                VettingHealthCheck.check_time < health_retention_date
            ).delete(synchronize_session=False)
            
            if old_health_checks:
                total_deleted += old_health_checks
                app.logger.info(f"🧹 Data cleanup: Deleted {old_health_checks} vetting health checks older than 7 days")
            
            alert_retention_date = datetime.utcnow() - timedelta(days=30)
            old_alerts = EnvironmentAlert.query.filter(
                EnvironmentAlert.sent_at < alert_retention_date
            ).delete(synchronize_session=False)
            
            if old_alerts:
                total_deleted += old_alerts
                app.logger.info(f"🧹 Data cleanup: Deleted {old_alerts} environment alerts older than 30 days")

            from models import PasswordResetToken
            expired_tokens = PasswordResetToken.query.filter(
                (PasswordResetToken.expires_at < datetime.utcnow()) |
                (PasswordResetToken.used == True)
            ).delete(synchronize_session=False)
            if expired_tokens:
                total_deleted += expired_tokens
                app.logger.info(f"🧹 Data cleanup: Deleted {expired_tokens} expired/used password reset tokens")

            if total_deleted > 0:
                db.session.commit()
                app.logger.info(f"🧹 Data retention cleanup complete: {total_deleted} total records cleaned")
            
        except Exception as e:
            app.logger.error(f"Data retention cleanup error: {str(e)}")
            db.session.rollback()


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
            
            app.logger.info(f"🩺 Vetting health check: {'✅ Healthy' if is_healthy else '❌ Issues detected'}")
            
        except Exception as e:
            app.logger.error(f"Vetting health check error: {str(e)}")


def send_vetting_health_alert(health_check):
    """
    Send email alert for vetting system health issues.
    
    Threshold-based suppression:
    - Only alerts if the same component has failed in 3 consecutive checks (persistent issue).
    - Single transient failures that self-heal are suppressed.
    
    Severity levels:
    - Critical: Component down AND 0 candidates processed → immediate alert.
    - Warning: Component fails but candidates still processing → suppressed.
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
            app.logger.info("🩺 Skipping alert - already sent within last hour")
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
                f"🩺 Suppressing transient alert — failures not persistent "
                f"(BH:{bh_fails}/3, OpenAI:{openai_fails}/3, DB:{db_fails}/3, Sched:{sched_fails}/3). "
                f"{candidates_today} candidates processed today."
            )
            return
        
        if is_warning:
            app.logger.info(
                f"🩺 Suppressing warning-level alert — component down but {candidates_today} "
                f"candidates processed today. System is still functional."
            )
            return
        
        health_alert_email = VettingConfig.get_value('health_alert_email', '')
        if not health_alert_email:
            app.logger.info("🩺 Health alert email not configured - skipping alert")
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
        
        severity_label = "🔴 CRITICAL" if is_critical else "🟡 WARNING"
        
        if candidates_today > 0:
            context_line = f'<div style="background: #d4edda; border-left: 4px solid #28a745; padding: 10px; margin: 10px 0;">✅ Despite this error, <strong>{candidates_today}</strong> candidates were successfully processed today.</div>'
        else:
            context_line = '<div style="background: #f8d7da; border-left: 4px solid #dc3545; padding: 10px; margin: 10px 0;">⛔ No candidates have been processed today — vetting may be completely stopped.</div>'
        
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
            app.logger.info(f"🩺 CRITICAL health alert sent to {health_alert_email}")
        else:
            app.logger.warning(f"🩺 Health alert failed: {response.status_code}")
            
    except Exception as e:
        app.logger.error(f"Failed to send health alert: {str(e)}")


def run_candidate_vetting_cycle():
    """Run the AI-powered candidate vetting cycle to analyze new applicants"""
    from app import app
    with app.app_context():
        try:
            from candidate_vetting_service import CandidateVettingService
            from models import VettingConfig
            
            config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
            if not config or config.setting_value.lower() != 'true':
                return  # Silently skip if disabled
            
            vetting_service = CandidateVettingService()
            summary = vetting_service.run_vetting_cycle()
            
            if summary.get('status') != 'disabled':
                app.logger.info(f"🎯 Candidate vetting cycle completed: {summary.get('candidates_processed', 0)} processed, "
                              f"{summary.get('candidates_qualified', 0)} qualified, {summary.get('notifications_sent', 0)} notifications")
                
        except Exception as e:
            app.logger.error(f"Candidate vetting cycle error: {str(e)}")


def reference_number_refresh():
    """Automatic refresh of all reference numbers every 120 hours while preserving all other XML data"""
    from app import app
    from extensions import db
    with app.app_context():
        try:
            from datetime import date
            from models import RefreshLog, GlobalSettings, BullhornActivity
            today = date.today()
            
            existing_refresh = RefreshLog.query.filter_by(refresh_date=today).first()
            if existing_refresh:
                app.logger.info(f"📝 Reference refresh already completed today at {existing_refresh.refresh_time}")
                return
            
            app.logger.info("🔄 Starting 120-hour reference number refresh...")
            
            from simplified_xml_generator import SimplifiedXMLGenerator
            
            generator = SimplifiedXMLGenerator(db=db)
            
            xml_content, stats = generator.generate_fresh_xml()
            app.logger.info(f"📊 Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
            
            from lightweight_reference_refresh import lightweight_refresh_references_from_content
            
            result = lightweight_refresh_references_from_content(xml_content)
            
            if result['success']:
                app.logger.info(f"✅ Reference refresh complete: {result['jobs_updated']} jobs updated in {result['time_seconds']:.2f} seconds")
                
                try:
                    refresh_log = RefreshLog(
                        refresh_date=today,
                        refresh_time=datetime.utcnow(),
                        jobs_updated=result['jobs_updated'],
                        processing_time=result['time_seconds'],
                        email_sent=False
                    )
                    db.session.add(refresh_log)
                    db.session.commit()
                    app.logger.info("📝 Refresh completion logged to database")
                except Exception as log_error:
                    app.logger.error(f"Failed to log refresh completion: {str(log_error)}")
                    db.session.rollback()
                
                from lightweight_reference_refresh import save_references_to_database
                db_save_success = save_references_to_database(result['xml_content'])
                
                if not db_save_success:
                    error_msg = "Database-first architecture requires successful DB save - 120-hour refresh FAILED"
                    app.logger.critical(f"❌ CRITICAL: {error_msg}")
                    raise Exception(error_msg)
                
                app.logger.info("💾 DATABASE-FIRST: Reference numbers successfully saved to database")
                app.logger.info("✅ Reference refresh complete: Reference numbers updated in database (30-minute upload cycle will use these values)")
                
                try:
                    from email_service import EmailService
                    
                    email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                    email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                    
                    if (email_enabled and email_enabled.setting_value == 'true' and 
                        email_setting and email_setting.setting_value):
                        email_service = EmailService()
                        
                        refresh_details = {
                            'execution_time': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                            'processing_time': result['time_seconds'],
                            'jobs_updated': result['jobs_updated'],
                            'database_saved': db_save_success,
                            'note': 'Reference numbers saved to database - 30-minute upload cycle will use these values'
                        }
                        
                        email_sent = email_service.send_reference_number_refresh_notification(
                            to_email=email_setting.setting_value,
                            schedule_name="120-Hour Reference Number Refresh",
                            total_jobs=result['jobs_updated'],
                            refresh_details=refresh_details,
                            status="success"
                        )
                        
                        if email_sent:
                            app.logger.info(f"📧 Refresh confirmation email sent to {email_setting.setting_value}")
                            refresh_log_var = locals().get('refresh_log')
                            if refresh_log_var:
                                refresh_log_var.email_sent = True
                                db.session.commit()
                        else:
                            app.logger.warning("📧 Failed to send refresh confirmation email")
                    else:
                        app.logger.warning("📧 No notification email configured - skipping confirmation email")
                        
                except Exception as email_error:
                    app.logger.error(f"📧 Failed to send refresh confirmation email: {str(email_error)}")
                
                try:
                    activity = BullhornActivity(
                        monitor_id=None,
                        activity_type='reference_refresh',
                        details=f'Daily automatic refresh: {result["jobs_updated"]} reference numbers updated',
                        notification_sent=True,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(activity)
                    db.session.commit()
                except Exception as log_error:
                    app.logger.warning(f"Could not log refresh activity: {str(log_error)}")
                    
            else:
                app.logger.error(f"❌ Reference refresh failed: {result.get('error', 'Unknown error')}")
                
                try:
                    from email_service import EmailService
                    
                    email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                    email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                    
                    if (email_enabled and email_enabled.setting_value == 'true' and 
                        email_setting and email_setting.setting_value):
                        email_service = EmailService()
                        
                        refresh_details = {
                            'execution_time': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                            'error': result.get('error', 'Unknown error')
                        }
                        
                        email_sent = email_service.send_reference_number_refresh_notification(
                            to_email=email_setting.setting_value,
                            schedule_name="120-Hour Reference Number Refresh",
                            total_jobs=0,
                            refresh_details=refresh_details,
                            status="error",
                            error_message=result.get('error', 'Unknown error')
                        )
                        
                        if email_sent:
                            app.logger.info(f"📧 Refresh failure alert sent to {email_setting.setting_value}")
                        else:
                            app.logger.warning("📧 Failed to send refresh failure alert")
                        
                except Exception as email_error:
                    app.logger.error(f"📧 Failed to send refresh failure alert: {str(email_error)}")
                
        except Exception as e:
            app.logger.error(f"Reference refresh error: {str(e)}")


def automated_upload():
    """Automatically upload fresh XML every 30 minutes if automation is enabled"""
    print("📤 AUTOMATED UPLOAD: Function invoked by scheduler", flush=True)
    from app import app
    from extensions import db
    with app.app_context():
        app.logger.info("📤 AUTOMATED UPLOAD: Function invoked")
        try:
            from models import GlobalSettings
            
            automation_setting = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
            if not (automation_setting and automation_setting.setting_value == 'true'):
                app.logger.info("📋 Automated uploads disabled in settings, skipping upload cycle")
                return
            
            sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
            if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
                app.logger.warning("📤 Automated upload skipped: SFTP not enabled")
                return
            
            app.logger.info("🚀 Starting automated 30-minute upload cycle...")
            app.logger.info("⚡ AUTOMATED UPLOAD FUNCTION EXECUTING - production priority enabled")
            
            from simplified_xml_generator import SimplifiedXMLGenerator
            generator = SimplifiedXMLGenerator(db=db)
            xml_content, stats = generator.generate_fresh_xml()
            
            app.logger.info(f"📊 Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
            app.logger.info("📍 CHECKPOINT 1: XML generation completed successfully")
            app.logger.info("💾 Reference numbers loaded from DATABASE (database-first approach)")
            
            upload_success = False
            upload_error_message = None
            
            try:
                import tempfile
                temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8')
                temp_file.write(xml_content)
                temp_file.close()
                
                sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
                sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
                sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
                sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
                sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
                
                if (sftp_hostname and sftp_hostname.setting_value and 
                    sftp_username and sftp_username.setting_value and 
                    sftp_password and sftp_password.setting_value):
                    
                    target_directory = sftp_directory.setting_value if sftp_directory else "/"
                    app.logger.info(f"📤 Uploading to configured directory: '{target_directory}'")
                    
                    from ftp_service import FTPService
                    ftp_service = FTPService(
                        hostname=sftp_hostname.setting_value,
                        username=sftp_username.setting_value,
                        password=sftp_password.setting_value,
                        target_directory=target_directory,
                        port=int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222,
                        use_sftp=True  # ALWAYS use SFTP for automated uploads (thread-safe)
                    )
                    app.logger.info(f"🔐 Using SFTP protocol for thread-safe uploads to {sftp_hostname.setting_value}:{ftp_service.port}")
                    app.logger.info(f"📂 Target directory: {target_directory}")
                    
                    current_env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'production').lower()
                    app.logger.info(f"🔍 Environment detection: APP_ENV={os.environ.get('APP_ENV')}, ENVIRONMENT={os.environ.get('ENVIRONMENT')}, using={current_env}")
                    
                    if current_env not in ['production', 'development']:
                        app.logger.error(f"❌ Invalid environment '{current_env}' - defaulting to development for safety")
                        current_env = 'development'
                    
                    if current_env == 'production':
                        production_filename = "myticas-job-feed-v2.xml"
                        app.logger.info("🎯 PRODUCTION ENVIRONMENT: Uploading to production file ONLY")
                        app.logger.info(f"📤 Uploading production XML as '{production_filename}'...")
                        app.logger.info(f"🔍 Local file path: {temp_file.name}")
                        app.logger.info(f"🎯 Remote filename: {production_filename}")
                        try:
                            app.logger.info("⚡ Calling FTP service for PRODUCTION upload...")
                            upload_result = ftp_service.upload_file(
                                local_file_path=temp_file.name,
                                remote_filename=production_filename
                            )
                            app.logger.info(f"📊 Production upload result: {upload_result}")
                            if upload_result:
                                app.logger.info("✅ Production file uploaded successfully")
                            else:
                                app.logger.error("❌ Production file upload failed")
                        except Exception as prod_error:
                            app.logger.error(f"❌ Production file upload error: {str(prod_error)}")
                            upload_result = False
                    else:
                        development_filename = "myticas-job-feed-v2-dev.xml"
                        app.logger.info("🧪 DEVELOPMENT ENVIRONMENT: Uploading to development file ONLY")
                        app.logger.info(f"📤 Uploading development XML as '{development_filename}'...")
                        app.logger.info(f"🔍 Local file path: {temp_file.name}")
                        app.logger.info(f"🎯 Remote filename: {development_filename}")
                        try:
                            upload_result = ftp_service.upload_file(
                                local_file_path=temp_file.name,
                                remote_filename=development_filename
                            )
                            if upload_result:
                                app.logger.info("✅ Development file uploaded successfully")
                            else:
                                app.logger.error("❌ Development file upload failed")
                        except Exception as dev_error:
                            app.logger.error(f"❌ Development file upload error: {str(dev_error)}")
                            upload_result = False
                    
                    app.logger.info(f"🔒 ENVIRONMENT ISOLATION: {current_env} → uploads ONLY to its designated file")
                    
                    if isinstance(upload_result, dict):
                        if upload_result['success']:
                            upload_success = True
                            app.logger.info(f"✅ Automated upload successful: {upload_result.get('message', 'File uploaded')}")
                        else:
                            upload_error_message = upload_result.get('error', 'Unknown upload error')
                            app.logger.error(f"❌ Automated upload failed: {upload_error_message}")
                    else:
                        if upload_result:
                            upload_success = True
                            app.logger.info("✅ Automated upload successful")
                        else:
                            upload_error_message = "Upload failed"
                            app.logger.error("❌ Automated upload failed")
                    
                    if upload_success:
                        try:
                            from datetime import timedelta
                            now_utc = datetime.utcnow()
                            upload_timestamp = now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
                            next_upload_dt = now_utc + timedelta(minutes=30)
                            next_upload_timestamp = next_upload_dt.strftime('%Y-%m-%d %H:%M:%S UTC')

                            last_upload_setting = GlobalSettings.query.filter_by(setting_key='last_sftp_upload_time').first()
                            if last_upload_setting:
                                last_upload_setting.setting_value = upload_timestamp
                                last_upload_setting.updated_at = now_utc
                            else:
                                last_upload_setting = GlobalSettings(
                                    setting_key='last_sftp_upload_time',
                                    setting_value=upload_timestamp
                                )
                                db.session.add(last_upload_setting)

                            next_upload_setting = GlobalSettings.query.filter_by(setting_key='next_sftp_upload_time').first()
                            if next_upload_setting:
                                next_upload_setting.setting_value = next_upload_timestamp
                                next_upload_setting.updated_at = now_utc
                            else:
                                next_upload_setting = GlobalSettings(
                                    setting_key='next_sftp_upload_time',
                                    setting_value=next_upload_timestamp
                                )
                                db.session.add(next_upload_setting)

                            db.session.commit()
                            app.logger.info(f"✅ Updated last upload timestamp: {upload_timestamp}")
                            app.logger.info(f"✅ Updated next upload timestamp: {next_upload_timestamp}")
                        except Exception as ts_error:
                            app.logger.error(f"Failed to track upload timestamp: {str(ts_error)}")
                else:
                    upload_error_message = "SFTP credentials not configured"
                    app.logger.error("❌ SFTP credentials not configured in Global Settings")
                
                try:
                    os.remove(temp_file.name)
                except:
                    pass
                
                
                email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                
                if (email_enabled and email_enabled.setting_value == 'true' and 
                    email_setting and email_setting.setting_value):
                    try:
                        from email_service import EmailService
                        from timezone_utils import format_eastern_time
                        email_service = EmailService()
                        
                        current_time = datetime.utcnow()
                        next_upload_time = current_time + timedelta(minutes=30)
                        
                        notification_details = {
                            'execution_time': format_eastern_time(current_time),
                            'jobs_count': stats['job_count'],
                            'xml_size': f"{stats['xml_size_bytes']:,} bytes",
                            'upload_attempted': True,
                            'upload_success': upload_success,
                            'upload_error': upload_error_message,
                            'next_upload': format_eastern_time(next_upload_time)
                        }
                        
                        status = "success" if upload_success else "error"
                        email_sent = email_service.send_automated_upload_notification(
                            to_email=email_setting.setting_value,
                            total_jobs=stats['job_count'],
                            upload_details=notification_details,
                            status=status
                        )
                        
                        if email_sent:
                            app.logger.info(f"📧 Upload notification sent to {email_setting.setting_value}")
                        else:
                            app.logger.warning("📧 Failed to send upload notification email")
                    
                    except Exception as email_error:
                        app.logger.error(f"Failed to send upload notification: {str(email_error)}")
                
            except Exception as upload_error:
                app.logger.error(f"Upload process error during automated upload: {str(upload_error)}")
            
        except Exception as e:
            app.logger.error(f"❌ Automated upload error: {str(e)}")


def run_xml_change_monitor():
    """Run XML change monitor and send notifications for detected changes"""
    from app import app
    from extensions import db
    try:
        with app.app_context():
            from models import GlobalSettings, BullhornActivity
            from xml_change_monitor import create_xml_monitor
            from utils.bullhorn_helpers import get_email_service
            
            email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
            if not email_setting or not email_setting.setting_value:
                app.logger.warning("XML MONITOR: No notification email configured in global settings")
                return
                
            xml_monitor = create_xml_monitor()
            email_service = get_email_service()
            result = xml_monitor.monitor_xml_changes(email_setting.setting_value, email_service, enable_email_notifications=False)
        
            if result.get('success'):
                changes = result.get('changes', {})
                total_changes = changes.get('total_changes', 0)
                
                if total_changes > 0:
                    app.logger.info(f"🔍 XML MONITOR COMPLETE: {total_changes} changes detected (email notifications temporarily disabled)")
                    
                    try:
                        activity_details = {
                            'monitor_type': 'XML Change Monitor',
                            'changes_detected': total_changes,
                            'added_jobs': changes.get('added', 0) if isinstance(changes.get('added'), int) else len(changes.get('added', [])),
                            'removed_jobs': changes.get('removed', 0) if isinstance(changes.get('removed'), int) else len(changes.get('removed', [])),
                            'modified_jobs': changes.get('modified', 0) if isinstance(changes.get('modified'), int) else len(changes.get('modified', [])),
                            'email_sent_to': email_setting.setting_value,
                            'xml_url': 'https://myticas.com/myticas-job-feed-v2.xml'
                        }
                        
                        xml_monitor_activity = BullhornActivity(
                            monitor_id=None,
                            activity_type='xml_sync_completed',
                            details=json.dumps(activity_details),
                            notification_sent=True
                        )
                        db.session.add(xml_monitor_activity)
                        db.session.commit()
                        
                        app.logger.info("📧 ACTIVITY LOGGED: XML change notification logged to Activity monitoring")
                        
                    except Exception as e:
                        app.logger.error(f"Failed to log XML monitor activity: {str(e)}")
                        db.session.rollback()
                        
                else:
                    app.logger.info("🔍 XML MONITOR COMPLETE: No changes detected")
            else:
                app.logger.error(f"XML MONITOR ERROR: {result.get('error', 'Unknown error')}")
            
    except Exception as e:
        app.logger.error(f"XML change monitor error: {str(e)}")


def start_scheduler_manual():
    """Manually start the scheduler and trigger monitoring"""
    from app import app, lazy_start_scheduler, process_bullhorn_monitors, scheduler
    from extensions import db
    from flask import jsonify
    try:
        from models import BullhornMonitor
        
        scheduler_started = lazy_start_scheduler()
        
        if scheduler_started:
            monitors = BullhornMonitor.query.filter_by(is_active=True).all()
            current_time = datetime.utcnow()
            for monitor in monitors:
                monitor.last_check = current_time
                monitor.next_check = current_time + timedelta(minutes=2)
            db.session.commit()
            
            try:
                process_bullhorn_monitors()
                message = f"Scheduler started. {len(monitors)} monitors activated with 2-minute intervals."
            except Exception as e:
                message = f"Scheduler started but monitoring failed: {str(e)}"
        else:
            message = "Scheduler was already running or failed to start"
            
        return jsonify({
            'success': True,
            'message': message,
            'scheduler_running': scheduler.running
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def cleanup_linkedin_source():
    """
    Hourly scheduled job: find any Bullhorn Candidate records whose source contains
    a LinkedIn variant (Linkedin, linkedin, LINKEDIN, etc.) but is NOT already
    "LinkedIn Job Board", and update them to "LinkedIn Job Board".

    THREAD-SAFETY: Uses standalone requests.get/post — never bh.session.* — because
    this runs in a background APScheduler thread and requests.Session is not thread-safe.
    """
    from app import app
    import requests as _requests

    with app.app_context():
        try:
            from bullhorn_service import BullhornService

            bh = BullhornService()
            if not bh.authenticate():
                logger.warning("linkedin_source_cleanup: Bullhorn authentication failed — skipping run")
                return

            base_url = bh.base_url
            rest_token = bh.rest_token
            headers = {
                "BhRestToken": rest_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            search_url = f"{base_url}search/Candidate"
            query = 'source:LinkedIn AND -source:"LinkedIn Job Board"'

            count_resp = _requests.get(search_url, headers=headers, params={
                "query": query, "fields": "id", "count": 1, "start": 0,
            }, timeout=30)
            count_resp.raise_for_status()
            total = count_resp.json().get("total", 0)

            if total == 0:
                logger.info("linkedin_source_cleanup: 0 records need updating — nothing to do")
                return

            logger.info(f"linkedin_source_cleanup: found {total:,} records to update")

            succeeded = 0
            failed = 0
            start = 0
            batch_size = 500

            while start < total:
                fetch_resp = _requests.get(search_url, headers=headers, params={
                    "query": query, "fields": "id",
                    "count": batch_size, "start": start,
                }, timeout=30)
                fetch_resp.raise_for_status()
                record_ids = [r["id"] for r in fetch_resp.json().get("data", [])]

                if not record_ids:
                    break

                for record_id in record_ids:
                    try:
                        upd = _requests.post(
                            f"{base_url}entity/Candidate/{record_id}",
                            headers=headers,
                            json={"source": "LinkedIn Job Board"},
                            timeout=15,
                        )
                        body = {}
                        try:
                            body = upd.json()
                        except Exception:
                            pass
                        if (upd.status_code in (200, 201)
                                and not body.get("errorCode")
                                and not body.get("errors")
                                and (body.get("changeType") == "UPDATE"
                                     or body.get("changedEntityId") is not None)):
                            succeeded += 1
                        else:
                            failed += 1
                    except Exception as rec_err:
                        failed += 1
                        logger.warning(f"linkedin_source_cleanup: error on ID {record_id}: {rec_err}")

                start += len(record_ids)
                time.sleep(0.05)

            logger.info(
                f"linkedin_source_cleanup: complete — {succeeded:,} updated, {failed:,} failed"
            )

        except Exception as e:
            logger.error(f"linkedin_source_cleanup: unexpected error — {e}")


def enforce_tearsheet_jobs_public():
    """
    Scheduled job (every 30 minutes): find all jobs in monitored tearsheets where
    isPublic is not true and set them to public.

    Runs automatically so any job added to a tearsheet without the isPublic flag
    set correctly is corrected within the next cycle — no manual intervention needed.

    THREAD-SAFETY: Uses standalone requests.get/post — never bh.session.* — because
    this runs in a background APScheduler thread and requests.Session is not thread-safe.
    """
    from app import app
    import requests as _requests

    INELIGIBLE_STATUSES = {
        'Qualifying', 'Hold - Covered', 'Hold - Client Hold', 'Offer Out',
        'Filled', 'Lost - Competition', 'Lost - Filled Internally',
        'Lost - Funding', 'Canceled', 'Placeholder/ MPC', 'Archive'
    }

    with app.app_context():
        try:
            from models import BullhornMonitor
            from bullhorn_service import BullhornService

            monitors = BullhornMonitor.query.filter_by(is_active=True).all()
            tearsheet_ids = [m.tearsheet_id for m in monitors if m.tearsheet_id]

            if not tearsheet_ids:
                logger.info("enforce_tearsheet_jobs_public: no active monitors configured — skipping")
                return

            bh = BullhornService()
            if not bh.authenticate():
                logger.warning("enforce_tearsheet_jobs_public: Bullhorn authentication failed — skipping run")
                return

            base_url = bh.base_url
            rest_token = bh.rest_token
            headers = {
                "BhRestToken": rest_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            tearsheet_clause = " OR ".join(str(tid) for tid in tearsheet_ids)
            query = f"tearsheets.id:({tearsheet_clause}) AND isPublic:0 AND NOT isDeleted:true"

            search_url = f"{base_url}search/JobOrder"

            count_resp = _requests.get(search_url, headers=headers, params={
                "query": query, "fields": "id", "count": 1, "start": 0,
            }, timeout=30)
            count_resp.raise_for_status()
            total = count_resp.json().get("total", 0)

            if total == 0:
                logger.info("enforce_tearsheet_jobs_public: all tearsheet jobs are already public — nothing to do")
                return

            logger.info(f"enforce_tearsheet_jobs_public: found {total:,} non-public job(s) across tearsheets {tearsheet_ids}")

            all_jobs = []
            start = 0
            batch_size = 200

            while start < total:
                fetch_resp = _requests.get(search_url, headers=headers, params={
                    "query": query, "fields": "id,status",
                    "count": batch_size, "start": start,
                }, timeout=30)
                fetch_resp.raise_for_status()
                page = fetch_resp.json().get("data", [])
                if not page:
                    break
                all_jobs.extend(page)
                start += len(page)
                if len(page) < batch_size:
                    break

            seen_ids = set()
            jobs_to_update = []
            for job in all_jobs:
                job_id = job.get("id")
                status = job.get("status", "")
                if job_id and job_id not in seen_ids and status not in INELIGIBLE_STATUSES:
                    seen_ids.add(job_id)
                    jobs_to_update.append(job_id)

            skipped = total - len(jobs_to_update)
            if not jobs_to_update:
                logger.info(f"enforce_tearsheet_jobs_public: all {total} non-public jobs have ineligible statuses — skipping updates")
                return

            logger.info(f"enforce_tearsheet_jobs_public: will update {len(jobs_to_update)} job(s) (skipped {skipped} with ineligible status)")

            succeeded = 0
            failed = 0
            sample_updated = []

            for job_id in jobs_to_update:
                try:
                    upd = _requests.post(
                        f"{base_url}entity/JobOrder/{job_id}",
                        headers=headers,
                        json={"isPublic": True},
                        timeout=15,
                    )
                    body = {}
                    try:
                        body = upd.json()
                    except Exception:
                        pass
                    if (upd.status_code in (200, 201)
                            and not body.get("errorCode")
                            and not body.get("errors")
                            and (body.get("changeType") == "UPDATE"
                                 or body.get("changedEntityId") is not None)):
                        succeeded += 1
                        if len(sample_updated) < 5:
                            sample_updated.append(job_id)
                    else:
                        failed += 1
                        logger.warning(
                            f"enforce_tearsheet_jobs_public: unexpected response for job {job_id}: "
                            f"HTTP {upd.status_code} — {body}"
                        )
                except Exception as rec_err:
                    failed += 1
                    logger.warning(f"enforce_tearsheet_jobs_public: error on job {job_id}: {rec_err}")

                time.sleep(0.05)

            logger.info(
                f"enforce_tearsheet_jobs_public: complete — {succeeded} updated, {failed} failed"
                + (f" | sample IDs: {sample_updated}" if sample_updated else "")
            )

        except Exception as e:
            logger.error(f"enforce_tearsheet_jobs_public: unexpected error — {e}")
