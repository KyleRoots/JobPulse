"""Diagnostic JSON endpoints, activity monitor, lock & note operations."""
from datetime import datetime, timedelta

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from routes.vetting import vetting_bp
from routes.vetting_handlers._shared import get_db


@vetting_bp.route('/screening/diagnostic')
@login_required
def vetting_diagnostic():
    """Temporary diagnostic endpoint to investigate vetting backlog"""
    from models import ParsedEmail, CandidateVettingLog, VettingConfig
    from sqlalchemy import func, case
    from screening.detection import _resolve_vetting_cutoff

    db = get_db()
    min_bh_id = request.args.get('min_bh_id', 4586546, type=int)

    # Overall ParsedEmail stats — split unvetted into "actionable" (post-cutoff)
    # vs "pre_cutoff_excluded" so operators don't keep mistaking the historical
    # backlog of pre-cutoff records for an actual processing queue.
    cutoff_dt = _resolve_vetting_cutoff()
    unvetted_predicate = (
        (ParsedEmail.status == 'completed')
        & (ParsedEmail.bullhorn_candidate_id.isnot(None))
        & (ParsedEmail.vetted_at.is_(None))
    )
    if cutoff_dt is not None:
        pending_eligible_predicate = unvetted_predicate & (ParsedEmail.received_at >= cutoff_dt)
    else:
        pending_eligible_predicate = unvetted_predicate

    stats = db.session.query(
        func.count(ParsedEmail.id).label('total'),
        func.count(case((ParsedEmail.status == 'completed', 1))).label('completed'),
        func.count(case((
            (ParsedEmail.status == 'completed') & (ParsedEmail.bullhorn_candidate_id.isnot(None)),
            1
        ))).label('with_bh_id'),
        func.count(case((unvetted_predicate, 1))).label('unvetted_eligible'),
        func.count(case((pending_eligible_predicate, 1))).label('pending_eligible'),
        func.count(case((
            (ParsedEmail.status == 'completed') & (ParsedEmail.bullhorn_candidate_id.isnot(None)) & (ParsedEmail.vetted_at.isnot(None)),
            1
        ))).label('already_vetted'),
    ).first()

    # Records with BH ID above threshold
    above_threshold = ParsedEmail.query.filter(
        ParsedEmail.bullhorn_candidate_id >= min_bh_id,
        ParsedEmail.status == 'completed'
    ).order_by(ParsedEmail.bullhorn_candidate_id.desc()).limit(100).all()

    records = []
    for pe in above_threshold:
        vetting_log = CandidateVettingLog.query.filter_by(
            bullhorn_candidate_id=pe.bullhorn_candidate_id
        ).order_by(CandidateVettingLog.created_at.desc()).first()

        records.append({
            'id': pe.id,
            'bullhorn_candidate_id': pe.bullhorn_candidate_id,
            'bullhorn_job_id': pe.bullhorn_job_id,
            'candidate_name': pe.candidate_name,
            'status': pe.status,
            'vetted_at': pe.vetted_at.isoformat() if pe.vetted_at else None,
            'processed_at': pe.processed_at.isoformat() if pe.processed_at else None,
            'received_at': pe.received_at.isoformat() if pe.received_at else None,
            'is_duplicate': pe.is_duplicate_candidate,
            'has_vetting_log': vetting_log is not None,
            'vetting_log_status': vetting_log.status if vetting_log else None,
            'vetting_log_created': vetting_log.created_at.isoformat() if vetting_log else None,
            'note_created': vetting_log.note_created if vetting_log else None,
        })

    last_check = VettingConfig.query.filter_by(setting_key='last_check_timestamp').first()
    last_run = VettingConfig.query.filter_by(setting_key='last_run_timestamp').first()
    vetting_enabled = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
    lock_in_progress = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
    lock_time = VettingConfig.query.filter_by(setting_key='vetting_lock_time').first()
    batch_size = VettingConfig.query.filter_by(setting_key='batch_size').first()

    return jsonify({
        'overall_stats': {
            'total_parsed_emails': stats.total,
            'completed': stats.completed,
            'with_bullhorn_id': stats.with_bh_id,
            # `unvetted_eligible` retained for backward-compat with prior
            # consumers; semantically equals `total_unvetted` (pre + post cutoff).
            'unvetted_eligible': stats.unvetted_eligible,
            'total_unvetted': stats.unvetted_eligible,
            'pending_eligible': stats.pending_eligible,
            'pre_cutoff_excluded': stats.unvetted_eligible - stats.pending_eligible,
            'cutoff_active': cutoff_dt.isoformat() if cutoff_dt else None,
            'already_vetted': stats.already_vetted,
        },
        'vetting_config': {
            'vetting_enabled': vetting_enabled.setting_value if vetting_enabled else None,
            'last_check_timestamp': last_check.setting_value if last_check else None,
            'last_run_timestamp': last_run.setting_value if last_run else None,
            'vetting_in_progress': lock_in_progress.setting_value if lock_in_progress else None,
            'vetting_lock_time': lock_time.setting_value if lock_time else None,
            'batch_size': batch_size.setting_value if batch_size else None,
        },
        'records_above_threshold': records,
        'threshold': min_bh_id,
        'count': len(records)
    })


