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
                   run_xml_change_monitor, start_scheduler_manual, cleanup_linkedin_source,
                   enforce_tearsheet_jobs_public, run_requirements_maintenance)

# Configure logging for debugging account manager extraction
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Suppress verbose logging from external libraries
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)

# NOTE: progress_tracker removed — per-process in-memory dicts don't work
# across multiple Gunicorn workers. Progress state is stored in GlobalSettings
# (DB-backed) in routes/scheduler.py and routes/xml_routes.py instead.

# Timeout handler for monitoring cycles - thread-safe version
class MonitoringTimeout(Exception):
    """Exception raised when monitoring cycle exceeds time limit"""
    pass

def with_timeout(seconds=110):
    """Thread-safe timeout decorator using threading instead of signals"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
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

            thread = threading.Thread(target=target)
            thread.daemon = True
            thread.start()

            if not completed.wait(timeout=seconds):
                app.logger.warning(f"⏱️ TIMEOUT: Monitoring cycle exceeded {seconds} seconds - stopping to prevent overdue")
                return None

            if exception[0]:
                raise exception[0]

            return result[0]
        return wrapper
    return decorator

from extensions import db, login_manager, csrf, PRODUCTION_DOMAINS, scheduler_started, scheduler_lock, create_app

app = create_app()

def is_production_request():
    """Detect if current request is from production domain with hardened detection"""
    if not has_request_context():
        return False

    try:
        host = request.headers.get('X-Forwarded-Host', request.host or '').split(',')[0].strip()
        host = host.split(':')[0].rstrip('.').lower()

        is_prod = host in PRODUCTION_DOMAINS

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

    env = app.config.get('ENVIRONMENT')
    if env == 'production':
        app.logger.debug(f"Using production filename (source: app config)")
        return f"{base_filename}.xml"
    elif env == 'development':
        app.logger.debug(f"Using development filename (source: app config)")
        return f"{base_filename}-dev.xml"

    try:
        if is_production_request():
            app.logger.debug(f"Using production filename (source: request host)")
            return f"{base_filename}.xml"
        else:
            app.logger.debug(f"Using development filename (source: request host)")
            return f"{base_filename}-dev.xml"
    except:
        app.logger.warning(f"Could not determine environment, defaulting to production filename for safety")
        return f"{base_filename}.xml"


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
from routes.scout_screening import scout_screening_bp
from routes.support_request import support_request_bp
from routes.support_auth import support_auth_bp
from routes.scout_support import scout_support_bp
from routes.knowledge_hub import knowledge_hub_bp
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
app.register_blueprint(scout_screening_bp)
app.register_blueprint(support_request_bp)
app.register_blueprint(support_auth_bp)
app.register_blueprint(scout_support_bp)
app.register_blueprint(knowledge_hub_bp)

from routes.scout_prospector import scout_prospector_bp
app.register_blueprint(scout_prospector_bp)

from routes.platform_support import platform_support_bp
app.register_blueprint(platform_support_bp)

from routes.vetting_sandbox import vetting_sandbox_bp
app.register_blueprint(vetting_sandbox_bp)

from routes.activity_log import activity_log_bp
app.register_blueprint(activity_log_bp)

from utils.bullhorn_helpers import get_bullhorn_service, get_email_service

_MODULE_MAP = {
    '/screening': 'scout_screening',
    '/vetting': 'scout_screening',
    '/scout-screening': 'scout_screening',
    '/ats-integration': 'scout_inbound',
    '/scout-inbound': 'scout_inbound',
    '/automations': 'scout_automation',
    '/workbench': 'scout_automation',
    '/scout-automation': 'scout_automation',
    '/scout-support': 'scout_support',
    '/scout-prospector': 'scout_prospector',
    '/settings': 'system',
    '/email-logs': 'system',
    '/log-monitoring': 'system',
    '/activity-log': 'system',
    '/dashboard': 'system',
}

@app.before_request
def _track_module_access():
    if not current_user.is_authenticated:
        return
    if request.path.startswith('/static') or request.path.startswith('/api'):
        return

    try:
        from extensions import db as _db
        now = datetime.utcnow()
        if not current_user.last_active_at or (now - current_user.last_active_at).total_seconds() > 60:
            current_user.last_active_at = now
            _db.session.commit()
    except Exception:
        try:
            from extensions import db as _db2
            _db2.session.rollback()
        except Exception:
            pass

    if request.method != 'GET':
        return
    module = None
    for prefix, mod in _MODULE_MAP.items():
        if request.path.startswith(prefix):
            module = mod
            break
    if not module:
        return
    last = session.get('_last_module')
    if last == module:
        return
    session['_last_module'] = module
    try:
        from models import UserActivityLog
        from extensions import db as _db
        from datetime import timedelta
        five_min_ago = datetime.utcnow() - timedelta(minutes=5)
        recent = UserActivityLog.query.filter(
            UserActivityLog.user_id == current_user.id,
            UserActivityLog.activity_type == 'module_access',
            UserActivityLog.created_at >= five_min_ago
        ).filter(
            UserActivityLog.details.contains(f'"module": "{module}"')
        ).first()
        if not recent:
            _db.session.add(UserActivityLog(
                user_id=current_user.id,
                activity_type='module_access',
                ip_address=request.remote_addr,
                details=json.dumps({'module': module, 'path': request.path})
            ))
            _db.session.commit()
    except Exception:
        try:
            from extensions import db as _db2
            _db2.session.rollback()
        except Exception:
            pass

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
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-XSS-Protection'] = '0'
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
app.config['WTF_CSRF_TIME_LIMIT'] = None

csrf.init_app(app)

# Exempt cron job API endpoints from CSRF (they use bearer token auth via CRON_SECRET)
from routes.health import cron_send_digest, cron_scout_vetting_followups
csrf.exempt(cron_send_digest)
csrf.exempt(cron_scout_vetting_followups)

# Exempt Scout Support admin JSON endpoints from CSRF (behind login + admin check)
from routes.scout_support import delete_ticket
csrf.exempt(delete_ticket)


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

        # Migration: Add retry_blocked columns to candidate_vetting_log if missing
        if 'candidate_vetting_log' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('candidate_vetting_log')]
            if 'retry_blocked' not in columns:
                db.session.execute(text('ALTER TABLE candidate_vetting_log ADD COLUMN retry_blocked BOOLEAN DEFAULT FALSE'))
                db.session.commit()
                app.logger.info('🔧 Migration: Added retry_blocked column to candidate_vetting_log')
            if 'retry_block_reason' not in columns:
                db.session.execute(text('ALTER TABLE candidate_vetting_log ADD COLUMN retry_block_reason VARCHAR(500)'))
                db.session.commit()
                app.logger.info('🔧 Migration: Added retry_block_reason column to candidate_vetting_log')
    except Exception as migrate_err:
        app.logger.warning(f'Migration check failed (may be first run): {migrate_err}')

    # Seed database with initial data (production-safe, idempotent)
    try:
        from seed_database import seed_database
        from models import User

        seeding_results = seed_database(db, User)

        if seeding_results.get('admin_created'):
            app.logger.info(f"🌱 Database seeding: Created admin user {seeding_results.get('admin_username')}")
        else:
            app.logger.info(f"🌱 Database seeding: Admin user already exists ({seeding_results.get('admin_username')})")

        if seeding_results.get('errors'):
            for error in seeding_results['errors']:
                app.logger.error(f"🌱 Seeding error: {error}")

    except Exception as e:
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

# Cleanup scheduler on exit with proper error handling
def cleanup_scheduler():
    try:
        if scheduler.running:
            scheduler.shutdown()
    except Exception:
        pass

atexit.register(cleanup_scheduler)

def allowed_file(filename):
    """Check if file has allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_resume_file(filename):
    """Check if file has an allowed resume extension (pdf, doc, docx, txt, rtf)"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS


# ── Scheduler Lock + Job Registration ─────────────────────────────────────────

from scheduler_setup import acquire_scheduler_lock, configure_scheduler_jobs

is_primary_worker = acquire_scheduler_lock()
print(f"🔒 SCHEDULER INIT: is_primary_worker = {is_primary_worker}", flush=True)

configure_scheduler_jobs(app, scheduler, is_primary_worker)
if not is_primary_worker:
    print(
        f"⚠️ SCHEDULER INIT: Process {os.getpid()} skipping scheduler setup"
        " - another worker handles scheduling",
        flush=True,
    )
    app.logger.info(
        f"⚠️ Process {os.getpid()} skipping scheduler setup"
        " - another worker handles scheduling"
    )

# Note: login and logout routes moved to routes/auth.py blueprint

# Note: Health check routes moved to routes/health.py blueprint

def get_automation_status():
    """Check if automation/scheduler is currently active"""
    try:
        recent_cutoff = datetime.utcnow() - timedelta(minutes=10)
        recent_activity = BullhornActivity.query.filter(
            BullhornActivity.created_at > recent_cutoff,
            BullhornActivity.activity_type.in_(['check_completed', 'job_added', 'job_removed', 'job_modified'])
        ).count()

        if recent_activity > 0:
            return True

        recent_monitors = BullhornMonitor.query.filter(
            BullhornMonitor.last_check > recent_cutoff
        ).count()

        if recent_monitors > 0:
            return True

        active_monitors = BullhornMonitor.query.filter_by(is_active=True).count()
        return active_monitors > 0

    except Exception as e:
        app.logger.debug(f"Automation status check error: {e}")
        return True

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

def _register_scheduler_listeners():
    """Register APScheduler job execution listeners for last-run tracking. Call once after scheduler.start()."""
    try:
        from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED
        import json as _json
        from datetime import datetime

        def _on_job_executed(event):
            try:
                with app.app_context():
                    from models import GlobalSettings
                    GlobalSettings.set_value(
                        f'scheduler_last_run_{event.job_id}',
                        _json.dumps({'timestamp': datetime.utcnow().isoformat(), 'success': True})
                    )
            except Exception:
                pass

        def _on_job_error(event):
            try:
                with app.app_context():
                    from models import GlobalSettings
                    GlobalSettings.set_value(
                        f'scheduler_last_run_{event.job_id}',
                        _json.dumps({'timestamp': datetime.utcnow().isoformat(), 'success': False})
                    )
            except Exception:
                pass

        def _on_job_missed(event):
            if event.job_id == 'automated_upload':
                app.logger.error(
                    f"⚠️ AUTOMATED UPLOAD MISSED: job skipped due to misfire. "
                    f"Scheduled: {event.scheduled_run_time}"
                )

        scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)
        scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
        scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)
        app.logger.info("📡 Scheduler job execution listeners registered")
    except Exception as e:
        app.logger.warning(f"Failed to register scheduler listeners: {e}")


def _restore_paused_jobs():
    """Re-apply paused state from GlobalSettings after scheduler (re)start."""
    try:
        import json as _json
        from models import GlobalSettings
        paused_raw = GlobalSettings.get_value('scheduler_paused_jobs', '[]')
        paused_ids = _json.loads(paused_raw)
        for job_id in paused_ids:
            try:
                scheduler.pause_job(job_id)
                app.logger.info(f"⏸ Restored paused state for scheduler job: {job_id}")
            except Exception:
                pass
    except Exception as e:
        app.logger.warning(f"Failed to restore paused scheduler jobs: {e}")


def ensure_background_services():
    """Ensure background services are started when first needed"""
    global _background_services_started
    if not scheduler.running:
        try:
            scheduler.start()
            app.logger.info("Background scheduler started/restarted successfully")
            _background_services_started = True

            _register_scheduler_listeners()
            _restore_paused_jobs()

            try:
                with app.app_context():
                    from datetime import datetime, timedelta
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

    if not _background_services_started:
        _background_services_started = True

    return True


def process_bullhorn_monitors():
    """Trigger a single incremental monitoring cycle.

    Used by manual scheduler-start routes (routes/scheduler.py, tasks.py).
    Can be called within a request context — no app context wrapper needed.
    Respects feed freeze state: returns immediately when feeds are frozen.
    """
    try:
        from feeds.freeze_manager import FreezeManager
        freeze_mgr = FreezeManager()
        if freeze_mgr.is_frozen():
            logging.info("🔒 XML FEED FROZEN: Skipping manual monitoring cycle")
            return

        from incremental_monitoring_service import IncrementalMonitoringService
        from models import BullhornMonitor
        from extensions import db as _db
        monitoring_service = IncrementalMonitoringService()
        cycle_results = monitoring_service.run_monitoring_cycle()

        real_monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        if real_monitors:
            current_time = datetime.utcnow()
            for monitor in real_monitors:
                monitor.last_check = current_time
                monitor.next_check = current_time + timedelta(minutes=5)
            _db.session.commit()

        logging.info(f"✅ Manual monitor cycle completed: {cycle_results}")
    except Exception:
        logging.error("Manual monitor cycle error", exc_info=True)
        try:
            from extensions import db as _db
            _db.session.rollback()
        except Exception:
            pass
        raise


# Note: /ready and /alive routes now provided by routes/health.py blueprint

# ONE-TIME CLEANUP PAGE REMOVED (2026-02-07)
# The /cleanup-duplicate-notes page was a one-time solution for duplicate AI vetting notes.
# It has been removed as the issue is resolved and automated batch cleanup is in place.
# The automated cleanup runs via incremental_monitoring_service.cleanup_duplicate_notes_batch()

# Phase 2 approach removed - lazy scheduler now completes in single phase for reliability

# Scheduler and background services will be started lazily when first needed
# This significantly reduces application startup time for deployment health checks
