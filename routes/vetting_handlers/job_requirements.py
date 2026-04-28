"""Per-job custom requirements / threshold / AI re-extraction endpoints."""
import threading
from datetime import datetime

from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_login import login_required

from routes.vetting import vetting_bp
from routes.vetting_handlers._shared import get_db


@vetting_bp.route('/screening/job/<int:job_id>/requirements', methods=['POST'])
@login_required
def save_job_requirements(job_id):
    """Save custom requirements for a job"""
    from models import JobVettingRequirements

    db = get_db()

    try:
        # Support both JSON and form data
        if request.is_json:
            data = request.get_json()
            custom_requirements = (data.get('custom_requirements') or '').strip()
            vetting_threshold = data.get('threshold') or ''
        else:
            custom_requirements = request.form.get('custom_requirements', '').strip()
            vetting_threshold = request.form.get('vetting_threshold', '').strip()

        job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
        if job_req:
            job_req.custom_requirements = custom_requirements if custom_requirements else None
            if vetting_threshold:
                job_req.vetting_threshold = int(vetting_threshold)
            else:
                job_req.vetting_threshold = None
            job_req.updated_at = datetime.utcnow()
        else:
            job_req = JobVettingRequirements(
                bullhorn_job_id=job_id,
                custom_requirements=custom_requirements if custom_requirements else None,
                vetting_threshold=int(vetting_threshold) if vetting_threshold else None
            )
            db.session.add(job_req)

        # Audit log
        try:
            import json as _json
            from models import UserActivityLog
            from flask_login import current_user
            db.session.add(UserActivityLog(
                user_id=current_user.id,
                activity_type='config_change',
                ip_address=request.remote_addr,
                details=_json.dumps({
                    'job_id': job_id,
                    'job_title': job_req.job_title or f'Job #{job_id}',
                    'custom_requirements_action': 'set' if custom_requirements else 'cleared',
                    'threshold': int(vetting_threshold) if vetting_threshold else None,
                    'page': 'vetting_settings',
                })
            ))
        except Exception as log_err:
            current_app.logger.warning(f"Failed to write config_change log: {log_err}")

        db.session.commit()

        if request.is_json:
            return jsonify({'success': True, 'message': f'Requirements saved for Job #{job_id}'})

        if custom_requirements:
            flash(f'Custom requirements saved for Job #{job_id}', 'success')
        else:
            flash(f'Custom requirements cleared - using AI interpretation for Job #{job_id}', 'info')

    except Exception as e:
        current_app.logger.error(f"Error saving job requirements: {str(e)}")
        if request.is_json:
            return jsonify({'success': False, 'error': str(e)}), 400
        flash(f'Error saving requirements: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/job/<int:job_id>/threshold', methods=['POST'])
