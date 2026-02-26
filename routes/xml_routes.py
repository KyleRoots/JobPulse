import os
import re
import json
import time
import shutil
import uuid
import signal
import tempfile
import logging
from datetime import datetime, timedelta, date
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, send_file, after_this_request, session, current_app
from flask_login import login_required, current_user
from routes import register_admin_guard
from werkzeug.utils import secure_filename
from extensions import db

logger = logging.getLogger(__name__)
xml_routes_bp = Blueprint('xml_routes', __name__)
register_admin_guard(xml_routes_bp)

progress_tracker = {}

ALLOWED_EXTENSIONS = {'xml'}


def allowed_file(filename):
    """Check if file has allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@xml_routes_bp.route('/api/refresh-reference-numbers', methods=['POST'])
@login_required
def refresh_reference_numbers():
    """Ad-hoc refresh of all reference numbers using fresh Bullhorn data"""
    try:
        from app import get_xml_filename
        from xml_processor import XMLProcessor
        from email_service import EmailService
        from ftp_service import FTPService
        from models import GlobalSettings, RefreshLog

        logger.info("🔄 AD-HOC REFERENCE NUMBER REFRESH: Starting manual refresh with fresh Bullhorn data")

        from simplified_xml_generator import SimplifiedXMLGenerator

        generator = SimplifiedXMLGenerator(db=db)

        xml_content, stats = generator.generate_fresh_xml()
        logger.info(f"📊 Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")

        from lightweight_reference_refresh import lightweight_refresh_references_from_content

        result = lightweight_refresh_references_from_content(xml_content)

        if not result['success']:
            return jsonify({
                'success': False,
                'error': f"Failed to refresh reference numbers: {result.get('error', 'Unknown error')}"
            }), 500

        logger.info(f"✅ Reference refresh complete: {result['jobs_updated']} jobs updated in {result['time_seconds']:.2f} seconds")

        from lightweight_reference_refresh import save_references_to_database
        db_save_success = save_references_to_database(result['xml_content'])

        if not db_save_success:
            error_msg = "Database-first architecture requires successful DB save - manual refresh aborted"
            logger.critical(f"❌ CRITICAL: {error_msg}")
            return jsonify({
                'success': False,
                'error': error_msg,
                'details': 'Reference numbers must be saved to database before upload. Please try again.'
            }), 500

        logger.info("💾 DATABASE-FIRST: Reference numbers successfully saved to database")

        email_service = EmailService()

        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
        sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()

        upload_success = False
        upload_error_message = None

        if (sftp_hostname and sftp_hostname.setting_value and
            sftp_username and sftp_username.setting_value and
            sftp_password and sftp_password.setting_value):

            temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8')
            try:
                temp_file.write(result['xml_content'])
                temp_file.flush()
                temp_file_path = temp_file.name
            finally:
                temp_file.close()

            try:
                ftp_service = FTPService(
                    hostname=sftp_hostname.setting_value,
                    username=sftp_username.setting_value,
                    password=sftp_password.setting_value,
                    target_directory=sftp_directory.setting_value if sftp_directory else "public_html",
                    port=int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222,
                    use_sftp=True
                )

                remote_filename = get_xml_filename()
                upload_result = ftp_service.upload_file(temp_file_path, remote_filename)

                if upload_result:
                    upload_success = True
                    logger.info(f"📤 Successfully uploaded refreshed XML as {remote_filename} to server")
                else:
                    upload_error_message = "Upload failed: FTP service returned False"
                    logger.error(upload_error_message)

            except Exception as upload_error:
                upload_error_message = str(upload_error)
                logger.error(f"Upload failed: {upload_error_message}")
            finally:
                try:
                    os.remove(temp_file_path)
                except:
                    pass
        else:
            upload_error_message = "SFTP credentials not configured"
            logger.warning("SFTP not configured - skipping upload")

        logger.info(f"🔄 MANUAL REFRESH COMPLETE: User {current_user.username} refreshed {result['jobs_updated']} reference numbers")

        try:
            today = date.today()
            refresh_log = RefreshLog(
                refresh_date=today,
                refresh_time=datetime.utcnow(),
                jobs_updated=result['jobs_updated'],
                processing_time=result['time_seconds'],
                email_sent=False
            )
            db.session.add(refresh_log)
            db.session.commit()
            logger.info("📝 Manual refresh completion logged to database")
        except Exception as e:
            logger.error(f"Failed to record refresh log: {e}")
            db.session.rollback()

        try:
            notification_email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
            if notification_email_setting and notification_email_setting.setting_value:
                email_result = email_service.send_reference_number_refresh_notification(
                    to_email=notification_email_setting.setting_value,
                    schedule_name="Manual Refresh",
                    total_jobs=result['jobs_updated'],
                    refresh_details={
                        'jobs_updated': result['jobs_updated'],
                        'upload_status': 'Success' if upload_success else f'Failed: {upload_error_message}',
                        'processing_time': result['time_seconds']
                    },
                    status="success"
                )
                if email_result:
                    logger.info(f"📧 Manual refresh notification sent to {notification_email_setting.setting_value}")
                else:
                    logger.warning("Failed to send notification email")

        except Exception as email_error:
            logger.error(f"Email notification failed: {str(email_error)}")

        return jsonify({
            'success': True,
            'jobs_processed': result['jobs_updated'],
            'upload_success': upload_success,
            'upload_error': upload_error_message if not upload_success else None,
            'message': f'Successfully refreshed {result["jobs_updated"]} reference numbers using fresh Bullhorn data'
        })

    except Exception as e:
        logger.error(f"Error in manual reference number refresh: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@xml_routes_bp.route('/upload', methods=['POST'])
@login_required
def upload_file():
    """Handle file upload and processing with progress tracking"""
    try:
        from xml_processor import XMLProcessor
        from models import GlobalSettings

        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))

        file = request.files['file']

        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))

        if not allowed_file(file.filename):
            flash('Invalid file type. Please upload an XML file.', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))

        original_filename = secure_filename(file.filename or 'unknown.xml')
        unique_id = str(uuid.uuid4())[:8]
        input_filename = f"{unique_id}_{original_filename}"
        input_filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], input_filename)

        file.save(input_filepath)

        processor = XMLProcessor()

        if not processor.validate_xml(input_filepath):
            flash('Invalid XML file structure. Please check your file and try again.', 'error')
            os.remove(input_filepath)
            return redirect(url_for('ats_integration.ats_integration_dashboard'))

        output_filename = original_filename
        output_filepath = os.path.join(os.getcwd(), f"{unique_id}_{output_filename}")

        result = processor.process_xml(input_filepath, output_filepath, preserve_reference_numbers=True)

        os.remove(input_filepath)

        if result['success']:
            flash(f'Successfully processed {result["jobs_processed"]} jobs with unique reference numbers', 'success')

            sftp_uploaded = False
            try:
                sftp_settings = db.session.query(GlobalSettings).filter_by(setting_key='sftp_enabled').first()

                if sftp_settings and sftp_settings.setting_value and sftp_settings.setting_value.lower() == 'true':
                    hostname = db.session.query(GlobalSettings).filter_by(setting_key='sftp_hostname').first()
                    username = db.session.query(GlobalSettings).filter_by(setting_key='sftp_username').first()
                    password = db.session.query(GlobalSettings).filter_by(setting_key='sftp_password').first()
                    directory = db.session.query(GlobalSettings).filter_by(setting_key='sftp_directory').first()
                    port = db.session.query(GlobalSettings).filter_by(setting_key='sftp_port').first()

                    if all([hostname, username, password]) and all([
                        hostname and hostname.setting_value,
                        username and username.setting_value,
                        password and password.setting_value
                    ]):
                        from ftp_service import FTPService

                        ftp_service = FTPService(
                            hostname=hostname.setting_value,
                            username=username.setting_value,
                            password=password.setting_value,
                            target_directory=directory.setting_value if directory and directory.setting_value else "/",
                            port=int(port.setting_value) if port and port.setting_value else 2222,
                            use_sftp=True
                        )

                        upload_success = ftp_service.upload_file(output_filepath, original_filename)

                        if upload_success:
                            sftp_uploaded = True
                            flash(f'File processed and uploaded to server successfully!', 'success')
                        else:
                            flash(f'File processed but upload to server failed', 'warning')
                    else:
                        flash(f'File processed but SFTP credentials not configured', 'warning')
            except Exception as e:
                logger.error(f"SFTP upload error: {str(e)}")
                flash(f'File processed but upload to server failed: {str(e)}', 'warning')

            session_key = f"processed_file_{unique_id}"
            current_app.config[session_key] = {
                'filepath': output_filepath,
                'filename': output_filename,
                'jobs_processed': result['jobs_processed']
            }

            upload_id = unique_id

            progress_tracker[upload_id] = {
                'step': 'completed',
                'message': 'Processing complete!',
                'completed': True,
                'error': None,
                'download_key': unique_id,
                'filename': output_filename,
                'jobs_processed': result['jobs_processed'],
                'sftp_uploaded': sftp_uploaded
            }

            return render_template('index.html',
                                 download_key=unique_id,
                                 filename=output_filename,
                                 jobs_processed=result['jobs_processed'],
                                 sftp_uploaded=sftp_uploaded,
                                 manual_upload_id=upload_id,
                                 show_progress=True)
        else:
            flash(f'Error processing file: {result["error"]}', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))

    except Exception as e:
        logger.error(f"Error in upload_file: {str(e)}")
        flash(f'An error occurred while processing the file: {str(e)}', 'error')
        return redirect(url_for('ats_integration.ats_integration_dashboard'))


@xml_routes_bp.route('/manual-upload-progress/<upload_id>')
@login_required
def get_manual_upload_progress(upload_id):
    """Get real-time progress for manual upload processing"""
    try:
        if upload_id not in progress_tracker:
            return jsonify({'error': 'Upload not found'}), 404

        progress = progress_tracker[upload_id]

        response_data = {
            'step': progress['step'],
            'message': progress['message'],
            'completed': progress['completed'],
            'error': progress['error']
        }

        if progress['completed'] and progress['error'] is None:
            response_data['download_key'] = progress.get('download_key')
            response_data['filename'] = progress.get('filename')
            response_data['jobs_processed'] = progress.get('jobs_processed')
            response_data['sftp_uploaded'] = progress.get('sftp_uploaded', False)

        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error getting manual upload progress: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@xml_routes_bp.route('/download/<download_key>')
def download_file(download_key):
    """Download the processed file"""
    try:
        session_key = f"processed_file_{download_key}"

        if session_key not in current_app.config:
            flash('Download link has expired or is invalid', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))

        file_info = current_app.config[session_key]
        filepath = file_info['filepath']
        filename = file_info['filename']

        if not os.path.exists(filepath):
            flash('File not found', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))

        @after_this_request
        def remove_file(response):
            try:
                os.remove(filepath)
                del current_app.config[session_key]
            except Exception as e:
                logger.error(f"Error cleaning up file: {str(e)}")
            return response

        return send_file(filepath,
                        as_attachment=True,
                        download_name=filename,
                        mimetype='application/xml')

    except Exception as e:
        logger.error(f"Error in download_file: {str(e)}")
        flash(f'Error downloading file: {str(e)}', 'error')
        return redirect(url_for('ats_integration.ats_integration_dashboard'))


@xml_routes_bp.route('/download-current-xml')
@login_required
def download_current_xml():
    """Generate and download fresh XML from all Bullhorn tearsheets"""
    try:
        from models import GlobalSettings, BullhornActivity
        from xml_change_monitor import create_xml_monitor
        from utils.bullhorn_helpers import get_email_service

        logger.info("🚀 Starting fresh XML generation for download")

        from simplified_xml_generator import SimplifiedXMLGenerator

        generator = SimplifiedXMLGenerator(db=db)

        xml_content, stats = generator.generate_fresh_xml()

        try:
            logger.info("📧 Checking for job changes to include in download notification...")

            email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
            email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()

            if (email_enabled and email_enabled.setting_value == 'true' and
                email_setting and email_setting.setting_value):

                xml_monitor = create_xml_monitor()
                email_service = get_email_service()

                result = xml_monitor.monitor_xml_changes_with_content(
                    xml_content=xml_content,
                    notification_email=email_setting.setting_value,
                    email_service=email_service,
                    enable_email_notifications=True
                )

                if result.get('success'):
                    changes = result.get('changes', {})
                    total_changes = changes.get('total_changes', 0)
                    email_sent = result.get('email_sent', False)

                    if total_changes > 0:
                        if email_sent:
                            logger.info(f"📧 Download notification sent: {total_changes} job changes detected since last download")
                        else:
                            logger.info(f"📧 Download notification attempted: {total_changes} changes detected but email sending failed")

                        try:
                            activity_details = {
                                'monitor_type': 'Manual Download Notification',
                                'changes_detected': total_changes,
                                'added_jobs': changes.get('added', 0) if isinstance(changes.get('added'), int) else len(changes.get('added', [])),
                                'removed_jobs': changes.get('removed', 0) if isinstance(changes.get('removed'), int) else len(changes.get('removed', [])),
                                'modified_jobs': changes.get('modified', 0) if isinstance(changes.get('modified'), int) else len(changes.get('modified', [])),
                                'email_attempted_to': email_setting.setting_value[:10] + "...",
                                'email_sent': email_sent
                            }

                            xml_monitor_activity = BullhornActivity(
                                monitor_id=None,
                                activity_type='download_notification',
                                details=json.dumps(activity_details),
                                notification_sent=email_sent
                            )
                            db.session.add(xml_monitor_activity)
                            db.session.commit()

                            logger.info("📧 Manual download notification logged to Activity monitoring")

                        except Exception as e:
                            logger.error(f"Failed to log download notification activity: {str(e)}")
                            db.session.rollback()
                    else:
                        logger.info("📧 No job changes detected since last download - no notification sent")
                else:
                    logger.warning(f"📧 Download notification check failed: {result.get('error', 'Unknown error')}")
            else:
                if not email_enabled or email_enabled.setting_value != 'true':
                    logger.info("📧 Email notifications globally disabled - skipping download notification")
                else:
                    logger.info("📧 No notification email configured - skipping download notification")

        except Exception as e:
            logger.error(f"Error sending download notification: {str(e)}")

        temp_filename = f'myticas-job-feed-v2_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xml'
        temp_filepath = os.path.join(tempfile.gettempdir(), temp_filename)

        with open(temp_filepath, 'w', encoding='utf-8') as f:
            f.write(xml_content)

        logger.info(f"✅ Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")

        @after_this_request
        def remove_temp_file(response):
            try:
                os.remove(temp_filepath)
            except Exception as e:
                logger.error(f"Error cleaning up temp file: {str(e)}")
            return response

        return send_file(temp_filepath,
                        as_attachment=True,
                        download_name=temp_filename,
                        mimetype='application/xml')

    except Exception as e:
        logger.error(f"Error generating fresh XML: {str(e)}")
        flash(f'Error generating XML file: {str(e)}', 'error')
        return redirect(url_for('ats_integration.ats_integration_dashboard'))


@xml_routes_bp.route('/automation-status')
@login_required
def automation_status():
    """Get current automation status — DB-first for enabled state, scheduler for precise timing"""
    try:
        from models import GlobalSettings
        from datetime import datetime, timezone, timedelta

        UPLOAD_INTERVAL_MINUTES = 30
        upload_interval = f"{UPLOAD_INTERVAL_MINUTES} minutes"

        automation_setting = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
        db_setting_enabled = automation_setting and automation_setting.setting_value == 'true'

        last_upload_setting = GlobalSettings.query.filter_by(setting_key='last_sftp_upload_time').first()
        last_upload_raw = last_upload_setting.setting_value if last_upload_setting else None
        last_upload_time = last_upload_raw if last_upload_raw else "No uploads yet"

        next_upload_time = None
        next_upload_iso = None
        next_upload_timestamp = None

        if db_setting_enabled:
            # Primary: ask the scheduler — only works if this request hits the primary worker
            try:
                from app import scheduler
                job = scheduler.get_job('automated_upload')
                if job and job.next_run_time:
                    next_run = job.next_run_time
                    next_upload_time = next_run.strftime('%Y-%m-%d %H:%M:%S UTC')
                    next_upload_iso = next_run.isoformat()
                    next_upload_timestamp = int(next_run.timestamp() * 1000)
            except Exception:
                pass

            # Fallback: calculate from last upload time when scheduler isn't accessible
            if not next_upload_time:
                if last_upload_raw:
                    try:
                        last_dt = None
                        for fmt in ('%Y-%m-%d %H:%M:%S UTC', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
                            try:
                                last_dt = datetime.strptime(last_upload_raw.strip(), fmt).replace(tzinfo=timezone.utc)
                                break
                            except ValueError:
                                continue
                        if last_dt:
                            now_utc = datetime.now(timezone.utc)
                            next_dt = last_dt + timedelta(minutes=UPLOAD_INTERVAL_MINUTES)
                            if next_dt < now_utc:
                                next_dt = now_utc + timedelta(minutes=1)
                            next_upload_time = next_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                            next_upload_iso = next_dt.isoformat()
                            next_upload_timestamp = int(next_dt.timestamp() * 1000)
                    except Exception:
                        pass

                if not next_upload_time:
                    next_upload_time = 'Pending first run'

        automation_enabled = db_setting_enabled
        status = 'Active' if db_setting_enabled else 'Disabled'

        return jsonify({
            'automation_enabled': automation_enabled,
            'db_setting_enabled': db_setting_enabled,
            'next_upload_time': next_upload_time,
            'next_upload_iso': next_upload_iso,
            'next_upload_timestamp': next_upload_timestamp,
            'last_upload_time': last_upload_time,
            'upload_interval': upload_interval,
            'status': status
        })

    except Exception as e:
        logger.error(f"Error getting automation status: {str(e)}")
        return jsonify({'error': 'Failed to get automation status'}), 500


@xml_routes_bp.route('/test-upload', methods=['POST'])
@login_required
def manual_test_upload():
    """Manual upload testing for dev environment"""
    try:
        from models import GlobalSettings
        from tasks import automated_upload

        logger.info("🧪 Manual test upload initiated")

        from simplified_xml_generator import SimplifiedXMLGenerator
        generator = SimplifiedXMLGenerator(db=db)
        xml_content, stats = generator.generate_fresh_xml()

        logger.info(f"📊 Generated test XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")

        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
            return jsonify({
                'success': False,
                'error': 'SFTP not enabled in settings',
                'job_count': stats['job_count'],
                'xml_size': stats['xml_size_bytes']
            })

        upload_result = automated_upload()

        return jsonify({
            'success': True,
            'message': 'Test upload completed',
            'job_count': stats['job_count'],
            'xml_size': stats['xml_size_bytes'],
            'destination': 'configured SFTP directory',
            'note': 'Upload attempted - check logs for detailed results'
        })

    except Exception as e:
        logger.error(f"Manual test upload error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
        }), 500


@xml_routes_bp.route('/manual-upload-now', methods=['POST'])
@login_required
def manual_upload_now():
    """Manually trigger XML generation and SFTP upload"""
    try:
        from models import GlobalSettings
        from ftp_service import FTPService

        logger.info("📤 Manual upload triggered by user")

        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
            return jsonify({
                'success': False,
                'error': 'SFTP is not enabled. Please enable it in settings first.'
            })

        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
        sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()

        if not (sftp_hostname and sftp_hostname.setting_value and
                sftp_username and sftp_username.setting_value and
                sftp_password and sftp_password.setting_value):
            return jsonify({
                'success': False,
                'error': 'SFTP credentials not configured. Please fill in hostname, username, and password.'
            })

        from simplified_xml_generator import SimplifiedXMLGenerator
        generator = SimplifiedXMLGenerator(db=db)
        xml_content, stats = generator.generate_fresh_xml()

        logger.info(f"📊 Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")

        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8')
        temp_file.write(xml_content)
        temp_file.close()

        try:
            port_value = int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222
        except ValueError:
            port_value = 2222

        target_directory = sftp_directory.setting_value if sftp_directory else "/"

        ftp_service = FTPService(
            hostname=sftp_hostname.setting_value,
            username=sftp_username.setting_value,
            password=sftp_password.setting_value,
            target_directory=target_directory,
            port=port_value,
            use_sftp=True
        )

        upload_result = ftp_service.upload_file(temp_file.name, 'myticas-job-feed-v2.xml')

        try:
            os.remove(temp_file.name)
        except:
            pass

        if isinstance(upload_result, dict):
            if upload_result.get('success'):
                logger.info(f"✅ Manual upload successful: {upload_result.get('message', 'File uploaded')}")
                return jsonify({
                    'success': True,
                    'message': f"Successfully uploaded XML with {stats['job_count']} jobs ({stats['xml_size_bytes']:,} bytes)"
                })
            else:
                logger.error(f"❌ Manual upload failed: {upload_result.get('error', 'Unknown error')}")
                return jsonify({
                    'success': False,
                    'error': upload_result.get('error', 'Upload failed')
                })
        else:
            if upload_result:
                logger.info("✅ Manual upload successful")
                return jsonify({
                    'success': True,
                    'message': f"Successfully uploaded XML with {stats['job_count']} jobs ({stats['xml_size_bytes']:,} bytes)"
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Upload failed - check SFTP settings'
                })

    except Exception as e:
        logger.error(f"❌ Manual upload error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
        })


@xml_routes_bp.route('/validate', methods=['POST'])
@login_required
def validate_file():
    """Validate XML file structure without processing"""
    try:
        from xml_processor import XMLProcessor

        if 'file' not in request.files:
            return jsonify({'valid': False, 'error': 'No file uploaded'})

        file = request.files['file']

        if file.filename == '':
            return jsonify({'valid': False, 'error': 'No file selected'})

        if not allowed_file(file.filename):
            return jsonify({'valid': False, 'error': 'Invalid file type'})

        temp_filename = f"temp_{str(uuid.uuid4())[:8]}_{secure_filename(file.filename or 'unknown.xml')}"
        temp_filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], temp_filename)
        file.save(temp_filepath)

        processor = XMLProcessor()
        is_valid = processor.validate_xml(temp_filepath)

        job_count = 0
        if is_valid:
            job_count = processor.count_jobs(temp_filepath)

        os.remove(temp_filepath)

        return jsonify({
            'valid': is_valid,
            'job_count': job_count,
            'error': None if is_valid else 'Invalid XML structure'
        })

    except Exception as e:
        logger.error(f"Error in validate_file: {str(e)}")
        return jsonify({'valid': False, 'error': str(e)})


@xml_routes_bp.route('/bullhorn/oauth/callback')
def bullhorn_oauth_callback_redirect():
    """Permanent redirect for old OAuth callback URL - preserves query params"""
    return redirect(url_for('ats_integration.oauth_callback', **request.args), code=307)


@xml_routes_bp.route('/automation_test')
@login_required
def automation_test():
    """Automation test center page"""
    reset_test_file()
    return render_template('automation_test.html')


@xml_routes_bp.route('/automation_test', methods=['POST'])
def automation_test_action():
    """Handle automation test actions"""
    try:
        data = request.get_json()
        action = data.get('action')

        if action == 'complete_demo':
            result = run_automation_demo()
            return jsonify(result)

        elif action == 'add_jobs':
            return run_step_test('add_jobs')

        elif action == 'remove_jobs':
            return run_step_test('remove_jobs')

        elif action == 'update_jobs':
            return run_step_test('update_jobs')

        elif action == 'file_upload':
            return run_step_test('file_upload')

        elif action == 'show_xml':
            demo_file = 'demo_test_current.xml'
            if os.path.exists(demo_file):
                try:
                    with open(demo_file, 'r', encoding='utf-8') as f:
                        xml_content = f.read()
                    return jsonify({
                        'success': True,
                        'xml_content': xml_content
                    })
                except Exception as e:
                    return jsonify({
                        'success': False,
                        'error': f'Error reading demo file: {str(e)}'
                    })
            else:
                sample_xml = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
  <job>
    <title><![CDATA[ Senior Python Developer (12345) ]]></title>
    <company><![CDATA[ Tech Innovations Inc ]]></company>
    <date><![CDATA[ July 12, 2025 ]]></date>
    <referencenumber><![CDATA[REF1234567]]></referencenumber>
    <url><![CDATA[https://myticas.com/]]></url>
    <description><![CDATA[ Looking for a Senior Python Developer with Django experience... ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ San Francisco ]]></city>
    <state><![CDATA[ California ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[]]></remotetype>
  </job>
</source>'''
                return jsonify({
                    'success': True,
                    'xml_content': sample_xml,
                    'note': 'This is sample XML content. Run the Complete Demo first to see actual processed results.'
                })

        else:
            return jsonify({'success': False, 'error': 'Unknown action'})

    except Exception as e:
        logger.error(f"Error in automation test: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@xml_routes_bp.route('/test_download/<download_key>')
@login_required
def test_download(download_key):
    """Download test XML file"""
    try:
        cache_file = f"download_cache_{download_key}.json"
        if not os.path.exists(cache_file):
            flash('Download link expired or invalid', 'error')
            return redirect(url_for('xml_routes.automation_test'))

        with open(cache_file, 'r') as f:
            download_info = json.load(f)

        file_path = download_info['file_path']
        original_name = download_info['original_name']

        if not os.path.exists(file_path):
            flash('Test file not found', 'error')
            return redirect(url_for('xml_routes.automation_test'))

        os.remove(cache_file)

        return send_file(
            file_path,
            as_attachment=True,
            download_name=original_name,
            mimetype='application/xml'
        )

    except Exception as e:
        logger.error(f"Test download error: {str(e)}")
        flash('Download failed', 'error')
        return redirect(url_for('xml_routes.automation_test'))


def reset_test_file():
    """Reset the test file to its original clean state"""
    try:
        original_xml = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
  <job>
    <title><![CDATA[ Senior Python Developer (12345) ]]></title>
    <company><![CDATA[ Tech Innovations Inc ]]></company>
    <date><![CDATA[ July 12, 2024 ]]></date>
    <referencenumber><![CDATA[TYBVQ4DZSL]]></referencenumber>
    <url><![CDATA[ https://myticas.com/ ]]></url>
    <description><![CDATA[ Senior Python Developer with Django and FastAPI experience ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ San Francisco ]]></city>
    <state><![CDATA[ California ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[  ]]></remotetype>
  </job>
  <job>
    <title><![CDATA[ Initial Job (99999) ]]></title>
    <company><![CDATA[ Myticas Consulting ]]></company>
    <date><![CDATA[ July 12, 2025 ]]></date>
    <referencenumber><![CDATA[ZNLCP9YE8X]]></referencenumber>
    <url><![CDATA[https://myticas.com/]]></url>
    <description><![CDATA[ Initial test job ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ Chicago ]]></city>
    <state><![CDATA[ Illinois ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[]]></remotetype>
  </job>
</source>'''

        with open('demo_test_current.xml', 'w', encoding='utf-8') as f:
            f.write(original_xml)

        logger.info("Test file reset to original clean state")

    except Exception as e:
        logger.error(f"Error resetting test file: {str(e)}")


def run_automation_demo():
    """Run the complete automation demo and return results"""
    try:
        from xml_integration_service import XMLIntegrationService
        from xml_processor import XMLProcessor

        xml_service = XMLIntegrationService()
        xml_processor = XMLProcessor()

        demo_xml_file = 'demo_test_current.xml'

        initial_xml = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
  <job>
    <title><![CDATA[ Initial Job (99999) ]]></title>
    <company><![CDATA[ Myticas Consulting ]]></company>
    <date><![CDATA[ July 12, 2025 ]]></date>
    <referencenumber><![CDATA[INIT999999]]></referencenumber>
    <url><![CDATA[https://myticas.com/]]></url>
    <description><![CDATA[ Initial test job ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ Chicago ]]></city>
    <state><![CDATA[ Illinois ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[]]></remotetype>
  </job>
</source>'''

        with open(demo_xml_file, 'w', encoding='utf-8') as f:
            f.write(initial_xml)

        previous_jobs = []
        current_jobs = [
            {
                'id': 12345,
                'title': 'Senior Python Developer',
                'clientCorporation': {'name': 'Tech Innovations Inc'},
                'description': 'Senior Python Developer with Django experience',
                'address': {'city': 'San Francisco', 'state': 'California', 'countryName': 'United States'},
                'employmentType': 'Full-time',
                'dateAdded': 1720742400000
            },
            {
                'id': 67890,
                'title': 'DevOps Engineer',
                'clientCorporation': {'name': 'Cloud Solutions LLC'},
                'description': 'DevOps Engineer with AWS experience',
                'address': {'city': 'Seattle', 'state': 'Washington', 'countryName': 'United States'},
                'employmentType': 'Contract',
                'dateAdded': 1720742400000
            }
        ]

        sync_result = xml_service.sync_xml_with_bullhorn_jobs(
            xml_file_path=demo_xml_file,
            current_jobs=current_jobs,
            previous_jobs=previous_jobs
        )

        if sync_result.get('success'):
            temp_output = f"{demo_xml_file}.processed"
            process_result = xml_processor.process_xml(demo_xml_file, temp_output, preserve_reference_numbers=False)

            if process_result.get('success'):
                os.replace(temp_output, demo_xml_file)

                with open(demo_xml_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                job_count = len(re.findall(r'<job>', content))

                return {
                    'success': True,
                    'summary': f'Successfully processed {job_count} total jobs',
                    'jobs_added': sync_result.get('added_count', 0),
                    'jobs_removed': sync_result.get('removed_count', 0),
                    'jobs_updated': sync_result.get('updated_count', 0),
                    'total_jobs': job_count
                }
            else:
                if os.path.exists(demo_xml_file):
                    os.remove(demo_xml_file)
                return {
                    'success': False,
                    'error': f'XML processing failed: {process_result.get("error")}'
                }
        else:
            try:
                demo_xml_file_var = locals().get('demo_xml_file')
                if demo_xml_file_var and os.path.exists(demo_xml_file_var):
                    os.remove(demo_xml_file_var)
            except:
                pass
            return {
                'success': False,
                'error': f'XML sync failed: {sync_result.get("error")}'
            }

    except Exception as e:
        try:
            demo_xml_file_var = locals().get('demo_xml_file')
            if demo_xml_file_var and os.path.exists(demo_xml_file_var):
                os.remove(demo_xml_file_var)
        except:
            pass
        return {
            'success': False,
            'error': f'Demo failed: {str(e)}'
        }


def run_step_test(step_type):
    """Run individual step tests that modify the actual XML file"""
    try:
        from xml_integration_service import XMLIntegrationService
        from xml_processor import XMLProcessor
        from ftp_service import FTPService
        from models import GlobalSettings

        demo_xml_file = 'demo_test_current.xml'

        if not os.path.exists(demo_xml_file):
            initial_xml = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
  <job>
    <title><![CDATA[ Initial Test Job (99999) ]]></title>
    <company><![CDATA[ Myticas Consulting ]]></company>
    <date><![CDATA[ July 12, 2025 ]]></date>
    <referencenumber><![CDATA[INIT999999]]></referencenumber>
    <url><![CDATA[https://myticas.com/]]></url>
    <description><![CDATA[ Initial test job ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ Chicago ]]></city>
    <state><![CDATA[ Illinois ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[]]></remotetype>
  </job>
</source>'''
            with open(demo_xml_file, 'w', encoding='utf-8') as f:
                f.write(initial_xml)

        xml_service = XMLIntegrationService()
        xml_processor = XMLProcessor()

        with open(demo_xml_file, 'r', encoding='utf-8') as f:
            content = f.read()
        current_job_count = len(re.findall(r'<job>', content))

        if step_type == 'add_jobs':
            new_job = {
                'id': 55555,
                'title': 'Frontend React Developer',
                'clientCorporation': {'name': 'Digital Solutions Inc'},
                'description': 'Frontend React Developer with TypeScript experience',
                'address': {'city': 'Austin', 'state': 'Texas', 'countryName': 'United States'},
                'employmentType': 'Full-time',
                'dateAdded': 1720742400000
            }

            sync_result = xml_service.sync_xml_with_bullhorn_jobs(
                xml_file_path=demo_xml_file,
                current_jobs=[new_job],
                previous_jobs=[]
            )

            if sync_result.get('success'):
                    with open(demo_xml_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    new_job_count = len(re.findall(r'<job>', content))

                    return jsonify({
                        'success': True,
                        'details': f'Added Frontend React Developer (55555) to XML file. Jobs: {current_job_count} → {new_job_count}',
                        'jobs_added': 1,
                        'total_jobs': new_job_count
                    })

            return jsonify({
                'success': False,
                'error': 'Failed to add job to XML file'
            })

        elif step_type == 'remove_jobs':
            with open(demo_xml_file, 'r', encoding='utf-8') as f:
                content = f.read()

            if '55555' in content:
                job_pattern = r'<job>.*?Frontend React Developer \(55555\).*?</job>'
                new_content = re.sub(job_pattern, '', content, flags=re.DOTALL)

                with open(demo_xml_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)

                with open(demo_xml_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                new_job_count = len(re.findall(r'<job>', content))

                return jsonify({
                    'success': True,
                    'details': f'Removed Frontend React Developer (55555) from XML file. Jobs: {current_job_count} → {new_job_count}',
                    'jobs_removed': 1,
                    'total_jobs': new_job_count
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'No job found to remove. Try adding a job first.'
                })

        elif step_type == 'update_jobs':
            with open(demo_xml_file, 'r', encoding='utf-8') as f:
                content = f.read()

            if '12345' in content:
                updated_content = content.replace(
                    'Senior Python Developer (12345)',
                    'Senior Python Developer - UPDATED (12345)'
                )
                updated_content = updated_content.replace(
                    'Senior Python Developer with Django experience',
                    'Senior Python Developer with Django and FastAPI experience - UPDATED'
                )

                with open(demo_xml_file, 'w', encoding='utf-8') as f:
                    f.write(updated_content)

                return jsonify({
                    'success': True,
                    'details': 'Updated Senior Python Developer job with new title and description',
                    'jobs_updated': 1,
                    'total_jobs': current_job_count
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'No job found to update. Try running Complete Demo first.'
                })

        elif step_type == 'file_upload':
            if os.path.exists(demo_xml_file):
                processed_filename = f"test_processed_{int(time.time())}.xml"
                shutil.copy2(demo_xml_file, processed_filename)

                file_size = os.path.getsize(demo_xml_file)
                with open(demo_xml_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                job_count = len(re.findall(r'<job>', content))

                upload_success = False
                upload_message = ""

                try:
                    sftp_settings = {}
                    for key in ['sftp_hostname', 'sftp_username', 'sftp_password', 'sftp_directory']:
                        setting = GlobalSettings.query.filter_by(setting_key=key).first()
                        if setting:
                            sftp_settings[key] = setting.setting_value

                    if all(sftp_settings.get(key) for key in ['sftp_hostname', 'sftp_username', 'sftp_password']):
                        def timeout_handler(signum, frame):
                            raise TimeoutError("SFTP upload timed out")

                        signal.signal(signal.SIGALRM, timeout_handler)
                        signal.alarm(15)

                        try:
                            ftp_service = FTPService(
                                hostname=sftp_settings['sftp_hostname'],
                                username=sftp_settings['sftp_username'],
                                password=sftp_settings['sftp_password'],
                                target_directory=sftp_settings.get('sftp_directory', '/'),
                                use_sftp=True
                            )

                            upload_success = ftp_service.upload_file(demo_xml_file, 'test-automation-demo.xml')
                            upload_message = "Real SFTP upload completed" if upload_success else "SFTP upload failed"
                        except TimeoutError:
                            upload_message = "SFTP upload timed out - simulated upload for demo"
                            upload_success = True
                        finally:
                            signal.alarm(0)
                    else:
                        upload_message = "SFTP credentials not configured - simulated upload"
                        upload_success = True

                except Exception as e:
                    upload_message = f"SFTP upload error: {str(e)[:100]}... - simulated upload for demo"
                    upload_success = True

                download_key = str(uuid.uuid4())

                download_info = {
                    'file_path': processed_filename,
                    'original_name': 'test-automation-demo.xml',
                    'timestamp': time.time()
                }

                cache_file = f"download_cache_{download_key}.json"
                with open(cache_file, 'w') as f:
                    json.dump(download_info, f)

                return jsonify({
                    'success': True,
                    'details': f'{upload_message}. XML file ({file_size} bytes, {job_count} jobs) processed and available for download',
                    'uploaded': upload_success,
                    'file_size': file_size,
                    'job_count': job_count,
                    'download_key': download_key,
                    'download_url': f'/test_download/{download_key}'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'No XML file found to upload. Run Complete Demo first.'
                })

        else:
            return jsonify({
                'success': False,
                'error': 'Unknown step type'
            })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Step test failed: {str(e)}'
        })
