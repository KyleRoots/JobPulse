"""Manual run / rescreen / re-vet / clean-slate dispatch endpoints."""
from datetime import datetime, timedelta

from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_login import login_required

from extensions import csrf
from routes.vetting import vetting_bp
from routes.vetting_handlers._shared import get_db


@vetting_bp.route('/screening/run', methods=['POST'])
@login_required
def run_vetting_now():
    """Manually trigger a vetting cycle (M3: pushed onto background scheduler).

    Previously ran the cycle inline on the request thread, which could exceed
    gunicorn's 300s --timeout for moderate batches and SIGKILL the worker
    mid-screening. Now hands off to the existing APScheduler job so the
    request returns immediately and screening progress shows on the System
    Health dashboard.
    """
    try:
        from models import VettingConfig
        from utils.screening_dispatch import enqueue_vetting_now

        config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
        if not config or (config.setting_value or '').lower() != 'true':
            flash('Vetting is disabled. Enable it first to run a cycle.', 'warning')
            return redirect(url_for('vetting.vetting_settings'))

        result = enqueue_vetting_now(reason='manual_run_now')
        if result['enqueued']:
            flash(
                f"{result['reason']} Watch the System Health dashboard for progress.",
                'success',
            )
        else:
            flash(result['reason'], 'warning')

    except Exception as e:
        current_app.logger.error(f"Error enqueuing vetting cycle: {str(e)}")
        flash(f'Error starting vetting cycle: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/rescreen-count', methods=['POST'])
