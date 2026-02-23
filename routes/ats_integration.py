"""
ATS Integration routes for JobPulse (Scout Inbound module).

Handles ATS monitoring dashboard, monitor CRUD operations, Bullhorn settings,
OAuth authentication, and related API endpoints.
"""

import json
import os
import logging
import time
import requests
from datetime import datetime
from urllib.parse import urlencode

from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, session, current_app
from flask_login import login_required, current_user


ats_integration_bp = Blueprint('ats_integration', __name__)


@ats_integration_bp.route('/ats-integration')
@login_required
def ats_integration_dashboard():
    """ATS monitoring dashboard"""
    from app import ensure_background_services
    from models import BullhornMonitor, BullhornActivity
    
    # Ensure scheduler is running when accessing the dashboard
    ensure_background_services()
    
    try:
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        recent_activities = BullhornActivity.query.order_by(BullhornActivity.created_at.desc()).limit(50).all()
    except Exception as e:
        current_app.logger.error(f"Error querying database in bullhorn_dashboard: {str(e)}")
        monitors = []
        recent_activities = []
    
    # Read job counts from cached last_job_snapshot (maintained by background scheduler)
    # This avoids synchronous Bullhorn API calls that previously blocked page load for 20-65s
    monitor_job_counts = {}
    for monitor in monitors:
        if monitor.last_job_snapshot:
            try:
                stored_jobs = json.loads(monitor.last_job_snapshot)
                monitor_job_counts[monitor.id] = len(stored_jobs)
            except (json.JSONDecodeError, TypeError):
                monitor_job_counts[monitor.id] = None
        else:
            monitor_job_counts[monitor.id] = None
    
    # Derive connection status from recent successful activity
    # instead of a live test_connection() call (saves ~2-5s OAuth roundtrip)
    bullhorn_connected = False
    try:
        if recent_activities:
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(hours=2)
            bullhorn_connected = any(
                a.created_at > cutoff and a.activity_type != 'error'
                for a in recent_activities
            )
    except Exception as e:
        current_app.logger.info(f"Bullhorn connection status check failed: {str(e)}")
    
    return render_template('ats_integration.html', 
                         monitors=monitors, 
                         recent_activities=recent_activities,
                         bullhorn_connected=bullhorn_connected,
                         monitor_job_counts=monitor_job_counts,
                         active_page='ats')


@ats_integration_bp.route('/test-ats-integration')
@login_required
def test_ats_integration_page():
    """Test page for ATS integration dashboard"""
    try:
        return render_template('ats_integration.html', 
                             monitors=[], 
                             recent_activities=[],
                             bullhorn_connected=False,
                             monitor_job_counts={})
    except Exception as e:
        return f"Error rendering template: {str(e)}", 500


