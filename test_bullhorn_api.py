#!/usr/bin/env python
"""Direct test of Bullhorn API connection"""

from flask import Flask, jsonify
from bullhorn_service import BullhornService
import logging

# Create a minimal Flask app for testing
app = Flask(__name__)
app.config['SECRET_KEY'] = 'test-key'

# Configure logging
logging.basicConfig(level=logging.INFO)

@app.route('/test')
def test():
    """Test the Bullhorn connection directly"""
    try:
        service = BullhornService()
        result = service.test_connection()
        
        return f"""
        <html>
        <head>
            <title>Bullhorn Test</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .success {{ color: green; }}
                .error {{ color: red; }}
            </style>
        </head>
        <body>
            <h1>Bullhorn Connection Test</h1>
            <p>Result: <span class="{'success' if result else 'error'}">{'SUCCESS' if result else 'FAILED'}</span></p>
            <p>Service has credentials: {bool(service.client_id and service.username)}</p>
            <hr>
            <a href="/">Back to main app</a>
        </body>
        </html>
        """
    except Exception as e:
        return f"<h1>Error</h1><pre>{str(e)}</pre>"

if __name__ == '__main__':
    print("Starting test server on http://localhost:5001/test")
    app.run(debug=True, port=5001)