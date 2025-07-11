import os
import logging
import json
from flask import Flask, render_template, request, send_file, flash, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import tempfile
import uuid
from xml_processor import XMLProcessor
from email_service import EmailService
from ftp_service import FTPService
from bullhorn_service import BullhornService
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
import atexit
import shutil
import threading
import time

# Configure logging
logging.basicConfig(level=logging.INFO)

# Global progress tracker for manual operations
progress_tracker = {}

class Base(DeclarativeBase):
    pass

# Create database instance
db = SQLAlchemy(model_class=Base)

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-12345")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

# Initialize database
db.init_app(app)


# Configuration
UPLOAD_FOLDER = tempfile.gettempdir()
ALLOWED_EXTENSIONS = {'xml'}
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max file size

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Import and initialize models
from models import create_models
ScheduleConfig, ProcessingLog, GlobalSettings, BullhornMonitor, BullhornActivity = create_models(db)

# Initialize database tables
with app.app_context():
    db.create_all()

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Cleanup scheduler on exit
atexit.register(lambda: scheduler.shutdown())

def allowed_file(filename):
    """Check if file has allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_scheduled_files():
    """Process all scheduled files that are due for processing"""
    with app.app_context():
        try:
            # Get all active schedules that are due
            now = datetime.utcnow()
            due_schedules = ScheduleConfig.query.filter(
                ScheduleConfig.is_active == True,
                ScheduleConfig.next_run <= now
            ).all()
            
            app.logger.info(f"Checking for scheduled files to process. Found {len(due_schedules)} due schedules")
            
            for schedule in due_schedules:
                app.logger.info(f"Processing schedule: {schedule.name} (ID: {schedule.id})")
                try:
                    # Check if file exists
                    if not os.path.exists(schedule.file_path):
                        app.logger.warning(f"Scheduled file not found: {schedule.file_path}")
                        continue
                    
                    # Process the file
                    processor = XMLProcessor()
                    
                    # Create backup of original file
                    backup_path = f"{schedule.file_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    shutil.copy2(schedule.file_path, backup_path)
                    
                    # Generate temporary output filename
                    temp_output = f"{schedule.file_path}.temp"
                    
                    # Process the XML
                    result = processor.process_xml(schedule.file_path, temp_output)
                    
                    # Log the processing result
                    log_entry = ProcessingLog(
                        schedule_config_id=schedule.id,
                        file_path=schedule.file_path,
                        processing_type='scheduled',
                        jobs_processed=result.get('jobs_processed', 0),
                        success=result.get('success', False),
                        error_message=result.get('error') if not result.get('success') else None
                    )
                    db.session.add(log_entry)
                    
                    # Update schedule
                    schedule.last_run = now
                    schedule.calculate_next_run()
                    
                    if result.get('success'):
                        # Replace original file with updated version
                        os.replace(temp_output, schedule.file_path)
                        app.logger.info(f"Successfully processed scheduled file: {schedule.file_path}")
                        
                        # Get original filename for email/FTP (use stored original filename if available)
                        original_filename = schedule.original_filename or os.path.basename(schedule.file_path).split('_', 1)[-1]
                        
                        # Upload to SFTP if configured (using Global Settings)
                        sftp_upload_success = True  # Default to success if not configured
                        if schedule.auto_upload_ftp:
                            try:
                                # Get SFTP settings from Global Settings
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
                                        app.logger.info(f"File uploaded to SFTP server: {original_filename}")
                                    else:
                                        app.logger.warning(f"Failed to upload file to SFTP server")
                                else:
                                    sftp_upload_success = False
                                    app.logger.warning(f"SFTP upload requested but credentials not configured in Global Settings")
                            except Exception as e:
                                sftp_upload_success = False
                                app.logger.error(f"Error uploading to SFTP: {str(e)}")
                        
                        # Send email notification if configured (using Global Settings)
                        if schedule.send_email_notifications:
                            try:
                                # Get email settings from Global Settings
                                email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                                email_address = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                                
                                if (email_enabled and email_enabled.setting_value == 'true' and 
                                    email_address and email_address.setting_value):
                                    
                                    email_service = EmailService()
                                    email_sent = email_service.send_processing_notification(
                                        to_email=email_address.setting_value,
                                        schedule_name=schedule.name,
                                        jobs_processed=result.get('jobs_processed', 0),
                                        xml_file_path=schedule.file_path,
                                        original_filename=original_filename,
                                        sftp_upload_success=sftp_upload_success
                                    )
                                    if email_sent:
                                        app.logger.info(f"Email notification sent successfully to {email_address.setting_value}")
                                    else:
                                        app.logger.warning(f"Failed to send email notification to {email_address.setting_value}")
                                else:
                                    app.logger.warning(f"Email notification requested but credentials not configured in Global Settings")
                            except Exception as e:
                                app.logger.error(f"Error sending email notification: {str(e)}")
                        
                    else:
                        # Clean up temp file on failure
                        if os.path.exists(temp_output):
                            os.remove(temp_output)
                        app.logger.error(f"Failed to process scheduled file: {schedule.file_path} - {result.get('error')}")
                        
                        # Send error notification email if configured (using Global Settings)
                        if schedule.send_email_notifications:
                            try:
                                # Get email settings from Global Settings
                                email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                                email_address = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                                
                                if (email_enabled and email_enabled.setting_value == 'true' and 
                                    email_address and email_address.setting_value):
                                    
                                    email_service = EmailService()
                                    email_service.send_processing_error_notification(
                                        to_email=email_address.setting_value,
                                        schedule_name=schedule.name,
                                        error_message=result.get('error', 'Unknown error')
                                    )
                                else:
                                    app.logger.warning(f"Error email notification requested but credentials not configured in Global Settings")
                            except Exception as e:
                                app.logger.error(f"Error sending error notification email: {str(e)}")
                    
                except Exception as e:
                    app.logger.error(f"Error processing scheduled file {schedule.file_path}: {str(e)}")
                    
                    # Log the error
                    log_entry = ProcessingLog(
                        schedule_config_id=schedule.id,
                        file_path=schedule.file_path,
                        processing_type='scheduled',
                        jobs_processed=0,
                        success=False,
                        error_message=str(e)
                    )
                    db.session.add(log_entry)
            
            db.session.commit()
            
        except Exception as e:
            app.logger.error(f"Error in scheduled processing: {str(e)}")
            db.session.rollback()

# Add the scheduled job to check every 5 minutes
scheduler.add_job(
    func=process_scheduled_files,
    trigger=IntervalTrigger(minutes=5),
    id='process_scheduled_files',
    name='Process Scheduled XML Files',
    replace_existing=True
)

def process_bullhorn_monitors():
    """Process all active Bullhorn monitors for tearsheet changes"""
    with app.app_context():
        try:
            current_time = datetime.utcnow()
            
            # Get all active monitors that are due for checking
            due_monitors = BullhornMonitor.query.filter(
                BullhornMonitor.is_active == True,
                BullhornMonitor.next_check <= current_time
            ).all()
            
            app.logger.info(f"Checking Bullhorn monitors. Found {len(due_monitors)} due monitors")
            
            for monitor in due_monitors:
                app.logger.info(f"Processing Bullhorn monitor: {monitor.name} (ID: {monitor.id})")
                try:
                    # Initialize Bullhorn service
                    bullhorn_service = BullhornService()
                    
                    if not bullhorn_service.test_connection():
                        app.logger.warning(f"Bullhorn connection failed for monitor: {monitor.name}")
                        # Log the error
                        activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='error',
                            details='Failed to connect to Bullhorn API'
                        )
                        db.session.add(activity)
                        continue
                    
                    # Get current jobs based on monitor type
                    if monitor.tearsheet_id == 0:
                        # Query-based monitor
                        current_jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                    else:
                        # Traditional tearsheet-based monitor
                        current_jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                    
                    # Compare with previous snapshot if it exists
                    previous_jobs = []
                    if monitor.last_job_snapshot:
                        try:
                            previous_jobs = json.loads(monitor.last_job_snapshot)
                        except json.JSONDecodeError:
                            app.logger.warning(f"Failed to parse job snapshot for monitor: {monitor.name}")
                    
                    # Find changes
                    changes = bullhorn_service.compare_job_lists(previous_jobs, current_jobs)
                    added_jobs = changes.get('added', [])
                    removed_jobs = changes.get('removed', [])
                    modified_jobs = changes.get('modified', [])
                    summary = changes.get('summary', {})
                    
                    # Log activities
                    for job in added_jobs:
                        activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='job_added',
                            job_id=job.get('id'),
                            job_title=job.get('title'),
                            details=json.dumps(job)
                        )
                        db.session.add(activity)
                    
                    for job in removed_jobs:
                        activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='job_removed',
                            job_id=job.get('id'),
                            job_title=job.get('title'),
                            details=json.dumps(job)
                        )
                        db.session.add(activity)
                    
                    for job in modified_jobs:
                        activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='job_modified',
                            job_id=job.get('id'),
                            job_title=job.get('title'),
                            details=json.dumps(job)
                        )
                        db.session.add(activity)
                    
                    # Log a summary activity for batch updates
                    if added_jobs or removed_jobs or modified_jobs:
                        summary_activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='check_completed',
                            details=json.dumps({
                                'summary': summary,
                                'changes_detected': True,
                                'timestamp': datetime.utcnow().isoformat()
                            })
                        )
                        db.session.add(summary_activity)
                        
                        app.logger.info(f"Bullhorn monitor {monitor.name}: {summary.get('added_count', 0)} added, {summary.get('removed_count', 0)} removed, {summary.get('modified_count', 0)} modified")
                    
                    # Send email notification if there are changes and notifications are enabled
                    if (added_jobs or removed_jobs or modified_jobs) and monitor.send_notifications:
                        # Get email address from Global Settings or monitor-specific setting
                        email_address = monitor.notification_email
                        if not email_address:
                            # Fall back to global notification email
                            global_email = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                            if global_email:
                                email_address = global_email.setting_value
                        
                        if email_address:
                            email_service = EmailService()
                            email_sent = email_service.send_bullhorn_notification(
                                to_email=email_address,
                                monitor_name=monitor.name,
                                added_jobs=added_jobs,
                                removed_jobs=removed_jobs,
                                modified_jobs=modified_jobs,
                                summary=summary
                            )
                            
                            if email_sent:
                                app.logger.info(f"Bullhorn notification sent for monitor: {monitor.name}")
                                # Mark activities as notified
                                for activity in db.session.new:
                                    if isinstance(activity, BullhornActivity) and activity.monitor_id == monitor.id:
                                        activity.notification_sent = True
                            else:
                                app.logger.warning(f"Failed to send Bullhorn notification for monitor: {monitor.name}")
                    
                    # Update monitor with new snapshot and next check time
                    monitor.last_job_snapshot = json.dumps(current_jobs)
                    monitor.last_check = current_time
                    monitor.calculate_next_check()
                    
                    # Log successful check (only if no changes were already logged)
                    if not (added_jobs or removed_jobs or modified_jobs):
                        activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='check_completed',
                            details=f"Checked query: {monitor.tearsheet_name}. Found {len(current_jobs)} jobs. No changes detected."
                        )
                        db.session.add(activity)
                    
                    app.logger.info(f"Successfully processed Bullhorn monitor: {monitor.name}")
                    
                except Exception as e:
                    app.logger.error(f"Error processing Bullhorn monitor {monitor.name}: {str(e)}")
                    # Log the error
                    activity = BullhornActivity(
                        monitor_id=monitor.id,
                        activity_type='error',
                        details=f"Error: {str(e)}"
                    )
                    db.session.add(activity)
                    
                    # Still update the next check time to avoid repeated failures
                    monitor.last_check = current_time
                    monitor.calculate_next_check()
            
            # Commit all changes
            db.session.commit()
            
        except Exception as e:
            app.logger.error(f"Error in Bullhorn monitor processing: {str(e)}")
            db.session.rollback()

# Add Bullhorn monitoring to scheduler
scheduler.add_job(
    func=process_bullhorn_monitors,
    trigger=IntervalTrigger(minutes=5),
    id='process_bullhorn_monitors',
    name='Process Bullhorn Monitors',
    replace_existing=True
)

@app.route('/')
def index():
    """Main page with file upload form"""
    return render_template('index.html')

@app.route('/scheduler')
def scheduler_dashboard():
    """Scheduling dashboard for automated processing"""
    # Get all active schedules
    schedules = ScheduleConfig.query.filter_by(is_active=True).all()
    
    # Get recent processing logs
    recent_logs = ProcessingLog.query.order_by(ProcessingLog.processed_at.desc()).limit(10).all()
    
    return render_template('scheduler.html', schedules=schedules, recent_logs=recent_logs)

@app.route('/api/schedules', methods=['POST'])
def create_schedule():
    """Create a new automated processing schedule"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'file_path', 'schedule_days']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
        
        # Validate file exists
        if not os.path.exists(data['file_path']):
            return jsonify({'success': False, 'error': 'File does not exist'}), 400
        
        # Validate XML file
        processor = XMLProcessor()
        if not processor.validate_xml(data['file_path']):
            return jsonify({'success': False, 'error': 'Invalid XML file'}), 400
        
        # Create new schedule
        schedule = ScheduleConfig(
            name=data['name'],
            file_path=data['file_path'],
            original_filename=data.get('original_filename'),
            schedule_days=int(data['schedule_days']),
            # Email notification settings (always enabled, uses Global Settings)
            send_email_notifications=True,
            notification_email=None,  # Will use Global Settings email
            # Auto-upload settings (always enabled, uses Global Settings)
            auto_upload_ftp=True,
            last_file_upload=datetime.utcnow()  # Track when file was initially uploaded
        )
        schedule.calculate_next_run()
        
        db.session.add(schedule)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Schedule created successfully',
            'schedule_id': schedule.id
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error creating schedule: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedules/<int:schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    """Delete a schedule"""
    try:
        schedule = ScheduleConfig.query.get_or_404(schedule_id)
        schedule.is_active = False
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Schedule deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedules/<int:schedule_id>/status', methods=['GET'])
def get_schedule_status(schedule_id):
    """Get the processing status of a schedule"""
    try:
        schedule = ScheduleConfig.query.get_or_404(schedule_id)
        
        # Get the latest processing log for this schedule
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
        else:
            return jsonify({
                'success': True,
                'last_processed': None,
                'jobs_processed': 0,
                'processing_success': None,
                'error_message': None
            })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedules/replace-file', methods=['POST'])
