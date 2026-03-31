"""
Scout Support Service — AI-powered internal ATS support ticket resolution.

Handles the full lifecycle of support tickets:
1. Intake: AI reads ticket + attachments, generates understanding summary
2. Clarification: Back-and-forth email conversation with user
3. Solution proposal: AI proposes fix, gets user approval
4. Admin approval: Summary sent to admin for final authorization
5. Execution: Bullhorn API actions with full audit trail
6. Completion: Proof sent to user + admin

Architecture: Modular mixin package (scout_support/).
ScoutSupportService inherits from 5 focused mixins:
- EmailMixin: Email sending, quoted history, stakeholder notifications
- AIAnalysisMixin: AI understanding, clarification analysis, attachment extraction
- ExecutionMixin: Bullhorn API action execution, entity CRUD, note creation
- AuditMixin: Audit note creation, change humanization
- ConversationMixin: Reply handling, classification, approval flow, admin Q&A

External imports (from scout_support_service import ScoutSupportService) remain unchanged.
"""
import os
import re
import json
import logging
from datetime import datetime
from typing import Dict, Optional, List

from openai import OpenAI

from scout_support.email import EmailMixin
from scout_support.ai_analysis import AIAnalysisMixin
from scout_support.execution import ExecutionMixin
from scout_support.audit import AuditMixin
from scout_support.conversation import ConversationMixin

logger = logging.getLogger(__name__)

SCOUT_SUPPORT_EMAIL = 'support@scoutgenius.ai'
SCOUT_SUPPORT_NAME = 'Scout Support'
DEFAULT_ADMIN_EMAIL = 'kroots@myticas.com'

STSI_STAKEHOLDER_EMAIL = 'jbocek@stsigroup.com'

STSI_ESCALATION_CONTACTS = {
    'email_notifications': 'doneil@q-staffing.com',
    'backoffice_onboarding': 'evalentine@stsigroup.com',
    'backoffice_finance': 'evalentine@stsigroup.com',
}

IMMEDIATE_ESCALATION_CATEGORIES = ['email_notifications', 'backoffice_onboarding', 'backoffice_finance']

CATEGORY_LABELS = {
    'ats_issue': 'ATS / Bullhorn Issue',
    'data_correction': 'Data Correction Request',
    'candidate_parsing': 'Candidate Parsing Error',
    'job_posting': 'Job Posting Issue',
    'account_access': 'Account / Access Request',
    'email_notifications': 'Email / Notification Issue',
    'feature_request': 'Feature Request',
    'other': 'Other',
    'backoffice_onboarding': 'Back-Office: Onboarding',
    'backoffice_finance': 'Back-Office: Finance (BTE)',
    'platform_bug': 'Platform Bug Report',
    'platform_feature': 'Platform Feature Request',
    'platform_question': 'Platform Question',
    'platform_other': 'Platform Feedback',
}

PLATFORM_CATEGORIES = ['platform_bug', 'platform_feature', 'platform_question', 'platform_other']

AI_FULL_CATEGORIES = ['ats_issue', 'candidate_parsing', 'job_posting', 'account_access', 'data_correction',
                      'email_notifications', 'feature_request', 'other',
                      'backoffice_onboarding', 'backoffice_finance']

HANDOFF_CATEGORIES = []

BACKOFFICE_CATEGORIES = []


def get_backoffice_cc(category: str, department: str, brand: str) -> List[str]:
    cc_list = [DEFAULT_ADMIN_EMAIL]
    dept = (department or '').strip()

    if brand == 'STSI':
        cc_list.append('evalentine@stsigroup.com')
    elif dept == 'MYT-Ottawa':
        cc_list.append('accounting@myticas.com')
    elif dept in ('MYT-Chicago', 'MYT-Ohio', 'MYT-Clover'):
        cc_list.append('ai@myticas.com')
    else:
        cc_list.append('accounting@myticas.com')

    return cc_list


