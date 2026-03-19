"""
Scout Screening Portal — customer-facing routes.

Provides a focused single-page view for scout_screening users showing:
- AI screening results (match scores, qualification status) for the user's jobs
- Per-job AI-interpreted requirements with user-editable custom overrides
- Match threshold control per job (overrides global admin setting for that job)
"""

import json
import logging
from collections import OrderedDict
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
    """Scout Screening Portal — AI match results grouped by candidate."""
    from models import CandidateJobMatch, CandidateVettingLog, JobVettingRequirements, VettingConfig

    job_ids = _get_user_job_ids()
    jobs_map = _get_user_jobs_with_meta()

    week_ago = datetime.utcnow() - timedelta(days=7)

    if job_ids:
        all_matches = (
            CandidateJobMatch.query
            .join(CandidateVettingLog)
            .filter(
                CandidateJobMatch.bullhorn_job_id.in_(job_ids),
                CandidateVettingLog.is_sandbox != True
            )
            .order_by(CandidateJobMatch.created_at.desc())
            .limit(2000)
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
        all_matches = []
        pending_logs = []

    global_threshold = int(VettingConfig.get_value('match_threshold', '80') or 80)

    def _is_loc_barrier(m):
        return (
            not m.is_qualified
            and 'location mismatch' in (m.gaps_identified or '').lower()
            and (m.technical_score or m.match_score or 0) >= (global_threshold - 15)
        )

    candidate_groups = OrderedDict()
    for m in all_matches:
        cand_id = m.vetting_log.bullhorn_candidate_id if m.vetting_log else None
        key = cand_id or f'unknown_{m.id}'
        loc_barrier = _is_loc_barrier(m)
        is_week = bool(m.created_at and m.created_at >= week_ago)

        if key not in candidate_groups:
            candidate_groups[key] = {
                'candidate_id': cand_id,
                'candidate_name': m.vetting_log.candidate_name if m.vetting_log else 'Unknown',
                'latest_date': m.created_at,
                'best_score': int(round(m.match_score or 0)),
                'has_qualified': bool(m.is_qualified),
                'has_loc_barrier': loc_barrier,
                'is_week': is_week,
                'matches': [m],
            }
        else:
            g = candidate_groups[key]
            g['matches'].append(m)
            if m.created_at and (not g['latest_date'] or m.created_at > g['latest_date']):
                g['latest_date'] = m.created_at
            score = int(round(m.match_score or 0))
            if score > g['best_score']:
                g['best_score'] = score
            if m.is_qualified:
                g['has_qualified'] = True
            if loc_barrier:
                g['has_loc_barrier'] = True
            if is_week:
                g['is_week'] = True

    groups_list = list(candidate_groups.values())[:200]

    for g in groups_list:
        if g['has_qualified']:
            g['overall_status'] = 'qualified'
        elif g['has_loc_barrier']:
            g['overall_status'] = 'location_barrier'
        elif g['best_score'] > 0:
            g['overall_status'] = 'not_recommended'
        else:
            g['overall_status'] = 'pending'
        g['job_count'] = len(g['matches'])
        for m in g['matches']:
            m._is_loc_barrier = _is_loc_barrier(m)

    metrics = {
        'qualified': sum(1 for g in groups_list if g['has_qualified']),
        'pending': len(pending_logs),
        'not_recommended': sum(1 for g in groups_list if not g['has_qualified'] and not g['has_loc_barrier'] and g['best_score'] > 0),
        'location_barrier': sum(1 for g in groups_list if not g['has_qualified'] and g['has_loc_barrier']),
        'screened_this_week': sum(1 for g in groups_list if g['is_week']),
    }

    job_requirements = {}
    if job_ids:
        reqs = JobVettingRequirements.query.filter(
            JobVettingRequirements.bullhorn_job_id.in_(job_ids)
        ).all()
        job_requirements = {r.bullhorn_job_id: r for r in reqs}

    return render_template(
        'scout_screening.html',
        candidate_groups=groups_list,
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

        # Audit log
        try:
            from models import UserActivityLog
            db.session.add(UserActivityLog(
                user_id=current_user.id,
                activity_type='config_change',
                ip_address=request.remote_addr,
                details=json.dumps({
                    'job_id': job_id,
                    'job_title': job_req.job_title or f'Job #{job_id}',
                    'custom_requirements_action': 'set' if custom_requirements else 'cleared',
                    'threshold': vetting_threshold,
                    'page': 'scout_screening',
                })
            ))
        except Exception as log_err:
            logger.warning(f"Failed to write config_change log: {log_err}")

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


@scout_screening_bp.route('/scout-screening/job/<int:job_id>/optimize-requirements', methods=['POST'])
@login_required
def optimize_job_requirements(job_id):
    """Use GPT-4o to rewrite custom requirements using prompt-engineering best practices."""
    try:
        data = request.get_json(silent=True) or {}
        raw = (data.get('custom_requirements') or '').strip()
        if not raw:
            return jsonify({'success': False, 'error': 'No requirements text provided.'}), 400

        from openai import OpenAI
        client = OpenAI()

        system_prompt = (
            "You are a prompt engineer specializing in AI-powered candidate screening systems. "
            "Your task is to take a recruiter's raw custom requirements and rewrite them as clear, "
            "unambiguous, machine-readable screening criteria for an AI vetting system.\n\n"
            "Rules:\n"
            "1. Preserve the recruiter's original intent exactly — never add requirements they did not mention.\n"
            "2. Make each requirement explicit and testable — replace vague qualifiers like 'some experience' "
            "or 'familiarity with' with concrete, measurable criteria.\n"
            "3. For experience requirements, always state the number of years and the specific domain.\n"
            "4. For eligibility or work-authorization requirements, add OR clauses for equivalent qualifications "
            "(e.g. 'Canadian citizen OR permanent resident', 'degree OR equivalent professional experience').\n"
            "5. Distinguish between required and preferred where inferable from context.\n"
            "6. If there are multiple requirements, number them for clarity.\n"
            "7. Use present-tense active language: 'Candidate must have…' or 'Candidate should demonstrate…'.\n"
            "8. Output ONLY the optimized requirements text — no explanations, no commentary, no preamble.\n"
            "9. Keep the output concise — clear and structured, not a lengthy essay."
        )

        response = client.chat.completions.create(
            model='gpt-5.4',
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': f'Optimize the following custom screening requirements:\n\n{raw}'}
            ],
            max_tokens=600,
            temperature=0.3
        )

        optimized = response.choices[0].message.content.strip()
        logger.info(f"Optimized requirements for job {job_id} ({len(raw)} → {len(optimized)} chars)")
        return jsonify({'success': True, 'optimized': optimized})

    except Exception as e:
        current_app.logger.error(f"Error optimizing requirements for job {job_id}: {e}")
        return jsonify({'success': False, 'error': 'Optimization failed — please try again.'}), 500


@scout_screening_bp.route('/scout-screening/search')
@login_required
def candidate_search():
    """Server-side candidate search across all historical screening records.
    
    Returns the most recent match record per (candidate, job) pair for any
    candidate matching the query — not limited to the 200-record default view.
    Scoped to the current user's assigned jobs.
    """
    from extensions import db
    from models import CandidateJobMatch, CandidateVettingLog, VettingConfig
    from sqlalchemy import func, or_

    q = request.args.get('q', '').strip()
    if len(q) < 3:
        return jsonify({'results': [], 'total': 0, 'truncated': False})

    job_ids = _get_user_job_ids()
    if not job_ids:
        return jsonify({'results': [], 'total': 0, 'truncated': False})

    # Build search filter — name partial match or exact ID match
    name_filter = CandidateVettingLog.candidate_name.ilike(f'%{q}%')
    if q.isdigit():
        search_filter = or_(name_filter, CandidateVettingLog.bullhorn_candidate_id == int(q))
    else:
        search_filter = name_filter

    # Subquery: one row per (bullhorn_candidate_id, bullhorn_job_id) — the latest match only
    latest_subq = (
        db.session.query(func.max(CandidateJobMatch.id).label('max_id'))
        .join(CandidateVettingLog, CandidateJobMatch.vetting_log_id == CandidateVettingLog.id)
        .filter(
            CandidateJobMatch.bullhorn_job_id.in_(job_ids),
            CandidateVettingLog.is_sandbox != True,
            search_filter
        )
        .group_by(CandidateVettingLog.bullhorn_candidate_id, CandidateJobMatch.bullhorn_job_id)
        .subquery()
    )

    matches = (
        CandidateJobMatch.query
        .join(CandidateVettingLog)
        .filter(CandidateJobMatch.id.in_(db.session.query(latest_subq.c.max_id)))
        .order_by(CandidateJobMatch.created_at.desc())
        .limit(501)
        .all()
    )

    truncated = len(matches) > 500
    matches = matches[:500]

    threshold = int(VettingConfig.get_value('match_threshold', '80') or 80)
    week_ago = datetime.utcnow() - timedelta(days=7)

    results = []
    for m in matches:
        log = m.vetting_log
        gaps = (m.gaps_identified or '').lower()
        tech = m.technical_score
        is_loc_barrier = (
            'location mismatch' in gaps
            and (tech or m.match_score) >= (threshold - 15)
            and not m.is_qualified
        )
        is_week = bool(m.created_at and m.created_at >= week_ago)
        results.append({
            'id': m.id,
            'created_at_display': m.created_at.strftime('%b %d, %Y') if m.created_at else '—',
            'created_ts': m.created_at.strftime('%Y%m%d%H%M%S') if m.created_at else '0',
            'is_week': is_week,
            'candidate_id': log.bullhorn_candidate_id if log else None,
            'candidate_name': log.candidate_name if log else 'Unknown',
            'job_id': m.bullhorn_job_id,
            'job_title': m.job_title or f'Job #{m.bullhorn_job_id}',
            'match_score': int(round(m.match_score or 0)),
            'technical_score': int(round(tech)) if tech is not None else None,
            'is_qualified': m.is_qualified,
            'is_loc_barrier': is_loc_barrier,
            'match_summary': m.match_summary or '',
            'gaps_identified': m.gaps_identified or '',
            'is_applied_job': m.is_applied_job,
            'vetting_log_id': m.vetting_log_id,
        })

    return jsonify({'results': results, 'total': len(results), 'truncated': truncated})


@scout_screening_bp.route('/api/scout-screening/stats')
@login_required
def stats_api():
    """Lightweight JSON stats for the current user's screening activity."""
    from models import CandidateJobMatch, CandidateVettingLog

    job_ids = _get_user_job_ids()
    week_ago = datetime.utcnow() - timedelta(days=7)

    if not job_ids:
        return jsonify({'qualified': 0, 'pending': 0, 'not_recommended': 0, 'location_barrier': 0, 'screened_this_week': 0})

    matches = CandidateJobMatch.query.filter(
        CandidateJobMatch.bullhorn_job_id.in_(job_ids)
    ).all()

    pending = CandidateVettingLog.query.filter(
        CandidateVettingLog.applied_job_id.in_(job_ids),
        CandidateVettingLog.status == 'pending',
        CandidateVettingLog.is_sandbox != True
    ).count()

    from models import VettingConfig as _VC
    _gt = int(_VC.get_value('match_threshold', '80') or 80)

    _lb_ids = set()
    _lb_count = 0
    for m in matches:
        if (not m.is_qualified
            and 'location mismatch' in (m.gaps_identified or '').lower()
            and (m.technical_score or m.match_score) >= (_gt - 15)):
            _lb_ids.add(id(m))
            _lb_count += 1

    return jsonify({
        'qualified': sum(1 for m in matches if m.is_qualified),
        'pending': pending,
        'location_barrier': _lb_count,
        'not_recommended': sum(
            1 for m in matches
            if not m.is_qualified
            and m.match_score > 0
            and id(m) not in _lb_ids
        ),
        'screened_this_week': sum(1 for m in matches if m.created_at and m.created_at >= week_ago),
    })


@scout_screening_bp.route('/scout-screening/guide')
@login_required
def guide():
    return render_template('scout_screening_guide.html', active_page='screening')