def replace_schedule_file():
    """Replace the XML file for an existing schedule"""
    try:
        schedule_id = request.form.get('schedule_id')
        if not schedule_id:
            return jsonify({'success': False, 'error': 'Schedule ID is required'}), 400
        
        schedule = ScheduleConfig.query.get_or_404(int(schedule_id))
        
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Only XML files are allowed'}), 400
        
        # Validate XML structure
        try:
            xml_processor = XMLProcessor()
            
            # Create a temporary copy for validation
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xml')
            file.save(temp_file.name)
            
            # Validate the XML structure
            validation_result = xml_processor.validate_xml_detailed(temp_file.name)
            if not validation_result['valid']:
                os.unlink(temp_file.name)
                return jsonify({
                    'success': False, 
                    'error': f'Invalid XML structure: {validation_result["error"]}'
                }), 400
            
            # If old file exists, remove it
            if schedule.file_path and os.path.exists(schedule.file_path):
                os.unlink(schedule.file_path)
            
            # Generate new secure filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = secure_filename(file.filename) if file.filename else 'uploaded_file.xml'
            new_filename = f"{timestamp}_{filename}"
            
            # Create scheduled files directory if it doesn't exist
            scheduled_dir = os.path.join(tempfile.gettempdir(), 'scheduled_files')
            os.makedirs(scheduled_dir, exist_ok=True)
            
            # Move the validated file to the scheduled directory
            new_filepath = os.path.join(scheduled_dir, new_filename)
            shutil.move(temp_file.name, new_filepath)
            
            # Update the schedule with new file path
            schedule.file_path = new_filepath
            schedule.original_filename = filename  # Store original filename
            schedule.updated_at = datetime.utcnow()
            schedule.last_file_upload = datetime.utcnow()  # Track when file was uploaded/replaced
            
            db.session.commit()
            
            # Immediately upload the new file to SFTP if configured
            sftp_upload_success = False
            if schedule.auto_upload_ftp:
                try:
                    # Get SFTP settings from Global Settings
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
                        
                        # Upload the new file (without reference number processing)
                        sftp_upload_success = ftp_service.upload_file(
                            local_file_path=new_filepath,
                            remote_filename=filename  # Use original filename
                        )
                        
                        if sftp_upload_success:
                            app.logger.info(f"File replacement uploaded to SFTP server: {filename}")
                        else:
                            app.logger.warning(f"Failed to upload replacement file to SFTP server")
                    else:
                        app.logger.warning(f"SFTP upload requested but credentials not configured in Global Settings")
                except Exception as e:
                    app.logger.error(f"Error uploading replacement file to SFTP: {str(e)}")
            
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
            # Clean up temporary file if it exists
            if 'temp_file' in locals() and os.path.exists(temp_file.name):
                os.unlink(temp_file.name)
            raise e
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

