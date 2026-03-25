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
"""

import re
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

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

        if ticket.status == 'awaiting_user_approval':
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

        if ticket.status not in ('awaiting_admin_approval', 'admin_clarifying'):
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
        if message_id:
            ticket.last_message_id = message_id
        db.session.commit()

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
                temperature=0.4,
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
