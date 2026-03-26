"""
AI Analysis — Understanding generation, clarification analysis, attachment extraction, vision.

Contains:
- _generate_understanding: AI intake analysis of new tickets
- _analyze_clarification: AI analysis of user clarification replies
- _extract_attachment_content: Multi-format attachment text extraction
- _build_attachment_acknowledgment: Human-readable attachment status summary
- _extract_pdf_text: PDF text extraction (PyMuPDF + PyPDF2 fallback)
- _extract_docx_text: DOCX/DOC text extraction
- _describe_image: Vision model image description for screenshots
"""

import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AIAnalysisMixin:
    """AI understanding, clarification analysis, and attachment extraction."""

    def _generate_understanding(self, ticket, attachment_content: str = '') -> Optional[str]:
        if not self.openai_client:
            return None

        from scout_support_service import CATEGORY_LABELS

        category_label = CATEGORY_LABELS.get(ticket.category, ticket.category)

        attachment_section = ''
        if attachment_content:
            attachment_section = f"""

Attached Files Content:
{attachment_content}

IMPORTANT: The user attached files to this ticket. Use the extracted content above as additional context
when analyzing the issue. If the attachments contain screenshots, the image descriptions will help you
understand what the user is seeing. If documents are attached, their text content is included above."""

        knowledge_section = ''
        try:
            from scout_support.knowledge import KnowledgeService
            ks = KnowledgeService()
            knowledge_section = ks.build_knowledge_context(
                ticket.subject, ticket.description, ticket.category
            )
        except Exception as e:
            logger.warning(f"Knowledge retrieval failed for ticket {ticket.ticket_number}: {e}")

        prompt = f"""You are Scout Support, an AI assistant for internal ATS (Bullhorn) support issues.

A user has submitted a support ticket. Analyze the issue and provide a clear, concise summary of your understanding.

Ticket Details:
- Category: {category_label}
- Subject: {ticket.subject}
- Priority: {ticket.priority}
- Submitted by: {ticket.submitter_name} ({ticket.submitter_email})
- Department: {ticket.submitter_department or 'Not specified'}

User's Description:
{ticket.description}{attachment_section}{knowledge_section}

=== CLARIFICATION RULES ===
Only ask clarification questions when you GENUINELY cannot proceed without the answer.
- If the user's description + any screenshots give you enough context to understand the issue,
  set clarification_needed=false and propose a solution immediately.
- Limit clarification questions to a MAXIMUM of 3, and only ask what is truly essential.
- Do NOT ask generic diagnostic questions like "can you reproduce in another browser?" or
  "what time did this happen?" unless that specific information is critical to your resolution.
- Prefer proposing a solution with underlying_concerns over asking clarification questions.
  A partial fix + flagging a potential deeper issue is more helpful than interrogating the user.
- If screenshots are attached, extract the information directly — do NOT ask the user to
  describe what you can already see in the screenshot.

Important: Determine not just whether you can fix this, but also whether there might be deeper
underlying issues. Many ATS problems have both an immediate fix AND a root cause that may need
the Bullhorn support team to investigate (e.g., workflow automations overriding manual changes,
field validation rules, permission configurations, data sync issues).

Respond with a JSON object:
{{
    "understanding": "A clear summary of what the user's issue is, written back to the user for confirmation. Use plain, non-technical language.",
    "clarification_needed": true/false,
    "clarification_questions": ["Only include truly essential questions, max 3. Empty array if clarification_needed is false."],
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
                token_limit = 8192 if attempt == 0 else 12288
                response = self.openai_client.chat.completions.create(
                    model='gpt-5',
                    messages=[
                        {'role': 'system', 'content': 'You are Scout Support, an expert AI assistant for Bullhorn ATS issues. You help internal users resolve their ATS problems. Respond only in valid JSON.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    max_completion_tokens=token_limit,
                    response_format={'type': 'json_object'},
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    finish_reason = response.choices[0].finish_reason if response.choices else 'unknown'
                    logger.warning(f"AI understanding returned empty content (attempt {attempt+1}/2, finish_reason={finish_reason}, token_limit={token_limit})")
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

    def _analyze_clarification(self, ticket, reply_body: str, attachment_content: str = '') -> Optional[str]:
        if not self.openai_client:
            return None

        from scout_support_service import CATEGORY_LABELS
        from extensions import db as _db

        conversations = ticket.conversations.order_by(
            _db.text('created_at ASC')
        ).all()

        history = []
        outbound_questions = []
        for conv in conversations:
            history.append(f"[{conv.direction.upper()}] {conv.sender_email}: {conv.body[:10000]}")
            if conv.direction == 'outbound' and conv.email_type in ('clarification', 'acknowledgment'):
                outbound_questions.append(conv.body[:5000])

        previous_questions_section = ''
        if outbound_questions:
            previous_questions_section = f"""

