import os
import logging
import threading
import time
import signal
import atexit
import shutil
import tempfile
import uuid
import traceback
import json
import re
import requests
from datetime import datetime, timedelta
from functools import wraps

from flask import render_template, request, send_file, flash, redirect, url_for, jsonify, after_this_request, has_request_context, session, abort
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

try:
    from lxml import etree
except ImportError:
    etree = None
    logging.warning("lxml not available, some XML features disabled")

from xml_processor import XMLProcessor
from email_service import EmailService
from ftp_service import FTPService
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from incremental_monitoring_service import IncrementalMonitoringService
from job_application_service import JobApplicationService
from xml_change_monitor import create_xml_monitor
from tasks import (check_monitor_health, check_environment_status, send_environment_alert,
                   activity_retention_cleanup, log_monitoring_cycle, email_parsing_timeout_cleanup,
                   run_data_retention_cleanup, run_vetting_health_check, send_vetting_health_alert,
                   run_candidate_vetting_cycle, reference_number_refresh, automated_upload,
                   run_xml_change_monitor, start_scheduler_manual)

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

from extensions import db, login_manager, csrf, PRODUCTION_DOMAINS, scheduler_started, scheduler_lock, create_app

app = create_app()

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



# Company-specific URL generation - no global override needed
# Each service will determine URLs based on company context

# Register blueprints
from routes.auth import auth_bp
from routes.health import health_bp
from routes.settings import settings_bp
from routes.dashboard import dashboard_bp
from routes.ats_integration import ats_integration_bp
from routes.scheduler import scheduler_bp
from routes.vetting import vetting_bp
from routes.triggers import triggers_bp
from routes.automations import automations_bp
from routes.email import email_bp
from routes.log_monitoring import log_monitoring_bp
from routes.job_application import job_application_bp
from routes.diagnostics import diagnostics_bp
from routes.ats_monitoring import ats_monitoring_bp
from routes.email_logs import email_logs_bp
from routes.xml_routes import xml_routes_bp
from routes.scout_inbound import scout_inbound_bp
from routes.support_request import support_request_bp
app.register_blueprint(auth_bp)
app.register_blueprint(health_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(ats_integration_bp)
app.register_blueprint(scheduler_bp)
app.register_blueprint(vetting_bp)
app.register_blueprint(triggers_bp)
app.register_blueprint(automations_bp)
app.register_blueprint(email_bp)
app.register_blueprint(log_monitoring_bp)
app.register_blueprint(job_application_bp)
app.register_blueprint(diagnostics_bp)
app.register_blueprint(ats_monitoring_bp)
app.register_blueprint(email_logs_bp)
app.register_blueprint(xml_routes_bp)
app.register_blueprint(scout_inbound_bp)
app.register_blueprint(support_request_bp)

from utils.bullhorn_helpers import get_bullhorn_service, get_email_service

@login_manager.user_loader
def load_user(user_id):
    User = globals().get('User')
    if User:
        return User.query.get(int(user_id))
    return None

# ============================================================================
# Security Headers
# ============================================================================

@app.after_request
def set_security_headers(response):
    """Add standard security headers to all responses."""
    # Prevent clickjacking — deny all framing
    response.headers['X-Frame-Options'] = 'DENY'
    # Prevent MIME-type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Enforce HTTPS for 1 year (Render/production uses HTTPS)
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # Control referrer information sent with requests
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # Disable legacy XSS filter (modern browsers rely on CSP instead)
    response.headers['X-XSS-Protection'] = '0'
    # Content-Security-Policy — conservative starting point
    # 'unsafe-inline' required because templates use inline scripts/styles
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com https://cdn.replit.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    return response

# Configuration
UPLOAD_FOLDER = tempfile.gettempdir()
ALLOWED_EXTENSIONS = {'xml'}
ALLOWED_RESUME_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'rtf'}
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max file size

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.config['WTF_CSRF_TIME_LIMIT'] = None  # No token expiry (avoids issues with long-open tabs)

csrf.init_app(app)

# Exempt cron job API endpoints from CSRF (they use bearer token auth via CRON_SECRET)
from routes.health import cron_send_digest, cron_scout_vetting_followups
csrf.exempt(cron_send_digest)
csrf.exempt(cron_scout_vetting_followups)


