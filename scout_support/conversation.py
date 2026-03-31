"""
Conversation — Reply handling, classification, approval flow, admin Q&A.

Contains:
- handle_user_reply: Process inbound user email replies
- handle_admin_reply: Process inbound admin email replies
- _handle_clarification_reply: AI-driven clarification loop
- _handle_user_approval_response: User approval/rejection handling
- _handle_admin_question: Admin Q&A during approval stage
- _strip_quoted_text: Remove quoted email text from replies
- _classify_user_response: AI + keyword user intent classification
- _classify_admin_response: AI + keyword admin intent classification
- _ai_classify_response: GPT-based response classification
- _keyword_classify_user: Keyword fallback for user classification
- _keyword_classify_admin: Keyword fallback for admin classification
- _refine_execution_with_admin_instructions: Merge admin instructions into execution plan
- _classify_admin_handling_intent: Detect if admin reply is an AI instruction vs direct user message
- _generate_admin_draft: Generate AI-drafted content based on admin instructions
"""

import re
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


class ConversationMixin:
    """Reply handling, classification, approval flow, and admin Q&A."""

    MAX_CLARIFICATION_ROUNDS = 3

    def _is_platform_ticket(self, ticket) -> bool:
        from scout_support_service import PLATFORM_CATEGORIES
        return ticket.category in PLATFORM_CATEGORIES

    def handle_user_reply(self, ticket_id: int, reply_body: str, message_id: str = '',
                          attachment_data: Optional[List[Dict]] = None) -> bool:
        from extensions import db
        from models import SupportTicket, SupportConversation
        from scout_support_service import SCOUT_SUPPORT_EMAIL

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            logger.error(f"Ticket {ticket_id} not found for reply handling")
            return False

        if message_id:
            existing = SupportConversation.query.filter_by(
                ticket_id=ticket_id, message_id=message_id
            ).first()
            if existing:
                logger.info(f"⏭️ Duplicate inbound message_id {message_id} for ticket {ticket.ticket_number} — skipping")
                return True

        attachment_note = ''
        if attachment_data:
            filenames = [a.get('filename', 'unknown') for a in attachment_data]
            attachment_note = f"\n\n[Attachments: {', '.join(filenames)}]"

        conv = SupportConversation(
            ticket_id=ticket.id,
            direction='inbound',
            sender_email=ticket.submitter_email,
            recipient_email=SCOUT_SUPPORT_EMAIL,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=reply_body + attachment_note,
            message_id=message_id,
            email_type='user_reply',
        )
        db.session.add(conv)
        if message_id:
            ticket.last_message_id = message_id
        db.session.commit()

        attachment_content = ''
        attachment_ack = ''
        if attachment_data:
            attachment_content = self._extract_attachment_content(attachment_data)
            attachment_ack = self._build_attachment_acknowledgment()
            if attachment_content:
                logger.info(f"📎 Extracted {len(attachment_content)} chars from {len(attachment_data)} attachment(s) on reply to {ticket.ticket_number}")

        if self._is_platform_ticket(ticket):
            return self._handle_platform_reply(ticket, reply_body, attachment_content=attachment_content)

        if ticket.status in ('completed', 'closed'):
            return self._handle_reopened_ticket(ticket, reply_body, attachment_content=attachment_content)
        elif ticket.status == 'admin_handling':
            return self._handle_user_reply_to_admin(ticket, reply_body)
        elif ticket.status == 'awaiting_user_approval':
            return self._handle_user_approval_response(ticket, reply_body)
        elif ticket.status in ('acknowledged', 'clarifying'):
            return self._handle_clarification_reply(ticket, reply_body, attachment_content=attachment_content, attachment_ack=attachment_ack)

        return True

    def handle_admin_reply(self, ticket_id: int, reply_body: str, message_id: str = '') -> bool:
        from extensions import db
        from models import SupportTicket, SupportConversation
        from scout_support_service import SCOUT_SUPPORT_EMAIL

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            return False

        if message_id:
            existing = SupportConversation.query.filter_by(
                ticket_id=ticket_id, message_id=message_id
            ).first()
            if existing:
                logger.info(f"⏭️ Duplicate admin inbound message_id {message_id} for ticket {ticket.ticket_number} — skipping")
                return True

        if self._is_platform_ticket(ticket):
            logger.warning(f"Admin reply ignored for platform ticket {ticket.ticket_number} — platform tickets do not use approval flow")
            return False

        if ticket.status not in ('awaiting_admin_approval', 'admin_clarifying', 'admin_handling', 'escalated'):
            return False

        if ticket.status == 'escalated':
            ticket.status = 'admin_handling'
            db.session.commit()
            logger.info(f"📋 Ticket {ticket.ticket_number} transitioned from escalated → admin_handling on admin reply")

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
        if message_id:
            ticket.last_message_id = message_id
        db.session.commit()

        if ticket.status == 'admin_handling':
            fresh_text = self._strip_quoted_text(reply_body)
            if fresh_text and fresh_text.strip():
                intent = self._classify_admin_handling_intent(fresh_text)
                if intent == 'ai_instruction':
                    logger.info(f"🤖 Admin AI instruction detected on ticket {ticket.ticket_number}")
                    conv.email_type = 'admin_ai_instruction'
                    db.session.commit()
                    self._generate_admin_draft(ticket, fresh_text)
                else:
                    from scout_support_service import ScoutSupportService
                    svc = ScoutSupportService()
                    svc.reply_to_ticket(ticket.id, fresh_text, ticket.admin_email)
                    logger.info(f"💬 Admin direct reply forwarded to user for ticket {ticket.ticket_number}")
            return True

        decision = self._classify_admin_response(reply_body)

        if decision == 'approved':
            ticket.status = 'approved'
            ticket.admin_approved_at = datetime.utcnow()
            ticket.admin_response = reply_body
            db.session.commit()
            logger.info(f"✅ Admin approved ticket {ticket.ticket_number}")

            fresh_text = self._strip_quoted_text(reply_body)
            if fresh_text and len(fresh_text.split()) > 5:
                self._refine_execution_with_admin_instructions(ticket, fresh_text)

            self._execute_solution(ticket)
            return True
        elif decision == 'hold':
            ticket.status = 'on_hold'
            ticket.admin_response = reply_body
            db.session.commit()
            self._send_status_email(ticket, 'on_hold')
            logger.info(f"⏸️ Admin placed ticket {ticket.ticket_number} on hold")
            return True
        elif decision == 'admin_question':
            ticket.status = 'admin_clarifying'
            db.session.commit()
            self._handle_admin_question(ticket, reply_body)
            logger.info(f"💬 Admin asked a question on ticket {ticket.ticket_number} — Scout Support responding")
            return True
        else:
            ticket.status = 'closed'
            ticket.admin_response = reply_body
            ticket.resolved_at = datetime.utcnow()
            db.session.commit()
            self._send_status_email(ticket, 'closed')
            logger.info(f"❌ Admin closed ticket {ticket.ticket_number}")
            return True

    def _handle_user_reply_to_admin(self, ticket, reply_body: str) -> bool:
        from extensions import db

        fresh_text = self._strip_quoted_text(reply_body)
        if not fresh_text or not fresh_text.strip():
            return True

        admin_email = ticket.admin_email
        if not admin_email:
            logger.warning(f"No admin email for ticket {ticket.ticket_number} in admin_handling state")
            return True

        admin_body = (
            f"**User Reply on Ticket {ticket.ticket_number}:**\n\n"
            f"**From:** {ticket.submitter_name} ({ticket.submitter_email})\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"{fresh_text}\n\n"
            f"You can reply directly to continue the conversation with the user.\n\n"
            f"— Scout Support"
        )

        self._send_email(
            to_email=admin_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=admin_body,
            ticket=ticket,
            email_type='user_reply_forwarded',
        )

        logger.info(f"📨 User reply on admin_handling ticket {ticket.ticket_number} forwarded to admin {admin_email}")
        return True

    def _handle_reopened_ticket(self, ticket, reply_body: str, attachment_content: str = '') -> bool:
        from extensions import db
        from models import SupportConversation

        fresh_text = self._strip_quoted_text(reply_body)
        if not fresh_text or not fresh_text.strip():
            return True

        escalation_keywords = [
            'escalate', 'speak to someone', 'talk to someone', 'human',
            'real person', 'manager', 'supervisor', 'admin', 'administrator'
        ]
        wants_escalation = any(kw in fresh_text.lower() for kw in escalation_keywords)

        previous_status = ticket.status
        ticket.status = 'clarifying'
        ticket.resolved_at = None
        db.session.commit()
        logger.info(f"🔄 Ticket {ticket.ticket_number} reopened from '{previous_status}' by user reply")

        if wants_escalation:
            ticket.status = 'awaiting_admin_approval'
            db.session.commit()

            self._send_user_confirmation_email(
                ticket,
                f'Your ticket has been reopened and escalated to the administrator as requested. '
                f'You will be notified once it has been reviewed.'
            )

            admin_body = (
                f"**Reopened Ticket — User Requested Escalation**\n\n"
                f"Ticket {ticket.ticket_number} was previously {previous_status} but the user has replied "
                f"and requested to speak with an administrator.\n\n"
                f"**User's message:**\n{fresh_text}\n\n"
                f"Please reply with one of:\n"
                f'- **"Approved"** — Retry the automated execution\n'
                f'- **"Hold"** — Place this ticket on hold\n'
                f'- **"Close"** — Close this ticket with a manual resolution note\n\n'
                f"Or reply directly to respond to the user."
            )
            self._send_admin_approval_request_custom(ticket, admin_body)
            logger.info(f"🔼 Reopened ticket {ticket.ticket_number} escalated directly to admin per user request")
            return True

        full_history = self._build_reopen_context(ticket)

        reopen_analysis = self._analyze_reopened_ticket(ticket, fresh_text, full_history, attachment_content)

        if not reopen_analysis:
            logger.warning(f"⚠️ Reopen analysis failed for {ticket.ticket_number} — escalating to admin")
            ticket.status = 'awaiting_admin_approval'
            db.session.commit()
            self._send_user_confirmation_email(
                ticket,
                f'Your ticket has been reopened and forwarded to the administrator for review. '
                f'You will be notified once it has been addressed.'
            )
            admin_body = (
                f"**Reopened Ticket — Needs Admin Review**\n\n"
                f"Ticket {ticket.ticket_number} was previously {previous_status} but the user has replied.\n\n"
                f"**User's new message:**\n{fresh_text}\n\n"
                f"**Note:** AI analysis could not be completed for this reopened ticket.\n\n"
                f"Please reply with one of:\n"
                f'- **"Approved"** — Retry the automated execution\n'
                f'- **"Hold"** — Place this ticket on hold\n'
                f'- **"Close"** — Close this ticket with a manual resolution note\n\n'
                f"Or reply directly to respond to the user."
            )
            self._send_admin_approval_request_custom(ticket, admin_body)
            return True

        try:
            analysis_data = json.loads(reopen_analysis)
        except (json.JSONDecodeError, TypeError):
            analysis_data = {}

        can_handle = analysis_data.get('can_handle_directly', False)
        ai_response = analysis_data.get('response_to_user', '')
        needs_new_solution = analysis_data.get('needs_new_solution', False)
        new_solution = analysis_data.get('proposed_solution', None)

        if can_handle and ai_response and not needs_new_solution:
            self._send_user_confirmation_email(ticket, ai_response)
            ticket.status = 'clarifying'
            db.session.commit()
            logger.info(f"✅ AI handled reopened ticket {ticket.ticket_number} directly")
            return True
        elif can_handle and needs_new_solution and new_solution:
            ticket.ai_understanding = analysis_data.get('updated_understanding', ticket.ai_understanding)
            ticket.proposed_solution = json.dumps(new_solution) if isinstance(new_solution, dict) else new_solution
            ticket.status = 'awaiting_user_approval'
            db.session.commit()

            self._send_solution_proposal(ticket, new_solution if isinstance(new_solution, dict) else {})
            logger.info(f"🔧 AI proposed new solution for reopened ticket {ticket.ticket_number}")
            return True
        else:
            ticket.status = 'awaiting_admin_approval'
            db.session.commit()

            self._send_user_confirmation_email(
                ticket,
                f'Your ticket has been reopened. I\'ve reviewed the full history and this needs to be reviewed '
                f'by the administrator. You will be notified once it has been addressed.'
            )

            escalation_reason = analysis_data.get('escalation_reason', 'AI determined this requires manual intervention')
            admin_body = (
                f"**Reopened Ticket — Needs Admin Review**\n\n"
                f"Ticket {ticket.ticket_number} was previously {previous_status} but the user has replied.\n\n"
                f"**User's new message:**\n{fresh_text}\n\n"
                f"**AI Assessment:**\n{escalation_reason}\n\n"
                f"Please reply with one of:\n"
                f'- **"Approved"** — Retry the automated execution\n'
                f'- **"Hold"** — Place this ticket on hold\n'
                f'- **"Close"** — Close this ticket with a manual resolution note\n\n'
                f"Or reply directly to respond to the user."
            )
            self._send_admin_approval_request_custom(ticket, admin_body)
            logger.info(f"🔼 Reopened ticket {ticket.ticket_number} escalated to admin after AI assessment")
            return True

    def _build_reopen_context(self, ticket) -> str:
        from models import SupportConversation, SupportAction

        conversations = SupportConversation.query.filter_by(
            ticket_id=ticket.id
        ).order_by(SupportConversation.created_at).all()

        actions = SupportAction.query.filter_by(
            ticket_id=ticket.id
        ).order_by(SupportAction.executed_at).all()

        context_parts = []
        context_parts.append(f"Original Issue: {ticket.subject}")
        context_parts.append(f"Description: {ticket.description or 'N/A'}")
        context_parts.append(f"AI Understanding: {ticket.ai_understanding or 'N/A'}")
        context_parts.append(f"Resolution Note: {ticket.resolution_note or 'N/A'}")

        if conversations:
            context_parts.append("\n--- Conversation History ---")
            for conv in conversations:
                direction = "USER" if conv.direction == 'inbound' else "SCOUT SUPPORT"
                if conv.email_type == 'admin_direct_reply':
                    direction = "ADMIN"
                elif conv.email_type == 'admin_reply':
                    direction = "ADMIN"
                context_parts.append(f"[{direction}] {conv.body[:500]}")

        if actions:
            context_parts.append("\n--- Execution Actions ---")
            for action in actions:
                status = "SUCCESS" if action.success else "FAILED"
                context_parts.append(f"[{status}] {action.action_type} on {action.entity_type} #{action.entity_id}: {action.field_name or ''}")

        if ticket.execution_history:
            try:
                history = json.loads(ticket.execution_history)
                if history:
                    context_parts.append(f"\n--- Execution History ({len(history)} attempt(s)) ---")
                    for attempt in history:
                        context_parts.append(f"Attempt {attempt.get('attempt', '?')}: {json.dumps(attempt.get('proof', []))[:300]}")
            except (json.JSONDecodeError, TypeError):
                pass

        return "\n".join(context_parts)

    def _analyze_reopened_ticket(self, ticket, user_message: str, full_history: str, attachment_content: str = '') -> Optional[str]:
        try:
            client = OpenAI()

            prompt = (
                f"A previously resolved/closed support ticket has been reopened by the user.\n\n"
                f"FULL TICKET HISTORY:\n{full_history[:6000]}\n\n"
                f"USER'S NEW MESSAGE:\n{user_message}\n\n"
            )
            if attachment_content:
                prompt += f"ATTACHMENT CONTENT:\n{attachment_content[:2000]}\n\n"

            prompt += (
                f"Based on the full history (including what was tried, what failed, what the admin resolved, "
                f"and the resolution note), determine:\n"
                f"1. Can you handle this new request directly with Bullhorn API actions?\n"
                f"2. Is this a follow-up question that can be answered from the history?\n"
                f"3. Does this need a new solution proposal?\n"
                f"4. Should this be escalated to the admin?\n\n"
                f"Respond with JSON:\n"
                f'{{\n'
                f'  "can_handle_directly": true/false,\n'
                f'  "response_to_user": "Plain-language response if you can answer directly",\n'
                f'  "needs_new_solution": true/false,\n'
                f'  "proposed_solution": {{}},\n'
                f'  "escalation_reason": "If escalating, explain why",\n'
                f'  "updated_understanding": "Updated AI understanding if the issue has evolved"\n'
                f'}}\n\n'
                f"If proposing a new solution, use the standard solution format with execution_steps, "
                f"description_user, description_admin, resolution_type."
            )

            response = client.chat.completions.create(
                model='gpt-5',
                messages=[
                    {'role': 'system', 'content': 'You are Scout Support, an expert AI assistant for Bullhorn ATS issues. You are analyzing a reopened ticket with full conversation history. Respond only in valid JSON.'},
                    {'role': 'user', 'content': prompt},
                ],
            )

            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Reopen analysis failed for {ticket.ticket_number}: {e}")
            return None

    def _send_admin_approval_request_custom(self, ticket, body: str):
        admin_email = ticket.admin_email
        if not admin_email:
            return

        self._send_email(
            to_email=admin_email,
            subject=f"[{ticket.ticket_number}] Reopened — Admin Review Needed",
            body=body,
            ticket=ticket,
            email_type='admin_escalation',
        )

    def _handle_clarification_reply(self, ticket, reply_body: str, attachment_content: str = '', attachment_ack: str = '') -> bool:
        from extensions import db
        from models import SupportConversation

        clarification_count = SupportConversation.query.filter_by(
            ticket_id=ticket.id,
            direction='outbound',
            email_type='clarification',
        ).count()

        analysis = self._analyze_clarification(ticket, reply_body, attachment_content=attachment_content)
        if not analysis:
            if clarification_count >= self.MAX_CLARIFICATION_ROUNDS:
                self._escalate_to_admin(ticket, reason=f"AI could not analyze the issue after {clarification_count} clarification rounds.")
                return True
            return False

        try:
            parsed = json.loads(analysis)
        except (json.JSONDecodeError, TypeError):
            parsed = {'fully_understood': False}

        if parsed.get('fully_understood', False):
            ticket.ai_understanding = json.dumps(parsed)
            solution_user = parsed.get('proposed_solution_user', '') or parsed.get('proposed_solution', '')
            solution_admin = parsed.get('proposed_solution_admin', '') or parsed.get('proposed_solution', '')
            resolution_type = parsed.get('resolution_type', 'full')
            concerns_user = parsed.get('underlying_concerns_user', '') or parsed.get('underlying_concerns', '')
            concerns_admin = parsed.get('underlying_concerns_admin', '') or parsed.get('underlying_concerns', '')

            if resolution_type == 'escalate':
                escalation_reason = concerns_admin or 'AI determined this requires human intervention after clarification.'
                self._escalate_to_admin(ticket, reason=escalation_reason, understanding=parsed.get('updated_understanding', ''))
                logger.info(f"⚠️ Ticket {ticket.ticket_number} escalated after clarification (resolution_type=escalate)")
                return True

            if solution_user:
                ticket.proposed_solution = json.dumps({
                    'description_user': solution_user,
                    'description_admin': solution_admin,
                    'can_execute': parsed.get('can_execute', False),
                    'requires_bullhorn': True,
                    'execution_steps': parsed.get('execution_steps', []),
                    'resolution_type': resolution_type,
                    'underlying_concerns_user': concerns_user,
                    'underlying_concerns_admin': concerns_admin,
                })
                ticket.status = 'awaiting_user_approval'
                db.session.commit()
                self._send_solution_proposal_email(ticket, solution_user, underlying_concerns=concerns_user, attachment_ack=attachment_ack)
            else:
                ticket.status = 'clarifying'
                db.session.commit()
                self._send_clarification_email(ticket, parsed.get('follow_up', ''), attachment_ack=attachment_ack)
        else:
            if clarification_count >= self.MAX_CLARIFICATION_ROUNDS:
                self._escalate_to_admin(
                    ticket,
                    reason=f"Unable to fully understand the issue after {clarification_count} clarification rounds.",
                    understanding=parsed.get('updated_understanding', ''),
                )
                logger.info(f"⚠️ Ticket {ticket.ticket_number} escalated after {clarification_count} clarification rounds")
                return True

            ticket.status = 'clarifying'
            db.session.commit()
            follow_up = parsed.get('follow_up', 'Could you provide more details about the issue?')
            self._send_clarification_email(ticket, follow_up, attachment_ack=attachment_ack)

        return True

    def _handle_platform_reply(self, ticket, reply_body: str, attachment_content: str = '') -> bool:
        from extensions import db
        from scout_support_service import CATEGORY_LABELS, DEFAULT_ADMIN_EMAIL

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)

        if ticket.status not in ('clarifying', 'in_progress'):
            ticket.status = 'clarifying'
            db.session.commit()

        first_name = ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'
        user_body = (
            f"Hi {first_name},\n\n"
            f"Thank you for your reply. Our team has been notified and will "
            f"review your message shortly.\n\n"
            f"**Ticket:** {ticket.ticket_number}\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"— Scout Genius"
        )

        self._send_platform_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body=user_body,
            ticket=ticket,
            email_type='platform_reply_ack',
        )

        admin_body = (
            f"**📩 User Reply on Platform Ticket {ticket.ticket_number}**\n\n"
            f"**From:** {ticket.submitter_name} ({ticket.submitter_email})\n"
            f"**Type:** {category_label}\n"
            f"**Subject:** {ticket.subject}\n\n"
            f"**User's Reply:**\n{reply_body}\n\n"
            f"{f'**Attachment Content:**{chr(10)}{attachment_content[:2000]}{chr(10)}{chr(10)}' if attachment_content else ''}"
            f"Reply to this ticket from the Scout Support dashboard."
        )

        self._send_platform_email(
            to_email=DEFAULT_ADMIN_EMAIL,
            subject=f"[Reply] {ticket.ticket_number} — {ticket.subject}",
            body=admin_body,
            email_type='platform_user_reply_notification',
        )

        logger.info(f"💬 Platform ticket {ticket.ticket_number} user reply recorded, admin notified")
        return True

    def _handle_platform_reply_ai(self, ticket, reply_body: str, attachment_content: str = '') -> bool:
        from extensions import db
        from scout_support_service import CATEGORY_LABELS

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)

        conversations = ticket.conversations.order_by(db.text('created_at ASC')).all()
        history = []
        for conv in conversations:
            role = "User" if conv.sender_email == ticket.submitter_email else "Scout Genius"
            history.append(f"[{role}] {conv.body[:500]}")

        prompt = f"""You are Scout Genius, a helpful platform support assistant. A user submitted platform feedback ({category_label}) and has sent a follow-up reply.

Ticket: {ticket.ticket_number}
Subject: {ticket.subject}
Original Description: {ticket.description}

Conversation History:
{chr(10).join(history[-10:])}

Latest Reply from User:
{reply_body}

{f'Attachment Content: {attachment_content[:2000]}' if attachment_content else ''}

IMPORTANT: This is a PLATFORM feedback ticket (not an ATS/Bullhorn support ticket). You must NOT propose any Bullhorn API actions, execution steps, or system modifications. Your role is to:
1. Acknowledge the user's follow-up
2. Provide helpful information or ask clarifying questions
3. Let them know the team has been notified if it requires human action

Respond with a JSON object:
{{
    "response": "Your helpful reply to the user in plain language",
    "needs_more_info": true/false,
    "follow_up_question": "Optional question if more info is needed",
    "can_close": true/false,
    "close_reason": "Optional reason if the conversation is naturally complete"
}}"""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-5.4",
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=1000,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith('```'):
                raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
            parsed = json.loads(raw)
        except Exception as e:
            logger.error(f"Platform reply AI analysis failed for {ticket.ticket_number}: {e}")
            parsed = {
                'response': 'Thank you for your follow-up. Our team has been notified and will review your feedback.',
                'needs_more_info': False,
                'can_close': False,
            }

        ai_response = parsed.get('response', 'Thank you for your follow-up.')
        can_close = parsed.get('can_close', False)

        first_name = ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'
        body_parts = [
            f"Hi {first_name},",
            "",
            ai_response,
        ]

        if parsed.get('needs_more_info') and parsed.get('follow_up_question'):
            body_parts.extend(["", parsed['follow_up_question']])

        if can_close:
            body_parts.extend(["", "This ticket will now be marked as resolved. If you need anything else, feel free to submit new feedback anytime."])
            ticket.status = 'closed'
            ticket.resolved_at = datetime.utcnow()
        else:
            ticket.status = 'clarifying'

        body_parts.extend(["", "— Scout Genius"])
        db.session.commit()

        self._send_platform_email(
            to_email=ticket.submitter_email,
            subject=f"Re: [{ticket.ticket_number}] {ticket.subject}",
            body="\n".join(body_parts),
            ticket=ticket,
            email_type='platform_reply',
        )

        logger.info(f"💬 Platform ticket {ticket.ticket_number} AI reply handled (close={can_close})")
        return True

    def _handle_user_approval_response(self, ticket, reply_body: str) -> bool:
        from extensions import db

        approval = self._classify_user_response(reply_body)

        if approval == 'approved':
            ticket.user_approved_at = datetime.utcnow()
            ticket.status = 'approved'
            db.session.commit()
            logger.info(f"👤 User approved ticket {ticket.ticket_number} — attempting execution")

            try:
                solution_data = json.loads(ticket.proposed_solution) if ticket.proposed_solution else {}
            except (json.JSONDecodeError, TypeError):
                solution_data = {}

            execution_steps = solution_data.get('execution_steps', [])

            if execution_steps:
                success = self._execute_solution(ticket)

                if success:
                    logger.info(f"✅ Ticket {ticket.ticket_number} executed successfully after user approval")
                else:
                    attempts_made = ticket.execution_attempts or 1
                    logger.warning(f"⚠️ Ticket {ticket.ticket_number} execution failed after {attempts_made} attempt(s) — escalating to admin")
                    ticket.status = 'awaiting_admin_approval'
                    db.session.commit()

                    try:
                        history = json.loads(ticket.execution_history) if ticket.execution_history else []
                    except (json.JSONDecodeError, TypeError):
                        history = []

                    if attempts_made > 1:
                        user_msg = (
                            f'I attempted {attempts_made} different approaches to fix this issue, '
                            f'but was unable to resolve it automatically. '
                            f'The issue has been escalated to the administrator with full diagnostic details from all attempts. '
                            f'You will be notified once the fix is finalized.'
                        )
                    else:
                        user_msg = (
                            f'I attempted the proposed fix but some steps could not be completed automatically. '
                            f'The issue has been escalated to the administrator for manual resolution. '
                            f'You will be notified once the fix is finalized.'
                        )

                    self._send_user_confirmation_email(ticket, user_msg)
                    self._send_admin_approval_request(ticket, execution_failed=True, failure_history=history)
            else:
                ticket.status = 'awaiting_admin_approval'
                db.session.commit()
                self._send_user_confirmation_email(
                    ticket,
                    'Your approval has been received. This issue requires manual intervention by the administrator. '
                    'You will receive a confirmation once the fix has been applied.'
                )
                self._send_admin_approval_request(ticket, needs_manual=True)
                logger.info(f"👤 Ticket {ticket.ticket_number} has no execution steps — forwarded to admin for manual resolution")

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
        fresh_text = self._strip_quoted_text(text)
        if not fresh_text:
            fresh_text = text.split('\n')[0] if text else ''
        logger.info(f"🔍 User response classification input ({len(fresh_text)} chars): {fresh_text[:300]}")

        if self.openai_client:
            try:
                ai_decision = self._ai_classify_response(fresh_text, role='user')
                if ai_decision:
                    logger.info(f"🤖 AI classified user response as: {ai_decision}")
                    return ai_decision
            except Exception as e:
                logger.warning(f"AI classification failed, falling back to keyword: {e}")

        return self._keyword_classify_user(fresh_text)

    def _keyword_classify_user(self, text: str) -> str:
        text_lower = text.lower().strip()
        approve_keywords = ['yes', 'approved', 'go ahead', 'looks good', 'proceed',
                           'correct', 'that works', 'do it', 'please proceed', 'sounds good']
        if any(kw in text_lower for kw in approve_keywords):
            return 'approved'

        reject_keywords = ['no', 'cancel', 'close', 'reject', "don't", 'stop']
        if any(kw in text_lower for kw in reject_keywords):
            return 'rejected'

        return 'needs_changes'

    def _handle_admin_question(self, ticket, admin_message: str):
        from extensions import db

        from scout_support_service import CATEGORY_LABELS

        conversations = ticket.conversations.order_by(db.text('created_at ASC')).all()

        history = []
        for conv in conversations:
            role = "Admin" if conv.sender_email == ticket.admin_email else ("User" if conv.sender_email == ticket.submitter_email else "Scout Support")
            history.append(f"[{role}] {conv.body[:500]}")

        try:
            understanding = json.loads(ticket.ai_understanding) if ticket.ai_understanding else {}
        except (json.JSONDecodeError, TypeError):
            understanding = {}

        try:
            solution_data = json.loads(ticket.proposed_solution) if ticket.proposed_solution else {}
        except (json.JSONDecodeError, TypeError):
            solution_data = {}

        knowledge_section = ''
        try:
            from scout_support.knowledge import KnowledgeService
            ks = KnowledgeService()
            knowledge_section = ks.build_knowledge_context(
                ticket.subject, f"{ticket.description}\n{admin_message}", ticket.category
            )
        except Exception as e:
            logger.warning(f"Knowledge retrieval failed during admin Q&A for ticket {ticket.ticket_number}: {e}")

        prompt = f"""You are Scout Support, an AI assistant for internal ATS (Bullhorn) support operations.

The administrator is reviewing ticket {ticket.ticket_number} for final approval and has a question or comment.
You need to respond with a clear, thorough answer to help them make their approval decision.

Ticket Details:
- Subject: {ticket.subject}
- Category: {CATEGORY_LABELS.get(ticket.category, ticket.category)}
- Priority: {ticket.priority}
- Submitted by: {ticket.submitter_name} ({ticket.submitter_email})
- Department: {ticket.submitter_department or 'Not specified'}

Original Issue:
{ticket.description}

AI Understanding:
{understanding.get('understanding', 'Not available')}

Proposed Solution (Technical):
{solution_data.get('description_admin', solution_data.get('description_user', 'Not available'))}

Execution Steps:
{json.dumps(solution_data.get('execution_steps', []), indent=2)}

Conversation History:
{chr(10).join(history)}{knowledge_section}

Admin's Question/Comment:
{admin_message}

Respond directly to the admin's question. Be thorough and technical — this is the administrator, not an end user.
Include any relevant Bullhorn entity details, field names, potential risks, or alternative approaches if applicable.

After answering, remind them they can:
- Reply "Approved" or "Go ahead" to authorize execution
- Reply "Hold" to pause the ticket
- Reply "Reject" or "Close" to cancel
- Or continue asking questions

Keep your response focused and professional. Do not wrap in JSON — respond in plain text."""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[
                    {'role': 'system', 'content': 'You are Scout Support, an AI ATS support assistant responding to an administrator\'s question during the approval review stage.'},
                    {'role': 'user', 'content': prompt},
                ],
                max_completion_tokens=2048,
            )
            answer = (response.choices[0].message.content or '').strip()
            if not answer:
                logger.warning(f"⚠️ AI returned empty response for admin question on {ticket.ticket_number}")
                answer = (
                    f"Thank you for your feedback. If you're ready to proceed, please reply with "
                    f"\"Approved\" or \"Go ahead\" to authorize execution of the proposed fix.\n\n"
                    f"You can also:\n"
                    f"- Reply \"Hold\" to pause this ticket\n"
                    f"- Reply \"Reject\" or \"Close\" to cancel\n"
                    f"- Or ask any questions about the proposed solution"
                )
        except Exception as e:
            logger.error(f"❌ Failed to generate admin question response for {ticket.ticket_number}: {e}")
            answer = (
                f"I apologize, but I encountered an error while processing your question. "
                f"Please try rephrasing, or you can:\n"
                f"- Reply \"Approved\" to authorize the proposed solution\n"
                f"- Reply \"Hold\" to pause this ticket\n"
                f"- Reply \"Reject\" to cancel"
            )

        self._send_email(
            to_email=ticket.admin_email,
            subject=f"[{ticket.ticket_number}] {ticket.subject}",
            body=answer,
            ticket=ticket,
            email_type='admin_clarification_response',
        )

        ticket.status = 'awaiting_admin_approval'
        db.session.commit()
        logger.info(f"💬 Responded to admin question on ticket {ticket.ticket_number}")

    def _strip_quoted_text(self, text: str) -> str:
        lines = text.split('\n')
        fresh_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('>'):
                continue
            if stripped.startswith('On ') and stripped.endswith('wrote:'):
                break
            if re.match(r'^-{3,}', stripped) or re.match(r'^_{3,}', stripped):
                break
            if stripped.lower().startswith('from:') and any(kw in stripped.lower() for kw in ['scout support', 'scoutgenius']):
                break
            if stripped.lower().startswith('subject:') and '[ss-' in stripped.lower():
                break
            if re.match(r'^(sent|date|from|to|subject):\s', stripped, re.IGNORECASE) and len(fresh_lines) > 2:
                header_cluster = sum(1 for fl in fresh_lines[-3:] if re.match(r'^(sent|date|from|to|subject):\s', fl.strip(), re.IGNORECASE))
                if header_cluster >= 1:
                    fresh_lines = fresh_lines[:-3]
                    break
            fresh_lines.append(line)
        return '\n'.join(fresh_lines).strip()

    def _classify_admin_response(self, text: str) -> str:
        fresh_text = self._strip_quoted_text(text)
        if not fresh_text:
            fresh_text = text.split('\n')[0] if text else ''
        logger.info(f"🔍 Admin response classification input ({len(fresh_text)} chars): {fresh_text[:300]}")

        if self.openai_client:
            try:
                ai_decision = self._ai_classify_response(fresh_text, role='admin')
                if ai_decision:
                    logger.info(f"🤖 AI classified admin response as: {ai_decision}")
                    return ai_decision
            except Exception as e:
                logger.warning(f"AI classification failed, falling back to keyword: {e}")

        return self._keyword_classify_admin(fresh_text)

    def _ai_classify_response(self, text: str, role: str = 'admin') -> Optional[str]:
        if role == 'admin':
            options_desc = (
                "- 'approved' — The admin is giving permission to proceed with the fix. This includes explicit approvals "
                "(\"Approved\", \"Yes\", \"Go ahead\") AND natural language consent (\"Okay, make that change\", "
                "\"Sure, do it\", \"Let's just ensure the field is changed... you can go ahead\") AND short positive "
                "affirmations (\"Good\", \"Good.\", \"Ok\", \"Fine\", \"Perfect\", \"Great\", \"Sounds good\", \"Correct\", "
                "\"Yep\", \"Sure\", \"Absolutely\"). "
                "The response may also include additional context, notes, caveats, or instructions alongside the approval — "
                "that still counts as approved. If the admin says to proceed with the fix in ANY way, classify as approved.\n"
                "- 'hold' — The admin explicitly wants to pause or defer the ticket (\"Hold off\", \"Let's wait\", \"Put this on hold\").\n"
                "- 'closed' — The admin explicitly wants to cancel or reject the fix (\"Reject this\", \"Cancel\", \"Do not proceed\", \"Close the ticket\").\n"
                "- 'admin_question' — The admin is asking a question or requesting more information before deciding. "
                "They have NOT given approval yet."
            )
        else:
            options_desc = (
                "- 'approved' — The user is agreeing to the proposed solution (\"Yes\", \"Go ahead\", \"Looks good\", \"That works\").\n"
                "- 'rejected' — The user explicitly wants to cancel (\"No thanks\", \"Cancel\", \"Close this\").\n"
                "- 'needs_changes' — The user wants modifications to the proposed solution before approving."
            )

        prompt = f"""Classify this email reply from the {role}. Read the FULL message and determine the {role}'s PRIMARY INTENT.

The {role}'s reply:
\"\"\"
{text[:3000]}
\"\"\"

Classification options:
{options_desc}

IMPORTANT:
- Focus on the {role}'s actual intent, not individual words.
- Replies often contain approval PLUS additional context, notes, or instructions — that is still an approval.
- Only classify as a question/hold/close if the {role} is genuinely NOT giving permission to proceed.

Respond with ONLY the classification label (one word, lowercase). Nothing else."""

        response = self.openai_client.chat.completions.create(
            model='gpt-5',
            messages=[{'role': 'user', 'content': prompt}],
            max_completion_tokens=20,
        )
        result = (response.choices[0].message.content or '').strip().lower().strip("'\"")

        valid_labels = {
            'admin': {'approved', 'hold', 'closed', 'admin_question'},
            'user': {'approved', 'rejected', 'needs_changes'},
        }
        if result in valid_labels.get(role, set()):
            return result
        for label in valid_labels.get(role, set()):
            if label in result:
                return label
        logger.warning(f"AI classification returned unexpected label: {result}")
        return None

    def _keyword_classify_admin(self, text: str) -> str:
        text_lower = text.lower().strip()
        words = set(re.findall(r'\b\w+\b', text_lower))

        approve_phrases = [
            'go ahead', 'green light', 'looks good', 'yes, proceed', 'yes, go ahead',
            'proceed', 'you can go ahead', 'approved', 'approve', 'authorize',
            'do it', 'execute', 'make the change', 'yes please', 'that is correct',
            'sounds good', 'that works', "that's fine", 'all good', 'no issues',
            'good to go', 'lgtm', 'ship it',
        ]
        if any(phrase in text_lower for phrase in approve_phrases):
            return 'approved'

        approve_exact_starts = ['approved', 'approve', 'yes', 'go ahead', 'proceed', 'authorized',
                                'green light', 'looks good', 'good', 'ok', 'okay', 'fine',
                                'sure', 'perfect', 'great', 'agreed', 'confirmed', 'affirmative',
                                'yep', 'yup', 'absolutely', 'correct']
        if text_lower in approve_exact_starts or any(text_lower.startswith(kw) for kw in approve_exact_starts):
            return 'approved'
        approve_words = {'approved', 'approve', 'authorized'}
        if words & approve_words:
            return 'approved'

        reject_phrases = ['reject', 'denied', 'deny', 'close this', 'cancel', 'do not proceed', "don't proceed"]
        if any(phrase in text_lower for phrase in reject_phrases):
            return 'closed'

        hold_phrases = ['hold off', 'put on hold', 'place on hold', 'wait', 'pause', 'defer']
        if any(phrase in text_lower for phrase in hold_phrases):
            return 'hold'

        return 'admin_question'

    def _refine_execution_with_admin_instructions(self, ticket, admin_text: str):
        from extensions import db
        if not self.openai_client:
            return

        try:
            solution_data = json.loads(ticket.proposed_solution) if ticket.proposed_solution else {}
        except (json.JSONDecodeError, TypeError):
            return

        original_steps = solution_data.get('execution_steps', [])
        if not original_steps:
            return

        try:
            understanding = json.loads(ticket.ai_understanding) if ticket.ai_understanding else {}
        except (json.JSONDecodeError, TypeError):
            understanding = {}

        prompt = f"""You are Scout Support. The admin has approved a solution but included additional instructions or conditions.

Original ticket:
- Subject: {ticket.subject}
- Description: {ticket.description}
- AI understanding: {understanding.get('understanding', '')}

Original execution steps:
{json.dumps(original_steps, indent=2)}

Admin's approval message (with additional instructions):
\"\"\"{admin_text}\"\"\"

Your task:
1. Keep the original execution steps as a base.
2. Incorporate the admin's additional instructions/conditions into the execution plan.
3. If the admin wants a conditional check (e.g., "if field X is empty, use field Y"), add a get_entity step first to check the condition, then add/modify the update steps accordingly.
4. Return the FULL updated execution_steps array in JSON format.

Important:
- Use the exact same step format as the original steps.
- Add any new steps needed to fulfill the admin's instructions.
- If the admin's instructions change a step, update that step.
- If the admin's instructions add a condition, add a get_entity step FIRST to retrieve the data, then use it in the update step.
- For conditional updates where the new value depends on a retrieved field, set `new_value` to `"{{{{EntityType_entityId_fieldName}}}}"` to reference runtime context from a prior get_entity step (e.g., `"{{{{JobOrder_34711_description}}}}"` to use the description field retrieved from JobOrder 34711).
- You can also add `"fallback_field": "fieldName"` to an update_entity step — if the primary new_value is empty, it will use that field's value from a prior get_entity of the same entity.

Respond with ONLY a JSON object:
{{"execution_steps": [...], "description_user": "Updated plain-language description of what will happen", "description_admin": "Updated technical description"}}"""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[
                    {'role': 'system', 'content': 'You are Scout Support, an expert AI assistant for Bullhorn ATS. Respond only in valid JSON.'},
                    {'role': 'user', 'content': prompt}
                ],
                max_completion_tokens=4096,
                response_format={'type': 'json_object'},
            )
            content = response.choices[0].message.content
            if not content or not content.strip():
                logger.warning("Admin instruction refinement returned empty content")
                return

            parsed = json.loads(content.strip())
            new_steps = parsed.get('execution_steps', [])
            if new_steps:
                solution_data['execution_steps'] = new_steps
                if parsed.get('description_user'):
                    solution_data['description_user'] = parsed['description_user']
                if parsed.get('description_admin'):
                    solution_data['description_admin'] = parsed['description_admin']
                ticket.proposed_solution = json.dumps(solution_data)
                db.session.commit()
                logger.info(f"🔄 Refined execution steps for {ticket.ticket_number} with admin instructions ({len(original_steps)} → {len(new_steps)} steps)")
            else:
                logger.info(f"Admin instructions did not change execution steps for {ticket.ticket_number}")

        except Exception as e:
            logger.warning(f"Failed to refine execution with admin instructions: {e}")

    def _classify_admin_handling_intent(self, text: str) -> str:
        if not self.openai_client:
            return self._keyword_classify_admin_handling(text)

        try:
            prompt = f"""You are Scout Support, an AI assistant for Bullhorn ATS support tickets.

An administrator has replied to a support ticket that is currently being handled manually (admin_handling status).
Determine whether the admin's message is:

1. "ai_instruction" — The admin is asking Scout Support (the AI) to do something: draft an email, summarize the issue, 
   generate a report, create a response, look something up, provide guidance, compose a message, etc.
   Key indicators: "draft", "write", "compose", "summarize", "create", "generate", "prepare", "put together", 
   "help me write", "can you", "please draft", "I need you to", addressing Scout Support directly.

2. "direct_reply" — The admin is writing a message intended to be forwarded to the user/submitter. This is a 
   conversational reply addressed to the user (e.g., "Hi Lisa, we're looking into this", "We've identified the issue", 
   "Please provide more details").

The admin's message:
\"\"\"
{text[:3000]}
\"\"\"

Respond with ONLY one label: ai_instruction or direct_reply"""

            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[{'role': 'user', 'content': prompt}],
                max_completion_tokens=20,
            )
            result = (response.choices[0].message.content or '').strip().lower().strip("'\"")

            if 'ai_instruction' in result:
                return 'ai_instruction'
            elif 'direct_reply' in result:
                return 'direct_reply'
            else:
                logger.warning(f"AI admin handling intent classification returned unexpected: {result}")
                return self._keyword_classify_admin_handling(text)

        except Exception as e:
            logger.warning(f"AI admin handling intent classification failed: {e}")
            return self._keyword_classify_admin_handling(text)

    def _keyword_classify_admin_handling(self, text: str) -> str:
        text_lower = text.lower().strip()

        instruction_phrases = [
            'draft', 'write up', 'compose', 'put together', 'prepare',
            'generate', 'create a', 'can you', 'please draft', 'help me write',
            'i need you to', 'summarize', 'draft up', 'write a', 'compile',
            'send to', 'email to', 'letter to', 'response to',
            'could you', 'would you', 'please create', 'please write',
            'please prepare', 'please compose', 'please generate',
            'please summarize', 'please compile',
        ]

        if any(phrase in text_lower for phrase in instruction_phrases):
            return 'ai_instruction'

        return 'direct_reply'

    def _generate_admin_draft(self, ticket, admin_instruction: str):
        from extensions import db
        from models import SupportTicket, SupportConversation
        from scout_support_service import SCOUT_SUPPORT_EMAIL

        conversation_history = ''
        try:
            conversations = SupportConversation.query.filter_by(
                ticket_id=ticket.id
            ).order_by(SupportConversation.created_at.asc()).all()
            if conversations:
                history_parts = []
                for conv in conversations:
                    role = "Admin" if conv.email_type in ('admin_reply', 'admin_direct_reply', 'admin_ai_instruction') else (
                        "Scout Support" if conv.direction == 'outbound' else "User"
                    )
                    history_parts.append(f"[{role}] {(conv.body or '')[:500]}")
                conversation_history = "\n---\n".join(history_parts)
        except Exception as e:
            logger.warning(f"Could not build conversation history for admin draft: {e}")

        ai_understanding = ''
        if ticket.ai_understanding:
            try:
                parsed = json.loads(ticket.ai_understanding)
                ai_understanding = parsed.get('understanding', parsed.get('updated_understanding', str(parsed)))
            except (json.JSONDecodeError, TypeError):
                ai_understanding = ticket.ai_understanding or ''

        attachment_context = ''
        try:
            from models import SupportAttachment
            attachments = SupportAttachment.query.filter_by(ticket_id=ticket.id).all()
            if attachments:
                att_parts = []
                for att in attachments:
                    size_kb = round(att.file_size / 1024, 1) if att.file_size else 'unknown'
                    att_parts.append(f"- {att.filename} ({size_kb} KB, {att.content_type})")
                attachment_context = "\n**Attachments:**\n" + "\n".join(att_parts)
        except Exception as e:
            logger.warning(f"Could not load attachment context for admin draft: {e}")

        prompt = f"""You are Scout Support, an expert AI assistant specializing in Bullhorn ATS/CRM support.

The administrator has given you an instruction regarding support ticket {ticket.ticket_number}. 
Generate the requested content based on the full ticket context.

**Admin's Instruction:**
{admin_instruction}

**Ticket Details:**
- Ticket: {ticket.ticket_number}
- Subject: {ticket.subject}
- Category: {ticket.category}
- Submitted by: {ticket.submitter_name} ({ticket.submitter_email})
- Department: {ticket.submitter_department or 'Not specified'}
- Priority: {ticket.priority}

**Original Description:**
{ticket.description[:3000]}

**AI Understanding:**
{ai_understanding[:2000]}
{attachment_context}

**Escalation Reason:**
{ticket.escalation_reason or 'Not specified'}

**Conversation History:**
{conversation_history[:4000]}

INSTRUCTIONS:
- Generate exactly what the admin requested (e.g., a draft email, summary, report, etc.)
- Be professional and thorough
- Include all relevant details from the ticket context
- If drafting an email to a third party (e.g., Bullhorn Support), include a professional subject line suggestion at the top
- Format the output clearly so the admin can copy and use it directly
- Do NOT include any meta-commentary like "Here is the draft" — just provide the content itself"""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[
                    {'role': 'system', 'content': 'You are Scout Support, an expert AI assistant for Bullhorn ATS. Generate professional, actionable content as requested by the administrator.'},
                    {'role': 'user', 'content': prompt}
                ],
                max_completion_tokens=4096,
            )
            draft_content = (response.choices[0].message.content or '').strip()

            if not draft_content:
                logger.warning(f"AI draft generation returned empty content for ticket {ticket.ticket_number}")
                draft_content = "I was unable to generate the requested content. Please try rephrasing your instruction."

        except Exception as e:
            logger.error(f"AI draft generation failed for ticket {ticket.ticket_number}: {e}")
            draft_content = f"I encountered an error while generating the requested content: {str(e)[:200]}. Please try again."

        draft_conv = SupportConversation(
            ticket_id=ticket.id,
            direction='outbound',
            sender_email=SCOUT_SUPPORT_EMAIL,
            recipient_email=ticket.admin_email,
            subject=f"Re: [{ticket.ticket_number}] AI Draft",
            body=draft_content,
            email_type='admin_ai_draft',
        )
        db.session.add(draft_conv)
        db.session.commit()

        email_body = (
            f"**AI-Generated Draft for Ticket {ticket.ticket_number}**\n\n"
            f"Based on your instruction:\n"
            f"*\"{admin_instruction[:500]}\"*\n\n"
            f"---\n\n"
            f"{draft_content}\n\n"
            f"---\n\n"
            f"You can copy and use this draft directly. "
            f"If you need revisions, reply to this email with your feedback and I'll generate an updated version.\n\n"
            f"— Scout Support"
        )

        self._send_email(
            to_email=ticket.admin_email,
            subject=f"[AI Draft] [{ticket.ticket_number}] {ticket.subject}",
            body=email_body,
            ticket=ticket,
            email_type='admin_ai_draft_email',
        )

        logger.info(f"✨ AI draft generated and sent to admin for ticket {ticket.ticket_number}")