def update_progress(schedule_id, step, message, completed=False, error=None):
    """Update progress for a manual operation"""
    progress_tracker[f"schedule_{schedule_id}"] = {
        'step': step,
        'message': message,
        'completed': completed,
        'error': error,
        'timestamp': time.time()
    }

@app.route('/api/schedules/<int:schedule_id>/progress', methods=['GET'])
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
    try:
        with app.app_context():
            schedule = ScheduleConfig.query.get(schedule_id)
            if not schedule:
                update_progress(schedule_id, 0, "Schedule not found", completed=True, error="Schedule not found")
                return
            
            update_progress(schedule_id, 1, "Starting XML processing...")
            time.sleep(0.5)  # Brief pause for user to see
            
            if not os.path.exists(schedule.file_path):
                update_progress(schedule_id, 1, "XML file not found", completed=True, error="XML file not found")
                return
            
            # Process the XML file
            processor = XMLProcessor()
            update_progress(schedule_id, 1, "Processing XML file and updating reference numbers...")
            
            # Create backup
            backup_path = f"{schedule.file_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(schedule.file_path, backup_path)
            
            # Generate temporary output
            temp_output = f"{schedule.file_path}.temp"
            
            # Process the XML
            result = processor.process_xml(schedule.file_path, temp_output)
            
            if not result.get('success'):
                update_progress(schedule_id, 1, f"XML processing failed: {result.get('error', 'Unknown error')}", completed=True, error=result.get('error'))
                return
            
            jobs_processed = result.get('jobs_processed', 0)
            if jobs_processed == 0:
                update_progress(schedule_id, 1, "No jobs found to process", completed=True, error="No jobs found in XML file")
                return
            
            # Replace original file with updated version
            os.replace(temp_output, schedule.file_path)
            
            update_progress(schedule_id, 2, f"Processed {jobs_processed} jobs. Sending email notification...")
            time.sleep(0.5)
            
            # Get original filename for email/FTP
            original_filename = schedule.original_filename or os.path.basename(schedule.file_path).split('_', 1)[-1]
            
            time.sleep(0.5)
            update_progress(schedule_id, 2, "Uploading to WP Engine server...")
            
            # Upload to SFTP if enabled (using Global Settings)
            sftp_upload_success = True  # Default to success if not configured
            if schedule.auto_upload_ftp:
                # Get SFTP settings from Global Settings
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
            
            # Send email notification if enabled (using Global Settings)
            if schedule.send_email_notifications:
                # Get email settings from Global Settings
                email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                email_address = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                
                if (email_enabled and email_enabled.setting_value == 'true' and 
                    email_address and email_address.setting_value):
                    
                    email_service = EmailService()
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
            
            # Log the processing
            log_entry = ProcessingLog(
                schedule_config_id=schedule.id,
                file_path=schedule.file_path,
                processing_type='manual',
                jobs_processed=jobs_processed,
                success=True,
                error_message=None
            )
            db.session.add(log_entry)
            
            # Update schedule last run time
            schedule.last_run = datetime.utcnow()
            db.session.commit()
            
            time.sleep(0.5)
            # Mark as completed
            update_progress(schedule_id, 4, f"Processing complete! {jobs_processed} jobs processed successfully.", completed=True)
            
    except Exception as e:
        app.logger.error(f"Error in manual processing: {str(e)}")
        update_progress(schedule_id, 0, f"Error: {str(e)}", completed=True, error=str(e))

