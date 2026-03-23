"""
Scout Support Service — AI-powered internal ATS support ticket resolution.

Handles the full lifecycle of support tickets:
1. Intake: AI reads ticket + attachments, generates understanding summary
2. Clarification: Back-and-forth email conversation with user
3. Solution proposal: AI proposes fix, gets user approval
4. Admin approval: Summary sent to admin for final authorization
5. Execution: Bullhorn API actions with full audit trail
6. Completion: Proof sent to user + admin
"""
import os
import re
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, Optional, List, Any

from openai import OpenAI

logger = logging.getLogger(__name__)

SCOUT_SUPPORT_EMAIL = 'support@scoutgenius.ai'
SCOUT_SUPPORT_NAME = 'Scout Support'
DEFAULT_ADMIN_EMAIL = 'kroots@myticas.com'

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
}

AI_FULL_CATEGORIES = ['ats_issue', 'candidate_parsing', 'job_posting', 'account_access', 'data_correction']

HANDOFF_CATEGORIES = ['email_notifications', 'feature_request', 'other']

BACKOFFICE_CATEGORIES = ['backoffice_onboarding', 'backoffice_finance']


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


class ScoutSupportService:

    def __init__(self, bullhorn_service=None):
        self.bullhorn_service = bullhorn_service
        api_key = os.environ.get('OPENAI_API_KEY')
        self.openai_client = OpenAI(api_key=api_key) if api_key else None
        logger.info("Scout Support Service initialized")

    def create_ticket(self, category: str, subject: str, description: str,
                      submitter_name: str, submitter_email: str,
                      submitter_department: str = '', brand: str = 'Myticas',
                      priority: str = 'medium',
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

        if attachment_info:
            ticket.description += f"\n\n[Attachments: {', '.join(a.get('filename', 'unknown') for a in attachment_info)}]"

        db.session.commit()
        logger.info(f"📋 Created support ticket {ticket_number} from {submitter_email}: {subject}")
        return ticket

    def process_new_ticket(self, ticket_id: int) -> bool:
        from extensions import db
        from models import SupportTicket

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            logger.error(f"Ticket {ticket_id} not found")
            return False

        if ticket.category in HANDOFF_CATEGORIES:
            return self._process_handoff_ticket(ticket)
        elif ticket.category in BACKOFFICE_CATEGORIES:
            return self._process_backoffice_ticket(ticket)
        else:
            return self._process_ai_full_ticket(ticket)

    def _process_ai_full_ticket(self, ticket) -> bool:
        from extensions import db

        understanding = self._generate_understanding(ticket)
        if not understanding:
            logger.error(f"Failed to generate AI understanding for ticket {ticket.ticket_number}")
            return False

        ticket.ai_understanding = understanding
        ticket.status = 'acknowledged'
        db.session.commit()

        self._send_acknowledgment_email(ticket, understanding)
        logger.info(f"✅ Ticket {ticket.ticket_number} acknowledged (AI full), email sent to {ticket.submitter_email}")
        return True

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

        logger.info(f"✅ Ticket {ticket.ticket_number} handed off (category={ticket.category}), CC'd {DEFAULT_ADMIN_EMAIL}")
        return True

    def _process_backoffice_ticket(self, ticket) -> bool:
        from extensions import db

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)
        cc_list = get_backoffice_cc(ticket.category, ticket.submitter_department, ticket.brand)

        ticket.status = 'escalated'
        ticket.escalation_reason = f"Back-office category '{category_label}' routed to designated contacts."
        db.session.commit()

        cc_names = ', '.join(cc_list)
        body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Thank you for reaching out. Your support ticket has been received and assigned ticket number **{ticket.ticket_number}**.\n\n"
            f"**Category:** {category_label}\n"
            f"**Department:** {ticket.submitter_department or 'Not specified'}\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"I've documented your request and have copied in the designated back-office team who will handle this directly. "
            f"You can expect a follow-up from them shortly.\n\n"
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

        logger.info(f"✅ Ticket {ticket.ticket_number} routed to back-office (category={ticket.category}, dept={ticket.submitter_department}), CC'd {cc_names}")
        return True

    def handle_user_reply(self, ticket_id: int, reply_body: str, message_id: str = '') -> bool:
        from extensions import db
        from models import SupportTicket, SupportConversation

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            logger.error(f"Ticket {ticket_id} not found for reply handling")
            return False

        conv = SupportConversation(
            ticket_id=ticket.id,
            direction='inbound',
            sender_email=ticket.submitter_email,
            recipient_email=SCOUT_SUPPORT_EMAIL,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=reply_body,
            message_id=message_id,
            email_type='user_reply',
        )
        db.session.add(conv)
        ticket.last_message_id = message_id
        db.session.commit()

        if ticket.status == 'awaiting_user_approval':
            return self._handle_user_approval_response(ticket, reply_body)
        elif ticket.status in ('acknowledged', 'clarifying'):
            return self._handle_clarification_reply(ticket, reply_body)

        return True

    def handle_admin_reply(self, ticket_id: int, reply_body: str, message_id: str = '') -> bool:
        from extensions import db
        from models import SupportTicket, SupportConversation

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket or ticket.status != 'awaiting_admin_approval':
            return False

        conv = SupportConversation(
            ticket_id=ticket.id,
            direction='inbound',
            sender_email=ticket.admin_email,
            recipient_email=SCOUT_SUPPORT_EMAIL,
            subject=f"Re: [{ticket.ticket_number}] Admin Approval",
            body=reply_body,
            message_id=message_id,
            email_type='admin_reply',
        )
        db.session.add(conv)

        decision = self._classify_admin_response(reply_body)

        if decision == 'approved':
            ticket.status = 'approved'
            ticket.admin_approved_at = datetime.utcnow()
            ticket.admin_response = reply_body
            db.session.commit()
            logger.info(f"✅ Admin approved ticket {ticket.ticket_number}")
            self._execute_solution(ticket)
            return True
        elif decision == 'hold':
            ticket.status = 'on_hold'
            ticket.admin_response = reply_body
            db.session.commit()
            self._send_status_email(ticket, 'on_hold')
            logger.info(f"⏸️ Admin placed ticket {ticket.ticket_number} on hold")
            return True
        else:
            ticket.status = 'closed'
            ticket.admin_response = reply_body
            ticket.resolved_at = datetime.utcnow()
            db.session.commit()
            self._send_status_email(ticket, 'closed')
            logger.info(f"❌ Admin closed ticket {ticket.ticket_number}")
            return True

    def _generate_understanding(self, ticket) -> Optional[str]:
        if not self.openai_client:
            return None

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)

        prompt = f"""You are Scout Support, an AI assistant for internal ATS (Bullhorn) support issues.

A user has submitted a support ticket. Analyze the issue and provide a clear, concise summary of your understanding.

Ticket Details:
- Category: {category_label}
- Subject: {ticket.subject}
- Priority: {ticket.priority}
- Submitted by: {ticket.submitter_name} ({ticket.submitter_email})
- Department: {ticket.submitter_department or 'Not specified'}

User's Description:
{ticket.description}

Respond with a JSON object:
{{
    "understanding": "A clear summary of what the user's issue is, written back to the user for confirmation",
    "clarification_needed": true/false,
    "clarification_questions": ["question 1", "question 2"],
    "can_resolve_autonomously": true/false,
    "resolution_approach": "Brief description of how Scout Support could resolve this",
    "requires_bullhorn_api": true/false,
    "affected_entities": ["candidate", "job", "placement", etc.]
}}"""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[
                    {'role': 'system', 'content': 'You are Scout Support, an expert AI assistant for Bullhorn ATS issues. You help internal users resolve their ATS problems. Respond only in valid JSON.'},
                    {'role': 'user', 'content': prompt}
                ],
                max_completion_tokens=2000,
                response_format={'type': 'json_object'},
            )
            content = response.choices[0].message.content.strip()
            parsed = json.loads(content)
            return json.dumps(parsed)
        except Exception as e:
            logger.error(f"AI understanding generation failed: {e}")
            return None

    def _handle_clarification_reply(self, ticket, reply_body: str) -> bool:
        from extensions import db

        analysis = self._analyze_clarification(ticket, reply_body)
        if not analysis:
            return False

        try:
            parsed = json.loads(analysis)
        except (json.JSONDecodeError, TypeError):
            parsed = {'fully_understood': False}

        if parsed.get('fully_understood', False):
            ticket.ai_understanding = json.dumps(parsed)
            solution = parsed.get('proposed_solution', '')
            if solution:
                ticket.proposed_solution = solution
                ticket.status = 'awaiting_user_approval'
                db.session.commit()
                self._send_solution_proposal_email(ticket, solution)
            else:
                ticket.status = 'clarifying'
                db.session.commit()
                self._send_clarification_email(ticket, parsed.get('follow_up', ''))
        else:
            ticket.status = 'clarifying'
            db.session.commit()
            follow_up = parsed.get('follow_up', 'Could you provide more details about the issue?')
            self._send_clarification_email(ticket, follow_up)

        return True

    def _analyze_clarification(self, ticket, reply_body: str) -> Optional[str]:
        if not self.openai_client:
            return None

        from extensions import db as _db
        conversations = ticket.conversations.order_by(
            _db.text('created_at ASC')
        ).all()

        history = []
        for conv in conversations:
            history.append(f"[{conv.direction.upper()}] {conv.sender_email}: {conv.body[:500]}")

        prompt = f"""You are Scout Support. You've been working on ticket {ticket.ticket_number}.

Original issue: {ticket.subject}
Category: {CATEGORY_LABELS.get(ticket.category, ticket.category)}

Conversation history:
{chr(10).join(history)}

Latest reply from user:
{reply_body}

Current AI understanding: {ticket.ai_understanding or 'Not yet established'}

Analyze whether you now fully understand the issue and can propose a solution.

Respond with JSON:
{{
    "fully_understood": true/false,
    "updated_understanding": "Your current understanding of the full issue",
    "proposed_solution": "If fully understood, describe the exact steps to resolve this. If it requires Bullhorn API changes, specify the entity type, entity ID, fields, and values.",
    "follow_up": "If not fully understood, your next clarification question to the user",
    "can_execute": true/false,
    "execution_steps": ["step 1", "step 2"]
}}"""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[
                    {'role': 'system', 'content': 'You are Scout Support, an expert AI assistant for Bullhorn ATS issues. Respond only in valid JSON.'},
                    {'role': 'user', 'content': prompt}
                ],
                max_completion_tokens=2000,
                response_format={'type': 'json_object'},
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Clarification analysis failed: {e}")
            return None

    def _handle_user_approval_response(self, ticket, reply_body: str) -> bool:
        from extensions import db

        approval = self._classify_user_response(reply_body)

        if approval == 'approved':
            ticket.status = 'awaiting_admin_approval'
            ticket.user_approved_at = datetime.utcnow()
            db.session.commit()
            self._send_admin_approval_request(ticket)
            self._send_user_confirmation_email(ticket, 'Your approval has been received. The proposed solution has been forwarded to the administrator for final authorization.')
            logger.info(f"👤 User approved ticket {ticket.ticket_number}, forwarding to admin")
            return True
        elif approval == 'needs_changes':
            ticket.status = 'clarifying'
            db.session.commit()
            self._send_clarification_email(ticket, "I understand you'd like some changes to the proposed solution. Could you describe what adjustments you'd like me to make?")
            return True
        else:
            ticket.status = 'closed'
            ticket.resolved_at = datetime.utcnow()
            db.session.commit()
            self._send_status_email(ticket, 'closed')
            return True

    def _classify_user_response(self, text: str) -> str:
        text_lower = text.lower().strip()
        approve_keywords = ['yes', 'approve', 'confirmed', 'go ahead', 'proceed', 'looks good', 'agree', 'correct', 'that works']
        reject_keywords = ['no', 'cancel', 'close', 'reject', 'decline', 'stop', 'nevermind']
        change_keywords = ['change', 'modify', 'adjust', 'update', 'instead', 'actually', 'but', 'however']

        if any(kw in text_lower for kw in approve_keywords):
            return 'approved'
        if any(kw in text_lower for kw in reject_keywords):
            return 'rejected'
        if any(kw in text_lower for kw in change_keywords):
            return 'needs_changes'
        return 'needs_changes'

    def _classify_admin_response(self, text: str) -> str:
        text_lower = text.lower().strip()
        approve_keywords = ['approved', 'approve', 'yes', 'go ahead', 'proceed', 'authorized', 'green light']
        hold_keywords = ['hold', 'wait', 'pause', 'defer', 'later']

        if any(kw in text_lower for kw in approve_keywords):
            return 'approved'
        if any(kw in text_lower for kw in hold_keywords):
            return 'hold'
        return 'closed'

    def _execute_solution(self, ticket) -> bool:
        from extensions import db
        from models import SupportAction

        logger.info(f"🔧 Executing solution for ticket {ticket.ticket_number}")

        try:
            understanding = json.loads(ticket.ai_understanding) if ticket.ai_understanding else {}
        except (json.JSONDecodeError, TypeError):
            understanding = {}

        try:
            solution_data = json.loads(ticket.proposed_solution) if ticket.proposed_solution else {}
        except (json.JSONDecodeError, TypeError):
            solution_data = {'description': ticket.proposed_solution}

        execution_steps = solution_data.get('execution_steps', [])
        requires_bullhorn = understanding.get('requires_bullhorn_api', False)

        if requires_bullhorn and self.bullhorn_service:
            proof_items = self._execute_bullhorn_actions(ticket, solution_data)
        else:
            proof_items = [{'step': 'Manual guidance provided', 'result': 'User-side resolution'}]

        proof_summary = json.dumps(proof_items, indent=2)
        ticket.execution_proof = proof_summary
        ticket.status = 'completed'
        ticket.resolved_at = datetime.utcnow()
        db.session.commit()

        self._send_completion_email(ticket, proof_items)
        logger.info(f"✅ Ticket {ticket.ticket_number} completed successfully")
        return True

    def _execute_bullhorn_actions(self, ticket, solution_data: dict) -> List[Dict]:
        from extensions import db
        from models import SupportAction

        proof_items = []
        steps = solution_data.get('execution_steps', [])

        for step in steps:
            action = SupportAction(
                ticket_id=ticket.id,
                action_type=step.get('action', 'unknown'),
                entity_type=step.get('entity_type'),
                entity_id=step.get('entity_id'),
                field_name=step.get('field'),
                old_value=step.get('old_value'),
                new_value=step.get('new_value'),
                summary=step.get('description', ''),
            )

            try:
                if self.bullhorn_service and step.get('action') == 'update_entity':
                    entity_type = step.get('entity_type', 'Candidate')
                    entity_id = step.get('entity_id')
                    field = step.get('field')
                    new_value = step.get('new_value')

                    if entity_id and field:
                        current = self.bullhorn_service.get_entity(entity_type, entity_id, fields=field)
                        if current:
                            action.old_value = str(current.get(field, ''))

                        self.bullhorn_service.update_entity(entity_type, entity_id, {field: new_value})
                        action.success = True
                        proof_items.append({
                            'step': f"Updated {entity_type} #{entity_id}: {field}",
                            'old_value': action.old_value,
                            'new_value': str(new_value),
                            'result': 'Success'
                        })
                    else:
                        action.success = False
                        action.error_message = 'Missing entity_id or field'
                        proof_items.append({'step': step.get('description', 'Unknown'), 'result': 'Failed — missing data'})
                else:
                    action.success = True
                    action.summary = step.get('description', 'Guidance step')
                    proof_items.append({'step': step.get('description', 'Step completed'), 'result': 'Guidance provided'})

            except Exception as e:
                action.success = False
                action.error_message = str(e)
                proof_items.append({'step': step.get('description', 'Unknown'), 'result': f'Failed: {str(e)}'})
                logger.error(f"Bullhorn action failed for ticket {ticket.ticket_number}: {e}")

            db.session.add(action)

        db.session.commit()
        return proof_items

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

    def find_ticket_by_email_subject(self, subject: str) -> Optional['SupportTicket']:
        from models import SupportTicket

        match = re.search(r'\[?(SS-\d{4}-\d{4})\]?', subject)
        if match:
            ticket_number = match.group(1)
            return SupportTicket.query.filter_by(ticket_number=ticket_number).first()
        return None

    def _send_acknowledgment_email(self, ticket, understanding_json: str):
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
            f"",
            f"**My Understanding of Your Issue:**",
            f"{summary}",
        ]

        if clarification_needed and questions:
            body_parts.append("")
            body_parts.append("Before I can proceed, I need a few clarifications:")
            for i, q in enumerate(questions, 1):
                body_parts.append(f"{i}. {q}")
            body_parts.append("")
            body_parts.append("Please reply to this email with your answers so I can move forward with resolving your issue.")
            ticket.status = 'clarifying'
        else:
            proposed = understanding.get('resolution_approach', '')
            if proposed:
                body_parts.append("")
                body_parts.append("**Proposed Resolution:**")
                body_parts.append(proposed)
                body_parts.append("")
                body_parts.append("If this looks correct, please reply with **\"Yes, go ahead\"** to approve, or let me know if anything needs to be adjusted.")
                ticket.status = 'awaiting_user_approval'
                ticket.proposed_solution = json.dumps({
                    'description': proposed,
                    'can_execute': understanding.get('can_resolve_autonomously', False),
                    'requires_bullhorn': understanding.get('requires_bullhorn_api', False),
                    'affected_entities': understanding.get('affected_entities', []),
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
        )

    def _send_clarification_email(self, ticket, follow_up_text: str):
        body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Regarding your ticket **{ticket.ticket_number}**:\n\n"
            f"{follow_up_text}\n\n"
            f"Please reply to this email with the additional details so I can move forward.\n\n"
            f"— Scout Support"
        )
        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=body,
            ticket=ticket,
            email_type='clarification',
        )

    def _send_solution_proposal_email(self, ticket, solution_text: str):
        body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"After reviewing your issue ({ticket.ticket_number}), here is my proposed solution:\n\n"
            f"**Proposed Fix:**\n{solution_text}\n\n"
            f"If this looks correct, please reply with **\"Yes, go ahead\"** to approve.\n"
            f"If you'd like any changes, just let me know what adjustments are needed.\n\n"
            f"— Scout Support"
        )
        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] Solution Proposed",
            body=body,
            ticket=ticket,
            email_type='solution_proposal',
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
            subject=f"Re: [{ticket.ticket_number}] Approval Received",
            body=body,
            ticket=ticket,
            email_type='user_confirmation',
        )

    def _send_admin_approval_request(self, ticket):
        try:
            understanding = json.loads(ticket.ai_understanding) if ticket.ai_understanding else {}
        except (json.JSONDecodeError, TypeError):
            understanding = {}

        try:
            solution = json.loads(ticket.proposed_solution) if ticket.proposed_solution else {}
        except (json.JSONDecodeError, TypeError):
            solution = {'description': ticket.proposed_solution or 'N/A'}

        body = (
            f"Hi,\n\n"
            f"A support ticket requires your approval before Scout Support can execute the fix.\n\n"
            f"**Ticket:** {ticket.ticket_number}\n"
            f"**Category:** {CATEGORY_LABELS.get(ticket.category, ticket.category)}\n"
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})\n"
            f"**Priority:** {ticket.priority.upper()}\n\n"
            f"**Issue Summary:**\n{understanding.get('understanding', ticket.description)}\n\n"
            f"**Proposed Solution:**\n{solution.get('description', 'N/A')}\n\n"
            f"**User has approved this solution.**\n\n"
            f"Please reply with one of:\n"
            f"- **\"Approved\"** — Scout Support will execute the fix\n"
            f"- **\"Hold\"** — Place this ticket on hold\n"
            f"- **\"Close\"** — Close this ticket without action\n\n"
            f"— Scout Support"
        )
        self._send_email(
            to_email=ticket.admin_email,
            subject=f"[{ticket.ticket_number}] Admin Approval Required",
            body=body,
            ticket=ticket,
            email_type='admin_approval_request',
        )

    def _send_completion_email(self, ticket, proof_items: List[Dict]):
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

        user_body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Great news — your support ticket **{ticket.ticket_number}** has been resolved.\n\n"
            f"**What was done:**\n{proof_text}\n\n"
            f"If you have any further issues, feel free to submit a new support ticket.\n\n"
            f"— Scout Support"
        )
        self._send_email(
            to_email=ticket.submitter_email,
            subject=f"[{ticket.ticket_number}] Issue Resolved ✅",
            body=user_body,
            ticket=ticket,
            email_type='completion_user',
        )

        admin_body = (
            f"Ticket **{ticket.ticket_number}** has been completed.\n\n"
            f"**Submitted by:** {ticket.submitter_name} ({ticket.submitter_email})\n"
            f"**Category:** {CATEGORY_LABELS.get(ticket.category, ticket.category)}\n\n"
            f"**Execution Proof:**\n{proof_text}\n\n"
            f"— Scout Support"
        )
        self._send_email(
            to_email=ticket.admin_email,
            subject=f"[{ticket.ticket_number}] Completed ✅",
            body=admin_body,
            ticket=ticket,
            email_type='completion_admin',
        )

    def _send_escalation_email(self, ticket, reason: str):
        user_body = (
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},\n\n"
            f"Regarding your ticket **{ticket.ticket_number}** — after reviewing your issue, "
            f"I've determined that this requires human intervention and have escalated it to the support team.\n\n"
            f"**Reason for escalation:** {reason}\n\n"
            f"Someone from the team will follow up with you directly.\n\n"
            f"— Scout Support"
        )
        self._send_email(
            to_email=ticket.submitter_email,
            cc_email=ticket.admin_email,
            subject=f"[{ticket.ticket_number}] Escalated to Support Team",
            body=user_body,
            ticket=ticket,
            email_type='escalation',
        )

    def _send_status_email(self, ticket, new_status: str):
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
            subject=f"[{ticket.ticket_number}] Status Update",
            body=body,
            ticket=ticket,
            email_type='status_update',
        )

    def _send_email(self, to_email: str, subject: str, body: str, ticket=None,
                    email_type: str = 'general', cc_email: str = None,
                    cc_emails: Optional[List[str]] = None):
        from extensions import db
        from models import SupportConversation, EmailDeliveryLog

        try:
            from email_service import EmailService
            email_svc = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)

            msg_id = f"<scout-support-{uuid.uuid4().hex[:12]}@scoutgenius.ai>"

            headers = {'Reply-To': SCOUT_SUPPORT_EMAIL}
            if ticket and ticket.last_message_id:
                headers['In-Reply-To'] = ticket.last_message_id
                headers['References'] = ticket.last_message_id

            if cc_emails:
                cc_list = list(cc_emails)
            elif cc_email:
                cc_list = [cc_email]
            else:
                cc_list = []

            success = email_svc.send_email(
                to_email=to_email,
                subject=subject,
                body=body,
                from_email=SCOUT_SUPPORT_EMAIL,
                from_name=SCOUT_SUPPORT_NAME,
                cc=cc_list,
                headers=headers,
            )

            if ticket:
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
                ticket.last_message_id = msg_id
                db.session.commit()

            if success:
                logger.info(f"📧 Scout Support email sent: {subject} → {to_email}")
            else:
                logger.error(f"❌ Scout Support email failed: {subject} → {to_email}")

        except Exception as e:
            logger.error(f"❌ Scout Support email error: {e}")
