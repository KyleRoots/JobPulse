"""
Scout Vetting Service — Conversational AI follow-up for qualified candidates.

Activates after Scout Screening qualifies a candidate. Uses AI to
generate job-specific verification questions and conducts multi-turn email
conversations before recruiter handoff.

Flow:
  1. Screening qualifies candidate → initiate_vetting()
  2. AI generates 3-5 questions based on gaps/match → send_initial_outreach()
  3. Candidate replies → process_candidate_reply() classifies intent, extracts answers
  4. If more info needed → send follow-up; if complete → finalize_vetting()
  5. Follow-up scheduler nudges unresponsive candidates (24h, 48h, then close)

Email routing:
  - Outbound: From scout@myticas.com, Reply-To scout-vetting@parse.lyntrix.ai
  - Inbound: Routed via existing /api/email/inbound handler (address-based dispatch)
  - Threading: Subject token [SV-{session_id}] + In-Reply-To/References headers
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Scout Vetting reply-to address for inbound routing
SCOUT_VETTING_REPLY_TO = 'scout-vetting@parse.lyntrix.ai'

# Sender display name for outbound emails
SCOUT_VETTING_FROM_NAME = 'Scout by Myticas'


class ScoutVettingService:
    """Conversational GPT-5.4 vetting engine."""

    MAX_CONCURRENT_SESSIONS = 3
    STAGGER_MINUTES = 15
    FOLLOWUP_HOURS = [24, 48]  # 24h first follow-up, 48h second, then unresponsive
    MAX_TURNS = 5  # Safety cap per conversation

    def __init__(self, email_service, bullhorn_service=None):
        self.email_service = email_service
        self.bullhorn = bullhorn_service
        self._openai_client = None  # Lazy-loaded

    @property
    def openai_client(self):
        """Lazy-load the OpenAI client."""
        if self._openai_client is None:
            try:
                import openai
            except ImportError:
                raise ImportError("The `openai` library is required for Scout Vetting. Install with: pip install openai")
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OPENAI_API_KEY not set in environment")
            self._openai_client = openai.OpenAI(api_key=api_key)
        return self._openai_client

    # ═══════════════════════════════════════════════════════════════
    # Toggle Checks
    # ═══════════════════════════════════════════════════════════════

    def is_enabled(self) -> bool:
        """Check if Scout Vetting is globally enabled."""
        from models import VettingConfig
        val = VettingConfig.get_value('scout_vetting_enabled', 'false')
        return str(val).lower() in ('true', '1', 'yes')

    def is_enabled_for_job(self, job_id: int) -> bool:
        """Check per-job override, fallback to global toggle.
        
        Returns True if vetting should run for this job:
          - Per-job True → always on
          - Per-job False → always off
          - Per-job null → follow global
        """
        from models import JobVettingRequirements
        jvr = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
        if jvr and jvr.scout_vetting_enabled is not None:
            return jvr.scout_vetting_enabled
        return self.is_enabled()

    def _get_enabled_at(self) -> Optional[datetime]:
        """Get the timestamp when Scout Vetting was enabled (for forward-only filtering)."""
        from models import VettingConfig
        val = VettingConfig.get_value('scout_vetting_enabled_at')
        if val:
            try:
                return datetime.fromisoformat(val)
            except (ValueError, TypeError):
                pass
        return None

    # ═══════════════════════════════════════════════════════════════
    # Session Lifecycle
    # ═══════════════════════════════════════════════════════════════

    def initiate_vetting(self, vetting_log, matches) -> Dict:
        """Create vetting sessions for qualified job matches.
        
        Respects:
          - Per-job toggle
          - Forward-only (only candidates analyzed after enable timestamp)
          - Active session dedup (no duplicate session for same candidate+job)
          - MAX_CONCURRENT_SESSIONS cap (extras are queued)
          - STAGGER_MINUTES between outreach to same candidate
          
        Args:
            vetting_log: CandidateVettingLog instance
            matches: List of CandidateJobMatch instances (is_qualified=True)
            
        Returns:
            Dict with counts: {'created': N, 'queued': N, 'skipped': N}
        """
        from app import db
        from models import ScoutVettingSession

        result = {'created': 0, 'queued': 0, 'skipped': 0, 'sessions': []}
        enabled_at = self._get_enabled_at()

        for match in matches:
            # Forward-only: skip if analyzed before feature was enabled
            if enabled_at and vetting_log.analyzed_at and vetting_log.analyzed_at < enabled_at:
                logger.info(f"Scout Vetting: Skipping candidate {vetting_log.bullhorn_candidate_id} "
                           f"— analyzed_at {vetting_log.analyzed_at} < enabled_at {enabled_at}")
                result['skipped'] += 1
                continue

            # Per-job toggle check
            if not self.is_enabled_for_job(match.bullhorn_job_id):
                logger.info(f"Scout Vetting: Skipping job {match.bullhorn_job_id} — per-job toggle off")
                result['skipped'] += 1
                continue

            # Active session dedup
            if self._check_active_session_exists(vetting_log.bullhorn_candidate_id, match.bullhorn_job_id):
                logger.info(f"Scout Vetting: Active session already exists for candidate "
                           f"{vetting_log.bullhorn_candidate_id} job {match.bullhorn_job_id}")
                result['skipped'] += 1
                continue

            # Count active sessions for this candidate
            active_count = ScoutVettingSession.query.filter(
                ScoutVettingSession.bullhorn_candidate_id == vetting_log.bullhorn_candidate_id,
                ScoutVettingSession.status.in_(['pending', 'outreach_sent', 'in_progress'])
            ).count()

            # Create session
            status = 'pending' if active_count < self.MAX_CONCURRENT_SESSIONS else 'queued'
            
            session = ScoutVettingSession(
                vetting_log_id=vetting_log.id,
                candidate_job_match_id=match.id,
                bullhorn_candidate_id=vetting_log.bullhorn_candidate_id,
                candidate_email=vetting_log.candidate_email or '',
                candidate_name=vetting_log.candidate_name,
                bullhorn_job_id=match.bullhorn_job_id,
                job_title=match.job_title,
                recruiter_email=match.recruiter_email,
                recruiter_name=match.recruiter_name,
                status=status,
                max_turns=self.MAX_TURNS,
            )
            db.session.add(session)
            db.session.flush()  # Get session.id for logging

            if status == 'pending':
                result['created'] += 1
                result['sessions'].append(session)
            else:
                result['queued'] += 1

            logger.info(f"Scout Vetting: Created session {session.id} (status={status}) "
                       f"for candidate {vetting_log.bullhorn_candidate_id} "
                       f"job {match.bullhorn_job_id}")

        db.session.commit()

        # Send outreach for pending sessions (staggered)
        for i, session in enumerate(result['sessions']):
            try:
                self._prepare_and_send_outreach(session, stagger_index=i)
            except Exception as e:
                logger.error(f"Scout Vetting: Failed to send outreach for session {session.id}: {e}")

        return result

    def _prepare_and_send_outreach(self, session, stagger_index: int = 0):
        """Generate questions and send initial outreach email.
        
        Args:
            session: ScoutVettingSession instance
            stagger_index: Position in batch for stagger delay calculation
        """
        from app import db

        try:
            # Generate vetting questions using AI
            questions = self.generate_vetting_questions(session)
            session.vetting_questions_json = json.dumps(questions)

            # Build and send outreach email
            html = self._build_outreach_email(session, questions)
            subject = self._build_subject(session, is_initial=True)

            result = self.email_service.send_html_email(
                to_email=session.candidate_email,
                subject=subject,
                html_content=html,
                notification_type='scout_vetting_outreach',
                reply_to=SCOUT_VETTING_REPLY_TO,
                from_name=SCOUT_VETTING_FROM_NAME,
            )

            success = result is True or (isinstance(result, dict) and result.get('success', False))
            message_id = result.get('message_id') if isinstance(result, dict) else None

            if success:
                session.status = 'outreach_sent'
                session.last_outreach_at = datetime.utcnow()
                session.current_turn = 1
                session.last_message_id = message_id
                self._capture_thread_root(session, message_id)

                # Record the outbound turn
                self._record_turn(session, 'outbound', subject, html, questions_asked=questions,
                                  message_id=message_id)

                logger.info(f"Scout Vetting: Outreach sent for session {session.id} "
                           f"to {session.candidate_email}")
            else:
                session.status = 'pending'  # Will retry next cycle
                logger.error(f"Scout Vetting: Email send failed for session {session.id}")

            db.session.commit()

        except Exception as e:
            logger.error(f"Scout Vetting: Error preparing outreach for session {session.id}: {e}")
            import traceback
            logger.error(traceback.format_exc())

    # ═══════════════════════════════════════════════════════════════
    # AI Question Generation
    # ═══════════════════════════════════════════════════════════════

    def generate_vetting_questions(self, session) -> List[str]:
        """Generate 3-5 job-specific verification questions using AI.
        
        Uses match data (gaps_identified, match_summary) and job requirements
        to create targeted questions that verify candidate qualifications.
        """
        from models import CandidateJobMatch

        match = CandidateJobMatch.query.get(session.candidate_job_match_id) if session.candidate_job_match_id else None

        gaps = match.gaps_identified if match else 'No specific gaps identified'
        summary = match.match_summary if match else 'General candidate match'
        skills = match.skills_match if match else ''
        experience = match.experience_match if match else ''

        prompt = f"""You are a professional recruiter verifying a candidate's fit for a specific role. \
Your task is to write 3–5 concise, friendly verification questions that will be sent via email.

