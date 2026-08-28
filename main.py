#!/usr/bin/env python3
"""
Main entry point for the Flask application.
Provides deployment-ready configuration with health checks and error handling.
"""
import os
import sys

# Load environment variables from .env file BEFORE anything else
from dotenv import load_dotenv
load_dotenv()

import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# Configure logging before importing the app
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler('app.log', maxBytes=10*1024*1024, backupCount=3)
    ]
)

logger = logging.getLogger(__name__)

def check_environment():
    """Check required environment variables and dependencies"""
    from seed_database import is_production_environment

    if is_production_environment() and not os.environ.get('SESSION_SECRET'):
        logger.error('SESSION_SECRET is required in production')
        raise RuntimeError(
            'SESSION_SECRET environment variable is required in production. '
            'Set it in Railway (or your host) before deploying.'
        )

    if not os.environ.get('SESSION_SECRET'):
        logger.warning('SESSION_SECRET not set — generating ephemeral secret for this process')
        os.environ['SESSION_SECRET'] = os.urandom(32).hex()
        logger.info('Generated fallback SESSION_SECRET (development only)')

    return True

def initialize_app():
    """Initialize the Flask application with error handling"""
    try:
        logger.info("Starting application initialization...")
        
        # Check environment
        check_environment()
        
        # Import app after environment setup. Do not hit /health or start
        # Bullhorn/scheduler here: gunicorn --preload cannot bind until this
        # returns (Railway healthcheck failures Aug 26 2026). Scheduler
        # starts in gunicorn.conf.py post_fork.
        from app import app

        logger.info("Flask application imported successfully")
        return app
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to initialize application: {error_msg}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        # Return a minimal app for emergency health checks
        from flask import Flask, jsonify
        emergency_app = Flask(__name__)
        
        @emergency_app.route('/')
        def emergency_root():
            return jsonify({
                'status': 'error',
                'service': 'job-feed-refresh',
                'message': 'Application failed to initialize',
                'timestamp': datetime.utcnow().isoformat(),
                'error': error_msg
            }), 503
        
        @emergency_app.route('/health')
        def emergency_health():
            return jsonify({
                'status': 'error',
                'message': 'Application failed to initialize',
                'timestamp': datetime.utcnow().isoformat(),
                'error': error_msg
            }), 503
        
        @emergency_app.route('/ready')
        def emergency_ready():
            return jsonify({
                'status': 'not_ready',
                'timestamp': datetime.utcnow().isoformat(),
                'error': error_msg
            }), 503
        
        @emergency_app.route('/alive')
        def emergency_alive():
            return jsonify({
                'status': 'alive',
                'timestamp': datetime.utcnow().isoformat(),
                'error': error_msg
            }), 200
            
        return emergency_app

# Initialize the application
app = initialize_app()

if __name__ == '__main__':
    # Development server - not used in production deployment
    logger.info("Starting development server...")
    port = int(os.environ.get('PORT', 5001))  # 5001 to avoid macOS AirPlay on 5000
    app.run(host='0.0.0.0', port=port, debug=False)
