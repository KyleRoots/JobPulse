from __future__ import annotations
"""
Notification Service - Recruiter email notifications for qualified candidates.

Contains:
- send_recruiter_notifications: Sends consolidated email with all recruiters CC'd
- _send_recruiter_email: Builds and sends the HTML notification email
"""

import logging
from datetime import datetime
from typing import List
from app import db
from models import CandidateJobMatch, CandidateVettingLog, VettingConfig
from vetting.name_utils import parse_names, parse_emails


class NotificationMixin:
    """Recruiter email notifications for qualified screening matches."""

    def send_recruiter_notifications(self, vetting_log: CandidateVettingLog) -> int:
        """
        Send ONE email notification with all recruiters CC'd.
        
        TRANSPARENCY MODEL: When a candidate matches multiple positions with different
        recruiters, ALL recruiters are CC'd on the SAME email thread. The primary
        recipient is the recruiter of the job the candidate applied to. This ensures
        complete visibility and enables direct collaboration on the same thread.
        
        Args:
            vetting_log: The vetting log with qualified matches
            
        Returns:
            Number of notifications sent (1 for success, 0 for failure/no matches)
        """
        # SAFETY CHECK: Re-verify vetting is still enabled before sending emails
        # This prevents emails if vetting was disabled mid-cycle
        # Force fresh database read to bypass SQLAlchemy session cache
        db.session.expire_all()
        if not self.is_enabled():
            logging.info(f"📧 Notification blocked - vetting disabled mid-cycle for {vetting_log.candidate_name}")
            return 0
        
        logging.info(f"📧 Notification check for {vetting_log.candidate_name} (ID: {vetting_log.bullhorn_candidate_id})")
        
        if not vetting_log.is_qualified:
            prestige_sent = self._send_prestige_review_notification(vetting_log)
            if not prestige_sent:
                logging.info(f"  ⏭️ Skipping - not qualified (is_qualified={vetting_log.is_qualified})")
            return prestige_sent
        
        # Get ALL qualified matches for this candidate
        matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id,
            is_qualified=True,
            notification_sent=False
        ).all()
        
        if not matches:
            logging.info(f"  ⏭️ Skipping - no unsent qualified matches (all already notified)")
            return 0
        
        logging.info(f"  📨 Found {len(matches)} unsent qualified matches")
        
        # Determine primary recruiter (from applied job) and CC list
        # Note: recruiter_email may now be comma-separated (multiple recruiters per job)
        primary_recruiter_email = None
        primary_recruiter_name = None
        cc_recruiter_emails = []
        
        # First pass: find the applied job recruiter (primary recipient)
        for match in matches:
            if match.is_applied_job and match.recruiter_email:
                emails = parse_emails(match.recruiter_email)
                names = parse_names(match.recruiter_name)
                if emails:
                    primary_recruiter_email = emails[0]  # First recruiter on applied job is primary
                    primary_recruiter_name = names[0] if names else ''
                break
        
        # Second pass: collect all unique recruiter emails from all matches
        # If no applied job recruiter found, first recruiter becomes primary
        seen_emails = set()
        for match in matches:
            emails = parse_emails(match.recruiter_email)
            names = parse_names(match.recruiter_name)
            
            for i, email in enumerate(emails):
                if email and email not in seen_emails:
                    seen_emails.add(email)
                    name = names[i] if i < len(names) else ''
                    
                    if not primary_recruiter_email:
                        # No applied job match - first recruiter becomes primary
                        primary_recruiter_email = email
                        primary_recruiter_name = name
                    elif email != primary_recruiter_email:
                        # Different from primary - add to CC list
                        cc_recruiter_emails.append(email)
        
        # Check email notification kill switch setting
        from models import VettingConfig
        send_to_recruiters = False
        admin_email = ''
        
        send_setting = VettingConfig.query.filter_by(setting_key='send_recruiter_emails').first()
        if send_setting:
            send_to_recruiters = send_setting.setting_value.lower() == 'true'
        
        admin_setting = VettingConfig.query.filter_by(setting_key='admin_notification_email').first()
        if admin_setting and admin_setting.setting_value:
            admin_email = admin_setting.setting_value
        
        # If kill switch is OFF, send only to admin email
        if not send_to_recruiters:
            if not admin_email:
                logging.warning(f"❌ Recruiter emails disabled but no admin email configured - cannot send notification for {vetting_log.candidate_name}")
                return 0
            
            logging.info(f"  🔒 Recruiter emails DISABLED - sending to admin only: {admin_email}")
            primary_recruiter_email = admin_email
            primary_recruiter_name = 'Admin'
            cc_recruiter_emails = []  # No CC when in testing mode
        elif not primary_recruiter_email:
            # Kill switch is ON but no recruiter emails found - try to fall back to admin
            if admin_email:
                logging.warning(f"⚠️ No recruiter emails found for candidate {vetting_log.candidate_name} - falling back to admin email: {admin_email}")
                primary_recruiter_email = admin_email
                primary_recruiter_name = 'Admin'
                cc_recruiter_emails = []
            else:
                logging.warning(f"❌ No recruiter emails found and no admin email configured - cannot send notification for {vetting_log.candidate_name}")
                return 0
        
        # Send ONE email with primary as To: and others as CC:
        try:
            success = self._send_recruiter_email(
                recruiter_email=primary_recruiter_email,
                recruiter_name=primary_recruiter_name or '',
                candidate_name=vetting_log.candidate_name,
                candidate_id=vetting_log.bullhorn_candidate_id,
                matches=matches,
                cc_emails=cc_recruiter_emails  # All other recruiters CC'd
            )
            
            if success:
                # Mark ALL matches as notified
                for match in matches:
                    match.notification_sent = True
                    match.notification_sent_at = datetime.utcnow()
                
                vetting_log.notifications_sent = True
                vetting_log.notification_count = 1  # One email sent to all
                db.session.commit()
                
                cc_info = f" (CC: {', '.join(cc_recruiter_emails)})" if cc_recruiter_emails else ""
                logging.info(f"Sent notification to {primary_recruiter_email}{cc_info} for {vetting_log.candidate_name} (Candidate ID: {vetting_log.bullhorn_candidate_id}, {len(matches)} positions)")
                
                # ── Scout Vetting trigger ──
                # After recruiter notification, initiate Scout Vetting for qualified matches
                try:
                    from scout_vetting_service import ScoutVettingService
                    sv_service = ScoutVettingService(email_service=self.email_service, bullhorn_service=self.bullhorn)
                    if sv_service.is_enabled():
                        sv_result = sv_service.initiate_vetting(vetting_log, matches)
                        logging.info(f"🔍 Scout Vetting initiated: {sv_result.get('created', 0)} sessions created, "
                                    f"{sv_result.get('queued', 0)} queued, {sv_result.get('skipped', 0)} skipped")
                except Exception as sv_err:
                    logging.error(f"Scout Vetting trigger error (non-blocking): {str(sv_err)}")
                
                return 1
            else:
                logging.error(f"Failed to send notification for {vetting_log.candidate_name} (Candidate ID: {vetting_log.bullhorn_candidate_id})")
                return 0
                
        except Exception as e:
            logging.error(f"Failed to send notification: {str(e)}")
            return 0
    
    def _send_recruiter_email(self, recruiter_email: str, recruiter_name: str,
                               candidate_name: str, candidate_id: int,
                               matches: List[CandidateJobMatch],
                               cc_emails: list = None) -> bool:
        """
        Send notification email to a recruiter about a qualified candidate.
        
        TRANSPARENCY MODEL: ONE email is sent with the primary recruiter as To:
        and all other recruiters CC'd on the same thread. Each job card shows
        which recruiter owns it for complete visibility.
        """
        # Build Bullhorn candidate URL (using cls45 subdomain for Bullhorn One)
        candidate_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=Candidate&id={candidate_id}"
        
        # Build transparency header if there are CC'd recruiters
        transparency_note = ""
        if cc_emails and len(cc_emails) > 0:
            transparency_note = f"""
                <div style="background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; padding: 12px; margin-bottom: 15px;">
                    <p style="margin: 0; color: #1565c0; font-size: 13px;">
                        <strong>📢 Team Thread:</strong> This candidate matches multiple positions.
                        CC'd on this email: <em>{', '.join(cc_emails)}</em>
                    </p>
                </div>
            """
        
        # Build email content
        subject = f"🎯 Qualified Candidate Alert: {candidate_name}"
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h1 style="margin: 0; font-size: 24px;">🎯 Qualified Candidate Match</h1>
            </div>
            
            <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef;">
                <p style="margin: 0 0 15px 0;">Hi {recruiter_name or 'there'},</p>
                
                {transparency_note}
                
                <p style="margin: 0 0 15px 0;">
                    A new candidate has been analyzed by Scout Screening and matches 
                    <strong>{len(matches)} position(s)</strong>.
                </p>
                
                <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; margin: 20px 0;">
                    <h2 style="margin: 0 0 10px 0; color: #495057; font-size: 18px;">
                        👤 {candidate_name}
                    </h2>
                    <a href="{candidate_url}" 
                       style="display: inline-block; background: #667eea; color: white; 
                              padding: 10px 20px; border-radius: 5px; text-decoration: none;
                              margin-top: 10px;">
                        View Candidate Profile →
                    </a>
                </div>
                
                <h3 style="color: #495057; margin: 20px 0 10px 0;">Matched Positions:</h3>
        """
        
        for match in matches:
            applied_badge = '<span style="background: #ffc107; color: #000; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">APPLIED</span>' if match.is_applied_job else ''
            job_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=JobOrder&id={match.bullhorn_job_id}"
            
            # Show recruiter ownership for each job
            recruiter_tag = ""
            if match.recruiter_name:
                is_your_job = match.recruiter_email == recruiter_email
                if is_your_job:
                    recruiter_tag = f'<span style="background: #28a745; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">YOUR JOB</span>'
                else:
                    recruiter_tag = f'<span style="background: #6c757d; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">{match.recruiter_name}\'s Job</span>'
            
            html_content += f"""
                <div style="background: white; padding: 15px; border-radius: 8px; 
                            border-left: 4px solid #28a745; margin: 10px 0;">
                    <h4 style="margin: 0 0 8px 0; color: #28a745;">
                        <a href="{job_url}" style="color: #28a745; text-decoration: none;">{match.job_title} (Job ID: {match.bullhorn_job_id})</a>{applied_badge}{recruiter_tag}
                    </h4>
                    <div style="color: #6c757d; margin-bottom: 8px;">
                        <strong>Match Score:</strong> {match.match_score:.0f}%
                    </div>
                    <p style="margin: 0; color: #495057;">{match.match_summary}</p>
                    
                    {f'<p style="margin: 10px 0 0 0; color: #495057;"><strong>Key Skills:</strong> {match.skills_match}</p>' if match.skills_match else ''}
                </div>
            """
        
        html_content += f"""
                <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid #dee2e6;">
                    <p style="color: #6c757d; font-size: 14px; margin: 0;">
                        <strong>Recommended Action:</strong> Review the candidate's profile and 
                        reach out if they're a good fit for your open position(s).
                    </p>
                </div>
            </div>
            
            <div style="background: #343a40; color: #adb5bd; padding: 15px; 
                        border-radius: 0 0 8px 8px; font-size: 12px; text-align: center;">
                Powered by Scout Screening™ • Myticas Consulting
            </div>
        </div>
        """
        
        # Send the email with CC recipients and BCC admin for transparency
        try:
            # Always BCC admin for monitoring/troubleshooting
            admin_bcc_email = 'kroots@myticas.com'
            
            job_titles = ', '.join(set(m.job_title for m in matches if m.job_title)) or 'unknown position'
            avg_score = sum(m.match_score for m in matches) / len(matches) if matches else 0
            changes_summary = f"Screening alert — {candidate_name} matched {job_titles} (Score: {avg_score:.0f}%)"
            result = self.email_service.send_html_email(
                to_email=recruiter_email,
                subject=subject,
                html_content=html_content,
                notification_type='vetting_recruiter_notification',
                cc_emails=cc_emails,  # CC all other recruiters on same thread
                bcc_emails=[admin_bcc_email],  # BCC admin for transparency
                changes_summary=changes_summary
            )
            return result is True or (isinstance(result, dict) and result.get('success', False))
        except Exception as e:
            logging.error(f"Email send error: {str(e)}")
            return False

    def _send_prestige_review_notification(self, vetting_log: CandidateVettingLog) -> int:
        prestige_matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id,
            notification_sent=False,
            prestige_boost_applied=True,
        ).filter(
            CandidateJobMatch.prestige_employer.isnot(None),
            CandidateJobMatch.prestige_employer != '',
        ).all()

        if not prestige_matches:
            return 0

        logging.info(f"  🏢 Found {len(prestige_matches)} prestige employer matches for not-qualified candidate {vetting_log.candidate_name}")

        primary_recruiter_email = None
        primary_recruiter_name = None
        cc_recruiter_emails = []

        for match in prestige_matches:
            if match.is_applied_job and match.recruiter_email:
                emails = parse_emails(match.recruiter_email)
                names = parse_names(match.recruiter_name)
                if emails:
                    primary_recruiter_email = emails[0]
                    primary_recruiter_name = names[0] if names else ''
                break

        seen_emails = set()
        for match in prestige_matches:
            emails = parse_emails(match.recruiter_email)
            names = parse_names(match.recruiter_name)
            for i, email in enumerate(emails):
                if email and email not in seen_emails:
                    seen_emails.add(email)
                    name = names[i] if i < len(names) else ''
                    if not primary_recruiter_email:
                        primary_recruiter_email = email
                        primary_recruiter_name = name
                    elif email != primary_recruiter_email:
                        cc_recruiter_emails.append(email)

        send_setting = VettingConfig.query.filter_by(setting_key='send_recruiter_emails').first()
        send_to_recruiters = send_setting and send_setting.setting_value.lower() == 'true'

        admin_setting = VettingConfig.query.filter_by(setting_key='admin_notification_email').first()
        admin_email = admin_setting.setting_value if admin_setting and admin_setting.setting_value else ''

        if not send_to_recruiters:
            if not admin_email:
                logging.warning(f"❌ Prestige notification blocked — recruiter emails disabled and no admin email")
                return 0
            primary_recruiter_email = admin_email
            primary_recruiter_name = 'Admin'
            cc_recruiter_emails = []
        elif not primary_recruiter_email:
            if admin_email:
                primary_recruiter_email = admin_email
                primary_recruiter_name = 'Admin'
                cc_recruiter_emails = []
            else:
                return 0

        prestige_firm = prestige_matches[0].prestige_employer
        candidate_name = vetting_log.candidate_name
        candidate_id = vetting_log.bullhorn_candidate_id
        candidate_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=Candidate&id={candidate_id}"
        highest_score = max(m.match_score for m in prestige_matches)

        subject = f"🏢 Prestige Review: {candidate_name} — Currently at {prestige_firm}"

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h1 style="margin: 0; font-size: 22px;">🏢 Prestige Employer — Review Recommended</h1>
            </div>
            
            <div style="background: #fff8e1; padding: 15px 20px; border-left: 4px solid #f9a825; border-right: 1px solid #e9ecef;">
                <p style="margin: 0; color: #5d4037; font-size: 14px;">
                    <strong>⚠️ Below Threshold — But Worth Reviewing</strong><br>
                    This candidate scored below the qualified threshold (<strong>{highest_score:.0f}%</strong>);
                    however, they are currently employed at <strong>{prestige_firm}</strong>.
                    Their resume may not fully reflect their skills and experience.
                    <strong>Recruiter review is recommended.</strong>
                </p>
            </div>

            <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef; border-top: none;">
                <p style="margin: 0 0 15px 0;">Hi {primary_recruiter_name or 'there'},</p>

                <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; margin: 15px 0;">
                    <h2 style="margin: 0 0 5px 0; color: #495057; font-size: 18px;">
                        👤 {candidate_name}
                    </h2>
                    <p style="margin: 0 0 10px 0; color: #6c757d; font-size: 13px;">
                        🏢 Currently at <strong style="color: #1e3a5f;">{prestige_firm}</strong>
                    </p>
                    <a href="{candidate_url}"
                       style="display: inline-block; background: #1e3a5f; color: white;
                              padding: 10px 20px; border-radius: 5px; text-decoration: none;">
                        View Candidate Profile →
                    </a>
                </div>

                <h3 style="color: #495057; margin: 20px 0 10px 0;">Screening Results:</h3>
        """

        for match in prestige_matches:
            job_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=JobOrder&id={match.bullhorn_job_id}"
            applied_badge = '<span style="background: #ffc107; color: #000; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">APPLIED</span>' if match.is_applied_job else ''
            boost_badge = ''
            if match.prestige_boost_applied:
                boost_badge = '<span style="background: #e3f2fd; color: #1565c0; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">+5 PRESTIGE</span>'

            html_content += f"""
                <div style="background: white; padding: 15px; border-radius: 8px;
                            border-left: 4px solid #f9a825; margin: 10px 0;">
                    <h4 style="margin: 0 0 8px 0; color: #5d4037;">
                        <a href="{job_url}" style="color: #5d4037; text-decoration: none;">{match.job_title} (Job ID: {match.bullhorn_job_id})</a>{applied_badge}{boost_badge}
                    </h4>
                    <div style="color: #6c757d; margin-bottom: 8px;">
                        <strong>Match Score:</strong> {match.match_score:.0f}%
                    </div>
                    <p style="margin: 0; color: #495057;">{match.match_summary}</p>
                    {f'<p style="margin: 10px 0 0 0; color: #495057;"><strong>Key Skills:</strong> {match.skills_match}</p>' if match.skills_match else ''}
                </div>
            """

        html_content += f"""
                <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid #dee2e6;">
                    <p style="color: #6c757d; font-size: 14px; margin: 0;">
                        <strong>Why this alert?</strong> Candidates at major consulting firms often have
                        broader experience than what appears on their resume. This candidate may be
                        a strong fit despite the score — a quick profile review is recommended.
                    </p>
                </div>
            </div>

            <div style="background: #343a40; color: #adb5bd; padding: 15px;
                        border-radius: 0 0 8px 8px; font-size: 12px; text-align: center;">
                Powered by Scout Screening™ • Myticas Consulting
            </div>
        </div>
        """

        try:
            admin_bcc_email = 'kroots@myticas.com'
            job_titles = ', '.join(set(m.job_title for m in prestige_matches if m.job_title)) or 'unknown'
            changes_summary = f"Prestige review alert — {candidate_name} at {prestige_firm}, matched {job_titles} (Score: {highest_score:.0f}%)"
            result = self.email_service.send_html_email(
                to_email=primary_recruiter_email,
                subject=subject,
                html_content=html_content,
                notification_type='vetting_prestige_notification',
                cc_emails=cc_recruiter_emails,
                bcc_emails=[admin_bcc_email],
                changes_summary=changes_summary
            )
            if result is True or (isinstance(result, dict) and result.get('success', False)):
                for match in prestige_matches:
                    match.notification_sent = True
                    match.notification_sent_at = datetime.utcnow()
                db.session.commit()
                logging.info(f"  🏢 Prestige review notification sent to {primary_recruiter_email} for {candidate_name}")
                return 1
            return 0
        except Exception as e:
            logging.error(f"Prestige notification send error: {str(e)}")
            return 0

