import json
import base64
import tempfile
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any


logger = logging.getLogger(__name__)


class ResumeMixin:

    def map_to_bullhorn_fields(self, email_data: Dict, resume_data: Dict,
                                source: str, work_auth: str = None) -> Dict[str, Any]:
        """
        Map extracted data to Bullhorn candidate field names

        Priority: Email data > Resume data for basic fields
        Resume data provides enhanced info (skills, work history)
        """
        candidate = {}

        first_name = email_data.get('first_name') or resume_data.get('first_name') or ''
        last_name = email_data.get('last_name') or resume_data.get('last_name') or ''

        candidate['firstName'] = first_name
        candidate['lastName'] = last_name
        candidate['name'] = f"{first_name} {last_name}".strip()

        candidate['email'] = email_data.get('email') or resume_data.get('email')
        candidate['phone'] = email_data.get('phone') or resume_data.get('phone')

        candidate['address'] = {}
        if email_data.get('city') or resume_data.get('city'):
            candidate['address']['city'] = email_data.get('city') or resume_data.get('city')
        if email_data.get('state') or resume_data.get('state'):
            candidate['address']['state'] = email_data.get('state') or resume_data.get('state')
        if resume_data.get('country'):
            candidate['address']['countryName'] = resume_data.get('country')

        bullhorn_source = self.SOURCE_TO_BULLHORN.get(source, 'Other')
        candidate['source'] = bullhorn_source

        candidate['status'] = 'Online Applicant'

        work_auth = work_auth or email_data.get('work_authorization')
        if work_auth:
            visa_type = self.WORK_AUTH_TO_VISA_TYPE.get(work_auth, work_auth)
            candidate['customText1'] = visa_type

        if resume_data.get('current_title'):
            candidate['occupation'] = resume_data['current_title']

        if resume_data.get('current_company'):
            candidate['companyName'] = resume_data['current_company']

        if resume_data.get('skills'):
            candidate['skillSet'] = ', '.join(resume_data['skills'][:20])

        if resume_data.get('years_experience'):
            try:
                years = int(resume_data['years_experience'])
                candidate['customInt1'] = years
            except (ValueError, TypeError):
                pass

        if resume_data.get('formatted_html'):
            formatted_html = resume_data['formatted_html']
            max_length = 50000
            if len(formatted_html) > max_length:
                formatted_html = formatted_html[:max_length] + '<p><em>[Resume truncated due to length...]</em></p>'
            candidate['description'] = formatted_html
        elif resume_data.get('raw_text'):
            raw_text = resume_data['raw_text']
            max_length = 50000
            if len(raw_text) > max_length:
                raw_text = raw_text[:max_length] + '\n\n[Resume truncated due to length...]'
            candidate['description'] = raw_text
        elif resume_data.get('summary'):
            candidate['description'] = resume_data['summary']

        if resume_data.get('linkedin_url'):
            candidate['customText9'] = resume_data['linkedin_url']

        return candidate

    def _check_existing_resume_summary(self, bullhorn, candidate_id: int,
                                         current_resume_filename: str = None) -> bool:
        """
        Check if an AI Resume Summary note already exists for this candidate
        within the last 24 hours for the same resume.

        Mirrors the proven vetting dedup pattern in candidate_vetting_service.py
        (create_candidate_note, lines 2746-2771): query get_candidate_notes()
        with action_filter + 24h window.

        Rules:
        1. If no AI Resume Summary exists in last 24h -> allow creation (return False)
        2. If one exists and current_resume_filename matches a recently processed
           ParsedEmail for this candidate -> skip (return True, duplicate)
        3. If one exists but resume filename differs -> allow (new resume)
        4. If no filename available -> enforce simple 24h rule (skip)
        5. Fail-safe: if the check itself errors -> allow creation (return False)

        Args:
            bullhorn: Authenticated BullhornService instance
            candidate_id: Bullhorn candidate ID
            current_resume_filename: Filename of the resume being processed

        Returns:
            True if a duplicate exists (should skip), False if safe to create.
        """
        try:
            existing_notes = bullhorn.get_candidate_notes(
                candidate_id,
                action_filter=["AI Resume Summary"],
                since=datetime.utcnow() - timedelta(hours=24)
            )

            if not existing_notes:
                return False

            if not current_resume_filename:
                self.logger.info(
                    f"RESUME SUMMARY DEDUP: Candidate {candidate_id} already has "
                    f"{len(existing_notes)} AI Resume Summary note(s) in last 24h. "
                    f"No filename to compare — skipping (24h fallback rule)."
                )
                return True

            from app import db
            from models import ParsedEmail as PE
            recent_emails = PE.query.filter(
                PE.bullhorn_candidate_id == candidate_id,
                PE.status == 'completed',
                PE.processed_at >= datetime.utcnow() - timedelta(hours=24),
                PE.resume_filename.isnot(None)
            ).order_by(PE.processed_at.desc()).all()

            previous_filenames = {pe.resume_filename for pe in recent_emails}

            if current_resume_filename in previous_filenames:
                self.logger.info(
                    f"RESUME SUMMARY DEDUP: Candidate {candidate_id} already has "
                    f"AI Resume Summary for resume '{current_resume_filename}' in last 24h. Skipping."
                )
                return True
            else:
                self.logger.info(
                    f"RESUME SUMMARY DEDUP: Candidate {candidate_id} has new resume "
                    f"'{current_resume_filename}' (previous: {previous_filenames}). Allowing new summary."
                )
                return False

        except Exception as e:
            self.logger.warning(
                f"Resume summary duplicate check failed (proceeding with creation): {e}"
            )
            return False

    def _extract_attachments(self, sendgrid_payload: Dict) -> List[Dict]:
        """
        Extract file attachments from SendGrid payload

        Returns list of dicts with 'filename', 'content', 'content_type'
        """
        attachments = []

        if 'attachments' in sendgrid_payload:
            try:
                att_data = json.loads(sendgrid_payload['attachments'])
                for att in att_data:
                    attachments.append({
                        'filename': att.get('filename', 'attachment'),
                        'content': base64.b64decode(att.get('content', '')),
                        'content_type': att.get('type', 'application/octet-stream')
                    })
            except Exception:
                pass

        for i in range(1, 11):
            att_key = f'attachment{i}'
            if att_key in sendgrid_payload:
                att_info_key = f'attachment-info'
                info = {}
                if att_info_key in sendgrid_payload:
                    try:
                        info = json.loads(sendgrid_payload[att_info_key])
                    except Exception:
                        pass

                content = sendgrid_payload[att_key]
                if isinstance(content, str):
                    content = content.encode()

                attachments.append({
                    'filename': info.get(att_key, {}).get('filename', f'attachment{i}'),
                    'content': content,
                    'content_type': info.get(att_key, {}).get('type', 'application/octet-stream')
                })

        return attachments

    def _is_resume_file(self, filename: str) -> bool:
        """Check if file is a resume based on extension"""
        resume_extensions = ['.pdf', '.doc', '.docx', '.rtf', '.txt']
        return any(filename.lower().endswith(ext) for ext in resume_extensions)

    def _get_resume_score(self, filename: str) -> int:
        """
        Score a file based on how likely it is to be a resume.
        Higher score = more likely to be a resume.

        This helps prioritize actual resumes over cover letters when multiple files are attached.
        """
        filename_lower = filename.lower()
        score = 0

        resume_keywords = ['resume', 'cv', 'curriculum']
        for keyword in resume_keywords:
            if keyword in filename_lower:
                score += 10

        non_resume_keywords = ['cover', 'letter', 'reference', 'portfolio', 'logo', 'photo', 'image']
        for keyword in non_resume_keywords:
            if keyword in filename_lower:
                score -= 10

        if filename_lower.endswith('.pdf'):
            score += 2
        elif filename_lower.endswith('.docx'):
            score += 1

        return score

    def _select_best_resume(self, attachments: List[Dict]) -> Optional[Dict]:
        """
        Select the best resume file from multiple attachments.
        Prioritizes files with 'resume' or 'cv' in the name.
        Deprioritizes files with 'cover', 'letter', etc.
        """
        resume_candidates = []

        for attachment in attachments:
            if self._is_resume_file(attachment['filename']):
                score = self._get_resume_score(attachment['filename'])
                resume_candidates.append((attachment, score))

        if not resume_candidates:
            return None

        resume_candidates.sort(key=lambda x: x[1], reverse=True)
        best_resume = resume_candidates[0][0]

        self.logger.info(f"Selected resume: {best_resume['filename']} (score: {resume_candidates[0][1]})")
        if len(resume_candidates) > 1:
            other_files = [f"{att['filename']} (score: {s})" for att, s in resume_candidates[1:]]
            self.logger.info(f"   Other candidates: {other_files}")

        return best_resume

    def _extract_resume_text(self, attachment: Dict) -> tuple:
        """
        Extract text content from resume file

        Uses existing resume_parser.py functionality

        Returns:
            tuple: (raw_text, formatted_html) - formatted_html contains proper HTML structure
                   for display in Bullhorn's Resume pane
        """
        from resume_parser import ResumeParser

        try:
            filename = attachment['filename']
            content = attachment['content']

            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as temp_file:
                temp_file.write(content)
                temp_path = temp_file.name

            try:
                parser = ResumeParser()
                result = parser.parse_resume(temp_path)

                if result.get('success'):
                    raw_text = result.get('raw_text', '')
                    formatted_html = result.get('formatted_html', '')
                    self.logger.info(f"Resume parsed: {len(raw_text)} chars raw, {len(formatted_html)} chars HTML")
                    return raw_text, formatted_html
                return '', ''
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            self.logger.error(f"Error extracting resume text: {e}")
            return '', ''