@vetting_bp.route('/screening/activity-monitor')
@login_required
def activity_monitor():
    """Real-time screening pipeline activity monitor (super-admin only)"""
    from flask_login import current_user

    if not getattr(current_user, 'is_admin', False):
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        return _activity_monitor_data()
    except Exception as e:
        current_app.logger.error(f"Activity monitor error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


def _activity_monitor_data():
    from models import ParsedEmail, CandidateVettingLog, VettingConfig, VettingHealthCheck
    from sqlalchemy import func, case

    db = get_db()
    now = datetime.utcnow()
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(hours=24)

    cutoff_config = VettingConfig.query.filter_by(setting_key='vetting_cutoff_date').first()
    cutoff_date = None
    if cutoff_config and cutoff_config.setting_value:
        try:
            cutoff_date = datetime.strptime(cutoff_config.setting_value, '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            cutoff_date = None

    queue_base = ParsedEmail.query.filter(
        ParsedEmail.status == 'completed',
        ParsedEmail.bullhorn_candidate_id.isnot(None),
        ParsedEmail.vetted_at.is_(None)
    )
    if cutoff_date:
        queue_base = queue_base.filter(ParsedEmail.received_at >= cutoff_date)
    queue_pending = queue_base.count()

    pipeline_stats = db.session.query(
        func.count(case((CandidateVettingLog.updated_at >= day_ago, 1))).label('processed_24h'),
        func.count(case((CandidateVettingLog.updated_at >= hour_ago, 1))).label('processed_1h'),
        func.count(case(((CandidateVettingLog.updated_at >= day_ago) & (CandidateVettingLog.is_qualified == True), 1))).label('qualified_24h'),
        func.count(case((
            (CandidateVettingLog.updated_at >= day_ago) &
            (CandidateVettingLog.is_qualified == False) &
            (
                (CandidateVettingLog.highest_match_score > 0) |
                (
                    (CandidateVettingLog.highest_match_score == 0) &
                    (CandidateVettingLog.error_message.is_(None)) &
                    (CandidateVettingLog.total_jobs_matched.isnot(None))
                )
            ), 1
        ))).label('not_qualified_24h'),
        func.count(case((
            (CandidateVettingLog.updated_at >= day_ago) &
            (CandidateVettingLog.highest_match_score == 0) &
            (
                (CandidateVettingLog.error_message.isnot(None)) |
                (CandidateVettingLog.total_jobs_matched.is_(None))
            ), 1
        ))).label('incomplete_24h'),
    ).filter(CandidateVettingLog.status == 'completed', CandidateVettingLog.is_sandbox != True).first()

    def _derive_note_action(is_qualified, score, error_message=None, total_jobs_matched=None):
        if score is not None and score == 0:
            if error_message or total_jobs_matched is None:
                return 'Scout Screen - Incomplete'
            return 'Scout Screen - Not Qualified'
        if is_qualified:
            return 'Scout Screen - Qualified'
        return 'Scout Screen - Not Qualified'

    recent_24h = CandidateVettingLog.query.filter(
        CandidateVettingLog.status == 'completed',
        CandidateVettingLog.updated_at >= day_ago,
        CandidateVettingLog.is_sandbox != True
    ).all()

    breakdown_counts = {}
    for r in recent_24h:
        action = _derive_note_action(r.is_qualified, r.highest_match_score, r.error_message, r.total_jobs_matched)
        breakdown_counts[action] = breakdown_counts.get(action, 0) + 1
    result_breakdown_24h = list(breakdown_counts.items())

    recent = CandidateVettingLog.query.filter(
        CandidateVettingLog.status == 'completed',
        CandidateVettingLog.is_sandbox != True
    ).order_by(CandidateVettingLog.updated_at.desc()).limit(20).all()

    recent_list = []
    for r in recent:
        recent_list.append({
            'candidate_name': r.candidate_name or 'Unknown',
            'bullhorn_id': r.bullhorn_candidate_id,
            'score': r.highest_match_score,
            'is_qualified': r.is_qualified,
            'note_action': _derive_note_action(r.is_qualified, r.highest_match_score, r.error_message, r.total_jobs_matched),
            'jobs_analyzed': r.total_jobs_matched or 0,
            'completed_at': r.updated_at.strftime('%b %d, %I:%M %p') if r.updated_at else '',
            'timestamp': r.updated_at.isoformat() if r.updated_at else ''
        })

    last_run = VettingConfig.query.filter_by(setting_key='last_run_timestamp').first()
    lock_in_progress = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
    vetting_enabled = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()

    latest_health = VettingHealthCheck.query.order_by(VettingHealthCheck.check_time.desc()).first()
    health_info = None
    if latest_health:
        health_info = {
            'is_healthy': latest_health.is_healthy,
            'bullhorn_status': latest_health.bullhorn_status,
            'openai_status': latest_health.openai_status,
            'database_status': latest_health.database_status,
            'scheduler_status': latest_health.scheduler_status,
            'check_time': latest_health.check_time.strftime('%b %d, %I:%M %p') if latest_health.check_time else ''
        }

    throughput = 0
    if pipeline_stats.processed_1h and pipeline_stats.processed_1h > 0:
        throughput = pipeline_stats.processed_1h
    if queue_pending == 0:
        est_hours_remaining = 0
    elif throughput > 0:
        est_hours_remaining = round(queue_pending / throughput, 1)
    else:
        est_hours_remaining = None

    return jsonify({
        'queue': {
            'pending': queue_pending,
            'cutoff_active': cutoff_date is not None,
            'cutoff_date': cutoff_config.setting_value if cutoff_config and cutoff_config.setting_value else None
        },
        'pipeline': {
            'processed_24h': pipeline_stats.processed_24h,
            'processed_1h': pipeline_stats.processed_1h,
            'qualified_24h': pipeline_stats.qualified_24h,
            'not_qualified_24h': pipeline_stats.not_qualified_24h,
            'incomplete_24h': pipeline_stats.incomplete_24h,
            'throughput_per_hour': throughput,
            'est_hours_remaining': est_hours_remaining
        },
        'result_breakdown': {row[0]: row[1] for row in result_breakdown_24h},
        'recent_activity': recent_list,
        'system': {
            'vetting_enabled': (vetting_enabled.setting_value or '').lower() == 'true' if vetting_enabled else False,
            'is_locked': (lock_in_progress.setting_value or '').lower() == 'true' if lock_in_progress else False,
            'last_run': last_run.setting_value if last_run else 'Never',
            'health': health_info
        }
    })


@vetting_bp.route('/screening/force-release-lock', methods=['POST'])
@login_required
def force_release_lock():
    """Force release a stuck vetting lock"""
    from models import VettingConfig

    db = get_db()

    try:
        lock = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
        lock_time = VettingConfig.query.filter_by(setting_key='vetting_lock_time').first()

        old_lock_value = lock.setting_value if lock else 'not set'
        old_lock_time = lock_time.setting_value if lock_time else 'not set'

        if lock:
            lock.setting_value = 'false'
        if lock_time:
            lock_time.setting_value = ''

        db.session.commit()

        current_app.logger.info(f"Force released vetting lock (was: {old_lock_value}, time: {old_lock_time})")
        flash(f'Vetting lock force-released. Previous state: lock={old_lock_value}, time={old_lock_time}', 'success')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error force-releasing lock: {str(e)}")
        flash(f'Error releasing lock: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/retry-failed-notes', methods=['POST'])
@login_required
def retry_failed_notes():
    """Retry creating Bullhorn notes for vetting logs where note creation previously failed.

    Processes in batches to prevent request timeouts. Call multiple times if needed.
    Accepts optional batch_size parameter (default 50, max 100).
    """
    from models import CandidateVettingLog
    from candidate_vetting_service import CandidateVettingService

    db = get_db()

    # Parse batch size from form or default to 50
    try:
        batch_size = int(request.form.get('batch_size', 50))
        batch_size = max(1, min(batch_size, 100))  # Clamp to 1-100
    except (ValueError, TypeError):
        batch_size = 50

    try:
        # Count total failed before applying batch limit
        total_failed = CandidateVettingLog.query.filter(
            CandidateVettingLog.status == 'completed',
            CandidateVettingLog.note_created == False,
            CandidateVettingLog.is_sandbox != True
        ).count()

        if total_failed == 0:
            flash('No failed notes to retry — all completed vetting logs have notes.', 'info')
            return redirect(url_for('vetting.vetting_settings'))

        failed_logs = CandidateVettingLog.query.filter(
            CandidateVettingLog.status == 'completed',
            CandidateVettingLog.note_created == False,
            CandidateVettingLog.is_sandbox != True
        ).order_by(CandidateVettingLog.analyzed_at.desc()).limit(batch_size).all()

        vetting_service = CandidateVettingService()
        success_count = 0
        fail_count = 0

        for log in failed_logs:
            try:
                if vetting_service.create_candidate_note(log):
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                current_app.logger.error(f"Error retrying note for candidate {log.bullhorn_candidate_id}: {str(e)}")
                fail_count += 1

        remaining = total_failed - success_count
        msg = f'Note retry batch complete: {success_count} created, {fail_count} failed (batch of {len(failed_logs)}).'
        if remaining > 0:
            msg += f' {remaining} still pending — click again to process the next batch.'

        flash(msg, 'success' if fail_count == 0 else 'warning')
        current_app.logger.info(
            f"Retry failed notes: {success_count}/{len(failed_logs)} succeeded in this batch, "
            f"{remaining} remaining of {total_failed} total"
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error retrying failed notes: {str(e)}")
        flash(f'Error retrying failed notes: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/sample-notes')
@login_required
def show_sample_notes():
    """Show sample note formats for qualified and non-qualified candidates"""

    qualified_note = """🎯 SCOUT SCREENING - QUALIFIED CANDIDATE

Analysis Date: 2026-01-29 12:45 UTC
Threshold: 80%
Qualified Matches: 2 of 5 jobs
Highest Match Score: 85%

QUALIFIED POSITIONS:

• Job ID: 34517 - Azure Integration Developer
  Match Score: 85%
  ⭐ APPLIED TO THIS POSITION
  Summary: Strong candidate with 5+ years of Azure experience including Logic Apps, Functions, and API Management.
  Skills: Azure Functions, Logic Apps, API Management, C#, .NET Core, SQL Server

• Job ID: 34520 - Senior Software Developer
  Match Score: 82%
  Summary: Solid technical background with full-stack development experience.
  Skills: Python, JavaScript, React, AWS, Docker, PostgreSQL"""

    not_qualified_note = """📋 SCOUT SCREENING - NOT RECOMMENDED

Analysis Date: 2026-01-29 12:45 UTC
Threshold: 80%
Highest Match Score: 62%
Jobs Analyzed: 5

This candidate did not meet the 80% match threshold for any current open positions.

TOP ANALYSIS RESULTS:

• Job ID: 34517 - Azure Integration Developer
  Match Score: 62%
  ⭐ APPLIED TO THIS POSITION
  Gaps: No direct Azure experience. Background is primarily in frontend development.

• Job ID: 34520 - Senior Software Developer
  Match Score: 58%
  Gaps: Entry-level experience (2 years vs 5+ required). No team lead experience."""

    return render_template('sample_notes.html',
                          qualified_note=qualified_note,
                          not_qualified_note=not_qualified_note)


@vetting_bp.route('/screening/create-test-note/<int:candidate_id>', methods=['POST'])
@login_required
def create_test_vetting_note(candidate_id):
    """Create a test vetting note on an actual Bullhorn candidate record"""
    from bullhorn_service import BullhornService

    note_type = request.form.get('note_type', 'qualified')

    try:
        bullhorn = BullhornService()
        if not bullhorn.authenticate():
            flash('Failed to authenticate with Bullhorn', 'error')
            return redirect(url_for('vetting.show_sample_notes'))

        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

        if note_type == 'qualified':
            note_text = f"""🎯 SCOUT SCREENING - QUALIFIED CANDIDATE

Analysis Date: {now}
Threshold: 80%
Qualified Matches: 2 of 5 jobs
Highest Match Score: 85%

QUALIFIED POSITIONS:

• Job ID: 34517 - Azure Integration Developer
  Match Score: 85%
  ⭐ APPLIED TO THIS POSITION
  Summary: Strong candidate with 5+ years of Azure experience.
  Skills: Azure Functions, Logic Apps, API Management, C#, .NET Core, SQL Server"""
            action = "Scout Screen - Qualified"
        else:
            note_text = f"""📋 SCOUT SCREENING - NOT RECOMMENDED

Analysis Date: {now}
Threshold: 80%
Highest Match Score: 62%
Jobs Analyzed: 5

This candidate did not meet the 80% match threshold for any current open positions."""
            action = "Scout Screen - Not Qualified"

        note_id = bullhorn.create_candidate_note(candidate_id, note_text, action=action)

        if note_id:
            flash(f'Successfully created {note_type.replace("_", " ")} test note on candidate {candidate_id}. Note ID: {note_id}', 'success')
        else:
            flash(f'Failed to create test note on candidate {candidate_id}.', 'error')

    except Exception as e:
        current_app.logger.error(f"Error creating test vetting note: {str(e)}")
        flash(f'Error creating test note: {str(e)}', 'error')

    return redirect(url_for('vetting.show_sample_notes'))
