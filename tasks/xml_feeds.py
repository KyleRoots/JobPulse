import os
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


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
                app.logger.info(f"Reference refresh already completed today at {existing_refresh.refresh_time}")
                return

            app.logger.info("Starting 120-hour reference number refresh...")

            from simplified_xml_generator import SimplifiedXMLGenerator

            generator = SimplifiedXMLGenerator(db=db)

            xml_content, stats = generator.generate_fresh_xml()
            app.logger.info(f"Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")

            from lightweight_reference_refresh import lightweight_refresh_references_from_content

            result = lightweight_refresh_references_from_content(xml_content)

            if result['success']:
                app.logger.info(f"Reference refresh complete: {result['jobs_updated']} jobs updated in {result['time_seconds']:.2f} seconds")

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
                    app.logger.info("Refresh completion logged to database")
                except Exception as log_error:
                    app.logger.error(f"Failed to log refresh completion: {str(log_error)}")
                    db.session.rollback()

                from lightweight_reference_refresh import save_references_to_database
                db_save_success = save_references_to_database(result['xml_content'])

                if not db_save_success:
                    error_msg = "Database-first architecture requires successful DB save - 120-hour refresh FAILED"
                    app.logger.critical(f"CRITICAL: {error_msg}")
                    raise Exception(error_msg)

                app.logger.info("DATABASE-FIRST: Reference numbers successfully saved to database")
                app.logger.info("Reference refresh complete: Reference numbers updated in database (30-minute upload cycle will use these values)")

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
                            app.logger.info(f"Refresh confirmation email sent to {email_setting.setting_value}")
                            refresh_log_var = locals().get('refresh_log')
                            if refresh_log_var:
                                refresh_log_var.email_sent = True
                                db.session.commit()
                        else:
                            app.logger.warning("Failed to send refresh confirmation email")
                    else:
                        app.logger.warning("No notification email configured - skipping confirmation email")

                except Exception as email_error:
                    app.logger.error(f"Failed to send refresh confirmation email: {str(email_error)}")

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
                app.logger.error(f"Reference refresh failed: {result.get('error', 'Unknown error')}")

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
                            app.logger.info(f"Refresh failure alert sent to {email_setting.setting_value}")
                        else:
                            app.logger.warning("Failed to send refresh failure alert")

                except Exception as email_error:
                    app.logger.error(f"Failed to send refresh failure alert: {str(email_error)}")

        except Exception as e:
            app.logger.error(f"Reference refresh error: {str(e)}")


def _upload_single_file(ftp_service, xml_content, remote_filename, app):
    """Helper: write XML to a temp file and upload via SFTP. Returns (success, error_msg)."""
    import tempfile
    temp_path = None
    try:
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8')
        temp_path = temp_file.name
        temp_file.write(xml_content)
        temp_file.close()

        app.logger.info(f"Uploading '{remote_filename}' ({len(xml_content):,} bytes)...")
        upload_result = ftp_service.upload_file(local_file_path=temp_path, remote_filename=remote_filename)

        if isinstance(upload_result, dict):
            if upload_result.get('success'):
                app.logger.info(f"'{remote_filename}' uploaded successfully")
                return True, None
            else:
                err = upload_result.get('error', 'Unknown upload error')
                app.logger.error(f"'{remote_filename}' upload failed: {err}")
                return False, err
        elif upload_result:
            app.logger.info(f"'{remote_filename}' uploaded successfully")
            return True, None
        else:
            app.logger.error(f"'{remote_filename}' upload failed")
            return False, "Upload returned False"
    except Exception as e:
        app.logger.error(f"'{remote_filename}' upload error: {e}")
        return False, str(e)
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except Exception:
                pass


