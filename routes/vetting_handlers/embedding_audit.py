"""Embedding-filter audit page, CSV exports, and per-log retry controls."""
from datetime import datetime, timedelta

from flask import jsonify, render_template, request
from flask_login import login_required

from extensions import csrf, db
from routes.vetting import vetting_bp
from routes.vetting_handlers._shared import get_db


# ═══════════════════════════════════════════════════════════════
# EMBEDDING FILTER MONITORING ROUTES
# ═══════════════════════════════════════════════════════════════


@vetting_bp.route('/screening/embedding-audit')
@login_required
def embedding_audit():
    """Embedding filter audit page — filtered pairs and escalations."""
    from models import EmbeddingFilterLog, EscalationLog, CandidateJobMatch

    _ = get_db()  # preserve original side-effect of resolving the db at request time

    # Parse date range filters
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    active_tab = request.args.get('tab', 'filtered')

    # Default to last 7 days
    if not date_from:
        from_date = datetime.utcnow() - timedelta(days=7)
    else:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
        except ValueError:
            from_date = datetime.utcnow() - timedelta(days=7)

    if not date_to:
        to_date = datetime.utcnow()
    else:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            to_date = datetime.utcnow()

    # Parse similarity range filter
    try:
        _sim_min_raw = request.args.get('sim_min', '0.0')
        sim_min = 0.0 if str(_sim_min_raw).strip().lower() == 'nan' else float(_sim_min_raw)
        if sim_min != sim_min:
            sim_min = 0.0
    except (ValueError, TypeError):
        sim_min = 0.0
    try:
        _sim_max_raw = request.args.get('sim_max', '1.0')
        sim_max = 1.0 if str(_sim_max_raw).strip().lower() == 'nan' else float(_sim_max_raw)
        if sim_max != sim_max:
            sim_max = 1.0
    except (ValueError, TypeError):
        sim_max = 1.0

    # Parse score band filter for escalations
    score_band = request.args.get('score_band', 'all')

    # ─── Filtered Pairs Tab ───
    filter_query = EmbeddingFilterLog.query.filter(
        EmbeddingFilterLog.filtered_at >= from_date,
        EmbeddingFilterLog.filtered_at <= to_date
    )
    if sim_min > 0:
        filter_query = filter_query.filter(EmbeddingFilterLog.similarity_score >= sim_min)
    if sim_max < 1.0:
        filter_query = filter_query.filter(EmbeddingFilterLog.similarity_score <= sim_max)

    sort_by = request.args.get('sort', 'date')
    if sort_by == 'similarity':
        filtered_pairs = filter_query.order_by(EmbeddingFilterLog.similarity_score.desc()).limit(500).all()
    else:
        filtered_pairs = filter_query.order_by(EmbeddingFilterLog.filtered_at.desc()).limit(500).all()

    # ─── Escalations Tab ───
    esc_query = EscalationLog.query.filter(
        EscalationLog.escalated_at >= from_date,
        EscalationLog.escalated_at <= to_date
    )

    if score_band == '60-69':
        esc_query = esc_query.filter(EscalationLog.mini_score >= 60, EscalationLog.mini_score < 70)
    elif score_band == '70-79':
        esc_query = esc_query.filter(EscalationLog.mini_score >= 70, EscalationLog.mini_score < 80)
    elif score_band == '80-85':
        esc_query = esc_query.filter(EscalationLog.mini_score >= 80, EscalationLog.mini_score <= 85)

    esc_sort = request.args.get('esc_sort', 'date')
    if esc_sort == 'delta':
        escalations = esc_query.order_by(EscalationLog.score_delta.desc()).limit(500).all()
    else:
        escalations = esc_query.order_by(EscalationLog.escalated_at.desc()).limit(500).all()

    # ─── Summary Banner (today's stats) ───
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    today_filtered = EmbeddingFilterLog.query.filter(
        EmbeddingFilterLog.filtered_at >= today_start
    ).count()

    today_escalated = EscalationLog.query.filter(
        EscalationLog.escalated_at >= today_start
    ).count()

    today_passed = CandidateJobMatch.query.filter(
        CandidateJobMatch.created_at >= today_start
    ).count()

    today_total = today_filtered + today_passed
    today_rate = round((today_filtered / today_total * 100), 1) if today_total > 0 else 0.0

    # Estimated savings
    savings_per_filter = 0.003 - 0.00002  # Layer 2 cost minus embedding cost
    savings_per_pass = 0.03 - 0.003       # Layer 3 cost minus Layer 2 cost
    today_savings = round(today_filtered * savings_per_filter + today_passed * savings_per_pass, 2)

    summary = {
        'today_filtered': today_filtered,
        'today_escalated': today_escalated,
        'today_total': today_total,
        'today_rate': today_rate,
        'today_savings': today_savings,
    }

    return render_template('embedding_audit.html',
                          filtered_pairs=filtered_pairs,
                          escalations=escalations,
                          summary=summary,
                          date_from=date_from or from_date.strftime('%Y-%m-%d'),
                          date_to=date_to or '',
                          sim_min=sim_min,
                          sim_max=sim_max,
                          score_band=score_band,
                          sort=sort_by,
                          esc_sort=esc_sort,
                          active_tab=active_tab,
                          active_page='screening_config')


