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
from sqlalchemy.orm import joinedload
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
            .options(joinedload(CandidateJobMatch.vetting_log))
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
        is_ajax_threshold_save = request.form.get('_ajax') == '1'

        threshold_raw = request.form.get('vetting_threshold', '').strip()
        vetting_threshold = int(threshold_raw) if threshold_raw else None
        if vetting_threshold is not None and not (50 <= vetting_threshold <= 100):
            flash('Threshold must be between 50 and 100.', 'error')
            return redirect(url_for('scout_screening.dashboard'))

        job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()

        if is_ajax_threshold_save:
            employer_prestige_boost = job_req.employer_prestige_boost if job_req else False
        else:
            prestige_boost_values = request.form.getlist('employer_prestige_boost')
            employer_prestige_boost = '1' in prestige_boost_values

        # Edited requirements: only processed on full modal save (not AJAX threshold-only saves).
        # Empty submission OR matches the AI-original exactly → clear the edit (revert to AI).
        # Sentinel: leave None on AJAX path so the persistence block below leaves edited_* untouched.
        edit_action = None
        new_edited = None
        if not is_ajax_threshold_save:
            submitted_edit = (request.form.get('edited_requirements', '') or '').strip()
            ai_baseline = ((job_req.ai_interpreted_requirements if job_req else '') or '').strip()

            if not submitted_edit or submitted_edit == ai_baseline:
                new_edited = None
                edit_action = 'cleared'
            else:
                new_edited = submitted_edit
                edit_action = 'set'

        if job_req:
            job_req.vetting_threshold = vetting_threshold
            job_req.employer_prestige_boost = employer_prestige_boost
            if not is_ajax_threshold_save:
                if new_edited is None:
                    job_req.edited_requirements = None
                    job_req.requirements_edited_at = None
                    job_req.requirements_edited_by = None
                elif new_edited != (job_req.edited_requirements or ''):
                    job_req.edited_requirements = new_edited
                    job_req.requirements_edited_at = datetime.utcnow()
                    job_req.requirements_edited_by = current_user.email
            job_req.updated_at = datetime.utcnow()
        else:
            job_req = JobVettingRequirements(
                bullhorn_job_id=job_id,
                vetting_threshold=vetting_threshold,
                employer_prestige_boost=employer_prestige_boost,
            )
            if not is_ajax_threshold_save and new_edited:
                job_req.edited_requirements = new_edited
                job_req.requirements_edited_at = datetime.utcnow()
                job_req.requirements_edited_by = current_user.email
            db.session.add(job_req)

        # Audit log
        try:
            from models import UserActivityLog
            audit_details = {
                'job_id': job_id,
                'job_title': job_req.job_title or f'Job #{job_id}',
                'threshold': vetting_threshold,
                'employer_prestige_boost': employer_prestige_boost,
                'page': 'scout_screening',
            }
            if edit_action is not None:
                audit_details['edited_requirements_action'] = edit_action
            db.session.add(UserActivityLog(
                user_id=current_user.id,
                activity_type='config_change',
                ip_address=request.remote_addr,
                details=json.dumps(audit_details),
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


@scout_screening_bp.route('/scout-screening/job/<int:job_id>/reset-requirements', methods=['POST'])
@login_required
def reset_job_requirements(job_id):
    """Discard recruiter edits and revert to the AI-extracted requirements."""
    from extensions import db
    from models import JobVettingRequirements

    job_ids = _get_user_job_ids()
    if job_id not in job_ids:
        return jsonify({'success': False, 'error': 'Not authorized for this job.'}), 403

    try:
        job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
        if not job_req:
            return jsonify({'success': False, 'error': 'No requirements record found.'}), 404

        had_edits = bool(job_req.edited_requirements)
        job_req.edited_requirements = None
        job_req.requirements_edited_at = None
        job_req.requirements_edited_by = None
        job_req.updated_at = datetime.utcnow()

        try:
            from models import UserActivityLog
            db.session.add(UserActivityLog(
                user_id=current_user.id,
                activity_type='config_change',
                ip_address=request.remote_addr,
                details=json.dumps({
                    'job_id': job_id,
                    'job_title': job_req.job_title or f'Job #{job_id}',
                    'edited_requirements_action': 'reset_to_ai',
                    'page': 'scout_screening',
                })
            ))
        except Exception as log_err:
            logger.warning(f"Failed to write reset audit log: {log_err}")

        db.session.commit()
        return jsonify({
            'success': True,
            'had_edits': had_edits,
            'ai_requirements': job_req.ai_interpreted_requirements or '',
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error resetting requirements for job {job_id}: {e}")
        return jsonify({'success': False, 'error': 'Reset failed — please try again.'}), 500


@scout_screening_bp.route('/scout-screening/job/<int:job_id>/optimize-requirements', methods=['POST'])
@login_required
def optimize_job_requirements(job_id):
    """Use AI to rewrite the editable requirements using prompt-engineering best practices."""
    try:
        data = request.get_json(silent=True) or {}
        # Accept new key first; fall back to legacy key for any cached client JS
        raw = (data.get('edited_requirements') or data.get('custom_requirements') or '').strip()
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
            max_completion_tokens=1500
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

    Query params:
      q          — search string (3+ chars). Matches candidate name (substring,
                   trigram-indexed), candidate email (substring), Bullhorn ID
                   (exact when fully numeric), and phone (when the query
                   normalises to 7+ digits, looked up via ParsedEmail).
      status     — qualified | not_recommended | location_barrier (optional;
                   'pending' is silently ignored — pending logs have no match
                   rows yet, the dashboard surfaces them in a separate section).
      min_score  — int 0-100; candidate's best score must meet this floor.
      this_week  — '1'/'true' to limit to candidates active in last 7 days.
      page       — 1-based page number, default 1.
      page_size  — 10-200, default 100.

    Response shape:
      results        — CandidateJobMatch rows for the candidates on the
                       requested page (multiple rows per candidate group).
      total_groups   — total candidate groups across the filtered set.
      page, page_size, total_pages — pagination meta (clamped server-side).
      group_counts   — { qualified, not_recommended, location_barrier, week }
                       totals across the entire filtered set, used to keep the
                       dashboard's metric tiles honest while search is active.
      truncated      — true when the underlying scan hit the safety cap;
                       results past the cap are not visible until the query
                       narrows.

    Scoped to the current user's assigned jobs via `_get_user_job_ids()`.
    """
    import re

    from extensions import db
    from models import CandidateJobMatch, CandidateVettingLog, ParsedEmail, VettingConfig
    from sqlalchemy import func, or_

    SAFETY_CAP = 5000

    q = (request.args.get('q', '') or '').strip()

    status_param = (request.args.get('status', '') or '').strip().lower()
    if status_param not in {'qualified', 'not_recommended', 'location_barrier'}:
        status_param = ''

    raw_min = request.args.get('min_score', '')
    try:
        min_score = int(raw_min) if raw_min not in (None, '') else 0
    except (TypeError, ValueError):
        min_score = 0
    min_score = max(0, min(100, min_score))

    this_week_only = (request.args.get('this_week', '') or '').strip().lower() in {'1', 'true', 'yes'}

    try:
        page = max(1, int(request.args.get('page', '1') or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.args.get('page_size', '100') or 100)
    except (TypeError, ValueError):
        page_size = 100
    page_size = max(10, min(200, page_size))

    def _empty():
        return jsonify({
            'results': [],
            'total_groups': 0,
            'page': 1,
            'page_size': page_size,
            'total_pages': 0,
            'group_counts': {'qualified': 0, 'not_recommended': 0,
                             'location_barrier': 0, 'week': 0},
            'truncated': False,
        })

    if len(q) < 3:
        return _empty()

    job_ids = _get_user_job_ids()
    if not job_ids:
        return _empty()

    # ------------------------------------------------------------------
    # Build OR-search predicates over CandidateVettingLog
    # ------------------------------------------------------------------
    like = f'%{q}%'
    predicates = [
        CandidateVettingLog.candidate_name.ilike(like),
        CandidateVettingLog.candidate_email.ilike(like),
    ]
    if q.isdigit():
        predicates.append(CandidateVettingLog.bullhorn_candidate_id == int(q))

    # Phone is not stored on the vetting log — resolve via ParsedEmail when the
    # query carries enough digits to disambiguate (≥ 7 digits keeps the branch
    # off for pure-name searches, which would otherwise force a wide scan).
    digits = re.sub(r'\D', '', q)
    if len(digits) >= 7:
        try:
            dialect_name = db.engine.dialect.name
        except Exception:
            dialect_name = ''
        if dialect_name == 'postgresql':
            phone_norm = func.regexp_replace(ParsedEmail.candidate_phone, '[^0-9]', '', 'g')
        else:
            # SQLite (used by tests) — fall back to a direct ilike. Tests don't
            # exercise normalised phone matching.
            phone_norm = ParsedEmail.candidate_phone
        phone_cid_subq = (
            db.session.query(ParsedEmail.bullhorn_candidate_id)
            .filter(ParsedEmail.bullhorn_candidate_id.isnot(None))
            .filter(phone_norm.ilike(f'%{digits}%'))
            .distinct()
            .subquery()
        )
        predicates.append(
            CandidateVettingLog.bullhorn_candidate_id.in_(
                db.session.query(phone_cid_subq)
            )
        )

    # ------------------------------------------------------------------
    # Pull matching CandidateJobMatch IDs (capped) for in-memory grouping
    # ------------------------------------------------------------------
    base_q = (
        db.session.query(CandidateJobMatch.id)
        .join(CandidateVettingLog, CandidateJobMatch.vetting_log_id == CandidateVettingLog.id)
        .filter(
            CandidateJobMatch.bullhorn_job_id.in_(job_ids),
            CandidateVettingLog.is_sandbox != True,
            or_(*predicates),
        )
    )
    raw_ids = [row[0] for row in base_q.limit(SAFETY_CAP + 1).all()]
    truncated = len(raw_ids) > SAFETY_CAP
    raw_ids = raw_ids[:SAFETY_CAP]

    if not raw_ids:
        return _empty()

    matches = (
        CandidateJobMatch.query
        .options(joinedload(CandidateJobMatch.vetting_log))
        .filter(CandidateJobMatch.id.in_(raw_ids))
        .order_by(CandidateJobMatch.created_at.desc())
        .all()
    )

    # Dedupe to one (candidate, job) pair, keeping the latest match (rows
    # are already ordered created_at desc, so the first occurrence wins).
    seen_pairs = set()
    deduped = []
    for m in matches:
        log = m.vetting_log
        cand_id = log.bullhorn_candidate_id if log else None
        key = (cand_id, m.bullhorn_job_id)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(m)
    matches = deduped

    threshold = int(VettingConfig.get_value('match_threshold', '80') or 80)
    week_ago = datetime.utcnow() - timedelta(days=7)

    # ------------------------------------------------------------------
    # Aggregate matches into candidate groups
    # ------------------------------------------------------------------
    groups = OrderedDict()
    for m in matches:
        log = m.vetting_log
        cand_id = log.bullhorn_candidate_id if log else None
        cand_name = (log.candidate_name if log else None) or 'Unknown'
        gaps = (m.gaps_identified or '').lower()
        tech = m.technical_score
        is_loc_barrier = (
            'location mismatch' in gaps
            and (tech or m.match_score) >= (threshold - 15)
            and not m.is_qualified
        )
        is_week = bool(m.created_at and m.created_at >= week_ago)
        score = int(round(m.match_score or 0))

        gkey = cand_id if cand_id is not None else f'unk_{m.id}'
        if gkey not in groups:
            groups[gkey] = {
                'candidate_id': cand_id,
                'candidate_name': cand_name,
                'best_score': 0,
                'has_qualified': False,
                'has_loc_barrier': False,
                'is_week': False,
                'latest_ts': '0',
                'matches': [],
            }
        g = groups[gkey]
        g['matches'].append({
            'id': m.id,
            'created_at_display': m.created_at.strftime('%b %d, %Y') if m.created_at else '—',
            'created_ts': m.created_at.strftime('%Y%m%d%H%M%S') if m.created_at else '0',
            'is_week': is_week,
            'candidate_id': cand_id,
            'candidate_name': cand_name,
            'job_id': m.bullhorn_job_id,
            'job_title': m.job_title or f'Job #{m.bullhorn_job_id}',
            'match_score': score,
            'technical_score': int(round(tech)) if tech is not None else None,
            'is_qualified': m.is_qualified,
            'is_loc_barrier': is_loc_barrier,
            'match_summary': m.match_summary or '',
            'gaps_identified': m.gaps_identified or '',
            'is_applied_job': m.is_applied_job,
            'vetting_log_id': m.vetting_log_id,
        })
        if score > g['best_score']:
            g['best_score'] = score
        if m.is_qualified:
            g['has_qualified'] = True
        if is_loc_barrier:
            g['has_loc_barrier'] = True
        if is_week:
            g['is_week'] = True
        ts = m.created_at.strftime('%Y%m%d%H%M%S') if m.created_at else '0'
        if ts > g['latest_ts']:
            g['latest_ts'] = ts

    # ------------------------------------------------------------------
    # Apply group-level filter chips (status / min_score / this_week)
    # ------------------------------------------------------------------
    def _keep(g):
        if status_param == 'qualified' and not g['has_qualified']:
            return False
        if status_param == 'location_barrier' and not (g['has_loc_barrier'] and not g['has_qualified']):
            return False
        if status_param == 'not_recommended' and (g['has_qualified'] or g['has_loc_barrier']):
            return False
        if min_score and g['best_score'] < min_score:
            return False
        if this_week_only and not g['is_week']:
            return False
        return True

    filtered_groups = [g for g in groups.values() if _keep(g)]
    filtered_groups.sort(key=lambda g: g['latest_ts'], reverse=True)

    # Aggregate counts across the FULL filtered set (for tile parity)
    group_counts = {
        'qualified': sum(1 for g in filtered_groups if g['has_qualified']),
        'location_barrier': sum(1 for g in filtered_groups
                                if g['has_loc_barrier'] and not g['has_qualified']),
        'not_recommended': sum(1 for g in filtered_groups
                               if not g['has_qualified'] and not g['has_loc_barrier']),
        'week': sum(1 for g in filtered_groups if g['is_week']),
    }

    total_groups = len(filtered_groups)
    total_pages = (total_groups + page_size - 1) // page_size if total_groups else 0
    if total_pages and page > total_pages:
        page = total_pages

    start = (page - 1) * page_size
    page_groups = filtered_groups[start:start + page_size]

    results = []
    for g in page_groups:
        g['matches'].sort(key=lambda x: x['created_ts'], reverse=True)
        results.extend(g['matches'])

    return jsonify({
        'results': results,
        'total_groups': total_groups,
        'page': page,
        'page_size': page_size,
        'total_pages': total_pages,
        'group_counts': group_counts,
        'truncated': truncated,
    })


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


@scout_screening_bp.route('/scout-screening/bulk-email', methods=['POST'])
@login_required
def bulk_email():
    """Send a summary email of selected screening results to specified recipients."""
    from email_service import EmailService
    from models import EmailDeliveryLog
    from extensions import db

    data = request.get_json(silent=True) or {}
    to_email = (data.get('to_email') or '').strip()
    cc_emails = [e.strip() for e in (data.get('cc_emails') or []) if e.strip()]
    candidates = data.get('candidates') or []

    if not to_email:
        return jsonify({'success': False, 'error': 'Recipient email is required.'}), 400
    if not candidates:
        return jsonify({'success': False, 'error': 'No candidates selected.'}), 400

    candidate_rows = ''
    for c in candidates:
        cid = c.get('candidate_id', '')
        name = c.get('candidate_name', 'Unknown')
        score = c.get('best_score', 0)
        status = c.get('status', 'unknown')
        status_label = status.replace('_', ' ').title()
        if status == 'qualified':
            status_color = '#28a745'
        elif status == 'location_barrier':
            status_color = '#ffc107'
            status_label = 'Location Barrier'
        else:
            status_color = '#6c757d'

        profile_link = ''
        if cid:
            profile_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=Candidate&id={cid}"
            profile_link = f'<a href="{profile_url}" style="color: #667eea; text-decoration: none; font-size: 12px;">View Profile →</a>'

        candidate_rows += f"""
        <tr style="border-bottom: 1px solid #e9ecef;">
            <td style="padding: 10px 12px; font-weight: 600; color: #495057;">
                {name}
                {f'<br><span style="font-size: 11px; color: #adb5bd;">ID: {cid}</span>' if cid else ''}
            </td>
            <td style="padding: 10px 12px; text-align: center;">
                <span style="background: {'#28a745' if score >= 80 else '#ffc107' if score >= 60 else '#6c757d'}; color: white; padding: 3px 10px; border-radius: 12px; font-size: 13px; font-weight: 600;">{score}%</span>
            </td>
            <td style="padding: 10px 12px; text-align: center;">
                <span style="color: {status_color}; font-weight: 500; font-size: 13px;">{status_label}</span>
            </td>
            <td style="padding: 10px 12px; text-align: center;">{profile_link}</td>
        </tr>
        """

    requester = current_user.display_name or current_user.username or 'A recruiter'
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 650px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0;">
            <h1 style="margin: 0; font-size: 22px;">📋 Screening Results Summary</h1>
            <p style="margin: 8px 0 0 0; font-size: 14px; opacity: 0.9;">Shared by {requester} via Scout Screening</p>
        </div>

        <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef;">
            <p style="margin: 0 0 15px 0; color: #495057;">
                Below are <strong>{len(candidates)} candidate(s)</strong> from the Scout Screening portal.
            </p>

            <table style="width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; border: 1px solid #dee2e6;">
                <thead>
                    <tr style="background: #e9ecef;">
                        <th style="padding: 10px 12px; text-align: left; font-size: 12px; color: #6c757d; text-transform: uppercase;">Candidate</th>
                        <th style="padding: 10px 12px; text-align: center; font-size: 12px; color: #6c757d; text-transform: uppercase;">Score</th>
                        <th style="padding: 10px 12px; text-align: center; font-size: 12px; color: #6c757d; text-transform: uppercase;">Status</th>
                        <th style="padding: 10px 12px; text-align: center; font-size: 12px; color: #6c757d; text-transform: uppercase;">Profile</th>
                    </tr>
                </thead>
                <tbody>
                    {candidate_rows}
                </tbody>
            </table>

            <div style="margin-top: 20px; padding-top: 15px; border-top: 1px solid #dee2e6;">
                <p style="color: #6c757d; font-size: 13px; margin: 0;">
                    <strong>Tip:</strong> Click "View Profile" to open the candidate record directly in Bullhorn.
                </p>
            </div>
        </div>

        <div style="background: #343a40; color: #adb5bd; padding: 15px; border-radius: 0 0 8px 8px; font-size: 12px; text-align: center;">
            Powered by Scout Screening™ • Myticas Consulting
        </div>
    </div>
    """

    try:
        email_service = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
        qualified_count = sum(1 for c in candidates if c.get('status') == 'qualified')
        subject = f"📋 Screening Results: {len(candidates)} Candidate{'s' if len(candidates) != 1 else ''} ({qualified_count} Qualified)"
        result = email_service.send_html_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            notification_type='screening_bulk_email',
            cc_emails=cc_emails if cc_emails else None,
            changes_summary=f"Bulk screening results shared by {requester}: {len(candidates)} candidates"
        )
        success = result is True or (isinstance(result, dict) and result.get('success', False))
        if success:
            logger.info(f"Bulk screening email sent by {current_user.username} to {to_email} ({len(candidates)} candidates)")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Email delivery failed.'}), 500
    except Exception as e:
        logger.error(f"Bulk screening email error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to send email.'}), 500


@scout_screening_bp.route('/scout-screening/guide')
@login_required
def guide():
    return render_template('scout_screening_guide.html', active_page='screening')
