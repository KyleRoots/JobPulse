"""
Scheduler routes blueprint for JobPulse
Extracted from app.py for cleaner codebase organization.
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, current_app, flash
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import os
import uuid
import tempfile
import shutil
import time
import threading
import json

scheduler_bp = Blueprint('scheduler', __name__)

# Progress tracker for manual operations (module-level for thread access)
progress_tracker = {}


def update_progress(schedule_id, step, message, completed=False, error=None):
    """Update progress for a manual operation"""
    progress_tracker[f"schedule_{schedule_id}"] = {
        'step': step,
        'message': message,
        'completed': completed,
        'error': error,
        'timestamp': time.time()
    }


@scheduler_bp.route('/scheduler')
@login_required
def scheduler_dashboard():
    """Scheduling dashboard for automated processing"""
    from app import db
    from models import ScheduleConfig, ProcessingLog, RefreshLog
    
    # Get all active schedules
    schedules = ScheduleConfig.query.filter_by(is_active=True).all()
    
    # Add real-time file information for each schedule
    for schedule in schedules:
        if schedule.file_path and os.path.exists(schedule.file_path):
            file_stats = os.stat(schedule.file_path)
            schedule.actual_file_size = file_stats.st_size
            schedule.actual_last_modified = datetime.fromtimestamp(file_stats.st_mtime)
        else:
            schedule.actual_file_size = None
            schedule.actual_last_modified = None
    
    # Get information about the actively maintained XML files
    active_xml_files = []
    for filename in ['myticas-job-feed.xml']:
        if os.path.exists(filename):
            file_stats = os.stat(filename)
            
            # Try to find the schedule for this file
            schedule_for_file = None
            for schedule in schedules:
                if schedule.file_path == filename:
                    schedule_for_file = schedule
                    break
            
            # Use server upload time if available
            if schedule_for_file and schedule_for_file.last_file_upload:
                last_modified = schedule_for_file.last_file_upload
            else:
                last_modified = datetime.fromtimestamp(file_stats.st_mtime)
            
            file_size_kb = file_stats.st_size / 1024
            
            if file_stats.st_size == 280377:
                display_size = "273.8 KB"
            else:
                display_size = f"{file_size_kb:.1f} KB"
            
            if hasattr(last_modified, 'strftime'):
                server_time_dt = last_modified - timedelta(hours=4)
                server_time_str = server_time_dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                server_time_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            
            display_filename = "myticas-job-feed-v2.xml" if filename == "myticas-job-feed.xml" else filename
            
            active_xml_files.append({
                'filename': display_filename,
                'file_size': file_stats.st_size,
                'display_size': display_size,
                'last_modified': last_modified,
                'server_time': server_time_str,
                'is_active': True
            })
    
    # Get recent processing logs
    recent_logs = ProcessingLog.query.order_by(ProcessingLog.processed_at.desc()).limit(10).all()
    
    # Calculate next reference number refresh timestamp
    next_refresh_info = {
        'next_run': None,
        'last_run': None,
        'time_until_next': None,
        'hours_until_next': None
    }
    
    try:
        last_refresh = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()
        
        if last_refresh:
            next_refresh_time = last_refresh.refresh_time + timedelta(hours=120)
            time_until_next = next_refresh_time - datetime.utcnow()
            
            next_refresh_info['next_run'] = next_refresh_time
            next_refresh_info['last_run'] = last_refresh.refresh_time
            next_refresh_info['time_until_next'] = time_until_next
            next_refresh_info['hours_until_next'] = time_until_next.total_seconds() / 3600 if time_until_next.total_seconds() > 0 else 0
        else:
            next_refresh_info['next_run'] = datetime.utcnow() + timedelta(minutes=5)
            next_refresh_info['last_run'] = None
            next_refresh_info['time_until_next'] = timedelta(minutes=5)
            next_refresh_info['hours_until_next'] = 0.08
    except Exception as e:
        current_app.logger.warning(f"Could not calculate next refresh timestamp: {str(e)}")
    
    return render_template('scheduler.html', schedules=schedules, recent_logs=recent_logs, 
                         active_xml_files=active_xml_files, next_refresh_info=next_refresh_info, 
                         active_page='scheduler')


@scheduler_bp.route('/api/schedules', methods=['POST'])
def create_schedule():
    """Create a new automated processing schedule"""
    from app import db
    from models import ScheduleConfig
    
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data.get('name'):
            return jsonify({'success': False, 'error': 'Schedule name is required'}), 400
        
        if not data.get('file_path'):
            return jsonify({'success': False, 'error': 'File path is required'}), 400
        
        # Create the schedule
        schedule = ScheduleConfig(
            name=data['name'],
            file_path=data['file_path'],
            original_filename=data.get('original_filename'),
            interval_hours=data.get('interval_hours', 24),
            auto_upload_ftp=data.get('auto_upload_ftp', True),
            send_email_notifications=data.get('send_email_notifications', True),
            is_active=True,
            next_run=datetime.utcnow() + timedelta(hours=data.get('interval_hours', 24))
        )
        
        db.session.add(schedule)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'schedule_id': schedule.id,
            'message': f'Schedule "{schedule.name}" created successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating schedule: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@scheduler_bp.route('/api/schedules/<int:schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    """Delete a schedule"""
    from app import db
    from models import ScheduleConfig
    
    try:
        schedule = ScheduleConfig.query.get_or_404(schedule_id)
        schedule.is_active = False
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Schedule deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@scheduler_bp.route('/api/schedules/<int:schedule_id>/status', methods=['GET'])
def get_schedule_status(schedule_id):
    """Get the processing status of a schedule"""
    from models import ScheduleConfig, ProcessingLog
    
    try:
        schedule = ScheduleConfig.query.get_or_404(schedule_id)
        
        latest_log = ProcessingLog.query.filter_by(
            schedule_config_id=schedule_id
        ).order_by(ProcessingLog.processed_at.desc()).first()
        
        if latest_log:
            return jsonify({
                'success': True,
                'last_processed': latest_log.processed_at.isoformat(),
                'jobs_processed': latest_log.jobs_processed,
                'processing_success': latest_log.success,
                'error_message': latest_log.error_message
            })
        
        return jsonify({
            'success': True,
            'last_processed': None,
            'jobs_processed': 0,
            'processing_success': None,
            'error_message': None
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@scheduler_bp.route('/api/schedules/replace-file', methods=['POST'])
def replace_schedule_file():
    """Replace the XML file for an existing schedule"""
    from app import db, allowed_file, get_email_service
    from models import ScheduleConfig, GlobalSettings
    from xml_processor import XMLProcessor
    from ftp_service import FTPService
    
    try:
        schedule_id = request.form.get('schedule_id')
        if not schedule_id:
            return jsonify({'success': False, 'error': 'Schedule ID is required'}), 400
        
        schedule = ScheduleConfig.query.get_or_404(int(schedule_id))
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Only XML files are allowed'}), 400
        
        try:
            xml_processor = XMLProcessor()
            
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xml')
            file.save(temp_file.name)
            
            validation_result = xml_processor.validate_xml_detailed(temp_file.name)
            if not validation_result['valid']:
                os.unlink(temp_file.name)
                return jsonify({
                    'success': False, 
                    'error': f'Invalid XML structure: {validation_result["error"]}'
                }), 400
            
            if schedule.file_path and os.path.exists(schedule.file_path):
                os.unlink(schedule.file_path)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = secure_filename(file.filename) if file.filename else 'uploaded_file.xml'
            new_filename = f"{timestamp}_{filename}"
            
            scheduled_dir = os.path.join(tempfile.gettempdir(), 'scheduled_files')
            os.makedirs(scheduled_dir, exist_ok=True)
            
            new_filepath = os.path.join(scheduled_dir, new_filename)
            shutil.move(temp_file.name, new_filepath)
            
            schedule.file_path = new_filepath
            schedule.original_filename = filename
            schedule.updated_at = datetime.utcnow()
            schedule.last_file_upload = datetime.utcnow()
            
            db.session.commit()
            
            sftp_upload_success = False
            if schedule.auto_upload_ftp:
                try:
                    sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
                    sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
                    sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
                    sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
                    sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
                    
                    if (sftp_hostname and sftp_hostname.setting_value and 
                        sftp_username and sftp_username.setting_value and 
                        sftp_password and sftp_password.setting_value):
                        
                        ftp_service = FTPService(
                            hostname=sftp_hostname.setting_value,
                            username=sftp_username.setting_value,
                            password=sftp_password.setting_value,
                            target_directory=sftp_directory.setting_value if sftp_directory else "/",
                            port=int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222,
                            use_sftp=True
                        )
                        
                        sftp_upload_success = ftp_service.upload_file(
                            local_file_path=new_filepath,
                            remote_filename=filename
                        )
                except Exception as e:
                    current_app.logger.error(f"Error uploading replacement file to SFTP: {str(e)}")
            
            success_message = 'File replaced successfully'
            if sftp_upload_success:
                success_message += ' and uploaded to server'
            elif schedule.auto_upload_ftp:
                success_message += ' but failed to upload to server'
            
            return jsonify({
                'success': True,
                'message': success_message,
                'jobs_count': validation_result.get('jobs_count', 0),
                'sftp_uploaded': sftp_upload_success
            })
            
        except Exception as e:
            temp_file = locals().get('temp_file')
            if temp_file and hasattr(temp_file, 'name') and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                except:
                    pass
            raise e
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@scheduler_bp.route('/api/schedules/<int:schedule_id>/progress', methods=['GET'])
def get_schedule_progress(schedule_id):
    """Get real-time progress for manual schedule execution"""
    try:
        progress_key = f"schedule_{schedule_id}"
        progress = progress_tracker.get(progress_key, {
            'step': 0,
            'message': 'Ready to start...',
            'completed': False,
            'error': None
        })
        
        return jsonify({
            'success': True,
            **progress
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def process_schedule_with_progress(schedule_id):
    """Process a schedule with real-time progress updates"""
    from flask import current_app
    from app import db, get_email_service
    from models import ScheduleConfig, ProcessingLog, GlobalSettings
    from xml_processor import XMLProcessor
    from ftp_service import FTPService
    
    try:
        # Need app context for database access in thread
        from app import app
        with app.app_context():
            schedule = ScheduleConfig.query.get(schedule_id)
            if not schedule:
                update_progress(schedule_id, 0, "Schedule not found", completed=True, error="Schedule not found")
                return
            
            update_progress(schedule_id, 1, "Starting XML processing...")
            time.sleep(0.5)
            
            if not os.path.exists(schedule.file_path):
                update_progress(schedule_id, 1, "XML file not found", completed=True, error="XML file not found")
                return
            
            processor = XMLProcessor()
            update_progress(schedule_id, 1, "Processing XML file and updating reference numbers...")
            
            backup_path = f"{schedule.file_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(schedule.file_path, backup_path)
            
            temp_output = f"{schedule.file_path}.temp"
            
            result = processor.process_xml(schedule.file_path, temp_output, preserve_reference_numbers=True)
            
            if not result.get('success'):
                update_progress(schedule_id, 1, f"XML processing failed: {result.get('error', 'Unknown error')}", completed=True, error=result.get('error'))
                return
            
            jobs_processed = result.get('jobs_processed', 0)
            if jobs_processed == 0:
                update_progress(schedule_id, 1, "No jobs found to process", completed=True, error="No jobs found in XML file")
                return
            
            os.replace(temp_output, schedule.file_path)
            
            update_progress(schedule_id, 2, f"Processed {jobs_processed} jobs. Sending email notification...")
            time.sleep(0.5)
            
            original_filename = schedule.original_filename or os.path.basename(schedule.file_path).split('_', 1)[-1]
            
            time.sleep(0.5)
            update_progress(schedule_id, 2, "Uploading to WP Engine server...")
            
            sftp_upload_success = True
            if schedule.auto_upload_ftp:
                sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
                sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
                sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
                sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
                sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
                sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
                
                if (sftp_enabled and sftp_enabled.setting_value == 'true' and 
                    sftp_hostname and sftp_hostname.setting_value and 
                    sftp_username and sftp_username.setting_value and 
                    sftp_password and sftp_password.setting_value):
                    
                    ftp_service = FTPService(
                        hostname=sftp_hostname.setting_value,
                        username=sftp_username.setting_value,
                        password=sftp_password.setting_value,
                        target_directory=sftp_directory.setting_value if sftp_directory else "/",
                        port=int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222,
                        use_sftp=True
                    )
                    
                    sftp_upload_success = ftp_service.upload_file(
                        local_file_path=schedule.file_path,
                        remote_filename=original_filename
                    )
                    
                    if sftp_upload_success:
                        update_progress(schedule_id, 3, f"File uploaded successfully to {sftp_hostname.setting_value}")
                    else:
                        update_progress(schedule_id, 3, "File upload failed", error="Failed to upload to SFTP server")
                else:
                    sftp_upload_success = False
                    update_progress(schedule_id, 3, "SFTP upload requested but not configured", error="SFTP credentials not set in Global Settings")
            
            time.sleep(0.5)
            update_progress(schedule_id, 4, "Sending email notification...")
            
            if schedule.send_email_notifications:
                email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                email_address = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                
                if (email_enabled and email_enabled.setting_value == 'true' and 
                    email_address and email_address.setting_value):
                    
                    email_service = get_email_service()
                    email_sent = email_service.send_processing_notification(
                        to_email=email_address.setting_value,
                        schedule_name=schedule.name,
                        jobs_processed=jobs_processed,
                        xml_file_path=schedule.file_path,
                        original_filename=original_filename,
                        sftp_upload_success=sftp_upload_success
                    )
                    
                    if email_sent:
                        update_progress(schedule_id, 4, f"Email sent successfully to {email_address.setting_value}")
                    else:
                        update_progress(schedule_id, 4, "Email sending failed", error="Failed to send email notification")
                else:
                    update_progress(schedule_id, 4, "Email notification requested but not configured in Global Settings", error="Email credentials not set")
            
            time.sleep(0.5)
            update_progress(schedule_id, 5, "Processing completed successfully!", completed=True)
            
            log_entry = ProcessingLog(
                schedule_config_id=schedule.id,
                file_path=schedule.file_path,
                processing_type='manual',
                jobs_processed=jobs_processed,
                success=True,
                error_message=None
            )
            db.session.add(log_entry)
            
            schedule.last_run = datetime.utcnow()
            db.session.commit()
            
            time.sleep(0.5)
            update_progress(schedule_id, 4, f"Processing complete! {jobs_processed} jobs processed successfully.", completed=True)
            
    except Exception as e:
        current_app.logger.error(f"Error in manual processing: {str(e)}")
        update_progress(schedule_id, 0, f"Error: {str(e)}", completed=True, error=str(e))


@scheduler_bp.route('/api/schedules/<int:schedule_id>/run', methods=['POST'])
def run_schedule_now(schedule_id):
    """Manually trigger a schedule to run now"""
    from app import db
    from models import ScheduleConfig
    
    try:
        schedule = ScheduleConfig.query.get_or_404(schedule_id)
        
        # Clear any existing progress
        if f"schedule_{schedule_id}" in progress_tracker:
            del progress_tracker[f"schedule_{schedule_id}"]
        
        # Start processing in a separate thread
        thread = threading.Thread(target=process_schedule_with_progress, args=(schedule_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'message': 'Processing started',
            'schedule_id': schedule_id
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error running schedule manually: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@scheduler_bp.route('/api/upload-schedule-file', methods=['POST'])
@login_required
def upload_schedule_file():
    """Handle file upload for scheduling"""
    from app import allowed_file
    from xml_processor import XMLProcessor
    
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Invalid file type'}), 400
        
        # Create uploads directory if it doesn't exist
        from flask import current_app
        uploads_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'scheduled_files')
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Save file with secure filename
        filename = secure_filename(file.filename or 'unknown.xml')
        unique_id = str(uuid.uuid4())[:8]
        final_filename = f"{unique_id}_{filename}"
        file_path = os.path.join(uploads_dir, final_filename)
        
        file.save(file_path)
        
        # Validate XML
        processor = XMLProcessor()
        if not processor.validate_xml(file_path):
            os.remove(file_path)
            return jsonify({'success': False, 'error': 'Invalid XML file'}), 400
        
        job_count = processor.count_jobs(file_path)
        
        return jsonify({
            'success': True,
            'file_path': file_path,
            'filename': filename,
            'original_filename': file.filename,
            'job_count': job_count
        })
        
    except Exception as e:
        current_app.logger.error(f"Error uploading schedule file: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@scheduler_bp.route('/start_scheduler')
def start_scheduler_manual():
    """Manually start the scheduler and trigger monitoring"""
    from app import db, lazy_start_scheduler, scheduler, process_bullhorn_monitors
    from models import BullhornMonitor
    
    try:
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
