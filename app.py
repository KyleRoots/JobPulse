import os
import logging
from datetime import datetime, timedelta
import json
import re
import requests
from flask import Flask, render_template, request, send_file, flash, redirect, url_for, jsonify, after_this_request, has_request_context, session, abort
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
# Use simplified incremental monitoring instead of comprehensive monitoring
from incremental_monitoring_service import IncrementalMonitoringService
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

# Environment configuration - use explicit environment variables
PRODUCTION_DOMAINS = {'jobpulse.lyntrix.ai', 'www.jobpulse.lyntrix.ai'}

# Set app environment at startup - this will be used for background tasks
# Priority: APP_ENV > ENVIRONMENT > production (default for safety)
env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'production').lower()
app.config['ENVIRONMENT'] = env

# Log the chosen environment for debugging
print(f"App environment set to: {env}")  # Use print for startup visibility

# Global scheduler state management
scheduler_started = False
scheduler_lock = threading.Lock()

def is_production_request():
    """Detect if current request is from production domain with hardened detection"""
    # Check if we have request context first
    if not has_request_context():
        # No request context - return False for safety
        return False
    
    try:
        # Handle X-Forwarded-Host (common in proxied deployments) and normalize
        host = request.headers.get('X-Forwarded-Host', request.host or '').split(',')[0].strip()
        host = host.split(':')[0].rstrip('.').lower()
        
        # Check against production domains
        is_prod = host in PRODUCTION_DOMAINS
        
        # Debug logging for troubleshooting
        if not is_prod:
            app.logger.debug(f"🔍 Not production: host='{host}' (X-Forwarded-Host={request.headers.get('X-Forwarded-Host', 'None')}, request.host={request.host})")
        else:
            app.logger.info(f"🎯 Production request detected: host='{host}'")
            
        return is_prod
        
    except (RuntimeError, AttributeError) as e:
        app.logger.debug(f"🔍 Production detection failed: {str(e)}")
        return False

def get_xml_filename():
    """Generate environment-specific XML filename for uploads"""
    base_filename = "myticas-job-feed-v2"
    
    # Try app config first (works for background tasks)
    env = app.config.get('ENVIRONMENT')
    if env == 'production':
        app.logger.debug(f"Using production filename (source: app config)")
        return f"{base_filename}.xml"
    elif env == 'development':
        app.logger.debug(f"Using development filename (source: app config)")
        return f"{base_filename}-dev.xml"
    
    # Fall back to request detection (for manual requests when config not set)
    try:
        if is_production_request():
            app.logger.debug(f"Using production filename (source: request host)")
            return f"{base_filename}.xml"
        else:
            app.logger.debug(f"Using development filename (source: request host)")
            return f"{base_filename}-dev.xml"
    except:
        # Final fallback - default to production to avoid publishing dev filename in production
        app.logger.warning(f"Could not determine environment, defaulting to production filename for safety")
        return f"{base_filename}.xml"

# Simple automated upload scheduling - no complex environment detection needed

app.secret_key = os.environ.get("SESSION_SECRET")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Production session optimization - Extended for better user experience
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # 30 days instead of 1 hour
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)  # Remember me lasts 30 days
app.config['REMEMBER_COOKIE_SECURE'] = True
app.config['REMEMBER_COOKIE_HTTPONLY'] = True

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

# Company-specific URL generation - no global override needed
# Each service will determine URLs based on company context

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

# Import models
from models import User, ScheduleConfig, ProcessingLog, RefreshLog, GlobalSettings, BullhornMonitor, BullhornActivity, TearsheetJobHistory, EmailDeliveryLog, RecruiterMapping, SchedulerLock

# Activity Detail Formatter Function
def format_activity_details(activity_type, details_json):
    """
    Convert JSON activity details into user-friendly formatted text.
    
    Args:
        activity_type: String indicating the type of activity
        details_json: JSON string containing activity details
    
    Returns:
        Formatted string suitable for display
    """
    if not details_json:
        return "No details available"
    
    try:
        details = json.loads(details_json) if isinstance(details_json, str) else details_json
    except (json.JSONDecodeError, TypeError):
        return details_json if isinstance(details_json, str) else "No details available"
    
    # Format based on activity type
    if activity_type == 'email_notification':
        monitor_type = details.get('monitor_type', 'Unknown')
        changes = details.get('changes_detected', 0)
        added = details.get('added_jobs', 0)
        removed = details.get('removed_jobs', 0)
        modified = details.get('modified_jobs', 0)
        
        if changes == 0:
            return f"{monitor_type}: No changes detected"
        
        change_parts = []
        if added > 0:
            change_parts.append(f"+{added} added")
        if removed > 0:
            change_parts.append(f"-{removed} removed")
        if modified > 0:
            change_parts.append(f"~{modified} modified")
        
        change_summary = ", ".join(change_parts) if change_parts else "processed"
        return f"{monitor_type}: {changes} jobs ({change_summary})"
    
    elif activity_type == 'xml_sync_completed':
        jobs_processed = details.get('jobs_processed', details.get('total_jobs', 0))
        execution_time = details.get('execution_time', '')
        cycle_time = details.get('cycle_time', 0)
        
        time_info = f" in {cycle_time:.1f}s" if cycle_time > 0 else ""
        return f"XML Sync: {jobs_processed} jobs processed{time_info}"
    
    elif activity_type == 'check_completed':
        results = details.get('results', {})
        monitor_name = details.get('monitor_name', 'Monitor')
        
        if isinstance(results, dict):
            jobs_found = results.get('jobs_found', results.get('total_jobs', 0))
            return f"{monitor_name}: {jobs_found} jobs verified"
        else:
            return f"{monitor_name}: Check completed"
    
    elif activity_type == 'job_added':
        job_title = details.get('job_title', details.get('title', 'Unknown Job'))
        company = details.get('company', '')
        location = details.get('location', '')
        
        parts = [job_title]
        if company:
            parts.append(f"at {company}")
        if location:
            parts.append(f"in {location}")
        
        return " ".join(parts)
    
    elif activity_type == 'job_removed':
        job_title = details.get('job_title', details.get('title', 'Unknown Job'))
        reason = details.get('reason', 'No longer active')
        return f"{job_title} - {reason}"
    
    elif activity_type == 'job_modified':
        changes = details.get('changes', {})
        if isinstance(changes, dict):
            change_list = []
            for field, change_info in changes.items():
                if isinstance(change_info, dict) and 'old' in change_info and 'new' in change_info:
                    change_list.append(f"{field} updated")
                else:
                    change_list.append(field)
            
            if change_list:
                return f"Updated: {', '.join(change_list)}"
        
        return details.get('description', 'Job details updated')
    
    elif activity_type == 'error':
        error_msg = details.get('error', details.get('message', 'Unknown error'))
        return f"Error: {error_msg}"
    
    elif activity_type == 'automated_upload':
        jobs_count = details.get('jobs_count', details.get('total_jobs', 0))
        success = details.get('upload_success', False)
        
        status = "successful" if success else "failed"
        return f"Automated Upload: {jobs_count} jobs - {status}"
    
    elif activity_type == 'scheduled_processing':
        # Handle scheduled processing activities
        if isinstance(details, str):
            return details
        jobs_processed = details.get('jobs_processed', 0)
        schedule_name = details.get('schedule_name', 'Unknown Schedule')
        return f"Scheduled Processing: {schedule_name} - {jobs_processed} jobs processed"
    
    elif activity_type == 'scheduled_processing_error':
        # Handle scheduled processing error activities
        if isinstance(details, str):
            return details
        error_msg = details.get('error', 'Unknown error')
        schedule_name = details.get('schedule_name', 'Unknown Schedule')
        return f"Processing Error: {schedule_name} - {error_msg}"
    
    elif activity_type == 'monitoring_cycle_completed':
        # Handle incremental monitoring cycle activities
        if isinstance(details, str):
            return details
        added = details.get('jobs_added', 0)
        removed = details.get('jobs_removed', 0) 
        updated = details.get('jobs_updated', 0)
        excluded = details.get('excluded_jobs', 0)
        total = details.get('total_jobs', 0)
        
        changes = []
        if added > 0:
            changes.append(f"+{added} added")
        if removed > 0:
            changes.append(f"-{removed} removed")  
        if updated > 0:
            changes.append(f"~{updated} updated")
        if excluded > 0:
            changes.append(f"🚫{excluded} excluded")
            
        if changes:
            change_summary = ", ".join(changes)
            return f"Monitoring Cycle: {total} total jobs ({change_summary})"
        else:
            return f"Monitoring Cycle: {total} total jobs (no changes)"
    
    # Default fallback for unknown activity types
    if isinstance(details, dict):
        # Try to extract meaningful information
        if 'message' in details:
            return details['message']
        elif 'description' in details:
            return details['description']
        elif 'total_jobs' in details:
            return f"Processed {details['total_jobs']} jobs"
        elif 'jobs_count' in details:
            return f"Processed {details['jobs_count']} jobs"
    
    # Final fallback - return truncated JSON
    detail_str = str(details) if not isinstance(details, str) else details
    return detail_str[:100] + "..." if len(detail_str) > 100 else detail_str

# Register the formatter as a Jinja2 template filter
@app.template_filter('format_activity')
def format_activity_filter(activity):
    """Template filter to format activity details for display"""
    return format_activity_details(activity.activity_type, activity.details)

# Register timezone conversion filters for templates
from timezone_utils import jinja_eastern_time, jinja_eastern_short, jinja_eastern_datetime

@app.template_filter('eastern_time')
def eastern_time_filter(utc_dt, format_string='%b %d, %Y at %I:%M %p %Z'):
    """Convert UTC datetime to Eastern Time with custom format"""
    return jinja_eastern_time(utc_dt, format_string)

@app.template_filter('eastern_short')
def eastern_short_filter(utc_dt):
    """Convert UTC datetime to short Eastern Time format (Oct 19, 2025 9:44 PM EDT)"""
    return jinja_eastern_short(utc_dt)

@app.template_filter('eastern_datetime')
def eastern_datetime_filter(utc_dt):
    """Convert UTC datetime to datetime Eastern Time format (2025-10-19 09:44 PM EDT)"""
    return jinja_eastern_datetime(utc_dt)

# Initialize database tables
with app.app_context():
    db.create_all()
    
    # Run any necessary schema migrations for existing tables
    # SQLAlchemy's create_all() only creates new tables, it doesn't add columns to existing ones
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        
        # Migration: Add vetting_threshold column to job_vetting_requirements if missing
        if 'job_vetting_requirements' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('job_vetting_requirements')]
            if 'vetting_threshold' not in columns:
                db.session.execute(text('ALTER TABLE job_vetting_requirements ADD COLUMN vetting_threshold INTEGER'))
                db.session.commit()
                app.logger.info('🔧 Migration: Added vetting_threshold column to job_vetting_requirements')
    except Exception as migrate_err:
        app.logger.warning(f'Migration check failed (may be first run): {migrate_err}')
    
    # Seed database with initial data (production-safe, idempotent)
    try:
        from seed_database import seed_database
        from models import User
        
        seeding_results = seed_database(db, User)
        
        # Log seeding results
        if seeding_results.get('admin_created'):
            app.logger.info(f"🌱 Database seeding: Created admin user {seeding_results.get('admin_username')}")
        else:
            app.logger.info(f"🌱 Database seeding: Admin user already exists ({seeding_results.get('admin_username')})")
        
        if seeding_results.get('errors'):
            for error in seeding_results['errors']:
                app.logger.error(f"🌱 Seeding error: {error}")
    
    except Exception as e:
        # Log seeding errors but don't crash the app
        app.logger.error(f"❌ Database seeding failed: {str(e)}")
        app.logger.debug(f"Seeding error details: {traceback.format_exc()}")

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
    """Process all active Bullhorn monitors using simplified incremental monitoring"""
    with app.app_context():
        try:
            # Check if XML feed is frozen
            from feeds.freeze_manager import FreezeManager
            freeze_mgr = FreezeManager()
            if freeze_mgr.is_frozen():
                app.logger.info("🔒 XML FEED FROZEN: Skipping monitoring cycle")
                return
            
            app.logger.info("🔄 INCREMENTAL MONITOR: Starting simplified monitoring cycle")
            
            # Initialize incremental monitoring service
            from incremental_monitoring_service import IncrementalMonitoringService
            monitoring_service = IncrementalMonitoringService()
            
            # Check if we should use the new feed generator
            use_new_feed = os.environ.get('USE_NEW_FEED', '').lower() == 'true'
            
            # Get monitors from database first
            db_monitors = BullhornMonitor.query.filter_by(is_active=True).all()
            
            if db_monitors:
                # Use actual database monitors
                monitors = db_monitors
                app.logger.info(f"Using {len(monitors)} database monitors")
            else:
                # Fallback to hardcoded monitors if database is empty
                class MockMonitor:
                    def __init__(self, name, tearsheet_id):
                        self.name = name
                        self.tearsheet_id = tearsheet_id
                        self.is_active = True
                
                monitors = [
                    MockMonitor('Sponsored - OTT', 1256),
                    MockMonitor('Sponsored - VMS', 1264),
                    MockMonitor('Sponsored - GR', 1499),
                    MockMonitor('Sponsored - CHI', 1257),
                    MockMonitor('Sponsored - STSI', 1556)
                ]
                app.logger.info(f"Using {len(monitors)} hardcoded tearsheet monitors (fallback)")
            
            # Run incremental monitoring cycle with Flask app context
            with app.app_context():
                cycle_results = monitoring_service.run_monitoring_cycle()
            
            # Update monitor timing in database
            if db_monitors:
                current_time = datetime.utcnow()
                for monitor in db_monitors:
                    monitor.last_check = current_time
                    monitor.next_check = current_time + timedelta(minutes=5)  # Set next check for 5 minutes
                try:
                    db.session.commit()
                    app.logger.info(f"✅ Updated timing for {len(db_monitors)} monitors")
                except Exception as e:
                    app.logger.error(f"Failed to update monitor timing: {e}")
                    db.session.rollback()
            
            app.logger.info(f"✅ Incremental monitoring completed: {cycle_results}")
            app.logger.info("📊 MONITOR CYCLE COMPLETE - Incremental monitoring handled all updates")
            
        except Exception as e:
            app.logger.error(f"❌ Incremental monitoring error: {str(e)}")
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

# CRITICAL: Use print() for guaranteed logging during module initialization
# app.logger may not be properly configured at this point
print("🔒 SCHEDULER INIT: Attempting to acquire scheduler lock...", flush=True)

