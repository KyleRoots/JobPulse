import re
from typing import Dict, Any, Optional

from utils.candidate_name_extraction import (
    extract_name_from_pattern,
    strip_html_to_text,
)


class ExtractionMixin:

    def detect_source(self, sender: str, subject: str, body: str) -> str:
        """
        Detect the source platform from email metadata

        Returns:
            Source name matching Bullhorn dropdown values
        """
        sender_lower = sender.lower()
        subject_lower = subject.lower()
        body_lower = body.lower()[:2000]

        for source, patterns in self.SOURCE_PATTERNS.items():
            for pattern in patterns['sender_patterns']:
                if pattern.lower() in sender_lower:
                    self.logger.info(f"Source detected from sender: {source}")
                    return source

            for pattern in patterns['subject_patterns']:
                if pattern.lower() in subject_lower:
                    self.logger.info(f"Source detected from subject: {source}")
                    return source

            matches = sum(1 for pattern in patterns['body_patterns']
                         if pattern.lower() in body_lower)
            if matches >= 2:
                self.logger.info(f"Source detected from body: {source}")
                return source

        self.logger.warning("Could not detect source, defaulting to 'Other'")
        return 'Other'

    def extract_bullhorn_job_id(self, subject: str, body: str, source: str = None) -> Optional[int]:
        """
        Extract Bullhorn Job ID from email subject or body

        Patterns:
        - Subject: "Job Title (34613) - Candidate Name"
        - Subject: "Job Title - Azure (34707) - Candidate Name"
        - Dice subject: "Job ID - 33633 | UX Designer - Moises Frausto has applied"
        - Body: "Bullhorn ID: 34613"
        """
        self.logger.info(f"Extracting job ID from subject: {subject[:100]}...")

        dice_match = re.search(r'Job\s*ID\s*[-–]\s*(\d{4,6})', subject, re.IGNORECASE)
        if dice_match:
            job_id = int(dice_match.group(1))
            self.logger.info(f"Extracted job ID from Dice 'Job ID -' format: {job_id}")
            return job_id

        match = re.search(r'\((\d{4,6})\)', subject)
        if match:
            job_id = int(match.group(1))
            self.logger.info(f"Extracted job ID from subject parentheses: {job_id}")
            return job_id

        match = re.search(r'Bullhorn\s*ID[:\s]+(\d{4,6})', body, re.IGNORECASE)
        if match:
            job_id = int(match.group(1))
            self.logger.info(f"Extracted job ID from body 'Bullhorn ID': {job_id}")
            return job_id

        matches = re.findall(r'\b(\d{5})\b', subject)
        if matches:
            job_id = int(matches[0])
            self.logger.info(f"Extracted job ID from subject (5-digit): {job_id}")
            return job_id

        self.logger.warning(f"Could not extract Bullhorn job ID from email. Subject: {subject}")
        return None

    def extract_candidate_from_email(self, subject: str, body: str, source: str) -> Dict[str, Any]:
        """
        Extract candidate information directly from email content

        Returns dict with:
        - first_name, last_name, email, phone
        - work_authorization, location
        """
        candidate = {
            'first_name': None,
            'last_name': None,
            'email': None,
            'phone': None,
            'work_authorization': None,
            'location': None,
            'city': None,
            'state': None
        }

        if source == 'Dice':
            candidate.update(self._extract_dice_candidate(subject, body))
        elif source == 'LinkedIn Job Board':
            candidate.update(self._extract_linkedin_candidate(subject, body))
        else:
            candidate.update(self._extract_generic_candidate(subject, body))

        return candidate

    def _extract_dice_candidate(self, subject: str, body: str) -> Dict[str, Any]:
        """Extract candidate info from Dice email format"""
        result = {}

        cleaned_subject = re.sub(r'\s*\([A-Za-z][^)]*\)\s*', ' ', subject)
        first, last = extract_name_from_pattern(cleaned_subject, r'-\s*')
        if first or last:
            result['first_name'] = first
            result['last_name'] = last

        body_text = strip_html_to_text(body)

        email_match = re.search(r'Email[:\s]+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', body_text)
        if email_match:
            result['email'] = email_match.group(1).lower()

        phone_match = re.search(r'Phone[:\s]+(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', body_text)
        if phone_match:
            result['phone'] = phone_match.group(1)

        work_auth_match = re.search(r'Work\s*Authorization[:\s]+([^\n<]+)', body, re.IGNORECASE)
        if work_auth_match:
            result['work_authorization'] = work_auth_match.group(1).strip()

        location_match = re.search(r'Location[:\s]+([^\n<]+)', body, re.IGNORECASE)
        if location_match:
            location = location_match.group(1).strip()
            result['location'] = location
            loc_parts = location.split(',')
            if len(loc_parts) >= 2:
                result['city'] = loc_parts[0].strip()
                result['state'] = loc_parts[1].strip()

        return result

    def _extract_linkedin_candidate(self, subject: str, body: str) -> Dict[str, Any]:
        """Extract candidate info from LinkedIn email format"""
        result = {}

        body_text = strip_html_to_text(body)

        first, last = extract_name_from_pattern(subject, r'-\s*')
        if first or last:
            result['first_name'] = first
            result['last_name'] = last

        if not result.get('first_name'):
            first, last = extract_name_from_pattern(body_text, r'Name[:\s]+')
            if first or last:
                result['first_name'] = first
                result['last_name'] = last

        email_match = re.search(r'Email[:\s]+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', body_text)
        if email_match:
            result['email'] = email_match.group(1).lower()

        phone_match = re.search(r'Phone[:\s]+(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', body_text)
        if phone_match:
            result['phone'] = phone_match.group(1)

        return result

    def _extract_generic_candidate(self, subject: str, body: str) -> Dict[str, Any]:
        """Generic candidate extraction for unknown sources"""
        result = {}

        body_text = strip_html_to_text(body)

        email_match = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', body_text)
        if email_match:
            result['email'] = email_match.group(1).lower()

        phone_match = re.search(r'(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', body_text)
        if phone_match:
            result['phone'] = phone_match.group(1)

        first, last = extract_name_from_pattern(subject, r'-\s*')
        if not (first or last):
            first, last = extract_name_from_pattern(subject, r'(?:from|by)\s+')
        if first or last:
            result['first_name'] = first
            result['last_name'] = last

        return result