JOB TITLE: {session.job_title or 'Not specified'}
CANDIDATE: {session.candidate_name or 'Candidate'}

MATCH SUMMARY: {summary}

SKILLS ASSESSMENT: {skills}

EXPERIENCE ASSESSMENT: {experience}

GAPS / ITEMS TO VERIFY: {gaps}

QUESTION WRITING RULES:
1. Focus exclusively on the gaps above — do NOT ask about skills or experience already confirmed in the resume.
2. Be specific: reference the actual technology, tool, or scenario (e.g., "Could you walk me through how you've used Kubernetes in a production environment?" not "Tell me about your DevOps experience").
3. Each question must be answerable in 2–3 sentences — no open-ended "Tell me about yourself" questions.
4. Tone: warm and professional, as if a recruiter is genuinely curious — not an interrogation.
5. Always include one question about current availability and preferred start date.
6. If there are fewer than 3 genuine gaps to verify, pad with role-specific questions about work style, remote/hybrid preferences, or compensation expectations — but keep them relevant to this role.
7. Maximum 5 questions. Do not repeat or rephrase the same gap twice.

Return ONLY a valid JSON array of question strings — no markdown, no explanation, no wrapping text.
Example format: ["Question 1?", "Question 2?", "Question 3?"]"""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[{'role': 'user', 'content': prompt}],
            )
            if not response.choices:
                raise ValueError("OpenAI returned an empty response")
            content = response.choices[0].message.content

            # Parse JSON array from response
            # Handle possible markdown code blocks
            content = content.strip()
            if content.startswith('```'):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
                content = content.strip()

            questions = json.loads(content)
            if isinstance(questions, list) and len(questions) >= 1:
                return questions[:5]  # Cap at 5
        except Exception as e:
            logger.error(f"Scout Vetting: Failed to generate questions for session {session.id}: {e}")

        # Fallback questions
        return [
            f"Could you tell me a bit more about your experience with the key skills listed in the {session.job_title} role?",
            "What is your current availability, and when would you be able to start a new position?",
            "Are you open to discussing the compensation range and work arrangement for this role?",
        ]

    # ═══════════════════════════════════════════════════════════════
    # Inbound Reply Processing
    # ═══════════════════════════════════════════════════════════════

    def process_candidate_reply(self, session, email_body: str, email_subject: str = '',
                                message_id: str = None) -> Optional[str]:
        """Process a candidate's email reply.
        
        Classifies intent, extracts answers, decides next action:
          - answer: Extract answers, check if enough info → follow-up or finalize
          - question: Answer their question and re-ask unanswered vetting questions
          - decline: Close session, record reason
          - unrelated/spam: Ignore
          
        Args:
            session: ScoutVettingSession instance
            email_body: Raw email body text
            email_subject: Email subject line
            message_id: Message-ID header for threading
            
        Returns:
            HTML reply to send, or None if no reply needed
        """
        from app import db

        try:
            # Update session state
            session.last_reply_at = datetime.utcnow()
            session.follow_up_count = 0  # Reset follow-up counter on any reply
            if session.status == 'outreach_sent':
                session.status = 'in_progress'

            # Classify intent and extract information
            classification = self._classify_reply(session, email_body)
            intent = classification.get('intent', 'unknown')
            reasoning = classification.get('reasoning', '')
            answers = classification.get('answers_extracted', {})

            # Record the inbound turn
            self._record_turn(
                session, 'inbound', email_subject, email_body,
                ai_intent=intent, ai_reasoning=reasoning,
                answers_extracted=answers, message_id=message_id
            )

            # Merge extracted answers into session
            existing_answers = json.loads(session.answered_questions_json or '{}')
            existing_answers.update(answers)
            session.answered_questions_json = json.dumps(existing_answers)

            # Determine next action based on intent
            if intent == 'decline':
                session.status = 'declined'
                db.session.commit()
                logger.info(f"Scout Vetting: Session {session.id} — candidate declined")
                return None  # No reply for declines

            elif intent in ('unrelated', 'spam', 'out_of_office'):
                logger.info(f"Scout Vetting: Session {session.id} — ignoring {intent} reply")
                db.session.commit()
                return None

            # Check if we have enough answers or hit turn limit
            questions = json.loads(session.vetting_questions_json or '[]')
            answered_count = len(existing_answers)
            session.current_turn += 1

            if answered_count >= len(questions) or session.current_turn >= session.max_turns:
                # All questions answered or max turns reached → finalize
                reply_html = self._generate_thank_you(session)
                quoted_history = self._build_quoted_history(session)
                subject = self._build_subject(session, is_initial=False)
                headers = self._get_threading_headers(session)

                # Send thank-you and finalize
                send_result = self.email_service.send_html_email(
                    to_email=session.candidate_email,
                    subject=subject,
                    html_content=reply_html + quoted_history,
                    notification_type='scout_vetting_reply',
                    reply_to=SCOUT_VETTING_REPLY_TO,
                    from_name=SCOUT_VETTING_FROM_NAME,
                    in_reply_to=headers['in_reply_to'],
                    references=headers['references'],
                )

                out_msg_id = send_result.get('message_id') if isinstance(send_result, dict) else None
                session.last_message_id = out_msg_id or session.last_message_id

                self._record_turn(session, 'outbound', subject, reply_html, message_id=out_msg_id)

                # Finalize
                self.finalize_vetting(session)
                db.session.commit()
                return reply_html

            else:
                # Generate follow-up with remaining questions
                unanswered = [q for q in questions if q not in existing_answers]
                reply_html = self._generate_followup_reply(session, classification, unanswered)
                quoted_history = self._build_quoted_history(session)
                subject = self._build_subject(session, is_initial=False)
                headers = self._get_threading_headers(session)

                send_result = self.email_service.send_html_email(
                    to_email=session.candidate_email,
                    subject=subject,
                    html_content=reply_html + quoted_history,
                    notification_type='scout_vetting_reply',
                    reply_to=SCOUT_VETTING_REPLY_TO,
                    from_name=SCOUT_VETTING_FROM_NAME,
                    in_reply_to=headers['in_reply_to'],
                    references=headers['references'],
                )

                out_msg_id = send_result.get('message_id') if isinstance(send_result, dict) else None
                session.last_message_id = out_msg_id or session.last_message_id
                session.last_outreach_at = datetime.utcnow()

                self._record_turn(session, 'outbound', subject, reply_html,
                                  questions_asked=unanswered, message_id=out_msg_id)

                db.session.commit()
                return reply_html

        except Exception as e:
            logger.error(f"Scout Vetting: Error processing reply for session {session.id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def _classify_reply(self, session, email_body: str) -> Dict:
        """Use AI to classify the candidate's reply intent and extract answers."""
        questions = json.loads(session.vetting_questions_json or '[]')
        existing_answers = json.loads(session.answered_questions_json or '{}')
        unanswered = [q for q in questions if q not in existing_answers]

        prompt = f"""You are analyzing a candidate's email reply in a recruitment vetting conversation. \
Your job is to classify the reply's intent and extract any useful answers.

CANDIDATE: {session.candidate_name}
JOB: {session.job_title}

QUESTIONS WE ASKED (outstanding — not yet answered):
{json.dumps(unanswered, indent=2)}

CANDIDATE'S REPLY:
---
{email_body[:4000]}
---

Return a JSON object with EXACTLY these fields:
{{
  "intent": "<one of the intent values below>",
  "reasoning": "<1-2 sentences explaining your classification>",
  "answers_extracted": {{
    "<exact question text from our list>": "<concise 1-2 sentence summary of their answer>"
  }},
  "candidate_questions": ["<any questions the candidate asked about the role, company, or process>"]
}}

INTENT VALUES — choose exactly one:
- "answer": The candidate is providing substantive information relevant to one or more of our questions, even if incomplete.
- "question": The candidate is asking for clarification or information BEFORE answering (their reply contains no answers, only questions back to us).
- "decline": The candidate explicitly states they are no longer interested, withdrawing, or asking to stop contact.
- "out_of_office": Automated out-of-office, vacation, or bounce notification — no human-authored content.
- "spam": Completely unrelated promotional/spam content with no reference to the job or conversation.
- "unrelated": Human-written reply but entirely off-topic (not a decline, not about this role).

EDGE CASE RULES:
- PARTIAL ANSWER → use "answer". Extract whatever they did answer; leave unanswered questions out of answers_extracted. Do NOT classify as "unrelated" just because they didn't answer everything.
- MULTILINGUAL REPLY → classify based on intent regardless of language; summarize answers in English in answers_extracted.
- CANDIDATE COUNTER-QUESTION WITH ANSWERS → use "answer" if they also provided substantive answers; use "question" if the reply is only questions back to us.
- AUTO-REPLIES (delivery receipts, email filters, HR system auto-responses) → "out_of_office".
- When extracting answers: match to the CLOSEST question from our list by meaning, not just keyword. Summarize in 1-2 sentences. Do not quote the full reply.
- If no questions from our list were answered, answers_extracted should be an empty object {{}}.

Return only valid JSON. No markdown, no preamble."""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[{'role': 'user', 'content': prompt}],
            )
            if not response.choices:
                raise ValueError("OpenAI returned an empty response")
            content = response.choices[0].message.content
            content = content.strip()
            if content.startswith('```'):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
                content = content.strip()
            return json.loads(content)
        except Exception as e:
            logger.error(f"Scout Vetting: Failed to classify reply for session {session.id}: {e}")
            return {
                'intent': 'answer',
                'reasoning': f'Classification failed: {str(e)}',
                'answers_extracted': {},
                'candidate_questions': []
            }

    # ═══════════════════════════════════════════════════════════════
    # Finalization & Handoff
    # ═══════════════════════════════════════════════════════════════

    def finalize_vetting(self, session):
        """Generate outcome assessment, create Bullhorn note, send recruiter handoff.
        
        Called when:
          - All questions answered
          - Max turns reached
          - Candidate marked unresponsive after follow-ups
        """
        from app import db

        try:
            # Generate outcome assessment
            outcome = self._generate_outcome(session)
            session.outcome_summary = outcome.get('summary', '')
            session.outcome_score = outcome.get('score', 0.0)
            session.status = outcome.get('recommendation', 'qualified')

            # Create Bullhorn note
            note_action = f"Scout Vetting - {session.status.replace('_', ' ').title()}"
            if self.bullhorn and session.bullhorn_candidate_id:
                try:
                    note_id = self._create_bullhorn_note(session, note_action)
                    if note_id:
                        session.bullhorn_note_id = note_id
                        session.note_created = True
                except Exception as e:
                    logger.error(f"Scout Vetting: Bullhorn note creation failed for session {session.id}: {e}")

            # Send recruiter handoff email
            if session.status == 'qualified' and session.recruiter_email:
                try:
                    self._send_recruiter_handoff(session)
                    session.handoff_sent = True
                except Exception as e:
                    logger.error(f"Scout Vetting: Recruiter handoff failed for session {session.id}: {e}")

            db.session.commit()
            logger.info(f"Scout Vetting: Session {session.id} finalized — "
                       f"status={session.status}, score={session.outcome_score}")

        except Exception as e:
            logger.error(f"Scout Vetting: Finalization error for session {session.id}: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _generate_outcome(self, session) -> Dict:
        """Use AI to generate a final vetting outcome."""
        questions = json.loads(session.vetting_questions_json or '[]')
        answers = json.loads(session.answered_questions_json or '{}')

        # Gather conversation history
        turns_text = self._get_conversation_summary(session)

        session_status = session.status or 'in_progress'
        unresponsive = (session_status == 'unresponsive')
        answered_count = len(answers)
        total_questions = len(questions)

        prompt = f"""You are evaluating a completed recruiter vetting conversation to determine whether the candidate should move forward in the hiring process.

CANDIDATE: {session.candidate_name}
JOB TITLE: {session.job_title}
CONVERSATION STATUS: {'Candidate was unresponsive — no replies received' if unresponsive else f'Completed normally ({session.current_turn} turns)'}
QUESTIONS ANSWERED: {answered_count} of {total_questions}

QUESTIONS ASKED:
{json.dumps(questions, indent=2)}

ANSWERS RECEIVED:
{json.dumps(answers, indent=2)}

CONVERSATION HISTORY:
{turns_text}

Evaluate the candidate and return a JSON object:
{{
  "recommendation": "qualified" | "not_qualified",
  "score": <integer 0-100>,
  "summary": "<2-3 sentences covering: (1) overall impression of their answers, (2) any notable strengths or concerns, (3) a clear statement of the recommendation>"
}}

SCORING GUIDE:
- 80-100: Strong, complete answers; candidate clearly meets key requirements; no significant concerns
- 60-79: Mostly satisfactory; minor gaps or vague answers on 1-2 points; worth pursuing
- 40-59: Mixed responses; meaningful gaps remain or answers raise questions; borderline
- 0-39: Insufficient information, red flags, or evasive responses; does not meet requirements

DECISION RULES:
- "qualified": Score ≥ 60 AND no deal-breaking red flags (e.g., clear misrepresentation, explicit unavailability, location hard block)
- "not_qualified": Score < 60 OR a clear disqualifying factor was revealed
- If the candidate was UNRESPONSIVE: score 0 and "not_qualified" — no reply = insufficient information to advance
- Unanswered questions count as mild negatives (possible avoidance), but a single unanswered question on a multi-question set should not automatically disqualify
- Be fair and proportionate: an honest partial answer is better than silence

Return only valid JSON. No markdown, no preamble."""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[{'role': 'user', 'content': prompt}],
            )
            if not response.choices:
                raise ValueError("OpenAI returned an empty response")
            content = response.choices[0].message.content
            content = content.strip()
            if content.startswith('```'):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
                content = content.strip()
            return json.loads(content)
        except Exception as e:
            logger.error(f"Scout Vetting: Outcome generation failed for session {session.id}: {e}")
            if unresponsive:
                return {
                    'recommendation': 'not_qualified',
                    'score': 0,
                    'summary': 'Candidate did not respond to vetting outreach. Marked not qualified due to non-response.'
                }
            coverage = len(answers) / max(len(questions), 1)
            return {
                'recommendation': 'qualified' if coverage >= 0.6 else 'not_qualified',
                'score': int(coverage * 70),
                'summary': 'Automated assessment — AI evaluation unavailable, scored based on answer coverage.'
            }

    # ═══════════════════════════════════════════════════════════════
    # Follow-up Scheduler
    # ═══════════════════════════════════════════════════════════════

    def run_followups(self) -> Dict:
        """Scheduled: Process follow-ups and promote queued sessions.
        
        Called every 30 minutes by the scheduler.
        
        Actions:
          1. Send 24h follow-up to sessions with no reply
          2. Send 48h follow-up (final nudge)
          3. Close sessions as unresponsive after 48h follow-up with no reply
          4. Promote queued sessions when slots open
          
        Returns:
            Dict with counts of actions taken
        """
        from app import db
        from models import ScoutVettingSession

        stats = {'followups_sent': 0, 'closed_unresponsive': 0, 'promoted': 0, 'errors': 0}
        now = datetime.utcnow()

        try:
            # Find sessions needing follow-up
            sessions_needing_followup = ScoutVettingSession.query.filter(
                ScoutVettingSession.status.in_(['outreach_sent', 'in_progress']),
                ScoutVettingSession.last_outreach_at.isnot(None),
            ).all()

            for session in sessions_needing_followup:
                try:
                    hours_since_outreach = (now - session.last_outreach_at).total_seconds() / 3600

                    if session.follow_up_count >= 2 and hours_since_outreach >= 48:
                        # Already sent 2 follow-ups + 48h wait → close as unresponsive
                        session.status = 'unresponsive'
                        self.finalize_vetting(session)
                        stats['closed_unresponsive'] += 1
                        logger.info(f"Scout Vetting: Session {session.id} marked unresponsive")

                    elif session.follow_up_count < len(self.FOLLOWUP_HOURS):
                        required_hours = self.FOLLOWUP_HOURS[session.follow_up_count]
                        if hours_since_outreach >= required_hours:
                            self._send_followup(session)
                            stats['followups_sent'] += 1

                except Exception as e:
                    logger.error(f"Scout Vetting: Follow-up error for session {session.id}: {e}")
                    stats['errors'] += 1

            # Promote queued sessions when slots open
            candidates_with_queued = db.session.query(
                ScoutVettingSession.bullhorn_candidate_id
            ).filter(
                ScoutVettingSession.status == 'queued'
            ).distinct().all()

            for (candidate_id,) in candidates_with_queued:
                active = ScoutVettingSession.query.filter(
                    ScoutVettingSession.bullhorn_candidate_id == candidate_id,
                    ScoutVettingSession.status.in_(['pending', 'outreach_sent', 'in_progress'])
                ).count()

                if active < self.MAX_CONCURRENT_SESSIONS:
                    slots_available = self.MAX_CONCURRENT_SESSIONS - active
                    queued = ScoutVettingSession.query.filter(
                        ScoutVettingSession.bullhorn_candidate_id == candidate_id,
                        ScoutVettingSession.status == 'queued'
                    ).order_by(ScoutVettingSession.created_at).limit(slots_available).all()

                    for session in queued:
                        session.status = 'pending'
                        try:
                            self._prepare_and_send_outreach(session)
                            stats['promoted'] += 1
                        except Exception as e:
                            logger.error(f"Scout Vetting: Promotion failed for session {session.id}: {e}")
                            stats['errors'] += 1

            db.session.commit()

        except Exception as e:
            logger.error(f"Scout Vetting: run_followups error: {e}")
            import traceback
            logger.error(traceback.format_exc())

        logger.info(f"Scout Vetting follow-ups: {stats}")
        return stats

    def _send_followup(self, session):
        """Send a follow-up email to an unresponsive candidate."""
        from app import db

        follow_up_num = session.follow_up_count + 1
        is_final = follow_up_num >= len(self.FOLLOWUP_HOURS)

        html = self._build_followup_email(session, follow_up_num, is_final)
        quoted_history = self._build_quoted_history(session)
        subject = self._build_subject(session, is_initial=False)
        headers = self._get_threading_headers(session)

        result = self.email_service.send_html_email(
            to_email=session.candidate_email,
            subject=subject,
            html_content=html + quoted_history,
            notification_type='scout_vetting_followup',
            reply_to=SCOUT_VETTING_REPLY_TO,
            from_name=SCOUT_VETTING_FROM_NAME,
            in_reply_to=headers['in_reply_to'],
            references=headers['references'],
        )

        success = result is True or (isinstance(result, dict) and result.get('success', False))
        message_id = result.get('message_id') if isinstance(result, dict) else None

        if success:
            session.follow_up_count = follow_up_num
            session.last_outreach_at = datetime.utcnow()
            session.last_message_id = message_id or session.last_message_id
            self._record_turn(session, 'outbound', subject, html, message_id=message_id)
            logger.info(f"Scout Vetting: Follow-up #{follow_up_num} sent for session {session.id}")
        else:
            logger.error(f"Scout Vetting: Follow-up email failed for session {session.id}")

    # ═══════════════════════════════════════════════════════════════
    # Session Lookup
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def find_session_by_subject_token(subject: str):
        """Extract session ID from [SV-{id}] token in email subject."""
        from models import ScoutVettingSession
        
        match = re.search(r'\[SV-(\d+)\]', subject or '')
        if match:
            session_id = int(match.group(1))
            return ScoutVettingSession.query.get(session_id)
        return None

    @staticmethod
    def find_session_by_email(sender_email: str):
        """Fallback: find active session by candidate email address."""
        from models import ScoutVettingSession

        return ScoutVettingSession.query.filter(
            ScoutVettingSession.candidate_email == sender_email,
            ScoutVettingSession.status.in_(['outreach_sent', 'in_progress'])
        ).order_by(ScoutVettingSession.updated_at.desc()).first()

    def _check_active_session_exists(self, candidate_id: int, job_id: int) -> bool:
        """Check if an active vetting session exists for this candidate+job."""
        from models import ScoutVettingSession

        return ScoutVettingSession.query.filter(
            ScoutVettingSession.bullhorn_candidate_id == candidate_id,
            ScoutVettingSession.bullhorn_job_id == job_id,
            ScoutVettingSession.status.in_(['pending', 'queued', 'outreach_sent', 'in_progress'])
        ).first() is not None

    # ═══════════════════════════════════════════════════════════════
    # Email Template Builders
    # ═══════════════════════════════════════════════════════════════

    def _build_subject(self, session, is_initial: bool = True) -> str:
        """Build email subject with session token."""
        token = f"[SV-{session.id}]"
        if is_initial:
            return f"About the {session.job_title} opportunity {token}"
        else:
            return f"Re: About the {session.job_title} opportunity {token}"

    def _build_outreach_email(self, session, questions: List[str]) -> str:
        """Build the initial outreach email HTML."""
        candidate_first = (session.candidate_name or 'there').split()[0]
        questions_html = ''.join(f'<li style="margin-bottom: 10px;">{q}</li>' for q in questions)

        return f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #333; line-height: 1.6;">
    <p>Hi {candidate_first},</p>
    
    <p>Thank you for your interest in the <strong>{session.job_title}</strong> position. 
    Your background looks promising, and I'd love to learn a bit more before moving forward.</p>
    
    <p>Could you please share some details on the following?</p>
    
    <ol style="padding-left: 20px;">
        {questions_html}
    </ol>
    
    <p>Feel free to reply directly to this email — a few sentences per question is perfect.</p>
    
    <p>Looking forward to hearing from you!</p>
    
    <p>Best regards,<br>
    <strong>Scout by Myticas</strong><br>
    <span style="color: #666; font-size: 13px;">Talent Acquisition Team</span></p>
    
    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
    <p style="font-size: 11px; color: #999;">
        This message was sent on behalf of Myticas Consulting regarding your job application.
        If you're no longer interested, simply reply and let us know.
    </p>