@ats_integration_bp.route('/ats-integration/create', methods=['GET', 'POST'])
@login_required
def create_ats_monitor():
    """Create a new Bullhorn monitor"""
    from app import db, get_bullhorn_service
    from models import BullhornMonitor
    
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            monitor_type = request.form.get('monitor_type')
            check_interval = int(request.form.get('check_interval_minutes', 60))
            notification_email = request.form.get('notification_email', '').strip()
            send_notifications = 'send_notifications' in request.form
            
            if not name or not monitor_type:
                flash('Name and Monitor Type are required', 'error')
                return redirect(url_for('ats_integration.create_ats_monitor'))
            
            if monitor_type == 'tearsheet':
                tearsheet_id = request.form.get('tearsheet_id') or request.form.get('manual_tearsheet_id')
                
                if not tearsheet_id:
                    flash('Please select a tearsheet from the dropdown or enter a tearsheet ID manually', 'error')
                    return redirect(url_for('ats_integration.create_ats_monitor'))
                
                try:
                    bullhorn_service = get_bullhorn_service()
                    
                    if bullhorn_service.authenticate():
                        url = f"{bullhorn_service.base_url}entity/Tearsheet/{tearsheet_id}"
                        params = {
                            'fields': 'id,name,description',
                            'BhRestToken': bullhorn_service.rest_token
                        }
                        response = bullhorn_service.session.get(url, params=params, timeout=5)
                        
                        if response.status_code == 200:
                            data = response.json()
                            tearsheet = data.get('data', {})
                            tearsheet_name = tearsheet.get('name', f"Tearsheet {tearsheet_id}")
                        else:
                            tearsheet_name = f"Tearsheet {tearsheet_id}"
                    else:
                        tearsheet_name = f"Tearsheet {tearsheet_id}"
                        
                except Exception:
                    tearsheet_name = f"Tearsheet {tearsheet_id}"
                
                monitor = BullhornMonitor(
                    name=name,
                    tearsheet_id=int(tearsheet_id),
                    tearsheet_name=tearsheet_name,
                    check_interval_minutes=check_interval,
                    notification_email=notification_email if notification_email else None,
                    send_notifications=send_notifications,
                    next_check=datetime.utcnow()
                )
                
                flash(f'Monitor "{name}" created successfully for tearsheet: {tearsheet_name}', 'success')
            
            elif monitor_type == 'query':
                job_search_query = request.form.get('job_search_query', '').strip()
                if not job_search_query:
                    flash('Job Search Query is required', 'error')
                    return redirect(url_for('ats_integration.create_ats_monitor'))
                
                monitor = BullhornMonitor(
                    name=name,
                    tearsheet_id=0,
                    tearsheet_name=job_search_query,
                    check_interval_minutes=check_interval,
                    notification_email=notification_email if notification_email else None,
                    send_notifications=send_notifications,
                    next_check=datetime.utcnow()
                )
                
                flash(f'Monitor "{name}" created successfully with search query: {job_search_query}', 'success')
            
            else:
                flash('Invalid monitor type', 'error')
                return redirect(url_for('ats_integration.create_ats_monitor'))
            
            db.session.add(monitor)
            db.session.commit()
            
            return redirect(url_for('ats_integration.ats_integration_dashboard'))
            
        except Exception as e:
            flash(f'Error creating monitor: {str(e)}', 'error')
            return redirect(url_for('ats_integration.create_ats_monitor'))
    
    # GET request - provide tearsheet options
    tearsheets = []
    
    try:
        bullhorn_service = get_bullhorn_service()
        
        known_ids = [1, 2, 3, 4, 5, 10, 20, 50, 100]
        
        for ts_id in known_ids:
            try:
                url = f"{bullhorn_service.base_url}entity/Tearsheet/{ts_id}"
                if bullhorn_service.base_url and bullhorn_service.rest_token:
                    params = {
                        'fields': 'id,name,description',
                        'BhRestToken': bullhorn_service.rest_token
                    }
                    response = bullhorn_service.session.get(url, params=params, timeout=3)
                    
                    if response.status_code == 200:
                        data = response.json()
                        tearsheet = data.get('data', {})
                        if tearsheet and tearsheet.get('name'):
                            tearsheets.append(tearsheet)
                            
            except Exception:
                continue
            
    except Exception as e:
        flash('Could not connect to Bullhorn. Please check your API credentials.', 'error')
        
    return render_template('ats_integration_create.html', tearsheets=tearsheets)


@ats_integration_bp.route('/ats-integration/monitor/<int:monitor_id>')
@login_required
def ats_monitor_details(monitor_id):
    """View details of a specific Bullhorn monitor"""
    from app import get_bullhorn_service
    from models import BullhornMonitor, BullhornActivity
    
    monitor = BullhornMonitor.query.get_or_404(monitor_id)
    activities = BullhornActivity.query.filter_by(monitor_id=monitor_id).order_by(BullhornActivity.created_at.desc()).limit(50).all()
    
    current_job_count = None
    try:
        bullhorn_service = get_bullhorn_service()
        
        if bullhorn_service.test_connection():
            if monitor.tearsheet_id == 0:
                current_jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
            else:
                current_jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
            
            current_job_count = len(current_jobs)
        else:
            current_app.logger.warning(f"Could not connect to Bullhorn to get job count for monitor {monitor.name}")
            
    except Exception as e:
        current_app.logger.error(f"Error getting job count for monitor {monitor.name}: {str(e)}")
    
    return render_template('ats_integration_details.html', 
                         monitor=monitor, 
                         activities=activities,
                         current_job_count=current_job_count)


@ats_integration_bp.route('/ats-integration/monitor/<int:monitor_id>/delete', methods=['POST'])
@login_required
def delete_ats_monitor(monitor_id):
    """Delete a Bullhorn monitor"""
    from app import db
    from models import BullhornMonitor
    
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        monitor_name = monitor.name
        
        monitor.is_active = False
        db.session.commit()
        
        flash(f'Monitor "{monitor_name}" deleted successfully', 'success')
        
    except Exception as e:
        current_app.logger.error(f"Error deleting Bullhorn monitor: {str(e)}")
        flash(f'Error deleting monitor: {str(e)}', 'error')
    
    return redirect(url_for('ats_integration.ats_integration_dashboard'))


