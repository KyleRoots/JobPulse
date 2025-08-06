import os
import logging
from datetime import datetime
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
from xml_integration_service import XMLIntegrationService
from monitor_health_service import MonitorHealthService
from job_application_service import JobApplicationService
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
User, ScheduleConfig, ProcessingLog, GlobalSettings, BullhornMonitor, BullhornActivity, TearsheetJobHistory, EmailDeliveryLog = create_models(db)

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
        try:
            from optimization_improvements import apply_optimizations
            optimizer = apply_optimizations(app, db, scheduler)
            app.logger.info("Application optimizations applied lazily")
        except ImportError:
            app.logger.debug("Optimization improvements module not available")
            optimizer = False  # Mark as attempted
        except Exception as e:
            app.logger.warning(f"Failed to apply optimizations: {str(e)}")
            optimizer = False
    return optimizer

# Defer file consolidation service initialization
app.file_consolidation = None
def lazy_init_file_consolidation():
    """Initialize file consolidation service only when needed"""
    if app.file_consolidation is None:
        try:
            from file_consolidation_service import FileConsolidationService
            app.file_consolidation = FileConsolidationService()
            app.logger.info("File consolidation service initialized lazily")
        except Exception as e:
            app.logger.warning(f"File consolidation service not available: {e}")
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
    """Process all scheduled files that are due for processing"""
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
                    # Reset to next normal interval
                    if schedule.interval_type == 'daily':
                        schedule.next_run = now + timedelta(days=1)
                    elif schedule.interval_type == 'weekly':
                        schedule.next_run = now + timedelta(weeks=1)
                    else:
                        schedule.next_run = now + timedelta(hours=1)
                db.session.commit()
            
            # Get all active schedules that are due
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
                    
                    # Update schedule timestamps FIRST (commit immediately to ensure persistence)
                    schedule.last_run = now
                    schedule.calculate_next_run()
                    db.session.commit()  # Commit schedule update immediately
                    app.logger.info(f"Updated schedule '{schedule.name}': next_run = {schedule.next_run}")
                    
                    if result.get('success'):
                        # Replace original file with updated version
                        os.replace(temp_output, schedule.file_path)
                        app.logger.info(f"Successfully processed scheduled file: {schedule.file_path}")
                        
                        # CRITICAL: Sync main XML file with scheduled file to ensure consistency
                        main_xml_path = 'myticas-job-feed.xml'
                        if schedule.file_path != main_xml_path and os.path.exists(main_xml_path):
                            try:
                                # Copy the updated scheduled file to main XML file
                                shutil.copy2(schedule.file_path, main_xml_path)
                                app.logger.info(f"✅ Synchronized main XML file {main_xml_path} with scheduled file {schedule.file_path}")
                            except Exception as sync_error:
                                app.logger.error(f"❌ Failed to sync main XML file: {str(sync_error)}")
                        
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
                                    
                                    # Send email notification immediately after XML processing
                                    
                                    email_service = get_email_service()
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
                        
                        # Create a general activity entry for scheduled processing
                        activity_entry = BullhornActivity(
                            monitor_id=None,  # No specific monitor - this is a general system activity
                            activity_type='scheduled_processing',
                            job_id=None,
                            job_title=None,
                            details=f"Scheduled processing completed for '{schedule.name}' - {result.get('jobs_processed', 0)} jobs processed",
                            notification_sent=schedule.send_email_notifications
                        )
                        db.session.add(activity_entry)
                        app.logger.info(f"ATS activity logged for scheduled processing: {schedule.name}")
                        
                    else:
                        # Clean up temp file on failure
                        if os.path.exists(temp_output):
                            os.remove(temp_output)
                        app.logger.error(f"Failed to process scheduled file: {schedule.file_path} - {result.get('error')}")
                        
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
                app.logger.info("Scheduled processing activity logging completed")
            except Exception as e:
                app.logger.error(f"Error committing activity logs: {str(e)}")
                db.session.rollback()
            
        except Exception as e:
            app.logger.error(f"Error in scheduled processing: {str(e)}")
            db.session.rollback()

# Add the scheduled job to check every 2 minutes for faster response
scheduler.add_job(
    func=process_scheduled_files,
    trigger=IntervalTrigger(minutes=2),  # Reduced from 5 to 2 minutes
    id='process_scheduled_files',
    name='Process Scheduled XML Files',
    replace_existing=True
)

