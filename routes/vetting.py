"""
Vetting Routes Blueprint
AI Candidate Vetting settings, operations, and job requirements management
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required
from datetime import datetime, timedelta

vetting_bp = Blueprint('vetting', __name__)


def get_db():
    """Get database instance from app context"""
    from app import db
    return db


@vetting_bp.route('/vetting')
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
        'health_alert_email': ''
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
            else:
                settings[key] = value or ''
    
    # Get stats
    stats = {
        'total_processed': CandidateVettingLog.query.filter_by(status='completed').count(),
        'qualified': CandidateVettingLog.query.filter_by(status='completed', is_qualified=True).count(),
        'notifications_sent': db.session.query(func.sum(CandidateVettingLog.notification_count)).scalar() or 0,
        'pending': CandidateVettingLog.query.filter(CandidateVettingLog.status.in_(['pending', 'processing'])).count()
    }
    
    # Get recent activity
    recent_activity = CandidateVettingLog.query.order_by(
        CandidateVettingLog.created_at.desc()
    ).limit(50).all()
    
    # Get recommended candidates
    recommended_candidates = CandidateVettingLog.query.filter_by(
        status='completed', 
        is_qualified=True
    ).order_by(CandidateVettingLog.created_at.desc()).limit(100).all()
    
    # Get not recommended candidates
    not_recommended_candidates = CandidateVettingLog.query.filter_by(
        status='completed',
        is_qualified=False
    ).order_by(CandidateVettingLog.created_at.desc()).limit(100).all()
    
    # Get job requirements - filtered to only show active tearsheet jobs
    from candidate_vetting_service import CandidateVettingService
    vetting_svc = CandidateVettingService()
    active_job_ids = vetting_svc.get_active_job_ids()
    
    if active_job_ids:
        job_requirements = JobVettingRequirements.query.filter(
            JobVettingRequirements.bullhorn_job_id.in_(active_job_ids)
        ).order_by(JobVettingRequirements.updated_at.desc()).all()
    else:
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
                          active_page='vetting')


@vetting_bp.route('/vetting/save', methods=['POST'])
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
        
        # Update settings
        settings_to_save = [
            ('vetting_enabled', 'true' if vetting_enabled else 'false'),
            ('send_recruiter_emails', 'true' if send_recruiter_emails else 'false'),
            ('match_threshold', str(threshold)),
            ('batch_size', str(batch)),
            ('admin_notification_email', admin_email),
            ('health_alert_email', health_alert_email)
        ]
        
        for key, value in settings_to_save:
            config = VettingConfig.query.filter_by(setting_key=key).first()
            if config:
                config.setting_value = value
            else:
                config = VettingConfig(setting_key=key, setting_value=value)
                db.session.add(config)
        
        db.session.commit()
        flash('Vetting settings saved successfully!', 'success')
        
    except Exception as e:
        current_app.logger.error(f"Error saving vetting settings: {str(e)}")
        flash(f'Error saving settings: {str(e)}', 'error')
    
    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/vetting/health-check', methods=['POST'])
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


@vetting_bp.route('/vetting/run', methods=['POST'])
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


@vetting_bp.route('/vetting/reset-recent', methods=['POST'])
@login_required
def reset_recent_vetting():
    """Reset vetted_at for recent applications to allow re-vetting"""
    from models import ParsedEmail
    
    db = get_db()
    
    try:
        # Reset vetted_at for records from the last 6 hours
        cutoff = datetime.utcnow() - timedelta(hours=6)
        
        reset_count = ParsedEmail.query.filter(
            ParsedEmail.received_at >= cutoff,
            ParsedEmail.vetted_at.isnot(None),
            ParsedEmail.status == 'completed',
            ParsedEmail.bullhorn_candidate_id.isnot(None)
        ).update({'vetted_at': None}, synchronize_session=False)
        
        db.session.commit()
        
        if reset_count > 0:
            flash(f'Reset vetting status for {reset_count} recent applications. They will be processed in the next vetting cycle.', 'success')
            current_app.logger.info(f"Reset vetted_at for {reset_count} ParsedEmail records from last 24 hours")
        else:
            flash('No recent applications found to reset.', 'info')
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error resetting recent vetting: {str(e)}")
        flash(f'Error resetting vetting: {str(e)}', 'error')
    
    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/vetting/full-clean-slate', methods=['POST'])
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


@vetting_bp.route('/vetting/test-email', methods=['POST'])
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
            <p>A new candidate has been analyzed by JobPulse AI and matches <strong>{len(matches)} position(s)</strong>.</p>
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
            Powered by JobPulse™ AI Vetting • Myticas Consulting
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
        success = email_service.send_html_email(
            to_email=test_email,
            subject=subject,
            html_content=html_content,
            notification_type='vetting_test_email'
        )
        
        if success:
            flash(f'Test email ({scenario_desc}) sent successfully to {test_email}!', 'success')
        else:
            flash(f'Failed to send test email to {test_email}', 'error')
            
    except Exception as e:
        current_app.logger.error(f"Error sending test vetting email: {str(e)}")
        flash(f'Error sending test email: {str(e)}', 'error')
    
    return redirect(url_for('vetting.vetting_settings'))


@vetting_bp.route('/vetting/sample-notes')
@login_required
def show_sample_notes():
    """Show sample note formats for qualified and non-qualified candidates"""
    
    qualified_note = """🎯 AI VETTING SUMMARY - QUALIFIED CANDIDATE

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
    
    not_qualified_note = """📋 AI VETTING SUMMARY - NOT RECOMMENDED

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


@vetting_bp.route('/vetting/create-test-note/<int:candidate_id>', methods=['POST'])
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
            note_text = f"""🎯 AI VETTING SUMMARY - QUALIFIED CANDIDATE

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
            action = "AI Vetting - Qualified"
        else:
            note_text = f"""📋 AI VETTING SUMMARY - NOT RECOMMENDED

Analysis Date: {now}
Threshold: 80%
Highest Match Score: 62%
Jobs Analyzed: 5

This candidate did not meet the 80% match threshold for any current open positions."""
            action = "AI Vetting - Not Recommended"
        
        note_id = bullhorn.create_candidate_note(candidate_id, note_text, action=action)
        
        if note_id:
            flash(f'Successfully created {note_type.replace("_", " ")} test note on candidate {candidate_id}. Note ID: {note_id}', 'success')
        else:
            flash(f'Failed to create test note on candidate {candidate_id}.', 'error')
            
    except Exception as e:
        current_app.logger.error(f"Error creating test vetting note: {str(e)}")
        flash(f'Error creating test note: {str(e)}', 'error')
    
    return redirect(url_for('vetting.show_sample_notes'))


@vetting_bp.route('/vetting/job/<int:job_id>/requirements', methods=['POST'])
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


@vetting_bp.route('/vetting/job/<int:job_id>/threshold', methods=['POST'])
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


@vetting_bp.route('/vetting/job/<int:job_id>/refresh-requirements', methods=['POST'])
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


@vetting_bp.route('/vetting/sync-requirements', methods=['POST'])
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


@vetting_bp.route('/vetting/extract-all-requirements', methods=['POST'])
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
