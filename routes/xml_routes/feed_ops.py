import os
import json
import uuid
import tempfile
import logging
from datetime import datetime, date
from flask import render_template, request, jsonify, redirect, url_for, flash, send_file, after_this_request, session, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from extensions import db
from routes.xml_routes import xml_routes_bp, allowed_file

logger = logging.getLogger(__name__)


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
                except Exception:
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

            from models import GlobalSettings
            GlobalSettings.set_value(f'upload_progress_{upload_id}', json.dumps({
                'step': 'completed',
                'message': 'Processing complete!',
                'completed': True,
                'error': None,
                'download_key': unique_id,
                'filename': output_filename,
                'jobs_processed': result['jobs_processed'],
                'sftp_uploaded': sftp_uploaded
            }))

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
    """Get real-time progress for manual upload processing (DB-backed)."""
    try:
        from models import GlobalSettings
        raw = GlobalSettings.get_value(f'upload_progress_{upload_id}')
        if not raw:
            return jsonify({'error': 'Upload not found'}), 404

        progress = json.loads(raw)

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