@app.route('/api/schedules/<int:schedule_id>/run', methods=['POST'])
def run_schedule_now(schedule_id):
    """Manually trigger a schedule to run now"""
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
        return jsonify({'success': False, 'error': str(e)}), 500
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error running schedule manually: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/upload-schedule-file', methods=['POST'])
def upload_schedule_file():
    """Handle file upload for scheduling"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Invalid file type'}), 400
        
        # Create uploads directory if it doesn't exist
        uploads_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'scheduled_files')
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
        app.logger.error(f"Error uploading schedule file: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and processing with progress tracking"""
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('index'))
        
        file = request.files['file']
        
        # Check if file was actually selected
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('index'))
        
        # Check file extension
        if not allowed_file(file.filename):
            flash('Invalid file type. Please upload an XML file.', 'error')
            return redirect(url_for('index'))
        
        # Generate unique filename
        original_filename = secure_filename(file.filename)
        unique_id = str(uuid.uuid4())[:8]
        input_filename = f"{unique_id}_{original_filename}"
        input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], input_filename)
        
        # Save uploaded file
        file.save(input_filepath)
        
        # Process the XML file
        processor = XMLProcessor()
        
        # Validate XML structure
        if not processor.validate_xml(input_filepath):
            flash('Invalid XML file structure. Please check your file and try again.', 'error')
            os.remove(input_filepath)
            return redirect(url_for('index'))
        
        # Generate output filename (preserve original name without "updated_" prefix)
        output_filename = original_filename
        # Try using current working directory instead of temp folder
        output_filepath = os.path.join(os.getcwd(), f"{unique_id}_{output_filename}")
        app.logger.info(f"Using output path: {output_filepath}")
        
        # Process the file
        result = processor.process_xml(input_filepath, output_filepath)
        
        # Clean up input file
        os.remove(input_filepath)
        
        # Debug: Check if output file exists immediately after processing
        import time
        app.logger.info(f"Output file path: {output_filepath}")
        app.logger.info(f"Output file exists immediately: {os.path.exists(output_filepath)}")
        
        # Brief pause to check if timing issue
        time.sleep(0.1)
        app.logger.info(f"Output file exists after 0.1s: {os.path.exists(output_filepath)}")
        
        # List files in temp directory to see what's there
        temp_files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) if 'myticas-job-feed-dice.xml' in f]
        app.logger.info(f"Files in temp directory containing 'myticas-job-feed-dice.xml': {temp_files}")
        
        if result['success']:
            flash(f'Successfully processed {result["jobs_processed"]} jobs with unique reference numbers', 'success')
            
            # Get global SFTP settings for automatic upload
            sftp_uploaded = False
            try:
                sftp_settings = db.session.query(GlobalSettings).filter_by(setting_key='sftp_enabled').first()
                app.logger.info(f"SFTP enabled setting: {sftp_settings.setting_value if sftp_settings else 'None'}")
                
                if sftp_settings and sftp_settings.setting_value.lower() == 'true':
                    # Get SFTP credentials
                    hostname = db.session.query(GlobalSettings).filter_by(setting_key='sftp_hostname').first()
                    username = db.session.query(GlobalSettings).filter_by(setting_key='sftp_username').first()
                    password = db.session.query(GlobalSettings).filter_by(setting_key='sftp_password').first()
                    directory = db.session.query(GlobalSettings).filter_by(setting_key='sftp_directory').first()
                    port = db.session.query(GlobalSettings).filter_by(setting_key='sftp_port').first()
                    
                    app.logger.info(f"SFTP credentials check - hostname: {hostname is not None}, username: {username is not None}, password: {password is not None}")
                    
                    if all([hostname, username, password]):
                        from ftp_service import FTPService
                        
                        ftp_service = FTPService(
                            hostname=hostname.setting_value,
                            username=username.setting_value,
                            password=password.setting_value,
                            target_directory=directory.setting_value if directory else "/",
                            port=int(port.setting_value) if port else 2222,
                            use_sftp=True
                        )
                        
                        app.logger.info(f"Attempting SFTP upload to {hostname.setting_value}")
                        app.logger.info(f"Upload file check - file exists: {os.path.exists(output_filepath)}")
                        # Upload file with original name
                        upload_success = ftp_service.upload_file(output_filepath, original_filename)
                        
                        if upload_success:
                            sftp_uploaded = True
                            flash(f'File processed and uploaded to server successfully!', 'success')
                        else:
                            flash(f'File processed but upload to server failed', 'warning')
                    else:
                        flash(f'File processed but SFTP credentials not configured', 'warning')
                else:
                    app.logger.info("SFTP upload disabled or not configured")
            except Exception as e:
                app.logger.error(f"SFTP upload error: {str(e)}")
                flash(f'File processed but upload to server failed: {str(e)}', 'warning')
            
            # Store output file info in session for download
            session_key = f"processed_file_{unique_id}"
            app.config[session_key] = {
                'filepath': output_filepath,
                'filename': output_filename,
                'jobs_processed': result['jobs_processed']
            }
            
            # Generate a manual upload ID for progress tracking
            upload_id = unique_id
            
            # Initialize progress tracking for this upload
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
            return redirect(url_for('index'))
            
    except Exception as e:
        app.logger.error(f"Error in upload_file: {str(e)}")
        flash(f'An error occurred while processing the file: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/manual-upload-progress/<upload_id>')
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
        
        # If completed, add download information
        if progress['completed'] and progress['error'] is None:
            response_data['download_key'] = progress.get('download_key')
            response_data['filename'] = progress.get('filename')
            response_data['jobs_processed'] = progress.get('jobs_processed')
            response_data['sftp_uploaded'] = progress.get('sftp_uploaded', False)
        
        return jsonify(response_data)
        
    except Exception as e:
        app.logger.error(f"Error getting manual upload progress: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/download/<download_key>')
def download_file(download_key):
    """Download the processed file"""
    try:
        session_key = f"processed_file_{download_key}"
        
        if session_key not in app.config:
            flash('Download link has expired or is invalid', 'error')
            return redirect(url_for('index'))
        
        file_info = app.config[session_key]
        filepath = file_info['filepath']
        filename = file_info['filename']
        
        if not os.path.exists(filepath):
            flash('File not found', 'error')
            return redirect(url_for('index'))
        
        # Send file and clean up
        from flask import after_this_request
        
        @after_this_request
        def remove_file(response):
            try:
                os.remove(filepath)
                del app.config[session_key]
            except Exception as e:
                app.logger.error(f"Error cleaning up file: {str(e)}")
            return response
        
        return send_file(filepath, 
                        as_attachment=True, 
                        download_name=filename,
                        mimetype='application/xml')
        
    except Exception as e:
        app.logger.error(f"Error in download_file: {str(e)}")
        flash(f'Error downloading file: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/settings')
def settings():
    """Global settings page for SFTP and email configuration"""
    try:
        # Get current settings
        settings_data = {}
        setting_keys = [
            'sftp_hostname', 'sftp_username', 'sftp_directory', 'sftp_port', 'sftp_enabled',
            'email_notifications_enabled', 'default_notification_email'
        ]
        
        for key in setting_keys:
            setting = db.session.query(GlobalSettings).filter_by(setting_key=key).first()
            settings_data[key] = setting.setting_value if setting else ''
        
        return render_template('settings.html', settings=settings_data)
        
    except Exception as e:
        app.logger.error(f"Error loading settings: {str(e)}")
        flash('Error loading settings', 'error')
        return redirect(url_for('index'))

@app.route('/settings', methods=['POST'])
def update_settings():
    """Update global settings"""
    try:
        # Update SFTP settings
        sftp_settings = {
            'sftp_enabled': 'true' if request.form.get('sftp_enabled') == 'on' else 'false',
            'sftp_hostname': request.form.get('sftp_hostname', ''),
            'sftp_username': request.form.get('sftp_username', ''),
            'sftp_password': request.form.get('sftp_password', ''),
            'sftp_directory': request.form.get('sftp_directory', '/'),
            'sftp_port': request.form.get('sftp_port', '2222')
        }
        
        # Update email settings
        email_settings = {
            'email_notifications_enabled': 'true' if request.form.get('email_notifications_enabled') == 'on' else 'false',
            'default_notification_email': request.form.get('default_notification_email', '')
        }
        
        # Combine all settings
        all_settings = {**sftp_settings, **email_settings}
        
        # Save to database
        for key, value in all_settings.items():
            # Skip password if empty (preserve existing password)
            if key == 'sftp_password' and not value:
                continue
                
            setting = db.session.query(GlobalSettings).filter_by(setting_key=key).first()
            
            if setting:
                setting.setting_value = str(value)
                setting.updated_at = datetime.utcnow()
            else:
                setting = GlobalSettings(
                    setting_key=key,
                    setting_value=str(value)
                )
                db.session.add(setting)
        
        db.session.commit()
        flash('Settings updated successfully!', 'success')
        
        return redirect(url_for('settings'))
        
    except Exception as e:
        app.logger.error(f"Error updating settings: {str(e)}")
        db.session.rollback()
        flash(f'Error updating settings: {str(e)}', 'error')
        return redirect(url_for('settings'))

@app.route('/test-sftp-connection', methods=['POST'])
def test_sftp_connection():
    """Test SFTP connection with form data"""
    try:
        # Get form data from request
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No connection data provided'
            })
        
        hostname = data.get('sftp_hostname', '').strip()
        username = data.get('sftp_username', '').strip()
        password = data.get('sftp_password', '').strip()
        directory = data.get('sftp_directory', '/').strip()
        port = data.get('sftp_port', '2222')
        
        if not all([hostname, username, password]):
            return jsonify({
                'success': False,
                'error': 'Please fill in hostname, username, and password fields.'
            })
        
        # Convert port to integer
        try:
            port = int(port) if port else 2222
        except ValueError:
            port = 2222
        
        # Test connection
        ftp_service = FTPService(
            hostname=hostname,
            username=username,
            password=password,
            target_directory=directory,
            port=port,
            use_sftp=True
        )
        
        app.logger.info(f"Testing SFTP connection to {hostname}:{port} with user {username}")
        success = ftp_service.test_connection()
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Successfully connected to {hostname} on port {port}!'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to connect. Please check your credentials and try again.'
            })
        
    except Exception as e:
        app.logger.error(f"Error testing SFTP connection: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Connection test failed: {str(e)}'
        })

