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
    BullhornMonitor, GlobalSettings
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
                'gaps_identified': ''
            }
        
        job_title = job.get('title', 'Unknown Position')
        job_description = job.get('publicDescription', '') or job.get('description', '')
        job_location = job.get('address', {}).get('city', '') if isinstance(job.get('address'), dict) else ''
        job_id = job.get('id', 'N/A')
        
        # Clean up job description (remove HTML tags if present)
        import re
        job_description = re.sub(r'<[^>]+>', '', job_description)
        
        # Truncate if too long
        max_resume_len = 8000
        max_desc_len = 4000
        resume_text = resume_text[:max_resume_len] if resume_text else ''
        job_description = job_description[:max_desc_len] if job_description else ''
        
        prompt = f"""Analyze how well this candidate's resume matches the job requirements. 
Provide an objective assessment with a percentage match score (0-100).

JOB DETAILS:
- Job ID: {job_id}
- Title: {job_title}
- Location: {job_location}
- Description: {job_description}

CANDIDATE RESUME:
{resume_text}

Respond in JSON format with these exact fields:
{{
    "match_score": <integer 0-100>,
    "match_summary": "<2-3 sentence summary of overall fit>",
    "skills_match": "<key skills that align with job requirements>",
    "experience_match": "<relevant experience that qualifies candidate>",
    "gaps_identified": "<any requirements the candidate may not meet>"
}}

Be thorough but concise. Focus on factual alignment between resume and job requirements.
Score above 80 only if there's strong alignment in skills, experience, and qualifications."""

        try:
            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert technical recruiter analyzing candidate-job fit. Provide objective, data-driven assessments."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=1000
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Ensure match_score is an integer
            result['match_score'] = int(result.get('match_score', 0))
            
            return result
            
        except Exception as e:
            logging.error(f"AI analysis error for job {job_id}: {str(e)}")
            return {
                'match_score': 0,
                'match_summary': f'Analysis failed: {str(e)}',
                'skills_match': '',
                'experience_match': '',
                'gaps_identified': ''
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
                
                # Analyze the match
                analysis = self.analyze_candidate_job_match(vetting_log.resume_text, job)
                
                # Get recruiter info from job
                recruiter_name = ''
                recruiter_email = ''
                recruiter_id = None
                
                owner = job.get('owner', {})
                if isinstance(owner, dict):
                    recruiter_name = f"{owner.get('firstName', '')} {owner.get('lastName', '')}".strip()
                    recruiter_email = owner.get('email', '')
                    recruiter_id = owner.get('id')
                
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
            
            logging.info(f"✅ Completed analysis for {candidate_name}: {len(qualified_matches)} qualified matches out of {len(all_match_results)} jobs")
            
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
        Send email notifications to recruiters for qualified matches.
        
        Args:
            vetting_log: The vetting log with qualified matches
            
        Returns:
            Number of notifications sent
        """
        if not vetting_log.is_qualified:
            return 0
        
        # Get qualified matches grouped by recruiter
        matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id,
            is_qualified=True,
            notification_sent=False
        ).all()
        
        if not matches:
            return 0
        
        # Group matches by recruiter email
        recruiter_matches = {}
        for match in matches:
            email = match.recruiter_email
            if email:
                if email not in recruiter_matches:
                    recruiter_matches[email] = {
                        'recruiter_name': match.recruiter_name,
                        'matches': []
                    }
                recruiter_matches[email]['matches'].append(match)
        
        notifications_sent = 0
        
        for recruiter_email, data in recruiter_matches.items():
            try:
                success = self._send_recruiter_email(
                    recruiter_email=recruiter_email,
                    recruiter_name=data['recruiter_name'],
                    candidate_name=vetting_log.candidate_name,
                    candidate_id=vetting_log.bullhorn_candidate_id,
                    matches=data['matches']
                )
                
                if success:
                    # Mark matches as notified
                    for match in data['matches']:
                        match.notification_sent = True
                        match.notification_sent_at = datetime.utcnow()
                    
                    notifications_sent += 1
                    
            except Exception as e:
                logging.error(f"Failed to send notification to {recruiter_email}: {str(e)}")
        
        if notifications_sent > 0:
            vetting_log.notifications_sent = True
            vetting_log.notification_count = notifications_sent
            db.session.commit()
        
        logging.info(f"Sent {notifications_sent} recruiter notifications for candidate {vetting_log.candidate_name}")
        return notifications_sent
    
    def _send_recruiter_email(self, recruiter_email: str, recruiter_name: str,
                               candidate_name: str, candidate_id: int,
                               matches: List[CandidateJobMatch]) -> bool:
        """
        Send notification email to a recruiter about a qualified candidate.
        """
        # Build Bullhorn candidate URL
        candidate_url = f"https://app.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=Candidate&id={candidate_id}"
        
        # Build email content
        subject = f"🎯 Qualified Candidate Alert: {candidate_name}"
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h1 style="margin: 0; font-size: 24px;">🎯 Qualified Candidate Match</h1>
            </div>
            
            <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef;">
                <p style="margin: 0 0 15px 0;">Hi {recruiter_name or 'there'},</p>
                
                <p style="margin: 0 0 15px 0;">
                    A new candidate has been analyzed by JobPulse AI and matches 
                    <strong>{len(matches)} position(s)</strong> you're recruiting for.
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
            
            html_content += f"""
                <div style="background: white; padding: 15px; border-radius: 8px; 
                            border-left: 4px solid #28a745; margin: 10px 0;">
                    <h4 style="margin: 0 0 8px 0; color: #28a745;">
                        {match.job_title} (Job ID: {match.bullhorn_job_id}){applied_badge}
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
        
        # Send the email
        try:
            success = self.email_service.send_email(
                to_email=recruiter_email,
                subject=subject,
                html_content=html_content
            )
            return success
        except Exception as e:
            logging.error(f"Email send error: {str(e)}")
            return False
    
    def run_vetting_cycle(self) -> Dict:
        """
        Run a complete vetting cycle with concurrency protection:
        1. Acquire lock (skip if already running)
        2. Detect new applicants
        3. Process each candidate
        4. Create notes for all
        5. Send notifications for qualified
        6. Update last run timestamp
        7. Release lock
        
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
        
        summary = {
            'candidates_detected': 0,
            'candidates_processed': 0,
            'candidates_qualified': 0,
            'notes_created': 0,
            'notifications_sent': 0,
            'errors': []
        }
        
        try:
            # Detect new applicants (uses last run timestamp or 10 minutes for first run)
            candidates = self.detect_new_applicants(since_minutes=10)
            summary['candidates_detected'] = len(candidates)
            
            if not candidates:
                logging.info("No new candidates to process")
                # Still update timestamp to move forward
                self._set_last_run_timestamp(cycle_start)
                return summary
            
            # Limit candidates per cycle to prevent runaway costs (max 5 per cycle)
            if len(candidates) > 5:
                logging.warning(f"Limiting to 5 candidates (found {len(candidates)}) to control API costs")
                candidates = candidates[:5]
            
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
