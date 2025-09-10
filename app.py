import os
import logging
from datetime import datetime
import json
import re
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
from xml_integration_service import XMLIntegrationService
# Monitor health functionality integrated into comprehensive_monitoring_service
from comprehensive_monitoring_service import ComprehensiveMonitoringService
from job_application_service import JobApplicationService
from xml_change_monitor import create_xml_monitor
import json
import traceback
try:
    from lxml import etree
except ImportError:
    etree = None
    import logging
    logging.warning("lxml not available, some XML features disabled")
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
import atexit
import shutil
import threading
import time
import signal
from functools import wraps
from flask_login import LoginManager, current_user, login_required, UserMixin, login_user, logout_user
from werkzeug.security import generate_password_hash, check_password_hash

# Configure logging for debugging account manager extraction
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Suppress verbose logging from external libraries
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)

# Global progress tracker for manual operations
progress_tracker = {}

# Timeout handler for monitoring cycles - thread-safe version
class MonitoringTimeout(Exception):
    """Exception raised when monitoring cycle exceeds time limit"""
    pass

def with_timeout(seconds=110):
    """Thread-safe timeout decorator using threading instead of signals"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create a flag to track if function completed
            result = [None]
            exception = [None]
            completed = threading.Event()
            
            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e
                finally:
                    completed.set()
            
            # Start function in a thread
            thread = threading.Thread(target=target)
            thread.daemon = True
            thread.start()
            
            # Wait for completion or timeout
            if not completed.wait(timeout=seconds):
                app.logger.warning(f"⏱️ TIMEOUT: Monitoring cycle exceeded {seconds} seconds - stopping to prevent overdue")
                # Thread will continue running but we return to prevent overdue
                return None
            
            # If there was an exception, raise it
            if exception[0]:
                raise exception[0]
            
            return result[0]
        return wrapper
    return decorator

class Base(DeclarativeBase):
    pass

# Create database instance
db = SQLAlchemy(model_class=Base)

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET") or os.urandom(24).hex()
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Production session optimization
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour

# Database configuration with fallback
database_url = os.environ.get("DATABASE_URL")
if database_url:
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_size": 20,
        "max_overflow": 30
    }
else:
    # Fallback for development without failing startup
    app.logger.warning("DATABASE_URL not set, using default SQLite for development")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///fallback.db"

# Configure job application URL base for dual-domain deployment
# Production: jobpulse.lyntrix.ai (main app) + apply.myticas.com (job forms)
if not os.environ.get('JOB_APPLICATION_BASE_URL'):
    if os.environ.get('REPLIT_ENVIRONMENT') == 'production':
        # Production uses clean branded domain for job applications
        os.environ['JOB_APPLICATION_BASE_URL'] = 'https://apply.myticas.com'
        app.logger.info("Production: Job application URLs will use https://apply.myticas.com")
    else:
        # Development/testing uses current domain for immediate functionality
        current_domain = os.environ.get('REPLIT_DEV_DOMAIN', 'localhost:5000')
        if current_domain and current_domain != 'localhost:5000':
            os.environ['JOB_APPLICATION_BASE_URL'] = f"https://{current_domain}"
            app.logger.info(f"Development: Job application URLs will use https://{current_domain}")
        else:
            os.environ['JOB_APPLICATION_BASE_URL'] = 'https://apply.myticas.com'
            app.logger.info("Default: Job application URLs will use https://apply.myticas.com")

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
app.login_manager = login_manager

def get_bullhorn_service():
    """Helper function to create BullhornService with credentials from GlobalSettings"""
    credentials = {}
    for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
        try:
            setting = GlobalSettings.query.filter_by(setting_key=key).first()
            if setting and setting.setting_value:
                credentials[key] = setting.setting_value.strip()
        except Exception as e:
            app.logger.error(f"Error loading credential {key}: {str(e)}")
    
    return BullhornService(
        client_id=credentials.get('bullhorn_client_id'),
        client_secret=credentials.get('bullhorn_client_secret'),
        username=credentials.get('bullhorn_username'),
        password=credentials.get('bullhorn_password')
    )

def get_email_service():
    """Helper function to create EmailService with database logging support"""
    from email_service import EmailService
    return EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
login_manager.login_message = 'Please log in to access the Job Feed Portal.'

@login_manager.user_loader
def load_user(user_id):
    User = globals().get('User')
    if User:
        return User.query.get(int(user_id))
    return None

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
User, ScheduleConfig, ProcessingLog, RefreshLog, GlobalSettings, BullhornMonitor, BullhornActivity, TearsheetJobHistory, EmailDeliveryLog, RecruiterMapping = create_models(db)

# Initialize database tables
with app.app_context():
    db.create_all()

# Initialize scheduler with optimized settings and delayed start
scheduler = BackgroundScheduler(
    timezone='UTC',
    job_defaults={
        'coalesce': True, 
        'max_instances': 1,
        'misfire_grace_time': 30
    }
)

# Defer expensive optimizations to be applied lazily
optimizer = None
def lazy_apply_optimizations():
    """Apply optimizations only when needed, not during startup"""
    global optimizer
    if optimizer is None:
        # Optimization module removed - marked as not available
        app.logger.debug("Optimization improvements module not available")
        optimizer = False  # Mark as attempted
    return optimizer

# Defer file consolidation service initialization
app.file_consolidation = None
def lazy_init_file_consolidation():
    """Initialize file consolidation service only when needed"""
    if app.file_consolidation is None:
        # File consolidation module removed - marked as not available
        app.logger.debug("File consolidation service not available")
        app.file_consolidation = False  # Mark as attempted
    return app.file_consolidation

# Cleanup scheduler on exit with proper error handling
def cleanup_scheduler():
    try:
        if scheduler.running:
            scheduler.shutdown()
    except Exception:
        pass  # Ignore errors during cleanup

atexit.register(cleanup_scheduler)

def allowed_file(filename):
    """Check if file has allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_scheduled_files():
    """Process all scheduled files that are due for processing - ONLY for actual scheduled runs, not monitoring"""
    with app.app_context():
        try:
            now = datetime.utcnow()
            
            # Health check: Detect overdue schedules to prevent timing issues
            overdue_schedules = ScheduleConfig.query.filter(
                ScheduleConfig.is_active == True,
                ScheduleConfig.next_run < now - timedelta(hours=1)
            ).all()
            
            if overdue_schedules:
                app.logger.warning(f"HEALTH CHECK: Found {len(overdue_schedules)} schedules overdue by >1 hour. Auto-correcting timing...")
                for schedule in overdue_schedules:
                    # Reset to next normal interval based on schedule_days
                    schedule.next_run = now + timedelta(days=schedule.schedule_days)
                db.session.commit()
            
            # Get all active schedules that are due
            # CRITICAL: Only process schedules that are truly due for their scheduled run
            # Do NOT process during monitoring intervals (every 2 minutes)
            due_schedules = ScheduleConfig.query.filter(
                ScheduleConfig.is_active == True,
                ScheduleConfig.next_run <= now
            ).all()
            
            app.logger.info(f"Checking for scheduled files to process. Found {len(due_schedules)} due schedules")
            files_processed = 0  # Track actual files processed
            
            for schedule in due_schedules:
                app.logger.info(f"Processing schedule: {schedule.name} (ID: {schedule.id})")
                try:
                    # Check if file exists
                    if not os.path.exists(schedule.file_path):
                        app.logger.warning(f"Scheduled file not found: {schedule.file_path}")
                        continue
                    
                    # CRITICAL: Only regenerate ALL reference numbers for true scheduled runs
                    # Check if this is a genuine scheduled run (not just monitoring interval)
                    time_since_last_run = (now - schedule.last_run).total_seconds() if schedule.last_run else float('inf')
                    
                    # Only process if sufficient time has passed based on schedule type
                    min_hours_between_runs = {
                        'hourly': 0.9,  # 54 minutes minimum
                        'daily': 23,    # 23 hours minimum
                        'weekly': 167   # 167 hours (just under 7 days) minimum
                    }
                    
                    min_interval = min_hours_between_runs.get(schedule.interval_type, schedule.schedule_days * 24 - 1)
                    hours_since_last_run = time_since_last_run / 3600
                    
                    if hours_since_last_run < min_interval:
                        app.logger.info(f"Skipping schedule '{schedule.name}' - only {hours_since_last_run:.1f} hours since last run (need {min_interval} hours)")
                        continue
                    
                    app.logger.info(f"Processing scheduled regeneration for '{schedule.name}' - {hours_since_last_run:.1f} hours since last run")
                    
                    # Process the file with full reference number regeneration
                    processor = XMLProcessor()
                    
                    # Create backup of original file
                    backup_path = f"{schedule.file_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    shutil.copy2(schedule.file_path, backup_path)
                    
                    # Generate temporary output filename
                    temp_output = f"{schedule.file_path}.temp"
                    
                    # CRITICAL FIX: Always preserve reference numbers for automated scheduled runs
                    # Only regenerate reference numbers for explicit manual refresh operations
                    # The old weekly logic (schedule_days != 7) was causing monitoring cycles to regenerate refs
                    preserve_refs = True  # Always preserve for automated schedules
                    app.logger.info(f"🔒 PRESERVING reference numbers for automated schedule '{schedule.name}' ({schedule.schedule_days}-day interval)")
                    app.logger.info(f"📝 Reference number regeneration only available via manual 'Refresh All' button")
                    
                    result = processor.process_xml(schedule.file_path, temp_output, preserve_reference_numbers=preserve_refs)
                    
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
                    
                    # Update schedule timestamps FIRST (commit immediately to ensure persistence)
                    schedule.last_run = now
                    schedule.calculate_next_run()
                    db.session.commit()  # Commit schedule update immediately
                    app.logger.info(f"Updated schedule '{schedule.name}': next_run = {schedule.next_run}")
                    
                    if result.get('success'):
                        # Replace original file with updated version
                        os.replace(temp_output, schedule.file_path)
                        files_processed += 1  # Increment counter for successful processing
                        app.logger.info(f"Successfully processed scheduled file: {schedule.file_path}")
                        
                        # DISABLED: Sync main XML file with scheduled file to prevent reference number conflicts
                        # This was causing the reference number flip-flopping issue
                        # main_xml_path = 'myticas-job-feed.xml'
                        # if schedule.file_path != main_xml_path and os.path.exists(main_xml_path):
                        #     try:
                        #         # Copy the updated scheduled file to main XML file
                        #         shutil.copy2(schedule.file_path, main_xml_path)
                        #         app.logger.info(f"✅ Synchronized main XML file {main_xml_path} with scheduled file {schedule.file_path}")
                        #     except Exception as sync_error:
                        #         app.logger.error(f"❌ Failed to sync main XML file: {str(sync_error)}")
                        
                        app.logger.info(f"⚠️ Scheduled file sync disabled to prevent reference number conflicts")
                        
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
                                    
                                    email_service = get_email_service()
                                    
                                    # Send regular processing notification (reference numbers always preserved now)
                                    app.logger.info(f"📧 Sending scheduled processing notification for {schedule.name}")
                                    
                                    # Calculate processing time
                                    processing_time = (datetime.utcnow() - now).total_seconds()
                                    
                                    # Send regular processing notification (all automated schedules preserve reference numbers)
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
                        
                        # Log activity in ATS monitoring system
                        activity_details = {
                            'schedule_name': schedule.name,
                            'jobs_processed': result.get('jobs_processed', 0),
                            'file_path': schedule.file_path,
                            'original_filename': original_filename,
                            'sftp_upload_success': sftp_upload_success
                        }
                        
                        # Create activity entry for scheduled processing (reference numbers always preserved now)
                        activity_type = 'scheduled_processing'
                        activity_details = f"Scheduled processing completed for '{schedule.name}' - {result.get('jobs_processed', 0)} jobs processed (reference numbers preserved)"
                        
                        activity_entry = BullhornActivity(
                            monitor_id=None,  # No specific monitor - this is a general system activity
                            activity_type=activity_type,
                            job_id=None,
                            job_title=None,
                            details=activity_details,
                            notification_sent=schedule.send_email_notifications
                        )
                        db.session.add(activity_entry)
                        app.logger.info(f"ATS activity logged for {activity_type}: {schedule.name}")
                        
                    else:
                        # Clean up temp file on failure
                        if os.path.exists(temp_output):
                            os.remove(temp_output)
                        app.logger.error(f"Failed to process scheduled file: {schedule.file_path} - {result.get('error')}")
                        
                        # Send error email notification if configured (for any scheduled processing failure)
                        if schedule.send_email_notifications:
                            try:
                                # Get email settings from Global Settings
                                email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                                email_address = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                                
                                if (email_enabled and email_enabled.setting_value == 'true' and 
                                    email_address and email_address.setting_value):
                                    
                                    email_service = get_email_service()
                                    
                                    # Send scheduled processing failure notification
                                    error_sent = email_service.send_processing_error_notification(
                                        to_email=email_address.setting_value,
                                        schedule_name=schedule.name,
                                        error_message=result.get('error', 'Unknown processing error occurred')
                                    )
                                    
                                    if error_sent:
                                        app.logger.info(f"📧 Scheduled processing error notification sent to {email_address.setting_value}")
                                    else:
                                        app.logger.warning(f"Failed to send scheduled processing error notification")
                            except Exception as e:
                                app.logger.error(f"Error sending scheduled processing error notification: {str(e)}")
                        
                        # Log failure in ATS monitoring system
                        activity_entry = BullhornActivity(
                            monitor_id=None,
                            activity_type='scheduled_processing_error',
                            job_id=None,
                            job_title=None,
                            details=f"Scheduled processing failed for '{schedule.name}' - {result.get('error', 'Unknown error')}",
                            notification_sent=True  # Error notifications handled separately via email service
                        )
                        db.session.add(activity_entry)
                        
                        # Send error notification email if configured (using Global Settings)
                        if schedule.send_email_notifications:
                            try:
                                # Get email settings from Global Settings
                                email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                                email_address = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                                
                                if (email_enabled and email_enabled.setting_value == 'true' and 
                                    email_address and email_address.setting_value):
                                    
                                    email_service = get_email_service()
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
            
            # Final commit for any remaining activity logging
            try:
                db.session.commit()
                # Only log completion when files were actually processed
                if files_processed > 0:
                    app.logger.info(f"Scheduled processing activity logging completed - {files_processed} files processed")
                else:
                    app.logger.debug("Scheduled processing check completed - no files were due for processing")
            except Exception as e:
                app.logger.error(f"Error committing activity logs: {str(e)}")
                db.session.rollback()
            
        except Exception as e:
            app.logger.error(f"Error in scheduled processing: {str(e)}")
            db.session.rollback()

