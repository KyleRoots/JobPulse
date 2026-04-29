import logging
import os
from datetime import datetime
from typing import Dict, Optional

from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _InboundCore:

    SOURCE_PATTERNS = {
        'Dice': {
            'sender_patterns': ['@dice.com', 'applicant@dice.com'],
            'subject_patterns': ['applied on Dice', 'Dice.com', 'Job ID -'],
            'body_patterns': ['Dice', 'Where tech connects']
        },
        'LinkedIn Job Board': {
            'sender_patterns': ['@linkedin.com', '@myticas.com'],
            'subject_patterns': ['applied on LinkedIn', 'has applied on LinkedIn'],
            'body_patterns': ['LinkedIn', 'Job posting is on behalf of']
        },
        'Indeed Job Board': {
            'sender_patterns': ['@indeed.com', '@indeedemail.com'],
            'subject_patterns': ['Indeed', 'applied on Indeed'],
            'body_patterns': ['Indeed', 'indeed.com']
        },
        'ZipRecruiter Job Board': {
            'sender_patterns': ['@ziprecruiter.com'],
            'subject_patterns': ['ZipRecruiter'],
            'body_patterns': ['ZipRecruiter']
        }
    }

    SOURCE_TO_BULLHORN = {
        'Dice': 'Dice',
        'LinkedIn Job Board': 'LinkedIn Job Board',
        'Indeed Job Board': 'Indeed Job Board',
        'ZipRecruiter Job Board': 'ZipRecruiter Job Board'
    }

    WORK_AUTH_TO_VISA_TYPE = {
        'US Citizen': 'US Citizen',
        'CAN Citizen': 'CAN Citizen',
        'US Perm Resident': 'US Perm Resident',
        'CAN Perm Resident': 'CAN Perm Resident',
        'Green Card Holder': 'Green Card Holder',
        'Green Card': 'Green Card Holder',
        'H1B': 'H1B',
        'H-1B': 'H1B',
        'L2-EAD': 'L2-EAD',
        'H4-EAD': 'H4-EAD',
        'OPT-EAD': 'OPT-EAD',
        'OPT': 'OPT-EAD',
        'GC-EAD': 'GC-EAD',
        'TN Visa': 'TN Visa',
        'TN': 'TN Visa',
        'CPT-EAD': 'CPT-EAD',
        'CPT': 'CPT-EAD'
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.openai_client = None
        self._init_openai()

    def _init_openai(self):
        """Initialize OpenAI client with 60-second timeout"""
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key:
            self.openai_client = OpenAI(
                api_key=api_key,
                timeout=60.0
            )
            self.logger.info("OpenAI client initialized for resume parsing (60s timeout)")
        else:
            self.logger.warning("OPENAI_API_KEY not set - AI resume parsing disabled")

    def _notify_admin_parse_failure(self, parsed_email, error_msg: str, extraction_summary: Optional[Dict] = None):
        """Send email notification to admin when a candidate fails to parse.

        When ``extraction_summary`` is provided (Layer 6), the email
        includes everything we *did* manage to extract — skills, current
        title, work history, partial name guesses — so the admin can
        manually create the candidate without re-doing the parsing work.
        """
        try:
            from email_service import EmailService
            from models import User

            admin_users = User.query.filter_by(is_admin=True).all()
            admin_emails = [u.email for u in admin_users if u.email]

            if not admin_emails:
                self.logger.warning("No admin emails found for parse failure notification")
                return

            email_svc = EmailService()
            candidate_name = parsed_email.candidate_name or 'Unknown'
            candidate_email = parsed_email.candidate_email or 'N/A'
            source = parsed_email.source_platform or 'Unknown'
            job_id = parsed_email.bullhorn_job_id or 'N/A'
            received = parsed_email.received_at.strftime('%Y-%m-%d %H:%M UTC') if parsed_email.received_at else 'N/A'
            resume = parsed_email.resume_filename or 'None'

            subject = f"[Scout Genius] Candidate Parse Failure — {candidate_name}"

            extraction_block = ''
            if extraction_summary:
                email_part = extraction_summary.get('email_extracted', {}) or {}
                resume_part = extraction_summary.get('resume_extracted', {}) or {}
                lines = ["", "--- Data We DID Extract (use to create candidate manually) ---"]
                lines.append(
                    f"From email subject/body: name={email_part.get('first_name')} {email_part.get('last_name')}, "
                    f"email={email_part.get('email')}, phone={email_part.get('phone')}"
                )
                lines.append(
                    f"From AI resume parser:   name={resume_part.get('first_name')} {resume_part.get('last_name')}, "
                    f"email={resume_part.get('email')}, phone={resume_part.get('phone')}"
                )
                if resume_part.get('current_title') or resume_part.get('current_company'):
                    lines.append(
                        f"Current role: {resume_part.get('current_title') or 'N/A'} at "
                        f"{resume_part.get('current_company') or 'N/A'}"
                    )
                if resume_part.get('years_experience') is not None:
                    lines.append(f"Years experience: {resume_part.get('years_experience')}")
                if resume_part.get('city') or resume_part.get('state'):
                    lines.append(
                        f"Location: {resume_part.get('city') or ''}, {resume_part.get('state') or ''}".rstrip(', ')
                    )
                if resume_part.get('skills_count'):
                    lines.append(
                        f"Skills extracted: {resume_part.get('skills_count')} "
                        f"(first 10: {resume_part.get('skills_preview')})"
                    )
                extraction_block = "\n" + "\n".join(lines) + "\n"

            body = (
                f"A candidate application has failed to parse inside the ATS.\n\n"
                f"--- Failure Details ---\n"
                f"Candidate Name: {candidate_name}\n"
                f"Candidate Email: {candidate_email}\n"
                f"Source Platform: {source}\n"
                f"Bullhorn Job ID: {job_id}\n"
                f"Resume File: {resume}\n"
                f"Received At: {received}\n"
                f"Processed At: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                f"--- Error ---\n"
                f"{error_msg}\n\n"
                f"--- Notes ---\n"
                f"{parsed_email.processing_notes or 'N/A'}\n"
                f"{extraction_block}"
                f"\nThis candidate profile did not make it through the parsing workflow. "
                f"Please review and take corrective action if needed.\n\n"
                f"— Scout Genius Automation"
            )

            for admin_email in admin_emails:
                email_svc.send_notification_email(
                    to_email=admin_email,
                    subject=subject,
                    message=body,
                    notification_type='parse_failure'
                )

            self.logger.info(f"Parse failure notification sent to {len(admin_emails)} admin(s) for candidate: {candidate_name}")
        except Exception as e:
            self.logger.error(f"Failed to send parse failure notification: {e}")
