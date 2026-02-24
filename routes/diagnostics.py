import json
import logging
import os
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_login import login_required
from extensions import db

logger = logging.getLogger(__name__)
diagnostics_bp = Blueprint('diagnostics', __name__)


@diagnostics_bp.route('/test-reference-refresh-notification')
@login_required
def test_reference_refresh_notification():
    """Test the reference number refresh notification system"""
    try:
        from models import GlobalSettings
        from utils.bullhorn_helpers import get_email_service

        email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
        email_address = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()

        if not (email_enabled and email_enabled.setting_value == 'true' and
                email_address and email_address.setting_value):
            return jsonify({
                'success': False,
                'error': 'Email notifications not configured in Global Settings'
            })

        email_service = get_email_service()

        test_refresh_details = {
            'jobs_refreshed': 53,
            'jobs_preserved': 0,
            'upload_status': 'successful',
            'processing_time': 12.34,
            'next_run': '2025-08-24 22:15 UTC'
        }

        notification_sent = email_service.send_reference_number_refresh_notification(
            to_email=email_address.setting_value,
            schedule_name='Test Master Job Feed',
            total_jobs=53,
            refresh_details=test_refresh_details,
            status='success'
        )

        if notification_sent:
            logger.info(f"📧 Test reference number refresh notification sent to {email_address.setting_value}")
            return jsonify({
                'success': True,
                'message': f'Test notification sent successfully to {email_address.setting_value}',
                'details': test_refresh_details
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to send test notification'
            })

    except Exception as e:
        logger.error(f"Error testing reference refresh notification: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@diagnostics_bp.route('/api/diagnostic/automation-status')
@login_required
def diagnostic_automation_status():
    """Diagnostic endpoint to check automation configuration and state"""
    try:
        from models import GlobalSettings

        automated_uploads = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
        sftp_enabled_setting = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()

        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()

        current_env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'development').lower()

        is_production = os.environ.get('REPLIT_DEPLOYMENT') is not None

        upload_filename = "myticas-job-feed-v2.xml" if current_env == 'production' else "myticas-job-feed-v2-dev.xml"

        diagnostic_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'environment_detection': {
                'APP_ENV': os.environ.get('APP_ENV'),
                'ENVIRONMENT': os.environ.get('ENVIRONMENT'),
                'REPLIT_DEPLOYMENT': 'present' if is_production else 'missing',
                'detected_environment': current_env,
                'is_production': is_production
            },
            'database_toggles': {
                'automated_uploads_enabled': automated_uploads.setting_value if automated_uploads else 'NOT_FOUND',
                'sftp_enabled': sftp_enabled_setting.setting_value if sftp_enabled_setting else 'NOT_FOUND'
            },
            'sftp_config': {
                'hostname': sftp_hostname.setting_value if sftp_hostname else 'NOT_FOUND',
                'username': sftp_username.setting_value if sftp_username else 'NOT_FOUND',
                'port': sftp_port.setting_value if sftp_port else 'NOT_FOUND'
            },
            'upload_behavior': {
                'target_filename': upload_filename,
                'automation_will_run': (
                    automated_uploads and automated_uploads.setting_value == 'true' and
                    sftp_enabled_setting and sftp_enabled_setting.setting_value == 'true'
                )
            }
        }

        return jsonify(diagnostic_data)

    except Exception as e:
        return jsonify({
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        }), 500


@diagnostics_bp.route('/api/candidates/check-duplicates', methods=['GET'])
@login_required
def api_check_duplicate_notes():
    """
    Query Bullhorn for candidates with duplicate AI Vetting notes.
    Returns sample candidate IDs for manual verification.
    """
    from candidate_vetting_service import CandidateVettingService

    try:
        sample_size = request.args.get('sample_size', 5, type=int)
        sample_size = min(sample_size, 20)

        vetting_service = CandidateVettingService()
        results = vetting_service.get_candidates_with_duplicates(sample_size=sample_size)

        return jsonify({
            'success': True,
            'total_checked': results.get('total_checked', 0),
            'candidates_with_duplicates': results.get('candidates_with_duplicates', []),
            'errors': results.get('errors', [])
        })

    except Exception as e:
        logger.error(f"Error checking duplicate notes: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