@ats_integration_bp.route('/ats-integration/monitor/<int:monitor_id>/test', methods=['POST'])
@login_required
def test_ats_monitor(monitor_id):
    """Test a Bullhorn monitor manually"""
    from app import get_bullhorn_service
    from models import BullhornMonitor
    
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        
        bullhorn_service = get_bullhorn_service()
        
        if not bullhorn_service.test_connection():
            return jsonify({
                'success': False,
                'message': 'Failed to connect to Bullhorn API. Check your credentials in Global Settings.'
            })
        
        if monitor.tearsheet_id == 0:
            jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
            message = f'Successfully connected to Bullhorn. Found {len(jobs)} jobs matching query: {monitor.tearsheet_name}'
        else:
            jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
            message = f'Successfully connected to Bullhorn. Found {len(jobs)} jobs in tearsheet {monitor.tearsheet_id}.'
        
        return jsonify({
            'success': True,
            'message': message,
            'job_count': len(jobs)
        })
        
    except Exception as e:
        current_app.logger.error(f"Error testing Bullhorn monitor: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        })


@ats_integration_bp.route('/api/ats-integration/monitor/<int:monitor_id>/jobs')
@login_required
def get_monitor_jobs(monitor_id):
    """Get current jobs from Bullhorn for a specific monitor (filtered by eligibility)"""
    from app import get_bullhorn_service
    from models import BullhornMonitor
    
    # Statuses that indicate a job should NOT be in the sponsored job feed
    INELIGIBLE_STATUSES = {
        'qualifying', 'hold - covered', 'hold - client hold', 'offer out',
        'filled', 'lost - competition', 'lost - filled internally',
        'lost - funding', 'canceled', 'placeholder/ mpc', 'archive'
    }
    
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        
        bullhorn_service = get_bullhorn_service()
        
        connection_test = bullhorn_service.test_connection()
        if not connection_test:
            current_app.logger.warning(f"Bullhorn connection failed for monitor {monitor_id}")
            return jsonify({
                'success': False,
                'error': 'Authentication failed: Unable to connect to Bullhorn API. Please refresh the page and try again.'
            })
        
        jobs = []
        try:
            if monitor.tearsheet_id == 0:
                jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
            else:
                jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
        except Exception as api_error:
            current_app.logger.error(f"Bullhorn API error for monitor {monitor_id}: {str(api_error)}")
            error_msg = str(api_error).lower()
            if 'auth' in error_msg or 'token' in error_msg or 'login' in error_msg or 'unauthorized' in error_msg:
                return jsonify({
                    'success': False,
                    'error': 'Authentication expired. Please refresh the page and try again.'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': f'API Error: {str(api_error)}'
                })
        
        formatted_jobs = []
        filtered_count = 0
        for job in jobs:
            # Filter out ineligible jobs (Archive, Filled, Canceled, etc.)
            status = job.get('status', '').strip()
            is_open = job.get('isOpen')
            
            if is_open == False or str(is_open).lower() in ('closed', 'false'):
                filtered_count += 1
                continue
            if status.lower() in INELIGIBLE_STATUSES:
                filtered_count += 1
                continue
            
            formatted_job = {
                'id': job.get('id'),
                'title': job.get('title', 'No Title'),
                'city': job.get('address', {}).get('city', '') if job.get('address') else '',
                'state': job.get('address', {}).get('state', '') if job.get('address') else '',
                'country': job.get('address', {}).get('countryName', '') if job.get('address') else '',
                'employmentType': job.get('employmentType', ''),
                'onSite': job.get('onSite', ''),
                'status': status,
                'isPublic': job.get('isPublic', False),
                'dateLastModified': job.get('dateLastModified', ''),
                'owner': job.get('owner', {}).get('firstName', '') + ' ' + job.get('owner', {}).get('lastName', '') if job.get('owner') else ''
            }
            formatted_jobs.append(formatted_job)
        
        if filtered_count > 0:
            current_app.logger.info(f"Monitor {monitor.name}: filtered {filtered_count} ineligible jobs from display")
        
        formatted_jobs.sort(key=lambda x: int(x['id']) if x['id'] else 0, reverse=True)
        
        return jsonify({
            'success': True,
            'jobs': formatted_jobs,
            'total_count': len(formatted_jobs),
            'filtered_count': filtered_count,
            'monitor_name': monitor.name,
            'monitor_type': 'Query' if monitor.tearsheet_id == 0 else 'Tearsheet'
        })
        
    except Exception as e:
        current_app.logger.error(f"Error fetching jobs for monitor {monitor_id}: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error fetching jobs: {str(e)}'
        })