</div>"""

    def _build_followup_email(self, session, follow_up_num: int, is_final: bool) -> str:
        """Build a follow-up email for unresponsive candidates."""
        candidate_first = (session.candidate_name or 'there').split()[0]

        if is_final:
            return f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #333; line-height: 1.6;">
    <p>Hi {candidate_first},</p>
    
    <p>I wanted to follow up one last time about the <strong>{session.job_title}</strong> position. 
    I understand things can get busy, so no worries if the timing isn't right.</p>
    
    <p>If you're still interested, a quick reply with your thoughts on the questions I sent earlier 
    would be great. Otherwise, I'll close out this conversation and you're welcome to reach out 
    anytime in the future.</p>
    
    <p>Best regards,<br>
    <strong>Scout by Myticas</strong></p>
</div>"""
        else:
            return f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #333; line-height: 1.6;">
    <p>Hi {candidate_first},</p>
    
    <p>Just checking in — I reached out about the <strong>{session.job_title}</strong> opportunity 
    and wanted to make sure my email didn't get lost.</p>
    
    <p>If you have a moment, I'd love to hear back on the questions I shared. 
    Even a brief response helps us move forward.</p>
    
    <p>Best regards,<br>
    <strong>Scout by Myticas</strong></p>
</div>"""

    def _generate_followup_reply(self, session, classification: Dict, unanswered: List[str]) -> str:
        """Generate a conversational follow-up reply using AI."""
        candidate_first = (session.candidate_name or 'there').split()[0]
        candidate_questions = classification.get('candidate_questions', [])

        # Use AI to generate a natural reply
        # Include a brief conversation context so GPT-5 doesn't repeat answered questions
        already_answered = list(json.loads(session.answered_questions_json or '{}').keys())

        prompt = f"""You are "Scout by Myticas", a professional recruiter conducting a friendly email vetting conversation. \
