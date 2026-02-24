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

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    """Handle file upload and processing with progress tracking"""
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))
        
        file = request.files['file']
        
        # Check if file was actually selected
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))
        
        # Check file extension
        if not allowed_file(file.filename):
            flash('Invalid file type. Please upload an XML file.', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))
        
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
            return redirect(url_for('ats_integration.ats_integration_dashboard'))
        
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
            return redirect(url_for('ats_integration.ats_integration_dashboard'))
            
    except Exception as e:
        app.logger.error(f"Error in upload_file: {str(e)}")
        flash(f'An error occurred while processing the file: {str(e)}', 'error')
        return redirect(url_for('ats_integration.ats_integration_dashboard'))

@app.route('/manual-upload-progress/<upload_id>')
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
            return redirect(url_for('ats_integration.ats_integration_dashboard'))
        
        file_info = app.config[session_key]
        filepath = file_info['filepath']
        filename = file_info['filename']
        
        if not os.path.exists(filepath):
            flash('File not found', 'error')
            return redirect(url_for('ats_integration.ats_integration_dashboard'))
        
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
        return redirect(url_for('ats_integration.ats_integration_dashboard'))

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
        return redirect(url_for('ats_integration.ats_integration_dashboard'))

@app.route('/automation-status')
@login_required
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

# Note: /settings routes moved to routes/settings.py blueprint

# Note: update_settings and test_sftp_connection also moved to routes/settings.py blueprint

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


# Note: ATS Integration routes moved to routes/ats_integration.py blueprint (formerly routes/bullhorn.py)

# Permanent 307 redirect: old OAuth callback URL → new URL
# Preserves query params (code, state) needed for OAuth flow
# Safety net for Bullhorn OAuth whitelist transition
@app.route('/bullhorn/oauth/callback')
def bullhorn_oauth_callback_redirect():
    """Permanent redirect for old OAuth callback URL - preserves query params"""
    return redirect(url_for('ats_integration.oauth_callback', **request.args), code=307)


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
@login_required
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
    return render_template('ats_monitoring.html', active_page='ats_monitoring')

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
        
        # Batch-load monitor names (eliminates N+1 queries)
        monitor_ids = {a.monitor_id for a in activities if a.monitor_id}
        monitor_map = {}
        if monitor_ids:
            monitors = BullhornMonitor.query.filter(BullhornMonitor.id.in_(monitor_ids)).all()
            monitor_map = {m.id: m.name for m in monitors}
        
        activity_data = []
        for activity in activities:
            # Resolve monitor name: use map for known IDs, "System" for null
            if activity.monitor_id:
                monitor_name = monitor_map.get(activity.monitor_id, "Unknown")
            else:
                monitor_name = "System"
            
            # Guard against None details
            details = activity.details or ''
            if len(details) > 200:
                details = details[:200] + '...'
            
            activity_data.append({
                'id': activity.id,
                'timestamp': activity.created_at.isoformat(),
                'monitor_name': monitor_name,
                'activity_type': activity.activity_type,
                'details': details
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

