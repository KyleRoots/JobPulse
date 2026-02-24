"""
Email Inbound Parsing Service
Handles incoming emails from SendGrid Inbound Parse, extracts candidate data,
parses resumes with AI, and creates/updates records in Bullhorn.
"""
import logging
import re
import json
import tempfile
import os
import base64
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List, Any
from email import message_from_string
from email.utils import parseaddr

from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EmailInboundService:
    """Service for processing inbound emails from job boards and creating Bullhorn candidates"""
    
    # Source detection patterns
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
    
    # Bullhorn field mappings
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
                timeout=60.0  # 60-second timeout to prevent hanging
            )
            self.logger.info("OpenAI client initialized for resume parsing (60s timeout)")
        else:
            self.logger.warning("OPENAI_API_KEY not set - AI resume parsing disabled")
    
    def _notify_admin_parse_failure(self, parsed_email, error_msg: str):
        """Send email notification to admin when a candidate fails to parse."""
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
                f"{parsed_email.processing_notes or 'N/A'}\n\n"
                f"This candidate profile did not make it through the parsing workflow. "
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
            
            self.logger.info(f"📧 Parse failure notification sent to {len(admin_emails)} admin(s) for candidate: {candidate_name}")
        except Exception as e:
            self.logger.error(f"Failed to send parse failure notification: {e}")
    
    def detect_source(self, sender: str, subject: str, body: str) -> str:
        """
        Detect the source platform from email metadata
        
        Returns:
            Source name matching Bullhorn dropdown values
        """
        sender_lower = sender.lower()
        subject_lower = subject.lower()
        body_lower = body.lower()[:2000]  # Check first 2000 chars of body
        
        for source, patterns in self.SOURCE_PATTERNS.items():
            # Check sender patterns
            for pattern in patterns['sender_patterns']:
                if pattern.lower() in sender_lower:
                    self.logger.info(f"Source detected from sender: {source}")
                    return source
            
            # Check subject patterns
            for pattern in patterns['subject_patterns']:
                if pattern.lower() in subject_lower:
                    self.logger.info(f"Source detected from subject: {source}")
                    return source
            
            # Check body patterns (less reliable, use as fallback)
            matches = sum(1 for pattern in patterns['body_patterns'] 
                         if pattern.lower() in body_lower)
            if matches >= 2:  # Require at least 2 body pattern matches
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
        
        # Pattern 1: Dice format "Job ID - XXXXX" in subject
        dice_match = re.search(r'Job\s*ID\s*[-–]\s*(\d{4,6})', subject, re.IGNORECASE)
        if dice_match:
            job_id = int(dice_match.group(1))
            self.logger.info(f"Extracted job ID from Dice 'Job ID -' format: {job_id}")
            return job_id
        
        # Pattern 2: ID in parentheses in subject
        match = re.search(r'\((\d{4,6})\)', subject)
        if match:
            job_id = int(match.group(1))
            self.logger.info(f"Extracted job ID from subject parentheses: {job_id}")
            return job_id
        
        # Pattern 3: "Bullhorn ID:" in body
        match = re.search(r'Bullhorn\s*ID[:\s]+(\d{4,6})', body, re.IGNORECASE)
        if match:
            job_id = int(match.group(1))
            self.logger.info(f"Extracted job ID from body 'Bullhorn ID': {job_id}")
            return job_id
        
        # Pattern 4: Just a 5-digit number in subject (common job ID format)
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
            # Generic extraction
            candidate.update(self._extract_generic_candidate(subject, body))
        
        return candidate
    
    def _extract_dice_candidate(self, subject: str, body: str) -> Dict[str, Any]:
        """Extract candidate info from Dice email format"""
        result = {}
        
        # Extract name from subject: "Job Title (ID) - Christopher (Chris) Huebner has applied"
        name_match = re.search(r'-\s*([A-Za-z]+(?:\s*\([^)]+\))?\s+[A-Za-z]+)\s+has applied', subject)
        if name_match:
            full_name = name_match.group(1)
            # Remove nickname in parentheses
            full_name = re.sub(r'\s*\([^)]+\)\s*', ' ', full_name).strip()
            parts = full_name.split()
            if len(parts) >= 2:
                result['first_name'] = parts[0]
                result['last_name'] = parts[-1]
        
        # Extract email from body
        email_match = re.search(r'Email[:\s]+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', body)
        if email_match:
            result['email'] = email_match.group(1).lower()
        
        # Extract phone from body
        phone_match = re.search(r'Phone[:\s]+(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', body)
        if phone_match:
            result['phone'] = phone_match.group(1)
        
        # Extract work authorization
        work_auth_match = re.search(r'Work\s*Authorization[:\s]+([^\n<]+)', body, re.IGNORECASE)
        if work_auth_match:
            result['work_authorization'] = work_auth_match.group(1).strip()
        
        # Extract location
        location_match = re.search(r'Location[:\s]+([^\n<]+)', body, re.IGNORECASE)
        if location_match:
            location = location_match.group(1).strip()
            result['location'] = location
            # Try to parse city, state
            loc_parts = location.split(',')
            if len(loc_parts) >= 2:
                result['city'] = loc_parts[0].strip()
                result['state'] = loc_parts[1].strip()
        
        return result
    
    def _extract_linkedin_candidate(self, subject: str, body: str) -> Dict[str, Any]:
        """Extract candidate info from LinkedIn email format"""
        result = {}
        
        # Extract name from subject: "Job Title (ID) - Rahul Kauldhar has applied on LinkedIn"
        name_match = re.search(r'-\s*([A-Za-z]+\s+[A-Za-z]+)\s+has applied', subject)
        if name_match:
            parts = name_match.group(1).split()
            if len(parts) >= 2:
                result['first_name'] = parts[0]
                result['last_name'] = parts[-1]
        
        # Extract name from body: "Name: Rahul Kauldhar"
        name_body_match = re.search(r'Name[:\s]+([A-Za-z]+\s+[A-Za-z]+)', body)
        if name_body_match and not result.get('first_name'):
            parts = name_body_match.group(1).split()
            if len(parts) >= 2:
                result['first_name'] = parts[0]
                result['last_name'] = parts[-1]
        
        # Extract email from body
        email_match = re.search(r'Email[:\s]+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', body)
        if email_match:
            result['email'] = email_match.group(1).lower()
        
        # Extract phone from body
        phone_match = re.search(r'Phone[:\s]+(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', body)
        if phone_match:
            result['phone'] = phone_match.group(1)
        
        return result
    
    def _extract_generic_candidate(self, subject: str, body: str) -> Dict[str, Any]:
        """Generic candidate extraction for unknown sources"""
        result = {}
        
        # Extract email
        email_match = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', body)
        if email_match:
            result['email'] = email_match.group(1).lower()
        
        # Extract phone
        phone_match = re.search(r'(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', body)
        if phone_match:
            result['phone'] = phone_match.group(1)
        
        # Try to extract name from subject
        name_match = re.search(r'-\s*([A-Za-z]+\s+[A-Za-z]+)\s+(?:has applied|applied)', subject)
        if name_match:
            parts = name_match.group(1).split()
            if len(parts) >= 2:
                result['first_name'] = parts[0]
                result['last_name'] = parts[-1]
        
        return result
    
    def parse_resume_with_ai(self, resume_text: str) -> Dict[str, Any]:
        """
        Use OpenAI GPT-4 to extract structured data from resume text
        
        Returns comprehensive candidate profile
        """
        if not self.openai_client:
            self.logger.warning("OpenAI not available, skipping AI resume parsing")
            return {}
        
        if not resume_text or len(resume_text.strip()) < 50:
            self.logger.warning("Resume text too short for AI parsing")
            return {}
        
        try:
            prompt = f"""Analyze this resume and extract structured candidate information.
Return a JSON object with the following fields (use null for missing data):

{{
    "first_name": "string",
    "last_name": "string",
    "email": "string",
    "phone": "string",
    "city": "string",
    "state": "string (2-letter code for US/Canada)",
    "country": "string",
    "current_title": "string (most recent job title)",
    "current_company": "string (most recent employer)",
    "years_experience": number (total years of professional experience),
    "skills": ["array", "of", "technical", "skills"],
    "education": [
        {{"degree": "string", "field": "string", "institution": "string", "year": number}}
    ],
    "certifications": ["array", "of", "certifications"],
    "work_history": [
        {{"title": "string", "company": "string", "start_year": number, "end_year": number, "current": boolean}}
    ],
    "summary": "2-3 sentence professional summary"
}}

Resume text:
{resume_text[:8000]}
"""

            # gpt-4.1-mini: cost-optimized for structured JSON extraction from resumes
            response = self.openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "You are an expert resume parser. Extract structured data accurately. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=2000
            )
            
            content = response.choices[0].message.content.strip()
            
            # Clean up JSON response
            if content.startswith('```json'):
                content = content[7:]
            if content.startswith('```'):
                content = content[3:]
            if content.endswith('```'):
                content = content[:-3]
            
            parsed = json.loads(content)
            self.logger.info(f"AI parsed resume successfully: {parsed.get('first_name')} {parsed.get('last_name')}")
            return parsed
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse AI response as JSON: {e}")
            return {}
        except TimeoutError as e:
            self.logger.error(f"⏰ OpenAI API timeout (60s) during resume parsing: {e}")
            return {'_timeout_error': 'OpenAI API timeout - resume parsing took too long'}
        except Exception as e:
            error_type = type(e).__name__
            if 'timeout' in str(e).lower() or 'timed out' in str(e).lower():
                self.logger.error(f"⏰ OpenAI API timeout during resume parsing: {e}")
                return {'_timeout_error': 'OpenAI API timeout - resume parsing took too long'}
            self.logger.error(f"AI resume parsing error ({error_type}): {e}")
            return {}  # Return empty dict for non-timeout errors - allow fallback to email data
    
    def find_duplicate_candidate(self, email: str, phone: str, first_name: str, last_name: str, 
                                  bullhorn_service) -> Tuple[Optional[int], float]:
        """
        Search Bullhorn for existing candidate
        
        Returns:
            Tuple of (candidate_id, confidence_score)
            confidence_score: 1.0 for exact email match, 0.9 for phone match, 
                            0.7+ for fuzzy name match
        """
        try:
            # First, search by email (most reliable)
            if email:
                results = bullhorn_service.search_candidates(email=email)
                if results and len(results) > 0:
                    candidate_id = results[0].get('id')
                    self.logger.info(f"Found duplicate candidate by email: {candidate_id}")
                    return candidate_id, 1.0
            
            # Second, search by phone
            if phone:
                # Normalize phone for search
                phone_digits = re.sub(r'\D', '', phone)
                if len(phone_digits) >= 10:
                    results = bullhorn_service.search_candidates(phone=phone_digits)
                    if results and len(results) > 0:
                        candidate_id = results[0].get('id')
                        self.logger.info(f"Found duplicate candidate by phone: {candidate_id}")
                        return candidate_id, 0.9
            
            # Third, search by name (less reliable, requires AI validation)
            if first_name and last_name:
                results = bullhorn_service.search_candidates(
                    first_name=first_name, 
                    last_name=last_name
                )
                if results and len(results) > 0:
                    # Use AI to validate if this is truly a duplicate
                    confidence = self._ai_validate_duplicate(
                        first_name, last_name, email, phone, results[0]
                    )
                    if confidence >= 0.7:
                        candidate_id = results[0].get('id')
                        self.logger.info(f"Found potential duplicate by name with confidence {confidence}: {candidate_id}")
                        return candidate_id, confidence
            
            return None, 0.0
            
        except Exception as e:
            self.logger.error(f"Error searching for duplicate candidate: {e}")
            return None, 0.0
    
    def _ai_validate_duplicate(self, first_name: str, last_name: str, email: str, 
                               phone: str, existing: Dict) -> float:
        """
        Use AI to validate if a name match is truly the same person
        
        Returns confidence score 0.0-1.0
        """
        if not self.openai_client:
            # Without AI, only return high confidence for exact name match
            if (existing.get('firstName', '').lower() == first_name.lower() and
                existing.get('lastName', '').lower() == last_name.lower()):
                return 0.75
            return 0.5
        
        try:
            prompt = f"""Determine if these two candidate profiles are the same person.

New Candidate:
- Name: {first_name} {last_name}
- Email: {email or 'N/A'}
- Phone: {phone or 'N/A'}

Existing Candidate in Database:
- Name: {existing.get('firstName', '')} {existing.get('lastName', '')}
- Email: {existing.get('email', 'N/A')}
- Phone: {existing.get('phone', 'N/A')}
- Location: {existing.get('address', {}).get('city', 'N/A')}, {existing.get('address', {}).get('state', 'N/A')}

Return only a number between 0.0 and 1.0 representing the probability these are the same person.
Consider: name spelling variations, nicknames, contact info matches.
"""

            # gpt-4.1-mini: cost-optimized for simple deduplication confidence scoring
            response = self.openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "You are a deduplication expert. Return only a decimal number."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=10
            )
            
            confidence = float(response.choices[0].message.content.strip())
            return min(max(confidence, 0.0), 1.0)
            
        except Exception as e:
            self.logger.error(f"AI duplicate validation error: {e}")
            return 0.5
    
    def map_to_bullhorn_fields(self, email_data: Dict, resume_data: Dict, 
                                source: str, work_auth: str = None) -> Dict[str, Any]:
        """
        Map extracted data to Bullhorn candidate field names
        
        Priority: Email data > Resume data for basic fields
        Resume data provides enhanced info (skills, work history)
        """
        # Start with resume data, overlay with email data (email takes priority)
        candidate = {}
        
        # Basic fields - email data takes priority
        first_name = email_data.get('first_name') or resume_data.get('first_name') or ''
        last_name = email_data.get('last_name') or resume_data.get('last_name') or ''
        
        candidate['firstName'] = first_name
        candidate['lastName'] = last_name
        # IMPORTANT: 'name' field is required for Bullhorn list view display
        candidate['name'] = f"{first_name} {last_name}".strip()
        
        candidate['email'] = email_data.get('email') or resume_data.get('email')
        candidate['phone'] = email_data.get('phone') or resume_data.get('phone')
        
        # Location
        candidate['address'] = {}
        if email_data.get('city') or resume_data.get('city'):
            candidate['address']['city'] = email_data.get('city') or resume_data.get('city')
        if email_data.get('state') or resume_data.get('state'):
            candidate['address']['state'] = email_data.get('state') or resume_data.get('state')
        if resume_data.get('country'):
            candidate['address']['countryName'] = resume_data.get('country')
        
        # Source mapping
        bullhorn_source = self.SOURCE_TO_BULLHORN.get(source, 'Other')
        candidate['source'] = bullhorn_source
        
        # Status - new applicants get "Online Applicant" status
        candidate['status'] = 'Online Applicant'
        
        # Work authorization / Visa Type (customText1)
        work_auth = work_auth or email_data.get('work_authorization')
        if work_auth:
            visa_type = self.WORK_AUTH_TO_VISA_TYPE.get(work_auth, work_auth)
            candidate['customText1'] = visa_type
        
        # Enhanced data from resume
        if resume_data.get('current_title'):
            candidate['occupation'] = resume_data['current_title']
        
        if resume_data.get('current_company'):
            candidate['companyName'] = resume_data['current_company']
        
        if resume_data.get('skills'):
            # Join skills for text field
            candidate['skillSet'] = ', '.join(resume_data['skills'][:20])  # Limit to 20 skills
        
        # Years of experience - store in customInt1 only
        # Note: employmentPreference is for employment TYPE (Direct Hire, Contract, etc.) - NOT years of experience
        if resume_data.get('years_experience'):
            try:
                years = int(resume_data['years_experience'])
                candidate['customInt1'] = years  # Years of experience stored here
            except (ValueError, TypeError):
                pass
        
        # Resume pane - use formatted HTML for better display, fallback to raw text
        # The 'description' field maps to the "Resume" pane in Bullhorn UI (supports HTML)
        if resume_data.get('formatted_html'):
            # Use HTML-formatted resume for cleaner display in Bullhorn
            formatted_html = resume_data['formatted_html']
            # Bullhorn description field has a limit, truncate if needed
            max_length = 50000  # Bullhorn typically allows up to 50K characters
            if len(formatted_html) > max_length:
                formatted_html = formatted_html[:max_length] + '<p><em>[Resume truncated due to length...]</em></p>'
            candidate['description'] = formatted_html
        elif resume_data.get('raw_text'):
            # Fallback to raw text if HTML not available
            raw_text = resume_data['raw_text']
            max_length = 50000
            if len(raw_text) > max_length:
                raw_text = raw_text[:max_length] + '\n\n[Resume truncated due to length...]'
            candidate['description'] = raw_text
        elif resume_data.get('summary'):
            candidate['description'] = resume_data['summary']
        
        # LinkedIn URL if available
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
        1. If no AI Resume Summary exists in last 24h → allow creation (return False)
        2. If one exists and current_resume_filename matches a recently processed
           ParsedEmail for this candidate → skip (return True, duplicate)
        3. If one exists but resume filename differs → allow (new resume)
        4. If no filename available → enforce simple 24h rule (skip)
        5. Fail-safe: if the check itself errors → allow creation (return False)
        
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
                return False  # No existing summary — safe to create
            
            # If we can't compare filenames, enforce simple 24h fallback rule
            if not current_resume_filename:
                self.logger.info(
                    f"⚠️ RESUME SUMMARY DEDUP: Candidate {candidate_id} already has "
                    f"{len(existing_notes)} AI Resume Summary note(s) in last 24h. "
                    f"No filename to compare — skipping (24h fallback rule)."
                )
                return True
            
            # Check if the existing summary was for a different resume
            # by looking at ParsedEmail records for this candidate in the same window
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
                # Same resume already processed — duplicate
                self.logger.info(
                    f"⚠️ RESUME SUMMARY DEDUP: Candidate {candidate_id} already has "
                    f"AI Resume Summary for resume '{current_resume_filename}' in last 24h. Skipping."
                )
                return True
            else:
                # Different resume — allow new summary
                self.logger.info(
                    f"✅ RESUME SUMMARY DEDUP: Candidate {candidate_id} has new resume "
                    f"'{current_resume_filename}' (previous: {previous_filenames}). Allowing new summary."
                )
                return False
                
        except Exception as e:
            # Fail-safe: if the duplicate check itself errors, allow creation
            # (better to create a potential extra note than silently drop a legitimate one)
            self.logger.warning(
                f"Resume summary duplicate check failed (proceeding with creation): {e}"
            )
            return False
    
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
            # Extract email metadata
            sender = sendgrid_payload.get('from', '')
            recipient = sendgrid_payload.get('to', '')
            subject = sendgrid_payload.get('subject', '')
            body_text = sendgrid_payload.get('text', '')
            body_html = sendgrid_payload.get('html', body_text)
            message_id = sendgrid_payload.get('headers', '').split('Message-ID:')[-1].split('\n')[0].strip() if 'Message-ID' in sendgrid_payload.get('headers', '') else None
            
            # Prefer HTML body for extraction
            body = body_html if body_html else body_text
            
            self.logger.info(f"Processing inbound email from {sender}: {subject[:50]}...")
            
            if message_id:
                existing = ParsedEmail.query.filter_by(message_id=message_id).first()
                if existing:
                    self.logger.info(f"⏭️ Skipping duplicate email (message_id already processed): {message_id[:50]}")
                    return {'success': True, 'message': 'Duplicate email skipped', 'duplicate': True}
            
            # Create parsed email record
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
            
            # Detect source platform
            source = self.detect_source(sender, subject, body)
            parsed_email.source_platform = source
            
            # Extract Bullhorn job ID
            job_id = self.extract_bullhorn_job_id(subject, body)
            parsed_email.bullhorn_job_id = job_id
            
            # Extract candidate info from email
            email_candidate = self.extract_candidate_from_email(subject, body, source)
            parsed_email.candidate_name = f"{email_candidate.get('first_name', '')} {email_candidate.get('last_name', '')}".strip()
            parsed_email.candidate_email = email_candidate.get('email')
            parsed_email.candidate_phone = email_candidate.get('phone')
            
            # Process resume attachment if present
            resume_data = {}
            resume_text = ''  # Store raw resume text for description field
            attachments = self._extract_attachments(sendgrid_payload)
            
            # Use smart resume selection to pick the best file (not cover letters)
            resume_file = self._select_best_resume(attachments)
            
            if resume_file:
                parsed_email.resume_filename = resume_file['filename']
                
                # Extract and parse resume text (returns both raw_text and formatted_html)
                resume_text, formatted_html = self._extract_resume_text(resume_file)
                if resume_text:
                    resume_data = self.parse_resume_with_ai(resume_text)
                    # Store raw text and formatted HTML for the Resume pane (description field)
                    # The formatted_html contains proper HTML structure (headings, paragraphs, lists)
                    # which displays nicely in Bullhorn's Resume pane
                    resume_data['raw_text'] = resume_text
                    resume_data['formatted_html'] = formatted_html
                    
                    # Enhanced logging for debugging AI extraction
                    self.logger.info(f"📊 AI Resume Extraction Results:")
                    self.logger.info(f"  - Name: {resume_data.get('first_name')} {resume_data.get('last_name')}")
                    self.logger.info(f"  - Current Title: {resume_data.get('current_title')}")
                    self.logger.info(f"  - Current Company: {resume_data.get('current_company')}")
                    self.logger.info(f"  - Years Experience: {resume_data.get('years_experience')}")
                    self.logger.info(f"  - Skills Count: {len(resume_data.get('skills', []))}")
                    if resume_data.get('skills'):
                        self.logger.info(f"  - Skills (first 10): {resume_data.get('skills', [])[:10]}")
                    self.logger.info(f"  - Education Count: {len(resume_data.get('education', []))}")
                    if resume_data.get('education'):
                        for edu in resume_data.get('education', []):
                            self.logger.info(f"    - {edu.get('degree')} from {edu.get('institution')} ({edu.get('year')})")
                    self.logger.info(f"  - Work History Count: {len(resume_data.get('work_history', []))}")
                    self.logger.info(f"  - Raw Resume Text Length: {len(resume_data.get('raw_text', ''))} chars")
                    self.logger.info(f"  - Formatted HTML Length: {len(resume_data.get('formatted_html', ''))} chars")
            
            db.session.commit()
            
            # ============================================================
            # EARLY VALIDATION - Fail fast with descriptive error messages
            # ============================================================
            
            # Combine candidate info from both email body and resume
            candidate_email = email_candidate.get('email') or resume_data.get('email')
            candidate_phone = email_candidate.get('phone') or resume_data.get('phone')
            first_name = email_candidate.get('first_name') or resume_data.get('first_name')
            last_name = email_candidate.get('last_name') or resume_data.get('last_name')
            
            has_name = bool(first_name or last_name)
            has_contact = bool(candidate_email or candidate_phone)
            has_email_data = bool(email_candidate.get('first_name') or email_candidate.get('email'))
            
            # Check if AI had a TIMEOUT error - this is more serious
            timeout_error = resume_data.get('_timeout_error')
            if timeout_error:
                self.logger.warning(f"⚠️ Resume parsing timed out: {timeout_error}")
                # Only fail if we don't have email-extracted candidate info to fall back on
                if not has_email_data:
                    parsed_email.status = 'failed'
                    parsed_email.processed_at = datetime.utcnow()
                    parsed_email.processing_notes = f"Resume parsing timed out and no candidate info in email body"
                    db.session.commit()
                    self._notify_admin_parse_failure(parsed_email, timeout_error)
                    result['success'] = False
                    result['message'] = timeout_error
                    return result
                else:
                    # Log the timeout but continue with email data
                    self.logger.info(f"⚠️ AI timed out but using email-extracted candidate info: {first_name} {last_name}")
            
            # Check if we have ANY usable candidate information
            if not has_name and not has_contact:
                # Check for specific failure reasons to provide helpful messages
                if not attachments:
                    error_msg = "No resume attachment found in email and could not extract candidate info from email body"
                elif not resume_file:
                    error_msg = f"No supported resume file found (received: {[a['filename'] for a in attachments]}) and no candidate info in email body"
                elif not resume_text:
                    error_msg = f"Could not extract text from resume '{resume_file['filename']}' - may be password-protected, scanned image, or corrupted"
                elif not resume_data or len(resume_data) == 0:
                    error_msg = "AI could not extract any information from resume and no candidate info in email body"
                else:
                    error_msg = "Could not extract candidate name or contact information from email or resume"
                
                self.logger.warning(f"⚠️ Early validation failed: {error_msg}")
                parsed_email.status = 'failed'
                parsed_email.processed_at = datetime.utcnow()
                parsed_email.processing_notes = error_msg
                db.session.commit()
                self._notify_admin_parse_failure(parsed_email, error_msg)
                result['success'] = False
                result['message'] = error_msg
                return result
            
            self.logger.info(f"✅ Validation passed: name={first_name} {last_name}, email={candidate_email}, phone={candidate_phone}")
            
            # ============================================================
            # END EARLY VALIDATION
            # ============================================================
            
            # Import bullhorn service helper that loads credentials from database
            from app import get_bullhorn_service
            bullhorn = get_bullhorn_service()
            
            duplicate_id, confidence = self.find_duplicate_candidate(
                candidate_email, candidate_phone, first_name, last_name, bullhorn
            )
            
            parsed_email.is_duplicate_candidate = duplicate_id is not None
            parsed_email.duplicate_confidence = confidence
            
            # Map data to Bullhorn fields
            bullhorn_data = self.map_to_bullhorn_fields(
                email_candidate, resume_data, source,
                email_candidate.get('work_authorization')
            )
            
            # Log key fields being sent to Bullhorn
            self.logger.info(f"📤 Bullhorn candidate data:")
            self.logger.info(f"  - occupation (title): {bullhorn_data.get('occupation')}")
            self.logger.info(f"  - companyName: {bullhorn_data.get('companyName')}")
            self.logger.info(f"  - skillSet: {bullhorn_data.get('skillSet', '')[:100]}...")
            self.logger.info(f"  - employmentPreference: {bullhorn_data.get('employmentPreference')}")
            self.logger.info(f"  - description (Resume pane) length: {len(bullhorn_data.get('description', ''))} chars")
            
            # Create or update candidate in Bullhorn
            if duplicate_id and confidence >= 0.85:
                # Update existing candidate with new info
                candidate_id = bullhorn.update_candidate(duplicate_id, bullhorn_data)
                result['is_duplicate'] = True
                self.logger.info(f"Updated existing candidate {candidate_id}")
            else:
                # Create new candidate
                candidate_id = bullhorn.create_candidate(bullhorn_data)
                self.logger.info(f"Created new candidate {candidate_id}")
            
            parsed_email.bullhorn_candidate_id = candidate_id
            result['candidate_id'] = candidate_id
            
            # Only create work history and education for NEW candidates
            # to avoid duplicate records when updating existing candidates
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
            
            # Add note to candidate record - always create at least a basic application note
            note_status = "not_attempted"
            note_id_created = None
            
            if candidate_id:
                note_created = False
                
                # First try: AI-generated summary note (preferred)
                if resume_data.get('summary'):
                    # ── DEDUP CHECK: Prevent duplicate AI Resume Summary notes ──
                    # Uses the same pattern as vetting dedup (24h window + action filter).
                    # Compares resume filenames to allow new summaries when the resume changes.
                    resume_filename = resume_file['filename'] if resume_file else None
                    if self._check_existing_resume_summary(bullhorn, candidate_id, resume_filename):
                        self.logger.info(f"⏭️ Skipped duplicate AI Resume Summary for candidate {candidate_id}")
                        # NOTE: We set note_created = True even though no new Bullhorn note was
                        # created. This is intentional — it indicates that an existing AI Resume
                        # Summary already exists within the 24h dedup window, so the fallback
                        # "Application Received" note should NOT be created either. Without this
                        # flag, the fallback block below would fire and create an unnecessary note.
                        note_created = True
                        note_status = "ai_summary_dedup_skipped"
                    else:
                        summary = resume_data.get('summary')
                        note_text = f"📋 AI-Generated Resume Summary:\n\n{summary}"
                        if resume_data.get('skills'):
                            skills_preview = ', '.join(resume_data['skills'][:10])
                            note_text += f"\n\n🔧 Key Skills: {skills_preview}"
                        if resume_data.get('years_experience'):
                            note_text += f"\n\n📅 Experience: {resume_data['years_experience']} years"
                        
                        note_id = bullhorn.create_candidate_note(candidate_id, note_text, "AI Resume Summary")
                        if note_id:
                            self.logger.info(f"✅ Created AI summary note {note_id} for candidate {candidate_id}")
                            note_created = True
                            note_status = "ai_summary_created"
                            note_id_created = note_id
                        else:
                            self.logger.warning(f"⚠️ Failed to create AI summary note for candidate {candidate_id}")
                            note_status = "ai_summary_failed"
                
                # Fallback: Create basic application note if AI summary wasn't available or failed
                if not note_created:
                    self.logger.info(f"📝 Creating fallback application note for candidate {candidate_id}")
                    
                    # Build a basic note with whatever info we have
                    note_parts = [f"📨 Job Application Received via {source}"]
                    note_parts.append(f"\n📅 Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
                    
                    if job_id:
                        note_parts.append(f"\n💼 Applied to Job ID: {job_id}")
                    
                    # Add any available info from resume_data
                    if resume_data.get('current_title'):
                        note_parts.append(f"\n👤 Current Title: {resume_data.get('current_title')}")
                    if resume_data.get('current_company'):
                        note_parts.append(f"\n🏢 Current Company: {resume_data.get('current_company')}")
                    if resume_data.get('skills'):
                        skills_preview = ', '.join(resume_data['skills'][:8])
                        note_parts.append(f"\n🔧 Skills: {skills_preview}")
                    if resume_data.get('years_experience'):
                        note_parts.append(f"\n📅 Experience: {resume_data['years_experience']} years")
                    
                    # Add email-extracted info if no resume data
                    if not resume_data.get('current_title') and email_candidate.get('first_name'):
                        note_parts.append(f"\n👤 Candidate: {email_candidate.get('first_name', '')} {email_candidate.get('last_name', '')}")
                    
                    if result.get('is_duplicate'):
                        note_parts.append("\n\n⚠️ Note: Candidate was identified as existing in database (duplicate)")
                    
                    fallback_note = ''.join(note_parts)
                    fallback_note_id = bullhorn.create_candidate_note(candidate_id, fallback_note, "Application Received")
                    if fallback_note_id:
                        self.logger.info(f"✅ Created fallback application note {fallback_note_id} for candidate {candidate_id}")
                        note_status = "fallback_created"
                        note_id_created = fallback_note_id
                    else:
                        self.logger.error(f"❌ Failed to create any note for candidate {candidate_id}")
                        note_status = "all_notes_failed"
            
            # Upload resume if available
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
            
            # Create job submission if we have job ID
            self.logger.info(f"🔗 Job submission check: job_id={job_id}, candidate_id={candidate_id}")
            if job_id and candidate_id:
                self.logger.info(f"📤 Attempting to create job submission: candidate {candidate_id} -> job {job_id}")
                submission_id = bullhorn.create_job_submission(candidate_id, job_id, source)
                if submission_id:
                    parsed_email.bullhorn_submission_id = submission_id
                    result['submission_id'] = submission_id
                    self.logger.info(f"✅ Created job submission {submission_id} for candidate {candidate_id} -> job {job_id}")
                else:
                    self.logger.warning(f"⚠️ Failed to create job submission for candidate {candidate_id} -> job {job_id}")
            elif not job_id:
                self.logger.warning(f"⚠️ No job ID extracted - cannot create job submission")
            elif not candidate_id:
                self.logger.warning(f"⚠️ No candidate ID - cannot create job submission")
            
            # Mark as completed with note creation status
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
            
            # Update parsed email status if record exists
            if result.get('parsed_email_id'):
                try:
                    parsed_email = ParsedEmail.query.get(result['parsed_email_id'])
                    if parsed_email:
                        parsed_email.status = 'failed'
                        parsed_email.processing_notes = str(e)
                        db.session.commit()
                        self._notify_admin_parse_failure(parsed_email, str(e))
                except:
                    pass
        
        return result
    
    def _extract_attachments(self, sendgrid_payload: Dict) -> List[Dict]:
        """
        Extract file attachments from SendGrid payload
        
        Returns list of dicts with 'filename', 'content', 'content_type'
        """
        attachments = []
        
        # SendGrid sends attachments as numbered fields: attachment1, attachment2, etc.
        # Or as 'attachments' JSON field
        
        # Check for 'attachments' field (JSON format)
        if 'attachments' in sendgrid_payload:
            try:
                att_data = json.loads(sendgrid_payload['attachments'])
                for att in att_data:
                    attachments.append({
                        'filename': att.get('filename', 'attachment'),
                        'content': base64.b64decode(att.get('content', '')),
                        'content_type': att.get('type', 'application/octet-stream')
                    })
            except:
                pass
        
        # Check for numbered attachment fields
        for i in range(1, 11):  # Check up to 10 attachments
            att_key = f'attachment{i}'
            if att_key in sendgrid_payload:
                att_info_key = f'attachment-info'
                info = {}
                if att_info_key in sendgrid_payload:
                    try:
                        info = json.loads(sendgrid_payload[att_info_key])
                    except:
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
        
        # Positive indicators (likely resume)
        resume_keywords = ['resume', 'cv', 'curriculum']
        for keyword in resume_keywords:
            if keyword in filename_lower:
                score += 10
        
        # Negative indicators (likely NOT resume)
        non_resume_keywords = ['cover', 'letter', 'reference', 'portfolio', 'logo', 'photo', 'image']
        for keyword in non_resume_keywords:
            if keyword in filename_lower:
                score -= 10
        
        # Extension preference (PDFs are commonly resumes)
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
        
        # Sort by score (highest first), then return the best match
        resume_candidates.sort(key=lambda x: x[1], reverse=True)
        best_resume = resume_candidates[0][0]
        
        self.logger.info(f"📄 Selected resume: {best_resume['filename']} (score: {resume_candidates[0][1]})")
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
            # Save to temp file
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
                    self.logger.info(f"📄 Resume parsed: {len(raw_text)} chars raw, {len(formatted_html)} chars HTML")
                    return raw_text, formatted_html
                return '', ''
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                    
        except Exception as e:
            self.logger.error(f"Error extracting resume text: {e}")
            return '', ''