@vetting_bp.route('/screening/embedding-audit/filtered-csv')
@login_required
def export_filtered_csv():
    """Export filtered pairs as CSV."""
    from models import EmbeddingFilterLog
    from flask import Response
    from defusedcsv import csv
    import io

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    if not date_from:
        from_date = datetime.utcnow() - timedelta(days=7)
    else:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
        except ValueError:
            from_date = datetime.utcnow() - timedelta(days=7)

    if not date_to:
        to_date = datetime.utcnow()
    else:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            to_date = datetime.utcnow()

    logs = EmbeddingFilterLog.query.filter(
        EmbeddingFilterLog.filtered_at >= from_date,
        EmbeddingFilterLog.filtered_at <= to_date
    ).order_by(EmbeddingFilterLog.filtered_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Candidate ID', 'Candidate Name', 'Job ID', 'Job Title',
                     'Similarity Score', 'Threshold', 'Resume Snippet'])

    def _sanitize_csv(val):
        """Prevent CSV formula injection by escaping dangerous leading characters."""
        s = str(val) if val is not None else ''
        if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
            return "'" + s
        return s

    for log in logs:
        writer.writerow([
            log.filtered_at.strftime('%Y-%m-%d %H:%M') if log.filtered_at else '',
            log.bullhorn_candidate_id,
            _sanitize_csv(log.candidate_name or ''),
            log.bullhorn_job_id,
            _sanitize_csv(log.job_title or ''),
            f'{log.similarity_score:.4f}' if log.similarity_score else '',
            f'{log.threshold_used:.2f}' if log.threshold_used else '',
            _sanitize_csv((log.resume_snippet or '')[:200])
        ])

    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename=filtered_pairs_{datetime.utcnow().strftime("%Y%m%d")}.csv'
    return response


@vetting_bp.route('/screening/embedding-audit/escalations-csv')
@login_required
def export_escalations_csv():
    """Export escalations as CSV."""
    from models import EscalationLog
    from flask import Response
    from defusedcsv import csv
    import io

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    if not date_from:
        from_date = datetime.utcnow() - timedelta(days=7)
    else:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
        except ValueError:
            from_date = datetime.utcnow() - timedelta(days=7)

    if not date_to:
        to_date = datetime.utcnow()
    else:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            to_date = datetime.utcnow()

    logs = EscalationLog.query.filter(
        EscalationLog.escalated_at >= from_date,
        EscalationLog.escalated_at <= to_date
    ).order_by(EscalationLog.escalated_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Candidate ID', 'Candidate Name', 'Job ID', 'Job Title',
                     'Layer 2 Score', 'Layer 3 Score', 'Delta', 'Material Change',
                     'Crossed Threshold', 'Threshold'])

    def _sanitize_csv_esc(val):
        s = str(val) if val is not None else ''
        if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
            return "'" + s
        return s

    for log in logs:
        writer.writerow([
            log.escalated_at.strftime('%Y-%m-%d %H:%M') if log.escalated_at else '',
            log.bullhorn_candidate_id,
            _sanitize_csv_esc(log.candidate_name or ''),
            log.bullhorn_job_id,
            _sanitize_csv_esc(log.job_title or ''),
            f'{log.mini_score:.0f}' if log.mini_score else '',
            f'{log.gpt4o_score:.0f}' if log.gpt4o_score else '',
            f'{log.score_delta:+.0f}' if log.score_delta else '',
            'Yes' if log.material_change else 'No',
            'Yes' if log.crossed_threshold else 'No',
            f'{log.threshold_used:.0f}' if log.threshold_used else ''
        ])

    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename=escalations_{datetime.utcnow().strftime("%Y%m%d")}.csv'
    return response


@vetting_bp.route('/screening/block-retry/<int:log_id>', methods=['POST'])
@csrf.exempt
@login_required
def block_retry(log_id):
    """Mark a vetting log as retry-blocked so the auto-retry cycle skips it permanently."""
    from models import CandidateVettingLog
    from flask_login import current_user
    if not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    log = CandidateVettingLog.query.get_or_404(log_id)
    if request.is_json:
        data = request.get_json(force=True, silent=True) or {}
        reason = data.get('reason', '').strip()
    else:
        reason = request.form.get('reason', '').strip()
    log.retry_blocked = True
    log.retry_block_reason = reason or None
    db.session.commit()
    return jsonify({
        'success': True,
        'log_id': log_id,
        'candidate_name': log.candidate_name,
        'reason': log.retry_block_reason
    })


@vetting_bp.route('/screening/unblock-retry/<int:log_id>', methods=['POST'])
@csrf.exempt
@login_required
def unblock_retry(log_id):
    """Remove the retry block from a vetting log — candidate re-enters the auto-retry cycle."""
    from models import CandidateVettingLog
    from flask_login import current_user
    if not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    log = CandidateVettingLog.query.get_or_404(log_id)
    log.retry_blocked = False
    log.retry_block_reason = None
    db.session.commit()
    return jsonify({
        'success': True,
        'log_id': log_id,
        'candidate_name': log.candidate_name
    })


@vetting_bp.route('/screening/dismiss-pending/<int:log_id>', methods=['POST'])
@csrf.exempt
@login_required
def dismiss_pending(log_id):
    """Dismiss a stuck pending/processing candidate — removes them from the awaiting vetting queue permanently."""
    from models import CandidateVettingLog
    from flask_login import current_user
    if not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    log = CandidateVettingLog.query.get_or_404(log_id)
    if log.status not in ('pending', 'processing'):
        return jsonify({'success': False, 'error': 'Candidate is not in a pending state'}), 400

    log.status = 'dismissed'
    log.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        'success': True,
        'log_id': log_id,
        'candidate_name': log.candidate_name
    })