=== QUESTIONS YOU PREVIOUSLY ASKED (these must be mapped against the user's reply) ===
{chr(10).join(f'--- Email {i+1} ---{chr(10)}{q}' for i, q in enumerate(outbound_questions))}
=== END OF PREVIOUS QUESTIONS ==="""

        attachment_section = ''
        if attachment_content:
            attachment_section = f"""

=== ATTACHMENTS FROM LATEST REPLY ===
{attachment_content}

These attachments are ANSWERS to your questions. Screenshots show the actual Bullhorn screens,
field values, error states, and configuration the user is seeing. Extract specific details from
them: record IDs, field values, error messages, screen names, button states, dropdown selections.
=== END OF ATTACHMENTS ==="""

        knowledge_section = ''
        try:
            from scout_support.knowledge import KnowledgeService
            ks = KnowledgeService()
            knowledge_section = ks.build_knowledge_context(
                ticket.subject, f"{ticket.description}\n{reply_body}", ticket.category
            )
        except Exception as e:
            logger.warning(f"Knowledge retrieval failed during clarification for ticket {ticket.ticket_number}: {e}")

        prompt = f"""You are Scout Support. You've been working on ticket {ticket.ticket_number}.

Original issue: {ticket.subject}
Category: {CATEGORY_LABELS.get(ticket.category, ticket.category)}

Full conversation history (oldest first):
{chr(10).join(history)}
{previous_questions_section}

Latest reply from user:
{reply_body}{attachment_section}{knowledge_section}

Current AI understanding: {ticket.ai_understanding or 'Not yet established'}

=== MANDATORY ANALYSIS STEPS ===
STEP 1 — ANSWER EXTRACTION: Before doing ANYTHING else, go through EACH question you previously
asked and identify the user's answer from their reply text AND from the attachment descriptions.
Users answer questions in different ways:
  - Direct text answers in the reply body
  - Inline answers inserted below quoted questions
  - Screenshots that visually demonstrate the answer (e.g., showing the field value, the error, the screen)
  - Providing context that implicitly answers the question

STEP 2 — GAP ANALYSIS: After extracting all answers, identify what is GENUINELY still unknown.
A question is answered if the user provided ANY relevant information about it, even indirectly
through screenshots or contextual description. Do NOT re-ask a question just because the answer
wasn't in the exact format you expected.

STEP 3 — DECISION: If you have enough information to understand the issue and propose a solution
(even a partial one), set fully_understood=true and propose the solution. Prefer proposing a
solution with underlying_concerns over asking another round of questions. Only set
fully_understood=false if critical information is genuinely missing and you cannot even propose
a partial solution.

CRITICAL RULES:
- NEVER repeat a question the user has already addressed in any form (text, screenshot, or context).
- If screenshots show the exact screen/field/error you asked about, that IS the answer.
- When in doubt, propose a solution rather than asking more questions. Users lose trust when
  AI keeps asking instead of acting.
- If you must ask follow-up questions, they must be NEW and specific — not rephrased versions
  of previous questions.

Important: Consider whether there might be deeper underlying issues beyond the immediate fix.

Respond with JSON:
{{
    "answers_extracted": {{
        "question_1_summary": "answer found (or 'NOT ANSWERED' if genuinely unanswered)",
        "question_2_summary": "answer found (or 'NOT ANSWERED' if genuinely unanswered)"
    }},
    "genuinely_unanswered": ["List ONLY questions that have NO answer anywhere in the reply, screenshots, or context. Empty array if all questions are addressed."],
    "fully_understood": true/false,
    "updated_understanding": "Your current understanding of the full issue, incorporating ALL answers extracted above",
    "proposed_solution_user": "If fully understood, describe the fix in simple, plain language for the user. Example: 'I will update the candidate status to New Lead and monitor it to make sure it stays.' Do NOT mention APIs, endpoints, entity IDs, or technical implementation details. Empty string if not fully understood.",
    "proposed_solution_admin": "If fully understood, describe the full technical fix for the administrator. Include API endpoints, entity types, entity IDs, field names, values, and verification steps. Empty string if not fully understood.",
    "follow_up": "If not fully understood, ask ONLY about items listed in genuinely_unanswered. NEVER repeat a question already covered by answers_extracted.",
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
                    max_completion_tokens=8192,
                    response_format={'type': 'json_object'},
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    finish_reason = response.choices[0].finish_reason if response.choices else 'unknown'
                    logger.warning(f"Clarification analysis returned empty content (attempt {attempt+1}/2, finish_reason={finish_reason})")
                    if attempt == 0:
                        continue
                    return None
                result = content.strip()
                try:
                    parsed_log = json.loads(result)
                    answers = parsed_log.get('answers_extracted', {})
                    unanswered = parsed_log.get('genuinely_unanswered', [])
                    understood = parsed_log.get('fully_understood', False)
                    logger.info(f"🧠 Clarification analysis for {ticket.ticket_number}: fully_understood={understood}, answers_extracted={len(answers)} items, genuinely_unanswered={unanswered}")
                    if answers:
                        for q_key, a_val in answers.items():
                            logger.info(f"   📋 {q_key}: {str(a_val)[:200]}")
                except Exception:
                    pass
                return result
            except Exception as e:
                logger.error(f"Clarification analysis failed (attempt {attempt+1}/2): {e}")
                if attempt == 0:
                    continue
                return None
        return None

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
        import tempfile
        if ext == 'docx' or not ext:
            try:
                import docx
                import io
                doc = docx.Document(io.BytesIO(data))
                return '\n'.join(p.text for p in doc.paragraphs if p.text)
            except Exception as e:
                logger.warning(f"python-docx extraction failed: {e}")
                return ''
        elif ext == 'doc':
            try:
                import subprocess
                with tempfile.NamedTemporaryFile(suffix='.doc', delete=True) as tmp:
                    tmp.write(data)
                    tmp.flush()
                    result = subprocess.run(['antiword', tmp.name], capture_output=True, text=True, timeout=10)
                    if result.returncode == 0 and result.stdout.strip():
                        return result.stdout.strip()
            except Exception as e:
                logger.warning(f"antiword .doc extraction failed: {e}")
            return ''
        return ''

    def _describe_image(self, data: bytes, content_type: str, filename: str) -> str:
        if not self.openai_client:
            return f"[Image attached: {filename} — AI vision not available]"

        import base64
        b64 = base64.b64encode(data).decode('utf-8')
        mime = content_type if content_type.startswith('image/') else 'image/png'
        logger.info(f"🖼️ Sending {filename} ({len(data)} bytes, {mime}) to vision...")

        vision_models = ['gpt-5', 'gpt-5.4']
        for attempt, model in enumerate(vision_models, 1):
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
                    max_completion_tokens=4096,
                )
                content = None
                if response.choices and response.choices[0].message:
                    msg = response.choices[0].message
                    content = msg.content
                    if not content:
                        content = getattr(msg, 'output_text', None)
                description = (content or '').strip()
                if description:
                    logger.info(f"🖼️ Vision success for {filename} ({model}, attempt {attempt}): {len(description)} chars")
                    return description
                else:
                    refusal = getattr(response.choices[0].message, 'refusal', None) if response.choices else None
                    logger.warning(f"🖼️ Vision returned empty for {filename} ({model}, attempt {attempt}), refusal={refusal}, finish_reason={response.choices[0].finish_reason if response.choices else 'N/A'}")
                    if attempt < len(vision_models):
                        import time
                        time.sleep(2)
                        continue
                    return f"[Image attached: {filename} — vision analysis returned empty]"
            except Exception as e:
                logger.warning(f"Vision failed for {filename} ({model}, attempt {attempt}): {e}")
                if attempt < len(vision_models):
                    import time
                    time.sleep(2)
                    continue
                return f"[Image attached: {filename} — vision analysis failed]"
        return f"[Image attached: {filename} — vision analysis failed]"