Write a natural, warm reply to a candidate who has partially responded to our questions.

CANDIDATE: {candidate_first}
JOB: {session.job_title}
CONVERSATION TURN: {session.current_turn} of {session.max_turns}

WHAT THE CANDIDATE SHARED (AI summary): {classification.get('reasoning', 'Provided some information')}

QUESTIONS ALREADY ANSWERED (DO NOT ask these again):
{json.dumps(already_answered, indent=2) if already_answered else '[]'}

QUESTIONS THE CANDIDATE ASKED US:
{json.dumps(candidate_questions, indent=2) if candidate_questions else '[]'}

REMAINING QUESTIONS TO ASK:
{json.dumps(unanswered, indent=2)}

WRITING RULES:
1. Open by genuinely acknowledging what they shared — be specific if possible (e.g., reference a detail from their summary), not generic ("Thanks for your reply!").
2. If they asked us questions, answer them briefly and professionally. Do not reveal internal AI processes or scoring. For role/company questions you can't answer, say "A member of our team will be happy to share more details when we connect."
3. Naturally flow into the remaining unanswered questions — do not number them as a list if there is only 1 left; embed it conversationally.
4. NEVER repeat a question that is in the "already answered" list above.
5. Close warmly with "Best regards, Scout by Myticas / Talent Acquisition Team".
6. Tone: conversational, genuine, professional — not scripted or robotic.

