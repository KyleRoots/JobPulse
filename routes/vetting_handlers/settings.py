"""Vetting settings page, save, and manual health-check endpoints."""
from datetime import datetime

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required

from routes.vetting import vetting_bp
from routes.vetting_handlers._shared import get_db


@vetting_bp.route('/screening')
@login_required
def vetting_settings():
    """AI Candidate Vetting settings and activity page"""
    from datetime import timedelta
    from models import VettingConfig, CandidateVettingLog, JobVettingRequirements, VettingHealthCheck
    from sqlalchemy import func

    db = get_db()

    # Get settings (batch query: 1 query instead of N)
    settings = {
        'vetting_enabled': False,
        'send_recruiter_emails': False,
        'screening_audit_enabled': False,
        'match_threshold': 80,
        'batch_size': 25,
        'admin_notification_email': '',
        'health_alert_email': '',
        'embedding_similarity_threshold': 0.25,
        'vetting_cutoff_date': '',
        'global_custom_requirements': '',
        # Recruiter-activity gate (Task D)
        'recruiter_activity_check_enabled': True,
        'recruiter_activity_lookback_minutes': 1440,
        # Quality auditor controls (Task #11 rescope)
        'quality_auditor_model': 'gpt-5.4',
        'platform_age_ceilings': '',
        'qualified_audit_sample_rate': 10,
    }

    all_configs = VettingConfig.query.filter(
        VettingConfig.setting_key.in_(settings.keys())
    ).all()
    config_map = {c.setting_key: c.setting_value for c in all_configs}

    for key in settings.keys():
        value = config_map.get(key)
        if value is not None:
            if key in ('vetting_enabled', 'send_recruiter_emails',
                       'screening_audit_enabled', 'recruiter_activity_check_enabled'):
                settings[key] = value.lower() == 'true'
            elif key in ('match_threshold', 'batch_size',
                         'recruiter_activity_lookback_minutes',
                         'qualified_audit_sample_rate'):
                try:
                    settings[key] = int(value)
                except (ValueError, TypeError):
                    settings[key] = (
                        80 if key == 'match_threshold'
                        else 25 if key == 'batch_size'
                        else 1440 if key == 'recruiter_activity_lookback_minutes'
                        else 10
                    )
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
    recent_activity = CandidateVettingLog.query.filter(
        CandidateVettingLog.is_sandbox != True
    ).order_by(
        CandidateVettingLog.created_at.desc()
    ).limit(50).all()

    recommended_candidates = CandidateVettingLog.query.filter(
        CandidateVettingLog.status == 'completed',
        CandidateVettingLog.is_qualified == True,
        CandidateVettingLog.is_sandbox != True
    ).order_by(CandidateVettingLog.created_at.desc()).limit(30).all()

    not_recommended_candidates = CandidateVettingLog.query.filter(
        CandidateVettingLog.status == 'completed',
        CandidateVettingLog.is_qualified == False,
        CandidateVettingLog.is_sandbox != True
    ).order_by(CandidateVettingLog.created_at.desc()).limit(30).all()

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

    pending_candidates = CandidateVettingLog.query.filter(
        CandidateVettingLog.status.in_(['pending', 'processing']),
        CandidateVettingLog.is_sandbox != True
    ).order_by(CandidateVettingLog.created_at.desc()).limit(50).all()

    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_vetting = CandidateVettingLog.query.filter(
        CandidateVettingLog.status == 'completed',
        CandidateVettingLog.updated_at >= week_ago,
        CandidateVettingLog.is_sandbox != True
    ).order_by(CandidateVettingLog.updated_at.desc()).limit(50).all()

    return render_template('vetting_settings.html',
                          settings=settings,
                          stats=stats,
                          recent_activity=recent_activity,
                          recommended_candidates=recommended_candidates,
                          not_recommended_candidates=not_recommended_candidates,
                          latest_health=latest_health,
                          recent_issues=recent_issues,
                          pending_candidates=pending_candidates,
                          recent_vetting=recent_vetting,
                          active_page='screening_config')


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
        screening_audit_enabled = 'screening_audit_enabled' in request.form
        match_threshold = request.form.get('match_threshold', '80')
        batch_size = request.form.get('batch_size', '25')
        admin_email = request.form.get('admin_notification_email', '')
        health_alert_email = request.form.get('health_alert_email', '')
        embedding_threshold = request.form.get('embedding_similarity_threshold', '0.25')
        vetting_cutoff = request.form.get('vetting_cutoff_date', '').strip()
        global_custom_requirements = request.form.get('global_custom_requirements', '').strip()
        # Recruiter-activity gate (Task D)
        recruiter_gate_enabled = 'recruiter_activity_check_enabled' in request.form
        recruiter_lookback_raw = request.form.get('recruiter_activity_lookback_minutes', '60')
        # Quality auditor controls (Task #11 rescope)
        # When the audit toggle is OFF the three fields below are disabled
        # in the UI and won't be submitted. Detect that case so we preserve
        # the previously-saved values instead of overwriting them with the
        # request.form default fallbacks.
        auditor_fields_submitted = 'quality_auditor_model' in request.form
        quality_auditor_model = request.form.get('quality_auditor_model', 'gpt-5.4').strip() or 'gpt-5.4'
        platform_age_ceilings_raw = request.form.get('platform_age_ceilings', '').strip()
        qualified_audit_sample_rate_raw = request.form.get('qualified_audit_sample_rate', '10')

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
            _emb_raw = str(embedding_threshold).strip()
            emb_thresh = 0.25 if _emb_raw.lower() == 'nan' else float(_emb_raw)
            if emb_thresh != emb_thresh or emb_thresh < 0.0 or emb_thresh > 1.0:
                emb_thresh = 0.25
        except (ValueError, TypeError):
            emb_thresh = 0.25

        # Validate vetting cutoff date (if provided)
        if vetting_cutoff:
            try:
                datetime.strptime(vetting_cutoff, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                flash('Invalid cutoff date format. Use YYYY-MM-DD HH:MM:SS', 'error')
                return redirect(url_for('vetting.vetting_settings'))

        # Validate recruiter-activity lookback minutes (0 disables the gate;
        # cap at 1440 = 24h to avoid pathological values)
        try:
            recruiter_lookback = int(str(recruiter_lookback_raw).strip())
            if recruiter_lookback < 0 or recruiter_lookback > 1440:
                recruiter_lookback = 60
        except (ValueError, TypeError):
            recruiter_lookback = 60

        # Validate qualified_audit_sample_rate (0-100; 0 disables Phase 2).
        # Skipped entirely when fields weren't submitted (audit toggle off).
        qualified_sample_rate = 10
        if auditor_fields_submitted:
            try:
                qualified_sample_rate = int(str(qualified_audit_sample_rate_raw).strip())
                if qualified_sample_rate < 0 or qualified_sample_rate > 100:
                    qualified_sample_rate = 10
            except (ValueError, TypeError):
                qualified_sample_rate = 10

        # Validate platform_age_ceilings JSON.
        # Empty string is allowed and means "use built-in defaults"; otherwise
        # must parse as a JSON object of platform_name -> positive number.
        # On parse failure we reject the save so the admin sees the error
        # instead of silently swallowing bad JSON and shipping defaults.
        platform_age_ceilings_value = ''
        if auditor_fields_submitted and platform_age_ceilings_raw:
            try:
                import json as _json
                parsed = _json.loads(platform_age_ceilings_raw)
                if not isinstance(parsed, dict) or not parsed:
                    raise ValueError('Must be a non-empty JSON object')
                cleaned = {}
                for k, v in parsed.items():
                    f = float(v)
                    if f <= 0 or f > 100:
                        raise ValueError(
                            f"Ceiling for '{k}' must be between 0 and 100 years"
                        )
                    cleaned[str(k).lower()] = f
                platform_age_ceilings_value = _json.dumps(cleaned)
            except (ValueError, TypeError, _json.JSONDecodeError) as je:
                flash(
                    f'Invalid Platform Age Ceilings JSON: {str(je)}. '
                    f'Settings NOT saved. Expected format: '
                    f'{{"databricks": 8.0, "snowflake": 10.0, ...}}',
                    'error'
                )
                return redirect(url_for('vetting.vetting_settings'))

        # Update settings
        settings_to_save = [
            ('vetting_enabled', 'true' if vetting_enabled else 'false'),
            ('send_recruiter_emails', 'true' if send_recruiter_emails else 'false'),
            ('screening_audit_enabled', 'true' if screening_audit_enabled else 'false'),
            ('match_threshold', str(threshold)),
            ('batch_size', str(batch)),
            ('admin_notification_email', admin_email),
            ('health_alert_email', health_alert_email),
            ('embedding_similarity_threshold', str(emb_thresh)),
            ('vetting_cutoff_date', vetting_cutoff),
            ('global_custom_requirements', global_custom_requirements),
            ('recruiter_activity_check_enabled',
             'true' if recruiter_gate_enabled else 'false'),
            ('recruiter_activity_lookback_minutes', str(recruiter_lookback)),
        ]
        if auditor_fields_submitted:
            settings_to_save.extend([
                ('quality_auditor_model', quality_auditor_model),
                ('platform_age_ceilings', platform_age_ceilings_value),
                ('qualified_audit_sample_rate', str(qualified_sample_rate)),
            ])

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
