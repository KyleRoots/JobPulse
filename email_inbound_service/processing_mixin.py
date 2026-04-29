import logging
from datetime import datetime
from typing import Dict, Any

from utils.candidate_name_extraction import (
    build_extraction_summary,
    is_valid_name,
    parse_name_from_email_address,
    parse_name_from_filename,
    strip_html_to_text,
)

logger = logging.getLogger(__name__)


class ProcessingMixin:

    def process_email(self, sendgrid_payload: Dict) -> Dict[str, Any]:
        """
        Main entry point - process a complete inbound email from SendGrid

        Args:
            sendgrid_payload: SendGrid Inbound Parse webhook payload

        Returns:
            Processing result with status and details
        """
        from app import db
        from models import ParsedEmail

        result = {
            'success': False,
            'message': '',
            'candidate_id': None,
            'submission_id': None,
            'is_duplicate': False,
            'parsed_email_id': None
        }

        try:
            sender = sendgrid_payload.get('from', '')
            recipient = sendgrid_payload.get('to', '')
            subject = sendgrid_payload.get('subject', '')
            body_text = sendgrid_payload.get('text', '')
            body_html = sendgrid_payload.get('html', body_text)
            message_id = sendgrid_payload.get('headers', '').split('Message-ID:')[-1].split('\n')[0].strip() if 'Message-ID' in sendgrid_payload.get('headers', '') else None

            body = body_html if body_html else body_text

            self.logger.info(f"Processing inbound email from {sender}: {subject[:50]}...")

            if message_id:
                existing = ParsedEmail.query.filter_by(message_id=message_id).first()
                if existing:
                    self.logger.info(f"Skipping duplicate email (message_id already processed): {message_id[:50]}")
                    return {'success': True, 'message': 'Duplicate email skipped', 'duplicate': True}

            parsed_email = ParsedEmail(
                message_id=message_id,
                sender_email=sender,
                recipient_email=recipient,
                subject=subject,
                status='processing',
                received_at=datetime.utcnow()
            )
            db.session.add(parsed_email)
            db.session.commit()
            result['parsed_email_id'] = parsed_email.id

            source = self.detect_source(sender, subject, body)
            parsed_email.source_platform = source

            job_id = self.extract_bullhorn_job_id(subject, body)
            parsed_email.bullhorn_job_id = job_id

            email_candidate = self.extract_candidate_from_email(subject, body, source)
            parsed_email.candidate_name = f"{email_candidate.get('first_name', '')} {email_candidate.get('last_name', '')}".strip()
            parsed_email.candidate_email = email_candidate.get('email')
            parsed_email.candidate_phone = email_candidate.get('phone')

            resume_data = {}
            resume_text = ''
            attachments = self._extract_attachments(sendgrid_payload)

            resume_file = self._select_best_resume(attachments)

            if resume_file:
                parsed_email.resume_filename = resume_file['filename']

                resume_text, formatted_html = self._extract_resume_text(resume_file)
                if resume_text:
                    resume_data = self.parse_resume_with_ai(resume_text)
                    resume_data['raw_text'] = resume_text
                    resume_data['formatted_html'] = formatted_html

                    self.logger.info(f"AI Resume Extraction Results:")
                    self.logger.info(f"  - Name: {resume_data.get('first_name')} {resume_data.get('last_name')}")
                    self.logger.info(f"  - Current Title: {resume_data.get('current_title')}")
                    self.logger.info(f"  - Current Company: {resume_data.get('current_company')}")
                    self.logger.info(f"  - Years Experience: {resume_data.get('years_experience')}")
                    skills = resume_data.get('skills') or []
                    education = resume_data.get('education') or []
                    work_history = resume_data.get('work_history') or []
                    raw_text_len = len(resume_data.get('raw_text') or '')
                    html_len = len(resume_data.get('formatted_html') or '')
                    self.logger.info(f"  - Skills Count: {len(skills)}")
                    if skills:
                        self.logger.info(f"  - Skills (first 10): {skills[:10]}")
                    self.logger.info(f"  - Education Count: {len(education)}")
                    if education:
                        for edu in education:
                            self.logger.info(f"    - {edu.get('degree')} from {edu.get('institution')} ({edu.get('year')})")
                    self.logger.info(f"  - Work History Count: {len(work_history)}")
                    self.logger.info(f"  - Raw Resume Text Length: {raw_text_len} chars")
                    self.logger.info(f"  - Formatted HTML Length: {html_len} chars")

            db.session.commit()

            candidate_email = email_candidate.get('email') or resume_data.get('email')
            candidate_phone = email_candidate.get('phone') or resume_data.get('phone')
            first_name = email_candidate.get('first_name') or resume_data.get('first_name')
            last_name = email_candidate.get('last_name') or resume_data.get('last_name')

            has_name = is_valid_name(first_name, last_name)
            has_contact = bool(candidate_email or candidate_phone)
            has_email_data = bool(email_candidate.get('first_name') or email_candidate.get('email'))

            if not is_valid_name(first_name, last_name) and parsed_email.resume_filename:
                fn_first, fn_last = parse_name_from_filename(parsed_email.resume_filename)
                if is_valid_name(fn_first, fn_last):
                    self.logger.info(
                        f"Layer 3 (filename) recovered name: {fn_first} {fn_last} "
                        f"from '{parsed_email.resume_filename}'"
                    )
                    first_name = first_name or fn_first
                    last_name = last_name or fn_last
                    parsed_email.candidate_name = f"{first_name} {last_name}".strip()
                    has_name = True

            if not is_valid_name(first_name, last_name) and candidate_email:
                ea_first, ea_last = parse_name_from_email_address(candidate_email)
                if is_valid_name(ea_first, ea_last):
                    self.logger.info(
                        f"Layer 3b (email local-part) recovered name: {ea_first} {ea_last}"
                    )
                    first_name = first_name or ea_first
                    last_name = last_name or ea_last
                    parsed_email.candidate_name = f"{first_name} {last_name}".strip()
                    has_name = True

            if (not is_valid_name(first_name, last_name) or not has_contact):
                ai_recovered = self._last_resort_ai_extraction(
                    subject=subject,
                    sender=sender,
                    resume_filename=parsed_email.resume_filename,
                    resume_text_preview=(resume_text or '')[:600],
                    body_preview=strip_html_to_text(body)[:800] if body else '',
                )
                if ai_recovered:
                    if not is_valid_name(first_name, last_name):
                        ai_first, ai_last = ai_recovered.get('first_name'), ai_recovered.get('last_name')
                        if is_valid_name(ai_first, ai_last):
                            self.logger.info(
                                f"Layer 5 (last-resort AI) recovered name: {ai_first} {ai_last}"
                            )
                            first_name = ai_first
                            last_name = ai_last
                            parsed_email.candidate_name = f"{first_name} {last_name}".strip()
                            has_name = True
                    if not candidate_email and ai_recovered.get('email'):
                        candidate_email = ai_recovered['email'].strip().lower()
                        parsed_email.candidate_email = candidate_email
                        self.logger.info(f"Layer 5 recovered email: {candidate_email}")
                    if not candidate_phone and ai_recovered.get('phone'):
                        candidate_phone = ai_recovered['phone'].strip()
                        parsed_email.candidate_phone = candidate_phone
                        self.logger.info(f"Layer 5 recovered phone: {candidate_phone}")
                    has_contact = bool(candidate_email or candidate_phone)
                    db.session.commit()

            has_name = bool(first_name or last_name)
            has_contact = bool(candidate_email or candidate_phone)

            if first_name:
                email_candidate['first_name'] = first_name
                resume_data['first_name'] = first_name
            if last_name:
                email_candidate['last_name'] = last_name
                resume_data['last_name'] = last_name

            extraction_summary = build_extraction_summary(
                resume_data=resume_data or {},
                email_candidate=email_candidate or {},
                filename=parsed_email.resume_filename,
            )

            timeout_error = resume_data.get('_timeout_error')
            if timeout_error:
                self.logger.warning(f"Resume parsing timed out: {timeout_error}")
                if not has_email_data and not has_name:
                    parsed_email.status = 'failed'
                    parsed_email.processed_at = datetime.utcnow()
                    parsed_email.processing_notes = f"Resume parsing timed out and no candidate info in email body"
                    db.session.commit()
                    self._notify_admin_parse_failure(parsed_email, timeout_error, extraction_summary)
                    result['success'] = False
                    result['message'] = timeout_error
                    return result
                else:
                    self.logger.info(f"AI timed out but using fallback-extracted candidate info: {first_name} {last_name}")

            if not has_name and not has_contact:
                if not attachments:
                    error_msg = "No resume attachment found in email and could not extract candidate info from email body"
                elif not resume_file:
                    error_msg = f"No supported resume file found (received: {[a['filename'] for a in attachments]}) and no candidate info in email body"
                elif not resume_text:
                    error_msg = f"Could not extract text from resume '{resume_file['filename']}' - may be password-protected, scanned image, or corrupted"
                elif not resume_data or len(resume_data) == 0:
                    error_msg = "AI could not extract any information from resume and no candidate info in email body"
                else:
                    error_msg = "Could not extract candidate name or contact information from email or resume (all 5 fallback layers exhausted)"

                self.logger.warning(f"Early validation failed: {error_msg}")
                parsed_email.status = 'failed'
                parsed_email.processed_at = datetime.utcnow()
                parsed_email.processing_notes = error_msg
                db.session.commit()
                self._notify_admin_parse_failure(parsed_email, error_msg, extraction_summary)
                result['success'] = False
                result['message'] = error_msg
                return result

            self.logger.info(f"Validation passed: name={first_name} {last_name}, email={candidate_email}, phone={candidate_phone}")

            from app import get_bullhorn_service
            bullhorn = get_bullhorn_service()

            duplicate_id, confidence = self.find_duplicate_candidate(
                candidate_email, candidate_phone, first_name, last_name, bullhorn
            )

            parsed_email.is_duplicate_candidate = duplicate_id is not None
            parsed_email.duplicate_confidence = confidence

            bullhorn_data = self.map_to_bullhorn_fields(
                email_candidate, resume_data, source,
                email_candidate.get('work_authorization')
            )

            self.logger.info(f"Bullhorn candidate data:")
            self.logger.info(f"  - occupation (title): {bullhorn_data.get('occupation')}")
            self.logger.info(f"  - companyName: {bullhorn_data.get('companyName')}")
            self.logger.info(f"  - skillSet: {bullhorn_data.get('skillSet', '')[:100]}...")
            self.logger.info(f"  - employmentPreference: {bullhorn_data.get('employmentPreference')}")
            self.logger.info(f"  - description (Resume pane) length: {len(bullhorn_data.get('description', ''))} chars")

            if duplicate_id and confidence >= 0.80:
                existing_candidate = bullhorn.get_candidate(duplicate_id)
                enriched_data = self._build_enrichment_update(existing_candidate, bullhorn_data)
                if enriched_data:
                    candidate_id = bullhorn.update_candidate(duplicate_id, enriched_data)
                    self.logger.info(f"Enriched existing candidate {candidate_id} with {len(enriched_data)} fields: {list(enriched_data.keys())}")
                else:
                    candidate_id = duplicate_id
                    self.logger.info(f"Existing candidate {candidate_id} already has all fields populated — no enrichment needed")
                result['is_duplicate'] = True
            else:
                candidate_id = bullhorn.create_candidate(bullhorn_data)
                self.logger.info(f"Created new candidate {candidate_id}")

            parsed_email.bullhorn_candidate_id = candidate_id
            result['candidate_id'] = candidate_id

            is_new_candidate = not result.get('is_duplicate')

            if is_new_candidate and candidate_id and resume_data.get('work_history'):
                work_history = resume_data.get('work_history', [])
                if work_history:
                    self.logger.info(f"Creating {len(work_history)} work history records for NEW candidate {candidate_id}")
                    work_ids = bullhorn.create_candidate_work_history(candidate_id, work_history)
                    self.logger.info(f"Created work history records: {work_ids}")
            elif not is_new_candidate:
                self.logger.info(f"Skipping work history creation for existing candidate {candidate_id} to avoid duplicates")

            if is_new_candidate and candidate_id and resume_data.get('education'):
                education = resume_data.get('education', [])
                if education:
                    self.logger.info(f"Creating {len(education)} education records for NEW candidate {candidate_id}")
                    edu_ids = bullhorn.create_candidate_education(candidate_id, education)
                    self.logger.info(f"Created education records: {edu_ids}")
            elif not is_new_candidate:
                self.logger.info(f"Skipping education creation for existing candidate {candidate_id} to avoid duplicates")

            note_status = "not_attempted"
            note_id_created = None

            if candidate_id:
                note_created = False

                if resume_data.get('summary'):
                    resume_filename = resume_file['filename'] if resume_file else None
                    if self._check_existing_resume_summary(bullhorn, candidate_id, resume_filename):
                        self.logger.info(f"Skipped duplicate AI Resume Summary for candidate {candidate_id}")
                        note_created = True
                        note_status = "ai_summary_dedup_skipped"
                    else:
                        summary = resume_data.get('summary')
                        note_text = f"AI-Generated Resume Summary:\n\n{summary}"
                        if resume_data.get('skills'):
                            skills_preview = ', '.join(resume_data['skills'][:10])
                            note_text += f"\n\nKey Skills: {skills_preview}"
                        if resume_data.get('years_experience'):
                            note_text += f"\n\nExperience: {resume_data['years_experience']} years"

                        note_id = bullhorn.create_candidate_note(candidate_id, note_text, "AI Resume Summary")
                        if note_id:
                            self.logger.info(f"Created AI summary note {note_id} for candidate {candidate_id}")
                            note_created = True
                            note_status = "ai_summary_created"
                            note_id_created = note_id
                        else:
                            self.logger.warning(f"Failed to create AI summary note for candidate {candidate_id}")
                            note_status = "ai_summary_failed"

                if not note_created:
                    self.logger.info(f"Creating fallback application note for candidate {candidate_id}")

                    note_parts = [f"Job Application Received via {source}"]
                    note_parts.append(f"\nDate: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

                    if job_id:
                        note_parts.append(f"\nApplied to Job ID: {job_id}")

                    if resume_data.get('current_title'):
                        note_parts.append(f"\nCurrent Title: {resume_data.get('current_title')}")
                    if resume_data.get('current_company'):
                        note_parts.append(f"\nCurrent Company: {resume_data.get('current_company')}")
                    if resume_data.get('skills'):
                        skills_preview = ', '.join(resume_data['skills'][:8])
                        note_parts.append(f"\nSkills: {skills_preview}")
                    if resume_data.get('years_experience'):
                        note_parts.append(f"\nExperience: {resume_data['years_experience']} years")

                    if not resume_data.get('current_title') and email_candidate.get('first_name'):
                        note_parts.append(f"\nCandidate: {email_candidate.get('first_name', '')} {email_candidate.get('last_name', '')}")

                    if result.get('is_duplicate'):
                        note_parts.append("\n\nNote: Candidate was identified as existing in database (duplicate)")

                    fallback_note = ''.join(note_parts)
                    fallback_note_id = bullhorn.create_candidate_note(candidate_id, fallback_note, "Application Received")
                    if fallback_note_id:
                        self.logger.info(f"Created fallback application note {fallback_note_id} for candidate {candidate_id}")
                        note_status = "fallback_created"
                        note_id_created = fallback_note_id
                    else:
                        self.logger.error(f"Failed to create any note for candidate {candidate_id}")
                        note_status = "all_notes_failed"

            if resume_file and candidate_id:
                self.logger.info(f"Uploading resume '{resume_file['filename']}' to candidate {candidate_id}")
                file_id = bullhorn.upload_candidate_file(
                    candidate_id,
                    resume_file['content'],
                    resume_file['filename']
                )
                if file_id:
                    self.logger.info(f"Successfully uploaded resume to Bullhorn, file ID: {file_id}")
                else:
                    self.logger.warning(f"Failed to upload resume to Bullhorn for candidate {candidate_id}")
                parsed_email.resume_file_id = file_id

            is_returning = result.get('is_duplicate', False)
            self.logger.info(f"Job submission check: job_id={job_id}, candidate_id={candidate_id}, returning_applicant={is_returning}")
            if job_id and candidate_id:
                self.logger.info(f"Creating job submission for {'RETURNING' if is_returning else 'NEW'} applicant: candidate {candidate_id} -> job {job_id}")
                submission_id = bullhorn.create_job_submission(candidate_id, job_id, source)
                if submission_id:
                    parsed_email.bullhorn_submission_id = submission_id
                    result['submission_id'] = submission_id
                    self.logger.info(f"Created job submission {submission_id} for candidate {candidate_id} -> job {job_id} (pipeline entry confirmed)")
                else:
                    self.logger.warning(f"Failed to create job submission for candidate {candidate_id} -> job {job_id}")
            elif not job_id:
                self.logger.warning(f"No job ID extracted - cannot create job submission")
            elif not candidate_id:
                self.logger.warning(f"No candidate ID - cannot create job submission")

            parsed_email.status = 'completed'
            parsed_email.processed_at = datetime.utcnow()
            note_info = f", Note: {note_status}"
            if note_id_created:
                note_info += f" (ID: {note_id_created})"
            parsed_email.processing_notes = f"Processed successfully. Candidate ID: {candidate_id}{note_info}"

            db.session.commit()

            result['success'] = True
            result['message'] = f"Successfully processed email and created/updated candidate {candidate_id}"

        except Exception as e:
            self.logger.error(f"Error processing inbound email: {e}", exc_info=True)
            result['message'] = str(e)

            if result.get('parsed_email_id'):
                try:
                    parsed_email = ParsedEmail.query.get(result['parsed_email_id'])
                    if parsed_email:
                        parsed_email.status = 'failed'
                        parsed_email.processing_notes = str(e)
                        db.session.commit()
                        self._notify_admin_parse_failure(parsed_email, str(e))
                except Exception:
                    pass

        return result