@login_required
def rescreen_count():
    """Return count of candidates that would be re-screened for the given time window"""
    from models import ParsedEmail, CandidateVettingLog, CandidateJobMatch

    try:
        hours = int(request.form.get('hours', 6))
        hours = max(1, min(24, hours))
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        db = get_db()

        # Count vetted candidates in the time window
        vetted_count = ParsedEmail.query.filter(
            ParsedEmail.received_at >= cutoff,
            ParsedEmail.vetted_at.isnot(None),
            ParsedEmail.status == 'completed',
            ParsedEmail.bullhorn_candidate_id.isnot(None)
        ).count()

        # Count zero-score candidates (all job matches = 0) in the time window
        zero_score_ids = db.session.query(CandidateVettingLog.id).join(
            ParsedEmail,
            ParsedEmail.bullhorn_candidate_id == CandidateVettingLog.bullhorn_candidate_id
        ).filter(
            ParsedEmail.received_at >= cutoff,
            CandidateVettingLog.highest_match_score == 0,
            CandidateVettingLog.status == 'completed'
        ).all()
        zero_count = len(zero_score_ids)

        return jsonify({
            'success': True,
            'hours': hours,
            'vetted_count': vetted_count,
            'zero_score_count': zero_count,
            'total': vetted_count + zero_count
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@vetting_bp.route('/screening/rescreen-recent', methods=['POST'])
@login_required
def rescreen_recent():
    """Reset vetted_at for candidates in the selected time window who haven't been vetted OR received 0% scores"""
    from models import ParsedEmail, CandidateVettingLog, CandidateJobMatch

    db = get_db()

    try:
        hours = int(request.form.get('hours', 6))
        hours = max(1, min(24, hours))
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        # 1. Reset vetted_at for all completed candidates in the window
        reset_count = ParsedEmail.query.filter(
            ParsedEmail.received_at >= cutoff,
            ParsedEmail.vetted_at.isnot(None),
            ParsedEmail.status == 'completed',
            ParsedEmail.bullhorn_candidate_id.isnot(None)
        ).update({'vetted_at': None}, synchronize_session=False)

        # 2. Also include zero-score candidates: delete their vetting logs
        #    so duplicate-prevention won't skip them on the next cycle
        zero_score_logs = CandidateVettingLog.query.join(
            ParsedEmail,
            ParsedEmail.bullhorn_candidate_id == CandidateVettingLog.bullhorn_candidate_id
        ).filter(
            ParsedEmail.received_at >= cutoff,
            CandidateVettingLog.highest_match_score == 0,
            CandidateVettingLog.status == 'completed'
        ).all()

        zero_count = len(zero_score_logs)
        for log in zero_score_logs:
            CandidateJobMatch.query.filter_by(vetting_log_id=log.id).delete()
            db.session.delete(log)

        db.session.commit()

        total = reset_count + zero_count
        if total > 0:
            flash(f'Re-screening {total} candidates from the last {hours}h ({reset_count} reset + {zero_count} zero-score). Processing will begin shortly.', 'success')
            current_app.logger.info(f"Re-screen: reset {reset_count} vetted_at + {zero_count} zero-score logs from last {hours}h")

            # M3: hand off to background scheduler instead of running inline.
            # See utils/screening_dispatch.py for rationale.
            try:
                from utils.screening_dispatch import enqueue_vetting_now
                enqueue_vetting_now(reason='rescreen_recent')
            except Exception as e:
                current_app.logger.warning(f"Auto-trigger after rescreen failed: {e}")
        else:
            flash(f'No candidates found to re-screen in the last {hours}h.', 'info')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error during rescreen: {str(e)}")
        flash(f'Error during rescreen: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/rescreen-remote-misfires', methods=['POST'])
@login_required
@csrf.exempt
def rescreen_remote_misfires():
    """Find and re-screen candidates with remote location misfires from the last N hours.

    Identifies CandidateJobMatch records where:
    - Job work_type is 'Remote'
    - gaps_identified contains 'different country'
    - match_summary contains same-country affirmative evidence
    Then resets the candidate's vetted_at so they get re-vetted with the new enforcer.
    """
    from models import CandidateJobMatch, CandidateVettingLog, ParsedEmail

    db = get_db()

    try:
        hours = int(request.form.get('hours', 48))
        hours = max(1, min(168, hours))
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        same_country_phrases = [
            'matches the remote job',
            'matches the location requirement',
            'meets the location requirement',
            'meets the remote location',
            'matching the remote job',
            "matches the job's country",
            "matches the remote job's country",
            "matches the job's remote location",
            "matching the job location requirement",
            "matching the job's country requirement",
            "same country as the job",
            "matches the country requirement",
        ]

        misfire_matches = CandidateJobMatch.query.join(
            CandidateVettingLog,
            CandidateJobMatch.vetting_log_id == CandidateVettingLog.id
        ).filter(
            CandidateJobMatch.created_at >= cutoff,
            CandidateJobMatch.job_work_type == 'Remote',
            CandidateJobMatch.gaps_identified.ilike('%different country%'),
        ).all()

        negation_words = ['not ', "n't ", 'no ', 'does not ', "doesn't ", 'cannot ', 'outside ']
        affected_candidate_ids = set()
        affected_vetting_log_ids = set()
        affected_details = []
        for match in misfire_matches:
            summary_lower = (match.match_summary or '').lower()
            has_evidence = False
            for phrase in same_country_phrases:
                idx = summary_lower.find(phrase)
                if idx >= 0:
                    preceding = summary_lower[max(0, idx - 20):idx]
                    if not any(neg in preceding for neg in negation_words):
                        has_evidence = True
                        break
            if has_evidence:
                affected_candidate_ids.add(match.bullhorn_candidate_id)
                affected_vetting_log_ids.add(match.vetting_log_id)
                affected_details.append({
                    'candidate_id': match.bullhorn_candidate_id,
                    'candidate_name': match.candidate_name,
                    'job_id': match.bullhorn_job_id,
                    'job_title': match.job_title,
                    'match_score': match.match_score,
                })

        reset_count = 0
        deleted_logs = 0
        deleted_matches = 0
        if affected_candidate_ids:
            for log_id in affected_vetting_log_ids:
                match_del = CandidateJobMatch.query.filter_by(vetting_log_id=log_id).delete()
                deleted_matches += match_del
                log_obj = CandidateVettingLog.query.get(log_id)
                if log_obj:
                    db.session.delete(log_obj)
                    deleted_logs += 1

            reset_count = ParsedEmail.query.filter(
                ParsedEmail.bullhorn_candidate_id.in_(list(affected_candidate_ids)),
                ParsedEmail.vetted_at.isnot(None),
                ParsedEmail.status == 'completed',
            ).update({'vetted_at': None}, synchronize_session=False)
            db.session.commit()

            current_app.logger.info(
                f"🛡️ REMOTE MISFIRE RE-SCREEN: Found {len(affected_details)} misfire matches "
                f"across {len(affected_candidate_ids)} candidates in last {hours}h. "
                f"Deleted {deleted_logs} vetting logs + {deleted_matches} match records. "
                f"Reset {reset_count} ParsedEmail records for re-vetting."
            )
            for d in affected_details:
                current_app.logger.info(
                    f"  → Candidate {d['candidate_id']} ({d['candidate_name']}) "
                    f"on job {d['job_id']} ({d['job_title']}), score={d['match_score']}"
                )

            flash(
                f'Found {len(affected_details)} remote location misfires across '
                f'{len(affected_candidate_ids)} candidates. Reset {reset_count} for re-screening.',
                'success'
            )
        else:
            flash(f'No remote location misfires found in the last {hours} hours.', 'info')
            current_app.logger.info(f"🛡️ REMOTE MISFIRE RE-SCREEN: No misfires found in last {hours}h")

        return jsonify({
            'success': True,
            'hours': hours,
            'misfire_matches': len(affected_details),
            'candidates_affected': len(affected_candidate_ids),
            'vetting_logs_deleted': deleted_logs,
            'match_records_deleted': deleted_matches,
            'emails_reset': reset_count,
            'details': affected_details[:50],
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error during remote misfire rescreen: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@vetting_bp.route('/screening/start-fresh', methods=['POST'])
@login_required
def start_fresh():
    """Set vetting_cutoff_date to now and trigger an immediate vetting cycle"""
    from models import VettingConfig

    db = get_db()

    try:
        now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

        cutoff_config = VettingConfig.query.filter_by(setting_key='vetting_cutoff_date').first()
        if cutoff_config:
            cutoff_config.setting_value = now_utc
        else:
            cutoff_config = VettingConfig(setting_key='vetting_cutoff_date', setting_value=now_utc)
            db.session.add(cutoff_config)

        db.session.commit()
        current_app.logger.info(f"Start Fresh: set vetting_cutoff_date to {now_utc}")

        # M3: hand off to background scheduler instead of running inline.
        try:
            from utils.screening_dispatch import enqueue_vetting_now
            result = enqueue_vetting_now(reason='start_fresh')
            if result['enqueued']:
                flash(
                    f'Started fresh at {now_utc} UTC. Cutoff set — only new candidates '
                    'will be screened. Screening started in the background; watch the '
                    'System Health dashboard for progress.',
                    'success',
                )
            else:
                flash(
                    f'Cutoff set to {now_utc} UTC. Vetting cycle will begin on the '
                    f'next scheduled run. ({result["reason"]})',
                    'success',
                )
        except Exception as e:
            current_app.logger.warning(f"Auto-trigger after start fresh failed: {e}")
            flash(f'Cutoff set to {now_utc} UTC. Vetting cycle will begin on the next scheduled run.', 'success')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error during start fresh: {str(e)}")
        flash(f'Error: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/revet-candidate/<int:candidate_id>', methods=['POST'])
@csrf.exempt
@login_required
def revet_candidate(candidate_id):
    """Re-vet a specific candidate by clearing their existing vetting records.

    This removes the VettingLog and CandidateJobMatch records so the duplicate
    loop prevention won't skip them on the next cycle.
    Supports both form-based (redirect) and fetch-based (JSON) calls.
    """
    from models import ParsedEmail, CandidateVettingLog, CandidateJobMatch, EmbeddingFilterLog, EscalationLog
    from flask_login import current_user

    db = get_db()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json'

    if not current_user.is_admin:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Unauthorized — admin access required'}), 403
        flash('Unauthorized — admin access required', 'error')
        return redirect(url_for('scout_screening.dashboard'))

    try:
        parsed_emails = ParsedEmail.query.filter(
            ParsedEmail.bullhorn_candidate_id == candidate_id,
            ParsedEmail.status == 'completed'
        ).all()

        if not parsed_emails:
            if is_ajax:
                return jsonify({'success': False, 'error': f'No ParsedEmail records found for candidate {candidate_id}.'}), 404
            flash(f'No ParsedEmail records found for candidate {candidate_id}.', 'warning')
            return redirect(url_for('vetting.vetting_settings'))

        pe_ids = [pe.id for pe in parsed_emails]

        vetting_logs = CandidateVettingLog.query.filter(
            CandidateVettingLog.parsed_email_id.in_(pe_ids)
        ).all()

        log_ids = [vl.id for vl in vetting_logs]

        filter_count = 0
        escalation_count = 0
        match_count = 0
        if log_ids:
            filter_count = EmbeddingFilterLog.query.filter(
                EmbeddingFilterLog.vetting_log_id.in_(log_ids)
            ).delete(synchronize_session=False)

            escalation_count = EscalationLog.query.filter(
                EscalationLog.vetting_log_id.in_(log_ids)
            ).delete(synchronize_session=False)

            match_count = CandidateJobMatch.query.filter(
                CandidateJobMatch.vetting_log_id.in_(log_ids)
            ).delete(synchronize_session=False)

        log_count = 0
        if log_ids:
            log_count = CandidateVettingLog.query.filter(
                CandidateVettingLog.id.in_(log_ids)
            ).delete(synchronize_session=False)

        for pe in parsed_emails:
            pe.vetted_at = None

        db.session.commit()

        current_app.logger.info(
            f"🔄 Re-vet reset for candidate {candidate_id}: "
            f"cleared {log_count} vetting logs, {match_count} match records, "
            f"reset {len(pe_ids)} ParsedEmails"
        )

        msg = (f'Reset candidate {candidate_id} for re-vetting: cleared {log_count} vetting log(s) '
               f'and {match_count} match record(s). Will be processed in next vetting cycle.')

        if is_ajax:
            return jsonify({'success': True, 'message': msg})

        flash(msg, 'success')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error resetting candidate {candidate_id} for re-vet: {str(e)}")
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash(f'Error resetting candidate for re-vet: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/process-backlog', methods=['POST'])
@login_required
def process_backlog():
    """Process unvetted backlog manually - runs a vetting cycle bypassing the scheduler"""
    from models import VettingConfig

    db = get_db()

    try:
        # First, force-release any stuck lock
        lock = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
        if lock and lock.setting_value == 'true':
            lock.setting_value = 'false'
            db.session.commit()
            current_app.logger.info("Released stuck lock before backlog processing")

        # Get batch size from request or config
        batch_size = request.form.get('batch_size', '50', type=str)
        try:
            batch_size = int(batch_size)
            batch_size = max(1, min(batch_size, 100))  # Clamp to 1-100
        except (ValueError, TypeError):
            batch_size = 50

        # M3: push backlog cycle onto background scheduler instead of
        # running inline on the request thread. The previous inline call
        # could exceed gunicorn's 300s timeout for large backlogs and
        # SIGKILL the worker mid-batch.
        enabled_config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
        if not enabled_config or (enabled_config.setting_value or '').lower() != 'true':
            flash('Vetting is disabled. Enable it first.', 'warning')
            return redirect(url_for('vetting.vetting_settings'))

        from utils.screening_dispatch import enqueue_vetting_now
        result = enqueue_vetting_now(reason=f'process_backlog_batch_{batch_size}')
        if result['enqueued']:
            flash(
                f'Backlog processing started in the background (batch size {batch_size}). '
                'Watch the System Health dashboard for progress.',
                'success',
            )
        else:
            flash(
                f'Could not start backlog processing: {result["reason"]}',
                'warning',
            )

        current_app.logger.info(f"Manual backlog processing enqueued: {result}")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error during backlog processing: {str(e)}")
        flash(f'Error during backlog processing: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/full-clean-slate', methods=['POST'])
@login_required
def full_clean_slate():
    """Complete reset of all vetting data - dashboard shows all zeros"""
    from models import CandidateVettingLog, CandidateJobMatch, VettingConfig, ParsedEmail

    db = get_db()

    try:
        # Delete all job matches
        match_count = CandidateJobMatch.query.delete()

        # Delete all vetting logs
        log_count = CandidateVettingLog.query.delete()

        # Reset vetted_at on all ParsedEmail records
        reset_count = ParsedEmail.query.filter(
            ParsedEmail.vetted_at.isnot(None)
        ).update({'vetted_at': None}, synchronize_session=False)

        # Reset the last check timestamp
        settings = VettingConfig.query.first()
        if settings:
            settings.last_check_timestamp = datetime.utcnow()

        db.session.commit()

        flash(f'Full Clean Slate complete! Deleted {log_count} vetting logs, {match_count} job matches, reset {reset_count} applications. Dashboard now shows all zeros.', 'success')
        current_app.logger.info(f"Full Clean Slate: Deleted {log_count} logs, {match_count} matches, reset {reset_count} vetted_at timestamps")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error during full clean slate: {str(e)}")
        flash(f'Error during clean slate: {str(e)}', 'error')

    return redirect(url_for('vetting.vetting_settings'))