@app.route('/validate', methods=['POST'])
def validate_file():
    """Validate XML file structure without processing"""
    try:
        if 'file' not in request.files:
            return jsonify({'valid': False, 'error': 'No file uploaded'})
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'valid': False, 'error': 'No file selected'})
        
        if not allowed_file(file.filename):
            return jsonify({'valid': False, 'error': 'Invalid file type'})
        
        # Save temporary file for validation
        temp_filename = f"temp_{str(uuid.uuid4())[:8]}_{secure_filename(file.filename)}"
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
        file.save(temp_filepath)
        
        # Validate XML
        processor = XMLProcessor()
        is_valid = processor.validate_xml(temp_filepath)
        
        # Get job count if valid
        job_count = 0
        if is_valid:
            job_count = processor.count_jobs(temp_filepath)
        
        # Clean up
        os.remove(temp_filepath)
        
        return jsonify({
            'valid': is_valid,
            'job_count': job_count,
            'error': None if is_valid else 'Invalid XML structure'
        })
        
    except Exception as e:
        app.logger.error(f"Error in validate_file: {str(e)}")
        return jsonify({'valid': False, 'error': str(e)})

@app.route('/bullhorn')
def bullhorn_dashboard():
    """ATS monitoring dashboard"""
    monitors = BullhornMonitor.query.filter_by(is_active=True).all()
    recent_activities = BullhornActivity.query.order_by(BullhornActivity.created_at.desc()).limit(20).all()
    
    # Check if Bullhorn is connected
    bullhorn_connected = False
    try:
        bullhorn_service = BullhornService()
        bullhorn_connected = bullhorn_service.test_connection()
    except Exception as e:
        app.logger.debug(f"Bullhorn connection check failed: {str(e)}")
    
    return render_template('bullhorn.html', 
                         monitors=monitors, 
                         recent_activities=recent_activities,
                         bullhorn_connected=bullhorn_connected)

