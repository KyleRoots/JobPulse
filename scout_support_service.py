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

STSI_STAKEHOLDER_EMAIL = 'jbocek@stsigroup.com'

STSI_ESCALATION_CONTACTS = {
    'email_notifications': 'doneil@q-staffing.com',
    'backoffice_onboarding': 'evalentine@stsigroup.com',  # Emma Valentine
    'backoffice_finance': 'evalentine@stsigroup.com',  # Emma Valentine
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
}

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


class ScoutSupportService:

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

        if ticket.category in HANDOFF_CATEGORIES:
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

        if resolution_type == 'escalate' or (confidence == 'low' and not has_clarification):
            ticket.ai_understanding = understanding
            ticket.status = 'escalated'
            escalation_reason = parsed.get('escalation_reason', 'AI determined it cannot fully resolve this issue.')
            ticket.escalation_reason = escalation_reason
            db.session.commit()

            self._escalate_to_admin(ticket, reason=escalation_reason, understanding=parsed.get('understanding', ''))
            logger.info(f"⚠️ Ticket {ticket.ticket_number} escalated — AI confidence: {confidence}, resolution_type: {resolution_type}")
            return True

        proposed_user = parsed.get('proposed_solution_user', '') or parsed.get('proposed_solution', '')
        proposed_admin = parsed.get('proposed_solution_admin', '') or parsed.get('proposed_solution', '')

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
            parsed['clarification_needed'] = True
            if not parsed.get('clarification_questions'):
                parsed['clarification_questions'] = [
                    'Could you provide any additional details or context about this issue?'
                ]
            understanding = json.dumps(parsed)

        ticket.ai_understanding = understanding
        ticket.status = 'acknowledged'
        db.session.commit()

        self._send_acknowledgment_email(ticket, understanding, attachment_ack=attachment_ack)
        logger.info(f"✅ Ticket {ticket.ticket_number} acknowledged (AI full, confidence={confidence}, resolution_type={resolution_type}, clarify_first={needs_clarification_first}), email sent to {ticket.submitter_email}")
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

    def handle_user_reply(self, ticket_id: int, reply_body: str, message_id: str = '',
                          attachment_data: Optional[List[Dict]] = None) -> bool:
        from extensions import db
        from models import SupportTicket, SupportConversation

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
            logger.error(f"Ticket {ticket_id} not found for reply handling")
            return False

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
        ticket.last_message_id = message_id
        db.session.commit()

        attachment_content = ''
        attachment_ack = ''
        if attachment_data:
            attachment_content = self._extract_attachment_content(attachment_data)
            attachment_ack = self._build_attachment_acknowledgment()
            if attachment_content:
                logger.info(f"📎 Extracted {len(attachment_content)} chars from {len(attachment_data)} attachment(s) on reply to {ticket.ticket_number}")

        if ticket.status == 'awaiting_user_approval':
            return self._handle_user_approval_response(ticket, reply_body)
        elif ticket.status in ('acknowledged', 'clarifying'):
            return self._handle_clarification_reply(ticket, reply_body, attachment_content=attachment_content, attachment_ack=attachment_ack)

        return True

    def handle_admin_reply(self, ticket_id: int, reply_body: str, message_id: str = '') -> bool:
        from extensions import db
        from models import SupportTicket, SupportConversation

        ticket = SupportTicket.query.get(ticket_id)
        if not ticket:
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
        ticket.last_message_id = message_id
        db.session.commit()

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

    def _extract_attachment_content(self, attachment_data: List[Dict]) -> str:
        if not attachment_data:
            return ''

        self._attachment_results = []
        extracted_parts = []
        for att in attachment_data:
            filename = att.get('filename', 'unknown')
            data = att.get('data', b'')
            content_type = att.get('content_type', 'application/octet-stream')
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

            try:
                if ext == 'txt' or content_type == 'text/plain':
                    text = data.decode('utf-8', errors='replace').strip()
                    if text:
                        extracted_parts.append(f"[File: {filename}]\n{text[:10000]}")
                        self._attachment_results.append({'filename': filename, 'status': 'read', 'type': 'text'})
                    else:
                        self._attachment_results.append({'filename': filename, 'status': 'empty', 'type': 'text'})

                elif ext == 'csv' or content_type == 'text/csv':
                    text = data.decode('utf-8', errors='replace').strip()
                    if text:
                        lines = text.split('\n')[:100]
                        extracted_parts.append(f"[File: {filename} — CSV, {len(text.split(chr(10)))} rows]\n{chr(10).join(lines)}")
                        self._attachment_results.append({'filename': filename, 'status': 'read', 'type': 'csv'})
                    else:
                        self._attachment_results.append({'filename': filename, 'status': 'empty', 'type': 'csv'})

                elif ext == 'pdf' or content_type == 'application/pdf':
                    text = self._extract_pdf_text(data)
                    if text:
                        extracted_parts.append(f"[File: {filename}]\n{text[:10000]}")
                        self._attachment_results.append({'filename': filename, 'status': 'read', 'type': 'document'})
                    else:
                        self._attachment_results.append({'filename': filename, 'status': 'failed', 'type': 'document'})

                elif ext in ('doc', 'docx') or 'word' in content_type:
                    text = self._extract_docx_text(data, ext)
                    if text:
                        extracted_parts.append(f"[File: {filename}]\n{text[:10000]}")
                        self._attachment_results.append({'filename': filename, 'status': 'read', 'type': 'document'})
                    else:
                        self._attachment_results.append({'filename': filename, 'status': 'failed', 'type': 'document'})

                elif ext in ('png', 'jpg', 'jpeg', 'gif') or content_type.startswith('image/'):
                    description = self._describe_image(data, content_type, filename)
                    is_failure = not description or any(marker in (description or '') for marker in [
                        'vision analysis failed', 'vision not available', 'vision analysis returned empty'
                    ])
                    if not is_failure:
                        extracted_parts.append(f"[Image: {filename}]\n{description}")
                        self._attachment_results.append({'filename': filename, 'status': 'read', 'type': 'image'})
                    else:
                        self._attachment_results.append({'filename': filename, 'status': 'failed', 'type': 'image'})

                elif ext == 'xlsx' or 'spreadsheet' in content_type:
                    extracted_parts.append(f"[File: {filename} — Excel spreadsheet attached, content not extracted]")
                    self._attachment_results.append({'filename': filename, 'status': 'unsupported', 'type': 'spreadsheet'})

                else:
                    extracted_parts.append(f"[File: {filename} — {content_type}, content not extracted]")
                    self._attachment_results.append({'filename': filename, 'status': 'unsupported', 'type': content_type})

            except Exception as e:
                logger.warning(f"Failed to extract content from {filename}: {e}")
                extracted_parts.append(f"[File: {filename} — extraction failed: {str(e)[:100]}]")
                self._attachment_results.append({'filename': filename, 'status': 'failed', 'type': ext or 'unknown'})

        return '\n\n'.join(extracted_parts) if extracted_parts else ''

    def _build_attachment_acknowledgment(self, attachment_results: List[Dict] = None) -> str:
        results = attachment_results or getattr(self, '_attachment_results', None)
        if not results:
            return ''

        read_files = [r for r in results if r['status'] == 'read']
        failed_files = [r for r in results if r['status'] == 'failed']
        empty_files = [r for r in results if r['status'] == 'empty']
        unsupported_files = [r for r in results if r['status'] == 'unsupported']

        parts = []

        if read_files:
            if len(read_files) == 1:
                f = read_files[0]
                if f['type'] == 'image':
                    parts.append(f"I have reviewed the attached screenshot ({f['filename']}) and used it to inform my analysis.")
                elif f['type'] == 'document':
                    parts.append(f"I have read the attached document ({f['filename']}) and used its contents to inform my analysis.")
                else:
                    parts.append(f"I have read the attached file ({f['filename']}) and used its contents to inform my analysis.")
            else:
                names = ', '.join(f['filename'] for f in read_files)
                parts.append(f"I have reviewed the attached files ({names}) and used their contents to inform my analysis.")

        problem_files = failed_files + empty_files + unsupported_files
        if problem_files:
            for f in problem_files:
                if f['status'] == 'failed':
                    parts.append(f"I was unable to read the attached file ({f['filename']}). If it contains important details, please include that information in your reply.")
                elif f['status'] == 'empty':
                    parts.append(f"The attached file ({f['filename']}) appears to be empty. If this was unintentional, please re-attach it in your reply.")
                elif f['status'] == 'unsupported':
                    parts.append(f"The attached file ({f['filename']}) is in a format I cannot read directly. If it contains important details, please describe them in your reply or attach it in a different format (e.g., PDF, image, or text).")

        return '\n'.join(parts)

    def _extract_pdf_text(self, data: bytes) -> str:
        import tempfile
        try:
            import fitz
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=True) as tmp:
                tmp.write(data)
                tmp.flush()
                doc = fitz.open(tmp.name)
                text_parts = []
                for page in doc:
                    page_text = page.get_text("text")
                    if page_text:
                        text_parts.append(page_text)
                doc.close()
                return '\n'.join(text_parts)
        except Exception as e:
            logger.warning(f"PyMuPDF PDF extraction failed: {e}")

        try:
            import PyPDF2
            import io
            reader = PyPDF2.PdfReader(io.BytesIO(data))
            text_parts = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return '\n'.join(text_parts)
        except Exception as e:
            logger.warning(f"PyPDF2 PDF extraction failed: {e}")
            return ''

    def _extract_docx_text(self, data: bytes, ext: str) -> str:
        import io
        if ext == 'docx':
            try:
                import docx
                doc = docx.Document(io.BytesIO(data))
                return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception as e:
                logger.warning(f"DOCX extraction failed: {e}")
                return ''
        elif ext == 'doc':
            import tempfile, subprocess
            try:
                with tempfile.NamedTemporaryFile(suffix='.doc', delete=True) as tmp:
                    tmp.write(data)
                    tmp.flush()
                    result = subprocess.run(
                        ['antiword', tmp.name], capture_output=True, text=True, timeout=15
                    )
                    if result.returncode == 0:
                        return result.stdout.strip()
            except Exception as e:
                logger.warning(f"DOC extraction via antiword failed: {e}")
            return ''
        return ''

    def _describe_image(self, data: bytes, content_type: str, filename: str) -> str:
        if not self.openai_client:
            return f"[Image attached: {filename} — AI vision not available]"

        import base64
        b64 = base64.b64encode(data).decode('utf-8')
        mime = content_type if content_type.startswith('image/') else 'image/png'
        logger.info(f"🖼️ Sending {filename} ({len(data)} bytes, {mime}) to vision...")

        max_attempts = 2
        vision_models = ['o4-mini', 'gpt-5']
        for attempt in range(1, max_attempts + 1):
            model = vision_models[attempt - 1] if attempt <= len(vision_models) else vision_models[-1]
            try:
                response = self.openai_client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            'role': 'user',
                            'content': [
                                {
                                    'type': 'text',
                                    'text': (
                                        'This image was attached to an internal ATS (Bullhorn) support ticket. '
                                        'Describe what you see in detail — focus on any error messages, field values, '
                                        'record IDs, status values, screen names, or other information that would help '
                                        'diagnose and resolve the issue. Be specific and factual.'
                                    ),
                                },
                                {
                                    'type': 'image_url',
                                    'image_url': {'url': f"data:{mime};base64,{b64}", 'detail': 'high'},
                                },
                            ],
                        }
                    ],
                    max_completion_tokens=1024,
                )
                content = None
                if response.choices and response.choices[0].message:
                    content = response.choices[0].message.content
                description = (content or '').strip()
                if description:
                    logger.info(f"🖼️ Vision success for {filename} ({model}, attempt {attempt}): {len(description)} chars")
                    return description
                else:
                    refusal = getattr(response.choices[0].message, 'refusal', None) if response.choices else None
                    logger.warning(f"🖼️ Vision returned empty for {filename} ({model}, attempt {attempt}), refusal={refusal}")
                    if attempt < max_attempts:
                        import time
                        time.sleep(2)
                        continue
                    return f"[Image attached: {filename} — vision analysis returned empty]"
            except Exception as e:
                logger.warning(f"Vision failed for {filename} ({model}, attempt {attempt}): {e}")
                if attempt < max_attempts:
                    import time
                    time.sleep(2)
                    continue
                return f"[Image attached: {filename} — vision analysis failed]"
        return f"[Image attached: {filename} — vision analysis failed]"

    def _get_stakeholder_emails(self, ticket) -> List[str]:
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

    def _generate_understanding(self, ticket, attachment_content: str = '') -> Optional[str]:
        if not self.openai_client:
            return None

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)

        attachment_section = ''
        if attachment_content:
            attachment_section = f"""

Attached Files Content:
{attachment_content}

IMPORTANT: The user attached files to this ticket. Use the extracted content above as additional context
when analyzing the issue. If the attachments contain screenshots, the image descriptions will help you
understand what the user is seeing. If documents are attached, their text content is included above."""

        prompt = f"""You are Scout Support, an AI assistant for internal ATS (Bullhorn) support issues.

A user has submitted a support ticket. Analyze the issue and provide a clear, concise summary of your understanding.

Ticket Details:
- Category: {category_label}
- Subject: {ticket.subject}
- Priority: {ticket.priority}
- Submitted by: {ticket.submitter_name} ({ticket.submitter_email})
- Department: {ticket.submitter_department or 'Not specified'}

User's Description:
{ticket.description}{attachment_section}

Important: Determine not just whether you can fix this, but also whether there might be deeper
underlying issues. Many ATS problems have both an immediate fix AND a root cause that may need
the Bullhorn support team to investigate (e.g., workflow automations overriding manual changes,
field validation rules, permission configurations, data sync issues).

Respond with a JSON object:
{{
    "understanding": "A clear summary of what the user's issue is, written back to the user for confirmation. Use plain, non-technical language.",
    "clarification_needed": true/false,
    "clarification_questions": ["question 1", "question 2"],
    "can_resolve_autonomously": true/false,
    "confidence_level": "high/medium/low",
    "resolution_type": "full/partial/escalate",
    "resolution_approach": "Brief description of how Scout Support could resolve this",
    "proposed_solution_user": "A simple, plain-language explanation of what you will do to fix the issue, written for a non-technical user. Example: 'I will update the candidate status from Online Applicant to New Lead in the system and verify that the change sticks.' Do NOT mention APIs, endpoints, entity IDs, POST/GET requests, or any technical implementation details. Leave empty string if clarification is needed first.",
    "proposed_solution_admin": "The full technical details of the fix for the administrator. Include API endpoints, entity types, entity IDs, field names, values, and verification steps. Leave empty string if clarification is needed first.",
    "execution_steps": [],
    "requires_bullhorn_api": true/false,
    "affected_entities": ["candidate", "job", "placement", etc.],
    "underlying_concerns_user": "If there might be deeper issues, explain them in simple terms for the user. Example: 'There may be an automated process in the system that is overriding your manual changes, which could cause this to happen again.' Empty string if no concerns.",
    "underlying_concerns_admin": "Technical details of the underlying concerns for the administrator. Include specifics about workflow automations, field validation rules, permission configurations, etc. Empty string if no concerns.",
    "escalation_reason": "If confidence_level is low or can_resolve_autonomously is false, explain why this should be escalated to a human"
}}

execution_steps format — populate this array with the specific Bullhorn API actions to execute.
Supported actions:
1. update_entity — Update any field on any Bullhorn entity:
   {{"action": "update_entity", "entity_type": "Candidate", "entity_id": 4649182, "field": "status", "new_value": "New Lead", "description": "Change candidate status to New Lead"}}
   Supported entity_types: Candidate, JobOrder, Placement, JobSubmission, ClientContact, ClientCorporation, Lead, Opportunity, Note, Sendout, Appointment, Task.
2. create_note — Add a note to a candidate record:
   {{"action": "create_note", "entity_id": 4649182, "note_text": "Status corrected per support ticket SS-2026-0001", "note_action": "Scout Support", "description": "Add audit note to candidate"}}
3. create_submission — Submit a candidate to a job:
   {{"action": "create_submission", "candidate_id": 4649182, "job_id": 34500, "source": "Scout Support", "description": "Submit candidate to job"}}
4. get_entity — Read an entity to verify data:
   {{"action": "get_entity", "entity_type": "Candidate", "entity_id": 4649182, "fields": "id,status,firstName,lastName", "description": "Verify candidate record"}}
5. search_entity — Search for entities:
   {{"action": "search_entity", "entity_type": "Candidate", "query": "email:user@example.com", "description": "Find candidate by email"}}
6. remove_from_tearsheet — Remove a job from a tearsheet:
   {{"action": "remove_from_tearsheet", "tearsheet_id": 100, "job_id": 34500, "description": "Remove job from tearsheet"}}
7. delete_entity — Soft-delete (default) or hard-delete a record:
   {{"action": "delete_entity", "entity_type": "Candidate", "entity_id": 12345, "soft_delete": true, "description": "Soft-delete duplicate candidate record"}}
8. bulk_update — Update the same field(s) across multiple records at once:
   {{"action": "bulk_update", "entity_type": "Candidate", "entity_ids": [111, 222, 333], "update_data": {{"status": "Inactive"}}, "description": "Set 3 candidates to Inactive"}}
9. bulk_delete — Soft-delete (default) or hard-delete multiple records at once:
   {{"action": "bulk_delete", "entity_type": "Note", "entity_ids": [444, 555], "soft_delete": true, "description": "Remove duplicate notes"}}
10. create_entity — Create a new Bullhorn entity:
    {{"action": "create_entity", "entity_type": "Note", "entity_data": {{"personReference": {{"id": 12345}}, "action": "Scout Support", "comments": "Status corrected"}}, "description": "Create audit note"}}
11. add_association — Link entities via a to-many association field (e.g., add categories to a job):
    {{"action": "add_association", "entity_type": "JobOrder", "entity_id": 34500, "association_field": "categories", "associated_ids": [10, 20], "description": "Add categories to job"}}
12. remove_association — Unlink entities from a to-many association field:
    {{"action": "remove_association", "entity_type": "JobOrder", "entity_id": 34500, "association_field": "categories", "associated_ids": [10], "description": "Remove category from job"}}
13. add_to_tearsheet — Add a job or candidate to a tearsheet:
    {{"action": "add_to_tearsheet", "tearsheet_id": 100, "job_id": 34500, "description": "Add job to tearsheet"}}
    {{"action": "add_to_tearsheet", "tearsheet_id": 100, "candidate_id": 4649182, "description": "Add candidate to tearsheet"}}
14. query_entity — Query entities using Bullhorn Query Language (BQL WHERE clause):
    {{"action": "query_entity", "entity_type": "Candidate", "where": "status='Active' AND owner.id=12345", "fields": "id,firstName,lastName,status", "count": 50, "description": "Find active candidates owned by user"}}
15. get_associations — Get related entities via a to-many field:
    {{"action": "get_associations", "entity_type": "Candidate", "entity_id": 4649182, "association_field": "submissions", "fields": "id,status,jobOrder(id,title)", "description": "Get candidate's job submissions"}}
16. get_files — List all files/attachments on a record:
    {{"action": "get_files", "entity_type": "Candidate", "entity_id": 4649182, "description": "List candidate's files"}}
17. delete_file — Remove a specific file/attachment from a record:
    {{"action": "delete_file", "entity_type": "Candidate", "entity_id": 4649182, "file_id": 999, "description": "Delete duplicate resume file"}}

Supported entity_types for all actions: Candidate, JobOrder, Placement, JobSubmission, ClientContact, ClientCorporation, Lead, Opportunity, Note, Sendout, Appointment, Task, Tearsheet, CorporateUser, CandidateEducation, CandidateWorkHistory, CandidateReference, Skill, Category, BusinessSector, PlacementChangeRequest.

Always include entity IDs from the user's ticket when available. Use the actual values mentioned in the issue description.

resolution_type guide:
- "full": You can fully resolve this issue with no concerns about deeper problems.
- "partial": You can fix the immediate problem, but there may be underlying issues that need human investigation or Bullhorn support involvement. Always populate both underlying_concerns fields when using this.
- "escalate": This is completely outside Scout Support's capability and must be escalated."""

        for attempt in range(2):
            try:
                response = self.openai_client.chat.completions.create(
                    model='gpt-5',
                    messages=[
                        {'role': 'system', 'content': 'You are Scout Support, an expert AI assistant for Bullhorn ATS issues. You help internal users resolve their ATS problems. Respond only in valid JSON.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    max_completion_tokens=4096,
                    response_format={'type': 'json_object'},
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    finish_reason = response.choices[0].finish_reason if response.choices else 'unknown'
                    logger.warning(f"AI understanding returned empty content (attempt {attempt+1}/2, finish_reason={finish_reason})")
                    if attempt == 0:
                        continue
                    return None
                parsed = json.loads(content.strip())
                return json.dumps(parsed)
            except json.JSONDecodeError as e:
                logger.error(f"AI understanding JSON parse failed (attempt {attempt+1}/2): {e}")
                if attempt == 0:
                    continue
                return None
            except Exception as e:
                logger.error(f"AI understanding generation failed (attempt {attempt+1}/2): {e}")
                if attempt == 0:
                    continue
                return None
        return None

    MAX_CLARIFICATION_ROUNDS = 3

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

    def _analyze_clarification(self, ticket, reply_body: str, attachment_content: str = '') -> Optional[str]:
        if not self.openai_client:
            return None

        from extensions import db as _db
        conversations = ticket.conversations.order_by(
            _db.text('created_at ASC')
        ).all()

        history = []
        for conv in conversations:
            history.append(f"[{conv.direction.upper()}] {conv.sender_email}: {conv.body[:10000]}")

        attachment_section = ''
        if attachment_content:
            attachment_section = f"""

Attached Files in Latest Reply:
{attachment_content}

IMPORTANT: The user included attachments with their reply. Use the extracted content above as additional
context. Screenshots may show error messages, field values, or Bullhorn screens that help diagnose the issue."""

        prompt = f"""You are Scout Support. You've been working on ticket {ticket.ticket_number}.

Original issue: {ticket.subject}
Category: {CATEGORY_LABELS.get(ticket.category, ticket.category)}

Full conversation history (oldest first):
{chr(10).join(history)}

Latest reply from user (may include quoted text with inline answers):
{reply_body}{attachment_section}

Current AI understanding: {ticket.ai_understanding or 'Not yet established'}

Analyze whether you now fully understand the issue and can propose a solution.

CRITICAL — Do NOT re-ask questions the user has already answered:
- Read the FULL conversation history carefully, including quoted text and inline replies.
- Users often reply by inserting answers directly below each question in the quoted email.
- If the user has answered a question (even partially), acknowledge that answer and do NOT ask it again.
- Only ask NEW clarification questions about genuinely missing information.

Important: Consider whether there might be deeper underlying issues beyond the immediate fix.
Many ATS problems have both an immediate resolution AND a root cause that may need the
Bullhorn support team to investigate (workflow automations, field validation rules, permission
configurations, data sync issues, etc.).

Respond with JSON:
{{
    "fully_understood": true/false,
    "updated_understanding": "Your current understanding of the full issue",
    "proposed_solution_user": "If fully understood, describe the fix in simple, plain language for the user. Example: 'I will update the candidate status to New Lead and monitor it to make sure it stays.' Do NOT mention APIs, endpoints, entity IDs, or technical implementation details. Empty string if not fully understood.",
    "proposed_solution_admin": "If fully understood, describe the full technical fix for the administrator. Include API endpoints, entity types, entity IDs, field names, values, and verification steps. Empty string if not fully understood.",
    "follow_up": "If not fully understood, your next clarification question to the user",
    "can_execute": true/false,
    "execution_steps": [],
    "resolution_type": "full/partial/escalate",
    "underlying_concerns_user": "If there might be deeper issues, explain in simple terms for the user. Empty string if no concerns.",
    "underlying_concerns_admin": "Technical details of underlying concerns for the administrator. Empty string if no concerns."
}}

execution_steps format — populate this array with the specific Bullhorn API actions to execute.
Supported actions:
1. update_entity — Update any field on any Bullhorn entity:
   {{"action": "update_entity", "entity_type": "Candidate", "entity_id": 4649182, "field": "status", "new_value": "New Lead", "description": "Change candidate status to New Lead"}}
   Supported entity_types: Candidate, JobOrder, Placement, JobSubmission, ClientContact, ClientCorporation, Lead, Opportunity, Note, Sendout, Appointment, Task.
2. create_note — Add a note to a candidate record:
   {{"action": "create_note", "entity_id": 4649182, "note_text": "Status corrected per support ticket", "note_action": "Scout Support", "description": "Add audit note"}}
3. create_submission — Submit a candidate to a job:
   {{"action": "create_submission", "candidate_id": 4649182, "job_id": 34500, "source": "Scout Support", "description": "Submit candidate to job"}}
4. get_entity — Read an entity to verify data:
   {{"action": "get_entity", "entity_type": "Candidate", "entity_id": 4649182, "fields": "id,status", "description": "Verify candidate record"}}
5. search_entity — Search for entities:
   {{"action": "search_entity", "entity_type": "Candidate", "query": "email:user@example.com", "description": "Find candidate by email"}}
6. remove_from_tearsheet — Remove a job from a tearsheet:
   {{"action": "remove_from_tearsheet", "tearsheet_id": 100, "job_id": 34500, "description": "Remove job from tearsheet"}}
7. delete_entity — Soft-delete or hard-delete a record:
   {{"action": "delete_entity", "entity_type": "Candidate", "entity_id": 12345, "soft_delete": true, "description": "Soft-delete duplicate candidate"}}
8. bulk_update — Update the same field(s) across multiple records:
   {{"action": "bulk_update", "entity_type": "Candidate", "entity_ids": [111, 222, 333], "update_data": {{"status": "Inactive"}}, "description": "Set 3 candidates to Inactive"}}
9. bulk_delete — Delete multiple records at once:
   {{"action": "bulk_delete", "entity_type": "Note", "entity_ids": [444, 555], "soft_delete": true, "description": "Remove duplicate notes"}}
10. create_entity — Create a new Bullhorn entity:
    {{"action": "create_entity", "entity_type": "Note", "entity_data": {{"personReference": {{"id": 12345}}, "action": "Scout Support", "comments": "Audit note"}}, "description": "Create audit note"}}
11. add_association — Link entities via association field:
    {{"action": "add_association", "entity_type": "JobOrder", "entity_id": 34500, "association_field": "categories", "associated_ids": [10, 20], "description": "Add categories to job"}}
12. remove_association — Unlink entities from association field:
    {{"action": "remove_association", "entity_type": "JobOrder", "entity_id": 34500, "association_field": "categories", "associated_ids": [10], "description": "Remove category from job"}}
13. add_to_tearsheet — Add job or candidate to tearsheet:
    {{"action": "add_to_tearsheet", "tearsheet_id": 100, "job_id": 34500, "description": "Add job to tearsheet"}}
14. query_entity — Query using BQL WHERE clause:
    {{"action": "query_entity", "entity_type": "Candidate", "where": "status='Active' AND owner.id=12345", "description": "Find active candidates"}}
15. get_associations — Get related entities via to-many field:
    {{"action": "get_associations", "entity_type": "Candidate", "entity_id": 4649182, "association_field": "submissions", "description": "Get submissions"}}
16. get_files — List files/attachments on a record:
    {{"action": "get_files", "entity_type": "Candidate", "entity_id": 4649182, "description": "List files"}}
17. delete_file — Remove a file/attachment:
    {{"action": "delete_file", "entity_type": "Candidate", "entity_id": 4649182, "file_id": 999, "description": "Delete file"}}

Supported entity_types: Candidate, JobOrder, Placement, JobSubmission, ClientContact, ClientCorporation, Lead, Opportunity, Note, Sendout, Appointment, Task, Tearsheet, CorporateUser, CandidateEducation, CandidateWorkHistory, CandidateReference, Skill, Category, BusinessSector, PlacementChangeRequest.

Always include entity IDs from the conversation when available.

resolution_type guide:
- "full": You can fully resolve this issue with no concerns about deeper problems.
- "partial": You can fix the immediate problem, but there may be underlying issues. Always populate both underlying_concerns fields.
- "escalate": Completely outside Scout Support's capability."""

        for attempt in range(2):
            try:
                response = self.openai_client.chat.completions.create(
                    model='gpt-5',
                    messages=[
                        {'role': 'system', 'content': 'You are Scout Support, an expert AI assistant for Bullhorn ATS issues. Respond only in valid JSON.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    max_completion_tokens=4096,
                    response_format={'type': 'json_object'},
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    finish_reason = response.choices[0].finish_reason if response.choices else 'unknown'
                    logger.warning(f"Clarification analysis returned empty content (attempt {attempt+1}/2, finish_reason={finish_reason})")
                    if attempt == 0:
                        continue
                    return None
                return content.strip()
            except Exception as e:
                logger.error(f"Clarification analysis failed (attempt {attempt+1}/2): {e}")
                if attempt == 0:
                    continue
                return None
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

        approve_keywords = ['yes', 'approve', 'confirmed', 'go ahead', 'proceed', 'looks good', 'agree', 'correct', 'that works']
        if any(kw in text_lower for kw in approve_keywords):
            return 'approved'

        reject_keywords = ['no thanks', 'cancel', 'close this', 'reject', 'decline', 'stop', 'nevermind']
        if any(kw in text_lower for kw in reject_keywords):
            return 'rejected'

        change_keywords = ['change', 'modify', 'adjust', 'instead', 'actually', 'however']
        if any(kw in text_lower for kw in change_keywords):
            return 'needs_changes'
        return 'needs_changes'

    def _handle_admin_question(self, ticket, admin_message: str):
        from extensions import db
        from models import SupportConversation

        conversations = ticket.conversations.order_by(
            db.text('created_at ASC')
        ).all()

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
{chr(10).join(history)}

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
            answer = response.choices[0].message.content.strip()
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
                "\"Sure, do it\", \"Let's just ensure the field is changed... you can go ahead\"). "
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
        ]
        if any(phrase in text_lower for phrase in approve_phrases):
            return 'approved'

        approve_exact_starts = ['approved', 'approve', 'yes', 'go ahead', 'proceed', 'authorized', 'green light', 'looks good']
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
        requires_bullhorn = (
            solution_data.get('requires_bullhorn', False)
            or understanding.get('requires_bullhorn_api', False)
        )

        if requires_bullhorn and self.bullhorn_service and execution_steps:
            proof_items = self._execute_bullhorn_actions(ticket, solution_data)
        elif requires_bullhorn and not self.bullhorn_service:
            logger.warning(f"Ticket {ticket.ticket_number} requires Bullhorn but service unavailable — re-initializing")
            self.bullhorn_service = self._init_bullhorn()
            if self.bullhorn_service and execution_steps:
                proof_items = self._execute_bullhorn_actions(ticket, solution_data)
            else:
                proof_items = [{'step': 'Bullhorn execution failed', 'result': 'Could not connect to Bullhorn — manual resolution required'}]
        elif requires_bullhorn and not execution_steps:
            logger.warning(f"Ticket {ticket.ticket_number} requires Bullhorn but no execution steps defined")
            proof_items = [{'step': 'No execution steps defined', 'result': 'Manual resolution required — AI did not generate actionable steps'}]
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
            action_type = step.get('action', 'unknown')
            entity_type = step.get('entity_type', 'Candidate')
            entity_id = step.get('entity_id')
            field = step.get('field')
            new_value = step.get('new_value')
            desc = step.get('description', 'Unknown step')

            action = SupportAction(
                ticket_id=ticket.id,
                action_type=action_type,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id else None,
                field_name=field,
                old_value=step.get('old_value'),
                new_value=str(new_value) if new_value is not None else None,
                summary=desc,
            )

            try:
                if action_type == 'update_entity' and entity_id and field:
                    result = self._exec_update_entity(action, entity_type, int(entity_id), field, new_value)
                    proof_items.append(result)

                elif action_type == 'create_note' and entity_id:
                    result = self._exec_create_note(action, int(entity_id), step)
                    proof_items.append(result)

                elif action_type == 'create_submission':
                    result = self._exec_create_submission(action, step)
                    proof_items.append(result)

                elif action_type == 'search_entity':
                    result = self._exec_search_entity(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'get_entity' and entity_id:
                    result = self._exec_get_entity(action, entity_type, int(entity_id), step)
                    proof_items.append(result)

                elif action_type == 'remove_from_tearsheet':
                    result = self._exec_remove_from_tearsheet(action, step)
                    proof_items.append(result)

                elif action_type == 'delete_entity' and entity_id:
                    result = self._exec_delete_entity(action, entity_type, int(entity_id), step)
                    proof_items.append(result)

                elif action_type == 'bulk_update':
                    result = self._exec_bulk_update(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'bulk_delete':
                    result = self._exec_bulk_delete(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'create_entity':
                    result = self._exec_create_entity(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'add_association':
                    result = self._exec_add_association(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'remove_association':
                    result = self._exec_remove_association(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'add_to_tearsheet':
                    result = self._exec_add_to_tearsheet(action, step)
                    proof_items.append(result)

                elif action_type == 'query_entity':
                    result = self._exec_query_entity(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'get_associations':
                    result = self._exec_get_associations(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'get_files':
                    result = self._exec_get_files(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'delete_file':
                    result = self._exec_delete_file(action, entity_type, step)
                    proof_items.append(result)

                else:
                    action.success = True
                    action.summary = desc
                    proof_items.append({'step': desc, 'result': 'Guidance provided'})

            except Exception as e:
                action.success = False
                action.error_message = str(e)
                proof_items.append({'step': desc, 'result': f'Failed: {str(e)}'})
                logger.error(f"Bullhorn action failed for ticket {ticket.ticket_number}: {e}")

            db.session.add(action)

        db.session.commit()
        return proof_items

    def _exec_update_entity(self, action, entity_type: str, entity_id: int, field: str, new_value) -> Dict:
        current = self.bullhorn_service.get_entity(entity_type, entity_id)
        if current:
            action.old_value = str(current.get(field, ''))

        success = self.bullhorn_service.update_entity(entity_type, entity_id, {field: new_value})
        if success:
            verified = self.bullhorn_service.get_entity(entity_type, entity_id, fields=f'id,{field}')
            verified_value = verified.get(field, 'unknown') if verified else 'unknown'
            action.success = True
            logger.info(f"✅ Bullhorn update: {entity_type} #{entity_id} {field}: {action.old_value} → {new_value} (verified: {verified_value})")
            return {
                'step': f"Updated {entity_type} #{entity_id}: {field}",
                'old_value': action.old_value,
                'new_value': str(new_value),
                'verified_value': str(verified_value),
                'result': 'Success',
            }
        else:
            action.success = False
            action.error_message = 'Bullhorn API returned failure'
            logger.error(f"❌ Bullhorn update failed: {entity_type} #{entity_id} {field}")
            return {'step': f"Update {entity_type} #{entity_id}: {field}", 'result': 'Failed — API returned error'}

    def _exec_create_note(self, action, candidate_id: int, step: dict) -> Dict:
        note_text = step.get('note_text', step.get('new_value', ''))
        note_action = step.get('note_action', 'Scout Support')
        if not note_text:
            action.success = False
            action.error_message = 'Missing note_text'
            return {'step': step.get('description', 'Create note'), 'result': 'Failed — no note text provided'}

        note_id = self.bullhorn_service.create_candidate_note(candidate_id, note_text, action=note_action)
        if note_id:
            action.success = True
            action.new_value = f"Note #{note_id}"
            logger.info(f"✅ Created note #{note_id} on Candidate #{candidate_id}")
            return {'step': f"Created note on Candidate #{candidate_id}", 'note_id': note_id, 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Note creation failed'
            return {'step': f"Create note on Candidate #{candidate_id}", 'result': 'Failed — API error'}

    def _exec_create_submission(self, action, step: dict) -> Dict:
        candidate_id = step.get('candidate_id')
        job_id = step.get('job_id')
        source = step.get('source', 'Scout Support')
        if not candidate_id or not job_id:
            action.success = False
            action.error_message = 'Missing candidate_id or job_id'
            return {'step': step.get('description', 'Create submission'), 'result': 'Failed — missing candidate or job ID'}

        submission_id = self.bullhorn_service.create_job_submission(int(candidate_id), int(job_id), source=source)
        if submission_id:
            action.success = True
            action.new_value = f"Submission #{submission_id}"
            logger.info(f"✅ Created submission #{submission_id}: Candidate #{candidate_id} → Job #{job_id}")
            return {'step': f"Submitted Candidate #{candidate_id} to Job #{job_id}", 'submission_id': submission_id, 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Submission creation failed'
            return {'step': step.get('description', 'Create submission'), 'result': 'Failed — API error'}

    def _exec_search_entity(self, action, entity_type: str, step: dict) -> Dict:
        query = step.get('query', '')
        if not query:
            action.success = False
            action.error_message = 'Missing search query'
            return {'step': step.get('description', 'Search'), 'result': 'Failed — no query provided'}

        results = self.bullhorn_service.search_entity(entity_type, query, count=step.get('count', 10))
        action.success = True
        action.new_value = f"{len(results)} results"
        return {'step': f"Searched {entity_type}: {query}", 'result_count': len(results), 'results': results[:5], 'result': 'Success'}

    def _exec_get_entity(self, action, entity_type: str, entity_id: int, step: dict) -> Dict:
        fields = step.get('fields')
        data = self.bullhorn_service.get_entity(entity_type, entity_id, fields=fields)
        if data:
            action.success = True
            action.new_value = json.dumps(data)[:500]
            return {'step': f"Retrieved {entity_type} #{entity_id}", 'data': data, 'result': 'Success'}
        else:
            action.success = False
            action.error_message = f'{entity_type} #{entity_id} not found'
            return {'step': f"Get {entity_type} #{entity_id}", 'result': 'Failed — entity not found'}

    def _exec_remove_from_tearsheet(self, action, step: dict) -> Dict:
        tearsheet_id = step.get('tearsheet_id')
        job_id = step.get('job_id')
        if not tearsheet_id or not job_id:
            action.success = False
            action.error_message = 'Missing tearsheet_id or job_id'
            return {'step': step.get('description', 'Remove from tearsheet'), 'result': 'Failed — missing IDs'}

        success = self.bullhorn_service.remove_job_from_tearsheet(int(tearsheet_id), int(job_id))
        if success:
            action.success = True
            logger.info(f"✅ Removed Job #{job_id} from Tearsheet #{tearsheet_id}")
            return {'step': f"Removed Job #{job_id} from Tearsheet #{tearsheet_id}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Remove from tearsheet failed'
            return {'step': step.get('description', 'Remove from tearsheet'), 'result': 'Failed — API error'}

    def _exec_delete_entity(self, action, entity_type: str, entity_id: int, step: dict) -> Dict:
        soft = step.get('soft_delete', True)
        current = self.bullhorn_service.get_entity(entity_type, entity_id)
        if current:
            action.old_value = json.dumps({k: v for k, v in current.items() if k in ('id', 'status', 'isDeleted', 'firstName', 'lastName', 'title', 'name')})[:500]

        success = self.bullhorn_service.delete_entity(entity_type, entity_id, soft_delete=soft)
        mode = 'soft-deleted' if soft else 'hard-deleted'
        if success:
            action.success = True
            logger.info(f"✅ {mode.title()} {entity_type} #{entity_id}")
            return {'step': f"{mode.title()} {entity_type} #{entity_id}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = f'{mode.title()} failed'
            return {'step': f"Delete {entity_type} #{entity_id}", 'result': f'Failed — {mode} error'}

    def _exec_bulk_update(self, action, entity_type: str, step: dict) -> Dict:
        entity_ids = step.get('entity_ids', [])
        update_data = step.get('update_data', {})
        if not entity_ids or not update_data:
            action.success = False
            action.error_message = 'Missing entity_ids or update_data'
            return {'step': step.get('description', 'Bulk update'), 'result': 'Failed — missing IDs or data'}

        int_ids = [int(eid) for eid in entity_ids]
        results = self.bullhorn_service.bulk_update_entities(entity_type, int_ids, update_data)
        succeeded = sum(1 for v in results.values() if v)
        failed = len(int_ids) - succeeded
        action.success = failed == 0
        action.new_value = f"{succeeded}/{len(int_ids)} succeeded"
        if failed > 0:
            action.error_message = f"{failed} updates failed"
        logger.info(f"{'✅' if failed == 0 else '⚠️'} Bulk update {entity_type}: {succeeded}/{len(int_ids)} succeeded")
        return {
            'step': f"Bulk updated {entity_type}: {list(update_data.keys())}",
            'total': len(int_ids), 'succeeded': succeeded, 'failed': failed,
            'result': 'Success' if failed == 0 else f'Partial — {failed} failed',
        }

    def _exec_bulk_delete(self, action, entity_type: str, step: dict) -> Dict:
        entity_ids = step.get('entity_ids', [])
        soft = step.get('soft_delete', True)
        if not entity_ids:
            action.success = False
            action.error_message = 'Missing entity_ids'
            return {'step': step.get('description', 'Bulk delete'), 'result': 'Failed — no IDs provided'}

        int_ids = [int(eid) for eid in entity_ids]
        results = self.bullhorn_service.bulk_delete_entities(entity_type, int_ids, soft_delete=soft)
        succeeded = sum(1 for v in results.values() if v)
        failed = len(int_ids) - succeeded
        mode = 'soft-deleted' if soft else 'hard-deleted'
        action.success = failed == 0
        action.new_value = f"{succeeded}/{len(int_ids)} {mode}"
        if failed > 0:
            action.error_message = f"{failed} deletes failed"
        logger.info(f"{'✅' if failed == 0 else '⚠️'} Bulk {mode} {entity_type}: {succeeded}/{len(int_ids)}")
        return {
            'step': f"Bulk {mode} {entity_type}",
            'total': len(int_ids), 'succeeded': succeeded, 'failed': failed,
            'result': 'Success' if failed == 0 else f'Partial — {failed} failed',
        }

    def _exec_create_entity(self, action, entity_type: str, step: dict) -> Dict:
        entity_data = step.get('entity_data', {})
        if not entity_data:
            action.success = False
            action.error_message = 'Missing entity_data'
            return {'step': step.get('description', 'Create entity'), 'result': 'Failed — no data provided'}

        new_id = self.bullhorn_service.create_entity(entity_type, entity_data)
        if new_id:
            action.success = True
            action.new_value = f"{entity_type} #{new_id}"
            logger.info(f"✅ Created {entity_type} #{new_id}")
            return {'step': f"Created {entity_type} #{new_id}", 'entity_id': new_id, 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Entity creation failed'
            return {'step': step.get('description', 'Create entity'), 'result': 'Failed — API error'}

    def _exec_add_association(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        association_field = step.get('association_field')
        associated_ids = step.get('associated_ids', [])
        if not entity_id or not association_field or not associated_ids:
            action.success = False
            action.error_message = 'Missing entity_id, association_field, or associated_ids'
            return {'step': step.get('description', 'Add association'), 'result': 'Failed — missing parameters'}

        success = self.bullhorn_service.add_entity_to_association(entity_type, int(entity_id), association_field, [int(i) for i in associated_ids])
        if success:
            action.success = True
            logger.info(f"✅ Added {association_field} association on {entity_type} #{entity_id}")
            return {'step': f"Added {association_field} on {entity_type} #{entity_id}: {associated_ids}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Association add failed'
            return {'step': step.get('description', 'Add association'), 'result': 'Failed — API error'}

    def _exec_remove_association(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        association_field = step.get('association_field')
        associated_ids = step.get('associated_ids', [])
        if not entity_id or not association_field or not associated_ids:
            action.success = False
            action.error_message = 'Missing entity_id, association_field, or associated_ids'
            return {'step': step.get('description', 'Remove association'), 'result': 'Failed — missing parameters'}

        success = self.bullhorn_service.remove_entity_from_association(entity_type, int(entity_id), association_field, [int(i) for i in associated_ids])
        if success:
            action.success = True
            logger.info(f"✅ Removed {association_field} association on {entity_type} #{entity_id}")
            return {'step': f"Removed {association_field} on {entity_type} #{entity_id}: {associated_ids}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Association remove failed'
            return {'step': step.get('description', 'Remove association'), 'result': 'Failed — API error'}

    def _exec_add_to_tearsheet(self, action, step: dict) -> Dict:
        tearsheet_id = step.get('tearsheet_id')
        job_id = step.get('job_id')
        candidate_id = step.get('candidate_id')
        if not tearsheet_id:
            action.success = False
            action.error_message = 'Missing tearsheet_id'
            return {'step': step.get('description', 'Add to tearsheet'), 'result': 'Failed — missing tearsheet ID'}

        if job_id:
            success = self.bullhorn_service.add_job_to_tearsheet(int(tearsheet_id), int(job_id))
            label = f"Job #{job_id}"
        elif candidate_id:
            success = self.bullhorn_service.add_candidate_to_tearsheet(int(tearsheet_id), int(candidate_id))
            label = f"Candidate #{candidate_id}"
        else:
            action.success = False
            action.error_message = 'Missing job_id or candidate_id'
            return {'step': step.get('description', 'Add to tearsheet'), 'result': 'Failed — need job_id or candidate_id'}

        if success:
            action.success = True
            logger.info(f"✅ Added {label} to Tearsheet #{tearsheet_id}")
            return {'step': f"Added {label} to Tearsheet #{tearsheet_id}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Add to tearsheet failed'
            return {'step': step.get('description', 'Add to tearsheet'), 'result': 'Failed — API error'}

    def _exec_query_entity(self, action, entity_type: str, step: dict) -> Dict:
        where = step.get('where', '')
        if not where:
            action.success = False
            action.error_message = 'Missing where clause'
            return {'step': step.get('description', 'Query'), 'result': 'Failed — no where clause'}

        results = self.bullhorn_service.query_entities(
            entity_type, where,
            fields=step.get('fields'),
            count=step.get('count', 50),
            order_by=step.get('order_by'),
        )
        action.success = True
        action.new_value = f"{len(results)} results"
        return {'step': f"Queried {entity_type}: {where}", 'result_count': len(results), 'results': results[:10], 'result': 'Success'}

    def _exec_get_associations(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        association_field = step.get('association_field')
        if not entity_id or not association_field:
            action.success = False
            action.error_message = 'Missing entity_id or association_field'
            return {'step': step.get('description', 'Get associations'), 'result': 'Failed — missing parameters'}

        results = self.bullhorn_service.get_entity_associations(
            entity_type, int(entity_id), association_field,
            fields=step.get('fields', 'id'),
            count=step.get('count', 100),
        )
        action.success = True
        action.new_value = f"{len(results)} associations"
        return {'step': f"Retrieved {association_field} on {entity_type} #{entity_id}", 'result_count': len(results), 'results': results[:20], 'result': 'Success'}

    def _exec_get_files(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        if not entity_id:
            action.success = False
            action.error_message = 'Missing entity_id'
            return {'step': step.get('description', 'Get files'), 'result': 'Failed — missing entity ID'}

        files = self.bullhorn_service.get_entity_files(entity_type, int(entity_id))
        action.success = True
        action.new_value = f"{len(files)} files"
        return {'step': f"Retrieved files on {entity_type} #{entity_id}", 'file_count': len(files), 'files': files, 'result': 'Success'}

    def _exec_delete_file(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        file_id = step.get('file_id')
        if not entity_id or not file_id:
            action.success = False
            action.error_message = 'Missing entity_id or file_id'
            return {'step': step.get('description', 'Delete file'), 'result': 'Failed — missing IDs'}

        success = self.bullhorn_service.delete_entity_file(entity_type, int(entity_id), int(file_id))
        if success:
            action.success = True
            logger.info(f"✅ Deleted file #{file_id} from {entity_type} #{entity_id}")
            return {'step': f"Deleted file #{file_id} from {entity_type} #{entity_id}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'File deletion failed'
            return {'step': step.get('description', 'Delete file'), 'result': 'Failed — API error'}

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

    def _escalate_to_admin(self, ticket, reason: str, understanding: str = ''):
        from extensions import db

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

        try:
            solution_data = json.loads(ticket.proposed_solution) if ticket.proposed_solution else {}
        except (json.JSONDecodeError, TypeError):
            solution_data = {}
        resolution_type = solution_data.get('resolution_type', 'full')
        concerns_user = solution_data.get('underlying_concerns_user', '') or solution_data.get('underlying_concerns', '')
        concerns_admin = solution_data.get('underlying_concerns_admin', '') or solution_data.get('underlying_concerns', '')

        user_body_parts = [
            f"Hi {ticket.submitter_name.split()[0] if ticket.submitter_name else 'there'},",
            f"",
            f"Great news — your support ticket **{ticket.ticket_number}** has been resolved.",
            f"",
            f"**What was done:**",
            f"{proof_text}",
        ]

        if concerns_user:
            user_body_parts.extend([
                f"",
                f"**Please Note:** {concerns_user}",
                f"",
                f"If this issue happens again, please submit a new ticket and we'll investigate further.",
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

    def _send_email(self, to_email: str, subject: str, body: str, ticket=None,
                    email_type: str = 'general', cc_email: str = None,
                    cc_emails: Optional[List[str]] = None):
        from extensions import db
        from models import SupportConversation, EmailDeliveryLog

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

            html_body = body.replace('\n', '<br>')

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
            )

            success = result.get('success', False) if isinstance(result, dict) else bool(result)

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
                if not getattr(ticket, 'thread_message_id', None):
                    ticket.thread_message_id = msg_id
                ticket.last_message_id = msg_id
                db.session.commit()

            if success:
                logger.info(f"📧 Scout Support email sent: {subject} → {to_email}")
            else:
                logger.error(f"❌ Scout Support email failed: {subject} → {to_email}")

        except Exception as e:
            logger.error(f"❌ Scout Support email error: {e}")
