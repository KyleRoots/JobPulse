"""
Scout Screening Portal — customer-facing routes.

Provides a focused single-page view for scout_screening users showing:
- AI screening results (match scores, qualification status) for the user's jobs
- Per-job AI-interpreted requirements with user-editable custom overrides
- Match threshold control per job (overrides global admin setting for that job)
"""

import json
import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, jsonify, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from routes import register_module_guard


scout_screening_bp = Blueprint('scout_screening', __name__)
register_module_guard(scout_screening_bp, 'scout_screening')
logger = logging.getLogger(__name__)


def _get_user_job_ids():
    """Return set of Bullhorn job IDs assigned to the current user.
    Mirrors the logic in scout_inbound._get_user_jobs() but returns IDs only.
    Admins and company admins get all job IDs across all monitors.
    """
    from models import BullhornMonitor

    monitors = BullhornMonitor.query.filter_by(is_active=True).all()
    job_ids = set()

    bullhorn_uid = current_user.bullhorn_user_id
    user_display = (current_user.display_name or '').strip().lower()

    for monitor in monitors:
        if not monitor.last_job_snapshot:
            continue
        try:
            jobs = json.loads(monitor.last_job_snapshot)
        except (json.JSONDecodeError, TypeError):
            continue

        for job in jobs:
            job_id = job.get('id')
            if not job_id:
                continue

            if current_user.is_admin or current_user.is_company_admin:
                job_ids.add(job_id)
                continue

            for au in job.get('assignedUsers', []):
                if bullhorn_uid and au.get('id') == bullhorn_uid:
                    job_ids.add(job_id)
                    break
                if user_display:
                    full_name = f"{au.get('firstName', '')} {au.get('lastName', '')}".strip().lower()
                    if full_name and full_name == user_display:
                        job_ids.add(job_id)
                        break

    return job_ids


def _get_user_jobs_with_meta():
    """Return a dict of {job_id: job_dict} for the current user's jobs.
    Used to enrich per-job requirement panels with title/location data.
    """
    from models import BullhornMonitor

    monitors = BullhornMonitor.query.filter_by(is_active=True).all()
    jobs_map = {}

    bullhorn_uid = current_user.bullhorn_user_id
    user_display = (current_user.display_name or '').strip().lower()

    for monitor in monitors:
        if not monitor.last_job_snapshot:
            continue
        try:
            jobs = json.loads(monitor.last_job_snapshot)
        except (json.JSONDecodeError, TypeError):
            continue

        for job in jobs:
            job_id = job.get('id')
            if not job_id or job_id in jobs_map:
                continue

            is_assigned = current_user.is_admin or current_user.is_company_admin
            if not is_assigned:
                for au in job.get('assignedUsers', []):
                    if bullhorn_uid and au.get('id') == bullhorn_uid:
                        is_assigned = True
                        break
                    if user_display:
                        full_name = f"{au.get('firstName', '')} {au.get('lastName', '')}".strip().lower()
                        if full_name and full_name == user_display:
                            is_assigned = True
                            break

            if is_assigned:
                addr = job.get('address', {}) or {}
                location_parts = [p for p in [
                    addr.get('city'), addr.get('state'), addr.get('countryName')
                ] if p]
                jobs_map[job_id] = {
                    'id': job_id,
                    'title': job.get('title', f'Job #{job_id}'),
                    'location': ', '.join(location_parts),
                    'work_type': job.get('onSite', ''),
                    'status': job.get('status', ''),
                    'tearsheet_name': monitor.tearsheet_name or monitor.name,
                }

    return jobs_map