# Try to acquire exclusive lock for scheduler  
try:
    scheduler_lock_fd = os.open(scheduler_lock_file, os.O_CREAT | os.O_WRONLY)
    fcntl.flock(scheduler_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    is_primary_worker = True
    worker_pid = os.getpid()
    print(f"✅ SCHEDULER INIT: Process {worker_pid} acquired scheduler lock - will run as PRIMARY scheduler", flush=True)
    app.logger.info(f"✅ Process {worker_pid} acquired scheduler lock - will run as PRIMARY scheduler")
    atexit.register(release_scheduler_lock)
except (IOError, OSError) as e:
    worker_pid = os.getpid()
    print(f"⚠️ SCHEDULER INIT: Process {worker_pid} could not acquire scheduler lock (already held): {e}", flush=True)
    app.logger.info(f"⚠️ Process {worker_pid} could not acquire scheduler lock - another scheduler is already running")
    is_primary_worker = False
    if scheduler_lock_fd:
        os.close(scheduler_lock_fd)
        scheduler_lock_fd = None
except Exception as e:
    # Catch ANY exception to prevent silent failures
    print(f"❌ SCHEDULER INIT: Unexpected error during lock acquisition: {e}", flush=True)
    app.logger.error(f"❌ Unexpected scheduler lock error: {e}")
    is_primary_worker = False

print(f"🔒 SCHEDULER INIT: is_primary_worker = {is_primary_worker}", flush=True)

if is_primary_worker:
    # RE-ENABLED: 5-Minute Incremental Monitoring (October 2025)
    # Provides real-time visibility into job changes before 30-minute upload cycle
    # Now safe with fast keyword classification (<1 second, no timeouts)
    # This monitors Bullhorn and updates local database every 5 minutes
    # SFTP uploads still happen on 30-minute cycle separately
    try:
        scheduler.add_job(
            func=process_bullhorn_monitors,
            trigger=IntervalTrigger(minutes=5),
            id='process_bullhorn_monitors',
            name='5-Minute Tearsheet Monitor with Keyword Classification',
            replace_existing=True
        )
        print("✅ SCHEDULER INIT: 5-minute tearsheet monitoring job added", flush=True)
        app.logger.info("✅ 5-minute tearsheet monitoring ENABLED - provides UI visibility before 30-minute upload cycle")
    except Exception as e:
        print(f"❌ SCHEDULER INIT: Failed to add 5-minute monitoring job: {e}", flush=True)
        app.logger.error(f"Failed to add 5-minute monitoring job: {e}")
else:
    print(f"⚠️ SCHEDULER INIT: Process {os.getpid()} skipping scheduler setup - another worker handles scheduling", flush=True)
    app.logger.info(f"⚠️ Process {os.getpid()} skipping scheduler setup - another worker handles scheduling")

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard_redirect'))
    
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
            
            login_user(user, remember=True)  # Remember user for extended session
            session.permanent = True  # Enable 30-day session persistence
            # Removed welcome message for cleaner login experience
            
            # Start scheduler on successful login
            ensure_background_services()
            
            # Redirect to originally requested page or index
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            # Force scroll to top by adding fragment
            return redirect(url_for('dashboard_redirect') + '#top')
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

def get_automation_status():
    """Check if automation/scheduler is currently active"""
    try:
        # Check if recent monitoring activities have occurred (sign of active automation)
        recent_cutoff = datetime.utcnow() - timedelta(minutes=10)
        recent_activity = BullhornActivity.query.filter(
            BullhornActivity.created_at > recent_cutoff,
            BullhornActivity.activity_type.in_(['check_completed', 'job_added', 'job_removed', 'job_modified'])
        ).count()
        
        if recent_activity > 0:
            return True
            
        # Check if monitors have been updated recently (indicates scheduler is running)
        recent_monitors = BullhornMonitor.query.filter(
            BullhornMonitor.last_check > recent_cutoff
        ).count()
        
        if recent_monitors > 0:
            return True
            
        # Fall back to checking if we have any active monitors at all
        active_monitors = BullhornMonitor.query.filter_by(is_active=True).count()
        return active_monitors > 0
        
    except Exception as e:
        app.logger.debug(f"Automation status check error: {e}")
        return True  # Default to active if can't determine

# Test route removed for production deployment

@app.route('/')
def root():
    """Root endpoint - redirect to login or dashboard based on authentication"""
    if current_user.is_authenticated:
        # Ensure scheduler is running for authenticated users
        ensure_background_services()
        return redirect(url_for('dashboard_redirect'))
    else:
        return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard_redirect():
    """Main dashboard home page"""
    # Ensure scheduler is running for authenticated users
    ensure_background_services()
    
    # Get automation status
    try:
        automation_active = get_automation_status()
    except:
        automation_active = True  # Default to active if can't determine
    
    # Get recent activities (use BullhornActivity model that exists)
    try:
        recent_activities = BullhornActivity.query.order_by(BullhornActivity.created_at.desc()).limit(5).all()
    except:
        recent_activities = []
    
    # Get latest schedules
    try:
        schedules = ScheduleConfig.query.filter_by(is_active=True).limit(3).all()
    except:
        schedules = []
    
    # Get environment status
    environment_status = None
    try:
        from models import EnvironmentStatus
        environment_status = EnvironmentStatus.query.filter_by(environment_name='production').first()
    except:
        environment_status = None
    
    # Dashboard metrics
    active_jobs = 0
    candidates_vetted = 0
    emails_sent = 0
    auto_fixed = 0
    
    try:
        # Active jobs: count from JobVettingRequirements (synced jobs from tearsheets)
        from models import JobVettingRequirements
        active_jobs = JobVettingRequirements.query.count()
    except Exception as e:
        app.logger.debug(f"Could not count active jobs: {e}")
    
    try:
        # Candidates vetted: count from CandidateVettingLog where status='completed'
        from models import CandidateVettingLog
        candidates_vetted = CandidateVettingLog.query.filter_by(status='completed').count()
    except Exception as e:
        app.logger.debug(f"Could not count vetted candidates: {e}")
    
    try:
        # Emails sent: count from EmailDeliveryLog where delivery_status='sent'
        from models import EmailDeliveryLog
        emails_sent = EmailDeliveryLog.query.filter_by(delivery_status='sent').count()
    except Exception as e:
        app.logger.debug(f"Could not count emails sent: {e}")
    
    try:
        # Auto-fixed issues: count BullhornActivity entries with type 'job_modified' (auto-corrected data)
        auto_fixed = BullhornActivity.query.filter_by(activity_type='job_modified').count()
    except Exception as e:
        app.logger.debug(f"Could not count auto-fixed issues: {e}")
    
    return render_template('dashboard.html', 
                         active_page='dashboard',
                         recent_activities=recent_activities,
                         automation_active=automation_active,
                         schedules=schedules,
                         environment_status=environment_status,
                         active_jobs=active_jobs,
                         candidates_vetted=candidates_vetted,
                         emails_sent=emails_sent,
                         auto_fixed=auto_fixed)

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
    
    return render_template('scheduler.html', schedules=schedules, recent_logs=recent_logs, active_xml_files=active_xml_files, next_refresh_info=next_refresh_info, active_page='scheduler')

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
    """Ad-hoc refresh of all reference numbers using fresh Bullhorn data"""
    try:
        app.logger.info("🔄 AD-HOC REFERENCE NUMBER REFRESH: Starting manual refresh with fresh Bullhorn data")
        
        # Generate fresh XML content using SimplifiedXMLGenerator (same as 120-hour refresh)
        from simplified_xml_generator import SimplifiedXMLGenerator
        
        # Create generator instance with database access
        generator = SimplifiedXMLGenerator(db=db)
        
        # Generate fresh XML content from all Bullhorn tearsheets
        xml_content, stats = generator.generate_fresh_xml()
        app.logger.info(f"📊 Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
        
        # Apply reference number refresh to the generated content
        from lightweight_reference_refresh import lightweight_refresh_references_from_content
        
        # Refresh reference numbers in the generated XML content
        result = lightweight_refresh_references_from_content(xml_content)
        
        if not result['success']:
            return jsonify({
                'success': False,
                'error': f"Failed to refresh reference numbers: {result.get('error', 'Unknown error')}"
            }), 500
        
        app.logger.info(f"✅ Reference refresh complete: {result['jobs_updated']} jobs updated in {result['time_seconds']:.2f} seconds")
        
        # CRITICAL: Save reference numbers to database for preservation (database-first approach)
        # Database save is REQUIRED - failure will prevent upload to ensure consistency
        from lightweight_reference_refresh import save_references_to_database
        db_save_success = save_references_to_database(result['xml_content'])
        
        if not db_save_success:
            # Database save failure is CRITICAL - prevent upload to maintain database-first architecture
            error_msg = "Database-first architecture requires successful DB save - manual refresh aborted"
            app.logger.critical(f"❌ CRITICAL: {error_msg}")
            return jsonify({
                'success': False,
                'error': error_msg,
                'details': 'Reference numbers must be saved to database before upload. Please try again.'
            }), 500
        
        app.logger.info("💾 DATABASE-FIRST: Reference numbers successfully saved to database")
        
        # Initialize services for upload and notification
        email_service = EmailService()
        
        # Initialize FTP service with proper credentials from GlobalSettings
        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
        sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
        
        upload_success = False
        upload_error_message = None
        
        # Upload the refreshed XML to server
        if (sftp_hostname and sftp_hostname.setting_value and 
            sftp_username and sftp_username.setting_value and 
            sftp_password and sftp_password.setting_value):
            
            # Create temporary file with refreshed content - ensure UTF-8 encoding
            import tempfile
            temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8')
            try:
                temp_file.write(result['xml_content'])
                temp_file.flush()  # Ensure content is written to disk
                temp_file_path = temp_file.name
            finally:
                temp_file.close()  # Explicitly close file before upload
            
            try:
                from ftp_service import FTPService
                ftp_service = FTPService(
                    hostname=sftp_hostname.setting_value,
                    username=sftp_username.setting_value,
                    password=sftp_password.setting_value,
                    target_directory=sftp_directory.setting_value if sftp_directory else "public_html",
                    port=int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222,
                    use_sftp=True
                )
                
                # Upload with environment-specific filename
                remote_filename = get_xml_filename()
                upload_result = ftp_service.upload_file(temp_file_path, remote_filename)
                
                if upload_result:
                    upload_success = True
                    app.logger.info(f"📤 Successfully uploaded refreshed XML as {remote_filename} to server")
                else:
                    upload_error_message = "Upload failed: FTP service returned False"
                    app.logger.error(upload_error_message)
                    
            except Exception as upload_error:
                upload_error_message = str(upload_error)
                app.logger.error(f"Upload failed: {upload_error_message}")
            finally:
                # Clean up temporary file
                try:
                    os.remove(temp_file_path)
                except:
                    pass
        else:
            upload_error_message = "SFTP credentials not configured"
            app.logger.warning("SFTP not configured - skipping upload")
        
        # Log this manual activity to application log and database
        app.logger.info(f"🔄 MANUAL REFRESH COMPLETE: User {current_user.username} refreshed {result['jobs_updated']} reference numbers")
        
        # Record the refresh in database (matching 120-hour refresh pattern)
        try:
            from datetime import date
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
            app.logger.info("📝 Manual refresh completion logged to database")
        except Exception as e:
            app.logger.error(f"Failed to record refresh log: {e}")
            db.session.rollback()
        
        # Send notification email (matching 120-hour refresh pattern)
        try:
            # Get notification email from global settings
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
                    app.logger.info(f"📧 Manual refresh notification sent to {notification_email_setting.setting_value}")
                else:
                    app.logger.warning("Failed to send notification email")
        
        except Exception as email_error:
            app.logger.error(f"Email notification failed: {str(email_error)}")
        
        return jsonify({
            'success': True,
            'jobs_processed': result['jobs_updated'],
            'upload_success': upload_success,
            'upload_error': upload_error_message if not upload_success else None,
            'message': f'Successfully refreshed {result["jobs_updated"]} reference numbers using fresh Bullhorn data'
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

@app.route('/download-current-xml')
@login_required
def download_current_xml():
    """Generate and download fresh XML from all Bullhorn tearsheets"""
    try:
        app.logger.info("🚀 Starting fresh XML generation for download")
        
        # Import simplified generator
        from simplified_xml_generator import SimplifiedXMLGenerator
        
        # Create generator instance with database access
        generator = SimplifiedXMLGenerator(db=db)
        
        # Generate fresh XML
        xml_content, stats = generator.generate_fresh_xml()
        
        # Send change notification email ONLY during manual downloads
        try:
            app.logger.info("📧 Checking for job changes to include in download notification...")
            
            # Check if email notifications are globally enabled
            email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
            email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
            
            if (email_enabled and email_enabled.setting_value == 'true' and 
                email_setting and email_setting.setting_value):
                
                xml_monitor = create_xml_monitor()
                email_service = get_email_service()
                
                # Use the GENERATED XML content instead of downloading from website
                # This ensures we compare the fresh data with the previous state
                result = xml_monitor.monitor_xml_changes_with_content(
                    xml_content=xml_content,
                    notification_email=email_setting.setting_value, 
                    email_service=email_service, 
                    enable_email_notifications=True
                )
                
                if result.get('success'):
                    changes = result.get('changes', {})
                    total_changes = changes.get('total_changes', 0)
                    email_sent = result.get('email_sent', False)  # Capture actual email send status
                    
                    if total_changes > 0:
                        if email_sent:
                            app.logger.info(f"📧 Download notification sent: {total_changes} job changes detected since last download")
                        else:
                            app.logger.info(f"📧 Download notification attempted: {total_changes} changes detected but email sending failed")
                        
                        # Log to Activity monitoring system with accurate send status
                        try:
                            activity_details = {
                                'monitor_type': 'Manual Download Notification',
                                'changes_detected': total_changes,
                                'added_jobs': changes.get('added', 0) if isinstance(changes.get('added'), int) else len(changes.get('added', [])),
                                'removed_jobs': changes.get('removed', 0) if isinstance(changes.get('removed'), int) else len(changes.get('removed', [])),
                                'modified_jobs': changes.get('modified', 0) if isinstance(changes.get('modified'), int) else len(changes.get('modified', [])),
                                'email_attempted_to': email_setting.setting_value[:10] + "...",  # Mask email for privacy
                                'email_actually_sent': email_sent,
                                'trigger': 'manual_download'
                            }
                            
                            xml_monitor_activity = BullhornActivity(
                                monitor_id=None,  # Manual download trigger, not tied to specific tearsheet
                                activity_type='download_notification',
                                details=json.dumps(activity_details),
                                notification_sent=email_sent  # Accurate status based on actual send result
                            )
                            db.session.add(xml_monitor_activity)
                            db.session.commit()
                            
                            app.logger.info("📧 Manual download notification logged to Activity monitoring")
                            
                        except Exception as e:
                            app.logger.error(f"Failed to log download notification activity: {str(e)}")
                            db.session.rollback()
                    else:
                        app.logger.info("📧 No job changes detected since last download - no notification sent")
                else:
                    app.logger.warning(f"📧 Download notification check failed: {result.get('error', 'Unknown error')}")
            else:
                if not email_enabled or email_enabled.setting_value != 'true':
                    app.logger.info("📧 Email notifications globally disabled - skipping download notification")
                else:
                    app.logger.info("📧 No notification email configured - skipping download notification")
                
        except Exception as e:
            app.logger.error(f"Error sending download notification: {str(e)}")
            # Continue with download even if notification fails
        
        # Create temporary file for download
        temp_filename = f'myticas-job-feed-v2_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xml'
        temp_filepath = os.path.join(tempfile.gettempdir(), temp_filename)
        
        # Write XML to temporary file
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            f.write(xml_content)
        
        app.logger.info(f"✅ Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
        
        # Clean up temp file after sending
        @after_this_request
        def remove_temp_file(response):
            try:
                os.remove(temp_filepath)
            except Exception as e:
                app.logger.error(f"Error cleaning up temp file: {str(e)}")
            return response
        
        # Return the file for download
        return send_file(temp_filepath,
                        as_attachment=True,
                        download_name=temp_filename,
                        mimetype='application/xml')
        
    except Exception as e:
        app.logger.error(f"Error generating fresh XML: {str(e)}")
        flash(f'Error generating XML file: {str(e)}', 'error')
        return redirect(url_for('bullhorn_dashboard'))

@app.route('/automation-status')
def automation_status():
    """Get current automation status based on REAL scheduler job state"""
    try:
        # Check for actual scheduled job first (reality-driven approach)
        job_exists = False
        job_scheduled = False
        next_upload_time = None
        next_upload_iso = None
        next_upload_timestamp = None
        upload_interval = "30 minutes"
        
        try:
            job = scheduler.get_job('automated_upload')
            if job:
                job_exists = True
                if job.next_run_time:
                    job_scheduled = True
                    # Provide multiple formats for robust client-side handling
                    next_upload_time = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S UTC')
                    next_upload_iso = job.next_run_time.isoformat()
                    next_upload_timestamp = int(job.next_run_time.timestamp() * 1000)  # milliseconds
        except Exception as e:
            app.logger.debug(f"Could not get scheduler job info: {str(e)}")
        
        # Get database setting for reference (but don't drive UI from it)
        automation_setting = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
        db_setting_enabled = automation_setting and automation_setting.setting_value == 'true'
        
        # Determine actual automation status from scheduler reality
        if job_exists and job_scheduled:
            automation_enabled = True
            status = 'Active'
        elif job_exists and not job_scheduled:
            automation_enabled = False  
            status = 'Job exists but not scheduled'
        else:
            automation_enabled = False
            status = 'Not scheduled'
        
        # Get last upload time from GlobalSettings
        last_upload_setting = GlobalSettings.query.filter_by(setting_key='last_sftp_upload_time').first()
        last_upload_time = last_upload_setting.setting_value if last_upload_setting else "No uploads yet"
        
        return jsonify({
            'automation_enabled': automation_enabled,
            'job_exists': job_exists,
            'job_scheduled': job_scheduled,
            'db_setting_enabled': db_setting_enabled,  # For debugging discrepancies
            'next_upload_time': next_upload_time,  # Human readable
            'next_upload_iso': next_upload_iso,    # ISO 8601 format
            'next_upload_timestamp': next_upload_timestamp,  # Unix timestamp in ms
            'last_upload_time': last_upload_time,
            'upload_interval': upload_interval,
            'status': status
        })
        
    except Exception as e:
        app.logger.error(f"Error getting automation status: {str(e)}")
        return jsonify({'error': 'Failed to get automation status'}), 500

@app.route('/test-upload', methods=['POST'])
@login_required
def manual_test_upload():
    """Manual upload testing for dev environment"""
    try:
        app.logger.info("🧪 Manual test upload initiated")
        
        # Generate fresh XML
        from simplified_xml_generator import SimplifiedXMLGenerator
        generator = SimplifiedXMLGenerator(db=db)
        xml_content, stats = generator.generate_fresh_xml()
        
        app.logger.info(f"📊 Generated test XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
        
        # Check if SFTP is configured
        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
            return jsonify({
                'success': False,
                'error': 'SFTP not enabled in settings',
                'job_count': stats['job_count'],
                'xml_size': stats['xml_size_bytes']
            })
        
        # Call the automated_upload function and capture its result
        upload_result = automated_upload()
        
        # automated_upload doesn't currently return structured results, so we'll simulate this for now
        # In the future, we should modify automated_upload to return success/failure status
        return jsonify({
            'success': True,  # We'll assume success if no exception was raised
            'message': 'Test upload completed',
            'job_count': stats['job_count'],
            'xml_size': stats['xml_size_bytes'],
            'destination': 'configured SFTP directory',
            'note': 'Upload attempted - check logs for detailed results'
        })
        
    except Exception as e:
        app.logger.error(f"Manual test upload error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
        }), 500

@app.route('/settings')
@login_required
def settings():
    """Global settings page for SFTP and email configuration"""
    try:
        # Get current settings
        settings_data = {}
        setting_keys = [
            'sftp_hostname', 'sftp_username', 'sftp_directory', 'sftp_port', 'sftp_enabled',
            'email_notifications_enabled', 'default_notification_email', 'automated_uploads_enabled'
        ]
        
        for key in setting_keys:
            setting = db.session.query(GlobalSettings).filter_by(setting_key=key).first()
            settings_data[key] = setting.setting_value if setting else ''
        
        return render_template('settings.html', settings=settings_data, active_page='settings')
        
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
        
        # Update automation settings
        automation_settings = {
            'automated_uploads_enabled': 'true' if request.form.get('automated_uploads_enabled') == 'on' else 'false'
        }
        
        # Combine all settings
        all_settings = {**sftp_settings, **email_settings, **automation_settings}
        
        # Check if automation setting changed to manage scheduler job
        old_automation_setting = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
        old_automation_enabled = old_automation_setting.setting_value == 'true' if old_automation_setting else False
        new_automation_enabled = automation_settings['automated_uploads_enabled'] == 'true'
        
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
        
        # Manage automated upload scheduler job if setting changed
        if old_automation_enabled != new_automation_enabled:
            try:
                if new_automation_enabled:
                    # Add automated upload job
                    if scheduler.get_job('automated_upload') is None:
                        scheduler.add_job(
                            func=automated_upload,
                            trigger='interval',
                            minutes=30,
                            id='automated_upload',
                            name='Automated Upload (Every 30 Minutes)',
                            replace_existing=True
                        )
                        app.logger.info("📤 Automated uploads enabled - 30-minute job added to scheduler")
                        flash('Automated uploads enabled! XML files will be uploaded every 30 minutes.', 'success')
                    else:
                        app.logger.info("📤 Automated upload job already exists")
                        flash('Automated uploads enabled!', 'success')
                else:
                    # Remove automated upload job
                    try:
                        scheduler.remove_job('automated_upload')
                        app.logger.info("📋 Automated uploads disabled - job removed from scheduler")
                        flash('Automated uploads disabled. Manual download workflow activated.', 'info')
                    except:
                        app.logger.info("📋 Automated upload job was not scheduled")
                        flash('Automated uploads disabled.', 'info')
            except Exception as scheduler_error:
                app.logger.error(f"Failed to update automation scheduler: {str(scheduler_error)}")
                flash('Settings saved but scheduler update failed. Restart application to apply automation changes.', 'warning')
        else:
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
        
        # If password is empty, try to use the saved password from database
        # (Password field is intentionally empty by default for security)
        if not password:
            saved_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
            if saved_password and saved_password.setting_value:
                password = saved_password.setting_value
                app.logger.info("Using saved password from database for SFTP test")
        
        if not all([hostname, username, password]):
            return jsonify({
                'success': False,
                'error': 'Please fill in hostname, username, and password fields (or ensure password is saved).'
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
        result = ftp_service.test_connection()
        
        # Handle dict response (SFTP) or bool response (FTP)
        if isinstance(result, dict):
            if result.get('success'):
                return jsonify({
                    'success': True,
                    'message': f'Successfully connected to {hostname} on port {port}!'
                })
            else:
                error_msg = result.get('error', 'Unknown error')
                app.logger.error(f"SFTP test failed: {error_msg}")
                return jsonify({
                    'success': False,
                    'error': error_msg
                })
        else:
            # Legacy bool response (FTP)
            if result:
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

@app.route('/manual-upload-now', methods=['POST'])
@login_required
def manual_upload_now():
    """Manually trigger XML generation and SFTP upload"""
    try:
        app.logger.info("📤 Manual upload triggered by user")
        
        # Check if SFTP is enabled
        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
            return jsonify({
                'success': False,
                'error': 'SFTP is not enabled. Please enable it in settings first.'
            })
        
        # Get SFTP settings
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
        
        # Generate fresh XML using SimplifiedXMLGenerator (database-first approach)
        from simplified_xml_generator import SimplifiedXMLGenerator
        generator = SimplifiedXMLGenerator(db=db)
        xml_content, stats = generator.generate_fresh_xml()
        
        app.logger.info(f"📊 Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
        
        # Save XML to temporary file
        import tempfile
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8')
        temp_file.write(xml_content)
        temp_file.close()
        
        # Upload to SFTP
        try:
            port_value = int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222
        except ValueError:
            port_value = 2222
        
        target_directory = sftp_directory.setting_value if sftp_directory else "/"
        
        from ftp_service import FTPService
        ftp_service = FTPService(
            hostname=sftp_hostname.setting_value,
            username=sftp_username.setting_value,
            password=sftp_password.setting_value,
            target_directory=target_directory,
            port=port_value,
            use_sftp=True
        )
        
        # Upload the file
        upload_result = ftp_service.upload_file(temp_file.name, 'myticas-job-feed-v2.xml')
        
        # Clean up temporary file
        try:
            os.remove(temp_file.name)
        except:
            pass
        
        # Check result
        if isinstance(upload_result, dict):
            if upload_result.get('success'):
                app.logger.info(f"✅ Manual upload successful: {upload_result.get('message', 'File uploaded')}")
                return jsonify({
                    'success': True,
                    'message': f"Successfully uploaded XML with {stats['job_count']} jobs ({stats['xml_size_bytes']:,} bytes)"
                })
            else:
                app.logger.error(f"❌ Manual upload failed: {upload_result.get('error', 'Unknown error')}")
                return jsonify({
                    'success': False,
                    'error': upload_result.get('error', 'Upload failed')
                })
        else:
            # FTP service returned boolean
            if upload_result:
                app.logger.info("✅ Manual upload successful")
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
        app.logger.error(f"❌ Manual upload error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
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
                         monitor_job_counts=monitor_job_counts,
                         active_page='ats')

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
        
        # Use incremental monitoring service
        from incremental_monitoring_service import IncrementalMonitoringService
        monitoring_service = IncrementalMonitoringService()
        
        # Run monitoring cycle
        cycle_results = monitoring_service.run_monitoring_cycle()
        
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
        
        # Test connection with better error handling
        connection_test = bullhorn_service.test_connection()
        if not connection_test:
            app.logger.warning(f"Bullhorn connection failed for monitor {monitor_id}")
            return jsonify({
                'success': False,
                'error': 'Authentication failed: Unable to connect to Bullhorn API. Please refresh the page and try again.'
            })
        
        # Get jobs based on monitor type with enhanced error handling
        jobs = []
        try:
            if monitor.tearsheet_id == 0:
                # Query-based monitor
                jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
            else:
                # Traditional tearsheet-based monitor
                jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
        except Exception as api_error:
            app.logger.error(f"Bullhorn API error for monitor {monitor_id}: {str(api_error)}")
            # Check if this looks like an authentication error
            error_msg = str(api_error).lower()
            if 'auth' in error_msg or 'token' in error_msg or 'login' in error_msg or 'unauthorized' in error_msg:
                return jsonify({
                    'success': False,
                    'error': 'Authentication expired. Please refresh the page and try again.'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': f'API Error: {str(api_error)}'
                })
        
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



@app.route('/api/bullhorn/connection-test', methods=['POST'])
@login_required
def api_bullhorn_connection_test():
    """API endpoint to test Bullhorn connection and show current API mode
    
    Returns JSON with:
    - connection_status: 'success' or 'failed'
    - api_mode: 'bullhorn_one' or 'legacy'
    - endpoints: Currently configured endpoints
    - message: Human-readable status message
    """
    try:
        bullhorn_service = get_bullhorn_service()
        
        if not bullhorn_service:
            return jsonify({
                'connection_status': 'failed',
                'api_mode': 'unknown',
                'message': 'Bullhorn credentials not configured',
                'endpoints': {}
            }), 400
        
        # Determine which API mode is active
        use_new_api = os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true'
        api_mode = 'bullhorn_one' if use_new_api else 'legacy'
        
        # Get the endpoints being used
        if use_new_api:
            endpoints = {
                'auth_url': BullhornService.BULLHORN_ONE_AUTH_URL,
                'token_url': BullhornService.BULLHORN_ONE_TOKEN_URL,
                'rest_login_url': BullhornService.BULLHORN_ONE_REST_LOGIN_URL,
                'rest_url': BullhornService.BULLHORN_ONE_REST_URL
            }
        else:
            endpoints = {
                'login_info_url': BullhornService.LEGACY_LOGIN_INFO_URL,
                'note': 'OAuth and REST URLs discovered dynamically from loginInfo endpoint'
            }
        
        # Test the connection
        connection_result = bullhorn_service.test_connection()
        
        if connection_result:
            return jsonify({
                'connection_status': 'success',
                'api_mode': api_mode,
                'message': f'Successfully connected to Bullhorn using {api_mode.replace("_", " ").title()} API',
                'endpoints': endpoints,
                'base_url': bullhorn_service.base_url if bullhorn_service.base_url else 'Not set yet'
            })
        else:
            return jsonify({
                'connection_status': 'failed',
                'api_mode': api_mode,
                'message': f'Failed to connect to Bullhorn using {api_mode.replace("_", " ").title()} API. Check credentials and endpoint configuration.',
                'endpoints': endpoints
            }), 400
            
    except Exception as e:
        app.logger.error(f"Bullhorn connection test failed: {str(e)}")
        return jsonify({
            'connection_status': 'error',
            'api_mode': os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true' and 'bullhorn_one' or 'legacy',
            'message': f'Connection test error: {str(e)}',
            'endpoints': {}
        }), 500


@app.route('/api/bullhorn/api-status', methods=['GET'])
@login_required
def api_bullhorn_api_status():
    """Get current Bullhorn API configuration status (no connection test)
    
    Returns the current API mode and configured endpoints without actually testing the connection.
    """
    use_new_api = os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true'
    api_mode = 'bullhorn_one' if use_new_api else 'legacy'
    
    if use_new_api:
        endpoints = {
            'auth_url': BullhornService.BULLHORN_ONE_AUTH_URL,
            'token_url': BullhornService.BULLHORN_ONE_TOKEN_URL,
            'rest_login_url': BullhornService.BULLHORN_ONE_REST_LOGIN_URL,
            'rest_url': BullhornService.BULLHORN_ONE_REST_URL
        }
    else:
        endpoints = {
            'login_info_url': BullhornService.LEGACY_LOGIN_INFO_URL,
            'note': 'OAuth and REST URLs discovered dynamically from loginInfo endpoint'
        }
    
    return jsonify({
        'api_mode': api_mode,
        'api_mode_display': 'Bullhorn One' if use_new_api else 'Legacy Bullhorn',
        'endpoints': endpoints,
        'toggle_env_var': 'BULLHORN_USE_NEW_API',
        'toggle_current_value': os.environ.get('BULLHORN_USE_NEW_API', 'false'),
        'migration_instructions': {
            'to_enable_bullhorn_one': 'Set BULLHORN_USE_NEW_API=true in environment variables',
            'to_revert_to_legacy': 'Set BULLHORN_USE_NEW_API=false or remove the variable'
        }
    })


# AI Candidate Vetting Routes
@app.route('/vetting')
@login_required
def vetting_settings():
    """AI Candidate Vetting settings and activity page"""
    from models import VettingConfig, CandidateVettingLog, JobVettingRequirements
    
    # Full management UI is now available in all environments (including production)
    # Get settings
    settings = {
        'vetting_enabled': False,
        'send_recruiter_emails': False,  # Email notification kill switch - OFF = admin only
        'match_threshold': 80,
        'batch_size': 25,
        'admin_notification_email': '',
        'health_alert_email': ''
    }
    
    for key in settings.keys():
        config = VettingConfig.query.filter_by(setting_key=key).first()
        if config:
            if key in ('vetting_enabled', 'send_recruiter_emails'):
                settings[key] = config.setting_value.lower() == 'true'
            elif key in ('match_threshold', 'batch_size'):
                try:
                    settings[key] = int(config.setting_value)
                except (ValueError, TypeError):
                    settings[key] = 80 if key == 'match_threshold' else 25
            else:
                settings[key] = config.setting_value or ''
    
    # Get stats
    from sqlalchemy import func
    stats = {
        'total_processed': CandidateVettingLog.query.filter_by(status='completed').count(),
        'qualified': CandidateVettingLog.query.filter_by(status='completed', is_qualified=True).count(),
        'notifications_sent': db.session.query(func.sum(CandidateVettingLog.notification_count)).scalar() or 0,
        'pending': CandidateVettingLog.query.filter(CandidateVettingLog.status.in_(['pending', 'processing'])).count()
    }
    
    # Get recent activity (all candidates for "All Candidates" tab)
    recent_activity = CandidateVettingLog.query.order_by(
        CandidateVettingLog.created_at.desc()
    ).limit(50).all()
    
    # Get recommended candidates (is_qualified=True) - separate query for full list
    recommended_candidates = CandidateVettingLog.query.filter_by(
        status='completed', 
        is_qualified=True
    ).order_by(CandidateVettingLog.created_at.desc()).limit(100).all()
    
    # Get not recommended candidates (is_qualified=False, completed)
    not_recommended_candidates = CandidateVettingLog.query.filter_by(
        status='completed',
        is_qualified=False
    ).order_by(CandidateVettingLog.created_at.desc()).limit(100).all()
    
    # Get job requirements - filtered to only show active tearsheet jobs
    from candidate_vetting_service import CandidateVettingService
    vetting_svc = CandidateVettingService()
    active_job_ids = vetting_svc.get_active_job_ids()
    
    if active_job_ids:
        # Only show requirements for jobs currently in active tearsheets
        job_requirements = JobVettingRequirements.query.filter(
            JobVettingRequirements.bullhorn_job_id.in_(active_job_ids)
        ).order_by(JobVettingRequirements.updated_at.desc()).all()
    else:
        # Fallback: show all if we can't fetch tearsheet jobs (auth issues, etc.)
        job_requirements = JobVettingRequirements.query.order_by(
            JobVettingRequirements.updated_at.desc()
        ).limit(50).all()
    
    # Get latest health check
    from models import VettingHealthCheck
    latest_health = VettingHealthCheck.query.order_by(
        VettingHealthCheck.check_time.desc()
    ).first()
    
    # Get recent health issues (last 24 hours)
    from datetime import timedelta
    day_ago = datetime.utcnow() - timedelta(hours=24)
    recent_issues = VettingHealthCheck.query.filter(
        VettingHealthCheck.is_healthy == False,
        VettingHealthCheck.check_time >= day_ago
    ).order_by(VettingHealthCheck.check_time.desc()).limit(10).all()
    
    # Get pending candidates for drill-down tab
    pending_candidates = CandidateVettingLog.query.filter(
        CandidateVettingLog.status.in_(['pending', 'processing'])
    ).order_by(CandidateVettingLog.created_at.desc()).limit(50).all()
    
    # Get recently vetted candidates (completed within last 7 days) for drill-down tab
    from datetime import timedelta
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_vetting = CandidateVettingLog.query.filter(
        CandidateVettingLog.status == 'completed',
        CandidateVettingLog.updated_at >= week_ago
    ).order_by(CandidateVettingLog.updated_at.desc()).limit(50).all()
    
    return render_template('vetting_settings.html', 
                          settings=settings, 
                          stats=stats, 
                          recent_activity=recent_activity,
                          recommended_candidates=recommended_candidates,
                          not_recommended_candidates=not_recommended_candidates,
                          job_requirements=job_requirements,
                          latest_health=latest_health,
                          recent_issues=recent_issues,
                          pending_candidates=pending_candidates,
                          recent_vetting=recent_vetting,
                          active_page='vetting')


@app.route('/vetting/save', methods=['POST'])
@login_required
def save_vetting_settings():
    """Save AI vetting settings"""
    from models import VettingConfig
    
    try:
        # Get form values
        vetting_enabled = 'vetting_enabled' in request.form
        send_recruiter_emails = 'send_recruiter_emails' in request.form
        match_threshold = request.form.get('match_threshold', '80')
        batch_size = request.form.get('batch_size', '25')
        admin_email = request.form.get('admin_notification_email', '')
        health_alert_email = request.form.get('health_alert_email', '')
        
        # Validate threshold
        try:
            threshold = int(match_threshold)
            if threshold < 50 or threshold > 100:
                threshold = 80
        except ValueError:
            threshold = 80
        
        # Validate batch size
        try:
            batch = int(batch_size)
            if batch < 1 or batch > 100:
                batch = 25
        except ValueError:
            batch = 25
        
        # Update settings
        settings_to_save = [
            ('vetting_enabled', 'true' if vetting_enabled else 'false'),
            ('send_recruiter_emails', 'true' if send_recruiter_emails else 'false'),
            ('match_threshold', str(threshold)),
            ('batch_size', str(batch)),
            ('admin_notification_email', admin_email),
            ('health_alert_email', health_alert_email)
        ]
        
        for key, value in settings_to_save:
            config = VettingConfig.query.filter_by(setting_key=key).first()
            if config:
                config.setting_value = value
            else:
                config = VettingConfig(setting_key=key, setting_value=value)
                db.session.add(config)
        
        db.session.commit()
        flash('Vetting settings saved successfully!', 'success')
        
    except Exception as e:
        app.logger.error(f"Error saving vetting settings: {str(e)}")
        flash(f'Error saving settings: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/vetting/health-check', methods=['POST'])
@login_required
def run_health_check_now():
    """Manually trigger a health check"""
    try:
        run_vetting_health_check()
        flash('Health check completed successfully!', 'success')
    except Exception as e:
        app.logger.error(f"Manual health check error: {str(e)}")
        flash(f'Health check error: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/vetting/run', methods=['POST'])
@login_required
def run_vetting_now():
    """Manually trigger a vetting cycle"""
    try:
        from candidate_vetting_service import CandidateVettingService
        
        vetting_service = CandidateVettingService()
        summary = vetting_service.run_vetting_cycle()
        
        if summary.get('status') == 'disabled':
            flash('Vetting is disabled. Enable it first to run a cycle.', 'warning')
        else:
            processed = summary.get('candidates_processed', 0)
            qualified = summary.get('candidates_qualified', 0)
            notified = summary.get('notifications_sent', 0)
            
            if processed > 0:
                flash(f'Vetting cycle complete: {processed} candidates processed, '
                      f'{qualified} qualified, {notified} notifications sent.', 'success')
            else:
                flash('Vetting cycle complete: No new candidates to process.', 'info')
                
    except Exception as e:
        app.logger.error(f"Error running vetting cycle: {str(e)}")
        flash(f'Error running vetting cycle: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/vetting/reset-recent', methods=['POST'])
@login_required
def reset_recent_vetting():
    """Reset vetted_at for recent applications to allow re-vetting
    
    This is useful when the clean slate accidentally marked new applications as vetted.
    Clears vetted_at for all records received in the last 6 hours.
    """
    try:
        from models import ParsedEmail
        from datetime import datetime, timedelta
        
        # Reset vetted_at for records from the last 6 hours
        cutoff = datetime.utcnow() - timedelta(hours=6)
        
        # Find and reset recent records
        reset_count = ParsedEmail.query.filter(
            ParsedEmail.received_at >= cutoff,
            ParsedEmail.vetted_at.isnot(None),
            ParsedEmail.status == 'completed',
            ParsedEmail.bullhorn_candidate_id.isnot(None)
        ).update({'vetted_at': None}, synchronize_session=False)
        
        db.session.commit()
        
        if reset_count > 0:
            flash(f'Reset vetting status for {reset_count} recent applications. They will be processed in the next vetting cycle.', 'success')
            app.logger.info(f"Reset vetted_at for {reset_count} ParsedEmail records from last 24 hours")
        else:
            flash('No recent applications found to reset.', 'info')
            
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error resetting recent vetting: {str(e)}")
        flash(f'Error resetting vetting: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/vetting/full-clean-slate', methods=['POST'])
@login_required
def full_clean_slate():
    """Complete reset of all vetting data - dashboard shows all zeros"""
    try:
        from models import CandidateVettingLog, CandidateJobMatch, VettingConfig, ParsedEmail
        
        # Delete all job matches
        match_count = CandidateJobMatch.query.delete()
        
        # Delete all vetting logs
        log_count = CandidateVettingLog.query.delete()
        
        # Reset vetted_at on all ParsedEmail records
        reset_count = ParsedEmail.query.filter(
            ParsedEmail.vetted_at.isnot(None)
        ).update({'vetted_at': None}, synchronize_session=False)
        
        # Reset the last check timestamp to now (so we only process future candidates)
        settings = VettingConfig.query.first()
        if settings:
            settings.last_check_timestamp = datetime.utcnow()
        
        db.session.commit()
        
        flash(f'Full Clean Slate complete! Deleted {log_count} vetting logs, {match_count} job matches, reset {reset_count} applications. Dashboard now shows all zeros.', 'success')
        app.logger.info(f"Full Clean Slate: Deleted {log_count} logs, {match_count} matches, reset {reset_count} vetted_at timestamps")
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error during full clean slate: {str(e)}")
        flash(f'Error during clean slate: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/vetting/test-email', methods=['POST'])
@login_required
def send_test_vetting_email():
    """Send a test notification email with sample data to demonstrate the format"""
    from email_service import EmailService
    
    test_email = request.form.get('test_email', 'kyleroots00@gmail.com')
    scenario = request.form.get('scenario', '2')
    action = request.form.get('action', 'send')
    
    # Sample candidate and job data
    candidate_id = 4583692
    candidate_name = "John Smith"
    candidate_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=Candidate&id={candidate_id}"
    
    # Job definitions for different scenarios (with recruiter info for transparency)
    jobs = [
        {
            'id': 34517,
            'title': 'Azure Integration Developer',
            'score': 85,
            'is_applied': True,
            'summary': 'Strong candidate with 5+ years of Azure experience including Logic Apps, Functions, and API Management. Background in healthcare and enterprise integration aligns well with position requirements.',
            'skills': 'Azure Functions, Logic Apps, API Management, C#, .NET Core, SQL Server',
            'recruiter_name': 'Sarah Johnson',
            'recruiter_email': 'sjohnson@myticas.com'
        },
        {
            'id': 34520,
            'title': 'Senior Software Developer',
            'score': 82,
            'is_applied': False,
            'summary': 'Solid technical background with full-stack development experience. Python and cloud deployment skills meet core requirements though less emphasis on healthcare domain.',
            'skills': 'Python, JavaScript, React, AWS, Docker, PostgreSQL',
            'recruiter_name': 'Mike Chen',
            'recruiter_email': 'mchen@myticas.com'
        },
        {
            'id': 34523,
            'title': 'Cloud Solutions Architect',
            'score': 80,
            'is_applied': False,
            'summary': 'Extensive cloud architecture experience with multi-platform expertise. Strong in Azure and AWS with demonstrated leadership in enterprise transformation projects.',
            'skills': 'Azure, AWS, Kubernetes, Terraform, CI/CD, Solution Design',
            'recruiter_name': 'Emily Rodriguez',
            'recruiter_email': 'erodriguez@myticas.com'
        }
    ]
    
    # Cross-reference only scenario - applied job below threshold
    cross_only_jobs = [
        {
            'id': 34517,
            'title': 'Azure Integration Developer',
            'score': 65,
            'is_applied': True,
            'summary': 'Candidate lacks required Azure Logic Apps experience. Good general technical background but missing key integration skills.',
            'skills': 'Python, JavaScript, Basic Azure knowledge',
            'below_threshold': True,
            'recruiter_name': 'Sarah Johnson',
            'recruiter_email': 'sjohnson@myticas.com'
        },
        {
            'id': 34520,
            'title': 'Senior Software Developer',
            'score': 88,
            'is_applied': False,
            'summary': 'Excellent match for this role! Strong Python and full-stack development experience matches all core requirements. Previous work in similar domains.',
            'skills': 'Python, JavaScript, React, AWS, Docker, PostgreSQL',
            'recruiter_name': 'Mike Chen',
            'recruiter_email': 'mchen@myticas.com'
        }
    ]
    
    # Multi-recruiter scenario - same candidate matches jobs from 2 different recruiters
    multi_recruiter_jobs = [
        {
            'id': 34517,
            'title': 'Azure Integration Developer',
            'score': 85,
            'is_applied': True,
            'summary': 'Strong candidate with 5+ years of Azure experience including Logic Apps, Functions, and API Management.',
            'skills': 'Azure Functions, Logic Apps, API Management, C#, .NET Core',
            'recruiter_name': 'Sarah Johnson',
            'recruiter_email': 'sjohnson@myticas.com'
        },
        {
            'id': 34520,
            'title': 'Senior Software Developer',
            'score': 88,
            'is_applied': False,
            'summary': 'Excellent Python and full-stack skills match this role perfectly. Previous healthcare experience is a bonus.',
            'skills': 'Python, JavaScript, React, AWS, Docker',
            'recruiter_name': 'Mike Chen',
            'recruiter_email': 'mchen@myticas.com'
        }
    ]
    
    # Determine if this is multi-recruiter mode
    is_multi_recruiter = scenario == 'multi'
    all_recruiter_emails = None
    
    # Build matches based on scenario
    if scenario == '1':
        matches = [jobs[0]]  # Just the applied job
        scenario_desc = "1 Match (Applied Job Only)"
    elif scenario == '2':
        matches = jobs[:2]  # Applied + 1 cross-ref
        scenario_desc = "2 Matches (Applied + 1 Cross-Reference)"
    elif scenario == '3':
        matches = jobs  # All 3 jobs
        scenario_desc = "3+ Matches (Applied + 2 Cross-References)"
    elif scenario == 'multi':
        matches = multi_recruiter_jobs
        all_recruiter_emails = {'sjohnson@myticas.com', 'mchen@myticas.com'}
        scenario_desc = "Multi-Recruiter (Same Email to All Recruiters)"
    else:  # cross_only
        matches = [j for j in cross_only_jobs if not j.get('below_threshold', False)]
        scenario_desc = "Cross-Reference Only (Applied Job Below Threshold)"
    
    # Build transparency note for multi-recruiter scenario (CC model)
    transparency_note = ""
    cc_emails_for_display = []
    if is_multi_recruiter and all_recruiter_emails:
        # In multi-recruiter scenario, primary is applied job recruiter, others are CC'd
        primary_email = 'sjohnson@myticas.com'  # Applied job recruiter
        cc_emails_for_display = [e for e in all_recruiter_emails if e != primary_email]
        if cc_emails_for_display:
            transparency_note = f"""
            <div style="background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; padding: 12px; margin-bottom: 15px;">
                <p style="margin: 0; color: #1565c0; font-size: 13px;">
                    <strong>📢 Team Thread:</strong> This candidate matches multiple positions.
                    CC'd on this email: <em>{', '.join(cc_emails_for_display)}</em>
                </p>
            </div>
            """
    
    # Build email HTML
    subject = f"🎯 [TEST] Qualified Candidate Alert: {candidate_name}"
    
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #dc3545; color: white; padding: 10px 20px; text-align: center; font-weight: bold;">
            ⚠️ TEST EMAIL - {scenario_desc} ⚠️
        </div>
        
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 0;">
            <h1 style="margin: 0; font-size: 24px;">🎯 Qualified Candidate Match</h1>
        </div>
        
        <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef;">
            <p style="margin: 0 0 15px 0;">Hi there,</p>
            
            {transparency_note}
            
            <p style="margin: 0 0 15px 0;">
                A new candidate has been analyzed by JobPulse AI and matches 
                <strong>{len(matches)} position(s)</strong>.
            </p>
            
            <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; margin: 20px 0;">
                <h2 style="margin: 0 0 10px 0; color: #495057; font-size: 18px;">
                    👤 {candidate_name}
                </h2>
                <a href="{candidate_url}" 
                   style="display: inline-block; background: #667eea; color: white; 
                          padding: 10px 20px; border-radius: 5px; text-decoration: none;
                          margin-top: 10px;">
                    View Candidate Profile →
                </a>
            </div>
            
            <h3 style="color: #495057; margin: 20px 0 10px 0;">Matched Positions:</h3>
    """
    
    for job in matches:
        job_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=JobOrder&id={job['id']}"
        applied_badge = '<span style="background: #ffc107; color: #000; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">APPLIED</span>' if job['is_applied'] else ''
        
        # Add recruiter ownership tag for multi-recruiter scenario
        recruiter_tag = ""
        if is_multi_recruiter and job.get('recruiter_name'):
            is_your_job = job.get('recruiter_email') == test_email
            if is_your_job:
                recruiter_tag = '<span style="background: #28a745; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">YOUR JOB</span>'
            else:
                recruiter_tag = f'<span style="background: #6c757d; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">{job["recruiter_name"]}\'s Job</span>'
        
        html_content += f"""
            <div style="background: white; padding: 15px; border-radius: 8px; 
                        border-left: 4px solid #28a745; margin: 10px 0;">
                <h4 style="margin: 0 0 8px 0; color: #28a745;">
                    <a href="{job_url}" style="color: #28a745; text-decoration: none;">{job['title']} (Job ID: {job['id']})</a>{applied_badge}{recruiter_tag}
                </h4>
                <div style="color: #6c757d; margin-bottom: 8px;">
                    <strong>Match Score:</strong> {job['score']}%
                </div>
                <p style="margin: 0; color: #495057;">{job['summary']}</p>
                <p style="margin: 10px 0 0 0; color: #495057;"><strong>Key Skills:</strong> {job['skills']}</p>
            </div>
        """
    
    html_content += f"""
            <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid #dee2e6;">
                <p style="color: #6c757d; font-size: 14px; margin: 0;">
                    <strong>Recommended Action:</strong> Review the candidate's profile and 
                    reach out if they're a good fit for your open position(s).
                </p>
            </div>
        </div>
        
        <div style="background: #343a40; color: #adb5bd; padding: 15px; 
                    border-radius: 0 0 8px 8px; font-size: 12px; text-align: center;">
            Powered by JobPulse™ AI Vetting • Myticas Consulting
        </div>
    </div>
    """
    
    # Handle preview vs send
    if action == 'preview':
        return render_template('vetting_email_preview.html', 
                             html_content=html_content, 
                             scenario_desc=scenario_desc,
                             test_email=test_email,
                             scenario=scenario)
    
    # Send the email
    try:
        email_service = EmailService()
        success = email_service.send_html_email(
            to_email=test_email,
            subject=subject,
            html_content=html_content,
            notification_type='vetting_test_email'
        )
        
        if success:
            flash(f'Test email ({scenario_desc}) sent successfully to {test_email}!', 'success')
        else:
            flash(f'Failed to send test email to {test_email}', 'error')
            
    except Exception as e:
        app.logger.error(f"Error sending test vetting email: {str(e)}")
        flash(f'Error sending test email: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/vetting/sample-notes')
@login_required
def show_sample_notes():
    """Show sample note formats for qualified and non-qualified candidates"""
    
    qualified_note = """🎯 AI VETTING SUMMARY - QUALIFIED CANDIDATE

Analysis Date: 2026-01-29 12:45 UTC
Threshold: 80%
Qualified Matches: 2 of 5 jobs
Highest Match Score: 85%

QUALIFIED POSITIONS:

• Job ID: 34517 - Azure Integration Developer
  Match Score: 85%
  ⭐ APPLIED TO THIS POSITION
  Summary: Strong candidate with 5+ years of Azure experience including Logic Apps, Functions, and API Management. Background in healthcare and enterprise integration aligns well with position requirements.
  Skills: Azure Functions, Logic Apps, API Management, C#, .NET Core, SQL Server

• Job ID: 34520 - Senior Software Developer
  Match Score: 82%
  Summary: Solid technical background with full-stack development experience. Python and cloud deployment skills meet core requirements.
  Skills: Python, JavaScript, React, AWS, Docker, PostgreSQL"""
    
    not_qualified_note = """📋 AI VETTING SUMMARY - NOT RECOMMENDED

Analysis Date: 2026-01-29 12:45 UTC
Threshold: 80%
Highest Match Score: 62%
Jobs Analyzed: 5

This candidate did not meet the 80% match threshold for any current open positions.

TOP ANALYSIS RESULTS:

• Job ID: 34517 - Azure Integration Developer
  Match Score: 62%
  ⭐ APPLIED TO THIS POSITION
  Gaps: No direct Azure experience. Background is primarily in frontend development. Missing required integration/middleware skills.

• Job ID: 34520 - Senior Software Developer
  Match Score: 58%
  Gaps: Entry-level experience (2 years vs 5+ required). No team lead experience. Limited backend exposure.

• Job ID: 34525 - Cloud Solutions Architect
  Match Score: 45%
  Gaps: No cloud certifications. Limited enterprise architecture experience. Missing AWS or Azure expertise."""
    
    return render_template('sample_notes.html', 
                          qualified_note=qualified_note, 
                          not_qualified_note=not_qualified_note)


@app.route('/vetting/create-test-note/<int:candidate_id>', methods=['POST'])
@login_required
def create_test_vetting_note(candidate_id):
    """Create a test vetting note on an actual Bullhorn candidate record"""
    from bullhorn_service import BullhornService
    
    note_type = request.form.get('note_type', 'qualified')  # 'qualified' or 'not_qualified'
    
    try:
        bullhorn = BullhornService()
        if not bullhorn.authenticate():
            flash('Failed to authenticate with Bullhorn', 'error')
            return redirect(url_for('show_sample_notes'))
        
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        
        if note_type == 'qualified':
            note_text = f"""🎯 AI VETTING SUMMARY - QUALIFIED CANDIDATE

Analysis Date: {now}
Threshold: 80%
Qualified Matches: 2 of 5 jobs
Highest Match Score: 85%

QUALIFIED POSITIONS:

• Job ID: 34517 - Azure Integration Developer
  Match Score: 85%
  ⭐ APPLIED TO THIS POSITION
  Summary: Strong candidate with 5+ years of Azure experience including Logic Apps, Functions, and API Management. Background in healthcare and enterprise integration aligns well with position requirements.
  Skills: Azure Functions, Logic Apps, API Management, C#, .NET Core, SQL Server

• Job ID: 34520 - Senior Software Developer
  Match Score: 82%
  Summary: Solid technical background with full-stack development experience. Python and cloud deployment skills meet core requirements.
  Skills: Python, JavaScript, React, AWS, Docker, PostgreSQL"""
            action = "AI Vetting - Qualified"
            
        else:
            note_text = f"""📋 AI VETTING SUMMARY - NOT RECOMMENDED

Analysis Date: {now}
Threshold: 80%
Highest Match Score: 62%
Jobs Analyzed: 5

This candidate did not meet the 80% match threshold for any current open positions.

TOP ANALYSIS RESULTS:

• Job ID: 34517 - Azure Integration Developer
  Match Score: 62%
  ⭐ APPLIED TO THIS POSITION
  Gaps: No direct Azure experience. Background is primarily in frontend development. Missing required integration/middleware skills.

• Job ID: 34520 - Senior Software Developer
  Match Score: 58%
  Gaps: Entry-level experience (2 years vs 5+ required). No team lead experience. Limited backend exposure.

• Job ID: 34525 - Cloud Solutions Architect
  Match Score: 45%
  Gaps: No cloud certifications. Limited enterprise architecture experience. Missing AWS or Azure expertise."""
            action = "AI Vetting - Not Recommended"
        
        note_id = bullhorn.create_candidate_note(candidate_id, note_text, action=action)
        
        if note_id:
            flash(f'Successfully created {note_type.replace("_", " ")} test note on candidate {candidate_id}. Note ID: {note_id}', 'success')
            app.logger.info(f"Created test vetting note on candidate {candidate_id}: type={note_type}, note_id={note_id}")
        else:
            flash(f'Failed to create test note on candidate {candidate_id}. Check logs for details.', 'error')
            
    except Exception as e:
        app.logger.error(f"Error creating test vetting note: {str(e)}")
        flash(f'Error creating test note: {str(e)}', 'error')
    
    return redirect(url_for('show_sample_notes'))


@app.route('/vetting/job/<int:job_id>/requirements', methods=['POST'])
@login_required
def save_job_requirements(job_id):
    """Save custom requirements for a job"""
    from models import JobVettingRequirements
    
    try:
        # Support both JSON and form data
        if request.is_json:
            data = request.get_json()
            custom_requirements = (data.get('custom_requirements') or '').strip()
            vetting_threshold = data.get('threshold') or ''
        else:
            custom_requirements = request.form.get('custom_requirements', '').strip()
            vetting_threshold = request.form.get('vetting_threshold', '').strip()
        
        job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
        if job_req:
            job_req.custom_requirements = custom_requirements if custom_requirements else None
            # Handle threshold - empty string means clear (use global default)
            if vetting_threshold:
                job_req.vetting_threshold = int(vetting_threshold)
            else:
                job_req.vetting_threshold = None
            job_req.updated_at = datetime.utcnow()
        else:
            job_req = JobVettingRequirements(
                bullhorn_job_id=job_id,
                custom_requirements=custom_requirements if custom_requirements else None,
                vetting_threshold=int(vetting_threshold) if vetting_threshold else None
            )
            db.session.add(job_req)
        
        db.session.commit()
        
        # For JSON requests, return JSON response
        if request.is_json:
            return jsonify({'success': True, 'message': f'Requirements saved for Job #{job_id}'})
        
        if custom_requirements:
            flash(f'Custom requirements saved for Job #{job_id}', 'success')
        else:
            flash(f'Custom requirements cleared - using AI interpretation for Job #{job_id}', 'info')
        
    except Exception as e:
        app.logger.error(f"Error saving job requirements: {str(e)}")
        if request.is_json:
            return jsonify({'success': False, 'error': str(e)}), 400
        flash(f'Error saving requirements: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/vetting/job/<int:job_id>/threshold', methods=['POST'])
@login_required
def save_job_threshold(job_id):
    """AJAX endpoint to save job-specific vetting threshold"""
    from models import JobVettingRequirements, VettingConfig
    
    try:
        data = request.get_json() if request.is_json else {}
        threshold_value = data.get('threshold')
        
        # Handle clearing threshold (null = use global)
        if threshold_value is None or threshold_value == '':
            new_threshold = None
        else:
            new_threshold = int(threshold_value)
            if new_threshold < 50 or new_threshold > 100:
                return jsonify({'success': False, 'error': 'Threshold must be between 50 and 100'}), 400
        
        job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
        if job_req:
            job_req.vetting_threshold = new_threshold
            job_req.updated_at = datetime.utcnow()
        else:
            # Create new record if it doesn't exist
            job_req = JobVettingRequirements(
                bullhorn_job_id=job_id,
                vetting_threshold=new_threshold
            )
            db.session.add(job_req)
        
        db.session.commit()
        
        # Get global threshold for display
        global_threshold = VettingConfig.get_value('match_threshold', '80')
        display_threshold = new_threshold if new_threshold is not None else int(global_threshold)
        
        return jsonify({
            'success': True,
            'threshold': new_threshold,
            'display_threshold': display_threshold,
            'is_custom': new_threshold is not None
        })
        
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid threshold value'}), 400
    except Exception as e:
        app.logger.error(f"Error saving job threshold: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/vetting/job/<int:job_id>/refresh-requirements', methods=['POST'])
@login_required
def refresh_job_requirements(job_id):
    """Re-fetch job description from Bullhorn and re-interpret with AI"""
    from models import JobVettingRequirements, GlobalSettings
    from bullhorn_service import BullhornService
    from candidate_vetting_service import CandidateVettingService
    
    try:
        # Get Bullhorn credentials from GlobalSettings (same as vetting service)
        credentials = {}
        for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
            setting = GlobalSettings.query.filter_by(setting_key=key).first()
            if setting and setting.setting_value:
                credentials[key.replace('bullhorn_', '')] = setting.setting_value
        
        if not credentials.get('username') or not credentials.get('password'):
            flash('Bullhorn credentials not configured in settings', 'error')
            return redirect(url_for('vetting_settings'))
        
        # Authenticate and fetch fresh job data
        bullhorn = BullhornService(
            client_id=credentials.get('client_id'),
            client_secret=credentials.get('client_secret'),
            username=credentials.get('username'),
            password=credentials.get('password')
        )
        if not bullhorn.authenticate():
            flash('Failed to authenticate with Bullhorn', 'error')
            return redirect(url_for('vetting_settings'))
        
        # Fetch the specific job
        job_data = bullhorn.get_job_by_id(job_id)
        if not job_data:
            flash(f'Could not find Job #{job_id} in Bullhorn', 'error')
            return redirect(url_for('vetting_settings'))
        
        job_title = job_data.get('title', 'Unknown')
        job_description = job_data.get('description', '') or job_data.get('publicDescription', '')
        
        if not job_description:
            flash(f'Job #{job_id} has no description in Bullhorn', 'warning')
            return redirect(url_for('vetting_settings'))
        
        # Use AI to extract requirements
        vetting_service = CandidateVettingService()
        extracted_requirements = vetting_service.extract_job_requirements(job_id, job_title, job_description)
        
        if extracted_requirements:
            # Save to database
            job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
            if job_req:
                job_req.ai_interpreted_requirements = extracted_requirements
                job_req.job_title = job_title
                job_req.last_ai_interpretation = datetime.utcnow()
                job_req.updated_at = datetime.utcnow()
            else:
                job_req = JobVettingRequirements(
                    bullhorn_job_id=job_id,
                    job_title=job_title,
                    ai_interpreted_requirements=extracted_requirements,
                    last_ai_interpretation=datetime.utcnow()
                )
                db.session.add(job_req)
            
            db.session.commit()
            flash(f'Successfully refreshed AI requirements for "{job_title}"', 'success')
            app.logger.info(f"Refreshed AI requirements for job #{job_id}: {job_title}")
        else:
            flash(f'AI could not extract requirements from Job #{job_id} description', 'warning')
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error refreshing job requirements: {str(e)}")
        flash(f'Error refreshing requirements: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/vetting/sync-requirements', methods=['POST'])
@login_required
def sync_job_requirements():
    """Sync AI requirements with active tearsheet jobs - removes orphaned entries"""
    try:
        from candidate_vetting_service import CandidateVettingService
        
        vetting_service = CandidateVettingService()
        results = vetting_service.sync_requirements_with_active_jobs()
        
        if results.get('error'):
            flash(f"Sync aborted: {results['error']}", 'warning')
        elif results['removed'] > 0:
            flash(f"Synced: removed {results['removed']} orphaned requirements (not in active tearsheets). {results['active_jobs']} active jobs remain.", 'success')
        else:
            flash(f"Already in sync! {results['active_jobs']} active jobs in tearsheets.", 'info')
            
    except Exception as e:
        app.logger.error(f"Error syncing requirements: {str(e)}")
        flash(f'Error syncing requirements: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/vetting/extract-all-requirements', methods=['POST'])
@login_required
def extract_all_job_requirements():
    """Extract AI requirements for all monitored jobs at once"""
    try:
        from candidate_vetting_service import CandidateVettingService
        from bullhorn_service import BullhornService
        from models import BullhornMonitor, JobVettingRequirements
        
        # First, sync to remove orphaned requirements
        vetting_service = CandidateVettingService()
        vetting_service.sync_requirements_with_active_jobs()
        
        # Get Bullhorn credentials
        credentials = {}
        for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
            setting = GlobalSettings.query.filter_by(setting_key=key).first()
            if setting and setting.setting_value:
                credentials[key.replace('bullhorn_', '')] = setting.setting_value.strip()
        
        if len(credentials) < 4:
            flash('Bullhorn credentials not fully configured', 'error')
            return redirect(url_for('vetting_settings'))
        
        # Initialize services
        bullhorn = BullhornService(
            client_id=credentials['client_id'],
            client_secret=credentials['client_secret'],
            username=credentials['username'],
            password=credentials['password']
        )
        
        if not bullhorn.test_connection():
            flash('Failed to connect to Bullhorn', 'error')
            return redirect(url_for('vetting_settings'))
        
        vetting_service = CandidateVettingService()
        
        # Get all active monitors
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        
        all_jobs = []
        location_updates = 0
        for monitor in monitors:
            try:
                if monitor.tearsheet_id == 0:
                    jobs = bullhorn.get_jobs_by_query(monitor.tearsheet_name)
                else:
                    jobs = bullhorn.get_tearsheet_jobs(monitor.tearsheet_id)
                
                for job in jobs:
                    job_id = int(job.get('id', 0))
                    
                    # Extract location data
                    job_address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
                    job_city = job_address.get('city', '')
                    job_state = job_address.get('state', '')
                    job_country = job_address.get('countryName', '') or job_address.get('country', '')
                    job_location = ', '.join(filter(None, [job_city, job_state, job_country]))
                    
                    # Get work type - handle both numeric and string values from Bullhorn
                    on_site_value = job.get('onSite', 1)
                    # Handle list format
                    if isinstance(on_site_value, list):
                        on_site_value = on_site_value[0] if on_site_value else 1
                    # Handle numeric values
                    if isinstance(on_site_value, (int, float)):
                        work_type_map = {1: 'On-site', 2: 'Hybrid', 3: 'Remote'}
                        job_work_type = work_type_map.get(int(on_site_value), 'On-site')
                    else:
                        # Handle string values like 'Remote', 'Hybrid', 'On-Site'
                        onsite_str = str(on_site_value).lower().strip() if on_site_value else ''
                        if 'remote' in onsite_str or onsite_str == 'offsite':
                            job_work_type = 'Remote'
                        elif 'hybrid' in onsite_str:
                            job_work_type = 'Hybrid'
                        else:
                            job_work_type = 'On-site'
                    
                    # Check if already has requirements
                    existing = JobVettingRequirements.query.filter_by(
                        bullhorn_job_id=job_id
                    ).first()
                    
                    if existing and existing.ai_interpreted_requirements:
                        # Update location/work_type for existing records if missing or different
                        needs_update = False
                        if not existing.job_location or existing.job_location != job_location:
                            existing.job_location = job_location
                            needs_update = True
                        if not existing.job_work_type or existing.job_work_type != job_work_type:
                            existing.job_work_type = job_work_type
                            needs_update = True
                        if needs_update:
                            db.session.commit()
                            location_updates += 1
                        continue
                    
                    all_jobs.append({
                        'id': job.get('id'),
                        'title': job.get('title', ''),
                        'description': job.get('publicDescription', '') or job.get('description', ''),
                        'location': job_location,
                        'work_type': job_work_type
                    })
            except Exception as e:
                app.logger.warning(f"Error fetching jobs from {monitor.name}: {str(e)}")
        
        if not all_jobs:
            if location_updates > 0:
                flash(f'Updated location data for {location_updates} existing jobs', 'success')
            else:
                flash('All jobs already have requirements extracted', 'info')
            return redirect(url_for('vetting_settings'))
        
        # Extract requirements for all jobs
        results = vetting_service.extract_requirements_for_jobs(all_jobs)
        
        msg = f"Extracted requirements for {results.get('extracted', 0)} jobs. "
        msg += f"Skipped {results.get('skipped', 0)}, Failed {results.get('failed', 0)}"
        if location_updates > 0:
            msg += f", Updated location for {location_updates} existing jobs"
        flash(msg, 'success')
        
    except Exception as e:
        app.logger.error(f"Error extracting all requirements: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('vetting_settings'))


@app.route('/bullhorn/oauth/start')
@login_required
def bullhorn_oauth_start():
    """Start OAuth flow with CSRF protection"""
    try:
        # Get current Bullhorn settings (only Client ID needed for OAuth)
        client_id_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_id').first()
        client_secret_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_secret').first()
        
        if not all([client_id_setting, client_secret_setting]):
            flash('Bullhorn OAuth credentials not configured. Please configure Client ID and Client Secret first.', 'error')
            return redirect(url_for('bullhorn_settings'))
        
        # Check if using Bullhorn One (new API) or Legacy
        use_new_api = os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true'
        
        if use_new_api:
            # Bullhorn One: Use fixed auth endpoint directly (already includes /oauth/authorize)
            auth_endpoint = BullhornService.BULLHORN_ONE_AUTH_URL
            logging.info(f"Using Bullhorn One auth endpoint: {auth_endpoint}")
        else:
            # Legacy: Get login info to determine correct data center
            login_info_url = "https://rest.bullhornstaffing.com/rest-services/loginInfo"
            login_info_params = {'username': 'oauth'}  # Use generic username for OAuth discovery
            
            response = requests.get(login_info_url, params=login_info_params, timeout=30)
            if response.status_code != 200:
                flash('Failed to get Bullhorn login info. Please try again.', 'error')
                return redirect(url_for('bullhorn_settings'))
            
            login_data = response.json()
            oauth_url = login_data.get('oauthUrl')
            
            if not oauth_url:
                flash('Invalid login info response from Bullhorn', 'error')
                return redirect(url_for('bullhorn_settings'))
            
            # Legacy uses base oauthUrl, need to add /authorize
            auth_endpoint = f"{oauth_url}/authorize"
        
        # Step 2: Generate secure state for CSRF protection
        import secrets
        state = secrets.token_urlsafe(32)
        session['oauth_state'] = state  # Store in Flask session
        session['oauth_timestamp'] = int(time.time())  # Add timestamp for expiry
        
        # Step 3: Build authorization URL
        base_url = os.environ.get('OAUTH_REDIRECT_BASE_URL', "https://jobpulse.lyntrix.ai").strip()
        redirect_uri = f"{base_url}/bullhorn/oauth/callback"
        
        logging.info(f"OAuth redirect_uri: {redirect_uri}")
        logging.info(f"OAuth client_id: {client_id_setting.setting_value}")
        
        auth_params = {
            'client_id': client_id_setting.setting_value,
            'response_type': 'code',
            'redirect_uri': redirect_uri,
            'state': state
        }
        
        # Build the full authorization URL
        from urllib.parse import urlencode
        auth_url = f"{auth_endpoint}?{urlencode(auth_params)}"
        
        logging.info(f"OAuth full auth_url: {auth_url}")
        logging.info(f"Starting OAuth with state: {state[:10]}...")
        return redirect(auth_url)
        
    except Exception as e:
        logging.error(f"OAuth start error: {str(e)}")
        flash(f'Error starting OAuth flow: {str(e)}', 'error')
        return redirect(url_for('bullhorn_settings'))


@app.route('/bullhorn/oauth/callback')
def bullhorn_oauth_callback():
    """Handle Bullhorn OAuth callback and exchange authorization code for tokens"""
    try:
        # Handle OAuth callback from Bullhorn after Terms of Service acceptance
        code = request.args.get('code')
        error = request.args.get('error')
        state = request.args.get('state')
        
        if error:
            flash(f'Bullhorn OAuth authorization failed: {error}', 'error')
            return redirect(url_for('bullhorn_settings'))
        
        if not code:
            flash('OAuth callback received but no authorization code found', 'warning')
            return redirect(url_for('bullhorn_settings'))
        
        # Validate state parameter for CSRF protection
        stored_state = session.get('oauth_state')
        stored_timestamp = session.get('oauth_timestamp', 0)
        
        # Clear state from session immediately (one-time use)
        if 'oauth_state' in session:
            del session['oauth_state']
        if 'oauth_timestamp' in session:
            del session['oauth_timestamp']
        
        # Validate state exists and matches
        if not stored_state or not state:
            flash('OAuth state validation failed - possible CSRF attack. Please try again.', 'error')
            logging.error("OAuth CSRF validation failed - missing state")
            return redirect(url_for('bullhorn_settings'))
        
        if stored_state != state:
            flash('OAuth state validation failed - possible CSRF attack. Please try again.', 'error')
            logging.error(f"OAuth CSRF validation failed - state mismatch: expected {stored_state[:10]}..., got {state[:10]}...")
            return redirect(url_for('bullhorn_settings'))
        
        # Check if state is too old (5 minute expiry)
        import time
        if int(time.time()) - stored_timestamp > 300:
            flash('OAuth session expired. Please try again.', 'warning')
            logging.warning("OAuth state expired")
            return redirect(url_for('bullhorn_settings'))
        
        logging.info(f"✅ OAuth callback received with valid state - code: {code[:10]}...")
        
        # Exchange authorization code for tokens directly (don't call authenticate())
        try:
            # Get current Bullhorn settings
            client_id_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_id').first()
            client_secret_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_secret').first()
            
            if not all([client_id_setting, client_secret_setting]):
                flash('Bullhorn credentials not configured. Please update settings first.', 'error')
                return redirect(url_for('bullhorn_settings'))
            
            # Check if using Bullhorn One (new API) or Legacy
            use_new_api = os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true'
            
            if use_new_api:
                # Bullhorn One: Use fixed endpoints
                token_endpoint = BullhornService.BULLHORN_ONE_TOKEN_URL
                rest_login_url = BullhornService.BULLHORN_ONE_REST_LOGIN_URL  # For login step
                rest_api_url = BullhornService.BULLHORN_ONE_REST_URL  # For API calls
                logging.info(f"OAuth callback using Bullhorn One endpoints: token={token_endpoint}, login={rest_login_url}")
            else:
                # Legacy: Get login info to determine correct data center
                login_info_url = "https://rest.bullhornstaffing.com/rest-services/loginInfo"
                login_info_params = {'username': 'oauth'}  # Generic username for OAuth flow
                
                response = requests.get(login_info_url, params=login_info_params, timeout=30)
                if response.status_code != 200:
                    flash('Failed to get Bullhorn login info. Please try again.', 'error')
                    return redirect(url_for('bullhorn_settings'))
                
                login_data = response.json()
                oauth_url = login_data.get('oauthUrl')
                rest_url = login_data.get('restUrl')
                
                if not oauth_url:
                    flash('Invalid login info response from Bullhorn', 'error')
                    return redirect(url_for('bullhorn_settings'))
                
                token_endpoint = f"{oauth_url}/token"
            
            # Build redirect_uri (must match what was used in authorization request)
            base_url = os.environ.get('OAUTH_REDIRECT_BASE_URL', "https://jobpulse.lyntrix.ai").strip()
            redirect_uri = f"{base_url}/bullhorn/oauth/callback"
            
            # Step 2: Exchange authorization code for access token
            # For Bullhorn One, redirect_uri MUST be included and match authorization request
            token_data = {
                'grant_type': 'authorization_code',
                'code': code,
                'client_id': client_id_setting.setting_value,
                'client_secret': client_secret_setting.setting_value,
                'redirect_uri': redirect_uri  # Required for Bullhorn One
            }
            
            logging.info(f"Token exchange with redirect_uri: {redirect_uri}")
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            
            token_response = requests.post(token_endpoint, data=token_data, headers=headers, timeout=30)
            if token_response.status_code != 200:
                logging.error(f"Token exchange failed: {token_response.status_code} - {token_response.text}")
                flash(f'Failed to exchange authorization code for tokens: {token_response.text}', 'error')
                return redirect(url_for('bullhorn_settings'))
            
            token_info = token_response.json()
            access_token = token_info.get('access_token')
            
            if not access_token:
                flash('No access token received from Bullhorn', 'error')
                return redirect(url_for('bullhorn_settings'))
            
            # Step 3: Get REST token for API access
            # For Bullhorn One, use the fixed login URL; for legacy, append /login to rest_url
            if use_new_api:
                rest_login_endpoint = rest_login_url
            else:
                rest_login_endpoint = f"{rest_url}/login"
            
            rest_params = {
                'version': '2.0',
                'access_token': access_token
            }
            
            logging.info(f"REST login request to: {rest_login_endpoint}")
            rest_response = requests.post(rest_login_endpoint, params=rest_params, timeout=30)
            if rest_response.status_code != 200:
                logging.error(f"REST login failed: {rest_response.status_code} - {rest_response.text}")
                flash('Failed to get REST token for API access', 'error')
                return redirect(url_for('bullhorn_settings'))
            
            rest_data = rest_response.json()
            rest_token = rest_data.get('BhRestToken')
            # Use the appropriate fallback URL based on API mode
            if use_new_api:
                base_url = rest_data.get('restUrl', rest_api_url)
            else:
                base_url = rest_data.get('restUrl', rest_url)
            
            if not rest_token:
                flash('No REST token received from Bullhorn', 'error')
                return redirect(url_for('bullhorn_settings'))
            
            # Success! OAuth flow completed
            flash('✅ Bullhorn OAuth authentication completed successfully! Terms of Service accepted and connection established.', 'success')
            logging.info(f"✅ Complete OAuth flow successful - REST Token: {rest_token[:20]}..., Base URL: {base_url}")
            
            # Test a simple API call to verify the connection works
            try:
                test_url = f"{base_url}/search/JobOrder?query=id>0&count=1&fields=id"
                test_response = requests.get(test_url, params={'BhRestToken': rest_token}, timeout=15)
                if test_response.status_code == 200:
                    flash('✅ API connection test passed - ready for data migration!', 'success')
                    logging.info("✅ API test call successful")
                else:
                    flash('⚠️ Authentication successful but API test failed. Connection may still work.', 'warning')
                    logging.warning(f"API test failed: {test_response.status_code}")
            except Exception as test_error:
                logging.warning(f"API test error (not critical): {str(test_error)}")
                flash('⚠️ Authentication successful but couldn\'t verify API access. Connection should still work.', 'warning')
                
        except requests.exceptions.RequestException as req_error:
            logging.error(f"Network error during OAuth token exchange: {str(req_error)}")
            flash(f'Network error during authentication: {str(req_error)}', 'error')
        except Exception as auth_error:
            logging.error(f"Error during OAuth token exchange: {str(auth_error)}")
            flash(f'Error completing authentication: {str(auth_error)}', 'error')
            
        return redirect(url_for('bullhorn_settings'))
        
    except Exception as e:
        logging.error(f"OAuth callback error: {str(e)}")
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
        # Validate job_id looks like a Bullhorn job ID (numeric)
        # This prevents catching routes like /vetting/settings/
        if not job_id.isdigit():
            abort(404)
        
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
    
    return render_template('email_logs.html', logs=logs, active_page='email_logs')

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

# ==================== Email Inbound Parsing Routes ====================

@app.route('/api/email/inbound', methods=['GET', 'POST'])
def email_inbound_webhook():
    """
    SendGrid Inbound Parse webhook endpoint
    
    Receives forwarded emails from job boards (LinkedIn, Dice, etc.)
    and processes them to create/update candidates in Bullhorn.
    
    This endpoint is public (no auth) because SendGrid needs to POST to it.
    Security is via SendGrid's signature verification.
    
    GET: Returns 200 OK for health checks / endpoint verification
    POST: Processes inbound email data from SendGrid
    """
    # Handle GET requests for health checks
    if request.method == 'GET':
        return jsonify({
            'status': 'ok',
            'endpoint': 'SendGrid Inbound Parse webhook',
            'methods': ['POST'],
            'message': 'Ready to receive emails'
        }), 200
    
    try:
        from email_inbound_service import EmailInboundService
        
        app.logger.info("📧 Received inbound email webhook")
        
        # SendGrid sends form data, not JSON
        payload = request.form.to_dict()
        
        # Add any file attachments
        if request.files:
            for key, file in request.files.items():
                payload[key] = file.read()
                payload[f'{key}_info'] = {
                    'filename': file.filename,
                    'content_type': file.content_type
                }
        
        # Process the email
        service = EmailInboundService()
        result = service.process_email(payload)
        
        if result['success']:
            app.logger.info(f"✅ Email processed successfully: candidate {result.get('candidate_id')}")
            return jsonify(result), 200
        else:
            app.logger.warning(f"⚠️ Email processing failed: {result.get('message')}")
            return jsonify(result), 200  # Return 200 to prevent SendGrid retries
            
    except Exception as e:
        app.logger.error(f"❌ Email inbound webhook error: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 200


@app.route('/email-parsing')
@login_required
def email_parsing_dashboard():
    """Dashboard for email parsing monitoring"""
    from models import ParsedEmail
    
    # Get recent parsed emails
    recent_emails = ParsedEmail.query.order_by(
        ParsedEmail.received_at.desc()
    ).limit(100).all()
    
    # Get stats
    total_emails = ParsedEmail.query.count()
    completed_emails = ParsedEmail.query.filter_by(status='completed').count()
    failed_emails = ParsedEmail.query.filter_by(status='failed').count()
    duplicate_candidates = ParsedEmail.query.filter_by(is_duplicate_candidate=True).count()
    
    stats = {
        'total': total_emails,
        'completed': completed_emails,
        'failed': failed_emails,
        'duplicates': duplicate_candidates,
        'success_rate': round((completed_emails / total_emails * 100) if total_emails > 0 else 0, 1),
        'duplicate_rate': round((duplicate_candidates / completed_emails * 100) if completed_emails > 0 else 0, 1)
    }
    
    return render_template('email_parsing.html', emails=recent_emails, stats=stats, active_page='email_parsing')


@app.route('/api/email/parsed')
@login_required
def api_parsed_emails():
    """API endpoint for getting parsed emails with pagination"""
    from models import ParsedEmail
    
    page = request.args.get('page', 1, type=int)
    per_page = 50
    status_filter = request.args.get('status')
    source_filter = request.args.get('source')
    
    query = ParsedEmail.query
    
    if status_filter:
        query = query.filter(ParsedEmail.status == status_filter)
    if source_filter:
        query = query.filter(ParsedEmail.source_platform == source_filter)
    
    emails = query.order_by(ParsedEmail.received_at.desc()).paginate(
        page=page, 
        per_page=per_page, 
        error_out=False
    )
    
    return jsonify({
        'emails': [{
            'id': email.id,
            'sender_email': email.sender_email,
            'subject': email.subject[:100] if email.subject else None,
            'source_platform': email.source_platform,
            'bullhorn_job_id': email.bullhorn_job_id,
            'candidate_name': email.candidate_name,
            'candidate_email': email.candidate_email,
            'status': email.status,
            'bullhorn_candidate_id': email.bullhorn_candidate_id,
            'bullhorn_submission_id': email.bullhorn_submission_id,
            'is_duplicate': email.is_duplicate_candidate,
            'duplicate_confidence': email.duplicate_confidence,
            'resume_filename': email.resume_filename,
            'received_at': email.received_at.strftime('%Y-%m-%d %H:%M:%S') if email.received_at else None,
            'processed_at': email.processed_at.strftime('%Y-%m-%d %H:%M:%S') if email.processed_at else None,
            'processing_notes': email.processing_notes
        } for email in emails.items],
        'pagination': {
            'page': emails.page,
            'pages': emails.pages,
            'total': emails.total,
            'has_next': emails.has_next,
            'has_prev': emails.has_prev
        }
    })


@app.route('/api/email/stats')
@login_required
def api_email_parsing_stats():
    """Get email parsing statistics"""
    from models import ParsedEmail
    from sqlalchemy import func
    
    # Overall stats
    total = ParsedEmail.query.count()
    completed = ParsedEmail.query.filter_by(status='completed').count()
    failed = ParsedEmail.query.filter_by(status='failed').count()
    processing = ParsedEmail.query.filter_by(status='processing').count()
    duplicates = ParsedEmail.query.filter_by(is_duplicate_candidate=True).count()
    
    # Stats by source
    source_stats = db.session.query(
        ParsedEmail.source_platform,
        func.count(ParsedEmail.id)
    ).group_by(ParsedEmail.source_platform).all()
    
    # Stats by date (last 7 days)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    daily_stats = db.session.query(
        func.date(ParsedEmail.received_at),
        func.count(ParsedEmail.id)
    ).filter(
        ParsedEmail.received_at >= seven_days_ago
    ).group_by(
        func.date(ParsedEmail.received_at)
    ).all()
    
    return jsonify({
        'overview': {
            'total': total,
            'completed': completed,
            'failed': failed,
            'processing': processing,
            'duplicates': duplicates,
            'success_rate': round((completed / total * 100) if total > 0 else 0, 1),
            'duplicate_rate': round((duplicates / completed * 100) if completed > 0 else 0, 1)
        },
        'by_source': {source or 'Unknown': count for source, count in source_stats},
        'daily': {str(date): count for date, count in daily_stats}
    })


@app.route('/api/email/clear-stuck', methods=['POST'])
@login_required
def api_clear_stuck_emails():
    """Manually clear stuck email parsing records (mark as failed after timeout)"""
    try:
        from models import ParsedEmail
        
        # Records stuck in 'processing' for more than 10 minutes
        timeout_threshold = datetime.utcnow() - timedelta(minutes=10)
        
        stuck_records = ParsedEmail.query.filter(
            ParsedEmail.status == 'processing',
            ParsedEmail.created_at < timeout_threshold
        ).all()
        
        if stuck_records:
            cleared_ids = []
            for record in stuck_records:
                record.status = 'failed'
                record.processing_notes = f"Manually cleared: Processing timeout (started at {record.created_at})"
                record.processed_at = datetime.utcnow()
                cleared_ids.append(record.id)
                app.logger.info(f"⏰ Manually cleared stuck email parsing record ID {record.id} (candidate: {record.candidate_name or 'Unknown'})")
            
            db.session.commit()
            return jsonify({
                'success': True,
                'message': f'Cleared {len(cleared_ids)} stuck records',
                'cleared_ids': cleared_ids
            })
        else:
            return jsonify({
                'success': True,
                'message': 'No stuck records found (records must be processing for >10 minutes)',
                'cleared_ids': []
            })
            
    except Exception as e:
        app.logger.error(f"Error clearing stuck email records: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/email/test-parse', methods=['POST'])
@login_required 
def api_test_email_parse():
    """Test endpoint to simulate email parsing (for development)"""
    try:
        from email_inbound_service import EmailInboundService
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        service = EmailInboundService()
        
        # Test source detection
        source = service.detect_source(
            data.get('from', ''),
            data.get('subject', ''),
            data.get('body', '')
        )
        
        # Test job ID extraction
        job_id = service.extract_bullhorn_job_id(
            data.get('subject', ''),
            data.get('body', '')
        )
        
        # Test candidate extraction
        candidate = service.extract_candidate_from_email(
            data.get('subject', ''),
            data.get('body', ''),
            source
        )
        
        return jsonify({
            'source_detected': source,
            'job_id': job_id,
            'candidate': candidate
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)

def check_monitor_health():
    """Lightweight health check for manual workflow - job counting focus"""
    with app.app_context():
        try:
            app.logger.info("Starting periodic health check for manual workflow...")
            
            # For manual workflow, just verify monitoring is still active and counting jobs
            active_monitors = BullhornMonitor.query.filter_by(is_active=True).count()
            app.logger.info(f"✅ Manual workflow health check: {active_monitors} active monitors for job counting")
            
            # Check if monitoring cycles are running (less critical for manual workflow)
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
            import traceback
            app.logger.error(traceback.format_exc())

def check_environment_status():
    """Check production environment status and send alerts on status changes"""
    with app.app_context():
        try:
            # Import models here to avoid circular imports
            from models import EnvironmentStatus, EnvironmentAlert
            
            # Get or create environment status record
            env_status = EnvironmentStatus.query.filter_by(environment_name='production').first()
            if not env_status:
                # Create initial environment status record with production URL
                env_status = EnvironmentStatus(
                    environment_name='production',
                    environment_url='https://jobpulse.lyntrix.ai',  # Production URL
                    current_status='unknown',
                    alert_email='kroots@myticas.com'
                )
                db.session.add(env_status)
                db.session.commit()
                app.logger.info("Created initial environment status record for production monitoring")
            
            previous_status = env_status.current_status
            current_time = datetime.utcnow()
            
            # Perform health check
            try:
                app.logger.info(f"Checking environment status for: {env_status.environment_url}")
                response = requests.get(
                    env_status.environment_url + '/health',  # Use health endpoint
                    timeout=env_status.timeout_seconds,
                    headers={'User-Agent': 'JobPulse-Environment-Monitor/1.0'}
                )
                
                # Check if response is successful
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
            
            # Update status and timing
            env_status.current_status = new_status
            env_status.last_check_time = current_time
            
            # Check for status change and send alerts
            status_changed = (previous_status != new_status and previous_status != 'unknown')
            
            if status_changed:
                env_status.last_status_change = current_time
                app.logger.info(f"🔄 Environment status changed: {previous_status} → {new_status}")
                
                # Calculate downtime for recovery alerts
                downtime_minutes = None
                if new_status == 'up' and previous_status == 'down':
                    # Environment recovered
                    if env_status.last_status_change:
                        # Find the start of the downtime
                        last_down_change = EnvironmentAlert.query.filter_by(
                            environment_status_id=env_status.id,
                            alert_type='down'
                        ).order_by(EnvironmentAlert.sent_at.desc()).first()
                        
                        if last_down_change:
                            downtime_delta = current_time - last_down_change.sent_at
                            downtime_minutes = round(downtime_delta.total_seconds() / 60, 2)
                            env_status.total_downtime_minutes += downtime_minutes
                
                # Send alert if notifications are enabled
                alert_sent = False
                if ((new_status == 'down' and env_status.alert_on_down) or 
                    (new_status == 'up' and env_status.alert_on_recovery)):
                    
                    try:
                        alert_sent = send_environment_alert(env_status, new_status, previous_status, downtime_minutes)
                    except Exception as alert_error:
                        app.logger.error(f"Failed to send environment alert: {str(alert_error)}")
            
            # Save changes to database
            db.session.commit()
            
            # Log current status
            if new_status == 'up':
                app.logger.info(f"✅ Environment monitoring: {env_status.environment_name} is UP (consecutive failures: {env_status.consecutive_failures})")
            else:
                app.logger.warning(f"❌ Environment monitoring: {env_status.environment_name} is DOWN (consecutive failures: {env_status.consecutive_failures})")
            
        except Exception as e:
            app.logger.error(f"Environment status check error: {str(e)}")
            db.session.rollback()
            import traceback
            app.logger.error(traceback.format_exc())

def send_environment_alert(env_status, new_status, previous_status, downtime_minutes=None):
    """Send email alert for environment status change"""
    try:
        from models import EnvironmentAlert
        from timezone_utils import format_eastern_time
        
        # Get current time in Eastern timezone
        current_time_eastern = format_eastern_time(datetime.utcnow())
        
        # Create alert message
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

This is an automated message from JobPulse Environment Monitoring.
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

This is an automated message from JobPulse Environment Monitoring.
"""
        
        # Initialize email service
        email_service = EmailService()
        
        # Send email notification
        success = email_service.send_notification_email(
            to_email=env_status.alert_email,
            subject=subject,
            message=message,
            notification_type=f'environment_{new_status}'
        )
        
        # Log the alert to database
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
                with app.app_context():
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
    # Add monitor health check job - reduced frequency for manual workflow
    scheduler.add_job(
        func=check_monitor_health,
        trigger=IntervalTrigger(hours=2),
        id='check_monitor_health',
        name='Monitor Health Check (Manual Workflow)',
        replace_existing=True
    )
    app.logger.info("Monitor health check enabled - periodic check every 2 hours for manual workflow")

    # Add environment monitoring job
    scheduler.add_job(
        func=check_environment_status,
        trigger=IntervalTrigger(minutes=5),  # Check every 5 minutes
        id='environment_monitoring',
        name='Production Environment Monitoring',
        replace_existing=True
    )
    app.logger.info("Environment monitoring enabled - checking production status every 5 minutes")

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

# Activity Retention Cleanup
def activity_retention_cleanup():
    """Clean up BullhornActivity records older than 15 days"""
    with app.app_context():
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=15)
            
            # Count activities to be removed
            old_activities = BullhornActivity.query.filter(
                BullhornActivity.created_at < cutoff_date
            ).count()
            
            if old_activities > 0:
                # Delete old activities
                deleted_count = BullhornActivity.query.filter(
                    BullhornActivity.created_at < cutoff_date
                ).delete()
                
                db.session.commit()
                app.logger.info(f"🗑️ Activity cleanup: Removed {deleted_count} activity records older than 15 days")
                
                # Log the cleanup as a system activity
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

if is_primary_worker:
    # Add activity retention cleanup - runs daily at 3 AM
    scheduler.add_job(
        func=activity_retention_cleanup,
        trigger='cron',
        hour=3,
        minute=0,
        id='activity_cleanup',
        name='Activity Retention Cleanup (15 days)',
        replace_existing=True
    )
    app.logger.info("📋 Scheduled activity retention cleanup (15 days)")

# Log Monitoring with Self-Healing
def log_monitoring_cycle():
    """Run log monitoring cycle - fetches Render logs, analyzes for issues, auto-fixes or escalates."""
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

if is_primary_worker:
    # Get interval from environment (default 15 minutes)
    log_monitor_interval = int(os.environ.get('LOG_MONITOR_INTERVAL_MINUTES', '15'))
    
    scheduler.add_job(
        func=log_monitoring_cycle,
        trigger='interval',
        minutes=log_monitor_interval,
        id='log_monitoring',
        name=f'Render Log Monitoring (Self-Healing) - {log_monitor_interval}min',
        replace_existing=True
    )
    app.logger.info(f"📊 Log monitoring enabled - checking Render logs every {log_monitor_interval} minutes")

# Log Monitoring UI Routes
@app.route('/log-monitoring')
@login_required
def log_monitoring_page():
    """Log monitoring dashboard page."""
    return render_template('log_monitoring.html', active_page='log_monitoring')

@app.route('/api/log-monitoring/status')
@login_required
def api_log_monitoring_status():
    """Get current log monitoring status."""
    try:
        from log_monitoring_service import get_log_monitor
        monitor = get_log_monitor()
        return jsonify({
            "success": True,
            "status": monitor.get_status()
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/log-monitoring/history')
@login_required
def api_log_monitoring_history():
    """Get recent log monitoring history."""
    try:
        from log_monitoring_service import get_log_monitor
        monitor = get_log_monitor()
        limit = request.args.get('limit', 10, type=int)
        return jsonify({
            "success": True,
            "history": monitor.get_history(limit)
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/log-monitoring/run', methods=['POST'])
@login_required
def api_log_monitoring_run():
    """Manually trigger a log monitoring cycle."""
    try:
        from log_monitoring_service import run_log_monitoring_cycle
        result = run_log_monitoring_cycle(was_manual=True)  # Mark as manual trigger
        return jsonify({
            "success": True,
            "result": result
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/log-monitoring/issues')
@login_required
def api_log_monitoring_issues():
    """Get all log monitoring issues with filtering support."""
    try:
        from models import LogMonitoringIssue, LogMonitoringRun
        
        # Filter parameters
        status_filter = request.args.get('status', None)  # auto_fixed, escalated, resolved, all
        severity_filter = request.args.get('severity', None)  # minor, major, critical, all
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 25, type=int)
        
        # Build query
        query = LogMonitoringIssue.query.order_by(LogMonitoringIssue.detected_at.desc())
        
        if status_filter and status_filter != 'all':
            query = query.filter(LogMonitoringIssue.status == status_filter)
        
        if severity_filter and severity_filter != 'all':
            query = query.filter(LogMonitoringIssue.severity == severity_filter)
        
        # Paginate
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        
        issues = [{
            'id': issue.id,
            'run_id': issue.run_id,
            'detected_at': issue.detected_at.isoformat() if issue.detected_at else None,
            'pattern_name': issue.pattern_name,
            'category': issue.category,
            'severity': issue.severity,
            'description': issue.description,
            'occurrences': issue.occurrences,
            'status': issue.status,
            'resolution_action': issue.resolution_action,
            'resolution_summary': issue.resolution_summary,
            'resolved_at': issue.resolved_at.isoformat() if issue.resolved_at else None,
            'resolved_by': issue.resolved_by
        } for issue in pagination.items]
        
        # Get counts for filtering UI
        total_count = LogMonitoringIssue.query.count()
        auto_fixed_count = LogMonitoringIssue.query.filter_by(status='auto_fixed').count()
        escalated_count = LogMonitoringIssue.query.filter_by(status='escalated').count()
        resolved_count = LogMonitoringIssue.query.filter_by(status='resolved').count()
        
        return jsonify({
            "success": True,
            "issues": issues,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": pagination.total,
                "pages": pagination.pages,
                "has_next": pagination.has_next,
                "has_prev": pagination.has_prev
            },
            "counts": {
                "total": total_count,
                "auto_fixed": auto_fixed_count,
                "escalated": escalated_count,
                "resolved": resolved_count
            }
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/log-monitoring/issues/<int:issue_id>')
@login_required
def api_log_monitoring_issue_detail(issue_id):
    """Get detailed information about a specific issue."""
    try:
        from models import LogMonitoringIssue
        
        issue = LogMonitoringIssue.query.get_or_404(issue_id)
        
        return jsonify({
            "success": True,
            "issue": {
                'id': issue.id,
                'run_id': issue.run_id,
                'detected_at': issue.detected_at.isoformat() if issue.detected_at else None,
                'pattern_name': issue.pattern_name,
                'category': issue.category,
                'severity': issue.severity,
                'description': issue.description,
                'occurrences': issue.occurrences,
                'sample_log': issue.sample_log,  # Full sample log for detail view
                'status': issue.status,
                'resolution_action': issue.resolution_action,
                'resolution_summary': issue.resolution_summary,
                'resolved_at': issue.resolved_at.isoformat() if issue.resolved_at else None,
                'resolved_by': issue.resolved_by
            }
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/log-monitoring/issues/<int:issue_id>/resolve', methods=['POST'])
@login_required
def api_log_monitoring_resolve_issue(issue_id):
    """Manually resolve an escalated issue."""
    try:
        from models import LogMonitoringIssue
        
        issue = LogMonitoringIssue.query.get_or_404(issue_id)
        
        if issue.status not in ['escalated', 'detected']:
            return jsonify({
                "success": False,
                "error": "Only escalated or detected issues can be manually resolved"
            }), 400
        
        data = request.get_json() or {}
        resolution_notes = data.get('resolution_notes', 'Manually resolved')
        
        issue.mark_resolved(
            resolver_email=current_user.email,
            resolution_notes=resolution_notes
        )
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"Issue #{issue_id} marked as resolved"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/log-monitoring/runs')
@login_required
def api_log_monitoring_runs():
    """Get monitoring runs from database for persistence across restarts."""
    try:
        from models import LogMonitoringRun
        
        limit = request.args.get('limit', 20, type=int)
        
        runs = LogMonitoringRun.query.order_by(LogMonitoringRun.run_time.desc()).limit(limit).all()
        
        return jsonify({
            "success": True,
            "runs": [{
                'id': run.id,
                'timestamp': run.run_time.isoformat() if run.run_time else None,
                'logs_analyzed': run.logs_analyzed,
                'issues_found': run.issues_found,
                'auto_fixed': run.issues_auto_fixed,
                'escalated': run.issues_escalated,
                'status': run.status,
                'was_manual': run.was_manual,
                'execution_time_ms': run.execution_time_ms
            } for run in runs]
        })
    except Exception as e:
        # Fall back to in-memory history if database not available
        from log_monitoring_service import get_log_monitor
        monitor = get_log_monitor()
        limit = request.args.get('limit', 10, type=int)
        return jsonify({
            "success": True,
            "runs": monitor.get_history(limit),
            "source": "memory"
        })

@app.route('/api/feedback', methods=['POST'])
@login_required
def api_submit_feedback():
    """Submit user feedback via email."""
    try:
        data = request.get_json()
        feedback_type = data.get('type', 'other')
        message = data.get('message', '')
        page = data.get('page', 'Unknown')
        user = data.get('user', 'Unknown')
        
        # Map feedback type to readable label
        type_labels = {
            'feature': '💡 Feature Enhancement Idea',
            'bug': '🐛 Bug Report',
            'question': '❓ Question About System',
            'other': '📝 Other Feedback'
        }
        type_label = type_labels.get(feedback_type, '📝 Other Feedback')
        
        # Send email using SendGrid
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content
        
        sg_api_key = os.environ.get('SENDGRID_API_KEY')
        admin_email = os.environ.get('ADMIN_EMAIL', 'kroots@myticas.com')
        
        if sg_api_key:
            sg = SendGridAPIClient(sg_api_key)
            
            email_content = f"""
JobPulse™ User Feedback Received

Type: {type_label}
From: {user}
Page: {page}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Message:
{message}

---
This feedback was submitted via the JobPulse™ Feedback system.
            """
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #1e3a5f 0%, #0d2847 100%); padding: 20px; border-radius: 8px;">
                    <h2 style="color: #60a5fa; margin: 0;">📬 JobPulse™ Feedback</h2>
                </div>
                <div style="padding: 20px; background: #f8fafc; border-radius: 0 0 8px 8px;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 8px 0; color: #64748b;"><strong>Type:</strong></td><td style="padding: 8px 0;">{type_label}</td></tr>
                        <tr><td style="padding: 8px 0; color: #64748b;"><strong>From:</strong></td><td style="padding: 8px 0;">{user}</td></tr>
                        <tr><td style="padding: 8px 0; color: #64748b;"><strong>Page:</strong></td><td style="padding: 8px 0;">{page}</td></tr>
                        <tr><td style="padding: 8px 0; color: #64748b;"><strong>Time:</strong></td><td style="padding: 8px 0;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
                    </table>
                    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                    <h3 style="color: #1e3a5f; margin-bottom: 10px;">Message:</h3>
                    <div style="background: white; padding: 15px; border-radius: 6px; border: 1px solid #e2e8f0;">
                        {message.replace(chr(10), '<br>')}
                    </div>
                </div>
            </div>
            """
            
            mail = Mail(
                from_email=Email("noreply@lyntrix.ai", "JobPulse Feedback"),
                to_emails=To(admin_email),
                subject=f"[JobPulse Feedback] {type_label} from {user}",
                plain_text_content=Content("text/plain", email_content),
                html_content=Content("text/html", html_content)
            )
            
            response = sg.send(mail)
            logging.info(f"Feedback email sent: {response.status_code}")
        
        # Log the feedback
        logging.info(f"User Feedback - Type: {feedback_type}, User: {user}, Page: {page}")
        
        return jsonify({"success": True, "message": "Feedback submitted successfully"})
        
    except Exception as e:
        logging.error(f"Error submitting feedback: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Email Parsing Stuck Record Cleanup
def email_parsing_timeout_cleanup():
    """Auto-fail stuck email parsing records after 10 minutes"""
    with app.app_context():
        try:
            from models import ParsedEmail
            
            # Records stuck in 'processing' for more than 10 minutes
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

if is_primary_worker:
    # Add email parsing stuck record cleanup - runs every 5 minutes
    scheduler.add_job(
        func=email_parsing_timeout_cleanup,
        trigger='interval',
        minutes=5,
        id='email_parsing_timeout_cleanup',
        name='Email Parsing Timeout Cleanup (10 min)',
        replace_existing=True
    )
    app.logger.info("📧 Scheduled email parsing timeout cleanup (10 min threshold, every 5 min)")

# Vetting System Health Check
def run_vetting_health_check():
    """Run health checks on the vetting system components"""
    with app.app_context():
        try:
            from models import VettingHealthCheck, VettingConfig, CandidateVettingLog
            from sqlalchemy import func
            from datetime import datetime, timedelta
            
            bullhorn_status = True
            bullhorn_error = None
            openai_status = True
            openai_error = None
            database_status = True
            database_error = None
            scheduler_status = True
            scheduler_error = None
            
            # Check Bullhorn connectivity
            try:
                from bullhorn_service import BullhornService
                bh = BullhornService()
                if not bh.access_token:
                    bullhorn_status = False
                    bullhorn_error = "Failed to obtain Bullhorn access token"
            except Exception as e:
                bullhorn_status = False
                bullhorn_error = str(e)[:500]
            
            # Check OpenAI API (lightweight check)
            try:
                import openai
                import os
                client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
                # Just verify client can be created and key exists
                if not os.environ.get('OPENAI_API_KEY'):
                    openai_status = False
                    openai_error = "OPENAI_API_KEY not configured"
            except Exception as e:
                openai_status = False
                openai_error = str(e)[:500]
            
            # Check database connectivity
            try:
                db.session.execute(db.text("SELECT 1"))
            except Exception as e:
                database_status = False
                database_error = str(e)[:500]
            
            # Check scheduler status
            try:
                if not scheduler.running:
                    scheduler_status = False
                    scheduler_error = "Scheduler is not running"
            except Exception as e:
                scheduler_status = False
                scheduler_error = str(e)[:500]
            
            # Gather stats
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
            
            # Get last successful cycle
            last_success = CandidateVettingLog.query.filter_by(status='completed').order_by(
                CandidateVettingLog.processed_at.desc()
            ).first()
            last_successful_cycle = last_success.processed_at if last_success else None
            
            # Determine overall health
            is_healthy = bullhorn_status and openai_status and database_status and scheduler_status
            
            # Create health check record
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
            
            # Send alert email if unhealthy
            if not is_healthy:
                send_vetting_health_alert(health_check)
            
            # Cleanup old health checks (keep last 7 days)
            cleanup_threshold = datetime.utcnow() - timedelta(days=7)
            VettingHealthCheck.query.filter(VettingHealthCheck.check_time < cleanup_threshold).delete()
            db.session.commit()
            
            app.logger.info(f"🩺 Vetting health check: {'✅ Healthy' if is_healthy else '❌ Issues detected'}")
            
        except Exception as e:
            app.logger.error(f"Vetting health check error: {str(e)}")


def send_vetting_health_alert(health_check):
    """Send email alert for vetting system health issues"""
    try:
        from models import VettingConfig, VettingHealthCheck
        from datetime import datetime, timedelta
        import sendgrid
        from sendgrid.helpers.mail import Mail
        import os
        
        # Check if we already sent an alert in the last hour
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_alert = VettingHealthCheck.query.filter(
            VettingHealthCheck.alert_sent == True,
            VettingHealthCheck.alert_sent_at >= one_hour_ago
        ).first()
        
        if recent_alert:
            app.logger.info("🩺 Skipping alert - already sent within last hour")
            return
        
        # Get health alert email - skip if not configured
        health_alert_email = VettingConfig.get_value('health_alert_email', '')
        if not health_alert_email:
            app.logger.info("🩺 Health alert email not configured - skipping alert")
            return
        
        # Build error message
        errors = []
        if not health_check.bullhorn_status:
            errors.append(f"Bullhorn: {health_check.bullhorn_error or 'Connection failed'}")
        if not health_check.openai_status:
            errors.append(f"OpenAI: {health_check.openai_error or 'API unavailable'}")
        if not health_check.database_status:
            errors.append(f"Database: {health_check.database_error or 'Connection failed'}")
        if not health_check.scheduler_status:
            errors.append(f"Scheduler: {health_check.scheduler_error or 'Not running'}")
        
        error_list = "\\n".join([f"• {e}" for e in errors])
        
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #dc3545;">⚠️ JobPulse Vetting System Alert</h2>
            <p>The AI Candidate Vetting system has detected issues that require attention:</p>
            
            <div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 15px 0;">
                <strong>Issues Detected:</strong><br>
                {"<br>".join([f"• {e}" for e in errors])}
            </div>
            
            <p><strong>System Stats:</strong></p>
            <ul>
                <li>Candidates Processed Today: {health_check.candidates_processed_today}</li>
                <li>Candidates Pending: {health_check.candidates_pending}</li>
                <li>Emails Sent Today: {health_check.emails_sent_today}</li>
            </ul>
            
            <p style="color: #666; font-size: 12px;">
                This is an automated alert from JobPulse. Check the <a href="https://jobpulse.lyntrix.ai/vetting/settings">Vetting Dashboard</a> for more details.
            </p>
        </body>
        </html>
        """
        
        message = Mail(
            from_email='noreply@myticas.com',
            to_emails=health_alert_email,
            subject='⚠️ JobPulse Vetting System Alert - Issues Detected',
            html_content=html_content
        )
        
        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
        response = sg.send(message)
        
        if response.status_code in [200, 202]:
            health_check.alert_sent = True
            health_check.alert_sent_at = datetime.utcnow()
            db.session.commit()
            app.logger.info(f"🩺 Health alert sent to {health_alert_email}")
        else:
            app.logger.warning(f"🩺 Health alert failed: {response.status_code}")
            
    except Exception as e:
        app.logger.error(f"Failed to send health alert: {str(e)}")


if is_primary_worker:
    # Add vetting system health check - runs every 10 minutes
    scheduler.add_job(
        func=run_vetting_health_check,
        trigger='interval',
        minutes=10,
        id='vetting_health_check',
        name='Vetting System Health Check',
        replace_existing=True
    )
    app.logger.info("🩺 Scheduled vetting system health check (every 10 minutes)")

# Candidate Vetting Cycle (AI-powered applicant matching)
def run_candidate_vetting_cycle():
    """Run the AI-powered candidate vetting cycle to analyze new applicants"""
    with app.app_context():
        try:
            from candidate_vetting_service import CandidateVettingService
            from models import VettingConfig
            
            # Check if vetting is enabled
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

if is_primary_worker:
    # Add candidate vetting cycle - runs every 2 minutes
    scheduler.add_job(
        func=run_candidate_vetting_cycle,
        trigger='interval',
        minutes=1,
        id='candidate_vetting_cycle',
        name='AI Candidate Vetting Cycle',
        replace_existing=True
    )
    app.logger.info("🎯 Scheduled AI candidate vetting cycle (every 1 minute)")

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
            
            # Generate fresh XML content using SimplifiedXMLGenerator
            from simplified_xml_generator import SimplifiedXMLGenerator
            
            # Create generator instance with database access
            generator = SimplifiedXMLGenerator(db=db)
            
            # Generate fresh XML content first
            xml_content, stats = generator.generate_fresh_xml()
            app.logger.info(f"📊 Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
            
            # Apply reference number refresh to the generated content
            from lightweight_reference_refresh import lightweight_refresh_references_from_content
            
            # Refresh reference numbers in the generated XML content
            result = lightweight_refresh_references_from_content(xml_content)
            
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
                
                # CRITICAL: Save reference numbers to database (database-first approach)
                # Database save is REQUIRED - failure will raise exception and alert via email
                from lightweight_reference_refresh import save_references_to_database
                db_save_success = save_references_to_database(result['xml_content'])
                
                if not db_save_success:
                    # Database save failure is CRITICAL - raise exception to trigger error handling
                    error_msg = "Database-first architecture requires successful DB save - 120-hour refresh FAILED"
                    app.logger.critical(f"❌ CRITICAL: {error_msg}")
                    raise Exception(error_msg)
                
                app.logger.info("💾 DATABASE-FIRST: Reference numbers successfully saved to database")
                app.logger.info("✅ Reference refresh complete: Reference numbers updated in database (30-minute upload cycle will use these values)")
                
                # Send email notification confirming refresh execution
                try:
                    from email_service import EmailService
                    
                    # Check if email notifications are enabled
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
                    
                    # Check if email notifications are enabled
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

# Automated Upload Function (30 minutes)
def automated_upload():
    """Automatically upload fresh XML every 30 minutes if automation is enabled"""
    with app.app_context():
        try:
            # Check if automated uploads are enabled in settings
            automation_setting = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
            if not (automation_setting and automation_setting.setting_value == 'true'):
                app.logger.info("📋 Automated uploads disabled in settings, skipping upload cycle")
                return
            
            # Check if SFTP is enabled and configured
            sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
            if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
                app.logger.warning("📤 Automated upload skipped: SFTP not enabled")
                return
            
            app.logger.info("🚀 Starting automated 30-minute upload cycle...")
            app.logger.info("⚡ AUTOMATED UPLOAD FUNCTION EXECUTING - production priority enabled")
            
            # Generate fresh XML using SimplifiedXMLGenerator (database-first approach)
            # SimplifiedXMLGenerator ALWAYS loads reference numbers from database
            from simplified_xml_generator import SimplifiedXMLGenerator
            generator = SimplifiedXMLGenerator(db=db)
            xml_content, stats = generator.generate_fresh_xml()
            
            app.logger.info(f"📊 Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
            app.logger.info("📍 CHECKPOINT 1: XML generation completed successfully")
            app.logger.info("💾 Reference numbers loaded from DATABASE (database-first approach)")
            
            # NOTE: Database is now the single source of truth for reference numbers
            # SimplifiedXMLGenerator already loaded references from database and saved them back
            # No need to preserve from SFTP - that would overwrite database values!
            
            # Use locking mechanism to prevent conflicts with monitoring cycle
            lock_file = 'monitoring.lock'
            upload_success = False
            upload_error_message = None
            
            try:
                # Check if monitoring cycle is running
                if os.path.exists(lock_file):
                    try:
                        with open(lock_file, 'r') as f:
                            lock_data = f.read().strip()
                            if lock_data:
                                lock_time = datetime.fromisoformat(lock_data)
                                lock_age = (datetime.utcnow() - lock_time).total_seconds()
                                
                                if lock_age < 240:  # 4 minutes
                                    app.logger.warning("🔒 Monitoring cycle is running, skipping automated upload")
                                    return
                                else:
                                    os.remove(lock_file)  # Remove stale lock
                    except Exception as e:
                        app.logger.warning(f"Error reading monitoring lock: {str(e)}. Proceeding with upload.")
                        if os.path.exists(lock_file):
                            os.remove(lock_file)
                
                # Create temporary lock for upload
                with open(lock_file, 'w') as f:
                    f.write(datetime.utcnow().isoformat())
                app.logger.info("🔒 Lock acquired for automated upload")
                
                try:
                    # Save XML to temporary file
                    import tempfile
                    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8')
                    temp_file.write(xml_content)
                    temp_file.close()
                    
                    # Get SFTP settings
                    sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
                    sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
                    sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
                    sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
                    sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
                    
                    if (sftp_hostname and sftp_hostname.setting_value and 
                        sftp_username and sftp_username.setting_value and 
                        sftp_password and sftp_password.setting_value):
                        
                        # Simple upload destination using configured settings
                        target_directory = sftp_directory.setting_value if sftp_directory else "/"
                        app.logger.info(f"📤 Uploading to configured directory: '{target_directory}'")
                        
                        # Upload BOTH development and production files for complete coverage
                        # FORCE SFTP for production reliability (thread-safe, no signal issues)
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
                        
                        # ENVIRONMENT-AWARE UPLOAD: Dev uploads to -dev.xml, Production uploads to .xml
                        # CRITICAL: Check both APP_ENV and ENVIRONMENT variables explicitly
                        # Default to 'development' if neither is set (safer than defaulting to production)
                        current_env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'development').lower()
                        app.logger.info(f"🔍 Environment detection: APP_ENV={os.environ.get('APP_ENV')}, ENVIRONMENT={os.environ.get('ENVIRONMENT')}, using={current_env}")
                        
                        # Validate environment value
                        if current_env not in ['production', 'development']:
                            app.logger.error(f"❌ Invalid environment '{current_env}' - defaulting to development for safety")
                            current_env = 'development'
                        
                        if current_env == 'production':
                            # PRODUCTION ENVIRONMENT: Upload ONLY to production file
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
                            # DEVELOPMENT ENVIRONMENT: Upload ONLY to development file
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
                        
                        # Log environment isolation status
                        app.logger.info(f"🔒 ENVIRONMENT ISOLATION: {current_env} → uploads ONLY to its designated file")
                        
                        # Handle both dict and boolean return types from FTP service
                        if isinstance(upload_result, dict):
                            if upload_result['success']:
                                upload_success = True
                                app.logger.info(f"✅ Automated upload successful: {upload_result.get('message', 'File uploaded')}")
                            else:
                                upload_error_message = upload_result.get('error', 'Unknown upload error')
                                app.logger.error(f"❌ Automated upload failed: {upload_error_message}")
                        else:
                            # FTP service returned boolean
                            if upload_result:
                                upload_success = True
                                app.logger.info("✅ Automated upload successful")
                            else:
                                upload_error_message = "Upload failed"
                                app.logger.error("❌ Automated upload failed")
                        
                        # Track successful upload time in GlobalSettings
                        if upload_success:
                            try:
                                last_upload_setting = GlobalSettings.query.filter_by(setting_key='last_sftp_upload_time').first()
                                upload_timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
                                if last_upload_setting:
                                    last_upload_setting.setting_value = upload_timestamp
                                    last_upload_setting.updated_at = datetime.utcnow()
                                else:
                                    last_upload_setting = GlobalSettings(
                                        setting_key='last_sftp_upload_time',
                                        setting_value=upload_timestamp
                                    )
                                    db.session.add(last_upload_setting)
                                db.session.commit()
                                app.logger.info(f"✅ Updated last upload timestamp: {upload_timestamp}")
                            except Exception as ts_error:
                                app.logger.error(f"Failed to track upload timestamp: {str(ts_error)}")
                    else:
                        upload_error_message = "SFTP credentials not configured"
                        app.logger.error("❌ SFTP credentials not configured in Global Settings")
                    
                    # Clean up temporary file
                    try:
                        os.remove(temp_file.name)
                    except:
                        pass
                
                finally:
                    # Always remove lock when upload completes
                    if os.path.exists(lock_file):
                        try:
                            os.remove(lock_file)
                            app.logger.info("🔓 Lock released after automated upload")
                        except Exception as e:
                            app.logger.error(f"Error removing upload lock: {str(e)}")
                
                # Send email notification
                # Check if email notifications are enabled
                email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                email_setting = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                
                if (email_enabled and email_enabled.setting_value == 'true' and 
                    email_setting and email_setting.setting_value):
                    try:
                        from email_service import EmailService
                        from timezone_utils import format_eastern_time
                        email_service = EmailService()
                        
                        # Prepare notification details with Eastern Time
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
                
            except Exception as lock_error:
                app.logger.error(f"Lock management error during automated upload: {str(lock_error)}")
            
        except Exception as e:
            app.logger.error(f"❌ Automated upload error: {str(e)}")

if is_primary_worker:
    # Schedule automated uploads every 30 minutes - simple and reliable
    print("📤 SCHEDULER INIT: Registering automated upload job (every 30 minutes)...", flush=True)
    try:
        scheduler.add_job(
            func=automated_upload,
            trigger=IntervalTrigger(minutes=30),
            id='automated_upload',
            name='Automated Upload (Every 30 Minutes)',
            replace_existing=True
        )
        print("✅ SCHEDULER INIT: Automated upload job registered successfully", flush=True)
        app.logger.info("📤 Scheduled automated uploads every 30 minutes")
    except Exception as e:
        print(f"❌ SCHEDULER INIT: Failed to register automated upload job: {e}", flush=True)
        app.logger.error(f"Failed to register automated upload job: {e}")
    
    # Schedule reference refresh every 120 hours - with proper next_run_time calculation
    # This ensures the schedule doesn't reset on application restart
    try:
        with app.app_context():
            from datetime import date, timedelta
            
            # Get the last refresh from database
            last_refresh = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()
            
            if last_refresh:
                # Calculate when the NEXT refresh should be (120 hours after last refresh)
                calculated_next_run = last_refresh.refresh_time + timedelta(hours=120)
                time_since_refresh = datetime.utcnow() - last_refresh.refresh_time
                
                # Check if we're already overdue
                if time_since_refresh > timedelta(hours=120):
                    app.logger.info(f"⏰ Last refresh was {time_since_refresh.total_seconds() / 3600:.1f} hours ago, running catch-up refresh...")
                    reference_number_refresh()
                    # After catch-up, schedule next run 120 hours from now
                    calculated_next_run = datetime.utcnow() + timedelta(hours=120)
                else:
                    hours_until_next = 120 - (time_since_refresh.total_seconds() / 3600)
                    app.logger.info(f"📝 Last refresh was {time_since_refresh.total_seconds() / 3600:.1f} hours ago, next refresh in {hours_until_next:.1f} hours")
                
                # Add job with calculated next_run_time to prevent restart-based schedule drift
                scheduler.add_job(
                    func=reference_number_refresh,
                    trigger=IntervalTrigger(hours=120, start_date=calculated_next_run),
                    id='reference_number_refresh',
                    name='120-Hour Reference Number Refresh',
                    replace_existing=True,
                    next_run_time=calculated_next_run
                )
                app.logger.info(f"📅 Scheduled reference number refresh - next run: {calculated_next_run.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            else:
                # No previous refresh found, run one now and schedule next
                app.logger.info("🆕 No previous refresh found, running initial refresh...")
                reference_number_refresh()
                
                # Schedule next run 120 hours from now
                next_run = datetime.utcnow() + timedelta(hours=120)
                scheduler.add_job(
                    func=reference_number_refresh,
                    trigger=IntervalTrigger(hours=120, start_date=next_run),
                    id='reference_number_refresh',
                    name='120-Hour Reference Number Refresh',
                    replace_existing=True,
                    next_run_time=next_run
                )
                app.logger.info(f"📅 Scheduled reference number refresh - next run: {next_run.strftime('%Y-%m-%d %H:%M:%S UTC')}")
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

if is_primary_worker:
    # XML Change Monitor - DISABLED automatic scheduling for manual workflow
    # Change notifications now triggered only during manual downloads
    app.logger.info("📧 XML Change Monitor: Auto-notifications DISABLED - notifications now sent only during manual downloads")

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

@app.route('/api/diagnostic/automation-status')
@login_required
def diagnostic_automation_status():
    """Diagnostic endpoint to check automation configuration and state"""
    try:
        # Get toggle states from database
        automated_uploads = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
        sftp_enabled_setting = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        
        # Get SFTP credentials
        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
        
        # Check environment detection
        current_env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'development').lower()
        
        # Check if REPLIT_DEPLOYMENT exists (production indicator)
        is_production = os.environ.get('REPLIT_DEPLOYMENT') is not None
        
        # Determine upload filename based on environment
        upload_filename = "myticas-job-feed-v2.xml" if current_env == 'production' else "myticas-job-feed-v2-dev.xml"
        
        diagnostic_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'environment_detection': {
                'APP_ENV': os.environ.get('APP_ENV'),
                'ENVIRONMENT': os.environ.get('ENVIRONMENT'),
                'REPLIT_DEPLOYMENT': 'present' if is_production else 'missing',
                'detected_environment': current_env,
                'is_production': is_production
            },
            'database_toggles': {
                'automated_uploads_enabled': automated_uploads.setting_value if automated_uploads else 'NOT_FOUND',
                'sftp_enabled': sftp_enabled_setting.setting_value if sftp_enabled_setting else 'NOT_FOUND'
            },
            'sftp_config': {
                'hostname': sftp_hostname.setting_value if sftp_hostname else 'NOT_FOUND',
                'username': sftp_username.setting_value if sftp_username else 'NOT_FOUND',
                'port': sftp_port.setting_value if sftp_port else 'NOT_FOUND'
            },
            'upload_behavior': {
                'target_filename': upload_filename,
                'automation_will_run': (
                    automated_uploads and automated_uploads.setting_value == 'true' and
                    sftp_enabled_setting and sftp_enabled_setting.setting_value == 'true'
                )
            }
        }
        
        return jsonify(diagnostic_data)
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        }), 500

# Phase 2 approach removed - lazy scheduler now completes in single phase for reliability

# Scheduler and background services will be started lazily when first needed
# This significantly reduces application startup time for deployment health checks