@app.route('/bullhorn/create', methods=['GET', 'POST'])
def create_bullhorn_monitor():
    """Create a new Bullhorn monitor"""
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            monitor_type = request.form.get('monitor_type')
            check_interval = int(request.form.get('check_interval_minutes', 60))
            notification_email = request.form.get('notification_email', '').strip()
            send_notifications = 'send_notifications' in request.form
            
            # Validate inputs
            if not name or not monitor_type:
                flash('Name and Monitor Type are required', 'error')
                return redirect(url_for('create_bullhorn_monitor'))
            
            # Handle tearsheet-based monitoring
            if monitor_type == 'tearsheet':
                tearsheet_id = request.form.get('tearsheet_id')
                if not tearsheet_id:
                    flash('Tearsheet selection is required', 'error')
                    return redirect(url_for('create_bullhorn_monitor'))
                
                # Get tearsheet name for reference
                try:
                    bullhorn_service = BullhornService()
                    tearsheets = bullhorn_service.get_tearsheets()
                    tearsheet_name = next((t['name'] for t in tearsheets if str(t['id']) == tearsheet_id), f"Tearsheet {tearsheet_id}")
                except:
                    tearsheet_name = f"Tearsheet {tearsheet_id}"
                
                monitor = BullhornMonitor(
                    name=name,
                    tearsheet_id=int(tearsheet_id),
                    tearsheet_name=tearsheet_name,
                    check_interval_minutes=check_interval,
                    notification_email=notification_email if notification_email else None,
                    send_notifications=send_notifications,
                    next_check=datetime.utcnow()
                )
                
                flash(f'Monitor "{name}" created successfully for tearsheet: {tearsheet_name}', 'success')
            
            # Handle query-based monitoring
            elif monitor_type == 'query':
                job_search_query = request.form.get('job_search_query', '').strip()
                if not job_search_query:
                    flash('Job Search Query is required', 'error')
                    return redirect(url_for('create_bullhorn_monitor'))
                
                monitor = BullhornMonitor(
                    name=name,
                    tearsheet_id=0,  # 0 indicates query-based monitor
                    tearsheet_name=job_search_query,  # Store query in tearsheet_name field
                    check_interval_minutes=check_interval,
                    notification_email=notification_email if notification_email else None,
                    send_notifications=send_notifications,
                    next_check=datetime.utcnow()
                )
                
                flash(f'Monitor "{name}" created successfully with search query: {job_search_query}', 'success')
            
            else:
                flash('Invalid monitor type', 'error')
                return redirect(url_for('create_bullhorn_monitor'))
            
            db.session.add(monitor)
            db.session.commit()
            
            return redirect(url_for('bullhorn_dashboard'))
            
        except Exception as e:
            flash(f'Error creating monitor: {str(e)}', 'error')
            return redirect(url_for('create_bullhorn_monitor'))
    
    # For GET request, load tearsheets from Bullhorn
    try:
        bullhorn_service = BullhornService()
        tearsheets = bullhorn_service.get_tearsheets()
        return render_template('bullhorn_create.html', tearsheets=tearsheets)
    except Exception as e:
        flash('Could not load tearsheets from Bullhorn. Please check your API credentials.', 'error')
        return render_template('bullhorn_create.html', tearsheets=[])

