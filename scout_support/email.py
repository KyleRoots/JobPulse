"""
Email — Email sending, quoted history, stakeholder notifications.

Contains:
- _send_email: Core email dispatch with threading and conversation logging
- _build_quoted_history: Build HTML quoted history for email threads
- _get_stakeholder_emails: Get stakeholder emails by brand
- _notify_stakeholders: Send notifications to brand stakeholders
- _send_admin_new_ticket_notification: Notify admin of new tickets
- _send_acknowledgment_email: Send AI understanding to user
- _get_immediate_escalation_cc: Get CC recipients for escalation categories
- _send_clarification_email: Request clarification from user
- _send_solution_proposal_email: Propose solution to user for approval
- _send_user_confirmation_email: Confirm user approval received
- _send_admin_approval_request: Request admin authorization
- _send_execution_failure_email: Notify admin of execution failures
- _send_completion_email: Send completion proof to user + admin
- _send_escalation_email: Notify user + admin of escalation
- _send_status_email: Notify user of status changes
- _escalate_to_admin: Full escalation flow (status change + emails)
"""

import re
import json
import uuid
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class EmailMixin:
    """Email sending, quoted history, and stakeholder notifications."""

    ADMIN_FACING_EMAIL_TYPES = {
        'admin_approval_request', 'admin_reply', 'admin_clarification_response',
        'admin_new_ticket_notification', 'completion_admin',
        'escalation_admin_summary', 'admin_execution_failure',
    }

    def _get_stakeholder_emails(self, ticket) -> List[str]:
        from scout_support_service import STSI_STAKEHOLDER_EMAIL
        if ticket.brand == 'STSI':
            return [STSI_STAKEHOLDER_EMAIL]
        return []

    def _notify_stakeholders(self, ticket, subject: str, body: str, email_type: str):
        stakeholders = self._get_stakeholder_emails(ticket)
        for email in stakeholders:
            try:
                self._send_email(
                    to_email=email,
                    subject=subject,
                    body=body,
                    ticket=None,
                    email_type=email_type,
                )
                logger.info(f"📧 Stakeholder notification ({email_type}) sent to {email} for {ticket.ticket_number}")
            except Exception as e:
                logger.warning(f"Failed to send stakeholder notification to {email} for {ticket.ticket_number}: {e}")

    def _send_admin_new_ticket_notification(self, ticket):
        from scout_support_service import CATEGORY_LABELS, DEFAULT_ADMIN_EMAIL
        try:
            category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)
            priority_label = {'low': 'Low', 'medium': 'Medium', 'high': 'High', 'urgent': 'Urgent'}.get(ticket.priority, ticket.priority)

            body = (
                f"**New Scout Support Ticket Created**\n\n"
                f"**Ticket:** {ticket.ticket_number}\n"
                f"**Category:** {category_label}\n"
                f"**Priority:** {priority_label}\n"
                f"**Subject:** {ticket.subject}\n"
                f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})\n"
                f"**Department:** {ticket.submitter_department or 'Not specified'}\n"
                f"**Brand:** {ticket.brand}\n\n"
                f"Scout Support AI is now processing this ticket. You will receive updates as the ticket progresses."
            )

            self._send_email(
                to_email=DEFAULT_ADMIN_EMAIL,
                subject=f"[New Ticket] [{ticket.ticket_number}] {ticket.subject}",
                body=body,
                ticket=None,
                email_type='admin_new_ticket_notification',
            )

            self._notify_stakeholders(
                ticket,
                subject=f"[New Ticket] [{ticket.ticket_number}] {ticket.subject}",
                body=body,
                email_type='stakeholder_new_ticket',
            )

            logger.info(f"📧 Admin notification sent for new ticket {ticket.ticket_number}")
        except Exception as e:
            logger.warning(f"Failed to send admin notification for {ticket.ticket_number}: {e}")

    def _send_acknowledgment_email(self, ticket, understanding_json: str, attachment_ack: str = ''):
        try:
            understanding = json.loads(understanding_json)
        except (json.JSONDecodeError, TypeError):
            understanding = {'understanding': 'We have received your ticket and are reviewing it.'}

        summary = understanding.get('understanding', 'We have received your ticket and are reviewing it.')
        clarification_needed = understanding.get('clarification_needed', False)
        questions = understanding.get('clarification_questions', [])

        body_parts = [
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},",
            f"",
            f"Thank you for reaching out. Your support ticket has been received and assigned ticket number **{ticket.ticket_number}**.",
        ]

        if attachment_ack:
            body_parts.append("")
            body_parts.append(f"**Attachments:** {attachment_ack}")

        body_parts.extend([
            f"",
            f"**My Understanding of Your Issue:**",
            f"{summary}",
        ])

        if clarification_needed and questions:
            body_parts.append("")
            body_parts.append("Before I can proceed, I need a few clarifications:")
            for i, q in enumerate(questions, 1):
                body_parts.append(f"{i}. {q}")
            body_parts.append("")
            body_parts.append("Please reply to this email with your answers so I can move forward with resolving your issue.")
            ticket.status = 'clarifying'
        else:
            proposed_user = understanding.get('proposed_solution_user', '') or understanding.get('resolution_approach', '')
            proposed_admin = understanding.get('proposed_solution_admin', '') or understanding.get('resolution_approach', '')
            concerns_user = understanding.get('underlying_concerns_user', '') or understanding.get('underlying_concerns', '')
            concerns_admin = understanding.get('underlying_concerns_admin', '') or understanding.get('underlying_concerns', '')
            resolution_type = understanding.get('resolution_type', 'full')
            if proposed_user:
                body_parts.append("")
                body_parts.append("**Proposed Resolution:**")
                body_parts.append(proposed_user)
                if concerns_user:
                    body_parts.append("")
                    body_parts.append(f"**Please Note:** {concerns_user}")
                body_parts.append("")
                body_parts.append("If this looks correct, please reply with **\"Yes, go ahead\"** to approve, or let me know if anything needs to be adjusted.")
                ticket.status = 'awaiting_user_approval'
                ticket.proposed_solution = json.dumps({
                    'description_user': proposed_user,
                    'description_admin': proposed_admin,
                    'can_execute': understanding.get('can_resolve_autonomously', False),
                    'requires_bullhorn': understanding.get('requires_bullhorn_api', False),
                    'affected_entities': understanding.get('affected_entities', []),
                    'execution_steps': understanding.get('execution_steps', []),
                    'resolution_type': resolution_type,
                    'underlying_concerns_user': concerns_user,
                    'underlying_concerns_admin': concerns_admin,
                })
            else:
                body_parts.append("")
                body_parts.append("I'm reviewing your issue and will follow up shortly with more details.")

        body_parts.append("")
        body_parts.append("— Scout Support")

        from extensions import db
        db.session.commit()

        body = "\n".join(body_parts)

        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"[{ticket.ticket_number}] {ticket.subject}",
            body=body,
            ticket=ticket,
            email_type='acknowledgment',
            cc_emails=self._get_immediate_escalation_cc(ticket),
        )

    def _get_immediate_escalation_cc(self, ticket) -> Optional[List[str]]:
        from scout_support_service import IMMEDIATE_ESCALATION_CATEGORIES, STSI_ESCALATION_CONTACTS
        if ticket.brand == 'STSI' and ticket.category in IMMEDIATE_ESCALATION_CATEGORIES:
            contact = STSI_ESCALATION_CONTACTS.get(ticket.category)
            if contact:
                return [contact]
        return None

    def _send_clarification_email(self, ticket, follow_up_text: str, attachment_ack: str = ''):
        body_parts = [
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},",
            f"",
            f"Regarding your ticket **{ticket.ticket_number}**:",
        ]

        if attachment_ack:
            body_parts.append("")
            body_parts.append(f"**Attachments:** {attachment_ack}")

        body_parts.extend([
            f"",
            f"{follow_up_text}",
            f"",
            f"Please reply to this email with the additional details so I can move forward.",
            f"",
            f"— Scout Support",
        ])

        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            cc_emails=self._get_immediate_escalation_cc(ticket),
            body="\n".join(body_parts),
            ticket=ticket,
            email_type='clarification',
        )

    def _send_solution_proposal_email(self, ticket, solution_text: str, underlying_concerns: str = '', attachment_ack: str = ''):
        body_parts = [
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},",
            f"",
            f"After reviewing your issue ({ticket.ticket_number}), here is my proposed solution:",
        ]

        if attachment_ack:
            body_parts.append("")
            body_parts.append(f"**Attachments:** {attachment_ack}")

        body_parts.extend([
            f"",
            f"**Proposed Fix:**",
            f"{solution_text}",
        ])

        if underlying_concerns:
            body_parts.extend([
                f"",
                f"**⚠️ Advisory Note:**",
                f"{underlying_concerns}",
                f"",
                f"I can proceed with the immediate fix above, but the concern noted may warrant further investigation by your team or Bullhorn Support.",
            ])

        body_parts.extend([
            f"",
            f"If this looks correct, please reply with **\"Yes, go ahead\"** to approve.",
            f"If you'd like any changes, just let me know what adjustments are needed.",
            f"",
            f"— Scout Support",
        ])

        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body="\n".join(body_parts),
            ticket=ticket,
            email_type='solution_proposal',
            cc_emails=self._get_immediate_escalation_cc(ticket),
        )

    def _send_user_confirmation_email(self, ticket, message: str):
        body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Regarding ticket **{ticket.ticket_number}**:\n\n"
            f"{message}\n\n"
            f"You'll receive a confirmation once the fix has been executed.\n\n"
            f"— Scout Support"
        )
        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=body,
            ticket=ticket,
            email_type='user_confirmation',
        )

    def _send_admin_approval_request(self, ticket):
        from scout_support_service import CATEGORY_LABELS

        try:
            understanding = json.loads(ticket.ai_understanding) if ticket.ai_understanding else {}
        except (json.JSONDecodeError, TypeError):
            understanding = {}

        try:
            solution = json.loads(ticket.proposed_solution) if ticket.proposed_solution else {}
        except (json.JSONDecodeError, TypeError):
            solution = {'description': ticket.proposed_solution or 'N/A'}

        resolution_type = solution.get('resolution_type', 'full')
        concerns_admin = solution.get('underlying_concerns_admin', '') or solution.get('underlying_concerns', '')

        body_parts = [
            f"Hi,",
            f"",
            f"A support ticket requires your approval before Scout Support can execute the fix.",
            f"",
            f"**Ticket:** {ticket.ticket_number}",
            f"**Category:** {CATEGORY_LABELS.get(ticket.category, ticket.category)}",
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})",
            f"**Priority:** {ticket.priority.upper()}",
            f"**Resolution Type:** {resolution_type.upper()}",
            f"",
            f"**Issue Summary:**",
            f"{understanding.get('understanding', ticket.description)}",
            f"",
            f"**Proposed Solution (Technical):**",
            f"{solution.get('description_admin', '') or solution.get('description', 'N/A')}",
        ]

        if concerns_admin:
            body_parts.extend([
                f"",
                f"**⚠️ Underlying Concerns (Partial Resolution):**",
                f"{concerns_admin}",
                f"",
                f"Scout Support can execute the immediate fix, but the concern above may require further investigation by your team or Bullhorn Support.",
            ])

        body_parts.extend([
            f"",
            f"**User has approved this solution.**",
            f"",
            f"Please reply with one of:",
            f"- **\"Approved\"** — Scout Support will execute the fix",
            f"- **\"Hold\"** — Place this ticket on hold",
            f"- **\"Close\"** — Close this ticket without action",
            f"",
            f"— Scout Support",
        ])

        self._send_email(
            to_email=ticket.admin_email,
            subject=f"[{ticket.ticket_number}] Admin Approval Required",
            body="\n".join(body_parts),
            ticket=ticket,
            email_type='admin_approval_request',
        )

    def _send_execution_failure_email(self, ticket, proof_items: List[Dict]):
        from scout_support_service import CATEGORY_LABELS

        proof_lines = []
        for item in proof_items:
            step = item.get('step', 'Unknown step')
            result = item.get('result', 'N/A')
            old_val = item.get('old_value', '')
            new_val = item.get('new_value', '')
            proof_lines.append(f"• {step}: {result}")
            if old_val and new_val:
                proof_lines.append(f"  Before: {old_val}")
                proof_lines.append(f"  After: {new_val}")

        proof_text = "\n".join(proof_lines) if proof_lines else "No details available."

        successful = [i for i in proof_items if 'success' in i.get('result', '').lower()]
        failed = [i for i in proof_items if 'fail' in i.get('result', '').lower()]

        admin_body_parts = [
            f"⚠️ Ticket **{ticket.ticket_number}** execution encountered errors.",
            f"",
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})",
            f"**Category:** {CATEGORY_LABELS.get(ticket.category, ticket.category)}",
            f"",
            f"**Execution Summary:** {len(successful)} succeeded, {len(failed)} failed",
            f"",
            f"**Execution Details:**",
            f"{proof_text}",
            f"",
            f"The user has **not** been notified. The ticket status is set to **execution_failed** and awaits your review.",
            f"",
            f"You can retry the ticket from the Scout Support dashboard, manually resolve it, or close it.",
            f"",
            f"— Scout Support",
        ]

        self._send_email(
            to_email=ticket.admin_email,
            subject=f"[{ticket.ticket_number}] Execution Failed ⚠️ — Action Required",
            body="\n".join(admin_body_parts),
            ticket=ticket,
            email_type='admin_execution_failure',
        )

    def _send_completion_email(self, ticket, proof_items: List[Dict]):
        from scout_support_service import CATEGORY_LABELS

        proof_lines = []
        for item in proof_items:
            step = item.get('step', 'Unknown step')
            result = item.get('result', 'N/A')
            old_val = item.get('old_value', '')
            new_val = item.get('new_value', '')
            proof_lines.append(f"• {step}: {result}")
            if old_val and new_val:
                proof_lines.append(f"  Before: {old_val}")
                proof_lines.append(f"  After: {new_val}")

        proof_text = "\n".join(proof_lines) if proof_lines else "No specific actions were executed."

        try:
            solution_data = json.loads(ticket.proposed_solution) if ticket.proposed_solution else {}
        except (json.JSONDecodeError, TypeError):
            solution_data = {}
        resolution_type = solution_data.get('resolution_type', 'full')
        solution_user_desc = solution_data.get('description_user', '') or solution_data.get('description', '')
        concerns_user = solution_data.get('underlying_concerns_user', '') or solution_data.get('underlying_concerns', '')
        concerns_admin = solution_data.get('underlying_concerns_admin', '') or solution_data.get('underlying_concerns', '')

        all_success = all(item.get('result', '').lower() == 'success' for item in proof_items) if proof_items else False

        first_name = ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'
        user_body_parts = [
            f"Hi {first_name},",
            f"",
        ]

        if all_success:
            user_body_parts.append(f"Your support ticket **{ticket.ticket_number}** has been resolved. The issue has been corrected.")
        else:
            user_body_parts.append(f"Your support ticket **{ticket.ticket_number}** has been addressed.")

        if solution_user_desc:
            clean_desc = re.sub(
                r'\s*\((?:[^)]*@[^)]*|[^)]*\+\d[\d\s\-]*|[^)]*hihello[^)]*|[^)]*bookwithme[^)]*|[^)]*outlook\.office[^)]*)\)',
                '', solution_user_desc, flags=re.IGNORECASE
            )
            clean_desc = re.sub(r'\.\s*(HiHello card|Book time|Contact):?\s*https?://\S+', '.', clean_desc, flags=re.IGNORECASE)
            clean_desc = re.sub(r'(HiHello card|Book time|Contact):?\s*https?://\S+', '', clean_desc, flags=re.IGNORECASE)
            clean_desc = re.sub(
                r'[Aa]pproved by admin:?\s*(\w+(?:\s+\w+)?)\s*(?:\([^)]*\))?\.?',
                r'Admin \1 also reviewed and approved.',
                clean_desc
            )
            clean_desc = re.sub(r'\s{2,}', ' ', clean_desc).strip()
            user_body_parts.extend([
                f"",
                f"**What was done:** {clean_desc}",
            ])

        if concerns_user:
            user_body_parts.extend([
                f"",
                f"**Please Note:** {concerns_user}",
            ])

        user_body_parts.extend([
            f"",
            f"If you have any other issues, feel free to submit a new support ticket.",
            f"",
            f"— Scout Support",
        ])

        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body="\n".join(user_body_parts),
            ticket=ticket,
            email_type='completion_user',
        )

        admin_body_parts = [
            f"Ticket **{ticket.ticket_number}** has been completed.",
            f"",
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})",
            f"**Category:** {CATEGORY_LABELS.get(ticket.category, ticket.category)}",
            f"**Resolution Type:** {resolution_type.upper()}",
            f"",
            f"**Execution Proof:**",
            f"{proof_text}",
        ]

        if concerns_admin:
            admin_body_parts.extend([
                f"",
                f"**⚠️ Admin Advisory — Underlying Concerns:**",
                f"{concerns_admin}",
                f"",
                f"The immediate fix was applied successfully, but the above concern may require further investigation. Consider reaching out to Bullhorn Support if the issue is systemic or recurring.",
            ])

        admin_body_parts.extend([
            f"",
            f"— Scout Support",
        ])

        self._send_email(
            to_email=ticket.admin_email,
            subject=f"[{ticket.ticket_number}] Completed ✅" if not concerns_admin else f"[{ticket.ticket_number}] Completed ✅ (Advisory Included)",
            body="\n".join(admin_body_parts),
            ticket=ticket,
            email_type='completion_admin',
        )

        stakeholder_body = (
            f"**Ticket Resolved:** {ticket.ticket_number}\n\n"
            f"**Subject:** {ticket.subject}\n"
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})\n"
            f"**Category:** {CATEGORY_LABELS.get(ticket.category, ticket.category)}\n"
            f"**Resolution Type:** {resolution_type.upper()}\n\n"
            f"**What was done:**\n{proof_text}\n\n"
            f"— Scout Support"
        )
        self._notify_stakeholders(
            ticket,
            subject=f"[{ticket.ticket_number}] Resolved ✅",
            body=stakeholder_body,
            email_type='stakeholder_completed',
        )

    def _send_escalation_email(self, ticket, reason: str):
        from scout_support_service import CATEGORY_LABELS, STSI_ESCALATION_CONTACTS

        user_body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Regarding your ticket **{ticket.ticket_number}** — after reviewing your issue, "
            f"I've determined that this requires human intervention and have escalated it to the support team.\n\n"
            f"**Reason for escalation:** {reason}\n\n"
            f"Someone from the team will follow up with you directly.\n\n"
            f"— Scout Support"
        )
        escalation_cc = [ticket.admin_email]
        stsi_escalation = STSI_ESCALATION_CONTACTS.get(ticket.category) if ticket.brand == 'STSI' else None
        if stsi_escalation:
            escalation_cc.append(stsi_escalation)

        self._send_email(
            to_email=ticket.submitter_email,
            cc_emails=escalation_cc,
            subject=f"[{ticket.ticket_number}] Escalated to Support Team",
            body=user_body,
            ticket=ticket,
            email_type='escalation',
        )

        stakeholder_body = (
            f"**Ticket Escalated:** {ticket.ticket_number}\n\n"
            f"**Subject:** {ticket.subject}\n"
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})\n"
            f"**Category:** {CATEGORY_LABELS.get(ticket.category, ticket.category)}\n"
            f"**Reason:** {reason}\n\n"
            f"— Scout Support"
        )
        self._notify_stakeholders(
            ticket,
            subject=f"[{ticket.ticket_number}] Escalated",
            body=stakeholder_body,
            email_type='stakeholder_escalated',
        )

    def _send_status_email(self, ticket, new_status: str):
        from scout_support_service import CATEGORY_LABELS

        status_messages = {
            'on_hold': "Your ticket has been placed on hold by the administrator. You'll be notified when it's resumed.",
            'closed': "Your ticket has been closed. If you still need assistance, please submit a new support ticket.",
        }
        message = status_messages.get(new_status, f"Your ticket status has been updated to: {new_status}")

        body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Regarding ticket **{ticket.ticket_number}**:\n\n"
            f"{message}\n\n"
            f"— Scout Support"
        )
        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=body,
            ticket=ticket,
            email_type='status_update',
        )

        stakeholder_body = (
            f"**Ticket Status Update:** {ticket.ticket_number}\n\n"
            f"**Subject:** {ticket.subject}\n"
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})\n"
            f"**New Status:** {new_status.replace('_', ' ').title()}\n\n"
            f"— Scout Support"
        )
        self._notify_stakeholders(
            ticket,
            subject=f"[{ticket.ticket_number}] {new_status.replace('_', ' ').title()}",
            body=stakeholder_body,
            email_type='stakeholder_status_update',
        )

    def _escalate_to_admin(self, ticket, reason: str, understanding: str = ''):
        from extensions import db
        from scout_support_service import CATEGORY_LABELS, DEFAULT_ADMIN_EMAIL, STSI_ESCALATION_CONTACTS

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)

        conversation_summary = ''
        try:
            from models import SupportConversation
            conversations = SupportConversation.query.filter_by(
                ticket_id=ticket.id
            ).order_by(SupportConversation.created_at.asc()).all()

            if conversations:
                summary_parts = []
                for conv in conversations:
                    direction = "User" if conv.direction == 'inbound' else "Scout Support"
                    snippet = (conv.body or '')[:500]
                    summary_parts.append(f"[{direction}] {snippet}")
                conversation_summary = "\n---\n".join(summary_parts)
        except Exception as e:
            logger.warning(f"Could not build conversation summary for escalation: {e}")

        ticket.status = 'escalated'
        if not ticket.escalation_reason:
            ticket.escalation_reason = reason
        db.session.commit()

        user_body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Thank you for your patience regarding ticket **{ticket.ticket_number}**.\n\n"
            f"After reviewing your request, I've determined that this issue requires direct attention from our team lead. "
            f"I've escalated your ticket and included a full summary of everything we've discussed so far.\n\n"
            f"You can expect a follow-up from them shortly.\n\n"
            f"— Scout Support"
        )

        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=user_body,
            ticket=ticket,
            email_type='escalation_user_notice',
            cc_email=DEFAULT_ADMIN_EMAIL,
        )

        admin_parts = [
            f"**Escalated Ticket: {ticket.ticket_number}**\n",
            f"**Category:** {category_label}",
            f"**Subject:** {ticket.subject}",
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})",
            f"**Department:** {ticket.submitter_department or 'Not specified'}",
            f"**Priority:** {ticket.priority}\n",
            f"**Escalation Reason:** {reason}\n",
        ]

        if understanding:
            admin_parts.append(f"**AI Understanding:** {understanding}\n")

        admin_parts.append(f"**Original Description:**\n{ticket.description}\n")

        if conversation_summary:
            admin_parts.append(f"**Conversation History:**\n{conversation_summary}")

        admin_body = "\n".join(admin_parts)

        escalation_admin_cc = []
        stsi_escalation = STSI_ESCALATION_CONTACTS.get(ticket.category) if ticket.brand == 'STSI' else None
        if stsi_escalation:
            escalation_admin_cc.append(stsi_escalation)

        self._send_email(
            to_email=DEFAULT_ADMIN_EMAIL,
            subject=f"[ESCALATED] [{ticket.ticket_number}] {ticket.subject}",
            body=admin_body,
            ticket=ticket,
            email_type='escalation_admin_summary',
            cc_emails=escalation_admin_cc if escalation_admin_cc else None,
        )

        self._notify_stakeholders(
            ticket,
            subject=f"[{ticket.ticket_number}] Escalated",
            body=(
                f"**Ticket Escalated:** {ticket.ticket_number}\n\n"
                f"**Subject:** {ticket.subject}\n"
                f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})\n"
                f"**Category:** {CATEGORY_LABELS.get(ticket.category, ticket.category)}\n"
                f"**Reason:** {reason}\n\n"
                f"— Scout Support"
            ),
            email_type='stakeholder_escalated',
        )

        logger.info(f"⚠️ Ticket {ticket.ticket_number} escalated to {DEFAULT_ADMIN_EMAIL}: {reason}")

    def _build_quoted_history(self, ticket, recipient_email: str = None, is_admin_facing: bool = False) -> str:
        from models import SupportConversation
        from scout_support_service import SCOUT_SUPPORT_EMAIL

        convos = SupportConversation.query.filter_by(
            ticket_id=ticket.id,
        ).order_by(SupportConversation.created_at.desc()).limit(10).all()

        if not convos:
            return ''

        ADMIN_ONLY_EMAIL_TYPES = {
            'admin_approval_request', 'admin_reply', 'admin_clarification_response',
            'admin_new_ticket_notification', 'completion_admin',
            'admin_execution_failure',
            'stakeholder_new_ticket', 'stakeholder_completed',
            'stakeholder_escalated', 'stakeholder_status_update',
            'escalation_admin_summary',
        }

        is_user_facing = not is_admin_facing

        parts = []
        for c in convos:
            if is_user_facing and c.email_type in ADMIN_ONLY_EMAIL_TYPES:
                continue
            if is_user_facing and c.direction == 'inbound' and c.sender_email and ticket.admin_email:
                if c.sender_email.lower() == ticket.admin_email.lower():
                    continue

            direction_label = c.sender_email if c.direction == 'inbound' else f"Scout Support ({SCOUT_SUPPORT_EMAIL})"
            timestamp = c.created_at.strftime('%b %d, %Y at %I:%M %p') if c.created_at else ''
            body_text = (c.body or '').strip()
            if not body_text:
                continue
            body_html = body_text.replace('\n', '<br>')
            parts.append(
                f'<b>From:</b> {direction_label}<br>'
                f'<b>Date:</b> {timestamp}<br>'
                f'<b>Subject:</b> {c.subject or ""}<br><br>'
                f'{body_html}'
            )

        if not parts:
            return ''

        quoted_html = '<br>'.join(
            f'<div style="border-left:2px solid #ccc;padding-left:10px;margin:10px 0;color:#555;">{p}</div>'
            for p in parts
        )
        return (
            '<br><br>'
            '<div style="border-top:1px solid #ddd;margin-top:20px;padding-top:10px;">'
            '<span style="color:#888;font-size:12px;">— Previous messages —</span><br>'
            f'{quoted_html}'
            '</div>'
        )

    def _send_email(self, to_email: str, subject: str, body: str, ticket=None,
                    email_type: str = 'general', cc_email: str = None,
                    cc_emails: Optional[List[str]] = None):
        from extensions import db
        from models import SupportConversation, EmailDeliveryLog
        from scout_support_service import SCOUT_SUPPORT_EMAIL, SCOUT_SUPPORT_NAME

        try:
            from email_service import EmailService
            email_svc = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)

            msg_id = f"<scout-support-{uuid.uuid4().hex[:12]}@scoutgenius.ai>"

            in_reply_to = None
            references = None
            if ticket and ticket.last_message_id:
                in_reply_to = ticket.last_message_id
                thread_id = getattr(ticket, 'thread_message_id', None) or ''
                if thread_id and thread_id != in_reply_to:
                    references = f"{thread_id} {in_reply_to}"
                else:
                    references = in_reply_to

            if cc_emails:
                cc_list = list(cc_emails)
            elif cc_email:
                cc_list = [cc_email]
            else:
                cc_list = []

            quoted_history = ''
            if ticket:
                is_admin_facing = email_type in self.ADMIN_FACING_EMAIL_TYPES
                quoted_history = self._build_quoted_history(ticket, recipient_email=to_email, is_admin_facing=is_admin_facing)

            html_body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', body)
            html_body = html_body.replace('\n', '<br>')
            if quoted_history:
                html_body += quoted_history

            result = email_svc.send_html_email(
                to_email=to_email,
                subject=subject,
                html_content=html_body,
                notification_type=email_type,
                cc_emails=cc_list if cc_list else None,
                in_reply_to=in_reply_to,
                references=references,
                reply_to=SCOUT_SUPPORT_EMAIL,
                from_name=SCOUT_SUPPORT_NAME,
                from_email=SCOUT_SUPPORT_EMAIL,
                message_id=msg_id,
            )

            success = result.get('success', False) if isinstance(result, dict) else bool(result)

            if ticket:
                is_admin = email_type in self.ADMIN_FACING_EMAIL_TYPES
                conv = SupportConversation(
                    ticket_id=ticket.id,
                    direction='outbound',
                    sender_email=SCOUT_SUPPORT_EMAIL,
                    recipient_email=to_email,
                    subject=subject,
                    body=body,
                    message_id=msg_id,
                    email_type=email_type,
                )
                db.session.add(conv)
                if not getattr(ticket, 'thread_message_id', None):
                    ticket.thread_message_id = msg_id
                if not is_admin:
                    ticket.last_message_id = msg_id
                db.session.commit()

            if success:
                logger.info(f"📧 Scout Support email sent: {subject} → {to_email}")
            else:
                logger.error(f"❌ Scout Support email failed: {subject} → {to_email}")

        except Exception as e:
            logger.error(f"❌ Scout Support email error: {e}")