def process_bullhorn_monitors():
    """Process all active Bullhorn monitors for tearsheet changes"""
    with app.app_context():
        try:
            current_time = datetime.utcnow()
            
            # PREVENTION LAYER 1: Enhanced auto-recovery for overdue monitors
            overdue_monitors = BullhornMonitor.query.filter(
                BullhornMonitor.is_active == True,
                BullhornMonitor.next_check < current_time - timedelta(minutes=10)
            ).all()
            
            if overdue_monitors:
                app.logger.warning(f"AUTO-RECOVERY: Found {len(overdue_monitors)} monitors overdue by >10 minutes. Implementing comprehensive timing correction...")
                for monitor in overdue_monitors:
                    old_time = monitor.next_check
                    monitor.last_check = current_time
                    monitor.next_check = current_time + timedelta(minutes=2)  # Reduced from 5 to 2 minutes
                    app.logger.info(f"AUTO-RECOVERY: {monitor.name} - Last: {monitor.last_check}, Next: {monitor.next_check} (was {old_time})")
                
                # CRITICAL: Immediate commit for timing corrections with error handling
                try:
                    db.session.commit()
                    app.logger.info("AUTO-RECOVERY: Timing corrections successfully committed to database")
                except Exception as e:
                    app.logger.error(f"AUTO-RECOVERY: Failed to commit timing corrections: {str(e)}")
                    db.session.rollback()
                    # Try again with individual commits
                    for monitor in overdue_monitors:
                        try:
                            monitor.last_check = current_time
                            monitor.next_check = current_time + timedelta(minutes=2)  # Reduced from 5 to 2 minutes
                            db.session.commit()
                        except Exception as individual_error:
                            app.logger.error(f"AUTO-RECOVERY: Failed individual commit for {monitor.name}: {str(individual_error)}")
                            db.session.rollback()
            
            # Get all active monitors that are due for checking
            due_monitors = BullhornMonitor.query.filter(
                BullhornMonitor.is_active == True,
                BullhornMonitor.next_check <= current_time
            ).all()
            
            app.logger.info(f"Checking Bullhorn monitors. Found {len(due_monitors)} due monitors")
            
            # Initialize Bullhorn service once for all monitors
            bullhorn_service = get_bullhorn_service()
            if not bullhorn_service.test_connection():
                app.logger.error("Failed to connect to Bullhorn API for monitoring")
                return
            
            # Track all jobs from all monitors for comprehensive sync
            all_current_jobs_from_monitors = []
            monitors_processed = []
            
            for monitor in due_monitors:
                app.logger.info(f"Processing Bullhorn monitor: {monitor.name} (ID: {monitor.id})")
                try:
                    
                    # Get current jobs based on monitor type
                    if monitor.tearsheet_id == 0:
                        # Query-based monitor
                        current_jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                    else:
                        # Traditional tearsheet-based monitor
                        current_jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                    
                    # Add all jobs from this monitor to the comprehensive list
                    all_current_jobs_from_monitors.extend(current_jobs)
                    monitors_processed.append(monitor)
                    
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
                    
                    # Enhanced safeguards to prevent false positives and detect XML corruption
                    skip_removals = False
                    
                    if removed_jobs:
                        current_count = len(current_jobs)
                        previous_count = len(previous_jobs)
                        removed_count = len(removed_jobs)
                        
                        # Stricter threshold: 95% retention expected for normal operations
                        if current_count < previous_count * 0.95:
                            app.logger.warning(f"Monitor {monitor.name}: Potential API retrieval issue detected. "
                                             f"Current jobs: {current_count}, Previous: {previous_count}. "
                                             f"Drop rate: {((previous_count - current_count) / previous_count * 100):.1f}%. "
                                             f"Skipping removal processing to prevent false positives.")
                            skip_removals = True
                        
                        # Additional safeguard: If more than 10 jobs removed at once, require manual verification
                        elif removed_count > 10:
                            app.logger.warning(f"Monitor {monitor.name}: Large batch removal detected ({removed_count} jobs). "
                                             f"This may indicate data corruption or API issues. "
                                             f"Skipping removal processing - manual verification recommended.")
                            skip_removals = True
                        
                        # Detect potential XML corruption: If "previous" count is significantly lower than expected
                        elif previous_count < 50 and current_count > previous_count * 1.2:
                            app.logger.info(f"Monitor {monitor.name}: XML restoration detected. "
                                          f"Previous XML had {previous_count} jobs, Bullhorn shows {current_count}. "
                                          f"This appears to be data recovery, not job removals.")
                            skip_removals = True
                    
                    if skip_removals:
                        removed_jobs = []  # Don't process removals
                        summary['removed_count'] = 0  # Update summary
                    elif removed_jobs:
                        # Log verified removals for debugging
                        app.logger.info(f"Monitor {monitor.name}: Verified {len(removed_jobs)} job removals. "
                                      f"Current: {len(current_jobs)}, Previous: {len(previous_jobs)}. "
                                      f"Proceeding with XML sync.")
                    
                    # XML Integration: Automatically update XML files when jobs change
                    xml_sync_success = False
                    xml_sync_summary = {}
                    comprehensive_sync_modifications = []
                    
                    # Log detected changes for debugging
                    if added_jobs or removed_jobs or modified_jobs:
                        app.logger.info(f"Monitor {monitor.name}: Changes detected - "
                                      f"Added: {len(added_jobs)}, Removed: {len(removed_jobs)}, "
                                      f"Modified: {len(modified_jobs)}. Starting XML sync...")
                    
                    # Periodic orphan cleanup: every 10 monitoring cycles, check for orphaned jobs
                    # This prevents accumulation of orphaned jobs in XML files
                    perform_orphan_cleanup = False
                    if not hasattr(monitor, 'cleanup_counter'):
                        monitor.cleanup_counter = 0
                    monitor.cleanup_counter += 1
                    if monitor.cleanup_counter >= 10:
                        perform_orphan_cleanup = True
                        monitor.cleanup_counter = 0
                    
                    if added_jobs or removed_jobs or modified_jobs or perform_orphan_cleanup:
                        # Find all active schedules that might need XML updates
                        active_schedules = ScheduleConfig.query.filter_by(is_active=True).all()
                        
                        for schedule in active_schedules:
                            if os.path.exists(schedule.file_path):
                                try:
                                    # Initialize XML integration service
                                    xml_service = XMLIntegrationService()
                                    
                                    # Perform orphan cleanup if scheduled
                                    if perform_orphan_cleanup:
                                        # Get all current jobs from all monitors for comprehensive orphan detection
                                        all_monitors = BullhornMonitor.query.filter_by(is_active=True).all()
                                        all_current_jobs = []
                                        
                                        for other_monitor in all_monitors:
                                            if other_monitor.tearsheet_id == 0:
                                                monitor_jobs = bullhorn_service.get_jobs_by_query(other_monitor.tearsheet_name)
                                            else:
                                                monitor_jobs = bullhorn_service.get_tearsheet_jobs(other_monitor.tearsheet_id)
                                            all_current_jobs.extend(monitor_jobs)
                                        
                                        # Remove orphaned jobs
                                        orphan_result = xml_service.remove_orphaned_jobs(schedule.file_path, all_current_jobs)
                                        
                                        if orphan_result.get('success') and orphan_result.get('removed_count', 0) > 0:
                                            app.logger.info(f"Periodic cleanup: Removed {orphan_result['removed_count']} orphaned jobs from {schedule.file_path}")
                                            
                                            # Log orphan cleanup activity
                                            activity = BullhornActivity(
                                                monitor_id=monitor.id,
                                                activity_type='orphan_cleanup',
                                                details=f"Periodic cleanup: Removed {orphan_result['removed_count']} orphaned jobs"
                                            )
                                            db.session.add(activity)
                                    
                                    # Sync XML file with current Bullhorn jobs (only if there are actual changes)
                                    if added_jobs or removed_jobs or modified_jobs:
                                        sync_result = xml_service.sync_xml_with_bullhorn_jobs(
                                            xml_file_path=schedule.file_path,
                                            current_jobs=current_jobs,
                                            previous_jobs=previous_jobs
                                        )
                                        
                                        # Enhanced verification and recovery for sync failures
                                        if sync_result.get('success'):
                                            # Verify all changes were applied correctly
                                            verification_errors = []
                                            
                                            # Check removed jobs
                                            for removed_job in removed_jobs:
                                                job_id = str(removed_job.get('id'))
                                                with open(schedule.file_path, 'r', encoding='utf-8') as f:
                                                    xml_content = f.read()
                                                    if f"({job_id})" in xml_content:
                                                        app.logger.warning(f"Job {job_id} still exists in XML after removal attempt")
                                                        # Attempt manual removal with retry
                                                        try:
                                                            manual_removal = xml_service.remove_job_from_xml(schedule.file_path, job_id)
                                                            if manual_removal:
                                                                app.logger.info(f"Manually removed job {job_id} from XML")
                                                            else:
                                                                verification_errors.append(f"Failed to remove job {job_id}")
                                                        except Exception as e:
                                                            app.logger.error(f"Error removing job {job_id}: {str(e)}")
                                                            verification_errors.append(f"Failed to remove job {job_id}: {str(e)}")
                                            
                                            # Check modified jobs
                                            for modified_job in modified_jobs:
                                                job_id = str(modified_job.get('id'))
                                                expected_title = modified_job.get('title', '')
                                                if not xml_service._verify_job_update_in_xml(schedule.file_path, job_id, expected_title):
                                                    app.logger.warning(f"Job {job_id} update verification failed, attempting recovery")
                                                    # Attempt to update the job again
                                                    update_success = xml_service.update_job_in_xml(schedule.file_path, modified_job, 'Scheduled Processing')
                                                    if not update_success:
                                                        verification_errors.append(f"Failed to update job {job_id}")
                                            
                                            # Check added jobs with enhanced verification
                                            for added_job in added_jobs:
                                                job_id = str(added_job.get('id'))
                                                with open(schedule.file_path, 'r', encoding='utf-8') as f:
                                                    xml_content = f.read()
                                                    # Check both title format and bhatsid format
                                                    if f"({job_id})" not in xml_content and f"<bhatsid><![CDATA[ {job_id} ]]></bhatsid>" not in xml_content:
                                                        app.logger.warning(f"SYNC GAP DETECTED: Job {job_id} missing from XML after addition attempt")
                                                        
                                                        # Log the sync gap for monitoring
                                                        app.logger.error(f"SYNC GAP CRITICAL: Job {job_id} ({added_job.get('title', 'Unknown')}) failed to sync to XML")
                                                        
                                                        # Attempt manual addition with retry
                                                        manual_addition = xml_service.add_job_to_xml(schedule.file_path, added_job)
                                                        if manual_addition:
                                                            app.logger.info(f"SYNC RECOVERY SUCCESS: Job {job_id} recovered via manual addition")
                                                            
                                                            # Send recovery notification email if configured
                                                            try:
                                                                if hasattr(xml_service, 'email_service') and xml_service.email_service:
                                                                    xml_service.email_service.send_recovery_notification(job_id, added_job.get('title', 'Unknown'))
                                                            except Exception as email_error:
                                                                app.logger.warning(f"Failed to send recovery notification for job {job_id}: {email_error}")
                                                        else:
                                                            verification_errors.append(f"Failed to add job {job_id}")
                                                            app.logger.error(f"CRITICAL: Unable to recover job {job_id} - manual intervention may be required")
                                            
                                            # Update sync result if verification failed
                                            if verification_errors:
                                                sync_result['success'] = False
                                                sync_result['errors'] = sync_result.get('errors', []) + verification_errors
                                                app.logger.error(f"XML sync verification failed: {'; '.join(verification_errors)}")
                                        else:
                                            # Sync initially failed, log error details
                                            app.logger.error(f"XML sync failed for schedule '{schedule.name}': {sync_result.get('errors', ['Unknown error'])}")
                                    else:
                                        # No sync needed if only performing orphan cleanup
                                        sync_result = {'success': True, 'total_changes': 0}
                                    
                                    if sync_result.get('success'):
                                        xml_sync_success = True
                                        xml_sync_summary = sync_result
                                        
                                        app.logger.info(f"XML sync completed for schedule '{schedule.name}': "
                                                       f"{sync_result.get('added_count', 0)} added, "
                                                       f"{sync_result.get('removed_count', 0)} removed, "
                                                       f"{sync_result.get('updated_count', 0)} updated")
                                        
                                        # Log sync details for debugging
                                        if sync_result.get('total_changes', 0) > 0:
                                            app.logger.info(f"XML sync made {sync_result.get('total_changes', 0)} changes to {schedule.file_path}")
                                        else:
                                            app.logger.info(f"XML sync completed but no changes were needed for {schedule.file_path}")
                                        
                                        # Update last file upload timestamp
                                        schedule.last_file_upload = datetime.utcnow()
                                        
                                        # Get original filename for SFTP upload (no reference number regeneration for real-time monitoring)
                                        original_filename = schedule.original_filename or os.path.basename(schedule.file_path)
                                        
                                        # Upload to SFTP if configured
                                        if schedule.auto_upload_ftp:
                                            try:
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
                                                        app.logger.info(f"Updated XML file uploaded to SFTP: {original_filename}")
                                                        xml_sync_summary['sftp_upload_success'] = True
                                                        
                                                        # Update the schedule's last_file_upload timestamp  
                                                        schedule.last_file_upload = datetime.utcnow()
                                                    else:
                                                        app.logger.warning(f"Failed to upload updated XML to SFTP")
                                                        xml_sync_summary['sftp_upload_success'] = False
                                                else:
                                                    app.logger.warning(f"SFTP upload requested but credentials not configured")
                                                    xml_sync_summary['sftp_upload_success'] = False
                                            except Exception as e:
                                                app.logger.error(f"Error uploading updated XML to SFTP: {str(e)}")
                                                xml_sync_summary['sftp_upload_success'] = False
                                    
                                except Exception as e:
                                    app.logger.error(f"Error syncing XML for schedule '{schedule.name}': {str(e)}")
                    
                    # Log activities
                    for job in added_jobs:
                        # Extract account manager from job data with debug logging
                        account_manager = None
                        job_id = job.get('id')
                        
                        # Debug: Log the job data structure for account manager fields
                        if job_id:
                            app.logger.debug(f"Job {job_id} userID field: {job.get('userID')}")
                            app.logger.debug(f"Job {job_id} owner field: {job.get('owner')}")
                            app.logger.debug(f"Job {job_id} assignedUsers field: {job.get('assignedUsers')}")
                        
                        # Priority 1: Extract from userID field (the correct field for account manager)
                        if job.get('userID') and isinstance(job['userID'], dict):
                            first_name = job['userID'].get('firstName', '').strip()
                            last_name = job['userID'].get('lastName', '').strip()
                            if first_name or last_name:
                                account_manager = f"{first_name} {last_name}".strip()
                                app.logger.debug(f"Job {job_id} extracted account manager from userID: {account_manager}")
                        # Priority 2: Fallback to owner field
                        elif job.get('owner') and isinstance(job['owner'], dict):
                            first_name = job['owner'].get('firstName', '').strip()
                            last_name = job['owner'].get('lastName', '').strip()
                            if first_name or last_name:
                                account_manager = f"{first_name} {last_name}".strip()
                                app.logger.debug(f"Job {job_id} extracted account manager from owner: {account_manager}")
                        # Priority 3: Fallback to assignedUsers field
                        elif job.get('assignedUsers') and len(job['assignedUsers']) > 0:
                            first_user = job['assignedUsers'][0]
                            if isinstance(first_user, dict):
                                first_name = first_user.get('firstName', '').strip()
                                last_name = first_user.get('lastName', '').strip()
                                if first_name or last_name:
                                    account_manager = f"{first_name} {last_name}".strip()
                                    app.logger.debug(f"Job {job_id} extracted account manager from assignedUsers: {account_manager}")
                        
                        if not account_manager:
                            app.logger.debug(f"Job {job_id} - No account manager data found in userID, owner, or assignedUsers fields")
                        
                        activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='job_added',
                            job_id=job.get('id'),
                            job_title=job.get('title'),
                            account_manager=account_manager,
                            details=f"Job added: {job.get('id')}"
                        )
                        db.session.add(activity)
                    
                    for job in removed_jobs:
                        # Extract account manager from job data with debug logging
                        account_manager = None
                        job_id = job.get('id')
                        
                        # Debug: Log the job data structure for account manager fields
                        if job_id:
                            app.logger.debug(f"Job {job_id} userID field: {job.get('userID')}")
                            app.logger.debug(f"Job {job_id} owner field: {job.get('owner')}")
                            app.logger.debug(f"Job {job_id} assignedUsers field: {job.get('assignedUsers')}")
                        
                        # Priority 1: Extract from userID field (the correct field for account manager)
                        if job.get('userID') and isinstance(job['userID'], dict):
                            first_name = job['userID'].get('firstName', '').strip()
                            last_name = job['userID'].get('lastName', '').strip()
                            if first_name or last_name:
                                account_manager = f"{first_name} {last_name}".strip()
                                app.logger.debug(f"Job {job_id} extracted account manager from userID: {account_manager}")
                        # Priority 2: Fallback to owner field
                        elif job.get('owner') and isinstance(job['owner'], dict):
                            first_name = job['owner'].get('firstName', '').strip()
                            last_name = job['owner'].get('lastName', '').strip()
                            if first_name or last_name:
                                account_manager = f"{first_name} {last_name}".strip()
                                app.logger.debug(f"Job {job_id} extracted account manager from owner: {account_manager}")
                        # Priority 3: Fallback to assignedUsers field
                        elif job.get('assignedUsers') and len(job['assignedUsers']) > 0:
                            first_user = job['assignedUsers'][0]
                            if isinstance(first_user, dict):
                                first_name = first_user.get('firstName', '').strip()
                                last_name = first_user.get('lastName', '').strip()
                                if first_name or last_name:
                                    account_manager = f"{first_name} {last_name}".strip()
                                    app.logger.debug(f"Job {job_id} extracted account manager from assignedUsers: {account_manager}")
                        
                        if not account_manager:
                            app.logger.debug(f"Job {job_id} - No account manager data found in userID, owner, or assignedUsers fields")
                        
                        activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='job_removed',
                            job_id=job.get('id'),
                            job_title=job.get('title'),
                            account_manager=account_manager,
                            details=f"Job removed: {job.get('id')}"
                        )
                        db.session.add(activity)
                    
                    for job in modified_jobs:
                        # Extract account manager from job data with debug logging
                        account_manager = None
                        job_id = job.get('id')
                        
                        # Debug: Log the job data structure for account manager fields
                        if job_id:
                            app.logger.debug(f"Job {job_id} userID field: {job.get('userID')}")
                            app.logger.debug(f"Job {job_id} owner field: {job.get('owner')}")
                            app.logger.debug(f"Job {job_id} assignedUsers field: {job.get('assignedUsers')}")
                        
                        # Priority 1: Extract from userID field (the correct field for account manager)
                        if job.get('userID') and isinstance(job['userID'], dict):
                            first_name = job['userID'].get('firstName', '').strip()
                            last_name = job['userID'].get('lastName', '').strip()
                            if first_name or last_name:
                                account_manager = f"{first_name} {last_name}".strip()
                                app.logger.debug(f"Job {job_id} extracted account manager from userID: {account_manager}")
                        # Priority 2: Fallback to owner field
                        elif job.get('owner') and isinstance(job['owner'], dict):
                            first_name = job['owner'].get('firstName', '').strip()
                            last_name = job['owner'].get('lastName', '').strip()
                            if first_name or last_name:
                                account_manager = f"{first_name} {last_name}".strip()
                                app.logger.debug(f"Job {job_id} extracted account manager from owner: {account_manager}")
                        # Priority 3: Fallback to assignedUsers field
                        elif job.get('assignedUsers') and len(job['assignedUsers']) > 0:
                            first_user = job['assignedUsers'][0]
                            if isinstance(first_user, dict):
                                first_name = first_user.get('firstName', '').strip()
                                last_name = first_user.get('lastName', '').strip()
                                if first_name or last_name:
                                    account_manager = f"{first_name} {last_name}".strip()
                                    app.logger.debug(f"Job {job_id} extracted account manager from assignedUsers: {account_manager}")
                        
                        if not account_manager:
                            app.logger.debug(f"Job {job_id} - No account manager data found in userID, owner, or assignedUsers fields")
                        
                        # Create concise field change summary instead of full job data
                        changes = job.get('changes', [])
                        field_changes = []
                        for change in changes:
                            field_name = change.get('field', '')
                            if field_name == 'title':
                                field_changes.append('title')
                            elif field_name == 'publicDescription' or field_name == 'description':
                                field_changes.append('description')
                            elif field_name == 'dateLastModified':
                                field_changes.append('date modified')
                            elif field_name == 'employmentType':
                                field_changes.append('employment type')
                            elif field_name == 'onSite':
                                field_changes.append('remote type')
                            elif field_name == 'owner' or 'assignedUsers' in field_name:
                                field_changes.append('assigned recruiter')
                            elif 'address' in field_name.lower():
                                field_changes.append('location')
                            else:
                                field_changes.append(field_name.lower())
                        
                        # Create concise summary
                        if field_changes:
                            change_summary = f"Updated: {', '.join(set(field_changes))}"
                        else:
                            change_summary = "Job details updated"
                            
                        activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='job_modified',
                            job_id=job.get('id'),
                            job_title=job.get('title'),
                            account_manager=account_manager,
                            details=change_summary
                        )
                        db.session.add(activity)
                    
                    # Log a summary activity for batch updates
                    if added_jobs or removed_jobs or modified_jobs:
                        # Mark monitor as having changes for comprehensive sync
                        monitor._has_changes = True
                        monitor._detected_changes = {
                            'added': added_jobs,
                            'removed': removed_jobs, 
                            'modified': modified_jobs
                        }
                        
                        summary_details = json.dumps({
                            'summary': summary,
                            'changes_detected': True,
                            'total_jobs': len(current_jobs),
                            'timestamp': datetime.utcnow().isoformat()
                        })
                        summary_activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='check_completed',
                            details=summary_details,
                            notification_sent=True  # System housekeeping - no email needed
                        )
                        db.session.add(summary_activity)
                        
                        app.logger.info(f"Bullhorn monitor {monitor.name}: {len(current_jobs)} total jobs, {summary.get('added_count', 0)} added, {summary.get('removed_count', 0)} removed, {summary.get('modified_count', 0)} modified")
                        
                        # IMMEDIATE WORKFLOW EXECUTION: Trigger XML sync immediately when changes are detected
                        app.logger.info(f"🚀 IMMEDIATE WORKFLOW TRIGGER: Changes detected for {monitor.name}, executing XML sync NOW")
                        
                        # Get XML Integration Service
                        from xml_integration_service import XMLIntegrationService
                        xml_service = XMLIntegrationService()
                        
                        # Determine which XML files need updating based on monitor
                        xml_files_to_update = []
                        active_schedules = ScheduleConfig.query.filter_by(is_active=True).all()
                        for schedule in active_schedules:
                            if schedule.file_path:
                                xml_files_to_update.append(os.path.basename(schedule.file_path))
                        
                        if not xml_files_to_update:
                            xml_files_to_update = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
                        
                        # Process each XML file immediately
                        immediate_sync_summary = {'added_count': 0, 'removed_count': 0, 'updated_count': 0, 'sftp_upload_success': False}
                        
                        for xml_filename in xml_files_to_update:
                            if os.path.exists(xml_filename):
                                try:
                                    app.logger.info(f"📝 IMMEDIATE SYNC: Processing {xml_filename} for {monitor.name}")
                                    
                                    # Process removals
                                    if removed_jobs:
                                        for job in removed_jobs:
                                            job_id = str(job.get('job_id'))
                                            if xml_service.remove_job_from_xml(xml_filename, job_id):
                                                immediate_sync_summary['removed_count'] += 1
                                                app.logger.info(f"🗑️ Immediately removed job {job_id} from {xml_filename}")
                                    
                                    # Process additions
                                    if added_jobs:
                                        for job in added_jobs:
                                            if xml_service.add_job_to_xml(xml_filename, job, monitor.name):
                                                immediate_sync_summary['added_count'] += 1
                                                app.logger.info(f"✅ Immediately added job {job.get('id')}: {job.get('title')} to {xml_filename}")
                                    
                                    # Process modifications
                                    if modified_jobs:
                                        for job in modified_jobs:
                                            # Find current job data
                                            job_id = str(job.get('id'))
                                            current_job_data = None
                                            for cj in current_jobs:
                                                if str(cj.get('id')) == job_id:
                                                    current_job_data = cj
                                                    break
                                            
                                            if current_job_data:
                                                if xml_service.update_job_in_xml(xml_filename, current_job_data, monitor.name):
                                                    immediate_sync_summary['updated_count'] += 1
                                                    app.logger.info(f"📝 Immediately updated job {job_id}: {current_job_data.get('title')} in {xml_filename}")
                                    
                                    app.logger.info(f"✅ IMMEDIATE SYNC COMPLETE for {xml_filename}: {immediate_sync_summary['added_count']} added, {immediate_sync_summary['removed_count']} removed, {immediate_sync_summary['updated_count']} updated")
                                    
                                    # IMMEDIATE SFTP UPLOAD after XML sync
                                    total_immediate_changes = immediate_sync_summary['added_count'] + immediate_sync_summary['removed_count'] + immediate_sync_summary['updated_count']
                                    if total_immediate_changes > 0:
                                        # Find matching schedule for auto-upload
                                        matching_schedule = None
                                        for schedule in active_schedules:
                                            if os.path.basename(schedule.file_path) == xml_filename:
                                                matching_schedule = schedule
                                                break
                                        
                                        if matching_schedule and matching_schedule.auto_upload_ftp:
                                            app.logger.info(f"📤 IMMEDIATE SFTP UPLOAD: Uploading {xml_filename} with {total_immediate_changes} changes")
                                            
                                            try:
                                                # Get SFTP settings
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
                                                    
                                                    from ftp_service import get_ftp_service
                                                    ftp_service = get_ftp_service()
                                                    
                                                    port = int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 22
                                                    directory = sftp_directory.setting_value if sftp_directory else ''
                                                    
                                                    upload_result = ftp_service.upload_file(
                                                        local_file_path=xml_filename,
                                                        remote_filename=xml_filename,
                                                        hostname=sftp_hostname.setting_value,
                                                        username=sftp_username.setting_value,
                                                        password=sftp_password.setting_value,
                                                        port=port,
                                                        directory=directory
                                                    )
                                                    
                                                    if upload_result.get('success'):
                                                        immediate_sync_summary['sftp_upload_success'] = True
                                                        app.logger.info(f"✅ IMMEDIATE SFTP UPLOAD SUCCESSFUL for {xml_filename}")
                                                    else:
                                                        app.logger.error(f"❌ Immediate SFTP upload failed: {upload_result.get('error')}")
                                            except Exception as upload_error:
                                                app.logger.error(f"Error during immediate SFTP upload: {str(upload_error)}")
                                    
                                except Exception as e:
                                    app.logger.error(f"Error in immediate sync for {xml_filename}: {str(e)}")
                        
                        # Mark XML sync as successful for this monitor
                        xml_sync_success = True
                        xml_sync_summary = immediate_sync_summary.copy()
                    
                    # CRITICAL TIMING FIX: Store notification data for sending AFTER comprehensive sync completes
                    # This ensures emails are sent AFTER XML files are uploaded to web server
                    
                    # Enhanced critical changes detection with better field mapping
                    critical_fields = ['title', 'city', 'state', 'country', 'jobtype', 'remotetype', 'assignedrecruiter', 
                                      'publicDescription', 'description', 'employmentType', 'onSite', 'owner', 'address',
                                      'assignedUsers', 'responseUser']
                    
                    critical_modifications = []
                    if modified_jobs:
                        for job in modified_jobs:
                            job_changes = job.get('changes', [])
                            for change in job_changes:
                                field_name = change.get('field', '')
                                # Check if this is a critical field (business-related, not AI classification)
                                if any(critical_field in field_name for critical_field in critical_fields):
                                    critical_modifications.append({
                                        'id': job.get('id'),
                                        'title': job.get('title', f'Job {job.get("id")}'),
                                        'changes': job_changes
                                    })
                                    break  # Found critical change, no need to check other changes for this job
                    
                    critical_changes_exist = bool(added_jobs or removed_jobs or critical_modifications)
                    
                    if critical_changes_exist:
                        app.logger.info(f"🔔 Critical changes detected for monitor {monitor.name}: {len(added_jobs)} added, {len(removed_jobs)} removed, {len(critical_modifications)} critically modified jobs")
                    
                    if critical_changes_exist and monitor.send_notifications:
                        # Get email address from Global Settings or monitor-specific setting
                        email_address = monitor.notification_email
                        if not email_address:
                            # Fall back to global notification email
                            global_email = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                            if global_email:
                                email_address = global_email.setting_value
                        
                        if email_address:
                            # Store notification data for sending after comprehensive sync
                            if not hasattr(app, '_pending_notifications'):
                                app._pending_notifications = []
                            
                            # Enhanced notification with better modified job details
                            notification_data = {
                                'monitor_name': monitor.name,
                                'monitor_id': monitor.id,
                                'email_address': email_address,
                                'added_jobs': added_jobs.copy() if added_jobs else [],
                                'removed_jobs': removed_jobs.copy() if removed_jobs else [],
                                'modified_jobs': critical_modifications.copy() if critical_modifications else [],
                                'total_jobs': len(current_jobs),
                                'summary': summary,
                                'xml_sync_info': xml_sync_summary.copy() if xml_sync_summary else {},
                                'timestamp': datetime.now()
                            }
                            
                            app._pending_notifications.append(notification_data)
                            app.logger.info(f"📧 Notification queued for monitor {monitor.name} - will send AFTER comprehensive sync completes (critical changes: {len(critical_modifications)} modified, {len(added_jobs)} added, {len(removed_jobs)} removed)")
                    elif (added_jobs or removed_jobs or modified_jobs) and monitor.send_notifications:
                        app.logger.info(f"Changes detected for monitor {monitor.name} but no critical fields modified - skipping notification")
                    elif (added_jobs or removed_jobs or modified_jobs):
                        app.logger.info(f"Changes detected for monitor {monitor.name} but notifications disabled")
                    
                    # Update monitor with new snapshot and next check time
                    # IMPORTANT: Only update snapshot if XML sync was successful or if no changes were detected
                    # This prevents losing track of changes if the sync fails
                    # Check both local xml_sync_success and comprehensive sync results
                    comprehensive_sync_success = hasattr(monitor, '_xml_sync_success') and monitor._xml_sync_success
                    
                    if xml_sync_success or comprehensive_sync_success or not (added_jobs or removed_jobs or modified_jobs):
                        monitor.last_job_snapshot = json.dumps(current_jobs)
                        if comprehensive_sync_success:
                            app.logger.info(f"Updated job snapshot for monitor {monitor.name} (via comprehensive sync)")
                        else:
                            app.logger.info(f"Updated job snapshot for monitor {monitor.name}")
                    else:
                        app.logger.warning(f"Skipping snapshot update for monitor {monitor.name} due to failed XML sync")
                        
                    # Special handling for monitors with empty snapshots - initialize with current jobs
                    if not monitor.last_job_snapshot or monitor.last_job_snapshot == '[]':
                        monitor.last_job_snapshot = json.dumps(current_jobs)
                        app.logger.info(f"Initialized empty snapshot for monitor {monitor.name} with {len(current_jobs)} jobs")
                        
                        # When initializing, also check for orphaned jobs in XML files
                        if current_jobs:
                            active_schedules = ScheduleConfig.query.filter_by(is_active=True).all()
                            for schedule in active_schedules:
                                if os.path.exists(schedule.file_path):
                                    try:
                                        xml_service = XMLIntegrationService()
                                        
                                        # Get all current jobs from all monitors for orphan detection
                                        all_monitors = BullhornMonitor.query.filter_by(is_active=True).all()
                                        all_current_jobs = []
                                        
                                        bullhorn_service = get_bullhorn_service()
                                        if bullhorn_service.test_connection():
                                            for other_monitor in all_monitors:
                                                if other_monitor.tearsheet_id == 0:
                                                    monitor_jobs = bullhorn_service.get_jobs_by_query(other_monitor.tearsheet_name)
                                                else:
                                                    monitor_jobs = bullhorn_service.get_tearsheet_jobs(other_monitor.tearsheet_id)
                                                all_current_jobs.extend(monitor_jobs)
                                        
                                        # Check for and remove orphaned jobs
                                        orphan_result = xml_service.remove_orphaned_jobs(schedule.file_path, all_current_jobs)
                                        
                                        if orphan_result.get('success') and orphan_result.get('removed_count', 0) > 0:
                                            app.logger.info(f"Removed {orphan_result['removed_count']} orphaned jobs from {schedule.file_path}")
                                            
                                            # Log orphan cleanup activity
                                            activity = BullhornActivity(
                                                monitor_id=monitor.id,
                                                activity_type='orphan_cleanup',
                                                details=f"Removed {orphan_result['removed_count']} orphaned jobs during snapshot initialization"
                                            )
                                            db.session.add(activity)
                                            
                                            # Upload cleaned file to SFTP
                                            if schedule.auto_upload_ftp:
                                                try:
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
                                                        
                                                        original_filename = schedule.original_filename or os.path.basename(schedule.file_path)
                                                        sftp_upload_success = ftp_service.upload_file(
                                                            local_file_path=schedule.file_path,
                                                            remote_filename=original_filename
                                                        )
                                                        
                                                        if sftp_upload_success:
                                                            app.logger.info(f"Uploaded cleaned XML file to SFTP: {original_filename}")
                                                            schedule.last_file_upload = datetime.utcnow()
                                                        else:
                                                            app.logger.warning(f"Failed to upload cleaned XML to SFTP")
                                                except Exception as e:
                                                    app.logger.error(f"Error uploading cleaned XML to SFTP: {str(e)}")
                                    except Exception as e:
                                        app.logger.error(f"Error during orphan cleanup: {str(e)}")
                    
                    # PREVENTION LAYER 2: Update timing first and commit immediately
                    monitor.last_check = current_time
                    monitor.calculate_next_check()
                    
                    # CRITICAL: Immediate commit of timing data to prevent loss
                    try:
                        # Use SQLAlchemy's proper text() function for raw SQL
                        from sqlalchemy import text
                        timing_update_sql = text("""
                        UPDATE bullhorn_monitor 
                        SET last_check = :last_check, 
                            next_check = :next_check 
                        WHERE id = :monitor_id
                        """)
                        db.session.execute(timing_update_sql, {
                            'last_check': current_time,
                            'next_check': monitor.next_check,
                            'monitor_id': monitor.id
                        })
                        db.session.commit()
                        app.logger.debug(f"TIMING-COMMIT: {monitor.name} timing safely committed - Next: {monitor.next_check}")
                    except Exception as timing_error:
                        app.logger.error(f"TIMING-COMMIT: Failed to commit timing for {monitor.name}: {str(timing_error)}")
                        db.session.rollback()
                        # Fallback: set timing again and try with ORM
                        try:
                            monitor.last_check = current_time
                            monitor.calculate_next_check()
                            db.session.commit()
                            app.logger.info(f"TIMING-FALLBACK: {monitor.name} timing committed via ORM fallback")
                        except Exception as fallback_error:
                            app.logger.error(f"TIMING-FALLBACK: Complete failure for {monitor.name}: {str(fallback_error)}")
                            db.session.rollback()
                    
                    # Log successful check (only if no changes were already logged)
                    if not (added_jobs or removed_jobs or modified_jobs):
                        activity = BullhornActivity(
                            monitor_id=monitor.id,
                            activity_type='check_completed',
                            details=f"Checked {monitor.tearsheet_name}. Found {len(current_jobs)} jobs. No changes detected.",
                            notification_sent=True  # System housekeeping - no email needed
                        )
                        db.session.add(activity)
                    else:
                        # Log XML sync result if changes were detected
                        if xml_sync_success:
                            activity = BullhornActivity(
                                monitor_id=monitor.id,
                                activity_type='xml_sync_completed',
                                details=f"XML sync completed: {xml_sync_summary.get('added_count', 0)} added, {xml_sync_summary.get('removed_count', 0)} removed, {xml_sync_summary.get('updated_count', 0)} updated. SFTP upload: {xml_sync_summary.get('sftp_upload_success', False)}",
                                notification_sent=True  # System housekeeping - no email needed
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
                    
                    # PREVENTION LAYER 3: Even on error, ensure timing is updated and committed
                    try:
                        monitor.last_check = current_time
                        monitor.calculate_next_check()
                        from sqlalchemy import text
                        timing_update_sql = text("""
                        UPDATE bullhorn_monitor 
                        SET last_check = :last_check, 
                            next_check = :next_check 
                        WHERE id = :monitor_id
                        """)
                        db.session.execute(timing_update_sql, {
                            'last_check': current_time,
                            'next_check': monitor.next_check,
                            'monitor_id': monitor.id
                        })
                        db.session.commit()
                        app.logger.info(f"ERROR-RECOVERY: {monitor.name} timing updated despite processing error")
                    except Exception as timing_error:
                        app.logger.error(f"ERROR-RECOVERY: Failed to update timing for {monitor.name}: {str(timing_error)}")
                        db.session.rollback()
            
            # After processing all individual monitors, perform comprehensive XML sync
            # CRITICAL: Always run comprehensive sync when monitors are processed, even with no jobs
            app.logger.info(f"COMPREHENSIVE SYNC CHECK: monitors_processed = {len(monitors_processed) if monitors_processed else 0}, all_jobs = {len(all_current_jobs_from_monitors) if all_current_jobs_from_monitors else 0}")
            if monitors_processed:
                total_jobs = len(all_current_jobs_from_monitors) if all_current_jobs_from_monitors else 0
                app.logger.info(f"🔥 COMPREHENSIVE SYNC STARTING: {total_jobs} total jobs from {len(monitors_processed)} monitors")
                
                # CRITICAL FIX: Only perform comprehensive cleanup if ALL tearsheets are being processed simultaneously
                # Individual tearsheet monitoring should NEVER trigger removal of all jobs
                # Check if this is a system-wide comprehensive sync vs individual tearsheet monitoring
                num_monitors_processed = len(monitors_processed) if monitors_processed else 0
                is_system_wide_sync = num_monitors_processed >= 3  # Multiple monitors processed = system sync
                
                if total_jobs == 0 and is_system_wide_sync:
                    app.logger.warning("🚨 SYSTEM-WIDE SYNC: No jobs found across all tearsheets - proceeding with comprehensive cleanup")
                elif total_jobs == 0:
                    app.logger.info("🔒 INDIVIDUAL MONITOR: Single tearsheet has 0 jobs - skipping comprehensive sync to prevent data loss")
                    # CRITICAL SAFETY: Skip comprehensive sync for individual empty tearsheets
                    # This prevents catastrophic data loss from individual empty tearsheet scenarios
                    return  # Exit the function safely without proceeding to comprehensive sync
                else:
                    app.logger.info(f"🎯 COMPREHENSIVE SYNC: Found {total_jobs} jobs across {num_monitors_processed} monitors - proceeding with XML comparison")
                
                # Only proceed with comprehensive sync if we have jobs OR it's a verified system-wide sync
                if total_jobs > 0 or (total_jobs == 0 and is_system_wide_sync):
                    # Perform comprehensive sync with existing logic
                    app.logger.info("Comprehensive sync logic temporarily simplified - critical bug fix implemented")
                    
                    # Track comprehensive sync results to complete monitor workflows
                    comprehensive_sync_made_changes = False
                    comprehensive_sync_summary = {'added_count': 0, 'updated_count': 0, 'removed_count': 0}
                    
                    # Find all active schedules
                    active_schedules = ScheduleConfig.query.filter_by(is_active=True).all()
                    
                    # Process main XML files for comprehensive sync
                    main_xml_files = [
                        'myticas-job-feed.xml',
                        'myticas-job-feed-scheduled.xml'
                    ]
                    
                    for xml_filename in main_xml_files:
                        if os.path.exists(xml_filename):
                            app.logger.info(f"🔄 COMPREHENSIVE SYNC: Processing {xml_filename}")
                            xml_service = XMLIntegrationService()
                            
                            # Compare current XML jobs with all Bullhorn jobs
                            try:
                                # Get current job IDs in XML
                                xml_job_ids = set()
                                if etree:
                                    parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
                                    tree = etree.parse(xml_filename, parser)
                                    root = tree.getroot()
                                    
                                    # Extract job IDs from bhatsid elements
                                    for bhatsid_elem in root.xpath('.//bhatsid'):
                                        if bhatsid_elem.text:
                                            job_id = bhatsid_elem.text.strip()
                                            if job_id:
                                                xml_job_ids.add(job_id)
                                else:
                                    app.logger.warning(f"lxml not available, skipping XML comparison for {xml_filename}")
                                
                                # Get all job IDs from Bullhorn monitors
                                bullhorn_job_ids = set()
                                if all_current_jobs_from_monitors:
                                    bullhorn_job_ids = {str(job.get('id')) for job in all_current_jobs_from_monitors if job.get('id')}
                                
                                # Find missing jobs that need to be added
                                missing_job_ids = bullhorn_job_ids - xml_job_ids
                                # Find orphaned jobs that need to be removed
                                orphaned_job_ids = xml_job_ids - bullhorn_job_ids
                                
                                app.logger.info(f"📊 COMPARISON: XML has {len(xml_job_ids)} jobs, Bullhorn has {len(bullhorn_job_ids)} jobs")
                                app.logger.info(f"➕ MISSING JOBS: {len(missing_job_ids)} jobs need to be added: {list(missing_job_ids)[:10]}")
                                app.logger.info(f"➖ ORPHANED JOBS: {len(orphaned_job_ids)} jobs need to be removed: {list(orphaned_job_ids)[:10]}")
                                
                                # Remove orphaned jobs from XML first
                                if orphaned_job_ids:
                                    jobs_removed = 0
                                    for job_id in orphaned_job_ids:
                                        if xml_service.remove_job_from_xml(xml_filename, job_id):
                                            jobs_removed += 1
                                            comprehensive_sync_made_changes = True
                                            app.logger.info(f"🗑️ Removed orphaned job {job_id} from XML")
                                    
                                    if jobs_removed > 0:
                                        comprehensive_sync_summary['removed_count'] += jobs_removed
                                        app.logger.info(f"🎯 COMPREHENSIVE SYNC REMOVAL: Removed {jobs_removed} orphaned jobs from {xml_filename}")
                                
                                # Add missing jobs to XML
                                if missing_job_ids:
                                    jobs_added = 0
                                    for job_id in missing_job_ids:
                                        # Find the job data
                                        job_data = None
                                        monitor_name = "Comprehensive Sync"
                                        
                                        for job in all_current_jobs_from_monitors:
                                            if str(job.get('id')) == job_id:
                                                job_data = job
                                                break
                                        
                                        if job_data:
                                            if xml_service.add_job_to_xml(xml_filename, job_data, monitor_name):
                                                jobs_added += 1
                                                comprehensive_sync_made_changes = True
                                                app.logger.info(f"✅ Added job {job_id}: {job_data.get('title', 'Unknown')}")
                                    
                                    if jobs_added > 0:
                                        comprehensive_sync_summary['added_count'] += jobs_added
                                        app.logger.info(f"🎯 COMPREHENSIVE SYNC SUCCESS: Added {jobs_added} jobs to {xml_filename}")
                                
                                # Handle job modifications BEFORE SFTP upload
                                # If comprehensive sync processes modifications, track them AND update the XML
                                if monitors_processed:
                                    for monitor in monitors_processed:
                                        if hasattr(monitor, '_detected_changes') and monitor._detected_changes:
                                            # Process modified jobs - ACTUALLY UPDATE THEM IN XML
                                            modified_jobs = monitor._detected_changes.get('modified', [])
                                            if modified_jobs:
                                                jobs_updated = 0
                                                for modified_job in modified_jobs:
                                                    # Find the updated job data in all_current_jobs_from_monitors
                                                    job_id = str(modified_job.get('id'))
                                                    updated_job_data = None
                                                    
                                                    for job in all_current_jobs_from_monitors:
                                                        if str(job.get('id')) == job_id:
                                                            updated_job_data = job
                                                            break
                                                    
                                                    if updated_job_data:
                                                        # Update the job in XML with the new data
                                                        if xml_service.update_job_in_xml(xml_filename, updated_job_data, monitor.name):
                                                            jobs_updated += 1
                                                            comprehensive_sync_made_changes = True
                                                            app.logger.info(f"📝 Updated job {job_id}: {updated_job_data.get('title', 'Unknown')} in XML")
                                                
                                                if jobs_updated > 0:
                                                    comprehensive_sync_summary['updated_count'] += jobs_updated
                                                    app.logger.info(f"🔄 COMPREHENSIVE SYNC UPDATE: Modified {jobs_updated} jobs in {xml_filename}")
                                
                                # Upload updated XML to SFTP if any changes were made (additions, removals, or modifications)
                                total_changes = (comprehensive_sync_summary.get('added_count', 0) + 
                                               comprehensive_sync_summary.get('removed_count', 0) + 
                                               comprehensive_sync_summary.get('updated_count', 0))
                                if total_changes > 0:
                                    # Find matching schedule for this XML file
                                    matching_schedule = None
                                    for schedule in active_schedules:
                                        if os.path.basename(schedule.file_path) == xml_filename:
                                            matching_schedule = schedule
                                            break
                                    
                                    if matching_schedule and matching_schedule.auto_upload_ftp:
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
                                                
                                                port = int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 22
                                                directory = sftp_directory.setting_value if sftp_directory else ''
                                                
                                                ftp_service = get_ftp_service()
                                                upload_result = ftp_service.upload_file(
                                                    local_file_path=xml_filename,
                                                    remote_filename=xml_filename,
                                                    hostname=sftp_hostname.setting_value,
                                                    username=sftp_username.setting_value,
                                                    password=sftp_password.setting_value,
                                                    port=port,
                                                    directory=directory
                                                )
                                                
                                                if upload_result.get('success'):
                                                    comprehensive_sync_summary['sftp_upload_success'] = True
                                                    app.logger.info(f"✅ SFTP upload successful for {xml_filename}")
                                                else:
                                                    app.logger.error(f"❌ SFTP upload failed for {xml_filename}: {upload_result.get('error')}")
                                                    comprehensive_sync_summary['sftp_upload_success'] = False
                                            else:
                                                app.logger.warning(f"SFTP upload requested but credentials not configured in Global Settings")
                                                comprehensive_sync_summary['sftp_upload_success'] = False
                                        except Exception as upload_error:
                                            app.logger.error(f"Error during SFTP upload for {xml_filename}: {str(upload_error)}")
                                            comprehensive_sync_summary['sftp_upload_success'] = False
                                

                                
                            except Exception as e:
                                app.logger.error(f"Error in comprehensive sync for {xml_filename}: {str(e)}")
                    
                    # CRITICAL FIX: If comprehensive sync made changes, mark XML sync as successful
                    # and populate sync summary for each monitor that detected changes
                    if comprehensive_sync_made_changes and monitors_processed:
                        app.logger.info(f"🔄 COMPREHENSIVE SYNC COMPLETED CHANGES: Updating monitor workflows")
                        
                        for monitor in monitors_processed:
                            # Mark XML sync as successful for monitors that had changes
                            if hasattr(monitor, '_has_changes') and monitor._has_changes:
                                # Set xml_sync_success = True for this monitor
                                monitor._xml_sync_success = True
                                monitor._xml_sync_summary = comprehensive_sync_summary.copy()
                                
                                app.logger.info(f"✅ Monitor {monitor.name}: XML sync marked successful via comprehensive sync")
                                
                                # Create xml_sync_completed activity
                                activity = BullhornActivity(
                                    monitor_id=monitor.id,
                                    activity_type='xml_sync_completed',
                                    details=f"XML sync completed via comprehensive sync: {comprehensive_sync_summary.get('added_count', 0)} added, {comprehensive_sync_summary.get('removed_count', 0)} removed, {comprehensive_sync_summary.get('updated_count', 0)} updated. SFTP upload: {comprehensive_sync_summary.get('sftp_upload_success', True)}",
                                    notification_sent=True  # System housekeeping - no email needed
                                )
                                db.session.add(activity)
                    
                    # Process pending notifications after comprehensive sync completes
                    if hasattr(app, '_pending_notifications') and app._pending_notifications:
                        app.logger.info(f"📧 Processing {len(app._pending_notifications)} pending notifications after comprehensive sync")
                        email_service = get_email_service()
                        
                        for notification_data in app._pending_notifications:
                            try:
                                # Update notification data with comprehensive sync results
                                notification_data['xml_sync_info'] = comprehensive_sync_summary.copy()
                                
                                # Send the notification
                                email_service.send_bullhorn_notification(
                                    monitor_name=notification_data['monitor_name'],
                                    email_address=notification_data['email_address'],
                                    added_jobs=notification_data['added_jobs'],
                                    removed_jobs=notification_data['removed_jobs'],
                                    modified_jobs=notification_data['modified_jobs'],
                                    total_jobs=notification_data['total_jobs'],
                                    summary=notification_data['summary'],
                                    xml_sync_info=notification_data['xml_sync_info']
                                )
                                
                                app.logger.info(f"✅ Email notification sent for monitor {notification_data['monitor_name']} to {notification_data['email_address']}")
                                
                            except Exception as email_error:
                                app.logger.error(f"❌ Failed to send notification for monitor {notification_data['monitor_name']}: {str(email_error)}")
                        
                        # Clear processed notifications
                        app._pending_notifications = []
                        app.logger.info("📧 All pending notifications processed and cleared")
                    
                    # Comprehensive sync logic temporarily simplified - critical bug fixed
                    app.logger.info("✅ CRISIS RESOLVED: Comprehensive sync safety mechanisms active")
            
            # Continue with monitor snapshot synchronization and final health checks
            app.logger.info("🔄 Finalizing monitor processing...")
            
            # CRITICAL: After all processing, ensure ALL monitor snapshots are in sync
            # This prevents jobs from being repeatedly detected as "new"
            if monitors_processed:
                app.logger.info("Synchronizing all monitor snapshots to prevent duplicate detections")
                try:
                    # Create a new Bullhorn service instance for snapshot sync
                    sync_bullhorn_service = get_bullhorn_service()
                    if sync_bullhorn_service.test_connection():
                        all_monitors = BullhornMonitor.query.filter_by(is_active=True).all()
                        for monitor in all_monitors:
                            try:
                                # Re-fetch current jobs for this monitor
                                if monitor.tearsheet_id == 0:
                                    current_jobs = sync_bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                                else:
                                    current_jobs = sync_bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                                
                                if current_jobs is not None:
                                    monitor.last_job_snapshot = json.dumps(current_jobs)
                                    app.logger.info(f"Synchronized {monitor.name} snapshot with {len(current_jobs)} jobs")
                            except Exception as e:
                                app.logger.error(f"Error synchronizing snapshot for {monitor.name}: {str(e)}")
                    else:
                        app.logger.error("Failed to connect to Bullhorn for snapshot synchronization")
                except Exception as e:
                    app.logger.error(f"Error during snapshot synchronization: {str(e)}")
            
            # PREVENTION LAYER 4: Final proactive health check and backup timing
            try:
                app.logger.info("HEALTH-CHECK: Performing final timing verification...")
                current_time_final = datetime.utcnow()
                
                # Check if any monitors are still showing as overdue after processing
                still_overdue = BullhornMonitor.query.filter(
                    BullhornMonitor.is_active == True,
                    BullhornMonitor.next_check < current_time_final - timedelta(minutes=2)  # Reduced from 5 to 2 minutes
                ).all()
                
                if still_overdue:
                    app.logger.warning(f"HEALTH-CHECK: Found {len(still_overdue)} monitors still overdue after processing. Implementing emergency timing reset...")
                    for monitor in still_overdue:
                        monitor.next_check = current_time_final + timedelta(minutes=2)  # Reduced from 5 to 2 minutes
                        app.logger.warning(f"EMERGENCY-RESET: {monitor.name} reset to {monitor.next_check}")
                    db.session.commit()
                
                # Log final timing status for all monitors
                all_monitors = BullhornMonitor.query.filter_by(is_active=True).all()
                healthy_count = 0
                for monitor in all_monitors:
                    if monitor.next_check > current_time_final:
                        healthy_count += 1
                    else:
                        app.logger.warning(f"HEALTH-CHECK: Monitor {monitor.name} still has timing issue: Next={monitor.next_check}, Now={current_time_final}")
                
                app.logger.info(f"HEALTH-CHECK: Final status - {healthy_count}/{len(all_monitors)} monitors have healthy timing")
                
            except Exception as health_error:
                app.logger.error(f"HEALTH-CHECK: Error during final health verification: {str(health_error)}")
            
            # Commit all changes
            db.session.commit()
            
        except Exception as e:
            app.logger.error(f"Error in Bullhorn monitor processing: {str(e)}")
            db.session.rollback()

# Use the regular monitoring function since simplified version is not available
monitoring_func = process_bullhorn_monitors
app.logger.info("Using standard monitoring function for Bullhorn processing")

# Add Bullhorn monitoring to scheduler
scheduler.add_job(
    func=monitoring_func,
    trigger=IntervalTrigger(minutes=2),  # Reduced from 5 to 2 minutes for faster detection
    id='process_bullhorn_monitors',
    name='Process Bullhorn Monitors',
    replace_existing=True
)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login page"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
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
            
            # Redirect to originally requested page or index
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            # Force scroll to top by adding fragment
            return redirect(url_for('index') + '#top')
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
    try:
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
    except Exception as e:
        error_status = {
            'status': 'unhealthy',
            'timestamp': datetime.utcnow().isoformat(),
            'error': str(e)
        }
        app.logger.error(f"Health check failed: {str(e)}")
        return jsonify(error_status), 503

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

@app.route('/')
def root():
    """Root endpoint - redirect to login or dashboard based on authentication"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    else:
        return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def index():
    """Main page with file upload form"""
    return render_template('index.html')

@app.route('/scheduler')
@login_required
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
            if 'temp_file' in locals() and temp_file and hasattr(temp_file, 'name') and os.path.exists(temp_file.name):
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
            return redirect(url_for('index'))
        
        # Generate output filename (preserve original name without "updated_" prefix)
        output_filename = original_filename
        # Use current working directory for output
        output_filepath = os.path.join(os.getcwd(), f"{unique_id}_{output_filename}")
        
        # Process the file
        result = processor.process_xml(input_filepath, output_filepath)
        
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
                        hostname.setting_value, 
                        username.setting_value, 
                        password.setting_value
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

@app.route('/bullhorn')
@login_required
def bullhorn_dashboard():
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
        
        # Get job counts for each monitor - prioritize stored snapshots over fresh API calls
        for monitor in monitors:
            try:
                # First, try to get count from stored snapshot
                if monitor.last_job_snapshot:
                    try:
                        stored_jobs = json.loads(monitor.last_job_snapshot)
                        monitor_job_counts[monitor.id] = len(stored_jobs)
                        continue
                    except (json.JSONDecodeError, TypeError):
                        app.logger.warning(f"Invalid job snapshot for monitor {monitor.name}, fetching fresh")
                
                # If no valid stored snapshot, fetch fresh data (only if connected)
                if bullhorn_connected:
                    if monitor.tearsheet_id == 0:
                        # Query-based monitor
                        jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                    else:
                        # Traditional tearsheet-based monitor
                        jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                    
                    monitor_job_counts[monitor.id] = len(jobs)
                    
                    # Store the fresh data for future use
                    monitor.last_job_snapshot = json.dumps(jobs)
                    monitor.last_check = datetime.utcnow()
                    db.session.commit()
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
        
        try:
            from simplified_monitoring_service import MonitoringService
        except ImportError:
            app.logger.warning("Simplified monitoring service not available, using direct processing")
            # Fallback to direct processing if simplified service not available
            from bullhorn_monitoring import process_bullhorn_monitors
            process_bullhorn_monitors()
            return jsonify({
                'success': True,
                'message': 'Job sync triggered successfully (fallback mode)',
                'timestamp': datetime.utcnow().isoformat()
            })
        
        # Run monitoring immediately
        service = MonitoringService(db.session)
        service.xml_service = XMLIntegrationService()
        service.email_service = get_email_service()
        service.BullhornMonitor = BullhornMonitor
        service.BullhornActivity = BullhornActivity
        service.GlobalSettings = GlobalSettings
        service.ScheduledProcessing = ScheduleConfig  # ScheduledProcessing is actually ScheduleConfig
        
        service.process_all_monitors()
        
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
        
        # Create health service instance
        health_service = MonitorHealthService(db.session, GlobalSettings, BullhornMonitor)
        
        # Run health check
        result = health_service.check_monitor_health()
        
        return jsonify({
            'success': True,
            'message': 'Health check completed successfully',
            'timestamp': datetime.utcnow().isoformat(),
            'result': result
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
        
        xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
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
            if schedule.interval_type == 'daily':
                schedule.next_run = current_time + timedelta(days=1)
            elif schedule.interval_type == 'weekly':
                schedule.next_run = current_time + timedelta(weeks=1)
            else:
                schedule.next_run = current_time + timedelta(hours=1)
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
    """Display job application form"""
    try:
        # Get source from query parameters
        source = request.args.get('source', '')
        
        # Decode job title from URL
        import urllib.parse
        decoded_title = urllib.parse.unquote(job_title)
        
        return render_template('apply.html', 
                             job_id=job_id, 
                             job_title=decoded_title, 
                             source=source)
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
        
        return jsonify(parse_result)
        
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
        
        # Submit the application
        submission_result = job_app_service.submit_application(
            application_data=application_data,
            resume_file=resume_file,
            cover_letter_file=cover_letter_file if cover_letter_file and cover_letter_file.filename != '' else None
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
            # Process the XML
            temp_output = f"{demo_xml_file}.processed"
            process_result = xml_processor.process_xml(demo_xml_file, temp_output)
            
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
                if 'demo_xml_file' in locals() and demo_xml_file and os.path.exists(demo_xml_file):
                    os.remove(demo_xml_file)
            except:
                pass
            return {
                'success': False,
                'error': f'XML sync failed: {sync_result.get("error")}'
            }
            
    except Exception as e:
        # Clean up on exception
        try:
            if 'demo_xml_file' in locals() and demo_xml_file and os.path.exists(demo_xml_file):
                os.remove(demo_xml_file)
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
            
            # Create health service instance
            health_service = MonitorHealthService(db.session, GlobalSettings, BullhornMonitor)
            
            # Perform health check
            result = health_service.check_monitor_health()
            
            if result['status'] == 'completed':
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
    if not _background_services_started:
        _background_services_started = True
        lazy_start_scheduler()
        lazy_apply_optimizations()
        lazy_init_file_consolidation()

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

# Scheduler and background services will be started lazily when first needed
# This significantly reduces application startup time for deployment health checks

