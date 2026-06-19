import logging
from datetime import datetime, timedelta
from typing import Dict, Any

from utils.candidate_name_extraction import (
    build_extraction_summary,
    is_cta_phrase,
    is_valid_name,
    parse_name_from_email_address,
    parse_name_from_filename,
    strip_html_to_text,
)

logger = logging.getLogger(__name__)


class ProcessingMixin:

    # Default cross-route de-dupe window (minutes). The two transport copies of
    # one application (Exchange copy via the mailbox-pull path + SendGrid copy
    # via the inbound webhook) arrive seconds-to-minutes apart; genuine
    # re-applies are hours/days apart and must be preserved.
    _CROSS_ROUTE_DEFAULT_WINDOW_MIN = 30

    def _cross_route_dedupe_enabled(self) -> bool:
        """Master switch (DB-backed, tunable without republish). Default ON."""
        try:
            from models import VettingConfig
            val = VettingConfig.get_value('cross_route_dedupe_enabled', 'true')
            return (val or 'true').strip().lower() == 'true'
        except Exception:  # noqa: BLE001 — config read must never break intake
            return True

    def _cross_route_dedupe_window_minutes(self) -> int:
        """Window (minutes) within which a sibling submission collapses the
        duplicate. 0 (or negative) disables the guard. Default 30, capped 24h."""
        try:
            from models import VettingConfig
            raw = VettingConfig.get_value(
                'cross_route_dedupe_window_minutes',
                str(self._CROSS_ROUTE_DEFAULT_WINDOW_MIN))
            minutes = int(raw)
        except (TypeError, ValueError):
            minutes = self._CROSS_ROUTE_DEFAULT_WINDOW_MIN
        except Exception:  # noqa: BLE001
            return self._CROSS_ROUTE_DEFAULT_WINDOW_MIN
        return max(0, min(minutes, 1440))

    def _find_cross_route_sibling(self, parsed_email_id, candidate_id, job_id):
        """Return a recent sibling ParsedEmail that ALREADY produced a Bullhorn
        submission for the SAME (candidate, job) within the configured window —
        i.e. the first transport copy of this same application — or None.

        Keyed on the resolved Bullhorn candidate_id (both transport copies
        resolve to the same candidate via candidate-level dedupe) + job_id, so
        it is independent of which mail system stamped the Message-ID. Requires
        a non-null bullhorn_submission_id so we only collapse against a copy
        that actually reached the pipeline — never against a still-processing or
        failed sibling, because dropping a real applicant is the exact failure
        this guard must avoid.
        """
        if not self._cross_route_dedupe_enabled():
            return None
        window = self._cross_route_dedupe_window_minutes()
        if window <= 0 or not candidate_id or not job_id:
            return None

        from models import ParsedEmail
        cutoff = datetime.utcnow() - timedelta(minutes=window)
        query = ParsedEmail.query.filter(
            ParsedEmail.bullhorn_candidate_id == candidate_id,
            ParsedEmail.bullhorn_job_id == job_id,
            ParsedEmail.bullhorn_submission_id.isnot(None),
            ParsedEmail.created_at >= cutoff,
        )
        if parsed_email_id is not None:
            query = query.filter(ParsedEmail.id != parsed_email_id)
        return query.order_by(ParsedEmail.created_at.desc()).first()

    def _cross_route_lock_enabled(self) -> bool:
        """Whether to serialize concurrent processing of the same applicant with
        a Postgres advisory lock (DB-backed, tunable without republish). The
        _find_cross_route_sibling guard above only catches the SECOND copy once
        the FIRST has committed a submission; when the two transport copies race
        each other before either creates the candidate, only this lock prevents
        two separate Bullhorn candidate records. Default ON."""
        try:
            from models import VettingConfig
            val = VettingConfig.get_value('cross_route_lock_enabled', 'true')
            return (val or 'true').strip().lower() == 'true'
        except Exception:  # noqa: BLE001 — config read must never break intake
            return True

    def _acquire_candidate_identity_xact_lock(self, environment_id,
                                              candidate_email):
        """Serialize concurrent processing of the SAME applicant (per
        environment) so two transport copies arriving at the same instant cannot
        each mint a separate Bullhorn candidate.

        Both copies of one application (Exchange via mailbox-pull + SendGrid via
        the inbound webhook) resolve to the same person, but when they run
        concurrently BOTH can pass find_duplicate_candidate before either has
        created the candidate → two candidate records (observed in prod). A
        transaction-scoped Postgres advisory lock keyed on (environment,
        normalized email) makes the second copy WAIT until the first commits; it
        then sees the first copy's candidate via find_duplicate_candidate and the
        existing cross-route guard collapses the duplicate submission. The lock
        auto-releases on the transaction's commit/rollback (no leak across the
        connection pool, and a crashed worker frees it when its connection
        closes).

        Keyed on email only (not job) so two simultaneous applies by the same
        person to different jobs also serialize — preventing a duplicate
        candidate without ever dropping the second job's submission. No-op when
        disabled or when no email is available. FAILS OPEN on any error
        (including non-Postgres backends in tests): a locking problem must never
        block a real applicant from reaching Bullhorn.
        """
        if not candidate_email or not self._cross_route_lock_enabled():
            return
        try:
            from app import db
            from sqlalchemy import text
            key = f"{environment_id or 0}:{candidate_email.strip().lower()}"
            db.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": key})
            self.logger.debug(
                f"Candidate-identity lock held for '{key}' "
                f"(cross-route race guard)")
        except Exception as ex:  # noqa: BLE001 — fail OPEN, never block intake
            # A real DBAPI error leaves the transaction in an aborted state, so
            # every subsequent query (find_duplicate_candidate, the candidate
            # write) would raise — which would BLOCK intake, the opposite of the
            # fail-open contract. Roll back to a clean session before continuing.
            try:
                from app import db
                db.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            self.logger.warning(
                f"Candidate-identity lock skipped, continuing intake: {ex}")

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
            'parsed_email_id': None,
            'ignored': False
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

            # Attribute this inbound application to its owning environment by
            # the recipient (To) address. Single-tenant inbound resolves to the
            # default (Myticas) environment, so behavior is unchanged.
            from models import Brand
            environment_id = Brand.resolve_environment_id_for_recipient(recipient)

            parsed_email = ParsedEmail(
                message_id=message_id,
                sender_email=sender,
                recipient_email=recipient,
                subject=subject,
                status='processing',
                received_at=datetime.utcnow(),
                environment_id=environment_id
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

            # CTA-Reject Guard: if the resolved (first, last) pair is a
            # job-board call-to-action phrase ("Invite Friend", "Apply
            # Now", "View Profile", etc.), drop it and let the recovery
            # layers below (filename → email local-part → last-resort
            # AI) try again. Prefers resume_data alone first since the
            # poisoned value typically comes from the email body, not
            # the AI resume parser.
            if first_name and last_name and is_cta_phrase(f"{first_name} {last_name}"):
                self.logger.warning(
                    f"CTA-style name rejected: '{first_name} {last_name}' "
                    f"(likely scraped from email body). Trying resume_data alone, "
                    f"then falling to recovery layers."
                )
                rd_first = resume_data.get('first_name')
                rd_last = resume_data.get('last_name')
                if (rd_first and rd_last
                        and not is_cta_phrase(f"{rd_first} {rd_last}")
                        and is_valid_name(rd_first, rd_last)):
                    first_name, last_name = rd_first, rd_last
                    parsed_email.candidate_name = f"{first_name} {last_name}".strip()
                    self.logger.info(
                        f"CTA-Reject Guard recovered name from resume_data: "
                        f"{first_name} {last_name}"
                    )
                else:
                    first_name = None
                    last_name = None

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

            # Always overwrite source dicts — including to None when the
            # CTA-Reject Guard above (or any other recovery layer) ended
            # up with no valid name. Previously this was gated on `if
            # first_name:` which left CTA-poisoned values in
            # email_candidate, allowing them to leak into
            # map_to_bullhorn_fields() below.
            email_candidate['first_name'] = first_name
            resume_data['first_name'] = first_name
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
                sender_blank = not (parsed_email.sender_email or '').strip()
                subject_blank = not (parsed_email.subject or '').strip()
                if not attachments and (sender_blank or subject_blank):
                    # Non-candidate noise gate: an inbound message with no
                    # attachment, no extractable name/contact, AND a blank
                    # sender or subject was never a real candidate submission
                    # (bounces, auto-replies, delivery receipts, malformed
                    # webhook traffic). Record it for audit but DO NOT fire an
                    # admin parse-failure alert — these otherwise flood
                    # recruiter inboxes with "None None" notifications.
                    self.logger.debug(
                        f"Ignoring non-candidate inbound email (no attachment, "
                        f"no name/contact, blank sender/subject) from "
                        f"'{parsed_email.sender_email}' subject='{parsed_email.subject}'"
                    )
                    parsed_email.status = 'ignored'
                    parsed_email.processed_at = datetime.utcnow()
                    parsed_email.processing_notes = (
                        "Ignored non-candidate email: no attachment, no extractable "
                        "candidate info, blank sender/subject (not a submission)"
                    )
                    db.session.commit()
                    result['success'] = False
                    result['message'] = 'Ignored non-candidate email (not a submission)'
                    result['ignored'] = True
                    return result

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

            # ── Cross-route race guard (pre-create serialization) ────────────
            # Serialize concurrent processing of the SAME applicant so two
            # transport copies that race each other here cannot each create a
            # separate Bullhorn candidate. Held until THIS transaction commits
            # (candidate + submission done at the end), so a second concurrent
            # copy waits, then finds this copy's candidate via
            # find_duplicate_candidate and the cross-route guard below collapses
            # the duplicate. Fails open — never blocks intake.
            self._acquire_candidate_identity_xact_lock(
                environment_id, candidate_email)

            duplicate_id, confidence = self.find_duplicate_candidate(
                candidate_email, candidate_phone, first_name, last_name, bullhorn
            )

            parsed_email.is_duplicate_candidate = duplicate_id is not None
            parsed_email.duplicate_confidence = confidence

            feed = self.detect_feed(body)
            bullhorn_data = self.map_to_bullhorn_fields(
                email_candidate, resume_data, source,
                email_candidate.get('work_authorization'),
                feed=feed
            )

            self.logger.info(f"Bullhorn candidate data:")
            self.logger.info(f"  - occupation (title): {bullhorn_data.get('occupation')}")
            self.logger.info(f"  - companyName: {bullhorn_data.get('companyName')}")
            self.logger.info(f"  - skillSet: {bullhorn_data.get('skillSet', '')[:100]}...")
            self.logger.info(f"  - employmentPreference: {bullhorn_data.get('employmentPreference')}")
            self.logger.info(f"  - description (Resume pane) length: {len(bullhorn_data.get('description', ''))} chars")

            if duplicate_id and confidence >= 0.80:
                existing_candidate = bullhorn.get_candidate(duplicate_id)
                enriched_data = self._build_enrichment_update(
                    existing_candidate, bullhorn_data,
                    is_pando=self._is_pando_feed(feed)
                )
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

            # ── Cross-route duplicate guard ──────────────────────────────────
            # The SAME application can reach us as TWO inbox messages with
            # DIFFERENT Message-IDs — one stamped by Microsoft/Exchange (pulled
            # from the apply@ mailbox via Graph) and one by SendGrid (delivered
            # to the inbound webhook) — so the message_id dedupe at the top can
            # never link them. Both copies resolve to the SAME Bullhorn
            # candidate, so if a recent sibling ParsedEmail already created a
            # submission for this (candidate, job), THIS is the second copy:
            # collapse it WITHOUT a duplicate submission / résumé upload / notes.
            # Genuine re-applies (hours/days later) fall outside the window and
            # proceed normally (the standing "show repeat applies" decision).
            sibling = self._find_cross_route_sibling(
                parsed_email.id, candidate_id, job_id)
            if sibling is not None:
                self.logger.info(
                    f"Cross-route duplicate detected: candidate {candidate_id} "
                    f"→ job {job_id} already submitted by ParsedEmail "
                    f"{sibling.id} (submission {sibling.bullhorn_submission_id}) "
                    f"within {self._cross_route_dedupe_window_minutes()}m — "
                    f"skipping duplicate submission/upload/notes."
                )
                parsed_email.is_duplicate_candidate = True
                parsed_email.status = 'duplicate'
                parsed_email.processed_at = datetime.utcnow()
                parsed_email.processing_notes = (
                    f"Cross-route duplicate of ParsedEmail {sibling.id} "
                    f"(submission {sibling.bullhorn_submission_id}). Same "
                    f"application arrived via both mail transports; skipped "
                    f"duplicate Bullhorn submission, résumé upload, and notes."
                )
                db.session.commit()
                result['success'] = True
                result['is_duplicate'] = True
                result['duplicate'] = True
                result['candidate_id'] = candidate_id
                result['submission_id'] = sibling.bullhorn_submission_id
                result['message'] = (
                    f"Cross-route duplicate collapsed for candidate "
                    f"{candidate_id} → job {job_id}"
                )
                return result

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

    def recover_resume_for_existing_candidate(self, sendgrid_payload: Dict,
                                              candidate_id: int) -> Dict[str, Any]:
        """Repair an applicant that was ingested WITHOUT their résumé.

        Re-runs only the résumé half of the pipeline against an ALREADY-EXISTING
        Bullhorn candidate: extract + AI-parse the résumé from the re-fetched
        email, enrich the candidate's résumé-derived fields (Resume pane / skills
        / occupation, blank fields only), and attach the résumé file. It does NOT
        create a candidate and does NOT create a job submission (both already
        exist from the original run) — so it can never double-submit to Bullhorn.

        Reuses the exact same helpers as ``process_email`` so parsing/mapping
        stays identical. Returns a result dict; never raises.
        """
        result = {
            'success': False,
            'candidate_id': candidate_id,
            'resume_file_id': None,
            'resume_filename': None,
            'enriched_fields': [],
            'message': '',
        }
        try:
            subject = sendgrid_payload.get('subject', '')
            body_text = sendgrid_payload.get('text', '')
            body_html = sendgrid_payload.get('html', body_text)
            body = body_html if body_html else body_text
            sender = sendgrid_payload.get('from', '')

            source = self.detect_source(sender, subject, body)

            attachments = self._extract_attachments(sendgrid_payload)
            resume_file = self._select_best_resume(attachments)
            if not resume_file:
                result['message'] = 'No résumé attachment found in re-fetched email'
                return result

            resume_text, formatted_html = self._extract_resume_text(resume_file)
            if not resume_text:
                result['message'] = (
                    f"Could not extract text from resume "
                    f"'{resume_file['filename']}'"
                )
                return result

            resume_data = self.parse_resume_with_ai(resume_text)
            resume_data['raw_text'] = resume_text
            resume_data['formatted_html'] = formatted_html

            email_candidate = self.extract_candidate_from_email(subject, body, source)

            from app import get_bullhorn_service
            bullhorn = get_bullhorn_service()

            feed = self.detect_feed(body)
            bullhorn_data = self.map_to_bullhorn_fields(
                email_candidate, resume_data, source,
                email_candidate.get('work_authorization'),
                feed=feed
            )

            existing_candidate = bullhorn.get_candidate(candidate_id)
            enriched_data = self._build_enrichment_update(
                existing_candidate, bullhorn_data,
                is_pando=self._is_pando_feed(feed)
            )
            if enriched_data:
                bullhorn.update_candidate(candidate_id, enriched_data)
                result['enriched_fields'] = list(enriched_data.keys())
                self.logger.info(
                    f"Résumé recovery: enriched candidate {candidate_id} with "
                    f"{len(enriched_data)} field(s): {list(enriched_data.keys())}"
                )

            self.logger.info(
                f"Résumé recovery: uploading '{resume_file['filename']}' to "
                f"candidate {candidate_id}"
            )
            file_id = bullhorn.upload_candidate_file(
                candidate_id,
                resume_file['content'],
                resume_file['filename'],
            )
            result['resume_file_id'] = file_id
            result['resume_filename'] = resume_file['filename']
            result['success'] = file_id is not None
            result['message'] = (
                'Résumé attached + candidate enriched'
                if file_id else 'Enriched but résumé file upload failed'
            )
            return result
        except Exception as e:
            self.logger.error(
                f"Résumé recovery failed for candidate {candidate_id}: {e}",
                exc_info=True,
            )
            result['message'] = str(e)
            return result
