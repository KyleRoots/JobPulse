"""
Vetting Routes Blueprint
AI Candidate Vetting settings, operations, and job requirements management
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required
from routes import register_module_guard
from datetime import datetime, timedelta
import logging

vetting_bp = Blueprint('vetting', __name__)
register_module_guard(vetting_bp, 'scout_screening', 'scout_vetting')


def get_db():
    """Get database instance from app context"""
    from app import db
    return db


@vetting_bp.route('/screening')
@login_required
def vetting_settings():
    """AI Candidate Vetting settings and activity page"""
    from models import VettingConfig, CandidateVettingLog, JobVettingRequirements, VettingHealthCheck
    from sqlalchemy import func
    
    db = get_db()
    
    # Get settings (batch query: 1 query instead of 6)
    settings = {
        'vetting_enabled': False,
        'send_recruiter_emails': False,
        'match_threshold': 80,
        'batch_size': 25,
        'admin_notification_email': '',
        'health_alert_email': '',
        'embedding_similarity_threshold': 0.25,
        'vetting_cutoff_date': '',
        'global_custom_requirements': ''
    }
    
    all_configs = VettingConfig.query.filter(
        VettingConfig.setting_key.in_(settings.keys())
    ).all()
    config_map = {c.setting_key: c.setting_value for c in all_configs}
    
    for key in settings.keys():
        value = config_map.get(key)
        if value is not None:
            if key in ('vetting_enabled', 'send_recruiter_emails'):
                settings[key] = value.lower() == 'true'
            elif key in ('match_threshold', 'batch_size'):
                try:
                    settings[key] = int(value)
                except (ValueError, TypeError):
                    settings[key] = 80 if key == 'match_threshold' else 25
            elif key == 'embedding_similarity_threshold':
                try:
                    settings[key] = float(value)
                except (ValueError, TypeError):
                    settings[key] = 0.25
            else:
                settings[key] = value or ''
    
    # Get stats — single aggregated query (4 queries → 1)
    from sqlalchemy import case
    stats_row = db.session.query(
        func.count(case((CandidateVettingLog.status == 'completed', 1))).label('total_processed'),
        func.count(case(((CandidateVettingLog.status == 'completed') & (CandidateVettingLog.is_qualified == True), 1))).label('qualified'),
        func.coalesce(func.sum(case((CandidateVettingLog.status == 'completed', CandidateVettingLog.notification_count))), 0).label('notifications_sent'),
        func.count(case((CandidateVettingLog.status.in_(['pending', 'processing']), 1))).label('pending'),
    ).first()
    
    stats = {
        'total_processed': stats_row.total_processed,
        'qualified': stats_row.qualified,
        'notifications_sent': stats_row.notifications_sent,
        'pending': stats_row.pending
    }
    
    # Get recent activity
    recent_activity = CandidateVettingLog.query.order_by(
        CandidateVettingLog.created_at.desc()
    ).limit(50).all()
    
    # Get recommended candidates (limit 30 — template renders max 30)
    recommended_candidates = CandidateVettingLog.query.filter_by(
        status='completed', 
        is_qualified=True
    ).order_by(CandidateVettingLog.created_at.desc()).limit(30).all()
    
    # Get not recommended candidates (limit 30 — template renders max 30)
    not_recommended_candidates = CandidateVettingLog.query.filter_by(
        status='completed',
        is_qualified=False
    ).order_by(CandidateVettingLog.created_at.desc()).limit(30).all()
    
    # Get job requirements - filtered to only show active tearsheet jobs
    # P1 optimization: read the class-level cache directly instead of
    # instantiating the full CandidateVettingService (avoids constructing
    # OpenAI, SendGrid, EmbeddingService clients just for a set of IDs).
    # The cache is kept warm by the background scheduler job (P0).
    from candidate_vetting_service import CandidateVettingService
    active_job_ids = CandidateVettingService._active_job_ids_cache
    
    if active_job_ids:
        job_requirements = JobVettingRequirements.query.filter(
            JobVettingRequirements.bullhorn_job_id.in_(active_job_ids)
        ).order_by(JobVettingRequirements.updated_at.desc()).all()
    else:
        # Cache not warm yet (first boot) — show all requirements
        job_requirements = JobVettingRequirements.query.order_by(
            JobVettingRequirements.updated_at.desc()
        ).limit(50).all()
    
    # Get latest health check
    latest_health = VettingHealthCheck.query.order_by(
        VettingHealthCheck.check_time.desc()
    ).first()
    
    # Get recent health issues
    day_ago = datetime.utcnow() - timedelta(hours=24)
    recent_issues = VettingHealthCheck.query.filter(
        VettingHealthCheck.is_healthy == False,
        VettingHealthCheck.check_time >= day_ago
    ).order_by(VettingHealthCheck.check_time.desc()).limit(10).all()
    
    # Get pending candidates
    pending_candidates = CandidateVettingLog.query.filter(
        CandidateVettingLog.status.in_(['pending', 'processing'])
    ).order_by(CandidateVettingLog.created_at.desc()).limit(50).all()
    
    # Get recently vetted candidates
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_vetting = CandidateVettingLog.query.filter(
        CandidateVettingLog.status == 'completed',
        CandidateVettingLog.updated_at >= week_ago
    ).order_by(CandidateVettingLog.updated_at.desc()).limit(50).all()
    
    return render_template('vetting_settings.html', 
                          settings=settings, 
                          stats=stats, 
                          recent_activity=recent_activity,
                          recommended_candidates=recommended_candidates,
                          not_recommended_candidates=not_recommended_candidates,
                          job_requirements=job_requirements,
                          latest_health=latest_health,
                          recent_issues=recent_issues,
                          pending_candidates=pending_candidates,
                          recent_vetting=recent_vetting,
                          active_page='screening')


@vetting_bp.route('/screening/save', methods=['POST'])
@login_required
def save_vetting_settings():
    """Save AI vetting settings"""
    from models import VettingConfig
    
    db = get_db()
    
    try:
        # Get form values
        vetting_enabled = 'vetting_enabled' in request.form
        send_recruiter_emails = 'send_recruiter_emails' in request.form
        match_threshold = request.form.get('match_threshold', '80')
        batch_size = request.form.get('batch_size', '25')
        admin_email = request.form.get('admin_notification_email', '')
        health_alert_email = request.form.get('health_alert_email', '')
        embedding_threshold = request.form.get('embedding_similarity_threshold', '0.25')
        vetting_cutoff = request.form.get('vetting_cutoff_date', '').strip()
        global_custom_requirements = request.form.get('global_custom_requirements', '').strip()
        
        # Validate threshold
        try:
            threshold = int(match_threshold)
            if threshold < 50 or threshold > 100:
                threshold = 80
        except ValueError:
            threshold = 80
        
        # Validate batch size
        try:
            batch = int(batch_size)
            if batch < 1 or batch > 100:
                batch = 25
        except ValueError:
            batch = 25
        
        # Validate embedding similarity threshold
        try:
            emb_thresh = float(embedding_threshold)
            if emb_thresh < 0.0 or emb_thresh > 1.0:
                emb_thresh = 0.25
        except ValueError:
            emb_thresh = 0.25
        
        # Validate vetting cutoff date (if provided)
        if vetting_cutoff:
            try:
                datetime.strptime(vetting_cutoff, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                flash('Invalid cutoff date format. Use YYYY-MM-DD HH:MM:SS', 'error')
                return redirect(url_for('vetting.vetting_settings'))
        
        # Update settings
        settings_to_save = [
            ('vetting_enabled', 'true' if vetting_enabled else 'false'),
            ('send_recruiter_emails', 'true' if send_recruiter_emails else 'false'),
            ('match_threshold', str(threshold)),
            ('batch_size', str(batch)),
            ('admin_notification_email', admin_email),
            ('health_alert_email', health_alert_email),
            ('embedding_similarity_threshold', str(emb_thresh)),
            ('vetting_cutoff_date', vetting_cutoff),
            ('global_custom_requirements', global_custom_requirements)
        ]
        
        for key, value in settings_to_save:
            config = VettingConfig.query.filter_by(setting_key=key).first()
            if config:
                config.setting_value = value
            else:
                config = VettingConfig(setting_key=key, setting_value=value)
                db.session.add(config)
        
        # Retry-on-lock: handles SQLite concurrency and transient DB errors
        from sqlalchemy.exc import OperationalError
        import time
        for attempt in range(3):
            try:
                db.session.commit()
                break
            except OperationalError as oe:
                if 'database is locked' in str(oe) and attempt < 2:
                    db.session.rollback()
                    time.sleep(0.5 * (attempt + 1))
                    current_app.logger.warning(f"⚠️ DB lock on settings save, retry {attempt + 1}/3")
                else:
                    raise
        flash('Vetting settings saved successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving vetting settings: {str(e)}")
        flash(f'Error saving settings: {str(e)}', 'error')
    
    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/health-check', methods=['POST'])
@login_required
def run_health_check_now():
    """Manually trigger a health check"""
    try:
        from app import run_vetting_health_check
        run_vetting_health_check()
        flash('Health check completed successfully!', 'success')
    except Exception as e:
        current_app.logger.error(f"Manual health check error: {str(e)}")
        flash(f'Health check error: {str(e)}', 'error')
    
    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/run', methods=['POST'])
@login_required
def run_vetting_now():
    """Manually trigger a vetting cycle"""
    try:
        from candidate_vetting_service import CandidateVettingService
        
        vetting_service = CandidateVettingService()
        summary = vetting_service.run_vetting_cycle()
        
        if summary.get('status') == 'disabled':
            flash('Vetting is disabled. Enable it first to run a cycle.', 'warning')
        else:
            processed = summary.get('candidates_processed', 0)
            qualified = summary.get('candidates_qualified', 0)
            notified = summary.get('notifications_sent', 0)
            
            if processed > 0:
                flash(f'Vetting cycle complete: {processed} candidates processed, '
                      f'{qualified} qualified, {notified} notifications sent.', 'success')
            else:
                flash('Vetting cycle complete: No new candidates to process.', 'info')
                
    except Exception as e:
        current_app.logger.error(f"Error running vetting cycle: {str(e)}")
        flash(f'Error running vetting cycle: {str(e)}', 'error')
    
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
            
            # Trigger immediate vetting cycle
            try:
                from candidate_vetting_service import CandidateVettingService
                vetting_service = CandidateVettingService()
                vetting_service.run_vetting_cycle()
            except Exception as e:
                current_app.logger.warning(f"Auto-trigger after rescreen failed: {e}")
        else:
            flash(f'No candidates found to re-screen in the last {hours}h.', 'info')
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error during rescreen: {str(e)}")
        flash(f'Error during rescreen: {str(e)}', 'error')
    
    return redirect(url_for('vetting.vetting_settings'))


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
        
        # Trigger immediate vetting cycle
        try:
            from candidate_vetting_service import CandidateVettingService
            vetting_service = CandidateVettingService()
            summary = vetting_service.run_vetting_cycle()
            processed = summary.get('candidates_processed', 0)
            flash(f'Started fresh at {now_utc} UTC. Cutoff set — only new candidates will be screened. {processed} candidates processed.', 'success')
        except Exception as e:
            current_app.logger.warning(f"Auto-trigger after start fresh failed: {e}")
            flash(f'Cutoff set to {now_utc} UTC. Vetting cycle will begin on the next scheduled run.', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error during start fresh: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/screening/revet-candidate/<int:candidate_id>', methods=['POST'])
@login_required
def revet_candidate(candidate_id):
    """Re-vet a specific candidate by clearing their existing vetting records.
    
    This removes the VettingLog and CandidateJobMatch records so the duplicate
    loop prevention won't skip them on the next cycle.
    """
    from models import ParsedEmail, CandidateVettingLog, CandidateJobMatch, EmbeddingFilterLog, EscalationLog
    
    db = get_db()
    
    try:
        # Find all ParsedEmail records for this candidate
        parsed_emails = ParsedEmail.query.filter(
            ParsedEmail.bullhorn_candidate_id == candidate_id,
            ParsedEmail.status == 'completed'
        ).all()
        
        if not parsed_emails:
            flash(f'No ParsedEmail records found for candidate {candidate_id}.', 'warning')
            return redirect(url_for('vetting.vetting_settings'))
        
        pe_ids = [pe.id for pe in parsed_emails]
        
        # Delete existing VettingLog records to bypass duplicate loop prevention
        vetting_logs = CandidateVettingLog.query.filter(
            CandidateVettingLog.parsed_email_id.in_(pe_ids)
        ).all()
        
        log_ids = [vl.id for vl in vetting_logs]
        
        # Cascade-delete all child tables that reference candidate_vetting_log
        # Order: children first, then parent
        filter_count = 0
        escalation_count = 0
        match_count = 0
        if log_ids:
            # 1. Delete EmbeddingFilterLog records (FK → candidate_vetting_log)
            filter_count = EmbeddingFilterLog.query.filter(
                EmbeddingFilterLog.vetting_log_id.in_(log_ids)
            ).delete(synchronize_session=False)
            
            # 2. Delete EscalationLog records (FK → candidate_vetting_log)
            escalation_count = EscalationLog.query.filter(
                EscalationLog.vetting_log_id.in_(log_ids)
            ).delete(synchronize_session=False)
            
            # 3. Delete CandidateJobMatch records (FK → candidate_vetting_log)
            match_count = CandidateJobMatch.query.filter(
                CandidateJobMatch.vetting_log_id.in_(log_ids)
            ).delete(synchronize_session=False)
        
        # 4. Delete the VettingLog records themselves
        log_count = 0
        if log_ids:
            log_count = CandidateVettingLog.query.filter(
                CandidateVettingLog.id.in_(log_ids)
            ).delete(synchronize_session=False)
        
        # Reset vetted_at on ParsedEmail records
        for pe in parsed_emails:
            pe.vetted_at = None
        
        db.session.commit()
        
        current_app.logger.info(
            f"🔄 Re-vet reset for candidate {candidate_id}: "
            f"cleared {log_count} vetting logs, {match_count} match records, "
            f"reset {len(pe_ids)} ParsedEmails"
        )
        flash(
            f'Reset candidate {candidate_id} for re-vetting: cleared {log_count} vetting log(s) '
            f'and {match_count} match record(s). Will be processed in next vetting cycle.',
            'success'
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error resetting candidate {candidate_id} for re-vet: {str(e)}")
        flash(f'Error resetting candidate for re-vet: {str(e)}', 'error')
    
    return redirect(url_for('vetting.vetting_settings'))



@vetting_bp.route('/screening/diagnostic')
@login_required
def vetting_diagnostic():
    """Temporary diagnostic endpoint to investigate vetting backlog"""
    from models import ParsedEmail, CandidateVettingLog, VettingConfig
    from sqlalchemy import func, case
    
    db = get_db()
    min_bh_id = request.args.get('min_bh_id', 4586546, type=int)
    
    # Overall ParsedEmail stats
    stats = db.session.query(
        func.count(ParsedEmail.id).label('total'),
        func.count(case((ParsedEmail.status == 'completed', 1))).label('completed'),
        func.count(case((
            (ParsedEmail.status == 'completed') & (ParsedEmail.bullhorn_candidate_id.isnot(None)),
            1
        ))).label('with_bh_id'),
        func.count(case((
            (ParsedEmail.status == 'completed') & (ParsedEmail.bullhorn_candidate_id.isnot(None)) & (ParsedEmail.vetted_at.is_(None)),
            1
        ))).label('unvetted_eligible'),
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
            'unvetted_eligible': stats.unvetted_eligible,
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
            CandidateVettingLog.note_created == False
        ).count()
        
        if total_failed == 0:
            flash('No failed notes to retry — all completed vetting logs have notes.', 'info')
            return redirect(url_for('vetting.vetting_settings'))
        
        # Apply batch limit
        failed_logs = CandidateVettingLog.query.filter(
            CandidateVettingLog.status == 'completed',
            CandidateVettingLog.note_created == False
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

@vetting_bp.route('/screening/process-backlog', methods=['POST'])
@login_required
def process_backlog():
    """Process unvetted backlog manually - runs a vetting cycle bypassing the scheduler"""
    from models import VettingConfig
    import threading
    
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
        
        # Run the vetting cycle in the current request context
        from candidate_vetting_service import CandidateVettingService
        vetting_service = CandidateVettingService()
        
        # Override batch size for this run
        summary = vetting_service.run_vetting_cycle()
        
        processed = summary.get('candidates_processed', 0)
        qualified = summary.get('candidates_qualified', 0)
        notes = summary.get('notes_created', 0)
        detected = summary.get('candidates_detected', 0)
        errors = summary.get('errors', [])
        status = summary.get('status', 'unknown')
        
        if status == 'disabled':
            flash('Vetting is disabled. Enable it first.', 'warning')
        elif processed > 0:
            flash(f'Backlog processing complete: {detected} detected, {processed} processed, {qualified} qualified, {notes} notes created.', 'success')
        elif detected > 0:
            flash(f'Detected {detected} candidates but processed 0. Check Bullhorn connection. Errors: {errors}', 'warning')
        else:
            flash(f'No candidates to process. Status: {status}', 'info')
        
        current_app.logger.info(f"Manual backlog processing: {summary}")
        
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


@vetting_bp.route('/screening/test-email', methods=['POST'])
@login_required
def send_test_vetting_email():
    """Send a test notification email with sample data"""
    from email_service import EmailService
    
    test_email = request.form.get('test_email', 'kyleroots00@gmail.com')
    scenario = request.form.get('scenario', '2')
    action = request.form.get('action', 'send')
    
    # Sample candidate and job data
    candidate_id = 4583692
    candidate_name = "John Smith"
    candidate_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=Candidate&id={candidate_id}"
    
    # Job definitions for different scenarios
    jobs = [
        {
            'id': 34517,
            'title': 'Azure Integration Developer',
            'score': 85,
            'is_applied': True,
            'summary': 'Strong candidate with 5+ years of Azure experience including Logic Apps, Functions, and API Management.',
            'skills': 'Azure Functions, Logic Apps, API Management, C#, .NET Core, SQL Server',
            'recruiter_name': 'Sarah Johnson',
            'recruiter_email': 'sjohnson@myticas.com'
        },
        {
            'id': 34520,
            'title': 'Senior Software Developer',
            'score': 82,
            'is_applied': False,
            'summary': 'Solid technical background with full-stack development experience. Python and cloud deployment skills meet core requirements.',
            'skills': 'Python, JavaScript, React, AWS, Docker, PostgreSQL',
            'recruiter_name': 'Mike Chen',
            'recruiter_email': 'mchen@myticas.com'
        },
        {
            'id': 34523,
            'title': 'Cloud Solutions Architect',
            'score': 80,
            'is_applied': False,
            'summary': 'Extensive cloud architecture experience with multi-platform expertise.',
            'skills': 'Azure, AWS, Kubernetes, Terraform, CI/CD, Solution Design',
            'recruiter_name': 'Emily Rodriguez',
            'recruiter_email': 'erodriguez@myticas.com'
        }
    ]
    
    # Cross-reference only scenario
    cross_only_jobs = [
        {
            'id': 34517,
            'title': 'Azure Integration Developer',
            'score': 65,
            'is_applied': True,
            'summary': 'Candidate lacks required Azure Logic Apps experience.',
            'skills': 'Python, JavaScript, Basic Azure knowledge',
            'below_threshold': True,
            'recruiter_name': 'Sarah Johnson',
            'recruiter_email': 'sjohnson@myticas.com'
        },
        {
            'id': 34520,
            'title': 'Senior Software Developer',
            'score': 88,
            'is_applied': False,
            'summary': 'Excellent match for this role!',
            'skills': 'Python, JavaScript, React, AWS, Docker, PostgreSQL',
            'recruiter_name': 'Mike Chen',
            'recruiter_email': 'mchen@myticas.com'
        }
    ]
    
    # Multi-recruiter scenario
    multi_recruiter_jobs = [
        {
            'id': 34517,
            'title': 'Azure Integration Developer',
            'score': 85,
            'is_applied': True,
            'summary': 'Strong candidate with 5+ years of Azure experience.',
            'skills': 'Azure Functions, Logic Apps, API Management, C#, .NET Core',
            'recruiter_name': 'Sarah Johnson',
            'recruiter_email': 'sjohnson@myticas.com'
        },
        {
            'id': 34520,
            'title': 'Senior Software Developer',
            'score': 88,
            'is_applied': False,
            'summary': 'Excellent Python and full-stack skills match this role perfectly.',
            'skills': 'Python, JavaScript, React, AWS, Docker',
            'recruiter_name': 'Mike Chen',
            'recruiter_email': 'mchen@myticas.com'
        }
    ]
    
    is_multi_recruiter = scenario == 'multi'
    all_recruiter_emails = None
    
    # Build matches based on scenario
    if scenario == '1':
        matches = [jobs[0]]
        scenario_desc = "1 Match (Applied Job Only)"
    elif scenario == '2':
        matches = jobs[:2]
        scenario_desc = "2 Matches (Applied + 1 Cross-Reference)"
    elif scenario == '3':
        matches = jobs
        scenario_desc = "3+ Matches (Applied + 2 Cross-References)"
    elif scenario == 'multi':
        matches = multi_recruiter_jobs
        all_recruiter_emails = {'sjohnson@myticas.com', 'mchen@myticas.com'}
        scenario_desc = "Multi-Recruiter (Same Email to All Recruiters)"
    else:
        matches = [j for j in cross_only_jobs if not j.get('below_threshold', False)]
        scenario_desc = "Cross-Reference Only (Applied Job Below Threshold)"
    
    # Build transparency note
    transparency_note = ""
    if is_multi_recruiter and all_recruiter_emails:
        primary_email = 'sjohnson@myticas.com'
        cc_emails = [e for e in all_recruiter_emails if e != primary_email]
        if cc_emails:
            transparency_note = f"""
            <div style="background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; padding: 12px; margin-bottom: 15px;">
                <p style="margin: 0; color: #1565c0; font-size: 13px;">
                    <strong>📢 Team Thread:</strong> This candidate matches multiple positions.
                    CC'd on this email: <em>{', '.join(cc_emails)}</em>
                </p>
            </div>
            """
    
    # Build email HTML (abbreviated for space - same as original)
    subject = f"🎯 [TEST] Qualified Candidate Alert: {candidate_name}"
    
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #dc3545; color: white; padding: 10px 20px; text-align: center; font-weight: bold;">
            ⚠️ TEST EMAIL - {scenario_desc} ⚠️
        </div>
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px;">
            <h1 style="margin: 0; font-size: 24px;">🎯 Qualified Candidate Match</h1>
        </div>
        <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef;">
            <p>Hi there,</p>
            {transparency_note}
            <p>A new candidate has been analyzed by Scout Screening and matches <strong>{len(matches)} position(s)</strong>.</p>
            <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; margin: 20px 0;">
                <h2 style="margin: 0 0 10px 0; color: #495057; font-size: 18px;">👤 {candidate_name}</h2>
                <a href="{candidate_url}" style="display: inline-block; background: #667eea; color: white; padding: 10px 20px; border-radius: 5px; text-decoration: none;">View Candidate Profile →</a>
            </div>
            <h3 style="color: #495057;">Matched Positions:</h3>
    """
    
    for job in matches:
        job_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=JobOrder&id={job['id']}"
        applied_badge = '<span style="background: #ffc107; color: #000; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">APPLIED</span>' if job['is_applied'] else ''
        
        html_content += f"""
            <div style="background: white; padding: 15px; border-radius: 8px; border-left: 4px solid #28a745; margin: 10px 0;">
                <h4 style="margin: 0 0 8px 0; color: #28a745;">
                    <a href="{job_url}" style="color: #28a745; text-decoration: none;">{job['title']} (Job ID: {job['id']})</a>{applied_badge}
                </h4>
                <div style="color: #6c757d;"><strong>Match Score:</strong> {job['score']}%</div>
                <p style="margin: 0; color: #495057;">{job['summary']}</p>
                <p style="margin: 10px 0 0 0; color: #495057;"><strong>Key Skills:</strong> {job['skills']}</p>
            </div>
        """
    
    html_content += """
        </div>
        <div style="background: #343a40; color: #adb5bd; padding: 15px; font-size: 12px; text-align: center;">
            Powered by Scout Screening™ • Myticas Consulting
        </div>
    </div>
    """
    
    # Handle preview vs send
    if action == 'preview':
        return render_template('vetting_email_preview.html', 
                             html_content=html_content, 
                             scenario_desc=scenario_desc,
                             test_email=test_email,
                             scenario=scenario)
    
    # Send the email
    try:
        email_service = EmailService()
        result = email_service.send_html_email(
            to_email=test_email,
            subject=subject,
            html_content=html_content,
            notification_type='vetting_test_email'
        )
        
        if result and (result is True or result.get('success')):
            flash(f'Test email ({scenario_desc}) sent successfully to {test_email}!', 'success')
        else:
            flash(f'Failed to send test email to {test_email}', 'error')
            
    except Exception as e:
        current_app.logger.error(f"Error sending test vetting email: {str(e)}")
        flash(f'Error sending test email: {str(e)}', 'error')
    
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
        
        # Extract requirements for all jobs
        results = vetting_service.extract_requirements_for_jobs(all_jobs)
        
        msg = f"Extracted requirements for {results.get('extracted', 0)} jobs. "
        msg += f"Skipped {results.get('skipped', 0)}, Failed {results.get('failed', 0)}"
        if location_updates > 0:
            msg += f", Updated location for {location_updates} existing jobs"
        flash(msg, 'success')
        
    except Exception as e:
        current_app.logger.error(f"Error extracting all requirements: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('vetting.vetting_settings'))


# ═══════════════════════════════════════════════════════════════
# EMBEDDING FILTER MONITORING ROUTES
# ═══════════════════════════════════════════════════════════════

@vetting_bp.route('/screening/send-digest', methods=['POST'])
@login_required
def send_embedding_digest():
    """Manually trigger the daily embedding filter digest email."""
    try:
        from embedding_digest_service import send_daily_digest
        
        success = send_daily_digest()
        
        if success:
            flash('Embedding filter digest email sent successfully!', 'success')
        else:
            flash('Failed to send digest email. Check SendGrid configuration.', 'error')
            
    except Exception as e:
        current_app.logger.error(f"Error sending embedding digest: {str(e)}")
        flash(f'Error sending digest: {str(e)}', 'error')
    
    return redirect(url_for('vetting.embedding_audit'))


@vetting_bp.route('/screening/embedding-audit')
@login_required
def embedding_audit():
    """Embedding filter audit page — filtered pairs and escalations."""
    from models import EmbeddingFilterLog, EscalationLog, CandidateJobMatch
    from sqlalchemy import func
    
    db = get_db()
    
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
        sim_min = float(request.args.get('sim_min', '0.0'))
    except (ValueError, TypeError):
        sim_min = 0.0
    try:
        sim_max = float(request.args.get('sim_max', '1.0'))
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
    savings_per_filter = 0.003 - 0.00002  # GPT-4o-mini minus embedding
    savings_per_pass = 0.03 - 0.003       # GPT-4o minus GPT-4o-mini
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
                          active_page='screening')


@vetting_bp.route('/screening/embedding-audit/filtered-csv')
@login_required
def export_filtered_csv():
    """Export filtered pairs as CSV."""
    from models import EmbeddingFilterLog
    from flask import Response
    import csv
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
    
    for log in logs:
        writer.writerow([
            log.filtered_at.strftime('%Y-%m-%d %H:%M') if log.filtered_at else '',
            log.bullhorn_candidate_id,
            log.candidate_name or '',
            log.bullhorn_job_id,
            log.job_title or '',
            f'{log.similarity_score:.4f}' if log.similarity_score else '',
            f'{log.threshold_used:.2f}' if log.threshold_used else '',
            (log.resume_snippet or '')[:200]
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
    import csv
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
                     'GPT-4o-mini Score', 'GPT-4o Score', 'Delta', 'Material Change', 
                     'Crossed Threshold', 'Threshold'])
    
    for log in logs:
        writer.writerow([
            log.escalated_at.strftime('%Y-%m-%d %H:%M') if log.escalated_at else '',
            log.bullhorn_candidate_id,
            log.candidate_name or '',
            log.bullhorn_job_id,
            log.job_title or '',
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
