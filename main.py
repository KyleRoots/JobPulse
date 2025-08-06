#!/usr/bin/env python3
"""
Main entry point for the Flask application.
Provides deployment-ready configuration with health checks and error handling.
"""
import os
import sys
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
        
        return app
        
    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        # Return a minimal app for emergency health checks
        from flask import Flask, jsonify
        emergency_app = Flask(__name__)
        
        @emergency_app.route('/health')
        def emergency_health():
            return jsonify({
                'status': 'error',
                'message': 'Application failed to initialize',
                'timestamp': datetime.utcnow().isoformat(),
                'error': str(e)
            }), 503
            
        return emergency_app

# Initialize the application
app = initialize_app()

if __name__ == '__main__':
    # Development server - not used in production deployment
    logger.info("Starting development server...")
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