Return ONLY the HTML body content (no <html>, <head>, or <body> tags — just a styled <div>).
Keep it concise — maximum 3-4 short paragraphs. Use inline styles for any formatting."""

        try:
            response = self.openai_client.chat.completions.create(
                model='gpt-5',
                messages=[{'role': 'user', 'content': prompt}],
            )
            if not response.choices:
                raise ValueError("OpenAI returned an empty response")
            content = response.choices[0].message.content
            # Strip markdown code blocks if present
            content = content.strip()
            if content.startswith('```'):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
            return content.strip()
        except Exception as e:
            logger.error(f"Scout Vetting: Follow-up generation failed: {e}")
            # Fallback template
            questions_html = ''.join(f'<li style="margin-bottom: 10px;">{q}</li>' for q in unanswered)
            return f"""<div style="font-family: Arial, sans-serif; max-width: 600px; color: #333; line-height: 1.6;">
    <p>Hi {candidate_first},</p>
    <p>Thanks for getting back to me! I appreciate the information you've shared so far.</p>
    <p>To help us complete the review, could you also address these remaining points?</p>
    <ol style="padding-left: 20px;">{questions_html}</ol>
    <p>Best regards,<br><strong>Scout by Myticas</strong></p>
</div>"""

    def _generate_thank_you(self, session) -> str:
        """Generate a thank-you email when vetting is complete."""
        candidate_first = (session.candidate_name or 'there').split()[0]
        return f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #333; line-height: 1.6;">
    <p>Hi {candidate_first},</p>
    
    <p>Thank you so much for taking the time to share those details about your background 
    for the <strong>{session.job_title}</strong> position. This is really helpful.</p>
    
    <p>I'll be reviewing everything and a member of our recruiting team will be in touch 
    soon with next steps.</p>
    
    <p>Best regards,<br>
    <strong>Scout by Myticas</strong><br>
    <span style="color: #666; font-size: 13px;">Talent Acquisition Team</span></p>
</div>"""

    def _send_recruiter_handoff(self, session):
        """Send handoff email to the recruiter with vetting results."""
        answers = json.loads(session.answered_questions_json or '{}')
        questions = json.loads(session.vetting_questions_json or '[]')

        # Build Q&A summary
        qa_rows = ''
        for q in questions:
            answer = answers.get(q, '<em style="color: #999;">Not answered</em>')
            qa_rows += f"""
            <tr>
                <td style="padding: 10px; border: 1px solid #dee2e6; vertical-align: top; width: 40%;"><strong>{q}</strong></td>
                <td style="padding: 10px; border: 1px solid #dee2e6;">{answer}</td>
            </tr>"""

        score_color = '#28a745' if (session.outcome_score or 0) >= 70 else '#ffc107' if (session.outcome_score or 0) >= 50 else '#dc3545'

        html = f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 700px; margin: 0 auto; color: #333;">
    <div style="background: linear-gradient(135deg, #1a1a2e, #16213e); padding: 20px; border-radius: 8px 8px 0 0;">
        <h2 style="color: #e94560; margin: 0;">🎯 Scout Vetting Complete</h2>
        <p style="color: #a0a0b0; margin: 8px 0 0;">Candidate ready for your review</p>
    </div>
    
    <div style="padding: 20px; border: 1px solid #dee2e6;">
        <table style="width: 100%; margin-bottom: 20px;">
            <tr>
                <td><strong>Candidate:</strong> {session.candidate_name}</td>
                <td><strong>Job:</strong> {session.job_title}</td>
            </tr>
            <tr>
                <td><strong>Email:</strong> {session.candidate_email}</td>
                <td><strong>Vetting Score:</strong> <span style="color: {score_color}; font-weight: bold;">{session.outcome_score or 0:.0f}%</span></td>
            </tr>
        </table>
        
        <div style="background: #f8f9fa; padding: 15px; border-radius: 6px; margin-bottom: 20px;">
            <h3 style="margin: 0 0 8px; font-size: 14px;">📋 Assessment Summary</h3>
            <p style="margin: 0; font-size: 14px;">{session.outcome_summary or 'Assessment pending.'}</p>
        </div>
        
        <h3 style="font-size: 14px;">💬 Vetting Q&A</h3>
        <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
            <thead>
                <tr style="background: #1a1a2e; color: white;">
                    <th style="padding: 10px; border: 1px solid #dee2e6; text-align: left;">Question</th>
                    <th style="padding: 10px; border: 1px solid #dee2e6; text-align: left;">Answer</th>
                </tr>
            </thead>
            <tbody>{qa_rows}</tbody>
        </table>
    </div>
    
    <div style="padding: 15px; border: 1px solid #dee2e6; border-top: none; border-radius: 0 0 8px 8px; background: #f8f9fa;">
        <p style="margin: 0; font-size: 12px; color: #666;">
            Conversation took {session.current_turn} turn(s) over {self._format_duration(session)}.
            <br>Scout Vetting by Myticas — AI-Assisted Talent Verification
        </p>
    </div>