@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file uploads that exceed MAX_CONTENT_LENGTH (50 MB)."""
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'success': False, 'error': 'File too large. Maximum upload size is 50 MB.'}), 413
    flash('File too large. Maximum upload size is 50 MB.', 'error')
    return redirect(request.referrer or url_for('dashboard.dashboard_redirect')), 413

# Import models
from models import User, ScheduleConfig, ProcessingLog, RefreshLog, GlobalSettings, BullhornMonitor, BullhornActivity, TearsheetJobHistory, EmailDeliveryLog, RecruiterMapping, SchedulerLock

from utils.filters import register_filters, format_activity_details
register_filters(app)

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

def allowed_resume_file(filename):
    """Check if file has an allowed resume extension (pdf, doc, docx, txt, rtf)"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS

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

# Note: login and logout routes moved to routes/auth.py blueprint

# Note: Health check routes moved to routes/health.py blueprint

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

# Note: / and /dashboard routes moved to routes/dashboard.py blueprint


# Note: Scheduler routes and helper functions moved to routes/scheduler.py blueprint


# Note: /api/refresh-reference-numbers route moved to routes/xml_routes.py blueprint


# Note: /upload, /manual-upload-progress, /download, /download-current-xml, /automation-status,
# /test-upload, /manual-upload-now, /validate, /bullhorn/oauth/callback, /automation_test
# routes moved to routes/xml_routes.py blueprint

# Note: /settings routes moved to routes/settings.py blueprint

# Note: update_settings and test_sftp_connection also moved to routes/settings.py blueprint

# Note: ATS Integration routes moved to routes/ats_integration.py blueprint (formerly routes/bullhorn.py)





# Note: reset_test_file, run_automation_demo, run_step_test helper functions moved to routes/xml_routes.py



# Note: test_download, ATS monitoring, and email log routes moved to blueprints

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)

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

    # P0 optimization: background-refresh active job IDs cache so /screening
    # never triggers a synchronous Bullhorn API call on page load.
    def refresh_active_job_ids_cache():
        """Refresh the CandidateVettingService active job IDs cache in the background."""
        with app.app_context():
            try:
                from candidate_vetting_service import CandidateVettingService
                import time
                svc = CandidateVettingService()
                active_jobs = svc.get_active_jobs_from_tearsheets()
                result = set(int(job.get('id')) for job in active_jobs if job.get('id'))
                CandidateVettingService._active_job_ids_cache = result
                CandidateVettingService._active_job_ids_cache_time = time.time()
                app.logger.info(f"🔄 Active job IDs cache refreshed: {len(result)} jobs")
            except Exception as e:
                app.logger.error(f"Error refreshing active job IDs cache: {e}")

    scheduler.add_job(
        func=refresh_active_job_ids_cache,
        trigger=IntervalTrigger(minutes=5),
        id='refresh_active_job_ids',
        name='Active Job IDs Cache Refresh (5 min)',
        replace_existing=True
    )
    # Also warm the cache immediately on startup
    try:
        refresh_active_job_ids_cache()
    except Exception as e:
        app.logger.warning(f"Initial active job IDs cache warm failed: {e}")
    app.logger.info("Active job IDs background cache refresh enabled (5-min interval)")

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

if is_primary_worker:
    # Add data retention cleanup - runs every 24 hours at 3 AM UTC
    scheduler.add_job(
        func=run_data_retention_cleanup,
        trigger='cron',
        hour=3,
        minute=0,
        id='data_retention_cleanup',
        name='Data Retention Cleanup (Daily)',
        replace_existing=True
    )
    app.logger.info("🧹 Scheduled data retention cleanup (daily at 3 AM UTC)")

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

