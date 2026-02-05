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
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from app import db
from models import (
    CandidateVettingLog, CandidateJobMatch, VettingConfig,
    BullhornMonitor, GlobalSettings, JobVettingRequirements, ParsedEmail
)
from bullhorn_service import BullhornService
from email_service import EmailService


def map_work_type(onsite_value) -> str:
    """
    Map Bullhorn onSite value to work type string.
    Handles both numeric (1, 2, 3) and string ('Remote', 'On-Site', 'Hybrid') values.
    """
    # Handle list format
    if isinstance(onsite_value, list):
        onsite_value = onsite_value[0] if onsite_value else 1
    
    # Handle numeric values
    if isinstance(onsite_value, (int, float)):
        work_type_map = {1: 'On-site', 2: 'Hybrid', 3: 'Remote'}
        return work_type_map.get(int(onsite_value), 'On-site')
    
    # Handle string values
    if onsite_value:
        onsite_str = str(onsite_value).lower().strip()
        if 'remote' in onsite_str or onsite_str == 'offsite':
            return 'Remote'
        elif 'hybrid' in onsite_str:
            return 'Hybrid'
        elif 'on-site' in onsite_str or 'onsite' in onsite_str or onsite_str == 'on site':
            return 'On-site'
    
    # Default to On-site
    return 'On-site'


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
        """Get global match threshold percentage"""
        try:
            return float(self.get_config_value('match_threshold', '80'))
        except (ValueError, TypeError):
            return 80.0
    
    def get_job_threshold(self, job_id: int) -> float:
        """Get match threshold for a specific job (returns job-specific if set, otherwise global default)"""
        try:
            job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
            if job_req and job_req.vetting_threshold is not None:
                return float(job_req.vetting_threshold)
            return self.get_threshold()  # Fall back to global default
        except Exception as e:
            logging.warning(f"Error getting job threshold for {job_id}: {e}")
            return self.get_threshold()
    
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
    
    def extract_job_requirements(self, job_id: int, job_title: str, job_description: str,
                                  job_location: str = None, job_work_type: str = None) -> Optional[str]:
        """
        Extract mandatory requirements from a job description using AI.
        Called during monitoring when new jobs are indexed so requirements
        are available for review BEFORE any candidates are vetted.
        
        Args:
            job_id: Bullhorn job ID
            job_title: Job title
            job_description: Full job description text
            job_location: Optional location string (city, state, country)
            job_work_type: Optional work type (On-site, Hybrid, Remote)
            
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
            
            # Save the extracted requirements with location data
            if requirements:
                self._save_ai_interpreted_requirements(job_id, job_title, requirements, job_location, job_work_type)
                logging.info(f"✅ Extracted requirements for job {job_id}: {job_title[:50]}")
                return requirements
            
            return None
            
        except Exception as e:
            logging.error(f"Error extracting requirements for job {job_id}: {str(e)}")
            return None
    
    def sync_requirements_with_active_jobs(self) -> dict:
        """
        Sync AI requirements with active tearsheet jobs.
        Removes requirements for jobs no longer in active tearsheets.
        
        SAFETY: Will NOT delete if active jobs cannot be fetched (prevents data loss on API failure)
        
        Returns:
            Summary dict with cleanup counts and status
        """
        results = {
            'active_jobs': 0,
            'requirements_before': 0,
            'removed': 0,
            'success': False,
            'error': None
        }
        
        try:
            # Get all requirements first
            all_requirements = JobVettingRequirements.query.all()
            results['requirements_before'] = len(all_requirements)
            
            # Get active job IDs from tearsheets
            active_jobs = self.get_active_jobs_from_tearsheets()
            active_job_ids = set(int(job.get('id')) for job in active_jobs if job.get('id'))
            results['active_jobs'] = len(active_job_ids)
            
            # SAFETY CHECK: If no active jobs were fetched but we have requirements,
            # this likely means an API failure - do NOT delete anything
            if len(active_job_ids) == 0 and results['requirements_before'] > 0:
                results['error'] = 'Could not fetch active jobs from tearsheets (API issue?) - sync aborted to prevent data loss'
                logging.warning(f"⚠️ Sync aborted: {results['error']}")
                return results
            
            # Find and remove orphaned requirements
            for req in all_requirements:
                if req.bullhorn_job_id not in active_job_ids:
                    db.session.delete(req)
                    results['removed'] += 1
            
            if results['removed'] > 0:
                db.session.commit()
                logging.info(f"🧹 Synced AI requirements: removed {results['removed']} orphaned entries (not in active tearsheets)")
            else:
                logging.info(f"✅ AI requirements in sync with {results['active_jobs']} active tearsheet jobs")
            
            results['success'] = True
                
        except Exception as e:
            db.session.rollback()
            results['error'] = str(e)
            logging.error(f"Error syncing AI requirements: {str(e)}")
            
        return results
    
    def check_and_refresh_changed_jobs(self, jobs: list = None) -> dict:
        """
        Check for jobs that have been modified in Bullhorn since last AI interpretation.
        Triggers re-extraction for changed jobs while preserving custom overrides.
        
        Args:
            jobs: Optional list of job dicts from tearsheets. If None, fetches from tearsheets.
            
        Returns:
            Summary dict with refresh counts
        """
        results = {
            'jobs_checked': 0,
            'jobs_refreshed': 0,
            'jobs_skipped': 0,
            'errors': []
        }
        
        try:
            # Get jobs if not provided
            if jobs is None:
                jobs = self.get_active_jobs_from_tearsheets()
            
            results['jobs_checked'] = len(jobs)
            logging.info(f"🔄 Checking {len(jobs)} jobs for modifications...")
            
            for job in jobs:
                job_id = job.get('id')
                if not job_id:
                    continue
                    
                try:
                    # Get existing requirements record
                    existing = JobVettingRequirements.query.filter_by(bullhorn_job_id=int(job_id)).first()
                    
                    if not existing or not existing.last_ai_interpretation:
                        # No existing interpretation - will be extracted when needed
                        continue
                    
                    # Get job's dateLastModified from Bullhorn
                    date_last_modified = job.get('dateLastModified')
                    if not date_last_modified:
                        continue
                    
                    # Convert Bullhorn timestamp (milliseconds) to datetime
                    if isinstance(date_last_modified, (int, float)):
                        job_modified_at = datetime.utcfromtimestamp(date_last_modified / 1000)
                    else:
                        # Try parsing as ISO string
                        try:
                            job_modified_at = datetime.fromisoformat(str(date_last_modified).replace('Z', '+00:00'))
                        except:
                            continue
                    
                    # Compare with our last interpretation timestamp
                    if job_modified_at > existing.last_ai_interpretation:
                        # Job was modified - refresh the interpretation
                        job_title = job.get('title', '')
                        job_description = job.get('description', '') or job.get('publicDescription', '')
                        
                        # Extract location data
                        job_address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
                        job_city = job_address.get('city', '')
                        job_state = job_address.get('state', '')
                        job_country = job_address.get('countryName', '') or job_address.get('country', '')
                        job_location = ', '.join(filter(None, [job_city, job_state, job_country]))
                        
                        job_work_type = map_work_type(job.get('onSite', 1))
                        
                        logging.info(f"📝 Job {job_id} modified (Bullhorn: {job_modified_at}, Last AI: {existing.last_ai_interpretation}) - refreshing...")
                        
                        # Update title and location (always)
                        existing.job_title = job_title
                        existing.job_location = job_location
                        existing.job_work_type = job_work_type
                        
                        # Only re-extract AI interpretation if no custom override
                        if not existing.custom_requirements:
                            # Re-extract requirements
                            extracted = self.extract_job_requirements(
                                int(job_id), job_title, job_description,
                                job_location, job_work_type
                            )
                            if extracted:
                                logging.info(f"  ✅ Refreshed AI interpretation for job {job_id}")
                            else:
                                logging.warning(f"  ⚠️ Could not refresh AI interpretation for job {job_id}")
                        else:
                            # Has custom override - just update metadata, not AI interpretation
                            existing.updated_at = datetime.utcnow()
                            db.session.commit()
                            logging.info(f"  ℹ️ Job {job_id} has custom requirements - updated metadata only")
                        
                        results['jobs_refreshed'] += 1
                    else:
                        results['jobs_skipped'] += 1
                        
                except Exception as e:
                    # Rollback to recover from failed transaction state
                    db.session.rollback()
                    logging.error(f"Error checking job {job_id} for changes: {str(e)}")
                    results['errors'].append(f"Job {job_id}: {str(e)}")
            
            if results['jobs_refreshed'] > 0:
                logging.info(f"🔄 Job change detection complete: {results['jobs_refreshed']} refreshed, {results['jobs_skipped']} unchanged")
            
        except Exception as e:
            logging.error(f"Error in job change detection: {str(e)}")
            results['errors'].append(str(e))
            
        return results
    
    def get_active_job_ids(self) -> set:
        """Get set of active job IDs from tearsheets (for filtering)"""
        try:
            active_jobs = self.get_active_jobs_from_tearsheets()
            return set(int(job.get('id')) for job in active_jobs if job.get('id'))
        except Exception as e:
            logging.error(f"Error getting active job IDs: {str(e)}")
            return set()
    
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
            
            # Use pre-processed location/work_type if available, otherwise extract from raw data
            if 'location' in job:
                job_location = job.get('location', '')
            else:
                job_address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
                job_city = job_address.get('city', '')
                job_state = job_address.get('state', '')
                job_country = job_address.get('countryName', '') or job_address.get('country', '')
                job_location = ', '.join(filter(None, [job_city, job_state, job_country]))
            
            if 'work_type' in job:
                job_work_type = job.get('work_type', 'On-site')
            else:
                job_work_type = map_work_type(job.get('onSite', 1))
            
            if not job_id:
                results['skipped'] += 1
                continue
                
            # Check if already exists
            existing = JobVettingRequirements.query.filter_by(bullhorn_job_id=int(job_id)).first()
            if existing and existing.ai_interpreted_requirements:
                results['skipped'] += 1
                continue
            
            # Extract requirements with location data
            try:
                extracted = self.extract_job_requirements(int(job_id), job_title, job_description, job_location, job_work_type)
                if extracted:
                    results['extracted'] += 1
                else:
                    results['failed'] += 1
            except Exception as e:
                logging.error(f"Error in batch extraction for job {job_id}: {str(e)}")
                results['failed'] += 1
        
        logging.info(f"📋 Job requirements extraction: {results['extracted']} extracted, {results['skipped']} skipped, {results['failed']} failed")
        return results
    
    def _save_ai_interpreted_requirements(self, job_id, job_title: str, requirements: str, 
                                          job_location: str = None, job_work_type: str = None):
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
            
            # Handle case where AI returns a list instead of string
            if isinstance(requirements, list):
                requirements = '\n'.join(str(r) for r in requirements)
            
            # Validate requirements content
            if not requirements or not str(requirements).strip():
                logging.warning(f"⚠️ Empty requirements string for job {job_id_int}, skipping save")
                return
            
            requirements = str(requirements).strip()
                
            logging.info(f"💾 Saving AI requirements for job {job_id_int}: {job_title[:50] if job_title else 'No title'}")
            
            job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id_int).first()
            if job_req:
                job_req.ai_interpreted_requirements = requirements.strip()
                job_req.last_ai_interpretation = datetime.utcnow()
                if job_title:
                    job_req.job_title = job_title
                if job_location:
                    job_req.job_location = job_location
                if job_work_type:
                    job_req.job_work_type = job_work_type
                logging.info(f"✅ Updated existing requirements for job {job_id_int}")
            else:
                job_req = JobVettingRequirements(
                    bullhorn_job_id=job_id_int,
                    job_title=job_title,
                    job_location=job_location,
                    job_work_type=job_work_type,
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
                    # Check if lock is stale (older than 5 minutes - auto-release quickly to avoid missed candidates)
                    lock_time_config = VettingConfig.query.filter_by(setting_key='vetting_lock_time').first()
                    if lock_time_config and lock_time_config.setting_value:
                        try:
                            lock_time = datetime.fromisoformat(lock_time_config.setting_value)
                            lock_age_minutes = (datetime.utcnow() - lock_time).total_seconds() / 60
                            if lock_age_minutes > 5:
                                # Stale lock detected - auto-release and continue
                                logging.warning(f"⚠️ Stale vetting lock detected ({lock_age_minutes:.1f} min old), auto-releasing")
                                # Fall through to acquire the lock
                            else:
                                logging.info("Vetting cycle already in progress, skipping")
                                return False
                        except (ValueError, TypeError) as e:
                            # Invalid timestamp - treat as stale and acquire
                            logging.warning(f"⚠️ Invalid lock timestamp, auto-releasing: {e}")
                    else:
                        # No lock timestamp means it's likely stale from a crash
                        logging.warning("⚠️ Vetting lock exists without timestamp, auto-releasing")
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
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation,description,address(address1,city,state,countryName)',
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
                    
                # Check if RECENTLY vetted (within last hour) to prevent duplicate processing
                # But allow re-vetting for candidates who apply again days/weeks later
                from datetime import timedelta
                recent_cutoff = datetime.utcnow() - timedelta(hours=1)
                
                recent_vetting = CandidateVettingLog.query.filter(
                    CandidateVettingLog.bullhorn_candidate_id == candidate_id,
                    CandidateVettingLog.created_at >= recent_cutoff
                ).first()
                
                if not recent_vetting:
                    new_candidates.append(candidate)
                    logging.info(f"New applicant detected: {candidate.get('firstName')} {candidate.get('lastName')} (ID: {candidate_id})")
            
            logging.info(f"Found {len(new_candidates)} new applicants to process out of {len(candidates)} recent online applicants")
            return new_candidates
            
        except Exception as e:
            logging.error(f"Error detecting new applicants: {str(e)}")
            return []
    
    def detect_pandologic_candidates(self, since_minutes: int = 5) -> List[Dict]:
        """
        Find candidates from Pandologic API that haven't been vetted recently.
        Pandologic feeds candidates directly into Bullhorn with owner='Pandologic API'.
        
        Args:
            since_minutes: Only look at candidates created in the last N minutes (fallback)
            
        Returns:
            List of candidate dictionaries from Bullhorn
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return []
        
        if not bullhorn.authenticate():
            logging.error("Failed to authenticate with Bullhorn for Pandologic detection")
            return []
        
        try:
            # Use same timestamp logic as detect_new_applicants
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
            else:
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
            
            since_timestamp = int(since_time.timestamp() * 1000)
            
            # Query for candidates with Pandologic API ownership
            url = f"{bullhorn.base_url}search/Candidate"
            params = {
                'query': f'owner.name:"Pandologic API" AND dateAdded:[{since_timestamp} TO *]',
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation,description,address(address1,city,state,countryName),owner(name)',
                'count': 50,
                'sort': '-dateAdded',
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logging.error(f"Failed to search for Pandologic candidates: {response.status_code}")
                return []
            
            data = response.json()
            candidates = data.get('data', [])
            
            logging.info(f"🔍 Pandologic: Found {len(candidates)} candidates since {since_time}")
            
            # Filter to only candidates not vetted within last 2 hours
            # (longer window than Online Applicants since Pandologic is a different source)
            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue
                
                # 2-hour window for Pandologic candidates
                recent_cutoff = datetime.utcnow() - timedelta(hours=2)
                
                recent_vetting = CandidateVettingLog.query.filter(
                    CandidateVettingLog.bullhorn_candidate_id == candidate_id,
                    CandidateVettingLog.created_at >= recent_cutoff
                ).first()
                
                if not recent_vetting:
                    new_candidates.append(candidate)
                    logging.info(f"🔵 Pandologic candidate detected: {candidate.get('firstName')} {candidate.get('lastName')} (ID: {candidate_id})")
            
            logging.info(f"🔍 Pandologic: {len(new_candidates)} candidates to vet out of {len(candidates)} total")
            return new_candidates
            
        except Exception as e:
            logging.error(f"Error detecting Pandologic candidates: {str(e)}")
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
            
            # DEBUG: Show most recent 5 ParsedEmail records for troubleshooting
            recent_emails = ParsedEmail.query.order_by(ParsedEmail.received_at.desc()).limit(5).all()
            for pe in recent_emails:
                logging.info(f"  📧 Recent ParsedEmail id={pe.id}: candidate='{pe.candidate_name}', "
                            f"status={pe.status}, bh_id={pe.bullhorn_candidate_id}, "
                            f"vetted_at={'SET' if pe.vetted_at else 'NULL'}, received={pe.received_at}")
            
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
                
                # Check if there's a RECENT vetting log for this candidate (within last hour)
                # This prevents duplicate processing of the same application cycle
                # But allows re-vetting for new applications (days/weeks later)
                from datetime import timedelta
                recent_cutoff = datetime.utcnow() - timedelta(hours=1)
                
                existing_log = CandidateVettingLog.query.filter(
                    CandidateVettingLog.bullhorn_candidate_id == candidate_id,
                    CandidateVettingLog.created_at >= recent_cutoff
                ).first()
                
                if existing_log:
                    # Only skip if recently completed or failed (within the hour)
                    if existing_log.status in ('completed', 'failed'):
                        already_vetted_ids.append(parsed_email.id)
                        logging.info(f"Candidate {candidate_id} recently vetted (status={existing_log.status}), marking for skip")
                        continue
                    
                    # Reset stuck 'processing' candidates (older than 10 minutes)
                    if existing_log.status == 'processing':
                        processing_age = (datetime.utcnow() - existing_log.created_at).total_seconds()
                        if processing_age > 600:  # 10 minutes
                            logging.warning(f"Resetting stuck candidate {candidate_id} (processing for {processing_age:.0f}s)")
                            existing_log.status = 'pending'
                            existing_log.error_message = f"Reset from stuck processing state after {processing_age:.0f}s"
                            db.session.commit()
                        else:
                            # Still processing recently, skip
                            logging.info(f"Candidate {candidate_id} still processing (started {processing_age:.0f}s ago), skipping")
                            continue
                    
                    # Pending status - delete old log and reprocess
                    if existing_log.status == 'pending':
                        logging.info(f"Candidate {candidate_id} has pending log, will reprocess")
                        db.session.delete(existing_log)
                        db.session.commit()
                
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
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation,description',
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
                content = download_response.content
                content_type = download_response.headers.get('Content-Type', 'unknown')
                content_length = len(content) if content else 0
                first_bytes = content[:50] if content else b''
                logging.info(f"Downloaded resume for candidate {candidate_id}: {filename}")
                logging.info(f"  Content-Type: {content_type}, Size: {content_length} bytes, First bytes: {first_bytes[:30]}")
                return content, filename
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
            
            # Debug: Check content size and first bytes
            content_size = len(file_content) if file_content else 0
            first_bytes = file_content[:50] if file_content and len(file_content) >= 50 else file_content
            logging.info(f"PDF extraction: size={content_size} bytes, starts with: {first_bytes[:20] if first_bytes else 'empty'}")
            
            # Check if content starts with %PDF (valid PDF header)
            if not file_content or not file_content.startswith(b'%PDF'):
                logging.error(f"Invalid PDF content - doesn't start with %PDF header. First 100 bytes: {file_content[:100] if file_content else 'empty'}")
                return None
            
            doc = fitz.open(stream=file_content, filetype="pdf")
            text_parts = []
            
            for page in doc:
                text_parts.append(page.get_text())
            
            doc.close()
            extracted_text = "\n".join(text_parts)
            logging.info(f"PDF extraction successful: {len(extracted_text)} chars extracted")
            return extracted_text
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
            # Additional debug for the specific error
            if file_content:
                logging.error(f"PDF content size: {len(file_content)} bytes, first 50 bytes: {file_content[:50]}")
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
            List of job dictionaries with recruiter info (including emails)
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
        all_user_ids = set()
        
        for monitor in monitors:
            try:
                jobs = bullhorn.get_tearsheet_jobs(monitor.tearsheet_id)
                for job in jobs:
                    job['tearsheet_id'] = monitor.tearsheet_id
                    job['tearsheet_name'] = monitor.name
                    all_jobs.append(job)
                    
                    # Collect user IDs from assignedUsers for email lookup
                    assigned_users = job.get('assignedUsers', {})
                    if isinstance(assigned_users, dict):
                        users_list = assigned_users.get('data', [])
                    elif isinstance(assigned_users, list):
                        users_list = assigned_users
                    else:
                        users_list = []
                    
                    for user in users_list:
                        if isinstance(user, dict) and user.get('id'):
                            all_user_ids.add(user['id'])
                    
            except Exception as e:
                logging.error(f"Error getting jobs from tearsheet {monitor.name}: {str(e)}")
        
        # Fetch emails for all unique users (Bullhorn API doesn't return email in nested syntax)
        user_email_map = {}
        if all_user_ids:
            user_email_map = bullhorn.get_user_emails(list(all_user_ids))
        
        # Enrich jobs with user emails
        for job in all_jobs:
            assigned_users = job.get('assignedUsers', {})
            if isinstance(assigned_users, dict):
                users_list = assigned_users.get('data', [])
            elif isinstance(assigned_users, list):
                users_list = assigned_users
            else:
                users_list = []
            
            # Add email to each user from our lookup
            for user in users_list:
                if isinstance(user, dict) and user.get('id'):
                    user_id = user['id']
                    if user_id in user_email_map:
                        user['email'] = user_email_map[user_id].get('email', '')
        
        logging.info(f"Loaded {len(all_jobs)} jobs from {len(monitors)} tearsheets with {len(user_email_map)} user emails")
        return all_jobs
    
    def analyze_candidate_job_match(self, resume_text: str, job: Dict, candidate_location: Optional[Dict] = None, prefetched_requirements: Optional[str] = None) -> Dict:
        """
        Use GPT-4o to analyze how well a candidate matches a job.
        
        Args:
            resume_text: Extracted text from candidate's resume
            job: Job dictionary from Bullhorn
            candidate_location: Optional dict with candidate's address info (city, state, countryName)
            
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
        
        # Country normalization mapping for consistent comparison
        def normalize_country(country_value: str) -> str:
            """Normalize country names/codes to consistent format for comparison."""
            if not country_value:
                return ''
            country_upper = country_value.strip().upper()
            country_map = {
                # United States variations
                'US': 'United States', 'USA': 'United States', 'U.S.': 'United States',
                'U.S.A.': 'United States', 'UNITED STATES': 'United States',
                'UNITED STATES OF AMERICA': 'United States',
                # Canada variations
                'CA': 'Canada', 'CAN': 'Canada', 'CANADA': 'Canada',
                'CDN': 'Canada', 'CANADIAN': 'Canada',
                # United Kingdom variations
                'UK': 'United Kingdom', 'GB': 'United Kingdom', 'GBR': 'United Kingdom',
                'UNITED KINGDOM': 'United Kingdom', 'GREAT BRITAIN': 'United Kingdom',
                'ENGLAND': 'United Kingdom',
                # India variations
                'IN': 'India', 'IND': 'India', 'INDIA': 'India',
                # Australia variations
                'AU': 'Australia', 'AUS': 'Australia', 'AUSTRALIA': 'Australia',
                # Germany variations
                'DE': 'Germany', 'DEU': 'Germany', 'GERMANY': 'Germany',
                # Mexico variations
                'MX': 'Mexico', 'MEX': 'Mexico', 'MEXICO': 'Mexico',
                # Brazil variations
                'BR': 'Brazil', 'BRA': 'Brazil', 'BRAZIL': 'Brazil',
                # Philippines variations
                'PH': 'Philippines', 'PHL': 'Philippines', 'PHILIPPINES': 'Philippines',
            }
            return country_map.get(country_upper, country_value.strip())
        
        def smart_correct_country(city: str, state: str, declared_country: str) -> str:
            """
            Smart correction for country based on city/state when there's a mismatch.
            This compensates for human data entry errors where candidates or jobs
            have the wrong country but correct city/state.
            
            Returns the corrected country name, or the original if no correction needed.
            """
            if not state and not city:
                return declared_country
            
            state_upper = state.strip().upper() if state else ''
            city_upper = city.strip().upper() if city else ''
            
            # Canadian provinces/territories
            canadian_provinces = {
                'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT',
                'ALBERTA', 'BRITISH COLUMBIA', 'MANITOBA', 'NEW BRUNSWICK', 
                'NEWFOUNDLAND AND LABRADOR', 'NEWFOUNDLAND', 'NOVA SCOTIA',
                'NORTHWEST TERRITORIES', 'NUNAVUT', 'ONTARIO', 'PRINCE EDWARD ISLAND',
                'QUEBEC', 'SASKATCHEWAN', 'YUKON'
            }
            
            # Major Canadian cities (for cases where state might be missing or wrong)
            canadian_cities = {
                'TORONTO', 'MONTREAL', 'VANCOUVER', 'CALGARY', 'EDMONTON', 'OTTAWA',
                'WINNIPEG', 'QUEBEC CITY', 'HAMILTON', 'KITCHENER', 'LONDON', 'VICTORIA',
                'HALIFAX', 'OSHAWA', 'WINDSOR', 'SASKATOON', 'REGINA', 'ST. CATHARINES',
                'KELOWNA', 'BARRIE', 'SHERBROOKE', 'GUELPH', 'KANATA', 'RICHMOND',
                'BURNABY', 'SURREY', 'MARKHAM', 'MISSISSAUGA', 'BRAMPTON', 'SCARBOROUGH',
                'WATERLOO', 'KINGSTON', 'THUNDER BAY', 'SAINT JOHN', 'MONCTON', 'FREDERICTON'
            }
            
            # UK regions/countries
            uk_regions = {
                'ENGLAND', 'SCOTLAND', 'WALES', 'NORTHERN IRELAND',
                'GREATER LONDON', 'WEST MIDLANDS', 'GREATER MANCHESTER'
            }
            
            # Major UK cities
            uk_cities = {
                'LONDON', 'MANCHESTER', 'BIRMINGHAM', 'LEEDS', 'GLASGOW', 'LIVERPOOL',
                'NEWCASTLE', 'SHEFFIELD', 'BRISTOL', 'EDINBURGH', 'CARDIFF', 'BELFAST',
                'NOTTINGHAM', 'LEICESTER', 'COVENTRY', 'BRADFORD', 'READING'
            }
            
            # Australian states
            australian_states = {
                'NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'ACT', 'NT',
                'NEW SOUTH WALES', 'VICTORIA', 'QUEENSLAND', 'WESTERN AUSTRALIA',
                'SOUTH AUSTRALIA', 'TASMANIA', 'AUSTRALIAN CAPITAL TERRITORY',
                'NORTHERN TERRITORY'
            }
            
            # Mexican states (common abbreviations and names)
            mexican_states = {
                'AGU', 'BCN', 'BCS', 'CAM', 'CHH', 'CHP', 'COA', 'COL', 'DIF', 'DUR',
                'GRO', 'GUA', 'HID', 'JAL', 'MEX', 'MIC', 'MOR', 'NAY', 'NLE', 'OAX',
                'PUE', 'QUE', 'ROO', 'SIN', 'SLP', 'SON', 'TAB', 'TAM', 'TLA', 'VER',
                'YUC', 'ZAC', 'CDMX', 'CIUDAD DE MEXICO', 'JALISCO', 'NUEVO LEON',
                'QUINTANA ROO', 'BAJA CALIFORNIA'
            }
            
            # Major Mexican cities
            mexican_cities = {
                'MEXICO CITY', 'GUADALAJARA', 'MONTERREY', 'PUEBLA', 'TIJUANA',
                'CANCUN', 'LEON', 'JUAREZ', 'MERIDA', 'CHIHUAHUA', 'AGUASCALIENTES',
                'MORELIA', 'QUERETARO', 'TOLUCA', 'HERMOSILLO'
            }
            
            # Check for Canada
            if state_upper in canadian_provinces or city_upper in canadian_cities:
                # Special case: London exists in both Canada (Ontario) and UK
                if city_upper == 'LONDON' and state_upper in {'ENGLAND', 'GREATER LONDON', ''}:
                    return 'United Kingdom'
                # If it's a Canadian province or known Canadian city, correct to Canada
                if declared_country != 'Canada':
                    return 'Canada'
            
            # Check for UK (but not if state indicates Canada - e.g., London, ON)
            if state_upper in uk_regions or (city_upper in uk_cities and state_upper not in canadian_provinces):
                if declared_country not in ('United Kingdom', 'UK', 'GB'):
                    return 'United Kingdom'
            
            # Check for Australia
            if state_upper in australian_states:
                if declared_country != 'Australia':
                    return 'Australia'
            
            # Check for Mexico
            if state_upper in mexican_states or city_upper in mexican_cities:
                if declared_country != 'Mexico':
                    return 'Mexico'
            
            return declared_country
        
        # Extract full job location details
        job_address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
        job_city = job_address.get('city', '')
        job_state = job_address.get('state', '')
        job_country_raw = job_address.get('countryName', '') or job_address.get('country', '')
        job_country_normalized = normalize_country(job_country_raw)
        job_country = smart_correct_country(job_city, job_state, job_country_normalized)  # Auto-fix country mismatches
        job_location_full = ', '.join(filter(None, [job_city, job_state, job_country]))
        
        # Get work type using helper that handles both numeric and string values
        work_type = map_work_type(job.get('onSite', 1))
        
        # Extract candidate location with normalized country
        candidate_city = ''
        candidate_state = ''
        candidate_country = ''
        if candidate_location and isinstance(candidate_location, dict):
            candidate_city = candidate_location.get('city', '')
            candidate_state = candidate_location.get('state', '')
            candidate_country_raw = candidate_location.get('countryName', '') or candidate_location.get('country', '')
            candidate_country_normalized = normalize_country(candidate_country_raw)
            candidate_country = smart_correct_country(candidate_city, candidate_state, candidate_country_normalized)  # Auto-fix country mismatches
        candidate_location_full = ', '.join(filter(None, [candidate_city, candidate_state, candidate_country]))
        
        job_id = job.get('id', 'N/A')
        
        # Check for custom requirements override
        # Use pre-fetched requirements if provided (for parallel execution outside Flask context)
        custom_requirements = prefetched_requirements if prefetched_requirements is not None else self._get_job_custom_requirements(job_id)
        
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
        
        # Build location matching instructions based on work type
        location_instruction = ""
        if job_location_full:
            if work_type == 'Remote':
                location_instruction = f"""
LOCATION REQUIREMENT (Remote Position):
- Job Location: {job_location_full} (Work Type: {work_type})
- Candidate Location: {candidate_location_full if candidate_location_full else 'Unknown'}
- For REMOTE positions: Candidate MUST be in the same COUNTRY as the job location for tax/legal compliance.
- City and state do NOT need to match for remote roles - only the country matters.
- If candidate is in a different country than the job, add "Location mismatch: different country" to gaps_identified and reduce score by 15-20 points.

CRITICAL STATE/PROVINCE RECOGNITION:
- ANY U.S. STATE (Pennsylvania, California, Texas, New York, Florida, etc.) IS PART OF THE UNITED STATES.
- If a remote job is in the United States and candidate is in ANY U.S. state, they ARE in the same country - NO location mismatch.
- Similarly, Canadian provinces (Ontario, British Columbia, etc.) are part of Canada.
- ONLY flag "Location mismatch: different country" if the candidate is literally in a DIFFERENT country (e.g., candidate in India for a US-based job, or candidate in UK for a Canada-based job).
- DO NOT flag location mismatch just because candidate is in a different state/city within the same country.

IMPORTANT LOCATION INFERENCE: If candidate location is not explicitly stated in the resume header/contact section:
1. Check the candidate's MOST RECENT work experience for the job location (city, state/province, country)
2. Use that work location as the candidate's presumed current location
3. This is a valid proxy - people typically work near where they live
4. Only mark location as "unknown" if NO location can be inferred from work history either"""
            else:  # On-site or Hybrid
                location_instruction = f"""
LOCATION REQUIREMENT ({work_type} Position):
- Job Location: {job_location_full} (Work Type: {work_type})
- Candidate Location: {candidate_location_full if candidate_location_full else 'Unknown'}
- For ON-SITE/HYBRID positions: Candidate should be in or near the job's city/metro area, or willing to relocate.

CRITICAL: If candidate is ALREADY in the same city or metro area as the job, they AUTOMATICALLY qualify for on-site/hybrid work.
- Do NOT flag "location mismatch" or "not willing to work on-site" if candidate lives locally.
- Local candidates CAN work on-site by default - no explicit statement needed.
- Only flag location issues if candidate is in a completely different region (different state/province) or country.
- If candidate is non-local AND doesn't mention relocation willingness, add "Location mismatch: candidate not in {job_city or job_state or 'job area'}" to gaps_identified.

IMPORTANT LOCATION INFERENCE: If candidate location is not explicitly stated in the resume header/contact section:
1. Check the candidate's MOST RECENT work experience for the job location (city, state/province, country)
2. Use that work location as the candidate's presumed current location
3. This is a valid proxy - people typically work near where they live
4. Only mark location as "unknown" if NO location can be inferred from work history either"""
        
        prompt = f"""Analyze how well this candidate's resume matches the MANDATORY job requirements.
Provide an objective assessment with a percentage match score (0-100).
{requirements_instruction}
{location_instruction}

JOB DETAILS:
- Job ID: {job_id}
- Title: {job_title}
- Location: {job_location_full} (Work Type: {work_type})
- Description: {job_description}

CANDIDATE INFORMATION:
- Known Location: {candidate_location_full if candidate_location_full else 'Not specified in system - infer from resume if possible'}

CANDIDATE RESUME:
{resume_text}

CRITICAL INSTRUCTIONS - READ CAREFULLY:
1. ONLY reference skills, technologies, and experience that are EXPLICITLY STATED in the resume text above.
2. DO NOT infer, assume, or hallucinate any skills not directly mentioned in the resume.
3. If a MANDATORY job requirement skill is NOT mentioned in the resume, you MUST list it in gaps_identified.
4. For skills_match and experience_match, ONLY quote or paraphrase content that actually exists in the resume.
5. If the job requires specific technologies (e.g., FPGA, Verilog, AWS, Python) and the resume does NOT mention them, the candidate does NOT qualify.
6. A candidate whose background is completely different from the job (e.g., DBA applying to FPGA role) should score BELOW 30.
7. LOCATION CHECK: If the job has a location requirement, verify candidate location matches. For remote jobs, same country is required. For on-site/hybrid, proximity to job location matters.

Respond in JSON format with these exact fields:
{{
    "match_score": <integer 0-100>,
    "match_summary": "<2-3 sentence summary of overall fit. IMPORTANT: If there is a country mismatch, say 'The candidate is based in [country] but the job requires [work type] work from [job country], creating a location compliance issue.' Do NOT use contradictory phrasing like 'mismatch which matches'.>",
    "skills_match": "<ONLY list skills from the resume that directly match job requirements - quote from resume>",
    "experience_match": "<ONLY list experience from the resume that is relevant to the job - be specific>",
    "gaps_identified": "<List ALL mandatory requirements NOT found in the resume INCLUDING location mismatches - this is critical>",
    "key_requirements": "<bullet list of the top 3-5 MANDATORY requirements from the job description>"
}}


SCORING GUIDELINES:
- 85-100: Candidate meets nearly ALL mandatory requirements with explicit evidence in resume AND location matches
- 70-84: Candidate meets MOST mandatory requirements but has 1-2 minor gaps (may include minor location concerns)
- 50-69: Candidate meets SOME requirements but is missing key qualifications or has location issues
- 30-49: Candidate has tangential experience, significant gaps, or major location mismatch
- 0-29: Candidate's background does not align with the role (wrong field/specialty or completely wrong location)

BE HONEST. If the resume does not show the required skills OR the candidate location doesn't match, the candidate should NOT score high."""

        try:
            system_message = """You are a strict, evidence-based technical recruiter analyzing candidate-job fit.

CRITICAL RULES:
1. You MUST only cite skills and experience that are EXPLICITLY written in the candidate's resume.
2. You MUST NOT infer or hallucinate skills that are not directly stated.
3. If a job requires FPGA and the resume shows SQL/database experience, they DO NOT match.
4. If a job requires Python and the resume only mentions Java, that is a GAP.
5. Be honest - a mismatched candidate should score LOW even if they have impressive but irrelevant skills.
6. Your assessment will be used for recruiter decisions - accuracy is critical.
7. LOCATION MATTERS: Check if the candidate's location is compatible with the job's work type (remote/onsite/hybrid).
   - Remote jobs: Candidate must be in the same COUNTRY for tax/legal compliance.
   - On-site/Hybrid jobs: Candidate should be in or near the job's city/metro area.
   - If candidate location doesn't match, this is a GAP that should reduce their score."""

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
            
            # Store data for deferred saving (to avoid Flask app context issues in parallel threads)
            # The caller should save these after parallel execution completes
            result['_deferred_save'] = {
                'job_id': job_id,
                'job_title': job_title,
                'key_requirements': key_requirements,
                'job_location_full': job_location_full,
                'work_type': work_type,
                'should_save': bool(key_requirements) and not custom_requirements
            }
            
            if not key_requirements:
                logging.warning(f"⚠️ AI did not return key_requirements for job {job_id} - requirements will not be saved")
            elif custom_requirements:
                logging.info(f"📝 Job {job_id} has custom requirements - skipping AI interpretation save (expected behavior)")
            # NOTE: Actual save is now deferred to caller to avoid Flask app context issues in parallel threads
            
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
            
            # Get resume text - PRIORITY: Use candidate's description field (parsed resume)
            # This is faster and more reliable than downloading/parsing files
            resume_text = None
            
            # First try: Get description field directly from candidate data
            raw_description = candidate.get('description') if candidate else None
            logging.info(f"📄 Candidate description field present: {bool(raw_description)}, type: {type(raw_description).__name__}, length: {len(str(raw_description)) if raw_description else 0}")
            
            if raw_description:
                description = str(raw_description).strip()
                # Clean HTML tags if present
                import re
                description = re.sub(r'<[^>]+>', ' ', description)
                description = re.sub(r'\s+', ' ', description).strip()
                
                logging.info(f"📄 After cleaning: {len(description)} chars, first 200: {description[:200]}")
                
                if len(description) >= 100:  # Minimum viable resume length
                    resume_text = description
                    logging.info(f"📄 Using candidate description field: {len(resume_text)} chars")
                else:
                    logging.info(f"Description too short ({len(description)} chars), will try file download")
            else:
                logging.info(f"📄 No description field in candidate data - will try file download")
            
            # Second try: Fall back to file download if description not available
            if not resume_text:
                logging.info("Falling back to resume file download...")
                file_content, filename = self.get_candidate_resume(candidate_id)
                if file_content and filename:
                    resume_text = self.extract_resume_text(file_content, filename)
                    if resume_text:
                        logging.info(f"Extracted {len(resume_text)} characters from resume file")
                    else:
                        logging.warning(f"Could not extract text from resume: {filename}")
                else:
                    logging.warning(f"No resume file found for candidate {candidate_id}")
            
            if resume_text:
                vetting_log.resume_text = resume_text[:50000]  # Limit storage size
            
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
            
            # CRITICAL: Cache resume text in local variable to prevent session expiration issues
            # SQLAlchemy expires object attributes after commits, so we need a stable copy
            cached_resume_text = vetting_log.resume_text
            
            # Extract candidate location from Bullhorn record
            # Primary: Use candidate's address field from Bullhorn
            # Fallback: AI will try to extract from resume text
            candidate_location = None
            if candidate and isinstance(candidate.get('address'), dict):
                candidate_location = candidate.get('address')
                loc_parts = [candidate_location.get('city', ''), candidate_location.get('state', ''), 
                            candidate_location.get('countryName', '') or candidate_location.get('country', '')]
                loc_str = ', '.join(filter(None, loc_parts))
                if loc_str:
                    logging.info(f"📍 Candidate location from Bullhorn: {loc_str}")
                else:
                    logging.info("📍 Candidate has address field but no city/state/country - AI will infer from resume")
            else:
                logging.info("📍 No address in Bullhorn record - AI will infer location from resume")
            
            # Analyze against each job - PARALLEL PROCESSING for faster throughput
            threshold = self.get_threshold()
            qualified_matches = []
            all_match_results = []
            
            # Pre-check resume validity once
            if not cached_resume_text or len(cached_resume_text.strip()) < 50:
                logging.error(f"❌ CRITICAL: Resume text missing or too short for candidate {candidate_id}")
                logging.error(f"   Resume text length: {len(cached_resume_text) if cached_resume_text else 0}")
                vetting_log.status = 'completed'
                vetting_log.error_message = 'Resume text too short for analysis'
                db.session.commit()
                return vetting_log
            
            # Get IDs of jobs already analyzed for this candidate
            existing_job_ids = set()
            existing_matches = CandidateJobMatch.query.filter_by(vetting_log_id=vetting_log.id).all()
            for match in existing_matches:
                existing_job_ids.add(match.bullhorn_job_id)
            
            # Filter to only jobs that need analysis
            jobs_to_analyze = [job for job in jobs if job.get('id') not in existing_job_ids]
            
            if not jobs_to_analyze:
                logging.info(f"All {len(jobs)} jobs already analyzed for this candidate")
                vetting_log.status = 'completed'
                vetting_log.analyzed_at = datetime.utcnow()
                db.session.commit()
                return vetting_log
            
            logging.info(f"🚀 Parallel analysis of {len(jobs_to_analyze)} jobs (skipping {len(existing_job_ids)} already analyzed)")
            logging.info(f"📄 Resume: {len(cached_resume_text)} chars, First 200: {cached_resume_text[:200]}")
            
            # PRE-FETCH all custom requirements BEFORE parallel processing
            # This is critical because parallel threads don't have Flask app context
            job_requirements_cache = {}
            for job in jobs_to_analyze:
                job_id = job.get('id')
                if job_id:
                    # Fetch custom requirements (or None if not set) - runs in main thread with app context
                    job_requirements_cache[job_id] = self._get_job_custom_requirements(job_id)
            
            logging.info(f"📋 Pre-fetched requirements for {len(job_requirements_cache)} jobs")
            
            # Helper function for parallel execution - runs AI analysis for one job
            def analyze_single_job(job_with_req):
                """Analyze one job match - called in parallel threads"""
                job = job_with_req['job']
                prefetched_req = job_with_req['requirements']  # Pre-fetched from main thread
                job_id = job.get('id')
                try:
                    analysis = self.analyze_candidate_job_match(
                        cached_resume_text, job, candidate_location,
                        prefetched_requirements=prefetched_req
                    )
                    return {
                        'job': job,
                        'job_id': job_id,
                        'analysis': analysis,
                        'error': None
                    }
                except Exception as e:
                    logging.error(f"Error analyzing job {job_id}: {str(e)}")
                    return {
                        'job': job,
                        'job_id': job_id,
                        'analysis': {'match_score': 0, 'match_summary': f'Analysis failed: {str(e)}'},
                        'error': str(e)
                    }
            
            # Prepare jobs with pre-fetched requirements
            jobs_with_requirements = [
                {'job': job, 'requirements': job_requirements_cache.get(job.get('id'))}
                for job in jobs_to_analyze
            ]
            
            # Run parallel analysis - max 15 concurrent threads to respect API rate limits
            analysis_results = []
            max_workers = min(15, len(jobs_to_analyze))
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(analyze_single_job, jwr): jwr for jwr in jobs_with_requirements}
                
                for future in as_completed(futures):
                    result = future.result()
                    analysis_results.append(result)
            
            logging.info(f"✅ Parallel analysis complete: {len(analysis_results)} jobs processed")
            
            # Process results and create match records (single-threaded for DB safety)
            for result in analysis_results:
                job = result['job']
                job_id = result['job_id']
                analysis = result['analysis']
                
                # Get recruiter info from job's assignedUsers
                recruiter_name = ''
                recruiter_email = ''
                recruiter_id = None
                
                assigned_users = job.get('assignedUsers', {})
                if isinstance(assigned_users, dict):
                    assigned_users_list = assigned_users.get('data', [])
                elif isinstance(assigned_users, list):
                    assigned_users_list = assigned_users
                else:
                    assigned_users_list = []
                
                if assigned_users_list and len(assigned_users_list) > 0:
                    first_user = assigned_users_list[0]
                    if isinstance(first_user, dict):
                        recruiter_name = f"{first_user.get('firstName', '')} {first_user.get('lastName', '')}".strip()
                        recruiter_email = first_user.get('email', '')
                        recruiter_id = first_user.get('id')
                
                # Determine if this is the job they applied to
                is_applied_job = vetting_log.applied_job_id == job_id if vetting_log.applied_job_id else False
                
                # Create match record - use job-specific threshold if set
                job_threshold = self.get_job_threshold(job_id)
                
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
                    is_qualified=analysis.get('match_score', 0) >= job_threshold,
                    is_applied_job=is_applied_job,
                    match_summary=analysis.get('match_summary', ''),
                    skills_match=analysis.get('skills_match', ''),
                    experience_match=analysis.get('experience_match', ''),
                    gaps_identified=analysis.get('gaps_identified', '')
                )
                
                db.session.add(match_record)
                all_match_results.append(match_record)
                
                # Log with threshold info (show if custom threshold used)
                threshold_note = f" (threshold: {int(job_threshold)}%)" if job_threshold != threshold else ""
                if match_record.is_qualified:
                    qualified_matches.append(match_record)
                    logging.info(f"  ✅ Match: {job.get('title')} - {analysis.get('match_score')}%{threshold_note}")
                else:
                    logging.info(f"  ❌ No match: {job.get('title')} - {analysis.get('match_score')}%{threshold_note}")
                
                # Handle deferred database save (now in main thread with Flask app context)
                deferred = analysis.get('_deferred_save')
                if deferred and deferred.get('should_save'):
                    try:
                        self._save_ai_interpreted_requirements(
                            deferred['job_id'],
                            deferred['job_title'],
                            deferred['key_requirements'],
                            deferred['job_location_full'],
                            deferred['work_type']
                        )
                    except Exception as save_err:
                        logging.warning(f"Failed to save requirements for job {deferred['job_id']}: {save_err}")
            
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
        
        # Build note content
        threshold = self.get_threshold()
        qualified_matches = [m for m in matches if m.is_qualified] if matches else []
        
        # Handle case where no jobs were analyzed (no matches recorded)
        if not matches:
            # Create a note explaining why no analysis was done
            error_reason = vetting_log.error_message or "No job matches could be performed"
            note_lines = [
                f"📋 AI VETTING SUMMARY - INCOMPLETE ANALYSIS",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Status: {vetting_log.status}",
                f"",
                f"Reason: {error_reason}",
                f"",
                f"This candidate could not be fully analyzed. Possible causes:",
                f"• No active jobs found in monitored tearsheets",
                f"• Resume could not be extracted or parsed",
                f"• Technical issue during processing",
                f"",
                f"Please review manually if needed."
            ]
            note_text = "\n".join(note_lines)
            action = "AI Vetting - Incomplete"
            
            note_id = bullhorn.create_candidate_note(
                vetting_log.bullhorn_candidate_id,
                note_text,
                action=action
            )
            
            if note_id:
                vetting_log.note_created = True
                vetting_log.bullhorn_note_id = note_id
                db.session.commit()
                logging.info(f"Created incomplete vetting note for candidate {vetting_log.bullhorn_candidate_id}")
                return True
            else:
                logging.error(f"Failed to create incomplete vetting note for candidate {vetting_log.bullhorn_candidate_id}")
                return False
        
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
            ]
            
            # Find applied job and separate from others
            applied_match = None
            other_qualified = []
            for match in qualified_matches:
                if match.is_applied_job:
                    applied_match = match
                else:
                    other_qualified.append(match)
            
            # Sort other qualified matches by score descending
            other_qualified.sort(key=lambda m: m.match_score, reverse=True)
            
            # Show applied job FIRST if qualified
            if applied_match:
                note_lines.append(f"APPLIED POSITION (QUALIFIED):")
                note_lines.append(f"")
                note_lines.append(f"• Job ID: {applied_match.bullhorn_job_id} - {applied_match.job_title}")
                note_lines.append(f"  Match Score: {applied_match.match_score:.0f}%")
                note_lines.append(f"  ⭐ APPLIED TO THIS POSITION")
                note_lines.append(f"  Summary: {applied_match.match_summary}")
                note_lines.append(f"  Skills: {applied_match.skills_match}")
                if other_qualified:
                    note_lines.append(f"")
                    note_lines.append(f"OTHER QUALIFIED POSITIONS:")
            else:
                note_lines.append(f"QUALIFIED POSITIONS:")
            
            # Show other qualified matches
            for match in other_qualified:
                note_lines.append(f"")
                note_lines.append(f"• Job ID: {match.bullhorn_job_id} - {match.job_title}")
                note_lines.append(f"  Match Score: {match.match_score:.0f}%")
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
            ]
            
            # Find the applied job (if analyzed)
            applied_match = None
            other_matches = []
            for match in matches:
                if match.is_applied_job:
                    applied_match = match
                else:
                    other_matches.append(match)
            
            # Sort other matches by score descending
            other_matches.sort(key=lambda m: m.match_score, reverse=True)
            
            # Show applied job FIRST if found
            if applied_match:
                note_lines.append(f"APPLIED POSITION:")
                note_lines.append(f"")
                note_lines.append(f"• Job ID: {applied_match.bullhorn_job_id} - {applied_match.job_title}")
                note_lines.append(f"  Match Score: {applied_match.match_score:.0f}%")
                note_lines.append(f"  ⭐ APPLIED TO THIS POSITION")
                if applied_match.gaps_identified:
                    note_lines.append(f"  Gaps: {applied_match.gaps_identified}")
                note_lines.append(f"")
                note_lines.append(f"OTHER TOP MATCHES:")
            else:
                note_lines.append(f"TOP ANALYSIS RESULTS:")
            
            # Show top 5 other matches (sorted by score)
            for match in other_matches[:5]:
                note_lines.append(f"")
                note_lines.append(f"• Job ID: {match.bullhorn_job_id} - {match.job_title}")
                note_lines.append(f"  Match Score: {match.match_score:.0f}%")
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
        # SAFETY CHECK: Re-verify vetting is still enabled before sending emails
        # This prevents emails if vetting was disabled mid-cycle
        # Force fresh database read to bypass SQLAlchemy session cache
        db.session.expire_all()
        if not self.is_enabled():
            logging.info(f"📧 Notification blocked - vetting disabled mid-cycle for {vetting_log.candidate_name}")
            return 0
        
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
        # CRITICAL: Force fresh database read to prevent SQLAlchemy session cache
        # from returning stale vetting_enabled value. This ensures toggles take
        # effect immediately across all environments (dev/production).
        db.session.expire_all()
        
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
            
            # ALSO detect Pandologic API candidates (they don't come through ParsedEmail)
            # These are fed directly into Bullhorn by Pandologic's integration
            pandologic_candidates = self.detect_pandologic_candidates(since_minutes=10)
            if pandologic_candidates:
                logging.info(f"🔵 Adding {len(pandologic_candidates)} Pandologic candidates to vetting queue")
                
                # Merge with existing candidates, dedupe by candidate ID
                existing_ids = {c.get('id') for c in candidates}
                for pando_candidate in pandologic_candidates:
                    if pando_candidate.get('id') not in existing_ids:
                        candidates.append(pando_candidate)
                        existing_ids.add(pando_candidate.get('id'))
                
                # Update detection method if we added Pandologic candidates
                if summary['detection_method'] == 'parsed_email':
                    summary['detection_method'] = 'parsed_email+pandologic'
                else:
                    summary['detection_method'] = 'bullhorn_search+pandologic'
            
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
