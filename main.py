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
from datetime import datetime

# Configure logging before importing the app
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log', mode='a')
    ]
)

logger = logging.getLogger(__name__)

def check_environment():
    """Check required environment variables and dependencies"""
    required_env_vars = ['SESSION_SECRET']
    missing_vars = []
    
    for var in required_env_vars:
        if not os.environ.get(var):
            missing_vars.append(var)
    
    if missing_vars:
        logger.warning(f"Missing environment variables: {missing_vars}")
        # Set fallback for SESSION_SECRET if missing
        if 'SESSION_SECRET' in missing_vars:
            os.environ['SESSION_SECRET'] = os.urandom(24).hex()
            logger.info("Generated fallback SESSION_SECRET for deployment")
    
    return True

def initialize_app():
    """Initialize the Flask application with error handling"""
    try:
        logger.info("Starting application initialization...")
        
        # Check environment
        check_environment()
        
        # Import app after environment setup
        from app import app
        
        logger.info("Flask application imported successfully")
        
        # Test basic app functionality
        with app.test_client() as client:
            response = client.get('/health')
            if response.status_code == 200:
                logger.info("Health check passed during startup")
            else:
                logger.warning(f"Health check returned status {response.status_code}")
        
        # Always ensure monitoring is started on application boot
        try:
            from app import ensure_background_services
            if ensure_background_services():
                logger.info("Monitoring system auto-started successfully on boot")
            else:
                logger.warning("Could not auto-start monitoring system")
        except Exception as e:
            logger.warning(f"Could not auto-start monitoring: {e}")
        
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