def automated_upload():
    """Automatically upload fresh XML every 30 minutes if automation is enabled.
    Generates two feeds:
      - myticas-job-feed-v2.xml   — all tearsheets; STSI (1531) jobs WITHOUT #STSIVMS or #STSIEG tags
      - myticas-job-feed-pando.xml — all tearsheets; ALL STSI (1531) jobs (no tag filter)
    """
    from app import app
    from extensions import db
    with app.app_context():
        app.logger.info("AUTOMATED UPLOAD: Function invoked by scheduler")
        try:
            from models import GlobalSettings

            automation_setting = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
            if not (automation_setting and automation_setting.setting_value == 'true'):
                app.logger.info("Automated uploads disabled in settings, skipping upload cycle")
                return

            sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
            if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
                app.logger.warning("Automated upload skipped: SFTP not enabled")
                return

            app.logger.info("Starting automated 30-minute dual-feed upload cycle...")

            from simplified_xml_generator import SimplifiedXMLGenerator
            generator = SimplifiedXMLGenerator(db=db)

            app.logger.info("[FEED 1/2] Generating pando feed (STSI: all jobs, no tag filter) — saves reference numbers...")
            pando_xml, pando_stats = generator.generate_fresh_xml(stsi_tag_mode=None)
            app.logger.info(f"pando feed: {pando_stats['job_count']} jobs, {pando_stats['xml_size_bytes']:,} bytes")

            app.logger.info("[FEED 2/2] Generating v2 feed (STSI: untagged jobs only) — uses existing references...")
            v2_xml, v2_stats = generator.generate_fresh_xml(stsi_tag_mode='exclude_tags')
            app.logger.info(f"v2 feed: {v2_stats['job_count']} jobs, {v2_stats['xml_size_bytes']:,} bytes")

            app.logger.info("CHECKPOINT 1: Both XML feeds generated successfully")
            app.logger.info("Reference numbers loaded from DATABASE (database-first approach)")

            v2_upload_ok = False
            pando_upload_ok = False
            upload_error_message = None

            try:
                sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
                sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
                sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
                sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
                sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()

                if (sftp_hostname and sftp_hostname.setting_value and
                    sftp_username and sftp_username.setting_value and
                    sftp_password and sftp_password.setting_value):

                    target_directory = sftp_directory.setting_value if sftp_directory else "/"
                    app.logger.info(f"Uploading to configured directory: '{target_directory}'")

                    from ftp_service import FTPService
                    ftp_service = FTPService(
                        hostname=sftp_hostname.setting_value,
                        username=sftp_username.setting_value,
                        password=sftp_password.setting_value,
                        target_directory=target_directory,
                        port=int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222,
                        use_sftp=True
                    )
                    app.logger.info(f"Using SFTP protocol for thread-safe uploads to {sftp_hostname.setting_value}:{ftp_service.port}")

                    current_env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'production').lower()
                    app.logger.info(f"Environment: {current_env}")

                    if current_env not in ['production', 'development']:
                        app.logger.error(f"Invalid environment '{current_env}' - defaulting to development for safety")
                        current_env = 'development'

                    if current_env == 'production':
                        v2_filename = "myticas-job-feed-v2.xml"
                        pando_filename = "myticas-job-feed-pando.xml"
                    else:
                        v2_filename = "myticas-job-feed-v2-dev.xml"
                        pando_filename = "myticas-job-feed-pando-dev.xml"

                    app.logger.info(f"{current_env.upper()}: uploading {v2_filename} + {pando_filename}")

                    v2_upload_ok, v2_err = _upload_single_file(ftp_service, v2_xml, v2_filename, app)
                    pando_upload_ok, pando_err = _upload_single_file(ftp_service, pando_xml, pando_filename, app)

                    if not v2_upload_ok or not pando_upload_ok:
                        errors = []
                        if not v2_upload_ok:
                            errors.append(f"v2: {v2_err}")
                        if not pando_upload_ok:
                            errors.append(f"pando: {pando_err}")
                        upload_error_message = "; ".join(errors)

                    app.logger.info(f"ENVIRONMENT ISOLATION: {current_env} -> uploads ONLY to its designated files")

                    upload_success = v2_upload_ok and pando_upload_ok

                    if upload_success:
                        try:
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

                            dual_feed_result = json.dumps({
                                'v2_jobs': v2_stats['job_count'],
                                'pando_jobs': pando_stats['job_count'],
                                'v2_size': v2_stats['xml_size_bytes'],
                                'pando_size': pando_stats['xml_size_bytes'],
                                'timestamp': upload_timestamp
                            })
                            feed_setting = GlobalSettings.query.filter_by(setting_key='dual_feed_last_result').first()
                            if feed_setting:
                                feed_setting.setting_value = dual_feed_result
                                feed_setting.updated_at = now_utc
                            else:
                                feed_setting = GlobalSettings(
                                    setting_key='dual_feed_last_result',
                                    setting_value=dual_feed_result
                                )
                                db.session.add(feed_setting)

                            db.session.commit()
                            app.logger.info(f"Updated last upload timestamp: {upload_timestamp}")
                            app.logger.info(f"Updated next upload timestamp: {next_upload_timestamp}")
                            app.logger.info(f"Dual feed stats saved: v2={v2_stats['job_count']} jobs, pando={pando_stats['job_count']} jobs")
                        except Exception as ts_error:
                            app.logger.error(f"Failed to track upload timestamp: {str(ts_error)}")
                else:
                    upload_error_message = "SFTP credentials not configured"
                    upload_success = False
                    app.logger.error("SFTP credentials not configured in Global Settings")

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
                            'jobs_count': v2_stats['job_count'],
                            'xml_size': f"{v2_stats['xml_size_bytes']:,} bytes",
                            'upload_attempted': True,
                            'upload_success': upload_success,
                            'upload_error': upload_error_message,
                            'next_upload': format_eastern_time(next_upload_time),
                            'pando_jobs_count': pando_stats['job_count'],
                            'pando_xml_size': f"{pando_stats['xml_size_bytes']:,} bytes",
                            'dual_feed': True
                        }

                        status = "success" if upload_success else "error"
                        email_sent = email_service.send_automated_upload_notification(
                            to_email=email_setting.setting_value,
                            total_jobs=v2_stats['job_count'],
                            upload_details=notification_details,
                            status=status
                        )

                        if email_sent:
                            app.logger.info(f"Upload notification sent to {email_setting.setting_value}")
                        else:
                            app.logger.warning("Failed to send upload notification email")

                    except Exception as email_error:
                        app.logger.error(f"Failed to send upload notification: {str(email_error)}")

            except Exception as upload_error:
                app.logger.error(f"Upload process error during automated upload: {str(upload_error)}")

        except Exception as e:
            app.logger.error(f"Automated upload error: {str(e)}")


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
                    app.logger.info(f"XML MONITOR COMPLETE: {total_changes} changes detected (email notifications temporarily disabled)")

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

                        app.logger.info("ACTIVITY LOGGED: XML change notification logged to Activity monitoring")

                    except Exception as e:
                        app.logger.error(f"Failed to log XML monitor activity: {str(e)}")
                        db.session.rollback()

                else:
                    app.logger.info("XML MONITOR COMPLETE: No changes detected")
            else:
                app.logger.error(f"XML MONITOR ERROR: {result.get('error', 'Unknown error')}")

    except Exception as e:
        app.logger.error(f"XML change monitor error: {str(e)}")
