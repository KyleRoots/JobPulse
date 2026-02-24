"""
Scout Inbound customer-facing routes.

Provides a consolidated single-page view for non-admin users showing:
- My Jobs: jobs assigned to the logged-in recruiter (via Bullhorn assignedUsers)
- Candidates Applied: inbound applicants filtered to the user's jobs
"""

import json
import logging
from datetime import datetime

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user


scout_inbound_bp = Blueprint('scout_inbound', __name__)
logger = logging.getLogger(__name__)


def _get_user_jobs():
    """Get all jobs assigned to the current user across all tearsheets."""
    from models import BullhornMonitor
    
    monitors = BullhornMonitor.query.filter_by(is_active=True).all()
    user_jobs = []
    all_job_ids = set()
    tearsheet_names = set()
    
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
            if not job_id or job_id in all_job_ids:
                continue
            
            assigned_users = job.get('assignedUsers', [])
            is_assigned = False
            
            for au in assigned_users:
                if bullhorn_uid and au.get('id') == bullhorn_uid:
                    is_assigned = True
                    break
                if user_display:
                    full_name = f"{au.get('firstName', '')} {au.get('lastName', '')}".strip().lower()
                    if full_name and full_name == user_display:
                        is_assigned = True
                        break
            
            if current_user.is_admin:
                is_assigned = True
            
            if is_assigned:
                all_job_ids.add(job_id)
                job['tearsheet_name'] = monitor.tearsheet_name or monitor.name
                user_jobs.append(job)
                tearsheet_names.add(monitor.tearsheet_name or monitor.name)
    
    return user_jobs, tearsheet_names, all_job_ids


@scout_inbound_bp.route('/scout-inbound')
@login_required
def scout_inbound_dashboard():
    """Consolidated Scout Inbound dashboard for customer users."""
    from models import ParsedEmail
    
    user_jobs, tearsheet_names, job_ids = _get_user_jobs()
    
    if job_ids:
        candidates = ParsedEmail.query.filter(
            ParsedEmail.bullhorn_job_id.in_(job_ids)
        ).order_by(ParsedEmail.received_at.desc()).limit(100).all()
    else:
        candidates = []
    
    total_applicants = len(candidates)
    processed = sum(1 for c in candidates if c.status == 'completed')
    failed = sum(1 for c in candidates if c.status == 'failed')
    duplicates = sum(1 for c in candidates if c.is_duplicate_candidate)
    duplicate_rate = (duplicates / total_applicants * 100) if total_applicants > 0 else 0
    
    job_stats = {
        'active_jobs': len(user_jobs),
        'tearsheets': sorted(tearsheet_names),
        'tearsheet_count': len(tearsheet_names),
    }
    
    candidate_stats = {
        'total': total_applicants,
        'processed': processed,
        'failed': failed,
        'duplicates': duplicates,
        'duplicate_rate': duplicate_rate,
    }
    
    return render_template('scout_inbound.html',
                         user_jobs=user_jobs,
                         candidates=candidates,
                         job_stats=job_stats,
                         candidate_stats=candidate_stats,
                         active_page='scout_inbound')


@scout_inbound_bp.route('/api/scout-inbound/jobs')
@login_required
def scout_inbound_jobs_api():
    """API endpoint returning jobs for the logged-in user."""
    user_jobs, tearsheet_names, _ = _get_user_jobs()
    
    return jsonify({
        'jobs': [{
            'id': j.get('id'),
            'title': j.get('title', ''),
            'status': j.get('status', ''),
            'clientName': j.get('clientName', ''),
            'location': j.get('location', {}),
            'employmentType': j.get('employmentType', ''),
            'tearsheet_name': j.get('tearsheet_name', ''),
            'assignedUsers': j.get('assignedUsers', []),
        } for j in user_jobs],
        'tearsheets': sorted(tearsheet_names),
        'total': len(user_jobs)
    })


@scout_inbound_bp.route('/api/scout-inbound/candidates')
@login_required
def scout_inbound_candidates_api():
    """API endpoint returning candidates for the logged-in user's jobs."""
    from models import ParsedEmail
    
    _, _, job_ids = _get_user_jobs()
    
    if not job_ids:
        return jsonify({'candidates': [], 'total': 0})
    
    candidates = ParsedEmail.query.filter(
        ParsedEmail.bullhorn_job_id.in_(job_ids)
    ).order_by(ParsedEmail.received_at.desc()).limit(100).all()
    
    return jsonify({
        'candidates': [{
            'id': c.id,
            'candidate_name': c.candidate_name,
            'candidate_email': c.candidate_email,
            'bullhorn_job_id': c.bullhorn_job_id,
            'bullhorn_candidate_id': c.bullhorn_candidate_id,
            'bullhorn_submission_id': c.bullhorn_submission_id,
            'source_platform': c.source_platform,
            'status': c.status,
            'is_duplicate': c.is_duplicate_candidate,
            'duplicate_confidence': c.duplicate_confidence,
            'resume_filename': c.resume_filename,
            'processing_notes': c.processing_notes,
            'received_at': c.received_at.strftime('%Y-%m-%d %H:%M') if c.received_at else None,
        } for c in candidates],
        'total': len(candidates)
    })