@login_required
def save_job_threshold(job_id):
    """AJAX endpoint to save job-specific vetting threshold"""
    from models import JobVettingRequirements, VettingConfig

    db = get_db()

    try:
        data = request.get_json() if request.is_json else {}
        threshold_value = data.get('threshold')

        if threshold_value is None or threshold_value == '':
            new_threshold = None
        else:
            new_threshold = int(threshold_value)
            if new_threshold < 50 or new_threshold > 100:
                return jsonify({'success': False, 'error': 'Threshold must be between 50 and 100'}), 400

        job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
        if job_req:
            job_req.vetting_threshold = new_threshold
            job_req.updated_at = datetime.utcnow()
        else:
            job_req = JobVettingRequirements(
                bullhorn_job_id=job_id,
                vetting_threshold=new_threshold
            )
            db.session.add(job_req)

        # Audit log
        try:
            import json as _json
            from models import UserActivityLog
            from flask_login import current_user
            db.session.add(UserActivityLog(
                user_id=current_user.id,
                activity_type='config_change',
                ip_address=request.remote_addr,
                details=_json.dumps({
                    'job_id': job_id,
                    'job_title': job_req.job_title or f'Job #{job_id}',
                    'custom_requirements_action': None,
                    'threshold': new_threshold,
                    'page': 'vetting_settings',
                })
            ))
        except Exception as log_err:
            current_app.logger.warning(f"Failed to write config_change log: {log_err}")

        db.session.commit()

        global_threshold = VettingConfig.get_value('match_threshold', '80')
        display_threshold = new_threshold if new_threshold is not None else int(global_threshold)

        return jsonify({
            'success': True,
            'threshold': new_threshold,
            'display_threshold': display_threshold,
            'is_custom': new_threshold is not None
        })

    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid threshold value'}), 400
    except Exception as e:
        current_app.logger.error(f"Error saving job threshold: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@vetting_bp.route('/screening/job/<int:job_id>/refresh-requirements', methods=['POST'])
@login_required
def refresh_job_requirements(job_id):
    """Re-fetch job description from Bullhorn and re-interpret with AI"""
    from models import JobVettingRequirements, GlobalSettings
    from bullhorn_service import BullhornService
    from candidate_vetting_service import CandidateVettingService

    db = get_db()

    try:
        # Get Bullhorn credentials
        credentials = {}
        for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
            setting = GlobalSettings.query.filter_by(setting_key=key).first()
            if setting and setting.setting_value:
                credentials[key.replace('bullhorn_', '')] = setting.setting_value

        if not credentials.get('username') or not credentials.get('password'):
            flash('Bullhorn credentials not configured in settings', 'error')
            return redirect(url_for('vetting.vetting_settings'))

        bullhorn = BullhornService(
            client_id=credentials.get('client_id'),
            client_secret=credentials.get('client_secret'),
            username=credentials.get('username'),
            password=credentials.get('password')
        )
        if not bullhorn.authenticate():
            flash('Failed to authenticate with Bullhorn', 'error')
            return redirect(url_for('vetting.vetting_settings'))

        job_data = bullhorn.get_job_by_id(job_id)
        if not job_data:
            flash(f'Could not find Job #{job_id} in Bullhorn', 'error')
            return redirect(url_for('vetting.vetting_settings'))

        job_title = job_data.get('title', 'Unknown')
        job_description = job_data.get('description', '') or job_data.get('publicDescription', '')

        if not job_description:
            flash(f'Job #{job_id} has no description in Bullhorn', 'warning')
            return redirect(url_for('vetting.vetting_settings'))

        vetting_service = CandidateVettingService()
        extracted_requirements = vetting_service.extract_job_requirements(job_id, job_title, job_description)

        if extracted_requirements:
            job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
            if job_req:
                job_req.ai_interpreted_requirements = extracted_requirements
                job_req.job_title = job_title
                job_req.last_ai_interpretation = datetime.utcnow()
                job_req.updated_at = datetime.utcnow()
            else:
                job_req = JobVettingRequirements(
                    bullhorn_job_id=job_id,
                    job_title=job_title,
                    ai_interpreted_requirements=extracted_requirements,
                    last_ai_interpretation=datetime.utcnow()
                )
                db.session.add(job_req)

            db.session.commit()
            flash(f'Successfully refreshed AI requirements for "{job_title}"', 'success')
        else:
            flash(f'AI could not extract requirements from Job #{job_id} description', 'warning')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error refreshing job requirements: {str(e)}")
        flash(f'Error refreshing requirements: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/sync-requirements', methods=['POST'])
@login_required
def sync_job_requirements():
    """Sync AI requirements with active tearsheet jobs - removes orphaned entries"""
    try:
        from candidate_vetting_service import CandidateVettingService

        vetting_service = CandidateVettingService()
        results = vetting_service.sync_requirements_with_active_jobs()

        if results.get('error'):
            flash(f"Sync aborted: {results['error']}", 'warning')
        elif results['removed'] > 0:
            flash(f"Synced: removed {results['removed']} orphaned requirements (not in active tearsheets). {results['active_jobs']} active jobs remain.", 'success')
        else:
            flash(f"Already in sync! {results['active_jobs']} active jobs in tearsheets.", 'info')

    except Exception as e:
        current_app.logger.error(f"Error syncing requirements: {str(e)}")
        flash(f'Error syncing requirements: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/extract-all-requirements', methods=['POST'])
@login_required
def extract_all_job_requirements():
    """Extract AI requirements for all monitored jobs at once"""
    from models import BullhornMonitor, JobVettingRequirements, GlobalSettings
    from bullhorn_service import BullhornService
    from candidate_vetting_service import CandidateVettingService

    db = get_db()

    try:
        # First, sync to remove orphaned requirements
        vetting_service = CandidateVettingService()
        vetting_service.sync_requirements_with_active_jobs()

        # Get Bullhorn credentials
        credentials = {}
        for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
            setting = GlobalSettings.query.filter_by(setting_key=key).first()
            if setting and setting.setting_value:
                credentials[key.replace('bullhorn_', '')] = setting.setting_value.strip()

        if len(credentials) < 4:
            flash('Bullhorn credentials not fully configured', 'error')
            return redirect(url_for('vetting.vetting_settings'))

        bullhorn = BullhornService(
            client_id=credentials['client_id'],
            client_secret=credentials['client_secret'],
            username=credentials['username'],
            password=credentials['password']
        )

        if not bullhorn.test_connection():
            flash('Failed to connect to Bullhorn', 'error')
            return redirect(url_for('vetting.vetting_settings'))

        vetting_service = CandidateVettingService()

        # Get all active monitors
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()

        all_jobs = []
        location_updates = 0
        for monitor in monitors:
            try:
                if monitor.tearsheet_id == 0:
                    jobs = bullhorn.get_jobs_by_query(monitor.tearsheet_name)
                else:
                    jobs = bullhorn.get_tearsheet_jobs(monitor.tearsheet_id)

                for job in jobs:
                    job_id = int(job.get('id', 0))

                    # Extract location data
                    job_address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
                    job_city = job_address.get('city', '')
                    job_state = job_address.get('state', '')
                    job_country = job_address.get('countryName', '') or job_address.get('country', '')
                    job_location = ', '.join(filter(None, [job_city, job_state, job_country]))

                    # Get work type
                    on_site_value = job.get('onSite', 1)
                    if isinstance(on_site_value, list):
                        on_site_value = on_site_value[0] if on_site_value else 1
                    if isinstance(on_site_value, (int, float)):
                        work_type_map = {1: 'On-site', 2: 'Hybrid', 3: 'Remote'}
                        job_work_type = work_type_map.get(int(on_site_value), 'On-site')
                    else:
                        onsite_str = str(on_site_value).lower().strip() if on_site_value else ''
                        if 'remote' in onsite_str or onsite_str == 'offsite':
                            job_work_type = 'Remote'
                        elif 'hybrid' in onsite_str:
                            job_work_type = 'Hybrid'
                        else:
                            job_work_type = 'On-site'

                    # Check if already has requirements
                    existing = JobVettingRequirements.query.filter_by(
                        bullhorn_job_id=job_id
                    ).first()

                    if existing and existing.ai_interpreted_requirements:
                        # Update location/work_type if needed
                        needs_update = False
                        if not existing.job_location or existing.job_location != job_location:
                            existing.job_location = job_location
                            needs_update = True
                        if not existing.job_work_type or existing.job_work_type != job_work_type:
                            existing.job_work_type = job_work_type
                            needs_update = True
                        if needs_update:
                            db.session.commit()
                            location_updates += 1
                        continue

                    all_jobs.append({
                        'id': job.get('id'),
                        'title': job.get('title', ''),
                        'description': job.get('publicDescription', '') or job.get('description', ''),
                        'location': job_location,
                        'work_type': job_work_type
                    })
            except Exception as e:
                current_app.logger.warning(f"Error fetching jobs from {monitor.name}: {str(e)}")

        if not all_jobs:
            if location_updates > 0:
                flash(f'Updated location data for {location_updates} existing jobs', 'success')
            else:
                flash('All jobs already have requirements extracted', 'info')
            return redirect(url_for('vetting.vetting_settings'))

        # Run AI extraction in a background thread so the HTTP request
        # returns immediately instead of timing out after 76 sequential API calls
        app = current_app._get_current_object()
        jobs_to_process = list(all_jobs)
        jobs_count = len(jobs_to_process)

        def _run_extraction():
            with app.app_context():
                try:
                    svc = CandidateVettingService()
                    results = svc.extract_requirements_for_jobs(jobs_to_process)
                    app.logger.info(
                        f"Background extraction complete — extracted: {results.get('extracted', 0)}, "
                        f"skipped: {results.get('skipped', 0)}, failed: {results.get('failed', 0)}"
                    )
                except Exception as bg_err:
                    app.logger.error(f"Background extraction error: {str(bg_err)}")

        t = threading.Thread(target=_run_extraction, daemon=True)
        t.start()

        location_msg = f" Also updated location data for {location_updates} existing jobs." if location_updates > 0 else ""
        flash(
            f'Extraction started for {jobs_count} jobs — running in the background. '
            f'Refresh in a few minutes to see updated counts.{location_msg}',
            'info'
        )

    except Exception as e:
        current_app.logger.error(f"Error extracting all requirements: {str(e)}")
        flash(f'Error: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))
