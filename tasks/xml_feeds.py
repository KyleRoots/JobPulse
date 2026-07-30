import os
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def reference_number_refresh():
    """Automatic refresh of all reference numbers every 120 hours while preserving all other XML data.

    Covers every published XML feed tearsheet set (v2 + STSI Indeed + ZipRecruiter),
    persists rotated refs to JobReferenceNumber, and relies on the 30-minute upload
    cycle to republish each feed file with the new values.
    """
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

            app.logger.info(
                "Starting 120-hour reference number refresh across all XML feeds "
                "(v2 + STSI Indeed + ZipRecruiter)..."
            )

            from simplified_xml_generator import SimplifiedXMLGenerator
            from lightweight_reference_refresh import refresh_all_feed_references

            generator = SimplifiedXMLGenerator(db=db)
            result = refresh_all_feed_references(generator)

            if result['success']:
                app.logger.info(
                    f"Reference refresh complete: {result['jobs_updated']} jobs updated "
                    f"across feeds {result.get('feeds_covered')} "
                    f"in {result['time_seconds']:.2f} seconds"
                )

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

                if not result.get('database_saved'):
                    error_msg = "Database-first architecture requires successful DB save - 120-hour refresh FAILED"
                    app.logger.critical(f"CRITICAL: {error_msg}")
                    raise Exception(error_msg)

                app.logger.info("DATABASE-FIRST: Reference numbers successfully saved to database")
                app.logger.info(
                    "Reference refresh complete: refs updated for all feed tearsheets "
                    "(30-minute upload cycle will publish v2 + Indeed + ZipRecruiter)"
                )

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
                            'database_saved': result.get('database_saved'),
                            'feeds_covered': ', '.join(result.get('feeds_covered') or []),
                            'tearsheet_ids': result.get('tearsheet_ids'),
                            'note': (
                                'Reference numbers saved for v2 + STSI Indeed + ZipRecruiter — '
                                '30-minute upload cycle will publish all three feeds'
                            ),
                        }

                        email_sent = email_service.send_reference_number_refresh_notification(
                            to_email=email_setting.setting_value,
                            schedule_name="120-Hour Reference Number Refresh (All Feeds)",
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
                    feeds = ', '.join(result.get('feeds_covered') or [])
                    activity = BullhornActivity(
                        monitor_id=None,
                        activity_type='reference_refresh',
                        details=(
                            f'Daily automatic refresh (all feeds: {feeds}): '
                            f'{result["jobs_updated"]} reference numbers updated'
                        ),
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
                            schedule_name="120-Hour Reference Number Refresh (All Feeds)",
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
            # Surface why it failed. The service records the underlying reason
            # (auth rejected, timeout, bad path); without it the failure e-mail
            # only says "returned False", which tells an operator nothing.
            err = getattr(ftp_service, 'last_error', None) or "Upload returned False"
            app.logger.error(f"'{remote_filename}' upload failed: {err}")
            return False, err
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
    Generates v2 feed (myticas-job-feed-v2.xml) plus STSI channel feeds for
    Indeed and ZipRecruiter on the same cycle.
    """
    from app import app
    from extensions import db
    from feeds.feed_config import (
        channel_feeds_for_upload,
        V2_FILENAME,
        V2_FILENAME_DEV,
        SOURCE_LINKEDIN,
    )
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

            app.logger.info("Starting automated 30-minute upload cycle (v2 + STSI channel feeds)...")

            from simplified_xml_generator import SimplifiedXMLGenerator
            generator = SimplifiedXMLGenerator(db=db)

            app.logger.info("Generating v2 feed (Myticas tearsheets + STSI LinkedIn)...")
            v2_xml, v2_stats = generator.generate_fresh_xml(source_channel=SOURCE_LINKEDIN)
            app.logger.info(f"v2 feed: {v2_stats['job_count']} jobs, {v2_stats['xml_size_bytes']:,} bytes")

            channel_results = {}
            for feed_cfg in channel_feeds_for_upload():
                key = feed_cfg['key']
                if feed_cfg.get('force_empty'):
                    app.logger.info(
                        f"Parking {key} XML feed (Indeed native Plan B enabled) — "
                        f"uploading empty feed to retire XML syndication"
                    )
                    tearsheet_ids = []
                else:
                    tearsheet_ids = feed_cfg['tearsheet_ids']
                    app.logger.info(f"Generating {key} feed from tearsheets {tearsheet_ids}...")
                xml_content, stats = generator.generate_fresh_xml(
                    tearsheet_ids=tearsheet_ids,
                    source_channel=feed_cfg['source_channel'],
                    allow_empty=True if feed_cfg.get('force_empty') else feed_cfg.get('allow_empty', False),
                    publisher_title=feed_cfg.get('publisher_title'),
                    publisher_link=feed_cfg.get('publisher_link'),
                )
                channel_results[key] = {
                    'xml': xml_content,
                    'stats': stats,
                    'filenames': {
                        'production': feed_cfg['filename'],
                        'development': feed_cfg.get('filename_dev', feed_cfg['filename']),
                    },
                }
                app.logger.info(
                    f"{key} feed: {stats['job_count']} jobs, {stats['xml_size_bytes']:,} bytes"
                )

            app.logger.info("CHECKPOINT 1: All XML feeds generated successfully")
            app.logger.info("Reference numbers loaded from DATABASE (database-first approach)")

            v2_upload_ok = False
            channel_upload_ok = {key: False for key in channel_results}
            upload_error_message = None

            try:
                sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
                sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
                sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
                sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
                sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()

                host_val = (sftp_hostname.setting_value or '').strip() if sftp_hostname else ''
                user_val = (sftp_username.setting_value or '').strip() if sftp_username else ''
                pass_val = (sftp_password.setting_value or '').strip() if sftp_password else ''
                dir_val = (sftp_directory.setting_value or '/').strip() if sftp_directory else '/'
                env_host = (os.environ.get('SFTP_HOSTNAME') or os.environ.get('SFTP_HOST') or '').strip()
                if not host_val or host_val.startswith('{') or '.' not in host_val:
                    if env_host:
                        app.logger.warning(
                            "sftp_hostname DB value looks invalid (%r); using SFTP_HOSTNAME env fallback",
                            (host_val or '')[:80],
                        )
                        host_val = env_host
                        try:
                            GlobalSettings.set_value('sftp_hostname', env_host)
                        except Exception:
                            pass
                user_val = user_val or (os.environ.get('SFTP_USERNAME') or '').strip()
                pass_val = pass_val or (os.environ.get('SFTP_PASSWORD') or '').strip()

                if host_val and user_val and pass_val:

                    target_directory = dir_val or "/"
                    app.logger.info(f"Uploading to configured directory: '{target_directory}'")

                    from ftp_service import FTPService
                    try:
                        port_value = int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222
                    except ValueError:
                        port_value = 2222
                    if port_value == 22:
                        port_value = 2222
                    ftp_service = FTPService(
                        hostname=host_val,
                        username=user_val,
                        password=pass_val,
                        target_directory=target_directory,
                        port=port_value,
                        use_sftp=True
                    )
                    app.logger.info(f"Using SFTP protocol for thread-safe uploads to {host_val}:{ftp_service.port}")

                    current_env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'production').lower()
                    app.logger.info(f"Environment: {current_env}")

                    if current_env not in ['production', 'development']:
                        app.logger.error(f"Invalid environment '{current_env}' - defaulting to development for safety")
                        current_env = 'development'

                    v2_filename = V2_FILENAME if current_env == 'production' else V2_FILENAME_DEV

                    app.logger.info(f"{current_env.upper()}: uploading {v2_filename}")

                    # One authenticated connection for all feeds in the cycle.
                    try:
                        with ftp_service.sftp_session():
                            v2_upload_ok, v2_err = _upload_single_file(
                                ftp_service, v2_xml, v2_filename, app
                            )

                            if not v2_upload_ok:
                                upload_error_message = f"v2: {v2_err}"

                            for key, result in channel_results.items():
                                remote_filename = result['filenames'][current_env]
                                app.logger.info(f"{current_env.upper()}: uploading {remote_filename}")
                                ok, err = _upload_single_file(
                                    ftp_service, result['xml'], remote_filename, app
                                )
                                channel_upload_ok[key] = ok
                                if not ok:
                                    err_part = f"{key}: {err}"
                                    upload_error_message = f"{upload_error_message}; {err_part}" if upload_error_message else err_part
                    except Exception as conn_error:
                        # Connection could not be established even after retries,
                        # so no feed was attempted. Report that plainly rather
                        # than letting it surface as a generic task crash.
                        upload_error_message = (
                            f"SFTP connection failed, no feeds uploaded "
                            f"({type(conn_error).__name__}: {conn_error})"
                        )
                        app.logger.error(upload_error_message)

                    app.logger.info(f"ENVIRONMENT ISOLATION: {current_env} -> uploads ONLY to its designated files")

                    upload_success = v2_upload_ok and all(channel_upload_ok.values())

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

                            feed_result = json.dumps({
                                'v2_jobs': v2_stats['job_count'],
                                'v2_size': v2_stats['xml_size_bytes'],
                                'stsi_indeed_jobs': channel_results['stsi_indeed']['stats']['job_count'],
                                'stsi_indeed_size': channel_results['stsi_indeed']['stats']['xml_size_bytes'],
                                'stsi_ziprecruiter_jobs': channel_results['stsi_ziprecruiter']['stats']['job_count'],
                                'stsi_ziprecruiter_size': channel_results['stsi_ziprecruiter']['stats']['xml_size_bytes'],
                                'timestamp': upload_timestamp
                            })
                            feed_setting = GlobalSettings.query.filter_by(setting_key='dual_feed_last_result').first()
                            if feed_setting:
                                feed_setting.setting_value = feed_result
                                feed_setting.updated_at = now_utc
                            else:
                                feed_setting = GlobalSettings(
                                    setting_key='dual_feed_last_result',
                                    setting_value=feed_result
                                )
                                db.session.add(feed_setting)

                            db.session.commit()
                            app.logger.info(f"Updated last upload timestamp: {upload_timestamp}")
                            app.logger.info(f"Updated next upload timestamp: {next_upload_timestamp}")
                            app.logger.info(
                                f"Feed stats saved: v2={v2_stats['job_count']}, "
                                f"indeed={channel_results['stsi_indeed']['stats']['job_count']}, "
                                f"zip={channel_results['stsi_ziprecruiter']['stats']['job_count']} jobs"
                            )
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
                            'stsi_indeed_jobs_count': channel_results['stsi_indeed']['stats']['job_count'],
                            'stsi_indeed_xml_size': f"{channel_results['stsi_indeed']['stats']['xml_size_bytes']:,} bytes",
                            'stsi_ziprecruiter_jobs_count': channel_results['stsi_ziprecruiter']['stats']['job_count'],
                            'stsi_ziprecruiter_xml_size': f"{channel_results['stsi_ziprecruiter']['stats']['xml_size_bytes']:,} bytes",
                            'upload_attempted': True,
                            'upload_success': upload_success,
                            'upload_error': upload_error_message,
                            'next_upload': format_eastern_time(next_upload_time),
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