@app.route('/bullhorn/monitor/<int:monitor_id>')
def bullhorn_monitor_details(monitor_id):
    """View details of a specific Bullhorn monitor"""
    monitor = BullhornMonitor.query.get_or_404(monitor_id)
    activities = BullhornActivity.query.filter_by(monitor_id=monitor_id).order_by(BullhornActivity.created_at.desc()).limit(50).all()
    
    return render_template('bullhorn_details.html', 
                         monitor=monitor, 
                         activities=activities)

@app.route('/bullhorn/monitor/<int:monitor_id>/delete', methods=['POST'])
def delete_bullhorn_monitor(monitor_id):
    """Delete a Bullhorn monitor"""
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        monitor_name = monitor.name
        
        # Soft delete by setting inactive
        monitor.is_active = False
        db.session.commit()
        
        flash(f'Monitor "{monitor_name}" deleted successfully', 'success')
        
    except Exception as e:
        app.logger.error(f"Error deleting Bullhorn monitor: {str(e)}")
        flash(f'Error deleting monitor: {str(e)}', 'error')
    
    return redirect(url_for('bullhorn_dashboard'))

@app.route('/bullhorn/monitor/<int:monitor_id>/test', methods=['POST'])
def test_bullhorn_monitor(monitor_id):
    """Test a Bullhorn monitor manually"""
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        
        # Initialize Bullhorn service
        bullhorn_service = BullhornService()
        
        if not bullhorn_service.test_connection():
            return jsonify({
                'success': False,
                'message': 'Failed to connect to Bullhorn API. Check your credentials in Global Settings.'
            })
        
        # Get jobs based on monitor type
        if monitor.tearsheet_id == 0:
            # Query-based monitor
            jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
            message = f'Successfully connected to Bullhorn. Found {len(jobs)} jobs matching query: {monitor.tearsheet_name}'
        else:
            # Traditional tearsheet-based monitor
            jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
            message = f'Successfully connected to Bullhorn. Found {len(jobs)} jobs in tearsheet {monitor.tearsheet_id}.'
        
        return jsonify({
            'success': True,
            'message': message,
            'job_count': len(jobs)
        })
        
    except Exception as e:
        app.logger.error(f"Error testing Bullhorn monitor: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        })

@app.route('/bullhorn/settings', methods=['GET', 'POST'])
def bullhorn_settings():
    """Manage Bullhorn API credentials in Global Settings"""
    if request.method == 'POST':
        app.logger.info(f"Bullhorn settings POST received with action: {request.form.get('action')}")
        app.logger.info(f"Form data: {dict(request.form)}")
        
        # Check if this is a test action
        if request.form.get('action') == 'test':
            try:
                app.logger.info("Testing Bullhorn connection from settings page")
                
                # Get credentials from database
                credentials = {}
                for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
                    setting = GlobalSettings.query.filter_by(setting_key=key).first()
                    credentials[key] = setting.setting_value if setting else ''
                
                app.logger.info(f"Loaded credentials - Client ID exists: {bool(credentials.get('bullhorn_client_id'))}")
                
                # Initialize service with credentials
                bullhorn_service = BullhornService(
                    client_id=credentials.get('bullhorn_client_id'),
                    client_secret=credentials.get('bullhorn_client_secret'),
                    username=credentials.get('bullhorn_username'),
                    password=credentials.get('bullhorn_password')
                )
                
                result = bullhorn_service.test_connection()
                
                if result:
                    flash('Successfully connected to Bullhorn API', 'success')
                else:
                    # Check if credentials are missing
                    if not credentials.get('bullhorn_client_id') or not credentials.get('bullhorn_username'):
                        flash('Missing Bullhorn credentials. Please save your credentials first.', 'error')
                    else:
                        flash('Failed to connect to Bullhorn API. Please check your credentials.', 'error')
            except Exception as e:
                flash(f'Connection test failed: {str(e)}', 'error')
            
            return redirect(url_for('bullhorn_settings'))
        
        # Otherwise, it's a save action
        elif request.form.get('action') == 'save' or not request.form.get('action'):
            try:
                # Update Bullhorn settings
                settings_to_update = [
                    ('bullhorn_client_id', request.form.get('bullhorn_client_id', '')),
                    ('bullhorn_client_secret', request.form.get('bullhorn_client_secret', '')),
                    ('bullhorn_username', request.form.get('bullhorn_username', '')),
                    ('bullhorn_password', request.form.get('bullhorn_password', '')),
                ]
                
                for key, value in settings_to_update:
                    setting = GlobalSettings.query.filter_by(setting_key=key).first()
                    if setting:
                        setting.setting_value = value
                    else:
                        setting = GlobalSettings(setting_key=key, setting_value=value)
                        db.session.add(setting)
                
                db.session.commit()
                flash('Bullhorn settings updated successfully', 'success')
                
            except Exception as e:
                flash(f'Error updating settings: {str(e)}', 'error')
            
            return redirect(url_for('bullhorn_settings'))
    
    # Get current settings
    settings = {}
    for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
        setting = GlobalSettings.query.filter_by(setting_key=key).first()
        settings[key] = setting.setting_value if setting else ''
    
    return render_template('bullhorn_settings.html', settings=settings)



@app.route('/bullhorn/oauth/callback')
def bullhorn_oauth_callback():
    """Handle Bullhorn OAuth callback"""
    try:
        # This endpoint is used as the redirect URI for Bullhorn OAuth
        # In a production implementation, this would handle the authorization code
        code = request.args.get('code')
        error = request.args.get('error')
        
        if error:
            flash(f'Bullhorn OAuth error: {error}', 'error')
        elif code:
            flash('OAuth authorization successful', 'success')
        else:
            flash('OAuth callback received with no code or error', 'info')
            
        return redirect(url_for('bullhorn_settings'))
        
    except Exception as e:
        flash(f'OAuth callback error: {str(e)}', 'error')
        return redirect(url_for('bullhorn_settings'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