</div>"""

        subject = f"🎯 Scout Vetting Complete: {session.candidate_name} for {session.job_title}"

        self.email_service.send_html_email(
            to_email=session.recruiter_email,
            subject=subject,
            html_content=html,
            notification_type='scout_vetting_handoff',
            from_name=SCOUT_VETTING_FROM_NAME,
        )

        logger.info(f"Scout Vetting: Recruiter handoff sent for session {session.id} "
                    f"to {session.recruiter_email}")

    # ═══════════════════════════════════════════════════════════════
    # Bullhorn Note Creation
    # ═══════════════════════════════════════════════════════════════

    def _create_bullhorn_note(self, session, action_label: str) -> Optional[int]:
        """Create a Bullhorn note summarizing the vetting outcome."""
        answers = json.loads(session.answered_questions_json or '{}')
        questions = json.loads(session.vetting_questions_json or '[]')

        # Build note body
        qa_text = ''
        for q in questions:
            answer = answers.get(q, 'Not answered')
            qa_text += f"\nQ: {q}\nA: {answer}\n"

        note_body = f"""Scout Vetting — {session.status.replace('_', ' ').title()}
Score: {session.outcome_score or 0:.0f}%

Assessment: {session.outcome_summary or 'No assessment generated.'}

Vetting Q&A:{qa_text}
---
Conversation: {session.current_turn} turn(s)
Session ID: SV-{session.id}"""

        try:
            # Use bullhorn service to create note
            note_data = {
                'action': action_label,
                'comments': note_body,
                'personReference': {
                    'id': session.bullhorn_candidate_id,
                    'searchEntity': 'Candidate'
                }
            }

            if hasattr(self.bullhorn, '_make_api_call'):
                result = self.bullhorn._make_api_call(
                    'PUT', 'entity/Note', data=note_data
                )
                if result and result.get('changedEntityId'):
                    return result['changedEntityId']
            
            return None

        except Exception as e:
            logger.error(f"Scout Vetting: Bullhorn note creation failed: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════
    # Helper Methods
    # ═══════════════════════════════════════════════════════════════

    def _build_quoted_history(self, session) -> str:
        """Build HTML quoted conversation history for email threading.

        Iterates prior VettingConversationTurn records and renders them as a
        styled block matching the Scout Support format (sender label, timestamp,
        body, left-border dividers).  Returns an empty string when there are no
        prior turns to quote.
        """
        from models import VettingConversationTurn
        from bs4 import BeautifulSoup

        turns = VettingConversationTurn.query.filter_by(
            session_id=session.id
        ).order_by(VettingConversationTurn.created_at.desc()).limit(10).all()

        if not turns:
            return ''

        parts = []
        for turn in turns:
            sender = 'Scout by Myticas' if turn.direction == 'outbound' else (session.candidate_name or session.candidate_email)
            timestamp = turn.created_at.strftime('%b %d, %Y at %I:%M %p') if turn.created_at else ''

            body_text = (turn.email_body or '').strip()
            if not body_text:
                continue

            if '<' in body_text and '>' in body_text:
                try:
                    soup = BeautifulSoup(body_text, 'html.parser')
                    body_text = soup.get_text(separator='\n').strip()
                except Exception:
                    pass

            body_html = body_text.replace('\n', '<br>')

            parts.append(
                f'<b>From:</b> {sender}<br>'
                f'<b>Date:</b> {timestamp}<br>'
                f'<b>Subject:</b> {turn.email_subject or ""}<br><br>'
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

    def _get_threading_headers(self, session) -> dict:
        """Return in_reply_to and references values for the current session."""
        in_reply_to = session.last_message_id
        references = None
        if in_reply_to:
            thread_id = session.thread_message_id or ''
            if thread_id and thread_id != in_reply_to:
                references = f"{thread_id} {in_reply_to}"
            else:
                references = in_reply_to
        return {'in_reply_to': in_reply_to, 'references': references}

    def _capture_thread_root(self, session, message_id: str):
        """Store the very first message ID as the thread root anchor."""
        if message_id and not session.thread_message_id:
            session.thread_message_id = message_id

    def _record_turn(self, session, direction: str, subject: str, body: str,
                     ai_intent: str = None, ai_reasoning: str = None,
                     questions_asked: List = None, answers_extracted: Dict = None,
                     message_id: str = None):
        """Record a conversation turn in the database."""
        from app import db
        from models import VettingConversationTurn

        turn = VettingConversationTurn(
            session_id=session.id,
            turn_number=session.current_turn,
            direction=direction,
            email_subject=subject,
            email_body=body,
            ai_intent=ai_intent,
            ai_reasoning=ai_reasoning,
            questions_asked_json=json.dumps(questions_asked) if questions_asked else None,
            answers_extracted_json=json.dumps(answers_extracted) if answers_extracted else None,
            message_id=message_id,
        )
        db.session.add(turn)

    def _get_conversation_summary(self, session) -> str:
        """Get a text summary of the conversation for outcome generation."""
        from models import VettingConversationTurn

        turns = VettingConversationTurn.query.filter_by(
            session_id=session.id
        ).order_by(VettingConversationTurn.turn_number).all()

        summary = []
        for turn in turns:
            direction_label = "OUTBOUND (us)" if turn.direction == 'outbound' else "INBOUND (candidate)"
            summary.append(f"Turn {turn.turn_number} [{direction_label}]:")
            if turn.ai_intent:
                summary.append(f"  Intent: {turn.ai_intent}")
            if turn.answers_extracted_json:
                summary.append(f"  Answers: {turn.answers_extracted_json}")
            summary.append("")

        return '\n'.join(summary) if summary else 'No conversation recorded.'

    @staticmethod
    def _format_duration(session) -> str:
        """Format the duration of a vetting session."""
        if not session.created_at:
            return 'unknown duration'
        delta = (session.updated_at or datetime.utcnow()) - session.created_at
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds() / 60)} minutes"
        elif hours < 24:
            return f"{hours:.1f} hours"
        else:
            return f"{hours / 24:.1f} days"