if is_primary_worker:
    # Add candidate vetting cycle - runs every 3 minutes (reduced from 1 min to lower API pressure)
    scheduler.add_job(
        func=run_candidate_vetting_cycle,
        trigger='interval',
        minutes=3,
        id='candidate_vetting_cycle',
        name='AI Candidate Vetting Cycle',
        replace_existing=True
    )
    app.logger.info("🎯 Scheduled AI candidate vetting cycle (every 3 minutes)")

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

    def run_salesrep_sync_job():
        try:
            with app.app_context():
                from salesrep_sync_service import run_salesrep_sync
                bullhorn = get_bullhorn_service()
                result = run_salesrep_sync(bullhorn)
                if result.get('updated', 0) > 0:
                    app.logger.info(
                        f"🏢 Sales Rep Sync: {result['updated']} companies updated "
                        f"(scanned {result['scanned']}, {result.get('errors', 0)} errors)"
                    )
        except Exception as e:
            app.logger.error(f"Sales Rep Sync job error: {e}")

    try:
        scheduler.add_job(
            func=run_salesrep_sync_job,
            trigger=IntervalTrigger(minutes=30),
            id='salesrep_sync',
            name='Sales Rep Display Name Sync (Every 30 Minutes)',
            replace_existing=True
        )
        print("✅ SCHEDULER INIT: Sales Rep sync job registered (every 30 minutes)", flush=True)
        app.logger.info("🏢 Scheduled Sales Rep display name sync every 30 minutes")
    except Exception as e:
        print(f"❌ SCHEDULER INIT: Failed to register Sales Rep sync job: {e}", flush=True)
        app.logger.error(f"Failed to register Sales Rep sync job: {e}")

    # Schedule reference refresh every 120 hours — NEVER fires inline on startup.
    # On restart, we reconstruct next_run from persisted RefreshLog state.
    # If overdue, we defer to now + 5min so the scheduler handles it, not startup.
    try:
        with app.app_context():
            from datetime import date, timedelta
            
            last_refresh = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()
            
            if last_refresh:
                calculated_next_run = last_refresh.refresh_time + timedelta(hours=120)
                time_since_refresh = datetime.utcnow() - last_refresh.refresh_time
                is_overdue = time_since_refresh > timedelta(hours=120)
                
                if is_overdue:
                    # Overdue — schedule for 5 minutes from now instead of firing inline
                    calculated_next_run = datetime.utcnow() + timedelta(minutes=5)
                    app.logger.info(
                        f"⏰ Reference refresh: last_run={last_refresh.refresh_time.strftime('%Y-%m-%d %H:%M:%S UTC')}, "
                        f"next_run={calculated_next_run.strftime('%Y-%m-%d %H:%M:%S UTC')}, overdue=true "
                        f"(deferred to +5min, NOT firing inline on startup)"
                    )
                else:
                    hours_until_next = (calculated_next_run - datetime.utcnow()).total_seconds() / 3600
                    app.logger.info(
                        f"📝 Reference refresh: last_run={last_refresh.refresh_time.strftime('%Y-%m-%d %H:%M:%S UTC')}, "
                        f"next_run={calculated_next_run.strftime('%Y-%m-%d %H:%M:%S UTC')}, overdue=false "
                        f"({hours_until_next:.1f}h remaining)"
                    )
                
                scheduler.add_job(
                    func=reference_number_refresh,
                    trigger=IntervalTrigger(hours=120),
                    id='reference_number_refresh',
                    name='120-Hour Reference Number Refresh',
                    replace_existing=True,
                    next_run_time=calculated_next_run
                )
            else:
                # No previous refresh found — schedule for 5 min from now
                calculated_next_run = datetime.utcnow() + timedelta(minutes=5)
                app.logger.info(
                    f"🆕 Reference refresh: last_run=NONE, "
                    f"next_run={calculated_next_run.strftime('%Y-%m-%d %H:%M:%S UTC')}, "
                    f"no history found — deferred to +5min"
                )
                scheduler.add_job(
                    func=reference_number_refresh,
                    trigger=IntervalTrigger(hours=120),
                    id='reference_number_refresh',
                    name='120-Hour Reference Number Refresh',
                    replace_existing=True,
                    next_run_time=calculated_next_run
                )
    except Exception as startup_error:
        app.logger.error(f"Failed to schedule reference refresh: {str(startup_error)}")

if is_primary_worker:
    # XML Change Monitor - DISABLED automatic scheduling for manual workflow
    # Change notifications now triggered only during manual downloads
    app.logger.info("📧 XML Change Monitor: Auto-notifications DISABLED - notifications now sent only during manual downloads")
# Note: /ready and /alive routes now provided by routes/health.py blueprint


# ONE-TIME CLEANUP PAGE REMOVED (2026-02-07)
# The /cleanup-duplicate-notes page was a one-time solution for duplicate AI vetting notes.
# It has been removed as the issue is resolved and automated batch cleanup is in place.
# The automated cleanup runs via incremental_monitoring_service.cleanup_duplicate_notes_batch()

# Phase 2 approach removed - lazy scheduler now completes in single phase for reliability

# Scheduler and background services will be started lazily when first needed
# This significantly reduces application startup time for deployment health checks

