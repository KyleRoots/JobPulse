"""
Health check routes for JobPulse.

Provides various health check endpoints for deployment monitoring and Kubernetes probes.
Also provides CSRF-exempt cron job endpoints authenticated via bearer token.
"""

import os
import time
from datetime import datetime
from functools import wraps
from flask import Blueprint, jsonify, current_app, request

health_bp = Blueprint('health', __name__)


@health_bp.route('/health')
def health_check():
    """Optimized health check with cached database status"""
    from app import db
    
    start_time = time.time()
    
    # Use cached database status if available (refresh every 10 seconds)
    db_status = 'unknown'
    cache_key = 'db_health_cache'
    cache_time_key = 'db_health_cache_time'
    
    # Check if we have a recent cached result (within 10 seconds)
    if hasattr(current_app, cache_time_key):
        cache_age = time.time() - getattr(current_app, cache_time_key, 0)
        if cache_age < 10:  # Use cached result if less than 10 seconds old
            db_status = getattr(current_app, cache_key, 'unknown')
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
            setattr(current_app, cache_key, db_status)
            setattr(current_app, cache_time_key, time.time())
    else:
        # First check - do a quick test
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text('SELECT 1'))
            db_status = 'connected'
        except Exception:
            db_status = 'disconnected'
        # Cache the result
        setattr(current_app, cache_key, db_status)
        setattr(current_app, cache_time_key, time.time())
    
    # Quick scheduler check
    scheduler_status = 'stopped'  # Default to stopped (lazy loading)
    try:
        from app import scheduler
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


@health_bp.route('/ready')
def readiness_check():
    """Fast readiness check without database query"""
    # Return OK immediately - app is ready if it can respond
    return "OK", 200


@health_bp.route('/alive')
def liveness_check():
    """Simple liveness check for deployment systems"""
    return "OK", 200


@health_bp.route('/ping')
def ping():
    """Ultra-fast health check for deployment monitoring"""
    # Return immediately without any expensive operations
    return jsonify({
        'status': 'ok',
        'service': 'job-feed-refresh',
        'timestamp': datetime.utcnow().isoformat()
    }), 200


@health_bp.route('/healthz')
def detailed_health_check():
    """Detailed health check with configuration status"""
    from app import db
    
    try:
        start_time = time.time()
        
        # Test database connection with timeout
        db_ok = False
        try:
            from sqlalchemy import text
            db.session.execute(text('SELECT 1')).scalar()
            db_ok = True
        except Exception:
            current_app.logger.warning("Database check failed during health check")
        
        # Quick configuration checks
        config_status = {
            'session_configured': bool(current_app.secret_key),
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
        current_app.logger.error(f"Health check error: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': 'Internal health check failure'
        }), 500


# ═══════════════════════════════════════════════════════════════
# CRON JOB API ENDPOINTS
# Authenticated via Bearer token (CRON_SECRET env var).
# CSRF-exempt since these are called by Render cron jobs, not browsers.
# ═══════════════════════════════════════════════════════════════

def require_cron_secret(f):
    """Decorator to require CRON_SECRET bearer token for API access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        cron_secret = os.environ.get('CRON_SECRET', '')
        if not cron_secret:
            current_app.logger.error("CRON_SECRET env var not set — cron endpoint disabled")
            return jsonify({'error': 'Cron endpoint not configured'}), 503
        
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer ') or auth_header[7:] != cron_secret:
            current_app.logger.warning(f"Unauthorized cron API call from {request.remote_addr}")
            return jsonify({'error': 'Unauthorized'}), 401
        
        return f(*args, **kwargs)
    return decorated


@health_bp.route('/api/cron/send-digest', methods=['POST'])
@require_cron_secret
def cron_send_digest():
    """Trigger daily vetting digest email via cron job.
    
    Authenticated via CRON_SECRET bearer token, CSRF-exempt.
    Called by Render cron job: Daily Vetting Digest Email (0 12 * * *)
    """
    try:
        from embedding_digest_service import send_daily_digest
        
        current_app.logger.info("📧 Cron trigger: sending daily vetting digest email")
        success = send_daily_digest()
        
        if success:
            current_app.logger.info("📧 Daily digest email sent successfully via cron")
            return jsonify({
                'success': True,
                'message': 'Daily digest email sent successfully',
                'timestamp': datetime.utcnow().isoformat()
            }), 200
        else:
            current_app.logger.warning("📧 Daily digest email failed to send via cron")
            return jsonify({
                'success': False,
                'error': 'Digest email failed to send — check SendGrid configuration'
            }), 500
            
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        current_app.logger.error(f"📧 Error in cron digest trigger: {str(e)}\n{error_detail}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@health_bp.route('/api/cron/scout-vetting-followups', methods=['POST'])
@require_cron_secret
def cron_scout_vetting_followups():
    """Run Scout Vetting follow-up processing.
    
    Sends follow-up emails to unresponsive candidates,
    closes expired sessions, and promotes queued sessions.
    
    Authenticated via CRON_SECRET bearer token, CSRF-exempt.
    Called by Render cron job every 30 minutes: */30 * * * *
    """
    try:
        from scout_vetting_service import ScoutVettingService
        from email_service import EmailService

        current_app.logger.info("🔍 Cron trigger: running Scout Vetting follow-ups")

        svc = ScoutVettingService(email_service=EmailService())

        if not svc.is_enabled():
            return jsonify({
                'success': True,
                'message': 'Scout Vetting is disabled — no follow-ups processed',
                'stats': {}
            }), 200

        stats = svc.run_followups()

        current_app.logger.info(f"🔍 Scout Vetting follow-ups complete: {stats}")
        return jsonify({
            'success': True,
            'message': 'Scout Vetting follow-ups processed',
            'stats': stats,
            'timestamp': datetime.utcnow().isoformat()
        }), 200

    except Exception as e:
        current_app.logger.error(f"🔍 Error in Scout Vetting follow-up cron: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
