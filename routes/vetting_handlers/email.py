"""Test notification email endpoints (vetting test email + embedding digest)."""
from datetime import datetime, timedelta  # noqa: F401  (timedelta kept for parity with original imports)

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required

from routes.vetting import vetting_bp


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
