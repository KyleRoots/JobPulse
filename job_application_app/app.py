"""
Separate Flask Application for Job Applications (apply.myticas.com)
Lightweight, focused app for handling job application forms
"""

import os
import logging
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET") or "job-app-secret-key"
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Enable CORS for cross-domain requests if needed
CORS(app, origins=['https://jobpulse.lyntrix.ai', 'https://apply.myticas.com'])

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import services
from job_application_service import JobApplicationService
import sys
import os
# Add parent directory to path to import tearsheet_config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tearsheet_config import TearsheetConfig

# Initialize job application service
job_app_service = JobApplicationService()

@app.route('/')
def index():
    """Landing page for job applications"""
    return render_template('index.html')

@app.route('/apply/<job_id>/<job_title>/')
def job_application_form(job_id, job_title):
    """Display job application form with appropriate branding based on domain"""
    try:
        # Get source from query parameters
        source = request.args.get('source', '')
        
        # Decode job title from URL
        import urllib.parse
        decoded_title = urllib.parse.unquote(job_title)
        
        # Detect which domain is being accessed
        domain = request.host.lower()
        
        # Get branding configuration based on domain
        branding = TearsheetConfig.get_branding_for_domain(domain)
        
        # Select appropriate template based on branding
        if 'stsigroup' in domain:
            template = 'apply_stsi.html'
            logger.info(f"Using STSI branded template for domain: {domain}")
        else:
            template = 'apply.html'
            logger.info(f"Using Myticas branded template for domain: {domain}")
        
        # Create response with cache-busting headers to force fresh content
        from flask import make_response
        import time
        response = make_response(render_template(template, 
                                                job_id=job_id, 
                                                job_title=decoded_title, 
                                                source=source,
                                                branding=branding,
                                                version=str(int(time.time()))))  # Add version timestamp
        
        # Aggressive cache prevention for CDN and browsers
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, private, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['X-Accel-Expires'] = '0'  # For nginx
        response.headers['Surrogate-Control'] = 'no-store'  # For CDN
        
        return response
    except Exception as e:
        logger.error(f"Error displaying job application form: {str(e)}")
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
        
        return jsonify(parse_result)
        
    except Exception as e:
        logger.error(f"Error parsing resume: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error parsing resume: {str(e)}'
        })

@app.route('/submit-application', methods=['POST'])
def submit_application():
    """Submit job application form"""
    try:
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
        
        if not resume_file:
            return jsonify({
                'success': False,
                'error': 'Resume file is required'
            })
        
        # Submit the application
        submission_result = job_app_service.submit_application(
            application_data=application_data,
            resume_file=resume_file,
            cover_letter_file=cover_letter_file
        )
        
        return jsonify(submission_result)
        
    except Exception as e:
        logger.error(f"Error submitting application: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error submitting application: {str(e)}'
        })

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'job-application-app',
        'version': '1.0.0'
    })

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logger.error(f"Internal server error: {str(error)}")
    return render_template('500.html'), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))  # Different port from main app
    app.run(host='0.0.0.0', port=port, debug=True)