@ats_integration_bp.route('/ats-integration/monitor/<int:monitor_id>/test-email', methods=['POST'])
@login_required
def test_email_notification(monitor_id):
    """Send a test email notification"""
    from app import get_email_service
    from models import BullhornMonitor, GlobalSettings
    
    try:
        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        
        email_address = monitor.notification_email
        if not email_address:
            global_email = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
            if global_email:
                email_address = global_email.setting_value
        
        if not email_address:
            return jsonify({
                'success': False,
                'message': 'No notification email configured. Please set an email address in Global Settings or the monitor settings.'
            })
        
        sample_added_jobs = [
            {'id': 12345, 'title': 'Senior Software Engineer', 'status': 'Open', 'clientCorporation': {'name': 'Tech Innovators Inc.'}},
            {'id': 12346, 'title': 'Data Analyst', 'status': 'Open', 'clientCorporation': {'name': 'Analytics Solutions Corp.'}}
        ]
        
        sample_removed_jobs = [
            {'id': 11111, 'title': 'Marketing Coordinator', 'status': 'Closed', 'clientCorporation': {'name': 'Creative Agency Ltd.'}}
        ]
        
        sample_modified_jobs = [
            {'id': 11223, 'title': 'Full Stack Developer', 'status': 'Open', 'clientCorporation': {'name': 'StartupXYZ'},
             'changes': [{'field': 'title', 'from': 'Junior Full Stack Developer', 'to': 'Full Stack Developer'},
                        {'field': 'status', 'from': 'Pending', 'to': 'Open'}]}
        ]
        
        sample_summary = {
            'total_previous': 8, 'total_current': 10, 'added_count': 2,
            'removed_count': 1, 'modified_count': 1, 'net_change': 2
        }
        
        email_service = get_email_service()
        email_sent = email_service.send_bullhorn_notification(
            to_email=email_address,
            monitor_name=f"{monitor.name} [TEST EMAIL]",
            added_jobs=sample_added_jobs,
            removed_jobs=sample_removed_jobs,
            modified_jobs=sample_modified_jobs,
            summary=sample_summary
        )
        
        if email_sent:
            return jsonify({
                'success': True,
                'message': f'Test email notification sent successfully to {email_address}. Check your inbox to see what real notifications will look like.'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to send test email. Please check your email configuration in Global Settings.'
            })
        
    except Exception as e:
        current_app.logger.error(f"Error sending test email notification: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        })


@ats_integration_bp.route('/ats-integration/settings', methods=['GET', 'POST'])
@login_required
def ats_integration_settings():
    """Manage Bullhorn API credentials in Global Settings"""
    from app import db
    from models import GlobalSettings
    from bullhorn_service import BullhornService
    
    if request.method == 'POST':
        if request.form.get('action') == 'test':
            try:
                credentials = {}
                for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
                    setting = GlobalSettings.query.filter_by(setting_key=key).first()
                    credentials[key] = setting.setting_value if setting else ''
                
                bullhorn_service = BullhornService(
                    client_id=credentials.get('bullhorn_client_id'),
                    client_secret=credentials.get('bullhorn_client_secret'),
                    username=credentials.get('bullhorn_username'),
                    password=credentials.get('bullhorn_password')
                )
                
                result = bullhorn_service.test_connection()
                
                if result:
                    flash('Successfully connected to Bullhorn API', 'success')
                else:
                    if not credentials.get('bullhorn_client_id') or not credentials.get('bullhorn_username'):
                        flash('Missing Bullhorn credentials. Please save your credentials first.', 'error')
                    else:
                        flash('Failed to connect to Bullhorn API. Please check your credentials.', 'error')
            except Exception as e:
                flash(f'Connection test failed: {str(e)}', 'error')
            
            return redirect(url_for('ats_integration.ats_integration_settings'))
        
        elif request.form.get('action') == 'save' or not request.form.get('action'):
            try:
                settings_to_update = [
                    ('bullhorn_client_id', request.form.get('bullhorn_client_id', '')),
                    ('bullhorn_client_secret', request.form.get('bullhorn_client_secret', '')),
                    ('bullhorn_username', request.form.get('bullhorn_username', '')),
                    ('bullhorn_password', request.form.get('bullhorn_password', '')),
                ]
                
                for key, value in settings_to_update:
                    setting = GlobalSettings.query.filter_by(setting_key=key).first()
                    if setting:
                        setting.setting_value = value
                    else:
                        setting = GlobalSettings(setting_key=key, setting_value=value)
                        db.session.add(setting)
                
                db.session.commit()
                flash('Bullhorn settings updated successfully', 'success')
                
            except Exception as e:
                flash(f'Error updating settings: {str(e)}', 'error')
            
            return redirect(url_for('ats_integration.ats_integration_settings'))
    
    # GET - return current settings
    settings = {}
    for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
        setting = GlobalSettings.query.filter_by(setting_key=key).first()
        settings[key] = setting.setting_value if setting else ''
    
    return render_template('ats_integration_settings.html', settings=settings)


@ats_integration_bp.route('/api/ats-integration/connection-test', methods=['POST'])
@login_required
def api_ats_connection_test():
    """API endpoint to test Bullhorn connection and show current API mode"""
    from app import get_bullhorn_service
    from bullhorn_service import BullhornService
    
    try:
        bullhorn_service = get_bullhorn_service()
        
        if not bullhorn_service:
            return jsonify({
                'connection_status': 'failed',
                'api_mode': 'unknown',
                'message': 'Bullhorn credentials not configured',
                'endpoints': {}
            }), 400
        
        use_new_api = os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true'
        api_mode = 'bullhorn_one' if use_new_api else 'legacy'
        
        endpoints = {
            'auth_url': BullhornService.BULLHORN_ONE_AUTH_URL if use_new_api else 'Dynamic (loginInfo)',
            'token_url': BullhornService.BULLHORN_ONE_TOKEN_URL if use_new_api else 'Dynamic (loginInfo)',
            'rest_url': BullhornService.BULLHORN_ONE_REST_URL if use_new_api else 'Dynamic (loginInfo)'
        }
        
        connection_result = bullhorn_service.test_connection()
        
        if connection_result:
            return jsonify({
                'connection_status': 'success',
                'api_mode': api_mode,
                'message': f'Successfully connected to Bullhorn using {api_mode} API',
                'endpoints': endpoints
            })
        else:
            return jsonify({
                'connection_status': 'failed',
                'api_mode': api_mode,
                'message': f'Failed to connect to Bullhorn {api_mode} API. Check credentials.',
                'endpoints': endpoints
            })
            
    except Exception as e:
        current_app.logger.error(f"Bullhorn connection test error: {str(e)}")
        return jsonify({
            'connection_status': 'error',
            'api_mode': 'unknown',
            'message': f'Error: {str(e)}',
            'endpoints': {}
        }), 500


@ats_integration_bp.route('/ats-integration/oauth/start')
@login_required
def oauth_start():
    """Start OAuth flow with CSRF protection"""
    from models import GlobalSettings
    from bullhorn_service import BullhornService
    import secrets
    
    try:
        client_id_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_id').first()
        client_secret_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_secret').first()
        
        if not all([client_id_setting, client_secret_setting]):
            flash('Bullhorn OAuth credentials not configured. Please configure Client ID and Client Secret first.', 'error')
            return redirect(url_for('ats_integration.ats_integration_settings'))
        
        use_new_api = os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true'
        
        if use_new_api:
            auth_endpoint = BullhornService.BULLHORN_ONE_AUTH_URL
            logging.info(f"Using Bullhorn One auth endpoint: {auth_endpoint}")
        else:
            login_info_url = "https://rest.bullhornstaffing.com/rest-services/loginInfo"
            login_info_params = {'username': 'oauth'}
            
            response = requests.get(login_info_url, params=login_info_params, timeout=30)
            if response.status_code != 200:
                flash('Failed to get Bullhorn login info. Please try again.', 'error')
                return redirect(url_for('ats_integration.ats_integration_settings'))
            
            login_data = response.json()
            oauth_url = login_data.get('oauthUrl')
            
            if not oauth_url:
                flash('Invalid login info response from Bullhorn', 'error')
                return redirect(url_for('ats_integration.ats_integration_settings'))
            
            auth_endpoint = f"{oauth_url}/authorize"
        
        state = secrets.token_urlsafe(32)
        session['oauth_state'] = state
        session['oauth_timestamp'] = int(time.time())
        
        base_url = os.environ.get('OAUTH_REDIRECT_BASE_URL', "https://app.scoutgenius.ai").strip()
        redirect_uri = f"{base_url}/ats-integration/oauth/callback"
        
        logging.info(f"OAuth redirect_uri: {redirect_uri}")
        logging.info(f"OAuth client_id: {client_id_setting.setting_value}")
        
        auth_params = {
            'client_id': client_id_setting.setting_value,
            'response_type': 'code',
            'redirect_uri': redirect_uri,
            'state': state
        }
        
        auth_url = f"{auth_endpoint}?{urlencode(auth_params)}"
        
        logging.info(f"OAuth full auth_url: {auth_url}")
        logging.info(f"Starting OAuth with state: {state[:10]}...")
        return redirect(auth_url)
        
    except Exception as e:
        logging.error(f"OAuth start error: {str(e)}")
        flash(f'Error starting OAuth flow: {str(e)}', 'error')
        return redirect(url_for('ats_integration.ats_integration_settings'))


@ats_integration_bp.route('/ats-integration/oauth/callback')
def oauth_callback():
    """Handle Bullhorn OAuth callback and exchange authorization code for tokens"""
    from models import GlobalSettings
    from bullhorn_service import BullhornService
    
    try:
        code = request.args.get('code')
        error = request.args.get('error')
        state = request.args.get('state')
        
        if error:
            flash(f'Bullhorn OAuth authorization failed: {error}', 'error')
            return redirect(url_for('ats_integration.ats_integration_settings'))
        
        if not code:
            flash('OAuth callback received but no authorization code found', 'warning')
            return redirect(url_for('ats_integration.ats_integration_settings'))
        
        stored_state = session.get('oauth_state')
        stored_timestamp = session.get('oauth_timestamp', 0)
        
        if 'oauth_state' in session:
            del session['oauth_state']
        if 'oauth_timestamp' in session:
            del session['oauth_timestamp']
        
        if not stored_state or not state:
            flash('OAuth state validation failed - possible CSRF attack. Please try again.', 'error')
            logging.error("OAuth CSRF validation failed - missing state")
            return redirect(url_for('ats_integration.ats_integration_settings'))
        
        if stored_state != state:
            flash('OAuth state validation failed - possible CSRF attack. Please try again.', 'error')
            logging.error(f"OAuth CSRF validation failed - state mismatch: expected {stored_state[:10]}..., got {state[:10]}...")
            return redirect(url_for('ats_integration.ats_integration_settings'))
        
        if int(time.time()) - stored_timestamp > 300:
            flash('OAuth session expired. Please try again.', 'warning')
            logging.warning("OAuth state expired")
            return redirect(url_for('ats_integration.ats_integration_settings'))
        
        logging.info(f"✅ OAuth callback received with valid state - code: {code[:10]}...")
        
        try:
            client_id_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_id').first()
            client_secret_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_secret').first()
            
            if not all([client_id_setting, client_secret_setting]):
                flash('Bullhorn credentials not configured. Please update settings first.', 'error')
                return redirect(url_for('ats_integration.ats_integration_settings'))
            
            use_new_api = os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true'
            
            if use_new_api:
                token_endpoint = BullhornService.BULLHORN_ONE_TOKEN_URL
                rest_login_url = BullhornService.BULLHORN_ONE_REST_LOGIN_URL
                rest_api_url = BullhornService.BULLHORN_ONE_REST_URL
                logging.info(f"OAuth callback using Bullhorn One endpoints: token={token_endpoint}, login={rest_login_url}")
            else:
                login_info_url = "https://rest.bullhornstaffing.com/rest-services/loginInfo"
                login_info_params = {'username': 'oauth'}
                
                response = requests.get(login_info_url, params=login_info_params, timeout=30)
                if response.status_code != 200:
                    flash('Failed to get Bullhorn login info. Please try again.', 'error')
                    return redirect(url_for('ats_integration.ats_integration_settings'))
                
                login_data = response.json()
                oauth_url = login_data.get('oauthUrl')
                rest_url = login_data.get('restUrl')
                
                if not oauth_url:
                    flash('Invalid login info response from Bullhorn', 'error')
                    return redirect(url_for('ats_integration.ats_integration_settings'))
                
                token_endpoint = f"{oauth_url}/token"
            
            base_url_env = os.environ.get('OAUTH_REDIRECT_BASE_URL', "https://app.scoutgenius.ai").strip()
            redirect_uri = f"{base_url_env}/ats-integration/oauth/callback"
            
            token_data = {
                'grant_type': 'authorization_code',
                'code': code,
                'client_id': client_id_setting.setting_value,
                'client_secret': client_secret_setting.setting_value,
                'redirect_uri': redirect_uri
            }
            
            logging.info(f"Token exchange with redirect_uri: {redirect_uri}")
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            
            token_response = requests.post(token_endpoint, data=token_data, headers=headers, timeout=30)
            if token_response.status_code != 200:
                logging.error(f"Token exchange failed: HTTP {token_response.status_code}")
                flash(f'Failed to exchange authorization code for tokens (HTTP {token_response.status_code})', 'error')
                return redirect(url_for('ats_integration.ats_integration_settings'))
            
            token_info = token_response.json()
            access_token = token_info.get('access_token')
            
            if not access_token:
                flash('No access token received from Bullhorn', 'error')
                return redirect(url_for('ats_integration.ats_integration_settings'))
            
            if use_new_api:
                rest_login_endpoint = rest_login_url
            else:
                rest_login_endpoint = f"{rest_url}/login"
            
            rest_params = {
                'version': '2.0',
                'access_token': access_token
            }
            
            logging.info(f"REST login request to: {rest_login_endpoint}")
            rest_response = requests.post(rest_login_endpoint, params=rest_params, timeout=30)
            if rest_response.status_code != 200:
                logging.error(f"REST login failed: HTTP {rest_response.status_code}")
                flash('Failed to get REST token for API access', 'error')
                return redirect(url_for('ats_integration.ats_integration_settings'))
            
            rest_data = rest_response.json()
            rest_token = rest_data.get('BhRestToken')
            if use_new_api:
                base_url_api = rest_data.get('restUrl', rest_api_url)
            else:
                base_url_api = rest_data.get('restUrl', rest_url)
            
            if not rest_token:
                flash('No REST token received from Bullhorn', 'error')
                return redirect(url_for('ats_integration.ats_integration_settings'))
            
            flash('✅ Bullhorn OAuth authentication completed successfully! Terms of Service accepted and connection established.', 'success')
            logging.info(f"✅ Complete OAuth flow successful - REST Token: ***{rest_token[-4:]}, Base URL: {base_url_api}")
            
            try:
                test_url = f"{base_url_api}/search/JobOrder?query=id>0&count=1&fields=id"
                test_response = requests.get(test_url, params={'BhRestToken': rest_token}, timeout=15)
                if test_response.status_code == 200:
                    flash('✅ API connection test passed - ready for data migration!', 'success')
                    logging.info("✅ API test call successful")
                else:
                    flash('⚠️ Authentication successful but API test failed. Connection may still work.', 'warning')
                    logging.warning(f"API test failed: {test_response.status_code}")
            except Exception as test_error:
                logging.warning(f"API test error (not critical): {str(test_error)}")
                flash('⚠️ Authentication successful but couldn\'t verify API access. Connection should still work.', 'warning')
                
        except requests.exceptions.RequestException as req_error:
            logging.error(f"Network error during OAuth token exchange: {str(req_error)}")
            flash(f'Network error during authentication: {str(req_error)}', 'error')
        except Exception as auth_error:
            logging.error(f"Error during OAuth token exchange: {str(auth_error)}")
            flash(f'Error completing authentication: {str(auth_error)}', 'error')
            
        return redirect(url_for('ats_integration.ats_integration_settings'))
        
    except Exception as e:
        logging.error(f"OAuth callback error: {str(e)}")
        flash(f'OAuth callback error: {str(e)}', 'error')
        return redirect(url_for('ats_integration.ats_integration_settings'))


@ats_integration_bp.route('/api/ats-integration/activities')
@login_required
def get_recent_activities():
    """Get recent Bullhorn activities for auto-refresh"""
    from models import BullhornActivity
    
    recent_activities = BullhornActivity.query.order_by(BullhornActivity.created_at.desc()).limit(50).all()
    
    activities_data = []
    for activity in recent_activities:
        activities_data.append({
            'id': activity.id,
            'monitor_name': activity.monitor.name if activity.monitor else 'Scheduled Processing',
            'monitor_id': activity.monitor.id if activity.monitor else None,
            'activity_type': activity.activity_type,
            'job_id': activity.job_id,
            'job_title': activity.job_title,
            'account_manager': activity.account_manager,
            'details': activity.details,
            'notification_sent': activity.notification_sent,
            'created_at': activity.created_at.strftime('%Y-%m-%d %H:%M')
        })
    
    return jsonify({
        'success': True,
        'activities': activities_data,
        'timestamp': datetime.utcnow().isoformat()
    })


@ats_integration_bp.route('/api/ats-integration/monitors')
@login_required
def get_monitor_status():
    """Get updated monitor information for auto-refresh"""
    from models import BullhornMonitor
    
    monitors = BullhornMonitor.query.filter_by(is_active=True).order_by(BullhornMonitor.name).all()
    current_time = datetime.utcnow()
    
    monitors_data = []
    for monitor in monitors:
        job_count = 0
        if monitor.last_job_snapshot:
            try:
                jobs = json.loads(monitor.last_job_snapshot)
                job_count = len(jobs)
            except:
                job_count = 0
        
        is_overdue = False
        overdue_minutes = 0
        if monitor.next_check and monitor.next_check < current_time:
            overdue_minutes = int((current_time - monitor.next_check).total_seconds() / 60)
            is_overdue = overdue_minutes > 10
        
        monitors_data.append({
            'id': monitor.id,
            'name': monitor.name,
            'last_check': monitor.last_check.strftime('%Y-%m-%d %H:%M') if monitor.last_check else 'Never',
            'next_check': monitor.next_check.strftime('%Y-%m-%d %H:%M') if monitor.next_check else 'Not scheduled',
            'job_count': job_count,
            'is_active': monitor.is_active,
            'check_interval_minutes': monitor.check_interval_minutes,
            'is_overdue': is_overdue,
            'overdue_minutes': overdue_minutes
        })
    
    return jsonify({
        'success': True,
        'monitors': monitors_data,
        'timestamp': datetime.utcnow().isoformat()
    })


@ats_integration_bp.route('/api/ats-integration/monitoring-cycles', methods=['GET'])
@login_required
def get_monitoring_cycles():
    """Get information about monitoring cycles and timing"""
    from models import BullhornMonitor
    
    try:
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        
        current_time = datetime.utcnow()
        cycle_info = []
        
        for monitor in monitors:
            next_check = monitor.next_check
            if next_check:
                time_until_next = (next_check - current_time).total_seconds()
                is_overdue = time_until_next < 0
                
                cycle_info.append({
                    'monitor_id': monitor.id,
                    'monitor_name': monitor.name,
                    'next_check': next_check.isoformat() + 'Z',
                    'time_until_next_seconds': int(time_until_next),
                    'is_overdue': is_overdue,
                    'overdue_minutes': abs(time_until_next / 60) if is_overdue else 0,
                    'interval_minutes': monitor.check_interval,
                    'last_check': monitor.last_check.isoformat() + 'Z' if monitor.last_check else None
                })
        
        next_global_cycle = None
        if cycle_info:
            next_times = [info for info in cycle_info if not info['is_overdue']]
            if next_times:
                next_global_cycle = min(next_times, key=lambda x: x['time_until_next_seconds'])
        
        return jsonify({
            'success': True,
            'current_time': current_time.isoformat() + 'Z',
            'monitors': cycle_info,
            'next_global_cycle': next_global_cycle,
            'total_active_monitors': len(monitors)
        })
    except Exception as e:
        current_app.logger.error(f"Error getting monitoring cycles: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ats_integration_bp.route('/api/ats-integration/api-status', methods=['GET'])
@login_required
def api_ats_status():
    """Get current Bullhorn API configuration status (no connection test)"""
    from bullhorn_service import BullhornService
    
    use_new_api = os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true'
    api_mode = 'bullhorn_one' if use_new_api else 'legacy'
    
    if use_new_api:
        endpoints = {
            'auth_url': BullhornService.BULLHORN_ONE_AUTH_URL,
            'token_url': BullhornService.BULLHORN_ONE_TOKEN_URL,
            'rest_login_url': BullhornService.BULLHORN_ONE_REST_LOGIN_URL,
            'rest_url': BullhornService.BULLHORN_ONE_REST_URL
        }
    else:
        endpoints = {
            'login_info_url': BullhornService.LEGACY_LOGIN_INFO_URL,
            'note': 'OAuth and REST URLs discovered dynamically from loginInfo endpoint'
        }
    
    return jsonify({
        'api_mode': api_mode,
        'api_mode_display': 'Bullhorn One' if use_new_api else 'Legacy Bullhorn',
        'endpoints': endpoints,
        'toggle_env_var': 'BULLHORN_USE_NEW_API',
        'toggle_current_value': os.environ.get('BULLHORN_USE_NEW_API', 'false'),
        'message': f'Currently configured for {api_mode.replace("_", " ").title()} API'
    })

