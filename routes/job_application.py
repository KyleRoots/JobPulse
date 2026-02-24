import logging
import traceback
from flask import Blueprint, render_template, request, jsonify, abort, make_response
from extensions import db, csrf

logger = logging.getLogger(__name__)
job_application_bp = Blueprint('job_application', __name__)

ALLOWED_RESUME_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'rtf'}


def allowed_resume_file(filename):
    """Check if file has an allowed resume extension (pdf, doc, docx, txt, rtf)"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS


@job_application_bp.route('/<job_id>/<job_title>/')
def job_application_form(job_id, job_title):
    """Display job application form with client-specific branding"""
    try:
        if not job_id.isdigit():
            abort(404)

        source = request.args.get('source', '')

        import urllib.parse
        decoded_title = urllib.parse.unquote(job_title)

        host = request.host.lower()
        if 'stsigroup.com' in host:
            template = 'apply_stsi.html'
            logger.info(f"Serving STSI template for domain: {host}")
        else:
            template = 'apply.html'
            logger.info(f"Serving Myticas template for domain: {host}")

        response = make_response(render_template(template,
                             job_id=job_id,
                             job_title=decoded_title,
                             source=source))

        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'

        return response
    except Exception as e:
        logger.error(f"Error displaying job application form: {str(e)}")
        return f"Error loading application form: {str(e)}", 500


@job_application_bp.route('/parse-resume', methods=['POST'])
@csrf.exempt
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

        if not allowed_resume_file(resume_file.filename):
            return jsonify({
                'success': False,
                'error': 'Invalid file type. Allowed: PDF, DOC, DOCX, TXT, RTF.'
            }), 400

        from job_application_service import JobApplicationService
        job_app_service = JobApplicationService()
        parse_result = job_app_service.parse_resume(resume_file)

        if parse_result.get('success') and parse_result.get('parsed_info'):
            parsed_info = parse_result['parsed_info']
            if parsed_info.get('success', False):
                return jsonify({
                    'success': True,
                    'parsed_info': parsed_info
                })
            else:
                return jsonify({
                    'success': False,
                    'error': parsed_info.get('error', 'Failed to parse resume'),
                    'parsed_info': {'parsed_data': {}}
                })
        else:
            return jsonify({
                'success': False,
                'error': parse_result.get('error', 'Failed to parse resume'),
                'parsed_info': {'parsed_data': {}}
            })

    except Exception as e:
        logger.error(f"Error parsing resume: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error parsing resume: {str(e)}'
        })


@job_application_bp.route('/submit-application', methods=['POST'])
@csrf.exempt
def submit_application():
    """Submit job application form"""
    try:
        logger.info("=== FORM SUBMISSION DEBUG ===")
        logger.info(f"Form data keys: {list(request.form.keys())}")
        for key, value in request.form.items():
            logger.info(f"Form field '{key}': '{value}'")
        logger.info(f"Files: {list(request.files.keys())}")
        logger.info("===========================")

        application_data = {
            'firstName': request.form.get('firstName'),
            'lastName': request.form.get('lastName'),
            'email': request.form.get('email'),
            'phone': request.form.get('phone'),
            'jobId': request.form.get('jobId'),
            'jobTitle': request.form.get('jobTitle'),
            'source': request.form.get('source', '')
        }

        required_fields = ['firstName', 'lastName', 'email', 'phone', 'jobId', 'jobTitle']
        for field in required_fields:
            if not application_data.get(field):
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                })

        resume_file = request.files.get('resume')
        cover_letter_file = request.files.get('coverLetter')

        if not resume_file or resume_file.filename == '':
            return jsonify({
                'success': False,
                'error': 'Resume file is required'
            })

        if not allowed_resume_file(resume_file.filename):
            return jsonify({
                'success': False,
                'error': 'Invalid resume file type. Allowed: PDF, DOC, DOCX, TXT, RTF.'
            }), 400

        from job_application_service import JobApplicationService
        job_app_service = JobApplicationService()
        submission_result = job_app_service.submit_application(
            application_data=application_data,
            resume_file=resume_file,
            cover_letter_file=cover_letter_file if cover_letter_file and cover_letter_file.filename != '' else None,
            request_host=request.host
        )

        return jsonify(submission_result)

    except Exception as e:
        logger.error(f"Error submitting application: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error submitting application: {str(e)}'
        })
