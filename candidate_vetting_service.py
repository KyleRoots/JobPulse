"""
Candidate Vetting Service - AI-powered candidate-job matching engine

This service monitors new job applicants with "Online Applicant" status,
analyzes their resumes against all open positions in monitored tearsheets,
and notifies recruiters when candidates match at 80%+ threshold.
"""

import logging
import io
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json

from openai import OpenAI

from app import db
from models import (
    CandidateVettingLog, CandidateJobMatch, VettingConfig,
    BullhornMonitor, GlobalSettings, JobVettingRequirements, ParsedEmail
)
from bullhorn_service import BullhornService
from email_service import EmailService


class CandidateVettingService:
    """
    AI-powered candidate vetting system that:
    1. Detects new Online Applicant candidates in Bullhorn
    2. Extracts and analyzes their resumes
    3. Compares against all jobs in monitored tearsheets
    4. Creates notes on all candidates (qualified and not)
    5. Sends email notifications for qualified matches (80%+)
    """
    
    def __init__(self, bullhorn_service: BullhornService = None):
        self.bullhorn = bullhorn_service
        self.email_service = EmailService()
        self.openai_client = None
        self._init_openai()
        
        # Default settings
        self.match_threshold = 80.0  # Minimum match percentage for notifications
        self.check_interval_minutes = 5
        self.model = "gpt-4o"  # Using GPT-4o for accuracy
        
    def _init_openai(self):
        """Initialize OpenAI client"""
        import os
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key:
            self.openai_client = OpenAI(api_key=api_key)
        else:
            logging.warning("OPENAI_API_KEY not found - AI matching will not work")
    
    def _get_bullhorn_service(self) -> BullhornService:
        """Get or create Bullhorn service with current credentials"""
        if self.bullhorn:
            return self.bullhorn
            
        credentials = {}
        for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
            setting = GlobalSettings.query.filter_by(setting_key=key).first()
            if setting and setting.setting_value:
                credentials[key.replace('bullhorn_', '')] = setting.setting_value
        
        if len(credentials) == 4:
            self.bullhorn = BullhornService(
                client_id=credentials['client_id'],
                client_secret=credentials['client_secret'],
                username=credentials['username'],
                password=credentials['password']
            )
            return self.bullhorn
        else:
            logging.error("Bullhorn credentials not fully configured")
            return None
    
    def get_config_value(self, key: str, default: str = None) -> str:
        """Get configuration value from database"""
        config = VettingConfig.query.filter_by(setting_key=key).first()
        return config.setting_value if config else default
    
    def is_enabled(self) -> bool:
        """Check if vetting is enabled"""
        return self.get_config_value('vetting_enabled', 'false').lower() == 'true'
    
    def get_threshold(self) -> float:
        """Get match threshold percentage"""
        try:
            return float(self.get_config_value('match_threshold', '80'))
        except (ValueError, TypeError):
            return 80.0
    
    def _get_job_custom_requirements(self, job_id: int) -> Optional[str]:
        """Get custom requirements for a job if user has specified any"""
        try:
            job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
            if job_req:
                return job_req.get_active_requirements()
            return None
        except Exception as e:
            logging.error(f"Error getting custom requirements for job {job_id}: {str(e)}")
            return None
    
    def extract_job_requirements(self, job_id: int, job_title: str, job_description: str) -> Optional[str]:
        """
        Extract mandatory requirements from a job description using AI.
        Called during monitoring when new jobs are indexed so requirements
        are available for review BEFORE any candidates are vetted.
        
        Args:
            job_id: Bullhorn job ID
            job_title: Job title
            job_description: Full job description text
            
        Returns:
            Extracted requirements string or None if extraction fails
        """
        if not self.openai_client:
            logging.warning("OpenAI client not initialized - cannot extract requirements")
            return None
            
        # Check if requirements already exist
        existing = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
        if existing and existing.ai_interpreted_requirements:
            logging.debug(f"Job {job_id} already has requirements extracted")
            return existing.ai_interpreted_requirements
        
        # Clean job description (remove HTML)
        import re
        clean_description = re.sub(r'<[^>]+>', '', job_description) if job_description else ''
        
        if len(clean_description) < 50:
            logging.warning(f"Job {job_id} has insufficient description for requirements extraction")
            return None
        
        # Truncate if too long
        clean_description = clean_description[:6000]
        
        prompt = f"""Analyze this job posting and extract ONLY the MANDATORY requirements.

JOB TITLE: {job_title}

JOB DESCRIPTION:
{clean_description}

Extract and list the TOP 5-7 MANDATORY requirements from this job. Focus on:
1. Required technical skills (programming languages, tools, technologies)
2. Required years of experience
3. Required certifications or licenses
4. Required education level
5. Required industry-specific knowledge

DO NOT include:
- "Nice to have" or "preferred" qualifications
- Soft skills (communication, teamwork, etc.)
- Generic requirements that apply to any job

Format as a bullet-point list. Be specific and concise."""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",  # Use cheaper model for requirements extraction
                messages=[
                    {"role": "system", "content": "You are a technical recruiter extracting key mandatory requirements from job descriptions. Be concise and specific."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=500
            )
            
            requirements = response.choices[0].message.content.strip()
            
            # Save the extracted requirements
            if requirements:
                self._save_ai_interpreted_requirements(job_id, job_title, requirements)
                logging.info(f"✅ Extracted requirements for job {job_id}: {job_title[:50]}")
                return requirements
            
            return None
            
        except Exception as e:
            logging.error(f"Error extracting requirements for job {job_id}: {str(e)}")
            return None
    
    def extract_requirements_for_jobs(self, jobs: list) -> dict:
        """
        Batch extract requirements for multiple jobs.
        Called during monitoring cycle to pre-populate requirements.
        
        Args:
            jobs: List of job dictionaries with id, title, description keys
            
        Returns:
            Summary dict with success/failure counts
        """
        results = {
            'total': len(jobs),
            'extracted': 0,
            'skipped': 0,
            'failed': 0
        }
        
        for job in jobs:
            job_id = job.get('id')
            job_title = job.get('title', '')
            job_description = job.get('description', '') or job.get('publicDescription', '')
            
            if not job_id:
                results['skipped'] += 1
                continue
                
            # Check if already exists
            existing = JobVettingRequirements.query.filter_by(bullhorn_job_id=int(job_id)).first()
            if existing and existing.ai_interpreted_requirements:
                results['skipped'] += 1
                continue
            
            # Extract requirements
            try:
                extracted = self.extract_job_requirements(int(job_id), job_title, job_description)
                if extracted:
                    results['extracted'] += 1
                else:
                    results['failed'] += 1
            except Exception as e:
                logging.error(f"Error in batch extraction for job {job_id}: {str(e)}")
                results['failed'] += 1
        
        logging.info(f"📋 Job requirements extraction: {results['extracted']} extracted, {results['skipped']} skipped, {results['failed']} failed")
        return results
    
    def _save_ai_interpreted_requirements(self, job_id, job_title: str, requirements: str):
        """Save the AI-interpreted requirements for a job for user review"""
        try:
            # Normalize job_id - handle strings, whitespace, and invalid values
            if job_id is None or str(job_id).strip() in ('', 'N/A', 'None'):
                logging.warning(f"⚠️ Cannot save requirements - invalid job_id: {job_id}")
                return
            
            # Strip whitespace and convert to int
            job_id_str = str(job_id).strip()
            try:
                job_id_int = int(job_id_str)
            except ValueError:
                logging.error(f"⚠️ Cannot convert job_id to integer: '{job_id}' (stripped: '{job_id_str}')")
                return
            
            # Validate requirements content
            if not requirements or not requirements.strip():
                logging.warning(f"⚠️ Empty requirements string for job {job_id_int}, skipping save")
                return
                
            logging.info(f"💾 Saving AI requirements for job {job_id_int}: {job_title[:50] if job_title else 'No title'}")
            
            job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id_int).first()
            if job_req:
                job_req.ai_interpreted_requirements = requirements.strip()
                job_req.last_ai_interpretation = datetime.utcnow()
                if job_title:
                    job_req.job_title = job_title
                logging.info(f"✅ Updated existing requirements for job {job_id_int}")
            else:
                job_req = JobVettingRequirements(
                    bullhorn_job_id=job_id_int,
                    job_title=job_title,
                    ai_interpreted_requirements=requirements.strip(),
                    last_ai_interpretation=datetime.utcnow()
                )
                db.session.add(job_req)
                logging.info(f"✅ Created new requirements record for job {job_id_int}")
            db.session.commit()
            logging.info(f"✅ Successfully saved AI requirements for job {job_id_int}")
        except Exception as e:
            logging.error(f"Error saving AI requirements for job {job_id}: {str(e)}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            db.session.rollback()
    
    def _get_last_run_timestamp(self) -> Optional[datetime]:
        """Get the last successful vetting run timestamp from config"""
        config = VettingConfig.query.filter_by(setting_key='last_run_timestamp').first()
        if config and config.setting_value:
            try:
                return datetime.fromisoformat(config.setting_value)
            except (ValueError, TypeError):
                return None
        return None
    
    def _set_last_run_timestamp(self, timestamp: datetime):
        """Save the last successful vetting run timestamp"""
        config = VettingConfig.query.filter_by(setting_key='last_run_timestamp').first()
        if config:
            config.setting_value = timestamp.isoformat()
        else:
            config = VettingConfig(setting_key='last_run_timestamp', setting_value=timestamp.isoformat())
            db.session.add(config)
        db.session.commit()
    
    def _acquire_vetting_lock(self) -> bool:
        """Try to acquire exclusive lock for vetting cycle. Returns True if acquired."""
        try:
            config = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            if config:
                if config.setting_value == 'true':
                    # Check if lock is stale (older than 30 minutes)
                    lock_time_config = VettingConfig.query.filter_by(setting_key='vetting_lock_time').first()
                    if lock_time_config and lock_time_config.setting_value:
                        try:
                            lock_time = datetime.fromisoformat(lock_time_config.setting_value)
                            if datetime.utcnow() - lock_time > timedelta(minutes=30):
                                logging.warning("Stale vetting lock detected (>30 min), releasing")
                            else:
                                logging.info("Vetting cycle already in progress, skipping")
                                return False
                        except (ValueError, TypeError):
                            pass
                    else:
                        logging.info("Vetting cycle already in progress (no timestamp), skipping")
                        return False
                config.setting_value = 'true'
            else:
                config = VettingConfig(setting_key='vetting_in_progress', setting_value='true')
                db.session.add(config)
            
            # Set lock time
            lock_time_config = VettingConfig.query.filter_by(setting_key='vetting_lock_time').first()
            if lock_time_config:
                lock_time_config.setting_value = datetime.utcnow().isoformat()
            else:
                lock_time_config = VettingConfig(setting_key='vetting_lock_time', setting_value=datetime.utcnow().isoformat())
                db.session.add(lock_time_config)
            
            db.session.commit()
            return True
        except Exception as e:
            logging.error(f"Error acquiring vetting lock: {str(e)}")
            return False
    
    def _release_vetting_lock(self):
        """Release the vetting lock"""
        try:
            config = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            if config:
                config.setting_value = 'false'
                db.session.commit()
        except Exception as e:
            logging.error(f"Error releasing vetting lock: {str(e)}")
    
    def detect_new_applicants(self, since_minutes: int = 5) -> List[Dict]:
        """
        Find new candidates with "Online Applicant" status that haven't been processed yet.
        Uses dateAdded filter in Bullhorn query to only fetch recent candidates.
        
        Args:
            since_minutes: Only look at candidates created/updated in the last N minutes
            
        Returns:
            List of candidate dictionaries from Bullhorn
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return []
        
        if not bullhorn.authenticate():
            logging.error("Failed to authenticate with Bullhorn for candidate detection")
            return []
        
        try:
            # Determine the since timestamp - use last run or fallback to since_minutes
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
                logging.info(f"Using last run timestamp for detection: {since_time}")
            else:
                # First run - only look at very recent candidates (prevent historical processing)
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
                logging.info(f"First run - only detecting candidates from last {since_minutes} minutes")
            
            since_timestamp = int(since_time.timestamp() * 1000)  # Bullhorn uses milliseconds
            
            # Build search query with date filter to prevent historical processing
            url = f"{bullhorn.base_url}search/Candidate"
            params = {
                'query': f'status:"Online Applicant" AND dateAdded:[{since_timestamp} TO *]',
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation',
                'count': 50,  # Limit batch size for performance
                'sort': '-dateAdded',  # Most recent first
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logging.error(f"Failed to search for applicants: {response.status_code}")
                return []
            
            data = response.json()
            candidates = data.get('data', [])
            
            logging.info(f"Bullhorn returned {len(candidates)} candidates since {since_time}")
            
            # Filter to only candidates not already processed
            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue
                    
                # Check if already in our vetting log
                existing = CandidateVettingLog.query.filter_by(
                    bullhorn_candidate_id=candidate_id
                ).first()
                
                if not existing:
                    new_candidates.append(candidate)
                    logging.info(f"New applicant detected: {candidate.get('firstName')} {candidate.get('lastName')} (ID: {candidate_id})")
            
            logging.info(f"Found {len(new_candidates)} new applicants to process out of {len(candidates)} recent online applicants")
            return new_candidates
            
        except Exception as e:
            logging.error(f"Error detecting new applicants: {str(e)}")
            return []
    
    def detect_unvetted_applications(self, limit: int = 25) -> List[Dict]:
        """
        Find candidates from ParsedEmail records that have been successfully processed
        but not yet vetted. This captures ALL inbound applicants (both new and existing
        candidates) since email parsing is the entry point for all applications.
        
        Args:
            limit: Maximum number of candidates to return (configurable batch size)
            
        Returns:
            List of candidate dictionaries ready for vetting
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return []
        
        if not bullhorn.authenticate():
            logging.error("Failed to authenticate with Bullhorn for candidate detection")
            return []
        
        try:
            # Diagnostic: Log ParsedEmail table stats for debugging
            from sqlalchemy import func
            total_emails = db.session.query(func.count(ParsedEmail.id)).scalar() or 0
            completed_emails = db.session.query(func.count(ParsedEmail.id)).filter(
                ParsedEmail.status == 'completed'
            ).scalar() or 0
            with_candidate_id = db.session.query(func.count(ParsedEmail.id)).filter(
                ParsedEmail.status == 'completed',
                ParsedEmail.bullhorn_candidate_id.isnot(None)
            ).scalar() or 0
            already_vetted = db.session.query(func.count(ParsedEmail.id)).filter(
                ParsedEmail.status == 'completed',
                ParsedEmail.bullhorn_candidate_id.isnot(None),
                ParsedEmail.vetted_at.isnot(None)
            ).scalar() or 0
            
            logging.info(f"📊 ParsedEmail stats: total={total_emails}, completed={completed_emails}, "
                        f"with_candidate_id={with_candidate_id}, already_vetted={already_vetted}, "
                        f"pending_vetting={with_candidate_id - already_vetted}")
            
            # Query ParsedEmail for completed applications that haven't been vetted
            unvetted_emails = ParsedEmail.query.filter(
                ParsedEmail.status == 'completed',
                ParsedEmail.vetted_at.is_(None),
                ParsedEmail.bullhorn_candidate_id.isnot(None)
            ).order_by(
                ParsedEmail.processed_at.asc()  # Process oldest first (FIFO)
            ).limit(limit).all()
            
            if not unvetted_emails:
                logging.info("No unvetted applications found in ParsedEmail records")
                return []
            
            logging.info(f"Found {len(unvetted_emails)} unvetted applications from email parsing")
            
            # Build candidate list from ParsedEmail records
            candidates_to_vet = []
            already_vetted_ids = []
            
            for parsed_email in unvetted_emails:
                candidate_id = parsed_email.bullhorn_candidate_id
                
                # Skip if already in vetting log (shouldn't happen but defensive check)
                existing_log = CandidateVettingLog.query.filter_by(
                    bullhorn_candidate_id=candidate_id
                ).first()
                
                if existing_log:
                    # Queue for batch update instead of individual commits
                    already_vetted_ids.append(parsed_email.id)
                    logging.info(f"Candidate {candidate_id} already vetted, marking for skip")
                    continue
                
                # Fetch full candidate data from Bullhorn
                candidate_data = self._fetch_candidate_details(bullhorn, candidate_id)
                
                if candidate_data:
                    # Attach the ParsedEmail ID for tracking
                    candidate_data['_parsed_email_id'] = parsed_email.id
                    candidate_data['_applied_job_id'] = parsed_email.bullhorn_job_id
                    candidate_data['_is_duplicate'] = parsed_email.is_duplicate_candidate
                    candidates_to_vet.append(candidate_data)
                    logging.info(f"Queued for vetting: {candidate_data.get('firstName')} {candidate_data.get('lastName')} (ID: {candidate_id}, Applied to Job: {parsed_email.bullhorn_job_id})")
            
            # Batch update already-vetted records in single transaction
            if already_vetted_ids:
                try:
                    ParsedEmail.query.filter(ParsedEmail.id.in_(already_vetted_ids)).update(
                        {'vetted_at': datetime.utcnow()},
                        synchronize_session=False
                    )
                    db.session.commit()
                    logging.info(f"Marked {len(already_vetted_ids)} already-vetted applications")
                except Exception as e:
                    db.session.rollback()
                    logging.error(f"Error updating already-vetted applications: {str(e)}")
            
            logging.info(f"Prepared {len(candidates_to_vet)} candidates for vetting from email parsing")
            return candidates_to_vet
            
        except Exception as e:
            logging.error(f"Error detecting unvetted applications: {str(e)}")
            db.session.rollback()
            return []
    
    def _fetch_candidate_details(self, bullhorn: BullhornService, candidate_id: int) -> Optional[Dict]:
        """
        Fetch full candidate details from Bullhorn by ID.
        
        Args:
            bullhorn: Authenticated Bullhorn service
            candidate_id: Bullhorn candidate ID
            
        Returns:
            Candidate data dictionary or None
        """
        try:
            url = f"{bullhorn.base_url}entity/Candidate/{candidate_id}"
            params = {
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation',
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('data', {})
            else:
                logging.warning(f"Failed to fetch candidate {candidate_id}: {response.status_code}")
                return None
                
        except Exception as e:
            logging.error(f"Error fetching candidate {candidate_id}: {str(e)}")
            return None
    
    def _mark_application_vetted(self, parsed_email_id: int):
        """Mark a ParsedEmail record as vetted"""
        try:
            parsed_email = ParsedEmail.query.get(parsed_email_id)
            if parsed_email:
                parsed_email.vetted_at = datetime.utcnow()
                db.session.commit()
                logging.debug(f"Marked ParsedEmail {parsed_email_id} as vetted")
        except Exception as e:
            logging.error(f"Error marking application vetted: {str(e)}")
    
    def get_candidate_resume(self, candidate_id: int) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Download the candidate's resume file from Bullhorn.
        
        Args:
            candidate_id: Bullhorn candidate ID
            
        Returns:
            Tuple of (file_content_bytes, filename) or (None, None) if not found
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn or not bullhorn.base_url:
            return None, None
        
        try:
            # First, get the list of files attached to the candidate
            url = f"{bullhorn.base_url}entityFiles/Candidate/{candidate_id}"
            params = {'BhRestToken': bullhorn.rest_token}
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logging.warning(f"Failed to get files for candidate {candidate_id}: {response.status_code}")
                return None, None
            
            data = response.json()
            files = data.get('EntityFiles', [])
            
            if not files:
                logging.info(f"No files found for candidate {candidate_id}")
                return None, None
            
            # Find the resume file (prioritize files with "Resume" type or name)
            resume_file = None
            for file_info in files:
                file_type = file_info.get('type', '').lower()
                file_name = file_info.get('name', '').lower()
                
                if 'resume' in file_type or 'resume' in file_name:
                    resume_file = file_info
                    break
            
            # If no explicit resume, use the first file (often the resume)
            if not resume_file and files:
                resume_file = files[0]
            
            if not resume_file:
                return None, None
            
            # Download the file content
            file_id = resume_file.get('id')
            filename = resume_file.get('name', f'resume_{candidate_id}')
            
            download_url = f"{bullhorn.base_url}file/Candidate/{candidate_id}/{file_id}"
            
            download_response = bullhorn.session.get(download_url, params=params, timeout=60)
            
            if download_response.status_code == 200:
                logging.info(f"Downloaded resume for candidate {candidate_id}: {filename}")
                return download_response.content, filename
            else:
                logging.warning(f"Failed to download file {file_id}: {download_response.status_code}")
                return None, None
                
        except Exception as e:
            logging.error(f"Error getting resume for candidate {candidate_id}: {str(e)}")
            return None, None
    
    def extract_resume_text(self, file_content: bytes, filename: str) -> Optional[str]:
        """
        Extract text content from a resume file (PDF, DOCX, DOC, TXT).
        
        Args:
            file_content: Raw file bytes
            filename: Original filename (for determining file type)
            
        Returns:
            Extracted text or None if extraction fails
        """
        if not file_content:
            return None
        
        filename_lower = filename.lower()
        
        try:
            if filename_lower.endswith('.pdf'):
                return self._extract_text_from_pdf(file_content)
            elif filename_lower.endswith('.docx'):
                return self._extract_text_from_docx(file_content)
            elif filename_lower.endswith('.doc'):
                return self._extract_text_from_doc(file_content)
            elif filename_lower.endswith('.txt'):
                return file_content.decode('utf-8', errors='ignore')
            else:
                # Try to decode as text
                return file_content.decode('utf-8', errors='ignore')
        except Exception as e:
            logging.error(f"Error extracting text from {filename}: {str(e)}")
            return None
    
    def _extract_text_from_pdf(self, file_content: bytes) -> Optional[str]:
        """Extract text from PDF file"""
        try:
            import fitz  # PyMuPDF
            
            doc = fitz.open(stream=file_content, filetype="pdf")
            text_parts = []
            
            for page in doc:
                text_parts.append(page.get_text())
            
            doc.close()
            return "\n".join(text_parts)
        except ImportError:
            logging.warning("PyMuPDF not installed - trying pdfminer")
            try:
                from pdfminer.high_level import extract_text
                return extract_text(io.BytesIO(file_content))
            except ImportError:
                logging.error("No PDF extraction library available")
                return None
        except Exception as e:
            logging.error(f"PDF extraction error: {str(e)}")
            return None
    
    def _extract_text_from_docx(self, file_content: bytes) -> Optional[str]:
        """Extract text from DOCX file"""
        try:
            from docx import Document
            
            doc = Document(io.BytesIO(file_content))
            text_parts = []
            
            for para in doc.paragraphs:
                text_parts.append(para.text)
            
            return "\n".join(text_parts)
        except ImportError:
            logging.error("python-docx not installed for DOCX extraction")
            return None
        except Exception as e:
            logging.error(f"DOCX extraction error: {str(e)}")
            return None
    
    def _extract_text_from_doc(self, file_content: bytes) -> Optional[str]:
        """Extract text from legacy DOC file"""
        try:
            import subprocess
            import tempfile
            import os
            
            # Write to temp file and use antiword or similar
            with tempfile.NamedTemporaryFile(suffix='.doc', delete=False) as tmp:
                tmp.write(file_content)
                tmp_path = tmp.name
            
            try:
                # Try antiword
                result = subprocess.run(['antiword', tmp_path], capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    return result.stdout
            except FileNotFoundError:
                logging.warning("antiword not available for DOC extraction")
            finally:
                os.unlink(tmp_path)
            
            return None
        except Exception as e:
            logging.error(f"DOC extraction error: {str(e)}")
            return None
    
    def get_active_jobs_from_tearsheets(self) -> List[Dict]:
        """
        Get all active jobs from monitored tearsheets.
        
        Returns:
            List of job dictionaries with recruiter info
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return []
        
        # Get all active monitors
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        if not monitors:
            logging.warning("No active tearsheet monitors configured")
            return []
        
        all_jobs = []
        
        for monitor in monitors:
            try:
                jobs = bullhorn.get_tearsheet_jobs(monitor.tearsheet_id)
                for job in jobs:
                    job['tearsheet_id'] = monitor.tearsheet_id
                    job['tearsheet_name'] = monitor.name
                    all_jobs.append(job)
                    
            except Exception as e:
                logging.error(f"Error getting jobs from tearsheet {monitor.name}: {str(e)}")
        
        logging.info(f"Loaded {len(all_jobs)} jobs from {len(monitors)} tearsheets")
        return all_jobs
    
    def analyze_candidate_job_match(self, resume_text: str, job: Dict) -> Dict:
        """
        Use GPT-4o to analyze how well a candidate matches a job.
        
        Args:
            resume_text: Extracted text from candidate's resume
            job: Job dictionary from Bullhorn
            
        Returns:
            Dictionary with match_score, match_summary, skills_match, experience_match, gaps_identified
        """
        if not self.openai_client:
            return {
                'match_score': 0,
                'match_summary': 'AI analysis unavailable',
                'skills_match': '',
                'experience_match': '',
                'gaps_identified': '',
                'key_requirements': ''
            }
        
        job_title = job.get('title', 'Unknown Position')
        # Use internal description field first (contains full details), fall back to publicDescription
        job_description = job.get('description', '') or job.get('publicDescription', '')
        job_location = job.get('address', {}).get('city', '') if isinstance(job.get('address'), dict) else ''
        job_id = job.get('id', 'N/A')
        
        # Check for custom requirements override
        custom_requirements = self._get_job_custom_requirements(job_id)
        
        # Clean up job description (remove HTML tags if present)
        import re
        job_description = re.sub(r'<[^>]+>', '', job_description)
        
        # Truncate if too long
        max_resume_len = 8000
        max_desc_len = 4000
        resume_text = resume_text[:max_resume_len] if resume_text else ''
        job_description = job_description[:max_desc_len] if job_description else ''
        
        # Build the requirements section - use custom if available, otherwise let AI extract
        requirements_instruction = ""
        if custom_requirements:
            requirements_instruction = f"""
IMPORTANT: Use these specific requirements for evaluation (manually specified):
{custom_requirements}

Focus ONLY on these requirements when scoring. Ignore nice-to-haves in the job description."""
        else:
            requirements_instruction = """
IMPORTANT: Identify and focus ONLY on MANDATORY requirements from the job description:
- Required skills (often marked as "required", "must have", "essential")
- Minimum years of experience specified
- Required certifications or licenses
- Required education level

DO NOT penalize candidates for missing "nice-to-have" or "preferred" qualifications.
Be lenient on soft skills - focus primarily on technical/hard skill requirements."""
        
        prompt = f"""Analyze how well this candidate's resume matches the MANDATORY job requirements.
Provide an objective assessment with a percentage match score (0-100).
{requirements_instruction}

JOB DETAILS:
- Job ID: {job_id}
- Title: {job_title}
- Location: {job_location}
- Description: {job_description}

CANDIDATE RESUME:
{resume_text}

CRITICAL INSTRUCTIONS - READ CAREFULLY:
1. ONLY reference skills, technologies, and experience that are EXPLICITLY STATED in the resume text above.
2. DO NOT infer, assume, or hallucinate any skills not directly mentioned in the resume.
3. If a MANDATORY job requirement skill is NOT mentioned in the resume, you MUST list it in gaps_identified.
4. For skills_match and experience_match, ONLY quote or paraphrase content that actually exists in the resume.
5. If the job requires specific technologies (e.g., FPGA, Verilog, AWS, Python) and the resume does NOT mention them, the candidate does NOT qualify.
6. A candidate whose background is completely different from the job (e.g., DBA applying to FPGA role) should score BELOW 30.

Respond in JSON format with these exact fields:
{{
    "match_score": <integer 0-100>,
    "match_summary": "<2-3 sentence summary of overall fit - be honest about mismatches>",
    "skills_match": "<ONLY list skills from the resume that directly match job requirements - quote from resume>",
    "experience_match": "<ONLY list experience from the resume that is relevant to the job - be specific>",
    "gaps_identified": "<List ALL mandatory requirements NOT found in the resume - this is critical>",
    "key_requirements": "<bullet list of the top 3-5 MANDATORY requirements from the job description>"
}}

SCORING GUIDELINES:
- 85-100: Candidate meets nearly ALL mandatory requirements with explicit evidence in resume
- 70-84: Candidate meets MOST mandatory requirements but has 1-2 minor gaps
- 50-69: Candidate meets SOME requirements but is missing key qualifications
- 30-49: Candidate has tangential experience but significant gaps
- 0-29: Candidate's background does not align with the role (wrong field/specialty)

BE HONEST. If the resume does not show the required skills, the candidate should NOT score high."""

        try:
            system_message = """You are a strict, evidence-based technical recruiter analyzing candidate-job fit.

CRITICAL RULES:
1. You MUST only cite skills and experience that are EXPLICITLY written in the candidate's resume.
2. You MUST NOT infer or hallucinate skills that are not directly stated.
3. If a job requires FPGA and the resume shows SQL/database experience, they DO NOT match.
4. If a job requires Python and the resume only mentions Java, that is a GAP.
5. Be honest - a mismatched candidate should score LOW even if they have impressive but irrelevant skills.
6. Your assessment will be used for recruiter decisions - accuracy is critical."""

            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Lower temperature for more deterministic/accurate responses
                max_tokens=1000
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Ensure match_score is an integer
            result['match_score'] = int(result.get('match_score', 0))
            
            # Save AI-interpreted requirements for future reference/editing
            key_requirements = result.get('key_requirements', '')
            logging.info(f"📋 AI response for job {job_id}: has_requirements={bool(key_requirements)}, has_custom={bool(custom_requirements)}")
            
            if not key_requirements:
                logging.warning(f"⚠️ AI did not return key_requirements for job {job_id} - requirements will not be saved")
            elif custom_requirements:
                logging.info(f"📝 Job {job_id} has custom requirements - skipping AI interpretation save (expected behavior)")
            else:
                # Save the AI-interpreted requirements
                self._save_ai_interpreted_requirements(job_id, job_title, key_requirements)
            
            return result
            
        except Exception as e:
            logging.error(f"AI analysis error for job {job_id}: {str(e)}")
            return {
                'match_score': 0,
                'match_summary': f'Analysis failed: {str(e)}',
                'skills_match': '',
                'experience_match': '',
                'gaps_identified': '',
                'key_requirements': ''
            }
    
    def get_candidate_job_submission(self, candidate_id: int) -> Optional[Dict]:
        """
        Get the job submission to find which job the candidate applied to.
        
        Args:
            candidate_id: Bullhorn candidate ID
            
        Returns:
            Job submission info or None
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return None
        
        try:
            # Search for job submissions by this candidate
            url = f"{bullhorn.base_url}search/JobSubmission"
            params = {
                'query': f'candidate.id:{candidate_id}',
                'fields': 'id,jobOrder(id,title),status,dateAdded',
                'count': 1,
                'sort': '-dateAdded',
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                submissions = data.get('data', [])
                if submissions:
                    return submissions[0]
            
            return None
            
        except Exception as e:
            logging.error(f"Error getting job submission for candidate {candidate_id}: {str(e)}")
            return None
    
    def process_candidate(self, candidate: Dict) -> Optional[CandidateVettingLog]:
        """
        Process a single candidate through the full vetting pipeline.
        
        Args:
            candidate: Candidate dictionary from Bullhorn
            
        Returns:
            CandidateVettingLog record or None if processing failed
        """
        candidate_id = candidate.get('id')
        candidate_name = f"{candidate.get('firstName', '')} {candidate.get('lastName', '')}".strip()
        candidate_email = candidate.get('email', '')
        
        logging.info(f"🔍 Processing candidate: {candidate_name} (ID: {candidate_id})")
        
        # Create or get vetting log entry
        vetting_log = CandidateVettingLog.query.filter_by(
            bullhorn_candidate_id=candidate_id
        ).first()
        
        if not vetting_log:
            vetting_log = CandidateVettingLog(
                bullhorn_candidate_id=candidate_id,
                candidate_name=candidate_name,
                candidate_email=candidate_email,
                status='processing'
            )
            db.session.add(vetting_log)
            db.session.commit()
        
        try:
            # Get which job they applied to
            submission = self.get_candidate_job_submission(candidate_id)
            if submission:
                job_order = submission.get('jobOrder', {})
                vetting_log.applied_job_id = job_order.get('id')
                vetting_log.applied_job_title = job_order.get('title')
            
            # Get and extract resume
            file_content, filename = self.get_candidate_resume(candidate_id)
            if file_content and filename:
                resume_text = self.extract_resume_text(file_content, filename)
                if resume_text:
                    vetting_log.resume_text = resume_text[:50000]  # Limit storage size
                    logging.info(f"Extracted {len(resume_text)} characters from resume")
                else:
                    logging.warning(f"Could not extract text from resume: {filename}")
            else:
                logging.warning(f"No resume file found for candidate {candidate_id}")
            
            # Get all active jobs from tearsheets
            jobs = self.get_active_jobs_from_tearsheets()
            
            if not jobs:
                vetting_log.status = 'completed'
                vetting_log.error_message = 'No active jobs found in tearsheets'
                db.session.commit()
                return vetting_log
            
            if not vetting_log.resume_text:
                vetting_log.status = 'completed'
                vetting_log.error_message = 'No resume text available for analysis'
                db.session.commit()
                return vetting_log
            
            # Analyze against each job
            threshold = self.get_threshold()
            qualified_matches = []
            all_match_results = []
            
            for job in jobs:
                job_id = job.get('id')
                
                # Skip if we've already analyzed this job for this candidate
                existing_match = CandidateJobMatch.query.filter_by(
                    vetting_log_id=vetting_log.id,
                    bullhorn_job_id=job_id
                ).first()
                
                if existing_match:
                    continue
                
                # Analyze the match - verify resume text exists and has content
                if not vetting_log.resume_text or len(vetting_log.resume_text.strip()) < 50:
                    logging.error(f"❌ CRITICAL: Resume text missing or too short for candidate {candidate_id}")
                    logging.error(f"   Resume text length: {len(vetting_log.resume_text) if vetting_log.resume_text else 0}")
                    continue
                
                logging.info(f"📄 Analyzing match - Resume: {len(vetting_log.resume_text)} chars, First 200: {vetting_log.resume_text[:200]}")
                analysis = self.analyze_candidate_job_match(vetting_log.resume_text, job)
                
                # Get recruiter info from job's assignedUsers (assignments field in Bullhorn)
                recruiter_name = ''
                recruiter_email = ''
                recruiter_id = None
                
                assigned_users = job.get('assignedUsers', {})
                # Handle both dict with 'data' key and direct list formats
                if isinstance(assigned_users, dict):
                    assigned_users_list = assigned_users.get('data', [])
                elif isinstance(assigned_users, list):
                    assigned_users_list = assigned_users
                else:
                    assigned_users_list = []
                
                # Use first assigned user as primary recruiter
                if assigned_users_list and len(assigned_users_list) > 0:
                    first_user = assigned_users_list[0]
                    if isinstance(first_user, dict):
                        recruiter_name = f"{first_user.get('firstName', '')} {first_user.get('lastName', '')}".strip()
                        recruiter_email = first_user.get('email', '')
                        recruiter_id = first_user.get('id')
                        logging.info(f"  📧 Job {job_id} assigned to: {recruiter_name} ({recruiter_email})")
                else:
                    logging.warning(f"  ⚠️ Job {job_id} has no assigned users")
                
                # Determine if this is the job they applied to
                is_applied_job = vetting_log.applied_job_id == job_id if vetting_log.applied_job_id else False
                
                # Create match record
                match_record = CandidateJobMatch(
                    vetting_log_id=vetting_log.id,
                    bullhorn_job_id=job_id,
                    job_title=job.get('title', ''),
                    job_location=job.get('address', {}).get('city', '') if isinstance(job.get('address'), dict) else '',
                    tearsheet_id=job.get('tearsheet_id'),
                    tearsheet_name=job.get('tearsheet_name', ''),
                    recruiter_name=recruiter_name,
                    recruiter_email=recruiter_email,
                    recruiter_bullhorn_id=recruiter_id,
                    match_score=analysis.get('match_score', 0),
                    is_qualified=analysis.get('match_score', 0) >= threshold,
                    is_applied_job=is_applied_job,
                    match_summary=analysis.get('match_summary', ''),
                    skills_match=analysis.get('skills_match', ''),
                    experience_match=analysis.get('experience_match', ''),
                    gaps_identified=analysis.get('gaps_identified', '')
                )
                
                db.session.add(match_record)
                all_match_results.append(match_record)
                
                if match_record.is_qualified:
                    qualified_matches.append(match_record)
                    logging.info(f"  ✅ Match: {job.get('title')} - {analysis.get('match_score')}%")
                else:
                    logging.info(f"  ❌ No match: {job.get('title')} - {analysis.get('match_score')}%")
            
            # Update vetting log summary
            vetting_log.status = 'completed'
            vetting_log.analyzed_at = datetime.utcnow()
            vetting_log.is_qualified = len(qualified_matches) > 0
            vetting_log.total_jobs_matched = len(qualified_matches)
            
            if all_match_results:
                vetting_log.highest_match_score = max(m.match_score for m in all_match_results)
            
            db.session.commit()
            
            logging.info(f"✅ Completed analysis for {candidate_name} (ID: {candidate_id}): {len(qualified_matches)} qualified matches out of {len(all_match_results)} jobs")
            
            return vetting_log
            
        except Exception as e:
            logging.error(f"Error processing candidate {candidate_id}: {str(e)}")
            vetting_log.status = 'failed'
            vetting_log.error_message = str(e)
            vetting_log.retry_count += 1
            db.session.commit()
            return vetting_log
    
    def create_candidate_note(self, vetting_log: CandidateVettingLog) -> bool:
        """
        Create a note on the candidate record summarizing the vetting results.
        
        Args:
            vetting_log: The vetting log with analysis results
            
        Returns:
            True if note was created successfully
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return False
        
        # Get all match results for this candidate
        matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id
        ).order_by(CandidateJobMatch.match_score.desc()).all()
        
        if not matches:
            return False
        
        # Build note content
        threshold = self.get_threshold()
        qualified_matches = [m for m in matches if m.is_qualified]
        
        if vetting_log.is_qualified:
            # Qualified candidate note
            note_lines = [
                f"🎯 AI VETTING SUMMARY - QUALIFIED CANDIDATE",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Threshold: {threshold}%",
                f"Qualified Matches: {len(qualified_matches)} of {len(matches)} jobs",
                f"Highest Match Score: {vetting_log.highest_match_score:.0f}%",
                f"",
                f"QUALIFIED POSITIONS:",
            ]
            
            for match in qualified_matches:
                note_lines.append(f"")
                note_lines.append(f"• Job ID: {match.bullhorn_job_id} - {match.job_title}")
                note_lines.append(f"  Match Score: {match.match_score:.0f}%")
                if match.is_applied_job:
                    note_lines.append(f"  ⭐ APPLIED TO THIS POSITION")
                note_lines.append(f"  Summary: {match.match_summary}")
                note_lines.append(f"  Skills: {match.skills_match}")
        else:
            # Not qualified note
            note_lines = [
                f"📋 AI VETTING SUMMARY - NOT RECOMMENDED",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Threshold: {threshold}%",
                f"Highest Match Score: {vetting_log.highest_match_score:.0f}%",
                f"Jobs Analyzed: {len(matches)}",
                f"",
                f"This candidate did not meet the {threshold}% match threshold for any current open positions.",
                f"",
                f"TOP ANALYSIS RESULTS:",
            ]
            
            # Show top 3 matches even if not qualified
            for match in matches[:3]:
                note_lines.append(f"")
                note_lines.append(f"• Job ID: {match.bullhorn_job_id} - {match.job_title}")
                note_lines.append(f"  Match Score: {match.match_score:.0f}%")
                if match.is_applied_job:
                    note_lines.append(f"  ⭐ APPLIED TO THIS POSITION")
                if match.gaps_identified:
                    note_lines.append(f"  Gaps: {match.gaps_identified}")
        
        note_text = "\n".join(note_lines)
        
        # Create the note
        action = "AI Vetting - Qualified" if vetting_log.is_qualified else "AI Vetting - Not Recommended"
        note_id = bullhorn.create_candidate_note(
            vetting_log.bullhorn_candidate_id,
            note_text,
            action=action
        )
        
        if note_id:
            vetting_log.note_created = True
            vetting_log.bullhorn_note_id = note_id
            db.session.commit()
            logging.info(f"Created vetting note for candidate {vetting_log.bullhorn_candidate_id}")
            return True
        else:
            logging.error(f"Failed to create vetting note for candidate {vetting_log.bullhorn_candidate_id}")
            return False
    
    def send_recruiter_notifications(self, vetting_log: CandidateVettingLog) -> int:
        """
        Send ONE email notification with all recruiters CC'd.
        
        TRANSPARENCY MODEL: When a candidate matches multiple positions with different
        recruiters, ALL recruiters are CC'd on the SAME email thread. The primary
        recipient is the recruiter of the job the candidate applied to. This ensures
        complete visibility and enables direct collaboration on the same thread.
        
        Args:
            vetting_log: The vetting log with qualified matches
            
        Returns:
            Number of notifications sent (1 for success, 0 for failure/no matches)
        """
        logging.info(f"📧 Notification check for {vetting_log.candidate_name} (ID: {vetting_log.bullhorn_candidate_id})")
        
        if not vetting_log.is_qualified:
            logging.info(f"  ⏭️ Skipping - not qualified (is_qualified={vetting_log.is_qualified})")
            return 0
        
        # Get ALL qualified matches for this candidate
        matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id,
            is_qualified=True,
            notification_sent=False
        ).all()
        
        if not matches:
            logging.info(f"  ⏭️ Skipping - no unsent qualified matches (all already notified)")
            return 0
        
        logging.info(f"  📨 Found {len(matches)} unsent qualified matches")
        
        # Determine primary recruiter (from applied job) and CC list
        primary_recruiter_email = None
        primary_recruiter_name = None
        cc_recruiter_emails = []
        
        # First pass: find the applied job recruiter (primary recipient)
        for match in matches:
            if match.is_applied_job and match.recruiter_email:
                primary_recruiter_email = match.recruiter_email
                primary_recruiter_name = match.recruiter_name
                break
        
        # Second pass: collect all unique recruiter emails
        # If no applied job recruiter found, first recruiter becomes primary
        seen_emails = set()
        for match in matches:
            if match.recruiter_email and match.recruiter_email not in seen_emails:
                seen_emails.add(match.recruiter_email)
                
                if not primary_recruiter_email:
                    # No applied job match - first recruiter becomes primary
                    primary_recruiter_email = match.recruiter_email
                    primary_recruiter_name = match.recruiter_name
                elif match.recruiter_email != primary_recruiter_email:
                    # Different from primary - add to CC list
                    cc_recruiter_emails.append(match.recruiter_email)
        
        # Check email notification kill switch setting
        from models import VettingConfig
        send_to_recruiters = False
        admin_email = ''
        
        send_setting = VettingConfig.query.filter_by(setting_key='send_recruiter_emails').first()
        if send_setting:
            send_to_recruiters = send_setting.setting_value.lower() == 'true'
        
        admin_setting = VettingConfig.query.filter_by(setting_key='admin_notification_email').first()
        if admin_setting and admin_setting.setting_value:
            admin_email = admin_setting.setting_value
        
        # If kill switch is OFF, send only to admin email
        if not send_to_recruiters:
            if not admin_email:
                logging.warning(f"❌ Recruiter emails disabled but no admin email configured - cannot send notification for {vetting_log.candidate_name}")
                return 0
            
            logging.info(f"  🔒 Recruiter emails DISABLED - sending to admin only: {admin_email}")
            primary_recruiter_email = admin_email
            primary_recruiter_name = 'Admin'
            cc_recruiter_emails = []  # No CC when in testing mode
        elif not primary_recruiter_email:
            # Kill switch is ON but no recruiter emails found - try to fall back to admin
            if admin_email:
                logging.warning(f"⚠️ No recruiter emails found for candidate {vetting_log.candidate_name} - falling back to admin email: {admin_email}")
                primary_recruiter_email = admin_email
                primary_recruiter_name = 'Admin'
                cc_recruiter_emails = []
            else:
                logging.warning(f"❌ No recruiter emails found and no admin email configured - cannot send notification for {vetting_log.candidate_name}")
                return 0
        
        # Send ONE email with primary as To: and others as CC:
        try:
            success = self._send_recruiter_email(
                recruiter_email=primary_recruiter_email,
                recruiter_name=primary_recruiter_name or '',
                candidate_name=vetting_log.candidate_name,
                candidate_id=vetting_log.bullhorn_candidate_id,
                matches=matches,
                cc_emails=cc_recruiter_emails  # All other recruiters CC'd
            )
            
            if success:
                # Mark ALL matches as notified
                for match in matches:
                    match.notification_sent = True
                    match.notification_sent_at = datetime.utcnow()
                
                vetting_log.notifications_sent = True
                vetting_log.notification_count = 1  # One email sent to all
                db.session.commit()
                
                cc_info = f" (CC: {', '.join(cc_recruiter_emails)})" if cc_recruiter_emails else ""
                logging.info(f"Sent notification to {primary_recruiter_email}{cc_info} for {vetting_log.candidate_name} (Candidate ID: {vetting_log.bullhorn_candidate_id}, {len(matches)} positions)")
                return 1
            else:
                logging.error(f"Failed to send notification for {vetting_log.candidate_name} (Candidate ID: {vetting_log.bullhorn_candidate_id})")
                return 0
                
        except Exception as e:
            logging.error(f"Failed to send notification: {str(e)}")
            return 0
    
    def _send_recruiter_email(self, recruiter_email: str, recruiter_name: str,
                               candidate_name: str, candidate_id: int,
                               matches: List[CandidateJobMatch],
                               cc_emails: list = None) -> bool:
        """
        Send notification email to a recruiter about a qualified candidate.
        
        TRANSPARENCY MODEL: ONE email is sent with the primary recruiter as To:
        and all other recruiters CC'd on the same thread. Each job card shows
        which recruiter owns it for complete visibility.
        """
        # Build Bullhorn candidate URL (using cls45 subdomain for Bullhorn One)
        candidate_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=Candidate&id={candidate_id}"
        
        # Build transparency header if there are CC'd recruiters
        transparency_note = ""
        if cc_emails and len(cc_emails) > 0:
            transparency_note = f"""
                <div style="background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; padding: 12px; margin-bottom: 15px;">
                    <p style="margin: 0; color: #1565c0; font-size: 13px;">
                        <strong>📢 Team Thread:</strong> This candidate matches multiple positions.
                        CC'd on this email: <em>{', '.join(cc_emails)}</em>
                    </p>
                </div>
            """
        
        # Build email content
        subject = f"🎯 Qualified Candidate Alert: {candidate_name}"
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h1 style="margin: 0; font-size: 24px;">🎯 Qualified Candidate Match</h1>
            </div>
            
            <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef;">
                <p style="margin: 0 0 15px 0;">Hi {recruiter_name or 'there'},</p>
                
                {transparency_note}
                
                <p style="margin: 0 0 15px 0;">
                    A new candidate has been analyzed by JobPulse AI and matches 
                    <strong>{len(matches)} position(s)</strong>.
                </p>
                
                <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; margin: 20px 0;">
                    <h2 style="margin: 0 0 10px 0; color: #495057; font-size: 18px;">
                        👤 {candidate_name}
                    </h2>
                    <a href="{candidate_url}" 
                       style="display: inline-block; background: #667eea; color: white; 
                              padding: 10px 20px; border-radius: 5px; text-decoration: none;
                              margin-top: 10px;">
                        View Candidate Profile →
                    </a>
                </div>
                
                <h3 style="color: #495057; margin: 20px 0 10px 0;">Matched Positions:</h3>
        """
        
        for match in matches:
            applied_badge = '<span style="background: #ffc107; color: #000; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">APPLIED</span>' if match.is_applied_job else ''
            job_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=JobOrder&id={match.bullhorn_job_id}"
            
            # Show recruiter ownership for each job
            recruiter_tag = ""
            if match.recruiter_name:
                is_your_job = match.recruiter_email == recruiter_email
                if is_your_job:
                    recruiter_tag = f'<span style="background: #28a745; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">YOUR JOB</span>'
                else:
                    recruiter_tag = f'<span style="background: #6c757d; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">{match.recruiter_name}\'s Job</span>'
            
            html_content += f"""
                <div style="background: white; padding: 15px; border-radius: 8px; 
                            border-left: 4px solid #28a745; margin: 10px 0;">
                    <h4 style="margin: 0 0 8px 0; color: #28a745;">
                        <a href="{job_url}" style="color: #28a745; text-decoration: none;">{match.job_title} (Job ID: {match.bullhorn_job_id})</a>{applied_badge}{recruiter_tag}
                    </h4>
                    <div style="color: #6c757d; margin-bottom: 8px;">
                        <strong>Match Score:</strong> {match.match_score:.0f}%
                    </div>
                    <p style="margin: 0; color: #495057;">{match.match_summary}</p>
                    
                    {f'<p style="margin: 10px 0 0 0; color: #495057;"><strong>Key Skills:</strong> {match.skills_match}</p>' if match.skills_match else ''}
                </div>
            """
        
        html_content += f"""
                <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid #dee2e6;">
                    <p style="color: #6c757d; font-size: 14px; margin: 0;">
                        <strong>Recommended Action:</strong> Review the candidate's profile and 
                        reach out if they're a good fit for your open position(s).
                    </p>
                </div>
            </div>
            
            <div style="background: #343a40; color: #adb5bd; padding: 15px; 
                        border-radius: 0 0 8px 8px; font-size: 12px; text-align: center;">
                Powered by JobPulse™ AI Vetting • Myticas Consulting
            </div>
        </div>
        """
        
        # Send the email with CC recipients and BCC admin for transparency
        try:
            # Always BCC admin for monitoring/troubleshooting
            admin_bcc_email = 'kroots@myticas.com'
            
            success = self.email_service.send_html_email(
                to_email=recruiter_email,
                subject=subject,
                html_content=html_content,
                notification_type='vetting_recruiter_notification',
                cc_emails=cc_emails,  # CC all other recruiters on same thread
                bcc_emails=[admin_bcc_email]  # BCC admin for transparency
            )
            return success
        except Exception as e:
            logging.error(f"Email send error: {str(e)}")
            return False
    
    def _get_batch_size(self) -> int:
        """Get configured batch size from database, default 25"""
        try:
            config = VettingConfig.query.filter_by(setting_key='batch_size').first()
            if config and config.setting_value:
                batch = int(config.setting_value)
                return max(1, min(batch, 100))  # Clamp to 1-100
        except (ValueError, TypeError):
            pass
        return 25  # Default batch size
    
    def run_vetting_cycle(self) -> Dict:
        """
        Run a complete vetting cycle with concurrency protection:
        1. Acquire lock (skip if already running)
        2. Detect unvetted applications from ParsedEmail (captures ALL inbound applicants)
        3. Process each candidate
        4. Create notes for all
        5. Send notifications for qualified
        6. Mark applications as vetted
        7. Update last run timestamp
        8. Release lock
        
        Returns:
            Summary dictionary with counts
        """
        if not self.is_enabled():
            logging.info("Candidate vetting is disabled")
            return {'status': 'disabled'}
        
        # Acquire lock to prevent overlapping runs
        if not self._acquire_vetting_lock():
            logging.info("Skipping vetting cycle - another cycle is in progress")
            return {'status': 'skipped', 'reason': 'cycle_in_progress'}
        
        logging.info("🚀 Starting candidate vetting cycle")
        cycle_start = datetime.utcnow()
        
        # Get configurable batch size
        batch_size = self._get_batch_size()
        logging.info(f"Using batch size: {batch_size}")
        
        summary = {
            'candidates_detected': 0,
            'candidates_processed': 0,
            'candidates_qualified': 0,
            'notes_created': 0,
            'notifications_sent': 0,
            'detection_method': 'parsed_email',
            'batch_size': batch_size,
            'errors': []
        }
        
        try:
            # Primary detection: Use ParsedEmail-based detection for 100% coverage
            # This captures ALL inbound applicants (both new and existing candidates)
            candidates = self.detect_unvetted_applications(limit=batch_size)
            
            # Fallback to legacy detection if no ParsedEmail records found
            # (for candidates entering through other channels)
            if not candidates:
                logging.info("No ParsedEmail records to vet, falling back to legacy detection")
                candidates = self.detect_new_applicants(since_minutes=10)
                if candidates and len(candidates) > batch_size:
                    candidates = candidates[:batch_size]
                summary['detection_method'] = 'bullhorn_search'
            
            summary['candidates_detected'] = len(candidates)
            
            if not candidates:
                logging.info("No new candidates to process")
                # Still update timestamp to move forward
                self._set_last_run_timestamp(cycle_start)
                return summary
            
            # Process each candidate
            for candidate in candidates:
                try:
                    vetting_log = self.process_candidate(candidate)
                    
                    if vetting_log and vetting_log.status == 'completed':
                        summary['candidates_processed'] += 1
                        
                        if vetting_log.is_qualified:
                            summary['candidates_qualified'] += 1
                        
                        # Create note
                        if self.create_candidate_note(vetting_log):
                            summary['notes_created'] += 1
                        
                        # Send notifications for qualified candidates
                        if vetting_log.is_qualified:
                            notif_count = self.send_recruiter_notifications(vetting_log)
                            summary['notifications_sent'] += notif_count
                    
                    # Mark the ParsedEmail record as vetted (if applicable)
                    parsed_email_id = candidate.get('_parsed_email_id')
                    if parsed_email_id:
                        self._mark_application_vetted(parsed_email_id)
                            
                except Exception as e:
                    error_msg = f"Error processing candidate {candidate.get('id')}: {str(e)}"
                    logging.error(error_msg)
                    summary['errors'].append(error_msg)
            
            # Update last run timestamp
            self._set_last_run_timestamp(cycle_start)
            
            logging.info(f"✅ Vetting cycle complete: {summary}")
            return summary
            
        except Exception as e:
            error_msg = f"Vetting cycle error: {str(e)}"
            logging.error(error_msg)
            summary['errors'].append(error_msg)
            return summary
        finally:
            # Always release the lock
            self._release_vetting_lock()