@scout_screening_bp.route('/scout-screening')
@login_required
def dashboard():
    """Scout Screening Portal — AI match results scoped to the user's jobs."""
    from models import CandidateJobMatch, CandidateVettingLog, JobVettingRequirements, VettingConfig

    job_ids = _get_user_job_ids()
    jobs_map = _get_user_jobs_with_meta()

    week_ago = datetime.utcnow() - timedelta(days=7)

    if job_ids:
        matches = (
            CandidateJobMatch.query
            .join(CandidateVettingLog)
            .filter(
                CandidateJobMatch.bullhorn_job_id.in_(job_ids),
                CandidateVettingLog.is_sandbox != True
            )
            .order_by(CandidateJobMatch.created_at.desc())
            .limit(200)
            .all()
        )
        pending_logs = (
            CandidateVettingLog.query
            .filter(
                CandidateVettingLog.applied_job_id.in_(job_ids),
                CandidateVettingLog.status == 'pending',
                CandidateVettingLog.is_sandbox != True
            )
            .order_by(CandidateVettingLog.created_at.desc())
            .limit(50)
            .all()
        )
    else:
        matches = []
        pending_logs = []

    qualified = [m for m in matches if m.is_qualified]
    not_recommended = [m for m in matches if not m.is_qualified and m.match_score > 0]
    screened_this_week = [m for m in matches if m.created_at and m.created_at >= week_ago]

    metrics = {
        'qualified': len(qualified),
        'pending': len(pending_logs),
        'not_recommended': len(not_recommended),
        'screened_this_week': len(screened_this_week),
    }

    job_requirements = {}
    if job_ids:
        reqs = JobVettingRequirements.query.filter(
            JobVettingRequirements.bullhorn_job_id.in_(job_ids)
        ).all()
        job_requirements = {r.bullhorn_job_id: r for r in reqs}

    global_threshold = int(VettingConfig.get_value('match_threshold', '80') or 80)

    return render_template(
        'scout_screening.html',
        matches=matches,
        pending_logs=pending_logs,
        metrics=metrics,
        jobs_map=jobs_map,
        job_requirements=job_requirements,
        global_threshold=global_threshold,
        week_ago=week_ago,
        active_page='screening',
    )


@scout_screening_bp.route('/scout-screening/job/<int:job_id>/save', methods=['POST'])
@login_required
def save_job_settings(job_id):
    """Save custom requirements and/or threshold for one of the user's jobs.
    Enforces ownership — the job must be in the user's assigned job set.
    """
    from extensions import db
    from models import JobVettingRequirements

    job_ids = _get_user_job_ids()
    if job_id not in job_ids:
        flash('You can only edit settings for your own jobs.', 'error')
        return redirect(url_for('scout_screening.dashboard'))

    try:
        custom_requirements = request.form.get('custom_requirements', '').strip() or None
        threshold_raw = request.form.get('vetting_threshold', '').strip()
        vetting_threshold = int(threshold_raw) if threshold_raw else None
        if vetting_threshold is not None and not (50 <= vetting_threshold <= 100):
            flash('Threshold must be between 50 and 100.', 'error')
            return redirect(url_for('scout_screening.dashboard'))

        job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
        if job_req:
            job_req.custom_requirements = custom_requirements
            job_req.vetting_threshold = vetting_threshold
            job_req.updated_at = datetime.utcnow()
        else:
            job_req = JobVettingRequirements(
                bullhorn_job_id=job_id,
                custom_requirements=custom_requirements,
                vetting_threshold=vetting_threshold,
            )
            db.session.add(job_req)

        db.session.commit()
        if request.form.get('_ajax') == '1':
            return ('', 204)
        flash(f'Settings saved for Job #{job_id}.', 'success')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving job settings for job {job_id}: {e}")
        if request.form.get('_ajax') == '1':
            return ('', 500)
        flash(f'Error saving settings: {str(e)}', 'error')

    return redirect(url_for('scout_screening.dashboard'))


@scout_screening_bp.route('/api/scout-screening/stats')
@login_required
def stats_api():
    """Lightweight JSON stats for the current user's screening activity."""
    from models import CandidateJobMatch, CandidateVettingLog

    job_ids = _get_user_job_ids()
    week_ago = datetime.utcnow() - timedelta(days=7)

    if not job_ids:
        return jsonify({'qualified': 0, 'pending': 0, 'not_recommended': 0, 'screened_this_week': 0})

    matches = CandidateJobMatch.query.filter(
        CandidateJobMatch.bullhorn_job_id.in_(job_ids)
    ).all()

    pending = CandidateVettingLog.query.filter(
        CandidateVettingLog.applied_job_id.in_(job_ids),
        CandidateVettingLog.status == 'pending',
        CandidateVettingLog.is_sandbox != True
    ).count()

    return jsonify({
        'qualified': sum(1 for m in matches if m.is_qualified),
        'pending': pending,
        'not_recommended': sum(1 for m in matches if not m.is_qualified and m.match_score > 0),
        'screened_this_week': sum(1 for m in matches if m.created_at and m.created_at >= week_ago),
    })