class ScoutSupportService(
    EmailMixin,
    AIAnalysisMixin,
    ExecutionMixin,
    AuditMixin,
    ConversationMixin,
):

    def __init__(self, bullhorn_service=None):
        if bullhorn_service:
            self.bullhorn_service = bullhorn_service
        else:
            self.bullhorn_service = self._init_bullhorn()
        api_key = os.environ.get('OPENAI_API_KEY')
        self.openai_client = OpenAI(api_key=api_key) if api_key else None
        logger.info(f"Scout Support Service initialized (Bullhorn: {'connected' if self.bullhorn_service else 'unavailable'})")

    def _init_bullhorn(self):
        try:
            from utils.bullhorn_helpers import get_bullhorn_service
            svc = get_bullhorn_service()
            if svc and svc.authenticate():
                logger.info("Bullhorn service authenticated for Scout Support")
                return svc
            else:
                logger.warning("Bullhorn authentication failed — execution disabled")
                return None
        except Exception as e:
            logger.warning(f"Could not initialize Bullhorn service: {e}")
            return None

    def create_ticket(self, category: str, subject: str, description: str,
                      submitter_name: str, submitter_email: str,
                      submitter_department: str = '', brand: str = 'Myticas',
                      priority: str = 'medium',
                      attachment_data: Optional[List[Dict]] = None,
                      attachment_info: Optional[List[Dict]] = None) -> 'SupportTicket':
        from extensions import db
        from models import SupportTicket, SupportConversation

        for attempt in range(3):
            ticket_number = SupportTicket.generate_ticket_number()
            if not SupportTicket.query.filter_by(ticket_number=ticket_number).first():
                break

        ticket = SupportTicket(
            ticket_number=ticket_number,
            category=category,
            subject=subject,
            description=description,
            priority=priority,
            brand=brand,
            status='new',
            submitter_name=submitter_name,
            submitter_email=submitter_email,
            submitter_department=submitter_department,
            admin_email=DEFAULT_ADMIN_EMAIL,
        )
        db.session.add(ticket)
        db.session.flush()

        initial_conv = SupportConversation(
            ticket_id=ticket.id,
            direction='inbound',
            sender_email=submitter_email,
            recipient_email=SCOUT_SUPPORT_EMAIL,
            subject=subject,
            body=description,
            email_type='ticket_submission',
        )
        db.session.add(initial_conv)

        effective_attachments = attachment_data or attachment_info
        if effective_attachments:
            filenames = [a.get('filename', 'unknown') for a in effective_attachments]
            ticket.description += f"\n\n[Attachments: {', '.join(filenames)}]"

        if attachment_data:
            from models import SupportAttachment
            for att in attachment_data:
                sa = SupportAttachment(
                    ticket_id=ticket.id,
                    filename=att.get('filename', 'attachment'),
                    content_type=att.get('content_type', 'application/octet-stream'),
                    file_data=att.get('data', b''),
                    file_size=len(att.get('data', b'')),
                )
                db.session.add(sa)

        self._attachment_data = attachment_data

        db.session.commit()
        logger.info(f"📋 Created support ticket {ticket_number} from {submitter_email}: {subject}")

        self._send_admin_new_ticket_notification(ticket)

        return ticket

    def process_new_ticket(self, ticket_id: int) -> bool:
        from extensions import db
        from models import SupportTicket

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            logger.error(f"Ticket {ticket_id} not found")
            return False

        if ticket.category in PLATFORM_CATEGORIES:
            return self._process_platform_ticket(ticket)
        elif ticket.category in HANDOFF_CATEGORIES:
            return self._process_handoff_ticket(ticket)
        elif ticket.category in BACKOFFICE_CATEGORIES:
            return self._process_backoffice_ticket(ticket)
        else:
            return self._process_ai_full_ticket(ticket)

    def _process_ai_full_ticket(self, ticket) -> bool:
        from extensions import db

        attachment_content = ''
        attachment_ack = ''
        if hasattr(self, '_attachment_data') and self._attachment_data:
            logger.info(f"📎 Ticket {ticket.ticket_number}: Processing {len(self._attachment_data)} attachment(s)")
            attachment_content = self._extract_attachment_content(self._attachment_data)
            self._attachment_data = None
            attachment_ack = self._build_attachment_acknowledgment()
            if attachment_content:
                logger.info(f"📎 Ticket {ticket.ticket_number}: Extracted {len(attachment_content)} chars of attachment content")
            else:
                logger.warning(f"📎 Ticket {ticket.ticket_number}: Attachment extraction returned empty content")
        else:
            logger.info(f"📎 Ticket {ticket.ticket_number}: No attachments to process")

        understanding = self._generate_understanding(ticket, attachment_content=attachment_content)
        if not understanding:
            logger.error(f"Failed to generate AI understanding for ticket {ticket.ticket_number}")
            self._escalate_to_admin(ticket, reason="AI was unable to analyze this ticket due to a processing error.")
            return False

        try:
            parsed = json.loads(understanding)
        except (json.JSONDecodeError, TypeError):
            parsed = {}

        confidence = parsed.get('confidence_level', 'high')
        can_resolve = parsed.get('can_resolve_autonomously', True)
        has_clarification = parsed.get('clarification_needed', False)
        resolution_type = parsed.get('resolution_type', 'full')
        concerns_user = parsed.get('underlying_concerns_user', '') or parsed.get('underlying_concerns', '')
        concerns_admin = parsed.get('underlying_concerns_admin', '') or parsed.get('underlying_concerns', '')

        proposed_user = parsed.get('proposed_solution_user', '') or parsed.get('proposed_solution', '')
        proposed_admin = parsed.get('proposed_solution_admin', '') or parsed.get('proposed_solution', '')
        execution_steps = parsed.get('execution_steps', []) or []

        has_actionable_solution = bool(proposed_user and execution_steps)
        if has_actionable_solution and has_clarification:
            logger.info(f"🔧 Ticket {ticket.ticket_number}: AI asked for clarification but provided a solution with {len(execution_steps)} execution steps — overriding to propose solution directly")
            has_clarification = False
            parsed['clarification_needed'] = False
            can_resolve = True
            parsed['can_resolve_autonomously'] = True
            understanding = json.dumps(parsed)

        if resolution_type == 'escalate' or (confidence == 'low' and not has_clarification):
            ticket.ai_understanding = understanding
            ticket.status = 'escalated'
            escalation_reason = parsed.get('escalation_reason', 'AI determined it cannot fully resolve this issue.')
            ticket.escalation_reason = escalation_reason
            db.session.commit()

            self._escalate_to_admin(ticket, reason=escalation_reason, understanding=parsed.get('understanding', ''))
            logger.info(f"⚠️ Ticket {ticket.ticket_number} escalated — AI confidence: {confidence}, resolution_type: {resolution_type}")
            return True

        if confidence in ('high', 'medium') and can_resolve and not has_clarification and proposed_user:
            ticket.ai_understanding = understanding
            ticket.proposed_solution = json.dumps({
                'description_user': proposed_user,
                'description_admin': proposed_admin,
                'can_execute': True,
                'requires_bullhorn': parsed.get('requires_bullhorn_api', False),
                'affected_entities': parsed.get('affected_entities', []),
                'execution_steps': parsed.get('execution_steps', []),
                'resolution_type': resolution_type,
                'underlying_concerns_user': concerns_user,
                'underlying_concerns_admin': concerns_admin,
            })
            ticket.status = 'awaiting_user_approval'
            db.session.commit()

            self._send_solution_proposal_email(ticket, proposed_user, underlying_concerns=concerns_user, attachment_ack=attachment_ack)
            logger.info(f"✅ Ticket {ticket.ticket_number} — {confidence} confidence, solution proposed (resolution_type={resolution_type})")
            return True

        needs_clarification_first = (
            confidence == 'low' or not can_resolve or has_clarification
        )

        if needs_clarification_first:
            clarification_questions = parsed.get('clarification_questions') or []
            specific_questions = [q for q in clarification_questions if q and len(q) > 10 and 'additional details' not in q.lower() and 'more detail' not in q.lower() and 'anything else' not in q.lower() and 'more context' not in q.lower()]

            if not specific_questions and proposed_user:
                logger.info(f"🔧 Ticket {ticket.ticket_number}: No specific clarification questions available but solution exists — proposing solution instead of asking generic questions")
                parsed['clarification_needed'] = False
                parsed['can_resolve_autonomously'] = True
                understanding = json.dumps(parsed)
                ticket.ai_understanding = understanding
                ticket.proposed_solution = json.dumps({
                    'description_user': proposed_user,
                    'description_admin': proposed_admin,
                    'can_execute': True,
                    'requires_bullhorn': parsed.get('requires_bullhorn_api', False),
                    'affected_entities': parsed.get('affected_entities', []) or [],
                    'execution_steps': execution_steps,
                    'resolution_type': resolution_type,
                    'underlying_concerns_user': concerns_user,
                    'underlying_concerns_admin': concerns_admin,
                })
                ticket.status = 'awaiting_user_approval'
                db.session.commit()
                self._send_solution_proposal_email(ticket, proposed_user, underlying_concerns=concerns_user, attachment_ack=attachment_ack)
                logger.info(f"✅ Ticket {ticket.ticket_number} — override to solution proposal (had generic clarification questions)")
                return True
            elif not specific_questions:
                logger.info(f"🔧 Ticket {ticket.ticket_number}: No specific clarification questions and no solution — escalating to admin")
                ticket.ai_understanding = json.dumps(parsed)
                ticket.status = 'escalated'
                ticket.escalation_reason = 'AI could not determine specific questions to ask or propose a solution.'
                db.session.commit()
                self._escalate_to_admin(ticket, reason=ticket.escalation_reason, understanding=parsed.get('understanding', ''))
                return True
            else:
                parsed['clarification_needed'] = True
                parsed['clarification_questions'] = specific_questions
                understanding = json.dumps(parsed)

        ticket.ai_understanding = understanding
        ticket.status = 'acknowledged'
        db.session.commit()

        self._send_acknowledgment_email(ticket, understanding, attachment_ack=attachment_ack)
        logger.info(f"✅ Ticket {ticket.ticket_number} acknowledged (AI full, confidence={confidence}, resolution_type={resolution_type}, clarify_first={needs_clarification_first}), email sent to {ticket.submitter_email}")
        return True

    def _generate_platform_understanding(self, ticket) -> str:
        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)

        prompt = f"""You are Scout Genius, a platform support assistant. A user has submitted platform feedback. Analyze and summarize it.

Feedback Details:
- Category: {category_label}
- Subject: {ticket.subject}
- Submitted by: {ticket.submitter_name} ({ticket.submitter_email})

User's Message:
{ticket.description}

Respond with a JSON object:
{{
    "understanding": "A clear summary of what the user is asking for or reporting, written back to them for confirmation.",
    "clarification_needed": true/false,
    "clarification_questions": ["question 1"],
    "is_platform_ticket": true
}}"""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5.4',
                messages=[
                    {'role': 'system', 'content': 'You are a helpful platform support assistant. Respond only in valid JSON.'},
                    {'role': 'user', 'content': prompt}
                ],
                max_completion_tokens=500,
                response_format={'type': 'json_object'},
            )
            content = response.choices[0].message.content
            if content and content.strip():
                parsed = json.loads(content.strip())
                parsed['is_platform_ticket'] = True
                return json.dumps(parsed)
        except Exception as e:
            logger.warning(f"Platform AI analysis failed for {ticket.ticket_number}: {e}")

        return json.dumps({
            'understanding': f"Platform feedback received: {ticket.subject}",
            'is_platform_ticket': True,
            'clarification_needed': False,
        })

    def _process_platform_ticket(self, ticket) -> bool:
        from extensions import db

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)

        understanding = self._generate_platform_understanding(ticket)
        if not understanding:
            logger.warning(f"AI analysis failed for platform ticket {ticket.ticket_number}, using fallback")
            understanding = json.dumps({
                'understanding': f"Platform feedback received: {ticket.subject}",
                'is_platform_ticket': True,
                'clarification_needed': False,
            })

        try:
            parsed = json.loads(understanding)
        except (json.JSONDecodeError, TypeError):
            parsed = {'understanding': 'We have received your feedback and are reviewing it.'}

        parsed['is_platform_ticket'] = True
        understanding = json.dumps(parsed)

        ticket.ai_understanding = understanding
        ticket.status = 'acknowledged'
        db.session.commit()

        ai_summary = parsed.get('understanding', 'We have received your feedback and are reviewing it.')
        has_clarification = parsed.get('clarification_needed', False)
        questions = parsed.get('clarification_questions', [])

        first_name = ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'
        page_info = ''
        if ticket.description and 'Page:' in ticket.description:
            for line in ticket.description.split('\n'):
                if line.strip().startswith('Page:'):
                    page_info = f"\n**{line.strip()}**"
                    break

        body_parts = [
            f"Hi {first_name},",
            "",
            f"Thank you for your feedback! Your submission has been received and assigned ticket number **{ticket.ticket_number}**.",
            "",
            f"**Type:** {category_label}",
            f"**Subject:** {ticket.subject}{page_info}",
            "",
            f"**My Understanding:**",
            f"{ai_summary}",
        ]

        if has_clarification and questions:
            body_parts.append("")
            body_parts.append("To help us address this more effectively, could you clarify:")
            for i, q in enumerate(questions, 1):
                body_parts.append(f"{i}. {q}")
            body_parts.append("")
            body_parts.append("Please reply to this email with your answers.")
            ticket.status = 'clarifying'
            db.session.commit()
        else:
            body_parts.append("")
            body_parts.append("Our team has been notified and will review your feedback. "
                              "You can track the status of this and all your submissions from the **My Tickets** page.")

        body_parts.extend(["", "— Scout Genius"])

        self._send_platform_email(
            to_email=ticket.submitter_email,
            subject=f"[{ticket.ticket_number}] {ticket.subject}",
            body="\n".join(body_parts),
            ticket=ticket,
            email_type='platform_acknowledgment',
        )

        admin_body = (
            f"**New Platform Feedback:** {ticket.ticket_number}\n\n"
            f"**Type:** {category_label}\n"
            f"**Subject:** {ticket.subject}\n"
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})\n"
            f"**Brand:** {ticket.brand or 'Myticas'}\n\n"
            f"**Message:**\n{ticket.description}\n\n"
            f"**AI Summary:**\n{ai_summary}\n\n"
            f"View and manage this ticket from the Scout Support dashboard."
        )
        self._send_platform_email(
            to_email=DEFAULT_ADMIN_EMAIL,
            subject=f"[Platform] {ticket.ticket_number} — {category_label}",
            body=admin_body,
            ticket=None,
            email_type='platform_admin_notification',
        )

        logger.info(f"🎫 Platform ticket {ticket.ticket_number} ({category_label}) acknowledged via AI analysis, admin notified")
        return True

    def _send_platform_email(self, to_email: str, subject: str, body: str, ticket=None,
                              email_type: str = 'general'):
        import re
        import uuid as _uuid
        from extensions import db
        from models import SupportConversation, EmailDeliveryLog

        msg_id = f"<scout-platform-{_uuid.uuid4().hex[:12]}@scoutgenius.ai>"

        if ticket:
            conv = SupportConversation(
                ticket_id=ticket.id,
                direction='outbound',
                sender_email='support@scoutgenius.ai',
                recipient_email=to_email,
                subject=subject,
                body=body,
                message_id=msg_id,
                email_type=email_type,
            )
            db.session.add(conv)
            if not getattr(ticket, 'thread_message_id', None):
                ticket.thread_message_id = msg_id
            db.session.commit()

        try:
            from email_service import EmailService
            email_svc = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)

            html_body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', body)
            html_body = html_body.replace('\n', '<br>')

            branded_html = (
                '<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; max-width: 640px; margin: 0 auto;">'
                '<div style="background: linear-gradient(135deg, #4a9678, #3d7d64); padding: 16px 24px; border-radius: 8px 8px 0 0;">'
                '<table cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
                '<td style="color: #ffffff; font-size: 18px; font-weight: 600;">Scout Genius</td>'
                '<td align="right" style="color: rgba(255,255,255,0.8); font-size: 12px;">Platform Support</td>'
                '</tr></table>'
                '</div>'
                '<div style="padding: 24px; background: #ffffff; border: 1px solid #e5e7eb; border-top: none;">'
                f'{html_body}'
                '</div>'
                '<div style="padding: 12px 24px; background: #f9fafb; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 8px 8px; text-align: center;">'
                '<span style="color: #9ca3af; font-size: 11px;">Powered by Scout Genius&trade; &mdash; support@scoutgenius.ai</span>'
                '</div>'
                '</div>'
            )

            result = email_svc.send_html_email(
                to_email=to_email,
                subject=subject,
                html_content=branded_html,
                notification_type=email_type,
                reply_to='support@scoutgenius.ai',
                from_name='Scout Genius',
                from_email='support@scoutgenius.ai',
                message_id=msg_id,
            )

            success = result.get('success', False) if isinstance(result, dict) else bool(result)

            if success:
                logger.info(f"📧 Platform email sent: {subject} → {to_email}")
            else:
                logger.error(f"❌ Platform email failed: {subject} → {to_email}")

        except Exception as e:
            logger.error(f"❌ Platform email error: {e}")

    def _process_handoff_ticket(self, ticket) -> bool:
        from extensions import db

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)
        ticket.status = 'escalated'
        ticket.escalation_reason = f"Category '{category_label}' is handled directly by the admin."
        db.session.commit()

        body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Thank you for reaching out. Your support ticket has been received and assigned ticket number **{ticket.ticket_number}**.\n\n"
            f"**Category:** {category_label}\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"I've documented your request and have copied in our team lead who will handle this directly. "
            f"You can expect a follow-up from them shortly.\n\n"
            f"— Scout Support"
        )

        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"[{ticket.ticket_number}] {ticket.subject}",
            body=body,
            ticket=ticket,
            email_type='handoff_acknowledgment',
            cc_email=DEFAULT_ADMIN_EMAIL,
        )

        self._notify_stakeholders(
            ticket,
            subject=f"[{ticket.ticket_number}] {ticket.subject}",
            body=body,
            email_type='stakeholder_handoff',
        )

        logger.info(f"📧 Handoff ticket {ticket.ticket_number} acknowledged, admin CC'd")
        return True

    def _process_backoffice_ticket(self, ticket) -> bool:
        from extensions import db

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)
        cc_list = get_backoffice_cc(ticket.category, ticket.submitter_department, ticket.brand)

        ticket.status = 'escalated'
        ticket.escalation_reason = f"Category '{category_label}' is routed to back-office."
        db.session.commit()

        body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Thank you for submitting your request. Your ticket has been assigned number **{ticket.ticket_number}**.\n\n"
            f"**Category:** {category_label}\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"This request has been routed to the appropriate team. You'll receive a follow-up shortly.\n\n"
            f"— Scout Support"
        )

        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"[{ticket.ticket_number}] {ticket.subject}",
            body=body,
            ticket=ticket,
            email_type='backoffice_acknowledgment',
            cc_emails=cc_list,
        )

        logger.info(f"📧 Back-office ticket {ticket.ticket_number} acknowledged, CC'd: {cc_list}")
        return True

    def close_ticket(self, ticket_id: int, resolution_note: str, closed_by: str, new_status: str = 'closed') -> bool:
        from extensions import db
        from models import SupportTicket

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            return False

        ticket.status = new_status
        ticket.resolution_note = resolution_note
        ticket.resolved_by = closed_by
        ticket.resolved_at = datetime.utcnow()
        db.session.commit()

        status_label = 'resolved' if new_status == 'completed' else 'closed'

        if new_status == 'completed':
            try:
                from scout_support.knowledge import KnowledgeService
                ks = KnowledgeService()
                ks.learn_from_ticket(ticket.id)
            except Exception as e:
                logger.warning(f"Knowledge learning failed for ticket {ticket.ticket_number}: {e}")

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)

        user_body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Your support ticket **{ticket.ticket_number}** has been {status_label}.\n\n"
            f"**Category:** {category_label}\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"**Resolution:**\n{resolution_note}\n\n"
            f"If you have any further questions or the issue persists, feel free to submit a new support ticket.\n\n"
            f"— Scout Support"
        )

        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=user_body,
            ticket=ticket,
            email_type=f'manual_{status_label}',
        )

        stakeholder_body = (
            f"**Ticket {status_label.title()}:** {ticket.ticket_number}\n\n"
            f"**Subject:** {ticket.subject}\n"
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})\n"
            f"**Category:** {category_label}\n"
            f"**{status_label.title()} by:** {closed_by}\n\n"
            f"**Resolution:**\n{resolution_note}\n\n"
            f"— Scout Support"
        )
        self._notify_stakeholders(
            ticket,
            subject=f"[{ticket.ticket_number}] {status_label.title()}",
            body=stakeholder_body,
            email_type=f'stakeholder_{status_label}',
        )

        logger.info(f"{'✅' if new_status == 'completed' else '🔒'} Ticket {ticket.ticket_number} {status_label} by {closed_by}: {resolution_note[:100]}")
        return True

    def retry_execution(self, ticket_id: int) -> Dict:
        from extensions import db
        from models import SupportTicket, SupportAction

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            return {'success': False, 'error': 'Ticket not found'}

        retryable_statuses = ('execution_failed', 'completed')
        if ticket.status not in retryable_statuses:
            return {'success': False, 'error': f'Cannot retry — ticket status is "{ticket.status}"'}

        SupportAction.query.filter_by(ticket_id=ticket.id).delete()
        ticket.execution_proof = None
        ticket.status = 'approved'
        db.session.commit()

        logger.info(f"🔄 Retrying execution for ticket {ticket.ticket_number} — cleared previous actions")

        success = self._execute_solution(ticket)

        return {
            'success': success,
            'status': ticket.status,
            'message': f'Execution {"completed successfully" if success else "encountered failures — check execution log"}'
        }

    def escalate_ticket(self, ticket_id: int, reason: str) -> bool:
        from extensions import db
        from models import SupportTicket

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            return False

        ticket.status = 'escalated'
        ticket.escalation_reason = reason
        db.session.commit()

        self._send_escalation_email(ticket, reason)
        logger.info(f"⬆️ Ticket {ticket.ticket_number} escalated: {reason}")
        return True

    def reopen_ticket(self, ticket_id: int, reopened_by: str) -> bool:
        from extensions import db
        from models import SupportTicket

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            return False

        previous_status = ticket.status
        ticket.status = 'acknowledged'
        ticket.resolution_note = None
        ticket.resolved_by = None
        ticket.resolved_at = None
        db.session.commit()

        logger.info(f"🔄 Ticket {ticket.ticket_number} reopened by {reopened_by} (was: {previous_status})")
        return True

    def update_platform_ticket_status(self, ticket_id: int, new_status: str, updated_by: str) -> bool:
        from extensions import db
        from models import SupportTicket

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            return False

        allowed_transitions = {
            'new': ['acknowledged', 'in_progress'],
            'acknowledged': ['in_progress', 'completed', 'closed'],
            'clarifying': ['in_progress', 'completed', 'closed'],
            'in_progress': ['completed', 'closed'],
        }

        allowed = allowed_transitions.get(ticket.status, [])
        if new_status not in allowed:
            logger.warning(f"Invalid status transition for {ticket.ticket_number}: {ticket.status} -> {new_status}")
            return False

        ticket.status = new_status
        db.session.commit()

        logger.info(f"📋 Platform ticket {ticket.ticket_number} status updated: {new_status} by {updated_by}")
        return True

    def close_platform_ticket(self, ticket_id: int, resolution_note: str, closed_by: str) -> bool:
        from extensions import db
        from models import SupportTicket

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            return False

        ticket.status = 'completed'
        ticket.resolution_note = resolution_note
        ticket.resolved_by = closed_by
        ticket.resolved_at = datetime.utcnow()
        db.session.commit()

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)
        first_name = ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'

        user_body = (
            f"Hi {first_name},\n\n"
            f"Your feedback ticket **{ticket.ticket_number}** has been resolved.\n\n"
            f"**Type:** {category_label}\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"**Resolution:**\n{resolution_note}\n\n"
            f"Thank you for helping us improve Scout Genius! If you have any further questions, "
            f"feel free to submit another feedback ticket.\n\n"
            f"— Scout Genius"
        )

        self._send_platform_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=user_body,
            ticket=ticket,
            email_type='platform_resolution',
        )

        logger.info(f"✅ Platform ticket {ticket.ticket_number} resolved by {closed_by}: {resolution_note[:100]}")
        return True

    def reply_to_ticket(self, ticket_id: int, reply_body: str, replied_by: str) -> bool:
        from extensions import db
        from models import SupportTicket, SupportConversation

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            return False

        if ticket.status in ('completed', 'closed'):
            return False

        if ticket.status != 'admin_handling':
            ticket.status = 'admin_handling'
            db.session.commit()

        conv = SupportConversation(
            ticket_id=ticket.id,
            direction='outbound',
            sender_email=replied_by,
            recipient_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=reply_body,
            email_type='admin_direct_reply',
        )
        db.session.add(conv)
        db.session.commit()

        first_name = ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'

        email_body = (
            f"Hi {first_name},\n\n"
            f"{reply_body}\n\n"
            f"**Ticket:** {ticket.ticket_number}\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"If you have questions, you can reply directly to this email.\n\n"
            f"— Scout Support"
        )

        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=email_body,
            ticket=ticket,
            email_type='admin_direct_reply',
        )

        logger.info(f"💬 Admin reply on ticket {ticket.ticket_number} by {replied_by}: {reply_body[:80]}")
        return True

    def reply_to_platform_ticket(self, ticket_id: int, reply_body: str, replied_by: str) -> bool:
        from extensions import db
        from models import SupportTicket

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            return False

        if ticket.status in ('completed', 'closed'):
            return False

        if ticket.status == 'new':
            ticket.status = 'acknowledged'
            db.session.commit()

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)
        first_name = ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'

        email_body = (
            f"Hi {first_name},\n\n"
            f"{reply_body}\n\n"
            f"**Ticket:** {ticket.ticket_number}\n"
            f"**Type:** {category_label}\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"— Scout Genius Support"
        )

        self._send_platform_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=email_body,
            ticket=ticket,
            email_type='admin_reply',
        )

        logger.info(f"💬 Admin reply on platform ticket {ticket.ticket_number} by {replied_by}: {reply_body[:80]}")
        return True

    def check_stale_platform_tickets(self):
        from models import SupportTicket

        stale_cutoff = datetime.utcnow() - timedelta(hours=48)
        stale_tickets = SupportTicket.query.filter(
            SupportTicket.category.in_(PLATFORM_CATEGORIES),
            SupportTicket.status.in_(['new', 'acknowledged', 'clarifying']),
            SupportTicket.created_at < stale_cutoff,
        ).all()

        if not stale_tickets:
            return 0

        lines = [f"**⏰ {len(stale_tickets)} Platform Ticket(s) Need Attention**\n"]
        for t in stale_tickets:
            age_hours = int((datetime.utcnow() - t.created_at).total_seconds() / 3600)
            category_label = CATEGORY_LABELS.get(t.category, t.category)
            lines.append(f"- **{t.ticket_number}** — {category_label}: {t.subject} "
                        f"(submitted {age_hours}h ago by {t.submitter_name}, status: {t.status})")

        lines.extend(["", "Please review these tickets from the Scout Support dashboard.", "", "— Scout Genius"])

        self._send_platform_email(
            to_email=DEFAULT_ADMIN_EMAIL,
            subject=f"[Scout Genius] {len(stale_tickets)} Stale Platform Ticket(s) — Action Required",
            body="\n".join(lines),
            email_type='platform_escalation',
        )

        logger.info(f"⏰ Escalation: {len(stale_tickets)} stale platform tickets notified to admin")
        return len(stale_tickets)

    def find_ticket_by_email_subject(self, subject: str) -> Optional['SupportTicket']:
        from models import SupportTicket

        match = re.search(r'\[?(SS-\d{4}-\d{4})\]?', subject)
        if match:
            ticket_number = match.group(1)
            return SupportTicket.query.filter_by(ticket_number=ticket_number).first()
        return None