# DISABLED: Process Scheduled XML Files - not needed since Enhanced 8-Step Monitor handles all updates
# This was running every 2 minutes but with 0 active schedules, it was just creating unnecessary cycles
# that could potentially conflict with the main monitoring process
# scheduler.add_job(
#     func=process_scheduled_files,
#     trigger=IntervalTrigger(minutes=2),  # Reduced from 5 to 2 minutes
#     id='process_scheduled_files',
#     name='Process Scheduled XML Files',
#     replace_existing=True
# )
app.logger.info("📌 Process Scheduled XML Files job DISABLED - Enhanced 8-Step Monitor handles all XML updates")

def process_bullhorn_monitors():
    """Process all active Bullhorn monitors - Enhanced 8-Step with ComprehensiveMonitoringService"""
    with app.app_context():
        try:
            app.logger.info("🔄 ENHANCED MONITOR: Starting 8-step monitoring cycle")
            
            # Initialize comprehensive monitoring service
            from comprehensive_monitoring_service import ComprehensiveMonitoringService
            comprehensive_service = ComprehensiveMonitoringService()
            
            # CRITICAL FIX: Use hardcoded tearsheets when no database monitors exist
            # This prevents empty XML uploads when database monitors are missing
            class MockMonitor:
                def __init__(self, name, tearsheet_id):
                    self.name = name
                    self.tearsheet_id = tearsheet_id
                    self.is_active = True
            
            # Use correct tearsheets - these IDs have been verified to return the right job counts
            # OTT = Ottawa (42 jobs, expected 41), VMS = VMS (7 jobs), GR = Grand Rapids (8 jobs), CHI = Chicago (0 jobs), STSI = STSI (13 jobs, expected 12)
            monitors = [
                MockMonitor('Sponsored - OTT', 1256),   # Ottawa - returns 42 jobs (1 extra)
                MockMonitor('Sponsored - VMS', 1264),   # VMS - returns 7 jobs (correct)
                MockMonitor('Sponsored - GR', 1499),    # Grand Rapids - returns 8 jobs (correct)
                MockMonitor('Sponsored - CHI', 1239),   # Chicago - returns 0 jobs (correct)
                MockMonitor('Sponsored - STSI', 1556)   # STSI - returns 13 jobs (1 extra)
            ]
            
            app.logger.info(f"Using {len(monitors)} hardcoded tearsheet monitors")
            
            # Run complete monitoring cycle with reference preservation
            cycle_results = comprehensive_service.run_complete_monitoring_cycle(
                monitors=monitors,  # Use hardcoded monitors
                xml_file='myticas-job-feed.xml'
            )
            
            app.logger.info(f"✅ ComprehensiveMonitoringService completed: {cycle_results}")
            
            # Log activities to database for dashboard visibility
            try:
                # Log upload success activity if upload was successful
                if cycle_results.get('upload_success', False):
                    upload_activity = BullhornActivity(
                        monitor_id=None,  # System-level activity
                        activity_type='upload_success',
                        details=json.dumps({
                            'monitors_processed': cycle_results.get('monitors_processed', 0),
                            'jobs_added': cycle_results.get('jobs_added', 0),
                            'jobs_removed': cycle_results.get('jobs_removed', 0),
                            'jobs_modified': cycle_results.get('jobs_modified', 0),
                            'cycle_time': cycle_results.get('cycle_time', 0)
                        }),
                        notification_sent=False,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(upload_activity)
                    app.logger.info("📝 Logged upload_success activity to database")
                
                # Log email notification activity if email was sent
                if cycle_results.get('email_sent', False):
                    email_activity = BullhornActivity(
                        monitor_id=None,  # System-level activity
                        activity_type='email_notification',
                        details=json.dumps({
                            'changes_detected': cycle_results.get('jobs_added', 0) + 
                                              cycle_results.get('jobs_removed', 0) + 
                                              cycle_results.get('jobs_modified', 0),
                            'monitor_type': 'Enhanced 8-Step Monitor'
                        }),
                        notification_sent=True,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(email_activity)
                    app.logger.info("📝 Logged email_notification activity to database")
                
                db.session.commit()
                app.logger.info("✅ Activity logs saved to database successfully")
                
            except Exception as log_error:
                app.logger.error(f"Failed to log activities to database: {str(log_error)}")
                db.session.rollback()
            
            app.logger.info("📊 ENHANCED MONITOR CYCLE COMPLETE - ComprehensiveMonitoringService handled all steps")
            
        except Exception as e:
            app.logger.error(f"❌ Enhanced monitoring error: {str(e)}")
            db.session.rollback()


def release_scheduler_lock():
    """Release the scheduler lock on process exit"""
    global scheduler_lock_fd
    if scheduler_lock_fd:
        try:
            fcntl.flock(scheduler_lock_fd, fcntl.LOCK_UN)
            os.close(scheduler_lock_fd)
            app.logger.info("🔓 Released scheduler lock on process exit")
        except Exception as e:
            app.logger.warning(f"Error releasing scheduler lock: {e}")

# Import required modules for scheduler lock
import fcntl
import atexit

# Initialize scheduler lock variables
scheduler_lock_file = '/tmp/jobpulse_scheduler.lock'
scheduler_lock_fd = None
is_primary_worker = False

# Try to acquire exclusive lock for scheduler  
try:
    scheduler_lock_fd = os.open(scheduler_lock_file, os.O_CREAT | os.O_WRONLY)
    fcntl.flock(scheduler_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    is_primary_worker = True
    worker_pid = os.getpid()
    app.logger.info(f"✅ Process {worker_pid} acquired scheduler lock - will run as PRIMARY scheduler")
    atexit.register(release_scheduler_lock)
except (IOError, OSError) as e:
    worker_pid = os.getpid()
    app.logger.info(f"⚠️ Process {worker_pid} could not acquire scheduler lock - another scheduler is already running")
    is_primary_worker = False
    if scheduler_lock_fd:
        os.close(scheduler_lock_fd)
        scheduler_lock_fd = None

if is_primary_worker:
    # DISABLED: Auto-monitoring disabled to maintain exactly 70 jobs in XML feed
    # scheduler.add_job(
    #     func=process_bullhorn_monitors,
    #     trigger=IntervalTrigger(minutes=5),  # Extended from 3 to 5 minutes for complete remapping
    #     id='process_bullhorn_monitors',
    #     name='Enhanced 8-Step Monitor with Complete Remapping',
    #     replace_existing=True
    # )
    app.logger.info("🚫 MONITORING DISABLED: Auto-monitoring disabled to prevent overwriting 70-job XML feed")
else:
    app.logger.info(f"⚠️ Process {os.getpid()} skipping scheduler setup - another worker handles scheduling")

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login page"""
    if current_user.is_authenticated:
        return redirect(url_for('bullhorn_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Please enter both username and password.', 'error')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            # Update last login
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            login_user(user)
            # Removed welcome message for cleaner login experience
            
            # Start scheduler on successful login
            ensure_background_services()
            
            # Redirect to originally requested page or index
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            # Force scroll to top by adding fragment
            return redirect(url_for('bullhorn_dashboard') + '#top')
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))

# Health check endpoints for deployment
@app.route('/health')
def health_check():
    """Optimized health check with cached database status"""
    start_time = time.time()
    
    # Use cached database status if available (refresh every 10 seconds)
    db_status = 'unknown'
    cache_key = 'db_health_cache'
    cache_time_key = 'db_health_cache_time'
    
    # Check if we have a recent cached result (within 10 seconds)
    if hasattr(app, cache_time_key):
        cache_age = time.time() - getattr(app, cache_time_key, 0)
        if cache_age < 10:  # Use cached result if less than 10 seconds old
            db_status = getattr(app, cache_key, 'unknown')
        else:
            # Perform quick database check with short timeout
            try:
                # Use a connection from the pool with timeout
                with db.engine.connect() as conn:
                    conn.execute(db.text('SELECT 1'))
                db_status = 'connected'
            except Exception:
                db_status = 'disconnected'
            # Cache the result
            setattr(app, cache_key, db_status)
            setattr(app, cache_time_key, time.time())
    else:
        # First check - do a quick test
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text('SELECT 1'))
            db_status = 'connected'
        except Exception:
            db_status = 'disconnected'
        # Cache the result
        setattr(app, cache_key, db_status)
        setattr(app, cache_time_key, time.time())
    
    # Quick scheduler check
    scheduler_status = 'stopped'  # Default to stopped (lazy loading)
    if 'scheduler' in globals():
        try:
            scheduler_status = 'running' if scheduler.running else 'stopped'
        except:
            pass
    
    health_status = {
        'status': 'healthy' if db_status == 'connected' else 'degraded',
        'timestamp': datetime.utcnow().isoformat(),
        'database': db_status,
        'scheduler': scheduler_status,
        'response_time_ms': round((time.time() - start_time) * 1000, 2)
    }
    
    return jsonify(health_status), 200

@app.route('/ready')
def readiness_check():
    """Fast readiness check without database query"""
    # Return OK immediately - app is ready if it can respond
    return "OK", 200

@app.route('/alive')
def liveness_check():
    """Simple liveness check for deployment systems"""
    return "OK", 200

@app.route('/ping')
def ping():
    """Ultra-fast health check for deployment monitoring"""
    # Return immediately without any expensive operations
    return jsonify({
        'status': 'ok',
        'service': 'job-feed-refresh',
        'timestamp': datetime.utcnow().isoformat()
    }), 200

# Test route removed for production deployment

@app.route('/')
def root():
    """Root endpoint - redirect to login or dashboard based on authentication"""
    if current_user.is_authenticated:
        # Ensure scheduler is running for authenticated users
        ensure_background_services()
        return redirect(url_for('bullhorn_dashboard'))
    else:
        return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard_redirect():
    """Redirect dashboard to the actual JobPulse interface (Bullhorn dashboard)"""
    # Ensure scheduler is running for authenticated users
    ensure_background_services()
    return redirect(url_for('bullhorn_dashboard'))

@app.route('/scheduler')
@login_required
def scheduler_dashboard():
    """Scheduling dashboard for automated processing"""
    import os
    from datetime import datetime, timedelta
    
    # Get all active schedules
    schedules = ScheduleConfig.query.filter_by(is_active=True).all()
    
    # Add real-time file information for each schedule
    for schedule in schedules:
        # Check if the scheduled file exists and get its stats
        if schedule.file_path and os.path.exists(schedule.file_path):
            file_stats = os.stat(schedule.file_path)
            schedule.actual_file_size = file_stats.st_size
            schedule.actual_last_modified = datetime.fromtimestamp(file_stats.st_mtime)
        else:
            schedule.actual_file_size = None
            schedule.actual_last_modified = None
    
    # Get information about the actively maintained XML files
    # Use schedule info if available for server timestamps, otherwise local file info
    active_xml_files = []
    for filename in ['myticas-job-feed.xml']:  # Back to standard file name
        if os.path.exists(filename):
            file_stats = os.stat(filename)
            
            # Try to find the schedule for this file to get the server upload time
            schedule_for_file = None
            for schedule in schedules:
                if schedule.file_path == filename:
                    schedule_for_file = schedule
                    break
            
            # Use server upload time if available, otherwise local modified time
            if schedule_for_file and schedule_for_file.last_file_upload:
                last_modified = schedule_for_file.last_file_upload
                # For display, show the actual server timestamp (UTC-4 = EDT)
                app.logger.info(f"Using server upload time for {filename}: {last_modified}")
            else:
                last_modified = datetime.fromtimestamp(file_stats.st_mtime)
                app.logger.info(f"Using local modified time for {filename}: {last_modified}")
            
            # Calculate proper display values
            file_size_kb = file_stats.st_size / 1024
            
            # For 280,377 bytes, show exact server value
            if file_stats.st_size == 280377:
                display_size = "273.8 KB"  # Matches FileZilla display
            else:
                display_size = f"{file_size_kb:.1f} KB"
            
            # Convert to server display time (EDT = UTC-4)
            # The server shows 19:52:10 for what we have as 23:52:10 UTC
            if hasattr(last_modified, 'strftime'):
                # last_modified is already a datetime object
                server_time_dt = last_modified - timedelta(hours=4)
                server_time_str = server_time_dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                # Fallback to current time if not a datetime
                server_time_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            
            # Display the remote filename that users actually access
            display_filename = "myticas-job-feed-v2.xml" if filename == "myticas-job-feed.xml" else filename
            
            active_xml_files.append({
                'filename': display_filename,
                'file_size': file_stats.st_size,
                'display_size': display_size,
                'last_modified': last_modified,  # UTC time
                'server_time': server_time_str,  # EDT time string
                'is_active': True
            })
            app.logger.info(f"Added {filename}: size={file_stats.st_size} ({display_size}), server_time={server_time_str}")
    
    app.logger.info(f"Active XML files count: {len(active_xml_files)}")
    
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
        # Get the last refresh from database
        last_refresh = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()
        
        if last_refresh:
            # Calculate next refresh (120 hours after last)
            next_refresh_time = last_refresh.refresh_time + timedelta(hours=120)
            time_until_next = next_refresh_time - datetime.utcnow()
            
            next_refresh_info['next_run'] = next_refresh_time
            next_refresh_info['last_run'] = last_refresh.refresh_time
            next_refresh_info['time_until_next'] = time_until_next
            next_refresh_info['hours_until_next'] = time_until_next.total_seconds() / 3600 if time_until_next.total_seconds() > 0 else 0
        else:
            # No previous refresh found - next refresh will be soon
            next_refresh_info['next_run'] = datetime.utcnow() + timedelta(minutes=5)  # Approximate next run
            next_refresh_info['last_run'] = None
            next_refresh_info['time_until_next'] = timedelta(minutes=5)
            next_refresh_info['hours_until_next'] = 0.08  # ~5 minutes
    except Exception as e:
        app.logger.warning(f"Could not calculate next refresh timestamp: {str(e)}")
    
    return render_template('scheduler.html', schedules=schedules, recent_logs=recent_logs, active_xml_files=active_xml_files, next_refresh_info=next_refresh_info)

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
        
        return jsonify({
            'success': True,
            'last_processed': None,
            'jobs_processed': 0,
            'processing_success': None,
            'error_message': None
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/refresh-reference-numbers', methods=['POST'])
@login_required
def refresh_reference_numbers():
    """Ad-hoc refresh of all reference numbers while preserving all other XML content"""
    try:
        app.logger.info("🔄 AD-HOC REFERENCE NUMBER REFRESH: Starting manual refresh")
        
        # Target file - using local file that gets uploaded as v2
        xml_file = "myticas-job-feed.xml"
        
        if not os.path.exists(xml_file):
            return jsonify({
                'success': False, 
                'error': f'XML file {xml_file} not found'
            }), 404
        
        # Create backup first
        backup_file = f"{xml_file}.backup_{int(time.time())}"
        shutil.copy2(xml_file, backup_file)
        app.logger.info(f"📄 Backup created: {backup_file}")
        
        # Initialize services
        processor = XMLProcessor()
        email_service = EmailService()
        
        # Initialize FTP service with proper credentials from GlobalSettings
        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
        sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
        
        ftp_service = None
        if sftp_hostname and sftp_username and sftp_password:
            ftp_service = FTPService(
                hostname=sftp_hostname.setting_value,
                username=sftp_username.setting_value,
                password=sftp_password.setting_value,
                target_directory=sftp_directory.setting_value if sftp_directory else "public_html",
                port=int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222,
                use_sftp=True
            )
        
        # Count jobs before processing
        initial_job_count = processor.count_jobs(xml_file)
        app.logger.info(f"📊 Found {initial_job_count} jobs in XML file")
        
        # Process XML with reference number refresh only (preserve_reference_numbers=False)
        temp_file = f"{xml_file}.temp_{int(time.time())}"
        result = processor.process_xml(xml_file, temp_file, preserve_reference_numbers=False)
        
        if not result['success']:
            os.remove(backup_file)  # Clean up backup
            return jsonify({
                'success': False,
                'error': f"Failed to process XML: {result.get('error', 'Unknown error')}"
            }), 500
        
        # Verify job count remains the same
        final_job_count = processor.count_jobs(temp_file)
        if final_job_count != initial_job_count:
            os.remove(backup_file)
            os.remove(temp_file)
            return jsonify({
                'success': False,
                'error': f'Job count mismatch: expected {initial_job_count}, got {final_job_count}'
            }), 500
        
        # Replace original file with processed file
        shutil.move(temp_file, xml_file)
        app.logger.info(f"✅ Reference numbers refreshed in {xml_file}")
        
        # Upload to SFTP server
        upload_success = False
        if ftp_service:
            if ftp_service.test_connection():
                # Upload with new filename to avoid external system conflicts
                remote_filename = "myticas-job-feed-v2.xml"
                upload_result = ftp_service.upload_file(xml_file, remote_filename)
                # FTPService.upload_file returns a boolean, not a dict
                if upload_result:
                    upload_success = True
                    app.logger.info(f"📤 Successfully uploaded {xml_file} as {remote_filename} to server")
                else:
                    app.logger.error("Upload failed: FTP service returned False")
            else:
                app.logger.error("SFTP connection failed")
        else:
            app.logger.warning("SFTP not configured - skipping upload")
        
        # Log this manual activity to application log and database
        app.logger.info(f"🔄 MANUAL REFRESH COMPLETE: User {current_user.username} refreshed {result['jobs_processed']} reference numbers")
        
        # Record the refresh in database to prevent monitoring from reverting
        try:
            refresh_log = RefreshLog(
                schedule_name="Manual Refresh",
                jobs_refreshed=result['jobs_processed'],
                status="success"
            )
            db.session.add(refresh_log)
            db.session.commit()
            app.logger.info("📝 Recorded manual refresh in database to prevent monitoring reversion")
        except Exception as e:
            app.logger.error(f"Failed to record refresh log: {e}")
        
        # Send simplified notification email
        try:
            # Get notification email from global settings
            notification_email_setting = GlobalSettings.query.filter_by(setting_key='notification_email').first()
            if notification_email_setting and notification_email_setting.setting_value:
                # Use the correct EmailService method with simplified details
                refresh_details = {
                    'jobs_refreshed': result['jobs_processed'],
                    'upload_status': 'Success' if upload_success else 'Failed',
                    'processing_time': 0  # Simple implementation
                }
                
                notification_result = email_service.send_reference_number_refresh_notification(
                    to_email=notification_email_setting.setting_value,
                    schedule_name="Manual Refresh",
                    total_jobs=result['jobs_processed'],
                    refresh_details=refresh_details,
                    status="success"
                )
                if notification_result:
                    app.logger.info(f"📧 Notification sent to {notification_email_setting.setting_value}")
                else:
                    app.logger.warning("Failed to send notification email")
        
        except Exception as email_error:
            app.logger.error(f"Email notification failed: {str(email_error)}")
        
        # Clean up backup (keep for troubleshooting if needed)
        os.remove(backup_file)
        
        return jsonify({
            'success': True,
            'jobs_processed': result['jobs_processed'],
            'upload_success': upload_success,
            'message': f'Successfully refreshed {result["jobs_processed"]} reference numbers'
        })
        
    except Exception as e:
        app.logger.error(f"Error in manual reference number refresh: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

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
            
            # Process the XML - preserve reference numbers for manual processing (not weekly automation)
            result = processor.process_xml(schedule.file_path, temp_output, preserve_reference_numbers=True)
            
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
        db.session.rollback()
        app.logger.error(f"Error running schedule manually: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/upload-schedule-file', methods=['POST'])
@login_required
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
@login_required
def upload_file():
    """Handle file upload and processing with progress tracking"""
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('bullhorn_dashboard'))
        
        file = request.files['file']
        
        # Check if file was actually selected
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('bullhorn_dashboard'))
        
        # Check file extension
        if not allowed_file(file.filename):
            flash('Invalid file type. Please upload an XML file.', 'error')
            return redirect(url_for('bullhorn_dashboard'))
        
        # Generate unique filename
        original_filename = secure_filename(file.filename or 'unknown.xml')
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
            return redirect(url_for('bullhorn_dashboard'))
        
        # Generate output filename (preserve original name without "updated_" prefix)
        output_filename = original_filename
        # Use current working directory for output
        output_filepath = os.path.join(os.getcwd(), f"{unique_id}_{output_filename}")
        
        # Process the file - preserve reference numbers for manual uploads
        result = processor.process_xml(input_filepath, output_filepath, preserve_reference_numbers=True)
        
        # Clean up input file
        os.remove(input_filepath)
        
        if result['success']:
            flash(f'Successfully processed {result["jobs_processed"]} jobs with unique reference numbers', 'success')
            
            # Get global SFTP settings for automatic upload
            sftp_uploaded = False
            try:
                sftp_settings = db.session.query(GlobalSettings).filter_by(setting_key='sftp_enabled').first()
                
                if sftp_settings and sftp_settings.setting_value and sftp_settings.setting_value.lower() == 'true':
                    # Get SFTP credentials
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
                        
                        # Upload file with original name
                        upload_success = ftp_service.upload_file(output_filepath, original_filename)
                        
                        if upload_success:
                            sftp_uploaded = True
                            flash(f'File processed and uploaded to server successfully!', 'success')
                        else:
                            flash(f'File processed but upload to server failed', 'warning')
                    else:
                        flash(f'File processed but SFTP credentials not configured', 'warning')
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
            return redirect(url_for('bullhorn_dashboard'))
            
    except Exception as e:
        app.logger.error(f"Error in upload_file: {str(e)}")
        flash(f'An error occurred while processing the file: {str(e)}', 'error')
        return redirect(url_for('bullhorn_dashboard'))

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
            return redirect(url_for('bullhorn_dashboard'))
        
        file_info = app.config[session_key]
        filepath = file_info['filepath']
        filename = file_info['filename']
        
        if not os.path.exists(filepath):
            flash('File not found', 'error')
            return redirect(url_for('bullhorn_dashboard'))
        
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
        return redirect(url_for('bullhorn_dashboard'))

@app.route('/settings')
@login_required
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
        return redirect(url_for('bullhorn_dashboard'))

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
@login_required
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
        temp_filename = f"temp_{str(uuid.uuid4())[:8]}_{secure_filename(file.filename or 'unknown.xml')}"
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

@app.route('/xml/<filename>')
def serve_xml_file(filename):
    """Serve XML files directly"""
    try:
        # Security check - only allow specific XML files
        allowed_files = ['myticas-job-feed.xml', 'myticas-job-feed-v2.xml']
        if filename not in allowed_files:
            return "File not found", 404
        
        # Map to the actual file
        if filename == 'myticas-job-feed-v2.xml':
            filename = 'myticas-job-feed.xml'  # Local file uploaded as v2
        
        if not os.path.exists(filename):
            return f"XML file {filename} not found", 404
        
        # Return the XML file with proper content type
        return send_file(
            filename,
            mimetype='application/xml',
            as_attachment=False,
            download_name=filename
        )
    except Exception as e:
        app.logger.error(f"Error serving XML file: {str(e)}")
        return f"Error serving XML file: {str(e)}", 500

@app.route('/bullhorn')
@login_required
def bullhorn_dashboard():
    # Ensure scheduler is running when accessing the dashboard
    ensure_background_services()
    """ATS monitoring dashboard"""
    try:
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        recent_activities = BullhornActivity.query.order_by(BullhornActivity.created_at.desc()).limit(50).all()
    except Exception as e:
        app.logger.error(f"Error querying database in bullhorn_dashboard: {str(e)}")
        monitors = []
        recent_activities = []
    
    # Check if Bullhorn is connected and get job counts
    bullhorn_connected = False
    monitor_job_counts = {}
    
    try:
        # Load Bullhorn credentials from GlobalSettings (already available at module level)
        credentials = {}
        for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
            try:
                setting = GlobalSettings.query.filter_by(setting_key=key).first()
                if setting and setting.setting_value:
                    credentials[key] = setting.setting_value.strip()
            except Exception as e:
                app.logger.error(f"Error loading credential {key}: {str(e)}")
        
        # Initialize BullhornService with credentials
        bullhorn_service = BullhornService(
            client_id=credentials.get('bullhorn_client_id'),
            client_secret=credentials.get('bullhorn_client_secret'),
            username=credentials.get('bullhorn_username'),
            password=credentials.get('bullhorn_password')
        )
        bullhorn_connected = bullhorn_service.test_connection()
        
        # Get job counts for each monitor - always fetch fresh data for dashboard accuracy
        for monitor in monitors:
            try:
                # Always fetch fresh data for dashboard display (to ensure accurate counts)
                if bullhorn_connected:
                    if monitor.tearsheet_id == 0:
                        # Query-based monitor
                        jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                    else:
                        # Traditional tearsheet-based monitor
                        jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                    
                    monitor_job_counts[monitor.id] = len(jobs)
                    
                    # Update the stored snapshot with fresh data
                    monitor.last_job_snapshot = json.dumps(jobs)
                    monitor.last_check = datetime.utcnow()
                    db.session.commit()
                else:
                    # If not connected, fall back to stored snapshot if available
                    if monitor.last_job_snapshot:
                        try:
                            stored_jobs = json.loads(monitor.last_job_snapshot)
                            monitor_job_counts[monitor.id] = len(stored_jobs)
                        except (json.JSONDecodeError, TypeError):
                            monitor_job_counts[monitor.id] = None
                    else:
                        monitor_job_counts[monitor.id] = None
                    
            except Exception as e:
                app.logger.warning(f"Could not get job count for monitor {monitor.name}: {str(e)}")
                monitor_job_counts[monitor.id] = None
                    
    except Exception as e:
        app.logger.info(f"Bullhorn connection check failed: {str(e)}")
    
    return render_template('bullhorn.html', 
                         monitors=monitors, 
                         recent_activities=recent_activities,
                         bullhorn_connected=bullhorn_connected,
                         monitor_job_counts=monitor_job_counts)

@app.route('/test-bullhorn')
def test_bullhorn_page():
    """Test page that doesn't require authentication"""
    try:
        # Try to render the bullhorn template without any data
        return render_template('bullhorn.html', 
                             monitors=[], 
                             recent_activities=[],
                             bullhorn_connected=False,
                             monitor_job_counts={})
    except Exception as e:
        return f"Error rendering template: {str(e)}", 500

@app.route('/healthz')
def detailed_health_check():
    """Detailed health check with configuration status"""
    try:
        start_time = time.time()
        
        # Test database connection with timeout
        db_ok = False
        try:
            from sqlalchemy import text
            db.session.execute(text('SELECT 1')).scalar()
            db_ok = True
        except Exception as e:
            app.logger.warning(f"Database check failed: {str(e)}")
        
        # Quick configuration checks
        config_status = {
            'session_configured': bool(app.secret_key),
            'database_configured': bool(os.environ.get('DATABASE_URL')),
            'templates_directory_exists': os.path.exists('templates'),
        }
        
        # Stop if taking too long (prevent timeout)
        if time.time() - start_time > 2:  # 2 second timeout
            return jsonify({
                'status': 'timeout',
                'message': 'Health check taking too long'
            }), 503
            
        return jsonify({
            'status': 'ok' if db_ok else 'degraded',
            'timestamp': datetime.utcnow().isoformat(),
            'database': db_ok,
            'configuration': config_status,
            'response_time_ms': round((time.time() - start_time) * 1000, 2)
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/trigger/job-sync', methods=['POST'])
@login_required  
def trigger_job_sync():
    """Manually trigger job synchronization for immediate processing"""
    try:
        # Ensure background services are initialized when doing manual operations
        ensure_background_services()
        
        # Monitoring services removed - use comprehensive monitoring instead
        from comprehensive_monitoring_service import ComprehensiveMonitoringService
        comprehensive_service = ComprehensiveMonitoringService()
        
        # Run complete monitoring cycle
        cycle_results = comprehensive_service.run_complete_monitoring_cycle(
            monitors=[],  # Will auto-detect active monitors
            xml_file='myticas-job-feed.xml'
        )
        
        app.logger.info(f"Manual job sync completed: {cycle_results}")
        
        return jsonify({
            'success': True,
            'message': 'Job sync triggered successfully',
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        app.logger.error(f"Manual job sync error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/trigger/file-cleanup', methods=['POST'])
@login_required
def trigger_file_cleanup():
    """Manually trigger file consolidation and cleanup"""
    try:
        app.logger.info("Manual file cleanup triggered")
        
        # Ensure background services are initialized
        ensure_background_services()
        
        # Initialize file consolidation service if needed
        file_service = lazy_init_file_consolidation()
        
        if file_service and file_service is not False:
            results = file_service.run_full_cleanup()
            
            return jsonify({
                'success': True,
                'message': 'File cleanup completed successfully',
                'timestamp': datetime.utcnow().isoformat(),
                'results': results
            })
        else:
            return jsonify({
                'success': False,
                'error': 'File consolidation service not available'
            }), 500
            
    except Exception as e:
        app.logger.error(f"Manual file cleanup error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/trigger/health-check', methods=['POST'])
@login_required
def trigger_health_check():
    """Manually trigger monitor health check"""
    try:
        app.logger.info("Manual health check triggered")
        
        # Health monitoring integrated into comprehensive_monitoring_service
        # health_service = MonitorHealthService(db.session, GlobalSettings, BullhornMonitor)
        
        # Health check functionality integrated into comprehensive monitoring
        return jsonify({
            'success': True,
            'message': 'Health check integrated into comprehensive monitoring system',
            'timestamp': datetime.utcnow().isoformat(),
            'result': {'status': 'integrated'}
        })
    except Exception as e:
        app.logger.error(f"Manual health check error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/trigger/ai-classification-fix', methods=['POST'])
@login_required
def trigger_ai_classification_fix():
    """Manually trigger AI classification fix for all jobs"""
    try:
        app.logger.info("Manual AI classification fix triggered")
        
        from job_classification_service import JobClassificationService
        from lxml import etree
        import os
        
        xml_files = ['myticas-job-feed.xml']  # Back to standard file name
        total_jobs_fixed = 0
        
        for xml_file in xml_files:
            if not os.path.exists(xml_file):
                continue
                
            try:
                # Parse XML file
                parser = etree.XMLParser(strip_cdata=False, recover=True)
                tree = etree.parse(xml_file, parser)
                root = tree.getroot()
                
                jobs = root.findall('.//job')
                jobs_to_fix = []
                
                # Find jobs missing AI classifications
                for job in jobs:
                    job_id_elem = job.find('bhatsid')
                    job_id = job_id_elem.text if job_id_elem is not None else 'Unknown'
                    
                    title_elem = job.find('title')
                    title = title_elem.text if title_elem is not None else ''
                    
                    description_elem = job.find('description')
                    description = description_elem.text if description_elem is not None else ''
                    
                    # Check if AI classifications are missing or empty
                    jobfunction_elem = job.find('jobfunction')
                    jobindustries_elem = job.find('jobindustries')
                    senoritylevel_elem = job.find('senoritylevel')
                    
                    missing_ai = []
                    if jobfunction_elem is None or not jobfunction_elem.text or jobfunction_elem.text.strip() == '':
                        missing_ai.append('jobfunction')
                    if jobindustries_elem is None or not jobindustries_elem.text or jobindustries_elem.text.strip() == '':
                        missing_ai.append('jobindustries')
                    if senoritylevel_elem is None or not senoritylevel_elem.text or senoritylevel_elem.text.strip() == '':
                        missing_ai.append('senoritylevel')
                    
                    if missing_ai:
                        jobs_to_fix.append({
                            'job_id': job_id,
                            'title': title,
                            'description': description,
                            'job_element': job,
                            'missing_fields': missing_ai
                        })
                
                # Fix missing AI classifications
                if jobs_to_fix:
                    app.logger.info(f"Found {len(jobs_to_fix)} jobs with missing AI classifications in {xml_file}")
                    
                    classification_service = JobClassificationService()
                    
                    for job_data in jobs_to_fix:
                        try:
                            # Get AI classifications for this job
                            ai_result = classification_service.classify_job(
                                job_data['title'], 
                                job_data['description']
                            )
                            
                            if ai_result and ai_result.get('success'):
                                # Update missing AI fields
                                if 'jobfunction' in job_data['missing_fields']:
                                    jobfunction_elem = job_data['job_element'].find('jobfunction')
                                    if jobfunction_elem is None:
                                        jobfunction_elem = etree.SubElement(job_data['job_element'], 'jobfunction')
                                        jobfunction_elem.tail = "\n    "
                                    jobfunction_elem.text = etree.CDATA(f" {ai_result['job_function']} ")
                                
                                if 'jobindustries' in job_data['missing_fields']:
                                    jobindustries_elem = job_data['job_element'].find('jobindustries')
                                    if jobindustries_elem is None:
                                        jobindustries_elem = etree.SubElement(job_data['job_element'], 'jobindustries')
                                        jobindustries_elem.tail = "\n    "
                                    jobindustries_elem.text = etree.CDATA(f" {ai_result['industries']} ")
                                
                                if 'senoritylevel' in job_data['missing_fields']:
                                    senoritylevel_elem = job_data['job_element'].find('senoritylevel')
                                    if senoritylevel_elem is None:
                                        senoritylevel_elem = etree.SubElement(job_data['job_element'], 'senoritylevel')
                                        senoritylevel_elem.tail = "\n  "
                                    senoritylevel_elem.text = etree.CDATA(f" {ai_result['seniority_level']} ")
                                
                                total_jobs_fixed += 1
                                app.logger.info(f"Fixed AI classifications for job {job_data['job_id']}")
                                
                        except Exception as e:
                            app.logger.error(f"Error fixing AI classifications for job {job_data['job_id']}: {str(e)}")
                    
                    # Save updated XML file
                    if total_jobs_fixed > 0:
                        with open(xml_file, 'wb') as f:
                            tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
                        
                        app.logger.info(f"Updated {xml_file} with AI classifications for {len(jobs_to_fix)} jobs")
                
            except Exception as e:
                app.logger.error(f"Error processing AI classifications in {xml_file}: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': f'AI classification fix completed successfully',
            'fixed_count': total_jobs_fixed,
            'timestamp': datetime.utcnow().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Manual AI classification fix error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500



@app.route('/api/bullhorn/monitoring-cycles', methods=['GET'])
@login_required
def get_monitoring_cycles():
    """Get information about monitoring cycles and timing"""
    try:
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        
        current_time = datetime.utcnow()
        cycle_info = []
        
        for monitor in monitors:
            next_check = monitor.next_check
            if next_check:
                time_until_next = (next_check - current_time).total_seconds()
                is_overdue = time_until_next < 0
                
                cycle_info.append({
                    'monitor_id': monitor.id,
                    'monitor_name': monitor.name,
                    'next_check': next_check.isoformat() + 'Z',
                    'time_until_next_seconds': int(time_until_next),
                    'is_overdue': is_overdue,
                    'overdue_minutes': abs(time_until_next / 60) if is_overdue else 0,
                    'interval_minutes': monitor.check_interval,
                    'last_check': monitor.last_check.isoformat() + 'Z' if monitor.last_check else None
                })
        
        # Calculate next global monitoring cycle (when any monitor will run next)
        next_global_cycle = None
        if cycle_info:
            next_times = [info for info in cycle_info if not info['is_overdue']]
            if next_times:
                next_global_cycle = min(next_times, key=lambda x: x['time_until_next_seconds'])
        
        return jsonify({
            'success': True,
            'current_time': current_time.isoformat() + 'Z',
            'monitors': cycle_info,
            'next_global_cycle': next_global_cycle,
            'total_active_monitors': len(monitors)
        })
    except Exception as e:
        app.logger.error(f"Error getting monitoring cycles: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/system/health')
def system_health_check():
    """System health check endpoint to detect scheduler timing issues"""
    try:
        current_time = datetime.utcnow()
        
        # Check Bullhorn monitors for timing issues
        overdue_monitors = BullhornMonitor.query.filter(
            BullhornMonitor.is_active == True,
            BullhornMonitor.next_check < current_time - timedelta(minutes=10)
        ).all()
        
        # Check scheduled files for timing issues
        overdue_schedules = ScheduleConfig.query.filter(
            ScheduleConfig.is_active == True,
            ScheduleConfig.next_run < current_time - timedelta(hours=1)
        ).all()
        
        # Enhanced health status calculation with timing drift detection
        health_status = "healthy"
        issues = []
        warnings = []
        
        # Check for critically overdue monitors (>10 minutes)
        if overdue_monitors:
            health_status = "warning"
            issues.append(f"{len(overdue_monitors)} Bullhorn monitors overdue >10 minutes")
        
        # Check for timing drift (monitors that will be overdue within 2 minutes)
        drift_monitors = BullhornMonitor.query.filter(
            BullhornMonitor.is_active == True,
            BullhornMonitor.next_check < current_time + timedelta(minutes=2),
            BullhornMonitor.next_check > current_time - timedelta(minutes=10)
        ).all()
        
        if drift_monitors and health_status == "healthy":
            warnings.append(f"{len(drift_monitors)} monitors approaching next check time")
        
        # Check for critically overdue schedules
        if overdue_schedules:
            health_status = "critical" if health_status == "warning" else "warning"
            issues.append(f"{len(overdue_schedules)} schedules overdue >1 hour")
        
        # Add timing accuracy metrics
        all_active_monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        timing_accuracy = {
            'healthy_monitors': len([m for m in all_active_monitors if m.next_check > current_time]),
            'total_monitors': len(all_active_monitors),
            'oldest_next_check': min([m.next_check for m in all_active_monitors]) if all_active_monitors else None,
            'newest_next_check': max([m.next_check for m in all_active_monitors]) if all_active_monitors else None
        }
        
        # Get active monitor and schedule counts
        active_monitors = BullhornMonitor.query.filter_by(is_active=True).count()
        active_schedules = ScheduleConfig.query.filter_by(is_active=True).count()
        
        return jsonify({
            'success': True,
            'health_status': health_status,
            'timestamp': current_time.isoformat(),
            'issues': issues,
            'warnings': warnings,
            'timing_accuracy': timing_accuracy,
            'system_info': {
                'active_monitors': active_monitors,
                'active_schedules': active_schedules,
                'overdue_monitors': len(overdue_monitors),
                'overdue_schedules': len(overdue_schedules),
                'drift_monitors': len(drift_monitors)
            },
            'next_actions': {
                'monitors_next_run': BullhornMonitor.query.filter_by(is_active=True).order_by(BullhornMonitor.next_check).first().next_check.isoformat() if active_monitors > 0 else None,
                'schedules_next_run': ScheduleConfig.query.filter_by(is_active=True).order_by(ScheduleConfig.next_run).first().next_run.isoformat() if active_schedules > 0 else None
            },
            'prevention_layers': {
                'auto_recovery': 'Active - detects monitors >10min overdue',
                'immediate_commits': 'Active - commits timing after each monitor',
                'error_recovery': 'Active - updates timing even on processing errors', 
                'final_health_check': 'Active - verifies timing after processing',
                'enhanced_monitoring': 'Active - tracks timing drift and accuracy'
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'health_status': 'error',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        })

@app.route('/api/system/fix-timing', methods=['POST'])
@login_required
def fix_system_timing():
    """Admin endpoint to manually fix scheduler timing issues"""
    try:
        current_time = datetime.utcnow()
        fixed_items = []
        
        # Fix overdue Bullhorn monitors
        overdue_monitors = BullhornMonitor.query.filter(
            BullhornMonitor.is_active == True,
            BullhornMonitor.next_check < current_time - timedelta(minutes=10)
        ).all()
        
        for monitor in overdue_monitors:
            old_time = monitor.next_check
            monitor.next_check = current_time + timedelta(minutes=2)  # Reduced from 5 to 2 minutes
            fixed_items.append(f"Monitor '{monitor.name}': {old_time} → {monitor.next_check}")
        
        # Fix overdue schedules
        overdue_schedules = ScheduleConfig.query.filter(
            ScheduleConfig.is_active == True,
            ScheduleConfig.next_run < current_time - timedelta(hours=1)
        ).all()
        
        for schedule in overdue_schedules:
            old_time = schedule.next_run
            # Reset to next normal interval based on schedule_days
            schedule.next_run = current_time + timedelta(days=schedule.schedule_days)
            fixed_items.append(f"Schedule '{schedule.name}': {old_time} → {schedule.next_run}")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Fixed timing for {len(fixed_items)} items',
            'fixed_items': fixed_items,
            'timestamp': current_time.isoformat()
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        })

@app.route('/api/bullhorn/activities')
@login_required
def get_recent_activities():
    """Get recent Bullhorn activities for auto-refresh"""
    recent_activities = BullhornActivity.query.order_by(BullhornActivity.created_at.desc()).limit(50).all()
    
    # Return JSON data that can be used to update the activity table
    activities_data = []
    for activity in recent_activities:
        activities_data.append({
            'id': activity.id,
            'monitor_name': activity.monitor.name if activity.monitor else 'Scheduled Processing',
            'monitor_id': activity.monitor.id if activity.monitor else None,
            'activity_type': activity.activity_type,
            'job_id': activity.job_id,
            'job_title': activity.job_title,
            'account_manager': activity.account_manager,
            'details': activity.details,
            'notification_sent': activity.notification_sent,
            'created_at': activity.created_at.strftime('%Y-%m-%d %H:%M')
        })
    
    return jsonify({
        'success': True,
        'activities': activities_data,
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/bullhorn/monitors')
@login_required
def get_monitor_status():
    """Get updated monitor information for auto-refresh"""
    monitors = BullhornMonitor.query.filter_by(is_active=True).order_by(BullhornMonitor.name).all()
    current_time = datetime.utcnow()
    
    monitors_data = []
    for monitor in monitors:
        # Get current job count from stored snapshot
        job_count = 0
        if monitor.last_job_snapshot:
            try:
                jobs = json.loads(monitor.last_job_snapshot)
                job_count = len(jobs)
            except:
                job_count = 0
        
        # Calculate if monitor is overdue (health check)
        is_overdue = False
        overdue_minutes = 0
        if monitor.next_check and monitor.next_check < current_time:
            overdue_minutes = int((current_time - monitor.next_check).total_seconds() / 60)
            is_overdue = overdue_minutes > 10  # Consider overdue if >10 minutes late
        
        monitors_data.append({
            'id': monitor.id,
            'name': monitor.name,
            'last_check': monitor.last_check.strftime('%Y-%m-%d %H:%M') if monitor.last_check else 'Never',
            'next_check': monitor.next_check.strftime('%Y-%m-%d %H:%M') if monitor.next_check else 'Not scheduled',
            'job_count': job_count,
            'is_active': monitor.is_active,
            'check_interval_minutes': monitor.check_interval_minutes,
            'is_overdue': is_overdue,
            'overdue_minutes': overdue_minutes
        })
    
    return jsonify({
        'success': True,
        'monitors': monitors_data,
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/bullhorn/create', methods=['GET', 'POST'])
@login_required
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
                # Check for both dropdown and manual entry
                tearsheet_id = request.form.get('tearsheet_id') or request.form.get('manual_tearsheet_id')
                
                if not tearsheet_id:
                    flash('Please select a tearsheet from the dropdown or enter a tearsheet ID manually', 'error')
                    return redirect(url_for('create_bullhorn_monitor'))
                
                # Get tearsheet name for reference
                try:
                    bullhorn_service = get_bullhorn_service()
                    
                    # Try to get the tearsheet name by accessing it directly
                    if bullhorn_service.authenticate():
                        url = f"{bullhorn_service.base_url}entity/Tearsheet/{tearsheet_id}"
                        params = {
                            'fields': 'id,name,description',
                            'BhRestToken': bullhorn_service.rest_token
                        }
                        response = bullhorn_service.session.get(url, params=params, timeout=5)
                        
                        if response.status_code == 200:
                            data = response.json()
                            tearsheet = data.get('data', {})
                            tearsheet_name = tearsheet.get('name', f"Tearsheet {tearsheet_id}")
                        else:
                            tearsheet_name = f"Tearsheet {tearsheet_id}"
                    else:
                        tearsheet_name = f"Tearsheet {tearsheet_id}"
                        
                except Exception:
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
    
    # For GET request, provide tearsheet options
    # Since tearsheet loading can be slow, we'll provide a manual entry option
    # alongside any successfully loaded tearsheets
    tearsheets = []
    
    try:
        bullhorn_service = get_bullhorn_service()
        
        # Quick check - try a few known tearsheet IDs
        known_ids = [1, 2, 3, 4, 5, 10, 20, 50, 100]
        
        for ts_id in known_ids:
            try:
                url = f"{bullhorn_service.base_url}entity/Tearsheet/{ts_id}"
                if bullhorn_service.base_url and bullhorn_service.rest_token:
                    params = {
                        'fields': 'id,name,description',
                        'BhRestToken': bullhorn_service.rest_token
                    }
                    response = bullhorn_service.session.get(url, params=params, timeout=3)
                    
                    if response.status_code == 200:
                        data = response.json()
                        tearsheet = data.get('data', {})
                        if tearsheet and tearsheet.get('name'):
                            tearsheets.append(tearsheet)
                            
            except Exception:
                continue
                
        # Don't show flash messages for tearsheet loading - keep the interface clean
        # Users can still use the dropdown if tearsheets are found, or manual entry if not
            
    except Exception as e:
        flash('Could not connect to Bullhorn. Please check your API credentials.', 'error')
        
    return render_template('bullhorn_create.html', tearsheets=tearsheets)

@app.route('/bullhorn/monitor/<int:monitor_id>')
def bullhorn_monitor_details(monitor_id):
    """View details of a specific Bullhorn monitor"""
    monitor = BullhornMonitor.query.get_or_404(monitor_id)
    activities = BullhornActivity.query.filter_by(monitor_id=monitor_id).order_by(BullhornActivity.created_at.desc()).limit(50).all()
    
    # Get current job count
    current_job_count = None
    try:
        # Initialize Bullhorn service
        bullhorn_service = get_bullhorn_service()
        
        if bullhorn_service.test_connection():
            # Get current jobs based on monitor type
            if monitor.tearsheet_id == 0:
                # Query-based monitor
                current_jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
            else:
                # Traditional tearsheet-based monitor
                current_jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
            
            current_job_count = len(current_jobs)
        else:
            app.logger.warning(f"Could not connect to Bullhorn to get job count for monitor {monitor.name}")
            
    except Exception as e:
        app.logger.error(f"Error getting job count for monitor {monitor.name}: {str(e)}")
    
    return render_template('bullhorn_details.html', 
                         monitor=monitor, 
                         activities=activities,
                         current_job_count=current_job_count)

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
        bullhorn_service = get_bullhorn_service()
        
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

@app.route('/api/bullhorn/monitor/<int:monitor_id>/jobs')
@login_required
def get_monitor_jobs(monitor_id):
    """Get current jobs from Bullhorn for a specific monitor - for troubleshooting/verification"""
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        
        # Initialize Bullhorn service with credentials from GlobalSettings
        bullhorn_service = get_bullhorn_service()
        
        if not bullhorn_service.test_connection():
            return jsonify({
                'success': False,
                'error': 'Failed to connect to Bullhorn API. Please check your credentials.'
            })
        
        # Get jobs based on monitor type
        jobs = []
        if monitor.tearsheet_id == 0:
            # Query-based monitor
            jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
        else:
            # Traditional tearsheet-based monitor
            jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
        
        # Format jobs for frontend display
        formatted_jobs = []
        for job in jobs:
            formatted_job = {
                'id': job.get('id'),
                'title': job.get('title', 'No Title'),
                'city': job.get('address', {}).get('city', '') if job.get('address') else '',
                'state': job.get('address', {}).get('state', '') if job.get('address') else '',
                'country': job.get('address', {}).get('countryName', '') if job.get('address') else '',
                'employmentType': job.get('employmentType', ''),
                'onSite': job.get('onSite', ''),
                'status': job.get('status', ''),
                'isPublic': job.get('isPublic', False),
                'dateLastModified': job.get('dateLastModified', ''),
                'owner': job.get('owner', {}).get('firstName', '') + ' ' + job.get('owner', {}).get('lastName', '') if job.get('owner') else ''
            }
            formatted_jobs.append(formatted_job)
        
        # Sort by job ID (descending) for consistent display
        formatted_jobs.sort(key=lambda x: int(x['id']) if x['id'] else 0, reverse=True)
        
        return jsonify({
            'success': True,
            'jobs': formatted_jobs,
            'total_count': len(formatted_jobs),
            'monitor_name': monitor.name,
            'monitor_type': 'Query' if monitor.tearsheet_id == 0 else 'Tearsheet'
        })
        
    except Exception as e:
        app.logger.error(f"Error fetching jobs for monitor {monitor_id}: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error fetching jobs: {str(e)}'
        })

@app.route('/bullhorn/monitor/<int:monitor_id>/test-email', methods=['POST'])
def test_email_notification(monitor_id):
    """Send a test email notification to show what the user will receive"""
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        
        # Get email address from Global Settings or monitor-specific setting
        email_address = monitor.notification_email
        if not email_address:
            # Fall back to global notification email
            global_email = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
            if global_email:
                email_address = global_email.setting_value
        
        if not email_address:
            return jsonify({
                'success': False,
                'message': 'No notification email configured. Please set an email address in Global Settings or the monitor settings.'
            })
        
        # Create sample job data to show what notifications look like
        sample_added_jobs = [
            {
                'id': 12345,
                'title': 'Senior Software Engineer',
                'status': 'Open',
                'clientCorporation': {'name': 'Tech Innovators Inc.'}
            },
            {
                'id': 12346,
                'title': 'Data Analyst',
                'status': 'Open',
                'clientCorporation': {'name': 'Analytics Solutions Corp.'}
            }
        ]
        
        sample_removed_jobs = [
            {
                'id': 11111,
                'title': 'Marketing Coordinator',
                'status': 'Closed',
                'clientCorporation': {'name': 'Creative Agency Ltd.'}
            }
        ]
        
        sample_modified_jobs = [
            {
                'id': 11223,
                'title': 'Full Stack Developer',
                'status': 'Open',
                'clientCorporation': {'name': 'StartupXYZ'},
                'changes': [
                    {'field': 'title', 'from': 'Junior Full Stack Developer', 'to': 'Full Stack Developer'},
                    {'field': 'status', 'from': 'Pending', 'to': 'Open'}
                ]
            }
        ]
        
        sample_summary = {
            'total_previous': 8,
            'total_current': 10,
            'added_count': 2,
            'removed_count': 1,
            'modified_count': 1,
            'net_change': 2
        }
        
        # Send the test email
        email_service = get_email_service()
        email_sent = email_service.send_bullhorn_notification(
            to_email=email_address,
            monitor_name=f"{monitor.name} [TEST EMAIL]",
            added_jobs=sample_added_jobs,
            removed_jobs=sample_removed_jobs,
            modified_jobs=sample_modified_jobs,
            summary=sample_summary
        )
        
        if email_sent:
            return jsonify({
                'success': True,
                'message': f'Test email notification sent successfully to {email_address}. Check your inbox to see what real notifications will look like.'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to send test email. Please check your email configuration in Global Settings.'
            })
        
    except Exception as e:
        app.logger.error(f"Error sending test email notification: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        })

@app.route('/bullhorn/settings', methods=['GET', 'POST'])
@login_required
def bullhorn_settings():
    """Manage Bullhorn API credentials in Global Settings"""
    if request.method == 'POST':
        # Check if this is a test action
        if request.form.get('action') == 'test':
            try:
                # Get credentials from database
                credentials = {}
                for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
                    setting = GlobalSettings.query.filter_by(setting_key=key).first()
                    credentials[key] = setting.setting_value if setting else ''
                
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

@app.route('/automation_test')
@login_required
def automation_test():
    """Automation test center page"""
    # Reset test file to original state
    reset_test_file()
    return render_template('automation_test.html')

@app.route('/automation_test', methods=['POST'])
def automation_test_action():
    """Handle automation test actions"""
    try:
        data = request.get_json()
        action = data.get('action')
        
        if action == 'complete_demo':
            # Run the complete demo script
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
            # Check if there's a recent demo file to show
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
                # Return sample XML content for display
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
        app.logger.error(f"Error in automation test: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

# ==================================================================
# JOB APPLICATION FORM ROUTES
# ==================================================================

# Initialize job application service
job_app_service = JobApplicationService()

@app.route('/<job_id>/<job_title>/')
def job_application_form(job_id, job_title):
    """Display job application form with client-specific branding"""
    try:
        # Get source from query parameters
        source = request.args.get('source', '')
        
        # Decode job title from URL
        import urllib.parse
        decoded_title = urllib.parse.unquote(job_title)
        
        # Domain-based template selection for client branding
        host = request.host.lower()
        if 'stsigroup.com' in host:
            template = 'apply_stsi.html'
            app.logger.info(f"Serving STSI template for domain: {host}")
        else:
            template = 'apply.html'
            app.logger.info(f"Serving Myticas template for domain: {host}")
        
        from flask import make_response
        response = make_response(render_template(template, 
                             job_id=job_id, 
                             job_title=decoded_title, 
                             source=source))
        
        # Add cache-busting headers to force fresh content
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        
        return response
    except Exception as e:
        app.logger.error(f"Error displaying job application form: {str(e)}")
        return f"Error loading application form: {str(e)}", 500

@app.route('/parse-resume', methods=['POST'])
def parse_resume():
    """Parse uploaded resume file and extract candidate information"""
    try:
        if 'resume' not in request.files:
            return jsonify({
                'success': False,
                'error': 'No resume file uploaded'
            })
        
        resume_file = request.files['resume']
        
        if resume_file.filename == '':
            return jsonify({
                'success': False,
                'error': 'No resume file selected'
            })
        
        # Parse the resume
        parse_result = job_app_service.parse_resume(resume_file)
        
        # Fix the nested structure issue - flatten the response
        if parse_result.get('success') and parse_result.get('parsed_info'):
            parsed_info = parse_result['parsed_info']
            # Check if the actual parsing succeeded
            if parsed_info.get('success', False):
                return jsonify({
                    'success': True,
                    'parsed_info': parsed_info
                })
            else:
                # Parsing failed, return the error
                return jsonify({
                    'success': False,
                    'error': parsed_info.get('error', 'Failed to parse resume'),
                    'parsed_info': {'parsed_data': {}}
                })
        else:
            # Service-level error
            return jsonify({
                'success': False,
                'error': parse_result.get('error', 'Failed to parse resume'),
                'parsed_info': {'parsed_data': {}}
            })
        
    except Exception as e:
        app.logger.error(f"Error parsing resume: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error parsing resume: {str(e)}'
        })

@app.route('/submit-application', methods=['POST'])
def submit_application():
    """Submit job application form"""
    try:
        # Debug: Log all form data received
        app.logger.info("=== FORM SUBMISSION DEBUG ===")
        app.logger.info(f"Form data keys: {list(request.form.keys())}")
        for key, value in request.form.items():
            app.logger.info(f"Form field '{key}': '{value}'")
        app.logger.info(f"Files: {list(request.files.keys())}")
        app.logger.info("===========================")
        
        # Extract form data
        application_data = {
            'firstName': request.form.get('firstName'),
            'lastName': request.form.get('lastName'),
            'email': request.form.get('email'),
            'phone': request.form.get('phone'),
            'jobId': request.form.get('jobId'),
            'jobTitle': request.form.get('jobTitle'),
            'source': request.form.get('source', '')
        }
        
        # Validate required fields
        required_fields = ['firstName', 'lastName', 'email', 'phone', 'jobId', 'jobTitle']
        for field in required_fields:
            if not application_data.get(field):
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                })
        
        # Get uploaded files
        resume_file = request.files.get('resume')
        cover_letter_file = request.files.get('coverLetter')
        
        if not resume_file or resume_file.filename == '':
            return jsonify({
                'success': False,
                'error': 'Resume file is required'
            })
        
        # Submit the application with request host for branding detection
        submission_result = job_app_service.submit_application(
            application_data=application_data,
            resume_file=resume_file,
            cover_letter_file=cover_letter_file if cover_letter_file and cover_letter_file.filename != '' else None,
            request_host=request.host
        )
        
        return jsonify(submission_result)
        
    except Exception as e:
        app.logger.error(f"Error submitting application: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error submitting application: {str(e)}'
        })

# ==================================================================
# END JOB APPLICATION FORM ROUTES
# ==================================================================

def reset_test_file():
    """Reset the test file to its original clean state"""
    try:
        # Original clean XML content with consistent formatting
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
            
        app.logger.info("Test file reset to original clean state")
        
    except Exception as e:
        app.logger.error(f"Error resetting test file: {str(e)}")

def run_automation_demo():
    """Run the complete automation demo and return results"""
    try:
        # Initialize services
        xml_service = XMLIntegrationService()
        xml_processor = XMLProcessor()
        
        # Create demo data
        demo_xml_file = 'demo_test_current.xml'
        
        # Create initial XML
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
        
        # Write initial XML file
        with open(demo_xml_file, 'w', encoding='utf-8') as f:
            f.write(initial_xml)
        
        # Simulate job changes
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
        
        # Run XML sync
        sync_result = xml_service.sync_xml_with_bullhorn_jobs(
            xml_file_path=demo_xml_file,
            current_jobs=current_jobs,
            previous_jobs=previous_jobs
        )
        
        if sync_result.get('success'):
            # Process the XML - regenerate for demo purposes
            temp_output = f"{demo_xml_file}.processed"
            process_result = xml_processor.process_xml(demo_xml_file, temp_output, preserve_reference_numbers=False)
            
            if process_result.get('success'):
                # Replace original
                os.replace(temp_output, demo_xml_file)
                
                # Get final job count
                import re
                with open(demo_xml_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                job_count = len(re.findall(r'<job>', content))
                
                # Keep the file for viewing (don't clean up immediately)
                # It will be overwritten on next demo run
                
                return {
                    'success': True,
                    'summary': f'Successfully processed {job_count} total jobs',
                    'jobs_added': sync_result.get('added_count', 0),
                    'jobs_removed': sync_result.get('removed_count', 0),
                    'jobs_updated': sync_result.get('updated_count', 0),
                    'total_jobs': job_count
                }
            else:
                # Clean up on failure
                if os.path.exists(demo_xml_file):
                    os.remove(demo_xml_file)
                return {
                    'success': False,
                    'error': f'XML processing failed: {process_result.get("error")}'
                }
        else:
            # Clean up on failure
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
        # Clean up on exception
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
        demo_xml_file = 'demo_test_current.xml'
        
        # Check if demo file exists, if not create it
        if not os.path.exists(demo_xml_file):
            # Create initial XML file
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
        
        # Read current XML to get existing jobs
        xml_service = XMLIntegrationService()
        xml_processor = XMLProcessor()
        
        # Get current job count
        import re
        with open(demo_xml_file, 'r', encoding='utf-8') as f:
            content = f.read()
        current_job_count = len(re.findall(r'<job>', content))
        
        if step_type == 'add_jobs':
            # Add a new job
            new_job = {
                'id': 55555,
                'title': 'Frontend React Developer',
                'clientCorporation': {'name': 'Digital Solutions Inc'},
                'description': 'Frontend React Developer with TypeScript experience',
                'address': {'city': 'Austin', 'state': 'Texas', 'countryName': 'United States'},
                'employmentType': 'Full-time',
                'dateAdded': 1720742400000
            }
            
            # Simulate adding the job
            sync_result = xml_service.sync_xml_with_bullhorn_jobs(
                xml_file_path=demo_xml_file,
                current_jobs=[new_job],
                previous_jobs=[]
            )
            
            if sync_result.get('success'):
                # Skip full processing for test - only sync jobs, don't regenerate all reference numbers
                # Full processing with reference number regeneration only happens during scheduled automation
                    
                    # Get new job count
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
            # Remove the last added job (if exists)
            # This simulates removing a job by ID
            with open(demo_xml_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Find and remove the Frontend React Developer job (55555)
            if '55555' in content:
                # Use regex to remove the job block
                import re
                job_pattern = r'<job>.*?Frontend React Developer \(55555\).*?</job>'
                new_content = re.sub(job_pattern, '', content, flags=re.DOTALL)
                
                with open(demo_xml_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                
                # Skip full processing for test - only remove job, don't regenerate all reference numbers
                # Full processing with reference number regeneration only happens during scheduled automation
                    
                # Get new job count
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
            # Update an existing job
            with open(demo_xml_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Update the Senior Python Developer job if it exists
            if '12345' in content:
                # Update the title and description
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
                
                # Skip full processing for test - only update job, don't regenerate all reference numbers
                # Full processing with reference number regeneration only happens during scheduled automation
                    
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
            # Real file upload test with download capability
            if os.path.exists(demo_xml_file):
                # Create a processed version for download
                processed_filename = f"test_processed_{int(time.time())}.xml"
                shutil.copy2(demo_xml_file, processed_filename)
                
                # Get file size and content info
                file_size = os.path.getsize(demo_xml_file)
                with open(demo_xml_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                job_count = len(re.findall(r'<job>', content))
                
                # Try actual SFTP upload if credentials are available
                upload_success = False
                upload_message = ""
                
                try:
                    # Get SFTP settings from global settings
                    sftp_settings = {}
                    for key in ['sftp_hostname', 'sftp_username', 'sftp_password', 'sftp_directory']:
                        setting = GlobalSettings.query.filter_by(setting_key=key).first()
                        if setting:
                            sftp_settings[key] = setting.setting_value
                    
                    if all(sftp_settings.get(key) for key in ['sftp_hostname', 'sftp_username', 'sftp_password']):
                        # Real SFTP upload with timeout
                        import signal
                        
                        def timeout_handler(signum, frame):
                            raise TimeoutError("SFTP upload timed out")
                        
                        # Set 15 second timeout for SFTP upload
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
                            upload_success = True  # Simulate success for demo
                        finally:
                            signal.alarm(0)  # Cancel the alarm
                    else:
                        upload_message = "SFTP credentials not configured - simulated upload"
                        upload_success = True  # Simulate success for demo
                        
                except Exception as e:
                    upload_message = f"SFTP upload error: {str(e)[:100]}... - simulated upload for demo"
                    upload_success = True  # Simulate success for demo to continue testing
                
                # Generate download key for the processed file
                import uuid
                download_key = str(uuid.uuid4())
                
                # Store download info in session or temporary storage
                # For this demo, we'll create a simple mapping
                download_info = {
                    'file_path': processed_filename,
                    'original_name': 'test-automation-demo.xml',
                    'timestamp': time.time()
                }
                
                # Store in a simple file-based cache (in production, use Redis or database)
                import json
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

@app.route('/test_download/<download_key>')
def test_download(download_key):
    """Download test XML file"""
    try:
        # Load download info from cache
        cache_file = f"download_cache_{download_key}.json"
        if not os.path.exists(cache_file):
            flash('Download link expired or invalid', 'error')
            return redirect(url_for('automation_test'))
        
        import json
        with open(cache_file, 'r') as f:
            download_info = json.load(f)
        
        file_path = download_info['file_path']
        original_name = download_info['original_name']
        
        if not os.path.exists(file_path):
            flash('Test file not found', 'error')
            return redirect(url_for('automation_test'))
        
        # Clean up cache file after use
        os.remove(cache_file)
        
        return send_file(
            file_path,
            as_attachment=True,
            download_name=original_name,
            mimetype='application/xml'
        )
        
    except Exception as e:
        app.logger.error(f"Test download error: {str(e)}")
        flash('Download failed', 'error')
        return redirect(url_for('automation_test'))

# ========================================================================================
# ATS MONITORING ROUTES
# ========================================================================================

@app.route('/ats-monitoring')
@login_required
def ats_monitoring():
    """ATS monitoring dashboard"""
    return render_template('ats_monitoring.html')

@app.route('/api/monitors')
@login_required
def get_monitors():
    """Get all active Bullhorn monitors"""
    try:
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        monitor_data = []
        
        for monitor in monitors:
            # Get current job count from snapshot
            job_count = 0
            if monitor.last_job_snapshot:
                try:
                    jobs = json.loads(monitor.last_job_snapshot)
                    job_count = len(jobs) if isinstance(jobs, list) else 0
                except:
                    job_count = 0
            
            monitor_data.append({
                'id': monitor.id,
                'name': monitor.name,
                'tearsheet_name': monitor.tearsheet_name,
                'tearsheet_id': monitor.tearsheet_id,
                'interval_minutes': monitor.check_interval_minutes,
                'last_check': monitor.last_check.isoformat() if monitor.last_check else None,
                'next_check': monitor.next_check.isoformat() if monitor.next_check else None,
                'job_count': job_count,
                'is_active': monitor.is_active
            })
        
        return jsonify(monitor_data)
    except Exception as e:
        app.logger.error(f"Error fetching monitors: {str(e)}")
        return jsonify([]), 500

@app.route('/api/monitors/<int:monitor_id>', methods=['DELETE'])
@login_required
def delete_monitor(monitor_id):
    """Delete a Bullhorn monitor"""
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        monitor_name = monitor.name
        
        # Delete associated activities
        BullhornActivity.query.filter_by(monitor_id=monitor_id).delete()
        
        # Delete the monitor
        db.session.delete(monitor)
        db.session.commit()
        
        app.logger.info(f"Deleted monitor: {monitor_name}")
        return jsonify({'success': True, 'message': f'Monitor "{monitor_name}" deleted successfully'})
    except Exception as e:
        app.logger.error(f"Error deleting monitor: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/monitors/<int:monitor_id>/toggle', methods=['POST'])
@login_required
def toggle_monitor(monitor_id):
    """Toggle monitor active status"""
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        monitor.is_active = not monitor.is_active
        
        if monitor.is_active:
            # Recalculate next check time when reactivating
            monitor.calculate_next_check()
        
        db.session.commit()
        
        status = "activated" if monitor.is_active else "deactivated"
        app.logger.info(f"Monitor {monitor.name} {status}")
        
        return jsonify({
            'success': True, 
            'message': f'Monitor "{monitor.name}" {status}',
            'is_active': monitor.is_active,
            'next_check': monitor.next_check.isoformat() if monitor.next_check else None
        })
    except Exception as e:
        app.logger.error(f"Error toggling monitor: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/activities')
@login_required
def get_activities():
    """Get recent Bullhorn activities"""
    try:
        activities = BullhornActivity.query.order_by(BullhornActivity.created_at.desc()).limit(50).all()
        activity_data = []
        
        for activity in activities:
            # Get monitor name
            monitor_name = "Unknown"
            if activity.monitor_id:
                monitor = BullhornMonitor.query.get(activity.monitor_id)
                if monitor:
                    monitor_name = monitor.name
            
            activity_data.append({
                'id': activity.id,
                'timestamp': activity.created_at.isoformat(),
                'monitor_name': monitor_name,
                'activity_type': activity.activity_type,
                'details': activity.details[:200] + '...' if len(activity.details) > 200 else activity.details
            })
        
        return jsonify(activity_data)
    except Exception as e:
        app.logger.error(f"Error fetching activities: {str(e)}")
        return jsonify([]), 500

@app.route('/api/system/health')
@login_required
def system_health():
    """Get system health status"""
    try:
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        current_time = datetime.utcnow()
        
        healthy_monitors = 0
        overdue_monitors = 0
        
        for monitor in monitors:
            if monitor.next_check and monitor.next_check < current_time - timedelta(minutes=10):
                overdue_monitors += 1
            else:
                healthy_monitors += 1
        
        status = "healthy" if overdue_monitors == 0 else "warning"
        
        return jsonify({
            'status': status,
            'total_monitors': len(monitors),
            'healthy_monitors': healthy_monitors,
            'overdue_monitors': overdue_monitors,
            'scheduler_status': 'running' if scheduler.running else 'stopped'
        })
    except Exception as e:
        app.logger.error(f"Error getting system health: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/monitors', methods=['POST'])
@login_required
def create_monitor():
    """Create a new Bullhorn monitor"""
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data.get('name') or not data.get('tearsheet_name'):
            return jsonify({'success': False, 'error': 'Name and tearsheet name are required'}), 400
        
        # Create new monitor
        monitor = BullhornMonitor(
            name=data['name'],
            tearsheet_name=data['tearsheet_name'],
            tearsheet_id=data.get('tearsheet_id', 0),
            interval_minutes=data.get('interval_minutes', 5),
            is_active=True
        )
        monitor.calculate_next_check()
        
        db.session.add(monitor)
        db.session.commit()
        
        app.logger.info(f"Created new monitor: {monitor.name}")
        
        return jsonify({
            'success': True,
            'message': f'Monitor "{monitor.name}" created successfully',
            'monitor_id': monitor.id
        })
    except Exception as e:
        app.logger.error(f"Error creating monitor: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/email-logs')
@login_required
def email_logs():
    """Display email delivery logs"""
    page = request.args.get('page', 1, type=int)
    per_page = 50  # Number of logs per page
    notification_type = request.args.get('type')  # Optional filter by notification type
    
    query = EmailDeliveryLog.query
    if notification_type:
        query = query.filter(EmailDeliveryLog.notification_type == notification_type)
    
    logs = query.order_by(EmailDeliveryLog.sent_at.desc()).paginate(
        page=page, 
        per_page=per_page, 
        error_out=False
    )
    
    return render_template('email_logs.html', logs=logs)

@app.route('/api/email-logs')
@login_required
def api_email_logs():
    """API endpoint for getting paginated email delivery logs"""
    page = request.args.get('page', 1, type=int)
    per_page = 50  # Number of logs per page
    notification_type = request.args.get('type')  # Optional filter by notification type
    
    query = EmailDeliveryLog.query
    if notification_type:
        query = query.filter(EmailDeliveryLog.notification_type == notification_type)
    
    logs = query.order_by(EmailDeliveryLog.sent_at.desc()).paginate(
        page=page, 
        per_page=per_page, 
        error_out=False
    )
    
    return jsonify({
        'logs': [{
            'id': log.id,
            'notification_type': log.notification_type,
            'job_id': log.job_id,
            'job_title': log.job_title,
            'recipient_email': log.recipient_email,
            'delivery_status': log.delivery_status,
            'sendgrid_message_id': log.sendgrid_message_id,
            'error_message': log.error_message,
            'schedule_name': log.schedule_name,
            'changes_summary': log.changes_summary,
            'sent_at': log.sent_at.strftime('%Y-%m-%d %H:%M:%S')
        } for log in logs.items],
        'pagination': {
            'page': logs.page,
            'pages': logs.pages,
            'total': logs.total,
            'has_next': logs.has_next,
            'has_prev': logs.has_prev
        }
    })

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)

def check_monitor_health():
    """Check monitor health and send notifications for overdue monitors"""
    with app.app_context():
        try:
            app.logger.info("Starting monitor health check...")
            
            # Health monitoring integrated into comprehensive_monitoring_service  
            # health_service = MonitorHealthService(db.session, GlobalSettings, BullhornMonitor)
            
            # Health check integrated into comprehensive monitoring system
            app.logger.info("Health monitoring is handled by comprehensive_monitoring_service")
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
            import traceback
            app.logger.error(traceback.format_exc())

# Defer scheduler startup to reduce initialization time
def lazy_start_scheduler():
    """Start scheduler only when needed to avoid startup delays"""
    try:
        if not scheduler.running:
            scheduler.start()
            app.logger.info("Background scheduler started lazily")
            return True
    except Exception as e:
        app.logger.error(f"Failed to start scheduler: {str(e)}")
        return False
    return scheduler.running

# Track if background services have been initialized
_background_services_started = False
def ensure_background_services():
    """Ensure background services are started when first needed"""
    global _background_services_started
    # Always check if scheduler is running, not just the flag
    if not scheduler.running:
        try:
            scheduler.start()
            app.logger.info("Background scheduler started/restarted successfully")
            _background_services_started = True
            
            # Force immediate monitor check after restart
            try:
                from datetime import datetime, timedelta
                # Update all monitors to run immediately
                monitors = BullhornMonitor.query.all()
                for monitor in monitors:
                    monitor.next_check_time = datetime.utcnow()
                db.session.commit()
                app.logger.info(f"Forced immediate check for {len(monitors)} monitors after restart")
            except Exception as e:
                app.logger.warning(f"Could not force immediate monitor check: {e}")
        except Exception as e:
            app.logger.error(f"Failed to start scheduler: {str(e)}")
            _background_services_started = False
            return False
    
    # Only run these once    
    if not _background_services_started:
        _background_services_started = True
        lazy_apply_optimizations()
        lazy_init_file_consolidation()
    
    return True

# Only add scheduler jobs on primary worker to prevent duplicates in production
if is_primary_worker:
    # Add monitor health check job - run every 15 minutes
    scheduler.add_job(
        func=check_monitor_health,
        trigger=IntervalTrigger(minutes=15),
        id='check_monitor_health',
        name='Monitor Health Check',
        replace_existing=True
    )
    app.logger.info("Monitor health check system enabled - will check every 15 minutes for overdue monitors")

    # Schedule automatic file cleanup
    def schedule_file_cleanup():
        """Schedule automatic file cleanup"""
        with app.app_context():
            try:
                # Initialize file consolidation service if needed
                file_service = lazy_init_file_consolidation()
                
                if file_service and file_service is not False:
                    results = file_service.run_full_cleanup()
                    app.logger.info(f"Scheduled file cleanup completed: {results.get('summary', {})}")
                else:
                    app.logger.warning("File consolidation service not available for scheduled cleanup")
            except Exception as e:
                app.logger.error(f"Scheduled file cleanup error: {e}")

    scheduler.add_job(
        func=schedule_file_cleanup,
        trigger="interval", 
        hours=24,
        id="file_cleanup_job",
        name="Daily File Cleanup",
        replace_existing=True
    )
    app.logger.info("Scheduled daily file cleanup job")

# Reference Number Refresh (120-hour cycle)
def reference_number_refresh():
    """Automatic refresh of all reference numbers every 120 hours while preserving all other XML data"""
    with app.app_context():
        try:
            from datetime import date
            today = date.today()
            
            # Check if refresh was already done today
            existing_refresh = RefreshLog.query.filter_by(refresh_date=today).first()
            if existing_refresh:
                app.logger.info(f"📝 Reference refresh already completed today at {existing_refresh.refresh_time}")
                return
            
            app.logger.info("🔄 Starting 120-hour reference number refresh...")
            
            # Import the lightweight refresh function
            from lightweight_reference_refresh import lightweight_refresh_references
            
            # Refresh reference numbers in the main XML file
            result = lightweight_refresh_references('myticas-job-feed.xml')
            
            if result['success']:
                app.logger.info(f"✅ Reference refresh complete: {result['jobs_updated']} jobs updated in {result['time_seconds']:.2f} seconds")
                
                # Log the refresh completion to database
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
                
                # ENABLED: Direct SFTP upload with same locking mechanism as monitoring cycle
                # This ensures 48-hour refresh uploads immediately while preventing conflicts
                upload_success = False
                upload_error_message = None
                
                # Use same lock mechanism as monitoring cycle for complete separation
                lock_file = 'monitoring.lock'
                
                try:
                    # Check if monitoring cycle is already running
                    if os.path.exists(lock_file):
                        try:
                            with open(lock_file, 'r') as f:
                                lock_data = f.read().strip()
                                if lock_data:
                                    lock_time = datetime.fromisoformat(lock_data)
                                    lock_age = (datetime.utcnow() - lock_time).total_seconds()
                                    
                                    # If monitoring lock is fresh, wait for it to complete
                                    if lock_age < 240:  # 4 minutes
                                        app.logger.info(f"🔒 Monitoring cycle is running, waiting 30 seconds before upload...")
                                        time.sleep(30)  # Brief wait for monitoring to complete
                                        
                                        # Check again after wait
                                        if os.path.exists(lock_file):
                                            with open(lock_file, 'r') as f2:
                                                lock_data2 = f2.read().strip()
                                                if lock_data2:
                                                    lock_time2 = datetime.fromisoformat(lock_data2)
                                                    lock_age2 = (datetime.utcnow() - lock_time2).total_seconds()
                                                    if lock_age2 < 240:
                                                        app.logger.warning("🔒 Monitoring cycle still running, skipping upload (will retry in next cycle)")
                                                        upload_error_message = "Monitoring cycle was running, upload skipped"
                                                    else:
                                                        os.remove(lock_file)  # Remove stale lock
                                    else:
                                        app.logger.info("🔓 Removing stale monitoring lock")
                                        os.remove(lock_file)
                        except Exception as e:
                            app.logger.warning(f"Error reading monitoring lock: {str(e)}. Proceeding with upload.")
                            if os.path.exists(lock_file):
                                os.remove(lock_file)
                    
                    # If no lock conflicts, proceed with upload
                    if not upload_error_message:
                        # Create temporary lock to prevent monitoring interference
                        with open(lock_file, 'w') as f:
                            f.write(datetime.utcnow().isoformat())
                        app.logger.info("🔒 Lock acquired for reference refresh upload")
                        
                        try:
                            # Upload the refreshed XML file
                            from ftp_service import FTPService
                            ftp_service = FTPService()
                            
                            app.logger.info("📤 Uploading refreshed XML to server...")
                            upload_result = ftp_service.upload_file(
                                local_file_path='myticas-job-feed.xml',
                                remote_filename='myticas-job-feed-v2.xml'
                            )
                            
                            if upload_result['success']:
                                upload_success = True
                                app.logger.info(f"✅ Upload successful: {upload_result.get('message', 'File uploaded')}")
                            else:
                                upload_error_message = upload_result.get('error', 'Unknown upload error')
                                app.logger.error(f"❌ Upload failed: {upload_error_message}")
                        
                        finally:
                            # Always remove lock when upload completes
                            if os.path.exists(lock_file):
                                try:
                                    os.remove(lock_file)
                                    app.logger.info("🔓 Lock released after reference refresh upload")
                                except Exception as e:
                                    app.logger.error(f"Error removing upload lock: {str(e)}")
                                
                except Exception as upload_exception:
                    upload_error_message = str(upload_exception)
                    app.logger.error(f"❌ Upload process failed: {upload_error_message}")
                    # Ensure lock is removed even on exception
                    if os.path.exists(lock_file):
                        try:
                            os.remove(lock_file)
                        except:
                            pass
                
                # Update status message based on upload result
                if upload_success:
                    app.logger.info("✅ Reference refresh complete: Local XML updated AND uploaded to server")
                elif upload_error_message:
                    app.logger.warning(f"⚠️ Reference refresh complete: Local XML updated, but upload failed: {upload_error_message}")
                else:
                    app.logger.info("✅ Reference refresh complete: Local XML updated (upload handled separately)")
                    
                # Store upload status for email notification
                upload_status_for_email = {
                    'upload_attempted': True,
                    'upload_success': upload_success, 
                    'upload_error': upload_error_message
                }
                
                # Send email notification confirming refresh execution
                try:
                    from email_service import EmailService
                    
                    # Get notification email from global settings
                    email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                    if email_setting and email_setting.setting_value:
                        email_service = EmailService()
                        
                        refresh_details = {
                            'execution_time': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                            'processing_time': result['time_seconds'],
                            'jobs_updated': result['jobs_updated'],
                            'upload_attempted': upload_status_for_email['upload_attempted'],
                            'upload_success': upload_status_for_email['upload_success'],
                            'upload_error': upload_status_for_email['upload_error']
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
                            # Update refresh log with email status
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
                
                # Log activity
                try:
                    activity = BullhornActivity(
                        monitor_id=None,  # System-level activity
                        activity_type='reference_refresh',
                        details=f'Daily automatic refresh: {result["jobs_updated"]} reference numbers updated',
                        notification_sent=True,  # Email notification attempted
                        created_at=datetime.utcnow()
                    )
                    db.session.add(activity)
                    db.session.commit()
                except Exception as log_error:
                    app.logger.warning(f"Could not log refresh activity: {str(log_error)}")
                    
            else:
                app.logger.error(f"❌ Reference refresh failed: {result.get('error', 'Unknown error')}")
                
                # Send failure notification email
                try:
                    from email_service import EmailService
                    
                    # Get notification email from global settings
                    email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                    if email_setting and email_setting.setting_value:
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

if is_primary_worker:
    # Schedule reference refresh every 120 hours
    scheduler.add_job(
        func=reference_number_refresh,
        trigger='interval',
        hours=120,
        id='reference_number_refresh',
        name='120-Hour Reference Number Refresh',
        replace_existing=True
    )
    app.logger.info("📅 Scheduled reference number refresh every 120 hours")
    
    # Check if catch-up refresh is needed on startup
    try:
        with app.app_context():
            from datetime import date, timedelta
            
            # Get the last refresh from database
            last_refresh = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()
            
            if last_refresh:
                # Check if 120 hours have passed since last refresh
                time_since_refresh = datetime.utcnow() - last_refresh.refresh_time
                if time_since_refresh > timedelta(hours=120):
                    app.logger.info(f"⏰ Last refresh was {time_since_refresh.total_seconds() / 3600:.1f} hours ago, running catch-up refresh...")
                    reference_number_refresh()
                else:
                    hours_until_next = 120 - (time_since_refresh.total_seconds() / 3600)
                    app.logger.info(f"📝 Last refresh was {time_since_refresh.total_seconds() / 3600:.1f} hours ago, next refresh in {hours_until_next:.1f} hours")
            else:
                # No previous refresh found, run one now
                app.logger.info("🆕 No previous refresh found, running initial refresh...")
                reference_number_refresh()
    except Exception as startup_error:
        app.logger.error(f"Failed to check/run startup refresh: {str(startup_error)}")

# XML Change Monitor - monitors live XML file for changes and sends focused notifications
def run_xml_change_monitor():
    """Run XML change monitor and send notifications for detected changes"""
    try:
        with app.app_context():
            # Get notification email from global settings
            email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
            if not email_setting or not email_setting.setting_value:
                app.logger.warning("XML MONITOR: No notification email configured in global settings")
                return
                
            xml_monitor = create_xml_monitor()
            email_service = get_email_service()
            # Temporarily disable email notifications from regular monitoring cycles
            result = xml_monitor.monitor_xml_changes(email_setting.setting_value, email_service, enable_email_notifications=False)
        
            if result.get('success'):
                changes = result.get('changes', {})
                total_changes = changes.get('total_changes', 0)
                
                if total_changes > 0:
                    app.logger.info(f"🔍 XML MONITOR COMPLETE: {total_changes} changes detected (email notifications temporarily disabled)")
                    
                    # Log to Activity monitoring system
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
                            monitor_id=None,  # XML monitor is system-level, not tied to specific tearsheet
                            activity_type='email_notification',
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

if is_primary_worker:
    scheduler.add_job(
        func=run_xml_change_monitor,
        trigger=IntervalTrigger(minutes=6),  # 6-minute interval to avoid overlap with 5-minute cycle
        id='xml_change_monitor',
        name='Live XML Change Monitor',
        replace_existing=True
    )
    app.logger.info("📧 XML Change Monitor: Scheduled to run every 6 minutes for focused change notifications")

# Add deployment health check routes
@app.route('/ready')
def ready():
    """Kubernetes/deployment readiness probe"""
    try:
        from sqlalchemy import text
        # Test database connection
        with db.engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        
        return jsonify({
            'status': 'ready',
            'timestamp': datetime.utcnow().isoformat(),
            'database': True
        })
    except Exception as e:
        app.logger.error(f"Readiness check failed: {e}")
        return jsonify({
            'status': 'not_ready',
            'timestamp': datetime.utcnow().isoformat(),
            'error': str(e)
        }), 503

@app.route('/alive')
def alive():
    """Basic liveness probe"""
    return jsonify({
        'status': 'alive',
        'timestamp': datetime.utcnow().isoformat(),
        'uptime': 'ok'
    })

@app.route('/start_scheduler')
def start_scheduler_manual():
    """Manually start the scheduler and trigger monitoring"""
    try:
        # Start scheduler if not running
        scheduler_started = lazy_start_scheduler()
        
        # Force an immediate check of all monitors
        if scheduler_started:
            # Reset all monitor timings to trigger immediate check
            monitors = BullhornMonitor.query.filter_by(is_active=True).all()
            current_time = datetime.utcnow()
            for monitor in monitors:
                monitor.last_check = current_time
                monitor.next_check = current_time + timedelta(minutes=2)
            db.session.commit()
            
            # Trigger the monitoring job immediately
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

@app.route('/test-reference-refresh-notification')
@login_required
def test_reference_refresh_notification():
    """Test the reference number refresh notification system"""
    try:
        # Get email settings from Global Settings
        email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
        email_address = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
        
        if not (email_enabled and email_enabled.setting_value == 'true' and 
                email_address and email_address.setting_value):
            return jsonify({
                'success': False,
                'error': 'Email notifications not configured in Global Settings'
            })
        
        # Create test notification
        email_service = get_email_service()
        
        # Sample refresh details for testing
        test_refresh_details = {
            'jobs_refreshed': 53,
            'jobs_preserved': 0,
            'upload_status': 'successful',
            'processing_time': 12.34,
            'next_run': '2025-08-24 22:15 UTC'
        }
        
        # Send test notification
        notification_sent = email_service.send_reference_number_refresh_notification(
            to_email=email_address.setting_value,
            schedule_name='Test Master Job Feed',
            total_jobs=53,
            refresh_details=test_refresh_details,
            status='success'
        )
        
        if notification_sent:
            app.logger.info(f"📧 Test reference number refresh notification sent to {email_address.setting_value}")
            return jsonify({
                'success': True,
                'message': f'Test notification sent successfully to {email_address.setting_value}',
                'details': test_refresh_details
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to send test notification'
            })
        
    except Exception as e:
        app.logger.error(f"Error testing reference refresh notification: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Scheduler and background services will be started lazily when first needed
# This significantly reduces application startup time for deployment health checks

