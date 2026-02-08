"""
Health check routes for JobPulse.

Provides various health check endpoints for deployment monitoring and Kubernetes probes.
"""

import os
import time
from datetime import datetime
from flask import Blueprint, jsonify, current_app


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
        except Exception as e:
            current_app.logger.warning(f"Database check failed: {str(e)}")
        
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
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500
