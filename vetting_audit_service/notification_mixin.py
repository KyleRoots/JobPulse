"""Auto-split from vetting_audit_service.py — see vetting_audit_service/__init__.py."""
import json
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

class NotificationMixin:
    """Audit summary notifications (email)."""

    def _send_audit_summary_email(self, summary: Dict):
        from app import db as app_db
        from models import VettingConfig, EmailDeliveryLog
        from email_service import EmailService

        admin_email = VettingConfig.get_value('admin_notification_email', '')
        if not admin_email:
            logger.warning("No admin_notification_email configured — skipping audit summary email")
            return

        # Re-read the persisted revet_new_score from VettingAuditLog when
        # available so this email reflects any back-fill that landed
        # between the audit cycle and email send time. The synchronous
        # _trigger_revet path returns None (the next vetting cycle is
        # async), so the in-memory ``new_score`` will be None for fresh
        # re-vets; the back-fill helper updates the persisted column the
        # moment the next vetting cycle finishes.
        from models import VettingAuditLog
        details_html = ''
        if summary.get('details'):
            rows = ''
            for d in summary['details']:
                action_badge = {
                    'revet_triggered': '<span style="color: #22c55e;">✅ Re-vetted</span>',
                    'flagged_for_review': '<span style="color: #f59e0b;">⚠️ Flagged</span>',
                    'revet_skipped_capped': '<span style="color: #f59e0b;">⛔ Re-vet capped (24h)</span>',
                    'revet_skipped_stable': '<span style="color: #6b7280;">⏸ Re-vet skipped (stable)</span>',
                    'no_action': '<span style="color: #6b7280;">—</span>'
                }.get(d.get('action_taken', ''), '—')

                resolved_new_score = d.get('new_score')
                if resolved_new_score is None and d.get('audit_log_id'):
                    try:
                        audit_row = VettingAuditLog.query.get(d['audit_log_id'])
                        if audit_row is not None and audit_row.revet_new_score is not None:
                            resolved_new_score = audit_row.revet_new_score
                    except Exception as lookup_err:
                        logger.warning(
                            f"⚠️ Audit summary email: failed to re-read "
                            f"audit row {d.get('audit_log_id')}: {lookup_err!r}"
                        )

                new_score_str = (
                    f"{resolved_new_score:.0f}%"
                    if resolved_new_score is not None
                    else 'Pending'
                )

                rows += f"""<tr>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('candidate_name', 'Unknown')}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('job_title', 'Unknown')}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('original_score', 0):.0f}%</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{new_score_str}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('finding_type', '')}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{action_badge}</td>
                </tr>"""

            details_html = f"""
            <table style="width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 14px;">
                <thead>
                    <tr style="background: #1f2937; color: #e5e7eb;">
                        <th style="padding: 8px; text-align: left;">Candidate</th>
                        <th style="padding: 8px; text-align: left;">Job</th>
                        <th style="padding: 8px; text-align: left;">Original</th>
                        <th style="padding: 8px; text-align: left;">New</th>
                        <th style="padding: 8px; text-align: left;">Issue</th>
                        <th style="padding: 8px; text-align: left;">Action</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>"""

        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; background: #111827; color: #e5e7eb; padding: 24px; border-radius: 8px;">
            <h2 style="color: #f59e0b; margin-bottom: 16px;">🔍 Scout Screening Quality Audit</h2>
            <div style="background: #1f2937; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
                <p style="margin: 4px 0;"><strong>Results Audited:</strong> {summary['total_audited']}</p>
                <p style="margin: 4px 0;"><strong>Issues Found:</strong> {summary['issues_found']}</p>
                <p style="margin: 4px 0;"><strong>Re-vets Triggered:</strong> {summary['revets_triggered']}</p>
            </div>
            {details_html}
            <p style="color: #6b7280; font-size: 12px; margin-top: 24px;">
                This is an automated quality audit from Scout Genius™. 
                Re-vetted candidates will have updated Bullhorn notes once the next vetting cycle processes them.
            </p>
        </div>
        """

        email_service = EmailService(db=app_db, EmailDeliveryLog=EmailDeliveryLog)
        email_service.send_email(
            to_email=admin_email,
            subject=f"Scout Screening Audit: {summary['issues_found']} issue(s) found, {summary['revets_triggered']} re-vet(s) triggered",
            html_content=html_body,
            notification_type='screening_audit_summary'
        )
        logger.info(f"📧 Audit summary email sent to {admin_email}")
