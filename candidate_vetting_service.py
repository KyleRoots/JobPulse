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
    BullhornMonitor, GlobalSettings, JobVettingRequirements, ParsedEmail,
    EmailDeliveryLog
)
from bullhorn_service import BullhornService
from email_service import EmailService
from vetting.geo_utils import smart_correct_country, normalize_country, map_work_type
from vetting.name_utils import parse_names, parse_emails
from vetting.resume_utils import (
    extract_resume_text as _extract_resume_text,
    extract_text_from_pdf as _extract_text_from_pdf,
    extract_text_from_docx as _extract_text_from_docx,
    extract_text_from_doc as _extract_text_from_doc,
)


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
        self.email_service = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
        self.openai_client = None
        self._init_openai()
        
        # Default settings
        self.match_threshold = 80.0  # Minimum match percentage for notifications
        self.check_interval_minutes = 5
        self.model = self._get_layer2_model()  # Default GPT-4o, configurable via VettingConfig
        
        # Embedding pre-filter (Layer 1)
        from embedding_service import EmbeddingService
        self.embedding_service = EmbeddingService()
        
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

        # PERF: Single bulk fetch replaces 4 individual queries per vetting cycle init.
        # One DB round-trip instead of four — net saving grows with vetting frequency.
        bh_keys = ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']
        rows = GlobalSettings.query.filter(GlobalSettings.setting_key.in_(bh_keys)).all()
        raw = {r.setting_key: r.setting_value for r in rows if r.setting_value}
        credentials = {k.replace('bullhorn_', ''): raw[k] for k in bh_keys if k in raw}

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
    
    def _get_layer2_model(self) -> str:
        """Get the Layer 2 model from VettingConfig (supports live revert)."""
        try:
            value = self.get_config_value('layer2_model', 'gpt-4o')
            if value and value.strip():
                return value.strip()
        except Exception:
            pass
        return 'gpt-4o'
    
    def _get_escalation_range(self) -> tuple:
        """Get escalation score range from VettingConfig.
        
        Returns:
            Tuple of (low, high) — scores within this range trigger GPT-4o re-analysis.
        """
        try:
            low = float(self.get_config_value('escalation_low', '60'))
            high = float(self.get_config_value('escalation_high', '85'))
            return (low, high)
        except (ValueError, TypeError):
            return (60.0, 85.0)
    
    def should_escalate_to_gpt4o(self, match_score: float) -> bool:
        """Check if a match score falls in the escalation range for GPT-4o re-analysis.
        
        Args:
            match_score: Layer 2 (GPT-4o-mini) match score.
            
        Returns:
            True if score is within [escalation_low, escalation_high].
        """
        low, high = self._get_escalation_range()
        return low <= match_score <= high
    
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
    
    def _get_global_custom_requirements(self) -> Optional[str]:
        """Get global screening instructions that apply to ALL jobs."""
        try:
            config = VettingConfig.query.filter_by(setting_key='global_custom_requirements').first()
            if config and config.setting_value and config.setting_value.strip():
                return config.setting_value.strip()
            return None
        except Exception as e:
            logging.error(f"Error getting global custom requirements: {str(e)}")
            return None
    
    def _recheck_years_calculation(self, resume_text: str, original_years_analysis: dict,
                                    job_id: int, job_title: str) -> Optional[dict]:
        """Re-check years-of-experience calculation when a >2yr shortfall is detected.
        
        Uses a focused prompt that asks GPT-4o to verify the arithmetic from the original
        analysis. This catches false negatives from model arithmetic errors (e.g., 
        miscounting 3.75yr as 1.8yr).
        
        Args:
            resume_text: The candidate's cleaned resume text
            original_years_analysis: The years_analysis dict from the initial analysis
            job_id: Bullhorn job ID for logging
            job_title: Job title for logging
            
        Returns:
            Corrected years_analysis dict if corrections were made, None if re-check
            confirms the original or if the re-check fails.
        """
        from datetime import datetime
        _today = datetime.utcnow()
        _today_str = _today.strftime('%B %d, %Y')
        _today_month = _today.month
        _today_year = _today.year
        
        # Build a summary of what needs re-checking
        skills_to_check = []
        for skill, data in original_years_analysis.items():
            if not isinstance(data, dict):
                continue
            if not data.get('meets_requirement', True):
                required = data.get('required_years', 0)
                estimated = data.get('estimated_years', 0)
                calculation = data.get('calculation', 'N/A')
                skills_to_check.append(
                    f"- {skill}: required {required}yr, initial estimate {estimated}yr, "
                    f"calculation: {calculation}"
                )
        
        if not skills_to_check:
            return None
        
        skills_summary = "\n".join(skills_to_check)
        
        prompt = f"""You are verifying a years-of-experience calculation. An initial AI analysis 
found the following shortfalls for Job "{job_title}" (ID: {job_id}):

{skills_summary}

TODAY'S DATE: {_today_str} (month {_today_month} of {_today_year}).

CANDIDATE RESUME:
{resume_text[:8000]}

INSTRUCTIONS:
1. For EACH skill listed above, re-scan the resume for ALL roles where the candidate performed 
   relevant work (focus on responsibilities, not just titles).
2. Calculate total months using the EXACT formula:
   Duration = (end_year - start_year) × 12 + (end_month - start_month)
   For "Present"/"Current" roles, use today: month {_today_month} of {_today_year}.
3. Show your step-by-step arithmetic.
4. Internships/part-time = 50% weight. University projects = 0.
5. Return the CORRECTED years_analysis.

Respond in JSON format:
{{
    "<skill_name>": {{
        "required_years": <N>,
        "estimated_years": <M>,
        "meets_requirement": true/false,
        "calculation": "<step-by-step month arithmetic>"
    }}
}}"""
        
        try:
            logging.info(f"🔄 Years re-check: verifying {len(skills_to_check)} skill(s) for job {job_id}")
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",  # Always use GPT-4o for re-checks (better arithmetic)
                messages=[
                    {"role": "system", "content": "You are a precise arithmetic calculator. "
                     "Your ONLY job is to verify years-of-experience calculations by counting "
                     "months between dates on a resume. Be exact. Show your work."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.0,  # Maximum determinism for arithmetic
                max_tokens=800
            )
            
            recheck = json.loads(response.choices[0].message.content)
            
            # Check if the re-check differs materially from the original
            any_correction = False
            for skill, data in recheck.items():
                if not isinstance(data, dict):
                    continue
                original = original_years_analysis.get(skill, {})
                if not isinstance(original, dict):
                    continue
                    
                orig_est = float(original.get('estimated_years', 0))
                new_est = float(data.get('estimated_years', 0))
                
                if abs(new_est - orig_est) >= 0.5:
                    any_correction = True
                    logging.info(
                        f"🔄 Years re-check CORRECTION for '{skill}' on job {job_id}: "
                        f"{orig_est:.1f}yr → {new_est:.1f}yr "
                        f"(calc: {data.get('calculation', 'N/A')})"
                    )
            
            if any_correction:
                logging.info(f"✅ Years re-check found corrections for job {job_id} — using updated values")
                return recheck
            else:
                logging.info(f"✅ Years re-check CONFIRMS original values for job {job_id}")
                return None
                
        except Exception as e:
            logging.error(f"❌ Years re-check failed for job {job_id}: {str(e)}")
            return None
    
    def extract_job_requirements(self, job_id: int, job_title: str, job_description: str,
                                  job_location: str = None, job_work_type: str = None) -> Optional[str]:
        """
        Extract mandatory requirements from a job description using AI.
        Called during monitoring when new jobs are indexed so requirements
        are available for review BEFORE any candidates are vetted.
        Also called for REFRESH when job is modified in Bullhorn.
        
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
        
        # NOTE: Removed early return check for existing requirements
        # This function is now called specifically for refresh when job is modified
        # So we always want to re-extract from the updated job description
        
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

You MUST output EXACTLY 5-7 requirements. No more, no less.
If the JD lists more than 7 qualifications, prioritize the most critical mandatory qualifications and CONSOLIDATE related items into a single requirement (e.g. merge "Python" + "SQL" + "data pipelines" into one "Technical skills" requirement).
Do NOT list every bullet point as a separate requirement.

Focus on requirements that are EXPLICITLY STATED in the job description:
1. Required technical skills (programming languages, tools, technologies)
2. Required years of experience — ONLY if the JD explicitly states a specific NUMBER (e.g., "5+ years", "3 years of experience", "10 or more years")
3. Required certifications or licenses
4. Required education level
5. Required industry-specific knowledge
6. Required location or work authorization

CRITICAL ANTI-HALLUCINATION RULES:
- ONLY list requirements that are EXPLICITLY written in the job description text above.
- Do NOT infer or fabricate years-of-experience requirements — if the JD does not state a specific number of years, do NOT add one based on the job title, seniority level, or your assumptions about the role.
- Do NOT add requirements based on what you think the role "should" need — only what the JD actually says.
- If the JD says "experience with X" without specifying years, list it as "Experience with X" — NOT "X+ years of X".
- If the JD uses vague phrases like "significant experience" or "proven track record", quote that phrase directly — do NOT convert it to a specific number of years.

Also DO NOT include:
- "Nice to have" or "preferred" qualifications
- Soft skills (communication, teamwork, etc.)
- Generic requirements that apply to any job

Format as a bullet-point list. Be specific and concise."""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a technical recruiter extracting ONLY explicitly stated mandatory requirements from job descriptions. You must NEVER infer, fabricate, or add requirements that are not directly written in the job description. If the job description does not mention a specific number of years, do NOT add one. Be concise and specific."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
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
    
    def refresh_empty_job_locations(self, jobs: list = None) -> dict:
        """
        One-time refresh of job locations for existing JobVettingRequirements 
        records that have empty job_location fields.
        
        This fetches the current address from Bullhorn and updates the database.
        
        Args:
            jobs: Optional list of job dicts from tearsheets. If None, fetches from tearsheets.
            
        Returns:
            Summary dict with refresh counts
        """
        results = {
            'jobs_checked': 0,
            'locations_updated': 0,
            'already_have_location': 0,
            'errors': []
        }
        
        try:
            # Get jobs if not provided
            if jobs is None:
                jobs = self.get_active_jobs_from_tearsheets()
            
            results['jobs_checked'] = len(jobs)
            
            if not jobs:
                return results
            
            # Build a lookup of job_id -> job data
            job_lookup = {int(job.get('id')): job for job in jobs if job.get('id')}
            
            # Find all requirements with empty locations
            empty_location_reqs = JobVettingRequirements.query.filter(
                (JobVettingRequirements.job_location == None) | 
                (JobVettingRequirements.job_location == '')
            ).all()
            
            updates_made = 0
            for req in empty_location_reqs:
                job = job_lookup.get(req.bullhorn_job_id)
                if not job:
                    continue
                
                # Extract location from job address
                job_address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
                job_city = job_address.get('city', '')
                job_state = job_address.get('state', '')
                job_country = job_address.get('countryName', '') or job_address.get('country', '')
                job_location = ', '.join(filter(None, [job_city, job_state, job_country]))
                
                if job_location:
                    req.job_location = job_location
                    updates_made += 1
                    logging.info(f"📍 Updated location for job {req.bullhorn_job_id}: {job_location}")
            
            if updates_made > 0:
                db.session.commit()
                results['locations_updated'] = updates_made
                logging.info(f"📍 Location refresh complete: updated {updates_made} jobs with empty locations")
            
            # Count jobs that already have locations
            results['already_have_location'] = results['jobs_checked'] - len(empty_location_reqs)
            
        except Exception as e:
            db.session.rollback()
            results['errors'].append(str(e))
            logging.error(f"Error refreshing job locations: {str(e)}")
        
        return results
    
    def get_candidates_with_duplicates(self, sample_size: int = 5) -> dict:
        """
        Query Bullhorn to find candidates with duplicate AI Vetting notes.
        Returns a sample of candidate IDs for manual verification.
        
        Args:
            sample_size: Number of candidate IDs to return
            
        Returns:
            Dict with candidate IDs that have duplicates and their duplicate counts
        """
        from bullhorn_service import BullhornService
        from models import GlobalSettings, CandidateVettingLog
        
        logging.info(f"🔍 Querying for candidates with duplicate AI Vetting notes...")
        
        results = {
            'candidates_with_duplicates': [],
            'total_checked': 0,
            'errors': []
        }
        
        try:
            # Get Bullhorn credentials
            credentials = {}
            for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
                setting = GlobalSettings.query.filter_by(setting_key=key).first()
                if setting and setting.setting_value:
                    credentials[key] = setting.setting_value.strip()
            
            bullhorn = BullhornService(
                client_id=credentials.get('bullhorn_client_id'),
                client_secret=credentials.get('bullhorn_client_secret'),
                username=credentials.get('bullhorn_username'),
                password=credentials.get('bullhorn_password')
            )
            
            if not bullhorn.authenticate():
                results['errors'].append("Failed to authenticate with Bullhorn")
                return results
            
            # Get candidate IDs from our vetting logs
            candidate_rows = db.session.query(
                CandidateVettingLog.bullhorn_candidate_id
            ).filter(
                CandidateVettingLog.note_created == True,
                CandidateVettingLog.bullhorn_candidate_id.isnot(None)
            ).distinct().order_by(
                CandidateVettingLog.bullhorn_candidate_id.desc()  # Most recent first
            ).limit(50).all()
            
            candidate_ids = [row[0] for row in candidate_rows]
            
            for candidate_id in candidate_ids:
                if not candidate_id:
                    continue
                
                results['total_checked'] += 1
                
                # Fetch notes for this candidate
                notes_url = f"{bullhorn.base_url}entity/Candidate/{candidate_id}/notes"
                notes_params = {
                    'fields': 'id,action,dateAdded,isDeleted',
                    'count': 200,
                    'BhRestToken': bullhorn.rest_token
                }
                
                try:
                    notes_response = bullhorn.session.get(notes_url, params=notes_params, timeout=15)
                    if notes_response.status_code != 200:
                        continue
                    
                    notes_data = notes_response.json()
                    notes = notes_data.get('data', [])
                    
                    # Filter for AI Vetting notes
                    ai_vetting_notes = [
                        n for n in notes 
                        if n.get('action') and 'AI Vetting' in n.get('action', '') and not n.get('isDeleted')
                    ]
                    
                    # If more than 1 AI Vetting note, this has duplicates
                    if len(ai_vetting_notes) > 1:
                        results['candidates_with_duplicates'].append({
                            'candidate_id': candidate_id,
                            'duplicate_count': len(ai_vetting_notes),
                            'note_timestamps': [n.get('dateAdded') for n in ai_vetting_notes[:5]]
                        })
                        
                        # Stop once we have enough samples
                        if len(results['candidates_with_duplicates']) >= sample_size:
                            break
                            
                except Exception as e:
                    logging.warning(f"Error checking candidate {candidate_id}: {e}")
                    continue
            
            logging.info(f"🔍 Found {len(results['candidates_with_duplicates'])} candidates with duplicates out of {results['total_checked']} checked")
            return results
            
        except Exception as e:
            results['errors'].append(str(e))
            logging.error(f"Error querying candidates with duplicates: {e}")
            return results
    
    def cleanup_duplicate_notes_batch(self, batch_size: int = 10) -> dict:
        """
        DEPRECATED (2026-02-07): This cleanup method has completed its work.
        - All 1,398 candidates were scanned and duplicates removed
        - Prevention logic in create_candidate_note() prevents new duplicates
        - This stub remains for compatibility but does nothing
        """
        return {
            'candidates_processed': 0,
            'notes_deleted': 0,
            'cleanup_complete': True,
            'deprecated': True,
            'errors': []
        }
    
    # The following 160+ lines of the original cleanup_duplicate_notes_batch method
    # have been removed since cleanup is complete. See git history for original code.
        from models import GlobalSettings, CandidateVettingLog
        from sqlalchemy import func
        
        logging.info(f"🧹 Starting duplicate notes cleanup batch (batch_size={batch_size})")
        
        results = {
            'candidates_processed': 0,
            'notes_deleted': 0,
            'cleanup_complete': False,
            'errors': []
        }
        
        try:
            # Get Bullhorn credentials
            credentials = {}
            for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
                setting = GlobalSettings.query.filter_by(setting_key=key).first()
                if setting and setting.setting_value:
                    credentials[key] = setting.setting_value.strip()
            
            bullhorn = BullhornService(
                client_id=credentials.get('bullhorn_client_id'),
                client_secret=credentials.get('bullhorn_client_secret'),
                username=credentials.get('bullhorn_username'),
                password=credentials.get('bullhorn_password')
            )
            
            if not bullhorn.authenticate():
                results['errors'].append("Failed to authenticate with Bullhorn")
                return results
            
            # OPTIMIZED: Query local database for candidates with AI vetting notes
            # This targets only the ~233 affected candidates instead of 958K from Bullhorn
            # Get unique candidate IDs that have notes created (note_created=True)
            
            # Get offset from database (persists across server restarts/deployments)
            offset_setting = GlobalSettings.query.filter_by(setting_key='cleanup_notes_offset').first()
            current_offset = int(offset_setting.setting_value) if offset_setting and offset_setting.setting_value else 0
            
            # Get distinct candidate IDs from our vetting logs where notes were created
            candidate_ids_query = db.session.query(
                CandidateVettingLog.bullhorn_candidate_id
            ).filter(
                CandidateVettingLog.note_created == True,
                CandidateVettingLog.bullhorn_candidate_id.isnot(None)
            ).distinct().order_by(
                CandidateVettingLog.bullhorn_candidate_id
            ).offset(current_offset).limit(batch_size)
            
            candidate_rows = candidate_ids_query.all()
            candidate_ids = [row[0] for row in candidate_rows]
            
            # Count total candidates with notes (for progress tracking)
            total_count = db.session.query(
                func.count(func.distinct(CandidateVettingLog.bullhorn_candidate_id))
            ).filter(
                CandidateVettingLog.note_created == True,
                CandidateVettingLog.bullhorn_candidate_id.isnot(None)
            ).scalar() or 0
            
            logging.info(f"🧹 Note cleanup: Found {len(candidate_ids)} candidates from local DB (offset={current_offset}, total={total_count})")
            
            if not candidate_ids:
                # Reset offset for next cycle since we've processed all
                self._save_cleanup_offset(0)
                results['cleanup_complete'] = True
                logging.info("🧹 Note cleanup: Completed full scan of all vetted candidates, resetting offset")
                return results
            
            # Advance offset for next cycle
            new_offset = current_offset + len(candidate_ids)
            
            # If we've gone through all candidates, reset for next cycle
            if new_offset >= total_count:
                new_offset = 0
                logging.info(f"🧹 Note cleanup: Reached end of {total_count} vetted candidates, will restart next cycle")
            
            # Save offset to database (persists across restarts)
            self._save_cleanup_offset(new_offset)
            
            for candidate_id in candidate_ids:
                if not candidate_id:
                    continue
                
                results['candidates_processed'] += 1
                
                # Fetch notes for this candidate
                notes_url = f"{bullhorn.base_url}entity/Candidate/{candidate_id}/notes"
                notes_params = {
                    'fields': 'id,action,dateAdded,isDeleted',
                    'count': 200,
                    'BhRestToken': bullhorn.rest_token
                }
                
                try:
                    notes_response = bullhorn.session.get(notes_url, params=notes_params, timeout=15)
                    if notes_response.status_code != 200:
                        continue
                    
                    notes_data = notes_response.json()
                    all_notes = notes_data.get('data', [])
                    
                    # Filter for screening notes (backward-compat: match old, intermediate, and new action strings)
                    screening_actions = {
                        'AI Vetting - Not Recommended',
                        'Scout Screening - Not Recommended',  # Legacy (>30 chars, never created successfully)
                        'Scout Screen - Not Qualified',        # Current format (≤30 chars)
                    }
                    vetting_notes = [
                        n for n in all_notes 
                        if n.get('action') in screening_actions
                        and not n.get('isDeleted', False)
                    ]
                    
                    if len(vetting_notes) <= 1:
                        continue
                    
                    # Sort by dateAdded (oldest first)
                    vetting_notes.sort(key=lambda x: x.get('dateAdded', 0))
                    
                    # Identify duplicates
                    last_kept_time = None
                    notes_to_delete = []
                    
                    for note in vetting_notes:
                        note_time = note.get('dateAdded', 0)
                        if isinstance(note_time, int):
                            note_datetime = datetime.utcfromtimestamp(note_time / 1000)
                        else:
                            continue
                        
                        if last_kept_time is None:
                            last_kept_time = note_datetime
                        else:
                            time_diff = (note_datetime - last_kept_time).total_seconds() / 60
                            if time_diff >= 60:
                                last_kept_time = note_datetime
                            else:
                                notes_to_delete.append(note)
                    
                    # Delete duplicates
                    for note in notes_to_delete:
                        note_id = note.get('id')
                        try:
                            delete_url = f"{bullhorn.base_url}entity/Note/{note_id}"
                            delete_data = {'isDeleted': True}
                            delete_response = bullhorn.session.post(
                                delete_url, 
                                json=delete_data,
                                params={'BhRestToken': bullhorn.rest_token},
                                timeout=5
                            )
                            if delete_response.status_code == 200:
                                results['notes_deleted'] += 1
                        except Exception as e:
                            pass  # Continue with other notes
                            
                except Exception as e:
                    continue
            
            if results['notes_deleted'] > 0:
                logging.info(f"🧹 Note cleanup: Deleted {results['notes_deleted']} duplicate notes from {results['candidates_processed']} candidates")
            
        except Exception as e:
            results['errors'].append(str(e))
            logging.error(f"Error in duplicate notes cleanup: {str(e)}")
        
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
                        
                        # ALWAYS re-extract AI interpretation, even with custom override
                        # Custom Override supplements AI interpretation, doesn't replace it
                        extracted = self.extract_job_requirements(
                            int(job_id), job_title, job_description,
                            job_location, job_work_type
                        )
                        if extracted:
                            logging.info(f"  ✅ Refreshed AI interpretation for job {job_id}")
                        else:
                            logging.warning(f"  ⚠️ Could not refresh AI interpretation for job {job_id}")
                        
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
    
    def sync_job_recruiter_assignments(self, jobs: list = None) -> dict:
        """
        Sync recruiter assignments from Bullhorn to existing CandidateJobMatch records.
        This ensures that recruiters added to jobs AFTER initial vetting still receive notifications.
        
        Should be called periodically (alongside job change detection) to pick up recruiter changes.
        
        Args:
            jobs: Optional list of job dicts from tearsheets. If None, fetches from tearsheets.
            
        Returns:
            Summary dict with sync counts
        """
        results = {
            'jobs_checked': 0,
            'matches_updated': 0,
            'recruiters_added': 0,
            'errors': []
        }
        
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            results['errors'].append("Could not connect to Bullhorn")
            return results
        
        try:
            # Get jobs if not provided
            if jobs is None:
                jobs = self.get_active_jobs_from_tearsheets()
            
            results['jobs_checked'] = len(jobs)
            
            if not jobs:
                return results
            
            # Build job ID -> current recruiters mapping
            job_recruiters = {}
            for job in jobs:
                job_id = job.get('id')
                if not job_id:
                    continue
                    
                # Extract all recruiter emails from assignedUsers
                assigned_users = job.get('assignedUsers', {})
                if isinstance(assigned_users, dict):
                    assigned_users_list = assigned_users.get('data', [])
                elif isinstance(assigned_users, list):
                    assigned_users_list = assigned_users
                else:
                    assigned_users_list = []
                
                recruiter_emails = []
                recruiter_names = []
                recruiter_ids = []
                
                for user in assigned_users_list:
                    if isinstance(user, dict):
                        email = user.get('email', '')
                        name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
                        user_id = user.get('id')
                        if email:
                            recruiter_emails.append(email)
                        if name:
                            recruiter_names.append(name)
                        if user_id:
                            recruiter_ids.append(str(user_id))
                
                if recruiter_emails:
                    job_recruiters[int(job_id)] = {
                        'emails': ', '.join(recruiter_emails),
                        'names': ', '.join(recruiter_names),
                        'primary_id': int(recruiter_ids[0]) if recruiter_ids else None
                    }
            
            # Find all CandidateJobMatch records for these jobs that might need updating
            job_ids = list(job_recruiters.keys())
            if not job_ids:
                return results
            
            matches = CandidateJobMatch.query.filter(
                CandidateJobMatch.bullhorn_job_id.in_(job_ids)
            ).all()
            
            # Update matches where recruiter info has changed
            for match in matches:
                current_data = job_recruiters.get(match.bullhorn_job_id)
                if not current_data:
                    continue
                
                current_emails = set(e.strip() for e in current_data['emails'].split(',') if e.strip())
                stored_emails = set(e.strip() for e in (match.recruiter_email or '').split(',') if e.strip())
                
                # Check if any new recruiters were added
                new_recruiters = current_emails - stored_emails
                
                if new_recruiters:
                    # Update the match record with current recruiter info
                    old_emails = match.recruiter_email
                    match.recruiter_email = current_data['emails']
                    match.recruiter_name = current_data['names']
                    # Keep primary ID for backward compatibility
                    if current_data['primary_id']:
                        match.recruiter_bullhorn_id = current_data['primary_id']
                    
                    results['matches_updated'] += 1
                    results['recruiters_added'] += len(new_recruiters)
                    
                    logging.info(f"🔄 Updated job {match.bullhorn_job_id} match #{match.id}: "
                                f"added {len(new_recruiters)} recruiter(s) - {', '.join(new_recruiters)}")
            
            if results['matches_updated'] > 0:
                db.session.commit()
                logging.info(f"✅ Recruiter sync complete: {results['matches_updated']} matches updated, "
                            f"{results['recruiters_added']} recruiters added")
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error in recruiter assignment sync: {str(e)}")
            results['errors'].append(str(e))
        
        return results
    
    # TTL cache for get_active_job_ids — avoids 7+ Bullhorn API calls per page load
    _active_job_ids_cache: set = None
    _active_job_ids_cache_time: float = 0
    _ACTIVE_JOB_IDS_TTL = 300  # 5 minutes

    # OpenAI quota exhaustion tracking (class-level, shared across instances)
    _consecutive_quota_errors: int = 0
    _quota_alert_sent: bool = False

    def get_active_job_ids(self) -> set:
        """Get set of active job IDs from tearsheets (for filtering).
        
        Results are cached for 5 minutes to avoid expensive Bullhorn API
        calls on every /screening page load.
        """
        import time
        now = time.time()
        if (CandidateVettingService._active_job_ids_cache is not None
                and now - CandidateVettingService._active_job_ids_cache_time < self._ACTIVE_JOB_IDS_TTL):
            return CandidateVettingService._active_job_ids_cache
        try:
            active_jobs = self.get_active_jobs_from_tearsheets()
            result = set(int(job.get('id')) for job in active_jobs if job.get('id'))
            CandidateVettingService._active_job_ids_cache = result
            CandidateVettingService._active_job_ids_cache_time = now
            return result
        except Exception as e:
            logging.error(f"Error getting active job IDs: {str(e)}")
            return CandidateVettingService._active_job_ids_cache or set()
    
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
        
        # BATCH: Pre-fetch all existing requirements in one query instead of per-job
        job_ids = [int(j.get('id')) for j in jobs if j.get('id')]
        existing_reqs = {}
        if job_ids:
            existing_rows = JobVettingRequirements.query.filter(
                JobVettingRequirements.bullhorn_job_id.in_(job_ids)
            ).all()
            existing_reqs = {r.bullhorn_job_id: r for r in existing_rows}
        
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
                
            # Check pre-fetched requirements (no per-job query)
            existing = existing_reqs.get(int(job_id))
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
            # Always rollback first in case session is in a bad state from errors
            try:
                db.session.rollback()
            except Exception:
                pass
            config = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            if config:
                config.setting_value = 'false'
                db.session.commit()
        except Exception as e:
            logging.error(f"Error releasing vetting lock: {str(e)}")
            try:
                db.session.rollback()
            except Exception:
                pass
    
    def _should_skip_candidate(self, candidate_id: int, applied_job_id: int = None) -> bool:
        """
        Job-aware dedup: decide whether to skip a candidate based on their vetting history.
        
        Rules:
        - Different job → always rescreen (return False)
        - Same job within 24h → skip (return True)
        - Same job 3+ times within 7 days → skip (return True)
        - No applied_job_id context → fall back to 24h global dedup
        
        Args:
            candidate_id: Bullhorn candidate ID
            applied_job_id: The job ID the candidate applied to (None if unknown)
            
        Returns:
            True if candidate should be skipped, False if they should be rescreened
        """
        from datetime import timedelta
        
        if not applied_job_id:
            # No job context — fall back to 24h global dedup (cross-path safety)
            recent_cutoff = datetime.utcnow() - timedelta(hours=24)
            recent = CandidateVettingLog.query.filter(
                CandidateVettingLog.bullhorn_candidate_id == candidate_id,
                CandidateVettingLog.status.in_(['completed', 'processing']),
                CandidateVettingLog.created_at >= recent_cutoff
            ).first()
            if recent:
                logging.debug(
                    f"Candidate {candidate_id} vetted within 24h (no job context), skipping"
                )
            return recent is not None
        
        # Rule 1: Same job within 24h → skip
        recent_cutoff = datetime.utcnow() - timedelta(hours=24)
        same_job_recent = CandidateVettingLog.query.filter(
            CandidateVettingLog.bullhorn_candidate_id == candidate_id,
            CandidateVettingLog.applied_job_id == applied_job_id,
            CandidateVettingLog.status.in_(['completed', 'processing']),
            CandidateVettingLog.created_at >= recent_cutoff
        ).first()
        if same_job_recent:
            logging.debug(
                f"Candidate {candidate_id} vetted for job {applied_job_id} within 24h, skipping"
            )
            return True
        
        # Rule 2: Same job 3+ times in 7 days → skip
        week_cutoff = datetime.utcnow() - timedelta(days=7)
        same_job_week_count = CandidateVettingLog.query.filter(
            CandidateVettingLog.bullhorn_candidate_id == candidate_id,
            CandidateVettingLog.applied_job_id == applied_job_id,
            CandidateVettingLog.status.in_(['completed', 'processing']),
            CandidateVettingLog.created_at >= week_cutoff
        ).count()
        if same_job_week_count >= 3:
            logging.debug(
                f"Candidate {candidate_id} vetted for job {applied_job_id} "
                f"{same_job_week_count} times in 7 days, skipping (soft cap)"
            )
            return True
        
        # Different job or under caps → allow rescreening
        return False
    
    def detect_new_applicants(self, since_minutes: int = 5) -> List[Dict]:
        """
        Find new candidates with "Online Applicant" status that haven't been processed yet.
        Uses dateLastModified filter to catch both new and returning candidates.
        
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
            
            # Use dateLastModified to catch returning candidates who reapply to new jobs
            # (dateAdded only reflects candidate creation, not new applications)
            url = f"{bullhorn.base_url}search/Candidate"
            params = {
                'query': f'status:"Online Applicant" AND dateLastModified:[{since_timestamp} TO *]',
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation,description,address(address1,city,state,countryName)',
                'count': 50,  # Limit batch size for performance
                'sort': '-dateLastModified',  # Most recently modified first
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logging.error(f"Failed to search for applicants: {response.status_code}")
                return []
            
            data = response.json()
            candidates = data.get('data', [])
            
            logging.info(f"Bullhorn returned {len(candidates)} candidates since {since_time}")
            
            # Job-aware dedup: allow rescreening for different jobs
            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue
                
                # For Online Applicants detected via Bullhorn search, we don't have the
                # applied job ID at this stage. Use global 24h dedup as a safety net.
                # The ParsedEmail path (primary) already handles job-aware dedup properly.
                if self._should_skip_candidate(candidate_id):
                    logging.debug(f"Candidate {candidate_id} vetted recently, skipping")
                else:
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
        
        Uses dateLastModified to catch returning candidates who reapply to new jobs
        (dateAdded only reflects candidate creation, not new applications).
        
        Job-aware dedup: candidates applying to different jobs are always rescreened.
        
        Args:
            since_minutes: Only look at candidates modified in the last N minutes (fallback)
            
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
            
            # Use dateLastModified to catch returning candidates who reapply to new jobs
            # (dateAdded only reflects candidate creation, not new applications)
            url = f"{bullhorn.base_url}search/Candidate"
            params = {
                'query': f'owner.name:"Pandologic API" AND dateLastModified:[{since_timestamp} TO *]',
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation,description,address(address1,city,state,countryName),owner(name)',
                'count': 50,
                'sort': '-dateLastModified',
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logging.error(f"Failed to search for Pandologic candidates: {response.status_code}")
                return []
            
            data = response.json()
            candidates = data.get('data', [])
            
            logging.info(f"🔍 Pandologic: Found {len(candidates)} candidates since {since_time}")
            
            # Job-aware dedup: get latest JobSubmission for each candidate to check
            # if they were already vetted for THIS specific job
            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue
                
                # Get latest job submission to determine which job they applied to
                applied_job_id = None
                try:
                    sub_url = f"{bullhorn.base_url}search/JobSubmission"
                    sub_params = {
                        'query': f'candidate.id:{candidate_id}',
                        'fields': 'id,jobOrder(id,title),dateAdded',
                        'count': 1,
                        'sort': '-dateAdded',
                        'BhRestToken': bullhorn.rest_token
                    }
                    sub_response = bullhorn.session.get(sub_url, params=sub_params, timeout=15)
                    if sub_response.status_code == 200:
                        submissions = sub_response.json().get('data', [])
                        if submissions:
                            job_order = submissions[0].get('jobOrder', {})
                            applied_job_id = job_order.get('id')
                            candidate['_applied_job_id'] = applied_job_id
                            candidate['_applied_job_title'] = job_order.get('title', '')
                except Exception as e:
                    logging.debug(f"Could not fetch JobSubmission for candidate {candidate_id}: {str(e)}")
                
                # Job-aware dedup: different job = always allow; same job = apply caps
                if self._should_skip_candidate(candidate_id, applied_job_id):
                    logging.debug(
                        f"Pandologic candidate {candidate_id} skipped by job-aware dedup "
                        f"(applied_job={applied_job_id})"
                    )
                else:
                    new_candidates.append(candidate)
                    job_info = f" for job {applied_job_id}" if applied_job_id else ""
                    logging.info(
                        f"🔵 Pandologic candidate detected: "
                        f"{candidate.get('firstName')} {candidate.get('lastName')} "
                        f"(ID: {candidate_id}{job_info})"
                    )
            
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
        
        Database query runs FIRST (no external API needed), and Bullhorn auth is only
        attempted when there are actual candidates to fetch details for.
        
        Args:
            limit: Maximum number of candidates to return (configurable batch size)
            
        Returns:
            List of candidate dictionaries ready for vetting
        """
        try:
            # ── Step 1: Query local database FIRST (no API call needed) ──
            from sqlalchemy import func, case
            stats = db.session.query(
                func.count(ParsedEmail.id).label('total'),
                func.count(case((ParsedEmail.status == 'completed', 1))).label('completed'),
                func.count(case((
                    (ParsedEmail.status == 'completed') & (ParsedEmail.bullhorn_candidate_id.isnot(None)),
                    1
                ))).label('with_candidate'),
                func.count(case((
                    (ParsedEmail.status == 'completed') & (ParsedEmail.bullhorn_candidate_id.isnot(None)) & (ParsedEmail.vetted_at.isnot(None)),
                    1
                ))).label('already_vetted'),
            ).first()
            
            logging.info(f"📊 ParsedEmail stats: total={stats.total}, completed={stats.completed}, "
                        f"with_candidate_id={stats.with_candidate}, already_vetted={stats.already_vetted}, "
                        f"pending_vetting={stats.with_candidate - stats.already_vetted}")
            
            # DEBUG: Show most recent 5 ParsedEmail records (only at DEBUG level)
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                recent_emails = ParsedEmail.query.order_by(ParsedEmail.received_at.desc()).limit(5).all()
                for pe in recent_emails:
                    logging.debug(f"  📧 Recent ParsedEmail id={pe.id}: candidate='{pe.candidate_name}', "
                                f"status={pe.status}, bh_id={pe.bullhorn_candidate_id}, "
                                f"vetted_at={'SET' if pe.vetted_at else 'NULL'}, received={pe.received_at}")
            
            # Query ParsedEmail for completed applications that haven't been vetted
            # Apply cutoff date if configured (skip historical backlog)
            cutoff_dt = None
            cutoff_raw = VettingConfig.get_value('vetting_cutoff_date')
            if cutoff_raw:
                # Accept both 'YYYY-MM-DD HH:MM:SS' and ISO 'YYYY-MM-DDTHH:MM:SS'
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
                    try:
                        cutoff_dt = datetime.strptime(cutoff_raw.strip(), fmt)
                        break
                    except ValueError:
                        continue
                if cutoff_dt:
                    logging.info(f"📅 Vetting cutoff active: only processing applicants received after {cutoff_dt} UTC")
                else:
                    logging.error(f"❌ Invalid vetting_cutoff_date format: '{cutoff_raw}' — expected 'YYYY-MM-DD HH:MM:SS' or ISO format. Cutoff DISABLED — entire backlog will be processed!")
            
            filters = [
                ParsedEmail.status == 'completed',
                ParsedEmail.vetted_at.is_(None),
                ParsedEmail.bullhorn_candidate_id.isnot(None),
            ]
            if cutoff_dt:
                filters.append(ParsedEmail.received_at >= cutoff_dt)
            
            unvetted_emails = ParsedEmail.query.filter(
                *filters
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
            
            # BATCH: Pre-fetch vetting logs linked to these specific ParsedEmail IDs
            batch_email_ids = [pe.id for pe in unvetted_emails]
            
            vetted_email_ids = set()
            if batch_email_ids:
                # Check if a vetting log already exists for these specific ParsedEmail IDs
                # This is the key dedup: same ParsedEmail.id = duplicate loop, different = valid re-application
                existing_logs = CandidateVettingLog.query.filter(
                    CandidateVettingLog.parsed_email_id.in_(batch_email_ids),
                    CandidateVettingLog.status.in_(['completed', 'failed', 'processing'])
                ).all()
                vetted_email_ids = {log.parsed_email_id for log in existing_logs}
                if vetted_email_ids:
                    logging.info(f"Found {len(vetted_email_ids)} ParsedEmails already linked to vetting logs")
            
            # Filter out already-vetted before making any Bullhorn API calls
            candidates_needing_details = []
            for parsed_email in unvetted_emails:
                candidate_id = parsed_email.bullhorn_candidate_id
                
                # Dedup: skip if a vetting log already exists for THIS specific ParsedEmail
                if parsed_email.id in vetted_email_ids:
                    already_vetted_ids.append(parsed_email.id)
                    logging.info(f"Candidate {candidate_id} already vetted for ParsedEmail {parsed_email.id}, skipping (duplicate loop prevention)")
                    continue
                
                candidates_needing_details.append(parsed_email)
            
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
            
            # ── Step 2: Only authenticate with Bullhorn if we have candidates to fetch ──
            if not candidates_needing_details:
                logging.info("All unvetted candidates were already processed or skipped")
                return []
            
            logging.info(f"Need Bullhorn details for {len(candidates_needing_details)} candidates")
            
            bullhorn = self._get_bullhorn_service()
            if not bullhorn:
                logging.warning(f"⚠️ Bullhorn service unavailable — {len(candidates_needing_details)} candidates waiting for vetting")
                return []
            
            if not bullhorn.authenticate():
                logging.warning(f"⚠️ Bullhorn authentication failed (possible rate limit) — "
                              f"{len(candidates_needing_details)} candidates waiting for vetting. "
                              f"Will retry next cycle.")
                return []
            
            # ── Step 3: Fetch candidate details from Bullhorn ──
            for parsed_email in candidates_needing_details:
                candidate_id = parsed_email.bullhorn_candidate_id
                candidate_data = self._fetch_candidate_details(bullhorn, candidate_id)
                
                if candidate_data:
                    # Attach the ParsedEmail ID for tracking
                    candidate_data['_parsed_email_id'] = parsed_email.id
                    candidate_data['_applied_job_id'] = parsed_email.bullhorn_job_id
                    candidate_data['_is_duplicate'] = parsed_email.is_duplicate_candidate
                    candidates_to_vet.append(candidate_data)
                    logging.info(f"Queued for vetting: {candidate_data.get('firstName')} {candidate_data.get('lastName')} (ID: {candidate_id}, Applied to Job: {parsed_email.bullhorn_job_id})")
            
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
                'fields': 'id,firstName,lastName,email,phone,address,status,dateAdded,dateLastModified,source,occupation,description',
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
    
    def _fetch_applied_job(self, bullhorn: 'BullhornService', job_id: int) -> Optional[Dict]:
        """
        Fetch a single job by ID from Bullhorn for applied-job injection.
        
        Used when the applied job isn't in a monitored tearsheet. Returns the
        job dict in the same format as get_active_jobs_from_tearsheets() so it
        can be seamlessly added to the job list.
        
        Only returns jobs with status 'Accepting Candidates' or where isOpen=True.
        Returns None for closed/deleted/invalid jobs.
        
        Args:
            bullhorn: Authenticated Bullhorn service
            job_id: Bullhorn job order ID
            
        Returns:
            Job dictionary matching tearsheet format, or None if closed/invalid
        """
        if not bullhorn or not bullhorn.rest_token:
            return None
        
        try:
            url = f"{bullhorn.base_url}entity/JobOrder/{job_id}"
            params = {
                'fields': (
                    'id,title,isOpen,status,dateAdded,dateLastModified,'
                    'clientCorporation(name),description,publicDescription,'
                    'address(address1,city,state,countryName),'
                    'employmentType,onSite,'
                    'assignedUsers(id,firstName,lastName,email),'
                    'responseUser(firstName,lastName),owner(firstName,lastName)'
                ),
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logging.warning(
                    f"Bullhorn returned {response.status_code} for job {job_id}"
                )
                return None
            
            job_data = response.json().get('data', {})
            
            if not job_data or not job_data.get('id'):
                return None
            
            # Only return open jobs
            is_open = job_data.get('isOpen', False)
            status = job_data.get('status', '')
            
            if not is_open and status != 'Accepting Candidates':
                logging.info(
                    f"Applied job {job_id} is closed (isOpen={is_open}, "
                    f"status={status}) — skipping injection"
                )
                return None
            
            # Enrich with user emails (same pattern as get_active_jobs_from_tearsheets)
            assigned_users = job_data.get('assignedUsers', {})
            if isinstance(assigned_users, dict):
                users_list = assigned_users.get('data', [])
            elif isinstance(assigned_users, list):
                users_list = assigned_users
            else:
                users_list = []
            
            user_ids = [u.get('id') for u in users_list if isinstance(u, dict) and u.get('id')]
            if user_ids:
                user_email_map = bullhorn.get_user_emails(user_ids)
                for user in users_list:
                    if isinstance(user, dict) and user.get('id') in user_email_map:
                        user['email'] = user_email_map[user['id']].get('email', '')
            
            # Mark as injected for audit trail
            job_data['_injected_applied_job'] = True
            
            return job_data
            
        except Exception as e:
            logging.error(f"Error fetching applied job {job_id}: {str(e)}")
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
        return _extract_resume_text(file_content, filename)
    
    def _extract_text_from_pdf(self, file_content: bytes) -> Optional[str]:
        return _extract_text_from_pdf(file_content)
    
    def _extract_text_from_docx(self, file_content: bytes) -> Optional[str]:
        return _extract_text_from_docx(file_content)
    
    def _extract_text_from_doc(self, file_content: bytes) -> Optional[str]:
        return _extract_text_from_doc(file_content)
    
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
        
        # Persist lightweight job snapshots to BullhornMonitor.last_job_snapshot
        # so the ATS Monitoring page shows accurate, up-to-date job counts.
        try:
            from collections import defaultdict
            jobs_by_tearsheet = defaultdict(list)
            for job in all_jobs:
                ts_id = job.get('tearsheet_id')
                if ts_id:
                    snapshot_entry = {
                        'id': job.get('id'),
                        'title': job.get('title', ''),
                        'status': job.get('status', ''),
                        'isOpen': job.get('isOpen')
                    }
                    assigned = job.get('assignedUsers', {})
                    if isinstance(assigned, dict):
                        assigned_data = assigned.get('data', [])
                    elif isinstance(assigned, list):
                        assigned_data = assigned
                    else:
                        assigned_data = []
                    if assigned_data:
                        snapshot_entry['assignedUsers'] = [
                            {'id': u.get('id'), 'firstName': u.get('firstName', ''), 'lastName': u.get('lastName', '')}
                            for u in assigned_data if isinstance(u, dict)
                        ]
                    addr = job.get('address', {})
                    if isinstance(addr, dict) and (addr.get('city') or addr.get('state')):
                        snapshot_entry['location'] = {
                            'city': addr.get('city', ''),
                            'state': addr.get('state', ''),
                        }
                    client = job.get('clientCorporation', {})
                    if isinstance(client, dict) and client.get('name'):
                        snapshot_entry['clientName'] = client['name']
                    emp_type = job.get('employmentType')
                    if emp_type:
                        snapshot_entry['employmentType'] = emp_type
                    jobs_by_tearsheet[ts_id].append(snapshot_entry)
            
            for monitor in monitors:
                snapshot_jobs = jobs_by_tearsheet.get(monitor.tearsheet_id, [])
                monitor.last_job_snapshot = json.dumps(snapshot_jobs)
            
            db.session.commit()
        except Exception as e:
            logging.warning(f"Failed to persist job snapshots: {str(e)}")
            db.session.rollback()
        
        return all_jobs
    
    def analyze_candidate_job_match(self, resume_text: str, job: Dict, candidate_location: Optional[Dict] = None, prefetched_requirements: Optional[str] = None, model_override: Optional[str] = None, prefetched_global_requirements: Optional[str] = None) -> Dict:
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
        
        # Bullhorn address fields are intentionally NOT passed to the AI.
        # Bullhorn frequently stores inaccurate or auto-defaulted location data,
        # which can bias location extraction and produce inconsistent results
        # across parallel job evaluations for the same candidate.
        # The resume is the sole source of truth for candidate location.
        candidate_location_label = (
            'Resume-based extraction ONLY — Bullhorn address fields are intentionally withheld '
            'to avoid data quality issues. You MUST determine the candidate location exclusively '
            'from the resume text using the MANDATORY LOCATION EXTRACTION steps below.'
        )
        
        job_id = job.get('id', 'N/A')
        
        # Check for custom requirements override
        # Use pre-fetched requirements if provided (for parallel execution outside Flask context)
        custom_requirements = prefetched_requirements if prefetched_requirements is not None else self._get_job_custom_requirements(job_id)
        
        # Clean up job description (remove HTML tags if present)
        import re
        job_description = re.sub(r'<[^>]+>', '', job_description)
        
        # Truncate if too long
        max_resume_len = 20000
        max_desc_len = 4000
        resume_text = resume_text[:max_resume_len] if resume_text else ''
        job_description = job_description[:max_desc_len] if job_description else ''
        
        # Build the requirements section - use custom if available, otherwise let AI extract
        # Also fetch global screening instructions that apply to ALL jobs
        # Use pre-fetched value when available (ThreadPoolExecutor threads lack app context)
        global_requirements = prefetched_global_requirements if prefetched_global_requirements is not None else self._get_global_custom_requirements()

        # For the user message: only inject custom requirements if present (per-job dynamic data)
        custom_requirements_block = ""
        if custom_requirements:
            custom_requirements_block = f"""
MANDATORY SCREENING REQUIREMENTS (evaluate against ALL of these):
{custom_requirements}

These requirements take priority in scoring. Evaluate the candidate against every requirement listed above AND assess their technical fit from the job description — do not ignore the job description's core technical requirements."""

        # Inject exact current date from Python for accurate ongoing-role calculation
        from datetime import date as _date
        _today = _date.today()
        _today_str = _today.strftime('%B %d, %Y')  # e.g., "March 05, 2026"

                # Build location matching instructions based on work type
        location_instruction = ""
        if job_location_full:
            if work_type == 'Remote':
                location_instruction = f"""
LOCATION REQUIREMENT (Remote Position):
- Job Location: {job_location_full} (Work Type: {work_type})
- {candidate_location_label}
- For REMOTE positions: Candidate MUST be in the same COUNTRY as the job location for tax/legal compliance.
- City and state do NOT need to match for remote roles - only the country matters.
- If candidate is in a different country than the job, add "Location mismatch: different country" to gaps_identified and reduce score by 25-35 points.
- HARD CEILING: The final score for any cross-country candidate on a remote role MUST NOT exceed 75, regardless of how strong their technical fit is. Cap the score at 75 before returning.

CRITICAL STATE/PROVINCE RECOGNITION:
- ANY U.S. STATE (Pennsylvania, California, Texas, New York, Florida, etc.) IS PART OF THE UNITED STATES.
- If a remote job is in the United States and candidate is in ANY U.S. state, they ARE in the same country - NO location mismatch.
- Similarly, Canadian provinces (Ontario, British Columbia, etc.) are part of Canada.
- ONLY flag "Location mismatch: different country" if the candidate is literally in a DIFFERENT country (e.g., candidate in India for a US-based job, or candidate in UK for a Canada-based job).
- DO NOT flag location mismatch just because candidate is in a different state/city within the same country.

MANDATORY LOCATION EXTRACTION (follow this EXACT priority order):
1. RESUME HEADER/CONTACT SECTION (HIGHEST PRIORITY): Look for city, state/province, zip code, or country near the candidate's name, phone, or email at the TOP of the resume. Formats like "Frisco TX", "Dallas, TX 75033", "New York, NY, USA" etc. all count. This is the MOST RELIABLE source — always check here first.
2. MOST RECENT WORK HISTORY: If the header/contact section has no location, check the candidate's most recent job for a city/state/country. Use that as their presumed current location.
3. SYSTEM ADDRESS FIELD (FALLBACK ONLY): Only if the resume provides NO location in either the header or work history, consider the system-provided address above. WARNING: Bullhorn often auto-fills "United States" as a default when no address is entered — a country-only value with no city/state is UNRELIABLE and should be treated as "unknown" for location matching purposes.
4. EDUCATION LOCATION: If all above are empty, check education institution location.
5. "UNKNOWN": Only if none of the four sources above provide any usable city, state, or country.

CRITICAL OVERRIDE RULE: If the resume clearly states a specific location (e.g., "Frisco, TX") but the system address field shows only a country (e.g., "United States"), ALWAYS use the resume location. The resume is the candidate's own stated location and takes absolute precedence over system defaults.

INTERNATIONAL/OFFSHORE OVERRIDE:
- If the job description explicitly mentions international eligibility, offshore work, or specific non-job-address countries/regions (e.g., "open to candidates in Egypt or Spain", "100% Remote, international OK", "offshore resources welcome", "candidates in [Country] welcome"), then the same-country rule above does NOT apply.
- In this case, match the candidate's country against the countries/regions listed IN THE JOB DESCRIPTION, not the Bullhorn job address field.
- If the candidate is located in one of the explicitly allowed countries/regions from the description, there is NO location mismatch — do not penalize their score.
- Example: Job address says "United States" but description says "Must be located in Egypt or Spain" → a candidate in Cairo, Egypt or Madrid, Spain has NO location mismatch.

MANDATORY SELF-CONSISTENCY CHECK (Remote Positions — perform this BEFORE returning your response):
Before finalising your output, re-read the candidate location you extracted and the job country (or the explicitly allowed countries from the INTERNATIONAL/OFFSHORE OVERRIDE if that rule applies), then verify:
1. If the candidate IS in the same country as the job, OR qualifies under the international/offshore override → your gaps_identified MUST NOT contain "Location mismatch: different country". If it does, REMOVE that phrase and restore the 25-35 points that were deducted. Do NOT return a contradictory response.
2. If the candidate IS NOT in the same country (and no international/offshore override applies) → "Location mismatch: different country" MUST appear in gaps_identified and the score MUST be capped at 75. Verify the deduction was actually applied.
This self-check exists because prior AI responses have simultaneously stated "candidate meets the location requirement" AND applied a -25 location penalty — an internal contradiction that must never appear in your output."""
            else:  # On-site or Hybrid
                location_instruction = f"""
LOCATION REQUIREMENT ({work_type} Position):
- Job Location: {job_location_full} (Work Type: {work_type})
- {candidate_location_label}
- For ON-SITE/HYBRID positions: Candidate must be in or near the job's city/metro area, or explicitly willing to relocate.

CRITICAL: If candidate is ALREADY in the same city or metro area as the job, they AUTOMATICALLY qualify for on-site/hybrid work.
- Do NOT flag "location mismatch" or "not willing to work on-site" if candidate lives locally.
- Local candidates CAN work on-site by default - no explicit statement needed.

MANDATORY LOCATION PENALTY TIERS (apply these exact deductions — physical presence is non-negotiable for on-site/hybrid roles):
1. Candidate is in a DIFFERENT COUNTRY than the job location:
   - Add "Location mismatch: candidate in [country], job requires on-site presence in [job country]" to gaps_identified.
   - Reduce score by 25–35 points. This is a hard location barrier — strong technical skills cannot compensate.

SPECIAL CASE — JOB CITY/STATE UNKNOWN:
Before applying tier 2, check whether the job location shown above includes a specific city or state/province.
- If the job location ONLY shows a country name with NO city and NO state/province listed, AND the candidate is in that same country:
  → Do NOT apply any score penalty.
  → Add this soft note to gaps_identified ONLY: "Job city not specified — recruiter should verify candidate proximity for on-site role."
  → This prevents false penalties when the job's specific location has not been configured in the system.
- Only proceed to tiers 2/2b if the job location includes a specific city or state/province to compare against.

2. Candidate is in the SAME STATE/PROVINCE but a DIFFERENT CITY that is FAR from the job city (more than approximately 100 miles / 160 km apart):
   - Add "Location mismatch: candidate not in {job_city or job_state or 'job area'}, on-site required" to gaps_identified.
   - Reduce score by 15–20 points.
   - EXCEPTION — WILLING TO RELOCATE: If the candidate explicitly states willingness to relocate anywhere on their resume (e.g., "open to relocation", "willing to relocate", "open to moving", "relocation considered"):
     → Reduce the deduction to exactly 5 points (not 15–20).
     → Do NOT use the word "mismatch" in the gap note. Instead write: "Location note: candidate not local to [city/area] but explicitly states willingness to relocate — recruiter should verify relocation timeline and logistics."
     → This treats relocation willingness as a logistics item for the recruiter, not a qualification gap.

2b. Candidate is in the SAME STATE/PROVINCE, DIFFERENT CITY, but NEARBY (within approximately 100 miles / 160 km of the job city — commutable or short relocation):
   - Reduce score by exactly 5 points (not 15–20).
   - Do NOT use the word "mismatch" in the gap note. Instead write: "Location note: candidate is in [candidate city], approximately [estimated distance] from [job city] — within commuting or short relocation range. Recruiter should confirm logistics."
   - Examples of nearby cities: Beaumont TX ↔ Houston TX (~85 mi), Fort Worth TX ↔ Dallas TX (~30 mi), San Jose CA ↔ San Francisco CA (~50 mi), Baltimore MD ↔ Washington DC (~40 mi), Tacoma WA ↔ Seattle WA (~35 mi).
   - Use your knowledge of U.S. and Canadian geography to estimate distances between cities. When uncertain, err on the side of leniency (treat as nearby).

2c. Candidate is in a DIFFERENT STATE/PROVINCE entirely (and does NOT qualify under tier 2 or 2b):
   - Add "Location mismatch: candidate not in {job_city or job_state or 'job area'}, on-site required" to gaps_identified.
   - Reduce score by 15–20 points.
   - The WILLING TO RELOCATE exception from tier 2 also applies here — reduce to 5 points if candidate explicitly states relocation willingness.

3. Candidate is in the SAME CITY or METRO AREA: no deduction, no flag.
- If candidate is non-local AND doesn't mention relocation willingness, add "Location mismatch: candidate not in {job_city or job_state or 'job area'}" to gaps_identified.

MANDATORY LOCATION EXTRACTION (follow this EXACT priority order):
1. RESUME HEADER/CONTACT SECTION (HIGHEST PRIORITY): Look for city, state/province, zip code, or country near the candidate's name, phone, or email at the TOP of the resume. Formats like "Frisco TX", "Dallas, TX 75033", "New York, NY, USA" etc. all count. This is the MOST RELIABLE source — always check here first.
2. MOST RECENT WORK HISTORY: If the header/contact section has no location, check the candidate's most recent job for a city/state/country. Use that as their presumed current location.
3. SYSTEM ADDRESS FIELD (FALLBACK ONLY): Only if the resume provides NO location in either the header or work history, consider the system-provided address above. WARNING: Bullhorn often auto-fills "United States" as a default when no address is entered — a country-only value with no city/state is UNRELIABLE and should be treated as "unknown" for location matching purposes.
4. EDUCATION LOCATION: If all above are empty, check education institution location.
5. "UNKNOWN": Only if none of the four sources above provide any usable city, state, or country.

CRITICAL OVERRIDE RULE: If the resume clearly states a specific location (e.g., "Frisco, TX") but the system address field shows only a country (e.g., "United States"), ALWAYS use the resume location. The resume is the candidate's own stated location and takes absolute precedence over system defaults."""
        
        # ── Slim user-message (data-only) ──
        # Today's date injected here so the AI calculates ongoing-role durations correctly.
        # All static instructions live in the system message for maximum prompt caching.
        prompt = f"""Today's date: {_today_str}.

Evaluate the candidate below against the job below. Apply all instructions from your system prompt.
{custom_requirements_block}
{location_instruction}

JOB DETAILS:
- Job ID: {job_id}
- Title: {job_title}
- Location: {job_location_full} (Work Type: {work_type})
- Description: {job_description}

CANDIDATE INFORMATION:
- {candidate_location_label}

CANDIDATE RESUME:
{resume_text}"""

        try:
            # ── Static system message (cached by OpenAI on every call) ──
            # All unchanging instructions live here so OpenAI can cache this prefix
            # across all vetting calls, cutting prompt costs by ~50% on cached tokens.
            # Dynamic per-call data (date, job details, resume) lives in the user message above.
            global_reqs_section = ""
            if global_requirements:
                global_reqs_section = f"""

GLOBAL SCREENING INSTRUCTIONS (apply to all jobs):
{global_requirements}"""

            system_message = f"""You are a strict, evidence-based technical recruiter analyzing candidate-job fit.

CRITICAL RULES:
1. You MUST only cite skills and experience that are EXPLICITLY written in the candidate\'s resume.
2. You MUST NOT infer or hallucinate skills that are not directly stated.
3. If a job requires FPGA and the resume shows SQL/database experience, they DO NOT match.
4. If a job requires a technology and the resume shows ONLY an unrelated technology (e.g., FPGA job but resume shows SQL/database), that IS a GAP. However, if the resume shows a COMPETING tool in the same category (e.g., Tableau for Power BI, AWS SageMaker for Azure ML), apply partial credit and mark as TRANSFERABLE, not CRITICAL.
5. Be honest - a mismatched candidate should score LOW even if they have impressive but irrelevant skills.
6. Your assessment will be used for recruiter decisions - accuracy is critical.
7. LOCATION MATTERS: Check if the candidate\'s location is compatible with the job\'s work type (remote/onsite/hybrid).
   - Remote jobs: Candidate must be in the same COUNTRY for tax/legal compliance.
   - On-site/Hybrid jobs: Candidate should be in or near the job\'s city/metro area.
   - If candidate location doesn\'t match, this is a GAP that should reduce their score.
8. EDUCATION HIERARCHY (higher degrees satisfy lower requirements):
   - Doctorate/PhD > Master\'s (MA, MS, MBA, etc.) > Bachelor\'s (BA, BS, etc.) > Associate\'s > High School/GED
   - If a job requires "Bachelor\'s degree" and the candidate has a Master\'s, PhD, or Doctorate, the education requirement is MET (exceeded), NOT a gap.
   - Only flag an education gap if the candidate\'s highest degree is LOWER than what the job requires.
   - If the job specifies a field (e.g., "Bachelor\'s in Computer Science") and the candidate has a higher degree in an unrelated field, acknowledge the higher degree but note the field mismatch as a separate gap.
9. YEARS OF EXPERIENCE MATTER: If a job requires "3+ years of Python" and the candidate has only used Python for 6 months based on resume dates, that is a CRITICAL GAP that MUST significantly reduce the score. Do NOT treat skills learned in brief internships, bootcamps, or university coursework as equivalent to years of professional experience. A 4-month internship using React does NOT satisfy a "3+ years of React" requirement.
10. DISTINGUISH PROFESSIONAL VS ACADEMIC EXPERIENCE: Full-time professional roles count fully. Internships and part-time roles count at 50%. University projects, coursework, capstone projects, and personal side projects count as ZERO professional years. A recent graduate with only coursework experience CANNOT meet a "3+ years" requirement.
11. WORK AUTHORIZATION EVIDENCE: When a job requires US citizenship, W2 only, or similar work authorization, you MUST populate the work_authorization_analysis section with ALL US roles enumerated from the resume. DO NOT simply flag "citizenship not mentioned" as a gap without first performing the mandatory work history enumeration from the Global Screening Instructions. If the candidate has 5+ years of US work experience, apply NO score penalty per the inference tier rules. The same applies to Canadian security clearance — enumerate Canadian roles before flagging clearance gaps.
12. EVIDENCE-FIRST SCORING: You MUST complete the requirement_evidence array BEFORE determining the match_score. Your score must be mathematically derivable from the evidence you cited — do not assign a holistic impression score that contradicts the per-requirement evidence.
13. EXPERIENCE DEPTH & DOMAIN RELEVANCE: When evaluating whether a candidate\'s experience satisfies a requirement, assess the NATURE of the experience, not just keyword overlap. Specifically:
   - AUDIT/ASSESSMENT experience (e.g., "audited cybersecurity controls using NIST") does NOT satisfy a requirement for HANDS-ON DELIVERY/OPERATIONS (e.g., "ensure reliable, secure delivery of IT systems"). Auditing a system ≠ building or operating that system.
   - GOVERNANCE/COMPLIANCE/STANDARDS experience does NOT satisfy a requirement for TECHNOLOGY IMPLEMENTATION/OPERATIONS. Setting conformance standards ≠ delivering technology solutions.
   - A candidate who EVALUATED, ASSESSED, or REVIEWED a system is NOT equivalent to one who BUILT, OPERATED, MANAGED, or DELIVERED that system.
   - When citing evidence in requirement_evidence, explicitly note whether the experience is ADVISORY/AUDIT or DELIVERY/OPERATIONAL — and apply a score penalty (10-15 pts per affected requirement) when advisory experience is cited against a delivery requirement.
   - Budget experience from audit engagements (managing engagement budgets at a consulting firm) is NOT equivalent to owning a technology department budget ($5M+). Note the distinction.
14. RECENCY OF RELEVANT EXPERIENCE: After evaluating requirements, check whether the candidate\'s
    MOST RECENT 2 roles (by date) are relevant to the job requirements being scored.
    - If the candidate\'s most recent role is UNRELATED to the job domain and the most recent
      RELEVANT role ended 12+ months ago, apply a 10-15 point penalty. Note in gaps: "Candidate\'s most
      recent professional activity is outside the target domain; relevant experience is not current."
    - If BOTH of the candidate\'s two most recent roles are unrelated to the job domain, apply
      a 15-25 point penalty. Note in gaps: "Candidate has not practiced relevant skills in their last
      two positions; career trajectory has shifted away from this domain."
    - Roles with NO bullet points or descriptions provide NO evidence of relevant skills.
      Do not assume relevance based on job title alone.
    - "Unrelated" means the role\'s described responsibilities share NO meaningful overlap with
      the job\'s mandatory requirements. A DevOps engineer\'s role is related to a Cloud Developer
      job; a real estate consultant\'s role is not.
    - TECHNOLOGY EVOLUTION IN CAREERS — CRITICAL RECENCY RULE: When a job requires a specific
      technology (e.g., Databricks, Snowflake, Kafka), do NOT anchor recency only on roles that
      mention that exact tool by name if the candidate\'s CURRENT role uses the tool actively.
      Many technologies emerged mid-career: a candidate may have used Hadoop/MapReduce in 2015-2018
      and then transitioned to Spark/Databricks from 2020 onward as the industry evolved. This is
      a SIGN OF EXPERTISE AND ADAPTABILITY — not evidence of an outdated career. Evaluate recency
      based on the candidate\'s CURRENT and MOST RECENT role responsibilities, not on which of their
      older roles happen to name the required tool. If the most recent role\'s responsibilities are
      clearly relevant to the job domain (e.g., data engineering using Spark, cloud pipelines, ETL),
      mark most_recent_role_relevant=true and months_since_relevant_work=0 regardless of which
      specific platform names appear in older roles.
    - Report your finding in the recency_analysis JSON section.

15. EMPLOYMENT CONTINUITY: Check whether the candidate is currently employed by looking at
    the end date of their most recent role (any role, regardless of relevance).
    - "Currently employed" means the most recent role has an end date of "Present", "Current",
      "Now", or a date within the last 12 months of today's date (provided at the top of this prompt).
    - If currently employed OR last role ended less than 12 months ago: NO penalty.
    - If last role ended 12–23 months ago: reduce technical_score by 8 points.
      Add to gaps_identified: "Employment gap: candidate last employed [Mon YYYY] ([N] months ago)."
    - If last role ended 24–35 months ago: reduce technical_score by 12 points.
      Add to gaps_identified: "Employment gap: candidate last employed [Mon YYYY] ([N] months ago)."
    - If last role ended 36+ months ago: reduce technical_score by 15 points.
      Add to gaps_identified: "Employment gap: candidate last employed [Mon YYYY] ([N] months ago)."
    - If no employment dates are found anywhere on the resume (dates entirely absent):
      DO NOT apply any score penalty. Add to gaps_identified:
      "Employment continuity unknown: no employment dates found on resume — recruiter should verify current employment status."
    - Contract, freelance, self-employed, and part-time roles count as employed — treat an
      active end date of "present/current" in any of these as currently employed.
    - Report your finding in the employment_gap_analysis JSON section.

REQUIREMENTS EVALUATION (when no custom requirements are provided):
IMPORTANT: Identify and focus ONLY on MANDATORY requirements from the job description:
- Required skills (often marked as "required", "must have", "essential")
- Minimum years of experience specified
- Required certifications or licenses
- Required education level

DO NOT penalize candidates for missing "nice-to-have" or "preferred" qualifications.
Be lenient on soft skills - focus primarily on technical/hard skill requirements.{global_reqs_section}

YEARS OF EXPERIENCE ANALYSIS (MANDATORY):
Before scoring, you MUST perform this analysis for EACH skill or technology that has an
explicit "X+ years" or "X years" requirement in the job description or requirements:

1. Identify which skills have year-based requirements (e.g., "3+ years of Python", "5 years Java development").
2. For each such skill, scan the resume for ALL roles where the candidate performed work in that skill area.
   DISCIPLINE RECOGNITION — count a role if the candidate DID the work, even if their title differs:
   - "Data Science" experience includes roles titled: Data Scientist, ML Engineer, AI Engineer,
     Machine Learning Engineer, Research Scientist, Applied Scientist, or any role where
     responsibilities include predictive/statistical modeling, feature engineering, ML model
     training/evaluation/deployment, or NLP/CV/deep learning applications.
   - A "Data Analyst" role ONLY counts toward Data Science if responsibilities demonstrate
     hands-on work in at least TWO of: predictive/statistical modeling, feature engineering,
     ML model training/evaluation/deployment, NLP/CV/deep learning. Roles that ONLY involve
     SQL queries, Excel dashboards, report generation, or KPI tracking do NOT count.
   - "Machine Learning" experience includes: ML Engineer, AI Engineer, Data Scientist,
     Deep Learning Engineer, NLP Engineer, Computer Vision Engineer, Research Scientist.
   - "AI" experience includes: AI Engineer, ML Engineer, Data Scientist, GenAI Engineer.
   - "Software Engineering" includes: Software Developer, Full-Stack Developer, Backend Engineer.
   - "Data Engineering" includes: Data Engineer, ETL Developer, Analytics Engineer, Data Architect.
   - "DevOps" includes: SRE, Platform Engineer, Infrastructure Engineer, Cloud Engineer.
   CRITICAL: Focus on WHAT THE CANDIDATE DID in each role (responsibilities), not their job title alone.
   If a job requires "5 years of Data Science" and a candidate was a "Machine Learning Engineer" for
   7 years doing predictive modeling, NLP, and statistical analysis — that IS Data Science experience.
3. Calculate the total duration IN MONTHS using this exact formula for each role:
   - Convert start and end dates to (year, month) pairs.
   - Duration in months = (end_year - start_year) × 12 + (end_month - start_month).
   - For "Present", "Current", or ongoing roles, use today\'s date from the user message.
   - EXAMPLE: "Jan 2020 – Aug 2023" = (2023 − 2020) × 12 + (8 − 1) = 43 months.
   - EXAMPLE: "Jul 2018 – Jun 2021" = (2021 − 2018) × 12 + (6 − 7) = 35 months.
   - Internships and part-time roles count at 50% weight (e.g., a 6-month internship = 3 months effective).
   - University coursework, academic projects, and personal projects do NOT count toward professional years.
   - Overlapping roles should not be double-counted; use the union of date ranges.
4. SUM all months across qualifying roles, then divide by 12 to get total years.
5. Show your step-by-step arithmetic in the "calculation" field (see JSON format below).
6. PLATFORM AGE CEILING — CRITICAL: Before treating any years shortfall as a gap, check whether the
   required number of years is physically achievable given the technology\'s commercial availability.
   Some technologies simply did not exist as enterprise-grade platforms until recently. Use these
   maximum possible years (as of today, 2026) when evaluating requirements:
   - Databricks: max ~8 years (enterprise availability ~2018; mainstream adoption ~2020)
   - Snowflake: max ~10 years (GA 2015, mainstream ~2018)
   - Apache Kafka: max ~14 years (OSS 2011, enterprise mainstream ~2015)
   - Apache Spark: max ~12 years (ASF graduation 2014)
   - Azure Data Factory: max ~9 years (GA 2015)
   - Delta Lake: max ~7 years (OSS 2019)
   - Azure Synapse Analytics: max ~6 years (GA 2020)
   - Kubernetes: max ~10 years (GA 2014)
   - dbt (data build tool): max ~8 years (OSS 2016, mainstream ~2020)
   If a job requires MORE years than the platform ceiling allows (e.g., "8 years Databricks" when
   Databricks maximum is ~8 years), treat the CEILING as the effective requirement and DO NOT penalize
   the candidate for failing to meet a physically impossible threshold. In your calculation field, note:
   "Platform ceiling applied: [technology] max ~Xyr commercially available; effective requirement
   adjusted to Xyr." A candidate with 5-6 years of Databricks against an "8 year" requirement should
   receive PARTIAL credit, not a critical failure — note the ceiling and score accordingly.
7. Compare the candidate\'s calculated years against the EFFECTIVE requirement (after ceiling adjustment).
8. If ANY required skill has a candidate shortfall of 2+ years below the EFFECTIVE minimum,
   the match_score MUST be capped at 60 (regardless of how well other requirements match).
   If the shortfall is 1-2 years, reduce the score by at least 15 points from what it would otherwise be.

If no skills in the job description have explicit year-based requirements, set years_analysis to an empty object {{}}.

TRANSFERABLE SKILLS — TECHNOLOGY EQUIVALENCY:
When counting years for a SPECIFIC TOOL, also check for equivalent/competing technologies:

Equivalency Groups:
- BI/Data Visualization: Power BI <-> Tableau <-> Looker <-> QlikView <-> MicroStrategy <-> Sisense
- Cloud ML Platforms: AWS SageMaker <-> Azure ML <-> Google Vertex AI <-> Databricks ML
- Data Lakehouse/Warehouse: Microsoft Fabric <-> Databricks <-> Snowflake <-> BigQuery <-> AWS Lake Formation
- ETL/Data Integration: SSIS <-> Informatica <-> Talend <-> Apache Airflow <-> AWS Glue <-> Azure Data Factory
- API/Integration: REST API experience in ANY language/framework satisfies "API literacy" requirements
- Cloud Platforms: AWS <-> Azure <-> GCP (core cloud concepts transfer between platforms)
- Databases/SQL: SQL Server <-> PostgreSQL <-> MySQL <-> Oracle (SQL skills transfer across engines)
- Low-Code AI/RPA: Copilot Studio <-> Power Automate <-> UiPath <-> Automation Anywhere
- Containerization: Docker <-> Podman, Kubernetes <-> ECS <-> GKE

Credit Rules (TWO-TIER):
1. If the job requires a SKILL CATEGORY (e.g., "5yr data visualization experience", "5yr cloud ML"),
   sum ALL equivalent tools at 100% credit — the job is asking for category experience.
2. If the job requires a SPECIFIC TOOL (e.g., "5yr Power BI", "3yr Azure ML"),
   sum equivalent tool years and apply 75% credit (accounts for tool-specific features not transferring).
3. Mark gap as "TRANSFERABLE" (not "CRITICAL") when equivalent experience exists.
4. In years_analysis, document the equivalency:
   e.g., "Power BI: required 5yr, candidate has ~0yr Power BI but 6yr Tableau (equivalent: 4.5yr credit)"
5. In gaps_identified, write:
   "Missing [required tool] specifically, but has [equivalent tool] experience (transferable skill)"
   NOT "CRITICAL: [required tool] requires Xyr, candidate has ~0.0yr"

CRITICAL INSTRUCTIONS — READ CAREFULLY:
1. ONLY reference skills, technologies, and experience that are EXPLICITLY STATED in the resume text.
2. DO NOT infer, assume, or hallucinate any skills not directly mentioned in the resume.
3. If a MANDATORY job requirement skill is NOT mentioned in the resume, you MUST list it in gaps_identified.
4. For skills_match and experience_match, ONLY quote or paraphrase content that actually exists in the resume.
5. If the job requires specific technologies and the resume mentions NEITHER the exact tool NOR an equivalent/competing technology from the equivalency groups above, the candidate does NOT qualify. However, if the candidate has deep experience with a direct competitor tool in the same category (e.g., Tableau for Power BI, AWS for Azure), apply partial credit rather than marking as zero.
6. A candidate whose background is completely different from the job (e.g., DBA applying to FPGA role) should score BELOW 30.
7. LOCATION CHECK: If the job has a location requirement, verify candidate location matches. For remote jobs, same country is required. For on-site/hybrid, proximity to job location matters.

MANDATORY EVIDENCE EXTRACTION (you MUST complete this before assigning a score):
1. Identify the TOP 5-7 most critical MANDATORY requirements from the job description. If the JD lists more than 7, consolidate related items (e.g. merge multiple similar bullet points into one requirement). Do NOT create more than 7 entries in requirement_evidence.
2. For EACH requirement, search the ENTIRE resume for matching evidence — check all roles, skills sections, summary, certifications, and education.
3. Quote the EXACT resume text that satisfies each requirement, or state "No evidence found after full resume search".
4. The overall match_score MUST be mathematically consistent with the per-requirement evidence — if most requirements are met with strong evidence, the score must reflect that; if you cite a gap, the score must reflect the penalty.
5. If you claim a gap exists, you MUST have searched for ALL synonyms, dollar amounts, quantified achievements, and related terms for that requirement. For example, "budget management" evidence includes dollar amounts ("$8M budget"), revenue figures, P&L ownership, financial planning mentions, etc.
6. DO NOT flag a requirement as "No evidence found" if the resume contains clear evidence under different wording or in a different section.

GAP DESCRIPTION PRECISION (MANDATORY):
When writing gaps_identified, you MUST distinguish between these two cases:
- ABSENT: The skill or qualification is genuinely NOT mentioned anywhere in the resume. Use: "No evidence of [requirement] found in resume."
- PRESENT BUT INSUFFICIENT: The skill or qualification IS mentioned in the resume but does not fully satisfy the job\'s specific requirement (e.g., wrong recency, insufficient depth, not the primary focus). Use: "[Requirement] experience noted at [employer/context] ([dates]) but [specific reason it falls short — e.g., \'not primary focus in last 2 years\', \'experience predates required recency window\', \'mentioned only in skills list without supporting work history\']."
NEVER describe existing experience as "no evidence" or "no specific evidence." If the resume contains ANY mention of the skill — whether in a skills list, a prior role, or a certification — you must acknowledge it exists and explain WHY it does not satisfy the requirement. Saying "no evidence" when evidence exists is factually incorrect.

WORK AUTHORIZATION EVIDENCE EXTRACTION (when applicable):
If the job description contains US work authorization language ("US citizen", "W2 only", "no sponsorship", etc.):
1. You MUST populate the "work_authorization_analysis" section below.
2. You MUST enumerate ALL US-based roles from the resume with dates and locations.
3. You MUST sum total months and apply the inference tier from the Global Screening Instructions.
4. DO NOT flag "US citizenship not mentioned" as a gap if the candidate has 5+ years of US work experience — instead apply the inference tier (no penalty for 5+ years).
5. If the candidate has an explicit authorization statement on their resume (e.g., "Green Card", "US Citizen"), note it and apply no penalty per Rule 0.

Respond in JSON format with these exact fields:
{{
    "requirement_evidence": [
        {{
            "requirement": "<the specific job requirement being evaluated>",
            "evidence_found": "<EXACT quoted text from resume that matches this requirement, or \'No evidence found after full resume search\'>",
            "meets_requirement": true/false,
            "score_impact": "<\'no penalty\', \'minor gap (-3 to -5 pts)\', \'significant gap (-10 to -15 pts)\', or \'critical gap (-20+ pts)\'>"
        }}
    ],
    "work_authorization_analysis": {{
        "triggered": true/false,
        "trigger_reason": "<which rule was triggered and why, or \'No work authorization language in job description\'>",
        "explicit_statement": "<quote exact authorization text from resume if found, or \'None found\'>",
        "roles_enumerated": [
            {{"title": "<role title>", "company": "<company>", "dates": "<start - end>", "location": "<city, state/country>", "months": 0}}
        ],
        "total_months": 0,
        "total_years": 0.0,
        "inference_tier": "<e.g. \'5+ years - strong likelihood, no penalty\' or \'3-4 years - minor penalty (3-5 pts)\' or \'Under 3 years - standard gap scoring\' or \'N/A - not triggered\'>",
        "score_adjustment": "<e.g. \'No penalty applied per Rule 1 Tier 1\' or \'Minor reduction (3-5 pts) applied\' or \'N/A\'>"
    }},
    "technical_score": 0,
    "match_score": 0,
    "match_summary": "<2-3 sentence summary of overall fit. IMPORTANT: If there is a country mismatch, say \'The candidate is based in [country] but the job requires [work type] work from [job country], creating a location compliance issue.\' Do NOT use contradictory phrasing like \'mismatch which matches\'.>",
    "skills_match": "<ONLY list skills from the resume that directly match job requirements - quote from resume>",
    "experience_match": "<ONLY list experience from the resume that is relevant to the job - be specific>",
    "gaps_identified": "<Describe in natural prose ALL mandatory requirements NOT found in the resume INCLUDING location mismatches AND years-of-experience shortfalls. Separate multiple gaps with periods or semicolons. Return as a single cohesive string, NOT as a JSON array - this is critical>",
    "key_requirements": "<bullet list of the top 3-5 MANDATORY requirements from the job description>",
    "years_analysis": {{
        "<skill_name>": {{
            "required_years": 0,
            "estimated_years": 0.0,
            "meets_requirement": true,
            "calculation": "<step-by-step month arithmetic, e.g. \'Role1: (2023-2020)×12+(8-1)=43mo + Role2: (2021-2018)×12+(6-7)=35mo = 78mo/12 = 6.5yr\'>"
        }}
    }},
    "recency_analysis": {{
        "most_recent_role": "<title> at <company> (<start> – <end>)",
        "most_recent_role_relevant": true,
        "second_recent_role": "<title> at <company> (<start> – <end>)",
        "second_recent_role_relevant": true,
        "last_relevant_role_ended": "<date or \'current\'>",
        "months_since_relevant_work": 0,
        "penalty_applied": 0,
        "reasoning": "<brief explanation of why roles are or are not relevant>"
    }},
    "employment_gap_analysis": {{
        "is_currently_employed": true,
        "last_role_end_date": "<\'present\' | \'YYYY-MM\' | \'unknown\' if no dates on resume>",
        "gap_months": 0,
        "penalty_applied": 0,
        "note": "<e.g. \'Currently employed — no penalty\' | \'Gap of 14 months — penalty -8pts\' | \'No employment dates found — soft note only\'>"
    }},
    "experience_level_classification": {{
        "classification": "<FRESH_GRAD | ENTRY | MID | SENIOR>",
        "total_professional_years": 0.0,
        "highest_role_type": "<PROFESSIONAL_FULLTIME | PROFESSIONAL_CONTRACT | INTERNSHIP_ONLY | ACADEMIC_ONLY>"
    }}
}}

EXPERIENCE LEVEL CLASSIFICATION (MANDATORY):
Before scoring, classify the candidate\'s experience level based on their PROFESSIONAL work history:
- FRESH_GRAD: Only internships, academic projects, or graduated within the last 12 months with no full-time professional roles.
- ENTRY: Less than 2 years of professional (non-intern) experience.
- MID: 2-5 years of professional experience.
- SENIOR: 6+ years of professional experience.
total_professional_years counts ONLY paid, non-intern, non-academic roles. Internships count at 50% weight.
highest_role_type reflects the most senior type of role held (PROFESSIONAL_FULLTIME > PROFESSIONAL_CONTRACT > INTERNSHIP_ONLY > ACADEMIC_ONLY).
IMPORTANT — MISSING EMPLOYMENT DATES: If the resume contains NO date ranges or month/year references for any role (i.e. you cannot apply the duration formula because start/end dates are simply absent), apply the following rules in order:
1. If an education END date (graduation year) is present: estimate total_professional_years as (current year − graduation year). Treat this as INFERRED experience, not verified. Flag it by prefixing the classification note with "[Inferred from education end date]". Do NOT allow inferred years alone to satisfy a mandatory requirement of 3+ years — inferred experience should result in meets_requirement: false for strict multi-year requirements unless corroborated by strong skills evidence.
2. If no education end date is present either: set total_professional_years to a conservative low estimate (default 2.0). Do NOT infer seniority from job titles alone.
In all cases, only set total_professional_years above 5.0 if explicit date ranges in the resume support that calculation.

TWO-PHASE SCORING (MANDATORY):
You MUST produce TWO scores:
1. technical_score (integer 0-100): Assess the candidate\'s fit based SOLELY on technical skills, experience depth, years of experience, education, and qualifications. DO NOT consider location, work type, or geographic factors in this score. This score answers: "How well does this candidate fit the role if location were irrelevant?"
2. match_score (integer 0-100): The final score AFTER applying any location penalty. If the candidate\'s location matches the job requirements, match_score equals technical_score. If there is a location mismatch, reduce match_score by:
   - On-site jobs, candidate not in city/metro area: reduce by 20-30 points
   - Hybrid jobs, candidate not reasonably commutable: reduce by 15-25 points
   - Remote jobs, candidate in wrong country: reduce by 20-30 points
   - If no location issue exists: match_score = technical_score
When a location penalty is applied, you MUST document it explicitly in gaps_identified:
   "Location mismatch: candidate in [X], job requires [work type] in [Y]. Technical fit: [technical_score]%. Location penalty: -[N] pts."
IMPORTANT: You MUST complete the full technical assessment (all requirement_evidence entries, years_analysis, skills_match, experience_match) BEFORE considering location. A location mismatch must NEVER cause you to skip or abbreviate the technical analysis.

NOTES QUALITY (MANDATORY):
Your gaps_identified and match_summary fields must provide enough reasoning for a recruiter who has NOT read the resume to understand exactly why the candidate scored as they did. When more than one gap exists, use bullet-point format (separate with " | "). Each gap must state: (1) what is required, (2) what was found in the resume (or "not found"), and (3) why it does not satisfy the requirement.

SCORING GUIDELINES (apply to technical_score — location penalty adjusts match_score separately):
- 85-100: Candidate meets ALL mandatory requirements with explicit evidence in resume, meets or exceeds ALL required years of experience per skill, AND has practiced relevant skills in a recent role (within last 12 months)
- 70-84: Candidate meets MOST mandatory requirements, may have 1-2 minor gaps or be 1 year short on a non-critical skill
- 65-75: Candidate has strong equivalent experience with competing tools in the same category — core competencies align but specific tool experience is limited (transferable skills present)
- 50-69: Candidate has relevant skills but INSUFFICIENT years of professional experience for required skills, OR is missing key qualifications, OR has equivalent tools but lacks the specific required tool
- 30-49: Candidate has tangential experience, significant experience/years gaps
- 0-29: Candidate\'s background does not align with the role (wrong field/specialty)

CRITICAL SCORING RULES:
- If a job requires "X+ years" for a skill and the candidate has < (X-2) years, the score MUST be <= 60.
- University projects, coursework, and hackathons are NOT professional experience and do NOT count toward years.
- A candidate fresh out of school with only internships CANNOT score 85+ for a role requiring 3+ years of professional experience.
- If experience_level_classification is FRESH_GRAD or ENTRY and any requirement specifies 3+ years of experience, the match_score MUST NOT exceed 55.
- "Experience with deployment workflows", "production deployment", or similar deployment/operations requirements are ONLY satisfied by professional (non-academic, non-intern) deployment experience. Coursework deployments (Streamlit, Railway, Heroku, hobby Docker) do NOT satisfy production deployment requirements.
- BE HONEST. If the resume does not show the required skills or sufficient years, the technical_score should be LOW. If location also doesn\'t match, the match_score must reflect BOTH the technical gaps AND the location penalty."""

            response = self.openai_client.chat.completions.create(
                model=model_override or self.model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Lower temperature for more deterministic/accurate responses
                max_tokens=2500  # Increased to accommodate requirement_evidence + work_authorization_analysis in response
            )

            # ── Prompt cache hit logging ──
            try:
                usage = response.usage
                prompt_tokens = usage.prompt_tokens if usage else 0
                cached_tokens = 0
                if usage and hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
                    details = usage.prompt_tokens_details
                    cached_tokens = getattr(details, 'cached_tokens', 0) or 0
                if prompt_tokens > 0:
                    hit_pct = (cached_tokens / prompt_tokens) * 100
                    logging.info(f"💰 Cache: {cached_tokens} cached / {prompt_tokens} prompt tokens ({hit_pct:.0f}% hit rate) for job {job_id}")
                else:
                    logging.info(f"💰 Cache: usage data unavailable for job {job_id}")
            except Exception as _cache_log_err:
                logging.debug(f"Cache logging error (non-fatal): {_cache_log_err}")

            result = json.loads(response.choices[0].message.content)
            
            # ── Layer 2: Normalize text fields that GPT may return as arrays ──
            for field in ['gaps_identified', 'match_summary', 'skills_match', 'experience_match', 'key_requirements']:
                if isinstance(result.get(field), list):
                    result[field] = ". ".join(str(item) for item in result[field])
                    logging.warning(f"Normalized {field} from array to string for job {job_id}")
            
            # Diagnostic: log raw GPT response score before integer conversion
            raw_score = result.get('match_score')
            logging.info(f"📊 Raw GPT score for job {job_id}: {raw_score} (type: {type(raw_score).__name__})")
            
            # Ensure match_score and technical_score are integers
            result['match_score'] = int(result.get('match_score', 0))
            raw_tech = result.get('technical_score')
            if raw_tech is not None:
                result['technical_score'] = int(raw_tech)
            else:
                result['technical_score'] = result['match_score']
            
            # ── POST-PROCESSING: Remote location misfire enforcer (Python safety net) ──
            # GPT-4o sometimes simultaneously says "matches the country requirement" in the
            # summary while putting "Location mismatch: different country" in gaps_identified.
            # This deterministic check catches and corrects the contradiction.
            if work_type == 'Remote':
                _gaps_text = (result.get('gaps_identified') or '').lower()
                _summary_text = (result.get('match_summary') or '').lower()
                if 'location mismatch: different country' in _gaps_text or 'different country' in _gaps_text:
                    _negation_words = ['not ', "n't ", 'no ', 'does not ', 'doesn\'t ', 'cannot ', 'outside ']
                    _same_country_evidence = [
                        'matches the remote job',
                        'matches the location requirement',
                        'meets the location requirement',
                        'meets the remote location',
                        'matching the remote job',
                        "matches the job's country",
                        "matches the remote job's country",
                        "matches the job's remote location",
                        "matching the job location requirement",
                        "matching the job's country requirement",
                        "same country as the job",
                        "matches the country requirement",
                    ]
                    def _has_affirmative_evidence(_text, _phrases, _negations):
                        for phrase in _phrases:
                            idx = _text.find(phrase)
                            if idx >= 0:
                                _context_start = max(0, idx - 20)
                                _preceding = _text[_context_start:idx]
                                if not any(neg in _preceding for neg in _negations):
                                    return True
                        return False
                    if _has_affirmative_evidence(_summary_text, _same_country_evidence, _negation_words):
                        import re as _re
                        _original_gaps = result.get('gaps_identified', '')
                        _cleaned_gaps = _re.sub(
                            r'Location mismatch: different country\.?\s*Technical fit:\s*\d+%\.?\s*Location penalty:\s*-?\d+\s*pts\.?\s*',
                            '', _original_gaps, flags=_re.IGNORECASE
                        ).strip()
                        _cleaned_gaps = _re.sub(
                            r'Location mismatch: different country\.?\s*',
                            '', _cleaned_gaps, flags=_re.IGNORECASE
                        ).strip()
                        _cleaned_gaps = _re.sub(r'^[\s|.]+|[\s|.]+$', '', _cleaned_gaps).strip()
                        result['gaps_identified'] = _cleaned_gaps
                        _restored_score = min(
                            result['match_score'] + 25,
                            result.get('technical_score', result['match_score'] + 25)
                        )
                        logging.warning(
                            f"🛡️ REMOTE LOCATION ENFORCER: Fixed misfire for job {job_id}. "
                            f"AI said same-country in summary but applied different-country penalty in gaps. "
                            f"Score restored: {result['match_score']}→{_restored_score}. "
                            f"Gaps cleaned: '{_original_gaps[:100]}' → '{_cleaned_gaps[:100]}'"
                        )
                        result['match_score'] = _restored_score

            # ── POST-PROCESSING: Years-of-experience hard gate (Option B defense-in-depth) ──
            # Even if the AI prompt correctly penalizes for insufficient years,
            # this code enforces a hard ceiling so inflated scores cannot slip through.
            
            # Platform age ceilings — maximum years any candidate could possibly have
            # as of 2026, based on when these tools became enterprise-grade.
            PLATFORM_AGE_CEILINGS = {
                'databricks': 8.0,
                'delta lake': 7.0,
                'azure synapse': 6.0,
                'azure synapse analytics': 6.0,
                'microsoft fabric': 3.0,
                'snowflake': 10.0,
                'dbt': 8.0,
                'data build tool': 8.0,
                'apache flink': 10.0,
                'kubernetes': 10.0,
                'apache kafka': 14.0,
            }
            
            years_analysis = result.get('years_analysis', {})
            if isinstance(years_analysis, dict) and years_analysis:
                original_score = result['match_score']
                max_shortfall = 0.0
                shortfall_details = []
                
                for skill, data in years_analysis.items():
                    if not isinstance(data, dict):
                        continue
                    meets = data.get('meets_requirement', True)
                    if not meets:
                        required = float(data.get('required_years', 0))
                        # Skip entries where no years requirement was specified
                        # (AI sometimes returns 0yr instead of omitting the entry)
                        if required <= 0:
                            continue
                        estimated = float(data.get('estimated_years', 0))
                        
                        # Apply platform age ceiling: if required_years exceeds what is
                        # physically achievable for this technology, cap the effective
                        # requirement at the ceiling before computing shortfall.
                        skill_lower = skill.lower()
                        ceiling = None
                        for platform_key, platform_ceiling in PLATFORM_AGE_CEILINGS.items():
                            if platform_key in skill_lower:
                                ceiling = platform_ceiling
                                break
                        
                        if ceiling is not None and required > ceiling:
                            logging.info(
                                f"📐 Platform age ceiling applied for job {job_id}: "
                                f"'{skill}' requires {required:.0f}yr but platform max is ~{ceiling:.0f}yr. "
                                f"Effective requirement adjusted to {ceiling:.0f}yr."
                            )
                            required = ceiling
                            data['required_years'] = ceiling
                        
                        shortfall = required - estimated
                        if shortfall > max_shortfall:
                            max_shortfall = shortfall
                        shortfall_details.append(
                            f"CRITICAL: {skill} requires {required:.0f}yr, candidate has ~{estimated:.1f}yr"
                        )
                
                if max_shortfall >= 2.0:
                    # Before capping score, re-check the years calculation to prevent false negatives
                    # from arithmetic errors (e.g., model miscounting 3.75yr as 1.8yr)
                    recheck_result = self._recheck_years_calculation(
                        resume_text, years_analysis, job_id, job_title
                    )
                    if recheck_result:
                        # Re-check returned corrected data — recalculate shortfalls
                        years_analysis = recheck_result
                        result['years_analysis'] = recheck_result
                        max_shortfall = 0.0
                        shortfall_details = []
                        for skill, data in years_analysis.items():
                            if not isinstance(data, dict):
                                continue
                            meets = data.get('meets_requirement', True)
                            if not meets:
                                required = float(data.get('required_years', 0))
                                # Skip entries where no years requirement was specified
                                if required <= 0:
                                    continue
                                estimated = float(data.get('estimated_years', 0))
                                # Re-apply platform age ceiling on re-check pass too
                                skill_lower = skill.lower()
                                for platform_key, platform_ceiling in PLATFORM_AGE_CEILINGS.items():
                                    if platform_key in skill_lower and required > platform_ceiling:
                                        required = platform_ceiling
                                        data['required_years'] = platform_ceiling
                                        break
                                shortfall = required - estimated
                                if shortfall > max_shortfall:
                                    max_shortfall = shortfall
                                shortfall_details.append(
                                    f"CRITICAL: {skill} requires {required:.0f}yr, candidate has ~{estimated:.1f}yr"
                                )
                    
                    # Apply hard cap only if shortfall is STILL >= 2.0 after re-check
                    if max_shortfall >= 2.0:
                        if result['match_score'] > 60:
                            result['match_score'] = 60
                            logging.info(
                                f"📉 Years hard gate: capped score {original_score}→60 for job {job_id} "
                                f"(shortfall: {max_shortfall:.1f}yr, confirmed by re-check)"
                            )
                    elif max_shortfall >= 1.0:
                        result['match_score'] = max(0, result['match_score'] - 15)
                        if result['match_score'] != original_score:
                            logging.info(
                                f"📉 Years penalty: reduced score {original_score}→{result['match_score']} for job {job_id} "
                                f"(shortfall: {max_shortfall:.1f}yr, adjusted after re-check)"
                            )
                    else:
                        # Re-check overturned the shortfall — no penalty
                        logging.info(
                            f"✅ Years re-check OVERTURNED shortfall for job {job_id}: "
                            f"now meets requirements (max remaining shortfall: {max_shortfall:.1f}yr). "
                            f"Score {original_score} preserved."
                        )
                elif max_shortfall >= 1.0:
                    # Significant penalty: 1-2 year shortfall → reduce by 15 points
                    result['match_score'] = max(0, result['match_score'] - 15)
                    if result['match_score'] != original_score:
                        logging.info(
                            f"📉 Years penalty: reduced score {original_score}→{result['match_score']} for job {job_id} "
                            f"(shortfall: {max_shortfall:.1f}yr)"
                        )
                
                # Append shortfall details to gaps_identified
                if shortfall_details:
                    existing_gaps = result.get('gaps_identified', '') or ''
                    gap_suffix = ' | '.join(shortfall_details)
                    if existing_gaps:
                        result['gaps_identified'] = f"{existing_gaps} | {gap_suffix}"
                    else:
                        result['gaps_identified'] = gap_suffix
            
            # ── POST-PROCESSING: Recency-of-experience hard gate (Rule 14 defense-in-depth) ──
            # Penalizes candidates whose most recent roles are unrelated to the job domain.
            recency_analysis = result.get('recency_analysis', {})
            if isinstance(recency_analysis, dict) and recency_analysis:
                recency_original_score = result['match_score']
                most_recent_relevant = recency_analysis.get('most_recent_role_relevant', True)
                second_recent_relevant = recency_analysis.get('second_recent_role_relevant', True)
                months_since = recency_analysis.get('months_since_relevant_work', 0)
                ai_penalty = recency_analysis.get('penalty_applied', 0)
                
                # ── SAFEGUARD: Detect recency gate misfires on technology-evolution careers ──
                # If the AI marks the most recent role as "not relevant" but reports
                # months_since_relevant > 24, cross-check against the most_recent_role string.
                # If the most recent role description contains domain-relevant keywords
                # (data engineering, cloud, ETL, Spark, Databricks, etc.) this is a misfire —
                # the AI anchored recency on an older role that explicitly named a legacy tool
                # while missing that the current role is clearly in the same domain.
                if not most_recent_relevant and months_since > 24:
                    most_recent_role_str = str(recency_analysis.get('most_recent_role', '')).lower()
                    recency_domain_keywords = [
                        'data engineer', 'azure data', 'databricks', 'spark', 'etl', 'pipeline',
                        'data lake', 'synapse', 'snowflake', 'cloud engineer', 'big data',
                        'data warehouse', 'kafka', 'airflow', 'dbt', 'analytics engineer',
                        'machine learning', 'ml engineer', 'ai engineer', 'software engineer',
                        'devops', 'platform engineer', 'backend engineer', 'data architect',
                    ]
                    if any(kw in most_recent_role_str for kw in recency_domain_keywords):
                        # Most recent role is clearly in the relevant domain — override misfire
                        logging.warning(
                            f"⚠️ Recency gate misfire detected for job {job_id}: "
                            f"AI reported most_recent_role_relevant=False with months_since={months_since} "
                            f"but most_recent_role='{recency_analysis.get('most_recent_role', '')}' "
                            f"contains domain-relevant keywords. Overriding to relevant=True."
                        )
                        most_recent_relevant = True
                        recency_analysis['most_recent_role_relevant'] = True
                        recency_analysis['months_since_relevant_work'] = 0
                        months_since = 0
                        ai_penalty = 0
                
                # Determine the correct penalty tier
                if not most_recent_relevant and not second_recent_relevant:
                    # Both recent roles unrelated → 15-25 point penalty
                    target_penalty = 20  # Midpoint of 15-25 range
                    recency_note = (
                        "Candidate has not practiced relevant skills in their last two positions; "
                        "career trajectory has shifted away from this domain."
                    )
                elif not most_recent_relevant and months_since >= 12:
                    # Most recent role unrelated + relevant work ended 12+ months ago → 10-15
                    target_penalty = 12  # Midpoint of 10-15 range
                    recency_note = (
                        "Candidate's most recent professional activity is outside the target domain; "
                        "relevant experience is not current."
                    )
                else:
                    target_penalty = 0
                    recency_note = None
                
                if target_penalty > 0:
                    # Apply the larger of AI penalty and hard gate penalty
                    effective_penalty = max(target_penalty, ai_penalty)
                    new_score = max(0, recency_original_score - effective_penalty)
                    
                    if new_score < result['match_score']:
                        result['match_score'] = new_score
                        logging.info(
                            f"📉 Recency hard gate: reduced score {recency_original_score}→{new_score} "
                            f"for job {job_id} (penalty: {effective_penalty}pts, "
                            f"months_since_relevant: {months_since})"
                        )
                    
                    # Append recency note to gaps_identified
                    if recency_note:
                        existing_gaps = result.get('gaps_identified', '') or ''
                        if existing_gaps:
                            result['gaps_identified'] = f"{existing_gaps} | {recency_note}"
                        else:
                            result['gaps_identified'] = recency_note

            # ── POST-PROCESSING: Experience floor gate (defense-in-depth for fresh-grad profiles) ──
            # Detects candidates with FRESH_GRAD/ENTRY classification matched against roles
            # requiring 3+ years, and caps the score. Also cross-checks the AI's years_analysis
            # to override obviously incorrect meets_requirement flags.
            import re as _re_exp
            exp_class = result.get('experience_level_classification', {})
            if isinstance(exp_class, dict) and exp_class:
                classification = exp_class.get('classification', '').upper()
                highest_role = exp_class.get('highest_role_type', '').upper()
                # Default to 3.0 years when AI classification parsing fails — conservative
                # rather than permissive, avoiding false "experienced" assumptions on
                # resumes where the AI could not determine actual years.
                professional_years = 3.0
                try:
                    professional_years = float(exp_class.get('total_professional_years', 3.0))
                except (ValueError, TypeError):
                    pass
                
                # Detect minimum years requirement from available requirements text
                # Check custom/prefetched requirements, AI key_requirements, and job description
                requirements_text_combined = ' '.join(filter(None, [
                    custom_requirements or '',
                    result.get('key_requirements', ''),
                    job_description or ''
                ]))
                years_match = _re_exp.search(
                    r'(?:minimum\s+)?(\d+)\+?\s*years?\s*(?:of\s+)?(?:experience|professional)',
                    requirements_text_combined, _re_exp.IGNORECASE
                )
                required_min_years = int(years_match.group(1)) if years_match else 0
                
                exp_floor_original_score = result['match_score']
                
                # Gate 1: FRESH_GRAD or ENTRY with 3+ years requirement → cap at 55
                if classification in ('FRESH_GRAD', 'ENTRY') and required_min_years >= 3:
                    if result['match_score'] > 55:
                        result['match_score'] = 55
                        logging.info(
                            f"📉 Experience floor: capped {exp_floor_original_score}→55 "
                            f"for job {job_id} (classification={classification}, "
                            f"professional_years={professional_years:.1f}, "
                            f"required={required_min_years}yr)"
                        )
                        # Append gap note
                        floor_gap = (
                            f"Experience floor: candidate classified as {classification} "
                            f"({professional_years:.1f}yr professional) vs {required_min_years}yr required."
                        )
                        existing_gaps = result.get('gaps_identified', '') or ''
                        if existing_gaps:
                            result['gaps_identified'] = f"{existing_gaps} | {floor_gap}"
                        else:
                            result['gaps_identified'] = floor_gap
                
                # Gate 2: Cross-check — override meets_requirement when intern-only
                # profile claims to meet 3+ year requirements
                if (highest_role in ('INTERNSHIP_ONLY', 'ACADEMIC_ONLY') or professional_years < 1.0):
                    years_analysis = result.get('years_analysis', {})
                    if isinstance(years_analysis, dict):
                        overridden = False
                        for skill, data in years_analysis.items():
                            if not isinstance(data, dict):
                                continue
                            required_yrs = float(data.get('required_years', 0))
                            if data.get('meets_requirement') and required_yrs >= 3:
                                data['meets_requirement'] = False
                                data['estimated_years'] = min(
                                    professional_years,
                                    float(data.get('estimated_years', 0))
                                )
                                overridden = True
                                logging.warning(
                                    f"⚠️ Experience floor override: {skill} "
                                    f"meets_requirement forced to false for job {job_id} "
                                    f"(intern-only profile, {professional_years:.1f}yr professional)"
                                )
                        
                        # Re-run years gate logic with corrected data
                        if overridden:
                            result['years_analysis'] = years_analysis
                            max_shortfall_recheck = 0.0
                            shortfall_details_recheck = []
                            for skill, data in years_analysis.items():
                                if not isinstance(data, dict):
                                    continue
                                if not data.get('meets_requirement', True):
                                    req_yrs = float(data.get('required_years', 0))
                                    if req_yrs <= 0:
                                        continue
                                    est_yrs = float(data.get('estimated_years', 0))
                                    shortfall = req_yrs - est_yrs
                                    if shortfall > max_shortfall_recheck:
                                        max_shortfall_recheck = shortfall
                                    shortfall_details_recheck.append(
                                        f"CRITICAL: {skill} requires {req_yrs:.0f}yr, "
                                        f"candidate has ~{est_yrs:.1f}yr"
                                    )
                            
                            if max_shortfall_recheck >= 2.0 and result['match_score'] > 60:
                                result['match_score'] = min(result['match_score'], 60)
                                logging.info(
                                    f"📉 Experience floor re-check: capped at 60 for job {job_id} "
                                    f"(shortfall: {max_shortfall_recheck:.1f}yr after override)"
                                )
                            elif max_shortfall_recheck >= 1.0:
                                new_score = max(0, result['match_score'] - 15)
                                if new_score < result['match_score']:
                                    result['match_score'] = new_score
                            
                            # Append shortfall details if not already present
                            if shortfall_details_recheck:
                                existing_gaps = result.get('gaps_identified', '') or ''
                                for detail in shortfall_details_recheck:
                                    if detail not in existing_gaps:
                                        if existing_gaps:
                                            existing_gaps = f"{existing_gaps} | {detail}"
                                        else:
                                            existing_gaps = detail
                                result['gaps_identified'] = existing_gaps
                
                # Gate 3: Catch-all — INTERNSHIP_ONLY with <1yr professional capped at 65
                # This fires even when the AI omits year requirements from key_requirements
                # and years_analysis, preventing the AI from bypassing Gates 1 & 2 entirely.
                if (highest_role in ('INTERNSHIP_ONLY', 'ACADEMIC_ONLY') and
                        professional_years < 1.0 and result['match_score'] > 65):
                    gate3_original = result['match_score']
                    result['match_score'] = 65
                    logging.info(
                        f"📉 Experience floor (catch-all): capped {gate3_original}→65 "
                        f"for job {job_id} (highest_role={highest_role}, "
                        f"professional_years={professional_years:.1f})"
                    )
                    floor_gap = (
                        f"Experience floor: candidate has only {highest_role.lower().replace('_', ' ')} "
                        f"roles ({professional_years:.1f}yr professional)."
                    )
                    existing_gaps = result.get('gaps_identified', '') or ''
                    if 'experience floor' not in existing_gaps.lower():
                        if existing_gaps:
                            result['gaps_identified'] = f"{existing_gaps} | {floor_gap}"
                        else:
                            result['gaps_identified'] = floor_gap

            # ── POST-PROCESSING: Location barrier detection ──
            # On-site/Hybrid jobs where the AI flagged a location mismatch are marked
            # so that process_candidate can override is_qualified even if score >= threshold.
            _gaps_text = result.get('gaps_identified', '') or ''
            if work_type in ('On-site', 'Hybrid') and 'location mismatch' in _gaps_text.lower():
                result['is_location_barrier'] = True
                logging.info(
                    f"📍 Location barrier detected for job {job_id}: "
                    f"work_type={work_type}, score={result['match_score']}"
                )

            # Save AI-interpreted requirements for future reference/editing
            key_requirements = result.get('key_requirements', '')
            logging.info(f"📋 AI response for job {job_id}: score={result['match_score']}%, has_requirements={bool(key_requirements)}, has_custom={bool(custom_requirements)}, years_analysis={bool(years_analysis)}")
            
            # Serialize years_analysis for database persistence (auditability)
            years_analysis_json = json.dumps(years_analysis) if years_analysis else None
            result['_years_analysis_json'] = years_analysis_json
            
            # Store data for deferred saving (to avoid Flask app context issues in parallel threads)
            # The caller should save these after parallel execution completes
            # ALWAYS save AI interpretation - custom requirements SUPPLEMENT, not REPLACE
            result['_deferred_save'] = {
                'job_id': job_id,
                'job_title': job_title,
                'key_requirements': key_requirements,
                'job_location_full': job_location_full,
                'work_type': work_type,
                'should_save': bool(key_requirements)  # Always save when we have requirements
            }
            
            if not key_requirements:
                logging.warning(f"⚠️ AI did not return key_requirements for job {job_id} - requirements will not be saved")
            elif custom_requirements:
                logging.info(f"📝 Job {job_id} has custom requirements - AI interpretation will ALSO be saved (custom supplements AI)")
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
        
        # Always create a FRESH vetting log for each application
        # This ensures returning applicants get a new analysis + Bullhorn note
        parsed_email_id = candidate.get('_parsed_email_id')
        applied_job_id = candidate.get('_applied_job_id')
        
        try:
            vetting_log = CandidateVettingLog(
                bullhorn_candidate_id=candidate_id,
                candidate_name=candidate_name,
                candidate_email=candidate_email,
                status='processing',
                applied_job_id=applied_job_id,
                parsed_email_id=parsed_email_id
            )
            db.session.add(vetting_log)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            # If UniqueViolation, find the existing log and re-analyze if it has 0% scores
            if 'UniqueViolation' in str(e) or 'unique constraint' in str(e).lower():
                existing_log = CandidateVettingLog.query.filter_by(
                    bullhorn_candidate_id=candidate_id
                ).first()
                if existing_log:
                    if existing_log.highest_match_score == 0 and existing_log.status == 'completed':
                        # Reset stale 0% vetting log for re-analysis
                        logging.info(
                            f"🔄 Re-analyzing candidate {candidate_id} ({candidate_name}) — "
                            f"previous vetting had 0% scores (likely from aggressive filter threshold)"
                        )
                        # Delete old match records so they can be regenerated
                        CandidateJobMatch.query.filter_by(vetting_log_id=existing_log.id).delete()
                        existing_log.status = 'processing'
                        existing_log.highest_match_score = 0.0
                        existing_log.total_jobs_matched = 0
                        existing_log.is_qualified = False
                        existing_log.note_created = False
                        existing_log.bullhorn_note_id = None
                        existing_log.analyzed_at = None
                        existing_log.error_message = None
                        db.session.commit()
                        vetting_log = existing_log
                    else:
                        # Already has real scores — skip
                        logging.info(f"⏭️ Candidate {candidate_id} already vetted with score {existing_log.highest_match_score}%")
                        return existing_log
                else:
                    logging.error(f"Failed to create vetting log for candidate {candidate_id}: {str(e)}")
                    return None
            else:
                logging.error(f"Failed to create vetting log for candidate {candidate_id}: {str(e)}")
                return None
        
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
                # Remove <style> and <script> block contents before stripping tags
                # (PDF-to-HTML conversion embeds stylesheets that regex tag-stripping leaves as raw text)
                import re
                description = re.sub(r'<style[^>]*>.*?</style>', ' ', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'<script[^>]*>.*?</script>', ' ', description, flags=re.DOTALL | re.IGNORECASE)
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
            
            # ═══════════════════════════════════════════════════════════════
            # APPLIED JOB INJECTION
            # If the candidate applied to a specific job, ensure it is in
            # the job list even if it's not in a monitored tearsheet.
            # This guarantees the applied position is always evaluated.
            # ═══════════════════════════════════════════════════════════════
            if vetting_log.applied_job_id:
                applied_in_tearsheets = any(
                    j.get('id') == vetting_log.applied_job_id for j in jobs
                )
                if not applied_in_tearsheets:
                    try:
                        applied_job_data = self._fetch_applied_job(
                            self._get_bullhorn_service(),
                            vetting_log.applied_job_id
                        )
                        if applied_job_data:
                            jobs.append(applied_job_data)
                            logging.info(
                                f"🎯 Injected applied job {vetting_log.applied_job_id} "
                                f"({applied_job_data.get('title', 'Unknown')}) — "
                                f"not in monitored tearsheets"
                            )
                        else:
                            logging.warning(
                                f"⚠️ Applied job {vetting_log.applied_job_id} could not be "
                                f"fetched (closed/invalid) — will proceed without it"
                            )
                    except Exception as e:
                        logging.warning(
                            f"⚠️ Failed to fetch applied job {vetting_log.applied_job_id}: "
                            f"{str(e)} — will proceed without it"
                        )
            
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
            
            # ═══════════════════════════════════════════════════════════════
            # LAYER 1: EMBEDDING PRE-FILTER
            # Compare resume embedding against ALL active job embeddings
            # (across all tearsheets) and filter out clearly irrelevant pairs.
            # This preserves multi-job vetting — each job is independently
            # evaluated against the resume. A candidate applied for Job A 
            # can still be surfaced for Jobs B, C, D if semantically relevant.
            #
            # IMPORTANT: The applied job is PROTECTED from filtering.
            # It is always sent to GPT regardless of cosine similarity.
            # ═══════════════════════════════════════════════════════════════
            pre_filter_count = len(jobs_to_analyze)
            candidate_filter_info = {
                'id': candidate_id,
                'name': candidate_name
            }
            
            # Protect the applied job from the embedding pre-filter
            # It must ALWAYS be evaluated by GPT regardless of similarity
            applied_job_entry = None
            if vetting_log.applied_job_id:
                for j in jobs_to_analyze:
                    if j.get('id') == vetting_log.applied_job_id:
                        applied_job_entry = j
                        break
            
            # Run embedding filter on non-applied jobs only
            non_applied_jobs = (
                [j for j in jobs_to_analyze if j.get('id') != vetting_log.applied_job_id]
                if applied_job_entry else jobs_to_analyze
            )
            
            try:
                filtered_jobs, filtered_count = self.embedding_service.filter_relevant_jobs(
                    cached_resume_text, non_applied_jobs,
                    candidate_filter_info, vetting_log.id
                )
                
                # Re-add applied job to the front (guaranteed GPT analysis)
                if applied_job_entry:
                    if applied_job_entry not in filtered_jobs:
                        filtered_jobs.insert(0, applied_job_entry)
                        logging.info(
                            f"🎯 Applied job {vetting_log.applied_job_id} "
                            f"({applied_job_entry.get('title', 'Unknown')}) protected "
                            f"from embedding pre-filter — guaranteed GPT analysis"
                        )
                    else:
                        logging.info(
                            f"🎯 Applied job {vetting_log.applied_job_id} passed "
                            f"embedding filter naturally"
                        )
                
                jobs_to_analyze = filtered_jobs
                
                if filtered_count > 0:
                    logging.info(
                        f"🔍 Embedding pre-filter: {pre_filter_count} → {len(jobs_to_analyze)} jobs "
                        f"({filtered_count} filtered out)"
                    )
            except Exception as e:
                logging.error(f"⚠️ Embedding pre-filter error (bypassing filter): {str(e)}")
                # On error, proceed with all jobs (safe fallback)
            
            if not jobs_to_analyze:
                # SAFEGUARD: Never allow 100% filter rate — fall back to top 5
                # jobs by similarity so GPT can still produce real scores.
                # A 100% block likely means the threshold is too aggressive.
                logging.warning(
                    f"⚠️ Embedding pre-filter blocked ALL {pre_filter_count} jobs for "
                    f"candidate {candidate_id} ({candidate_name}). "
                    f"Falling back to top 5 jobs by similarity to avoid 0% scores."
                )
                # Re-run filter with threshold=0 to get similarity-ranked jobs
                try:
                    # Get all jobs with their similarities by temporarily using 0 threshold
                    from models import EmbeddingFilterLog
                    filter_logs = EmbeddingFilterLog.query.filter_by(
                        vetting_log_id=vetting_log.id
                    ).order_by(EmbeddingFilterLog.similarity_score.desc()).limit(5).all()
                    
                    if filter_logs:
                        # Re-include the top 5 most similar jobs
                        top_job_ids = {log.bullhorn_job_id for log in filter_logs}
                        jobs_to_analyze = [
                            job for job in jobs
                            if job.get('id') in top_job_ids
                        ]
                        top_sims = [f"{log.job_title}: {log.similarity_score:.4f}" for log in filter_logs]
                        logging.info(
                            f"🔄 Fallback: passing top {len(jobs_to_analyze)} jobs to GPT: "
                            f"{', '.join(top_sims)}"
                        )
                except Exception as fb_e:
                    logging.error(f"Fallback failed: {str(fb_e)}")
                
                if not jobs_to_analyze:
                    # True fallback: even the similarity lookup failed
                    logging.info(f"All jobs filtered by embedding pre-filter for candidate {candidate_id} — no GPT calls needed")
                    vetting_log.status = 'completed'
                    vetting_log.analyzed_at = datetime.utcnow()
                    db.session.commit()
                    return vetting_log
            
            logging.info(f"🚀 Parallel analysis of {len(jobs_to_analyze)} jobs (skipping {len(existing_job_ids)} already analyzed)")
            logging.info(f"📄 Resume: {len(cached_resume_text)} chars, First 200: {cached_resume_text[:200]}")
            
            # PRE-FETCH all custom requirements BEFORE parallel processing
            # This is critical because parallel threads don't have Flask app context
            # BATCH: One IN query instead of N individual queries
            job_requirements_cache = {}
            batch_job_ids = [job.get('id') for job in jobs_to_analyze if job.get('id')]
            if batch_job_ids:
                try:
                    batch_reqs = JobVettingRequirements.query.filter(
                        JobVettingRequirements.bullhorn_job_id.in_(batch_job_ids)
                    ).all()
                    for req in batch_reqs:
                        active = req.get_active_requirements()
                        if active:
                            job_requirements_cache[req.bullhorn_job_id] = active
                except Exception as e:
                    logging.error(f"Error batch-fetching job requirements: {str(e)}")
            
            logging.info(f"📋 Pre-fetched requirements for {len(job_requirements_cache)} jobs")
            
            # Read Layer 2 model fresh each cycle (supports live revert via VettingConfig)
            self.model = self._get_layer2_model()
            logging.info(f"🤖 Layer 2 model: {self.model}")
            
            # PRE-FETCH all DB-dependent config BEFORE entering ThreadPoolExecutor
            # Threads lack Flask app context — any DB access inside them will crash
            escalation_range = self._get_escalation_range()
            global_threshold = self.get_threshold()
            prefetched_global_reqs = self._get_global_custom_requirements() or ''  # '' not None — avoids DB fallback in threads
            
            # Pre-fetch per-job thresholds (batch query, not N individual queries)
            job_threshold_cache = {}
            try:
                batch_threshold_reqs = JobVettingRequirements.query.filter(
                    JobVettingRequirements.bullhorn_job_id.in_(batch_job_ids),
                    JobVettingRequirements.vetting_threshold.isnot(None)
                ).all()
                for req in batch_threshold_reqs:
                    job_threshold_cache[req.bullhorn_job_id] = float(req.vetting_threshold)
            except Exception as e:
                logging.error(f"Error pre-fetching job thresholds: {str(e)}")
            
            # Helper function for parallel execution - runs AI analysis for one job
            def analyze_single_job(job_with_req):
                """Analyze one job match - called in parallel threads.
                
                Layer 2: Uses self.model (GPT-4o-mini by default).
                Layer 3: If score falls in escalation range, re-analyzes with GPT-4o.
                
                IMPORTANT: This runs in a ThreadPoolExecutor thread WITHOUT Flask app context.
                ALL database access must use pre-fetched values from the main thread.
                """
                job = job_with_req['job']
                prefetched_req = job_with_req['requirements']  # Pre-fetched from main thread
                job_id = job.get('id')
                try:
                    # Layer 2: Main analysis with self.model (GPT-4o-mini default)
                    analysis = self.analyze_candidate_job_match(
                        cached_resume_text, job, candidate_location,
                        prefetched_requirements=prefetched_req,
                        prefetched_global_requirements=prefetched_global_reqs
                    )
                    
                    mini_score = analysis.get('match_score', 0)
                    
                    # Layer 3: Escalation check — re-analyze borderline with GPT-4o
                    # Uses pre-fetched escalation_range (no DB access needed)
                    esc_low, esc_high = escalation_range
                    if esc_low <= mini_score <= esc_high and self.model != 'gpt-4o':
                        job_title = job.get('title', 'Unknown')
                        logging.info(
                            f"⬆️ Escalating {candidate_name} × {job_title}: "
                            f"Layer 2 score={mini_score}% (in escalation range)"
                        )
                        try:
                            # Thread-safe: pass model_override instead of mutating self.model
                            escalated_analysis = self.analyze_candidate_job_match(
                                cached_resume_text, job, candidate_location,
                                prefetched_requirements=prefetched_req,
                                model_override='gpt-4o',
                                prefetched_global_requirements=prefetched_global_reqs
                            )
                            
                            gpt4o_score = escalated_analysis.get('match_score', 0)
                            
                            # Defer escalation log save to main thread (needs Flask context)
                            analysis['_escalation_data'] = {
                                'mini_score': mini_score,
                                'gpt4o_score': gpt4o_score,
                                'job_id': job_id,
                                'job_title': job_title
                            }
                            
                            # Use the GPT-4o result as the final analysis
                            analysis = escalated_analysis
                            # Carry over escalation data to the new analysis dict
                            analysis['_escalation_data'] = {
                                'mini_score': mini_score,
                                'gpt4o_score': gpt4o_score,
                                'job_id': job_id,
                                'job_title': job_title
                            }
                            
                        except Exception as esc_e:
                            logging.error(f"Escalation failed for job {job_id}: {str(esc_e)}")
                            # Fall back to Layer 2 result (preserved)
                    
                    return {
                        'job': job,
                        'job_id': job_id,
                        'analysis': analysis,
                        'error': None
                    }
                except Exception as e:
                    error_str = str(e)
                    logging.error(f"Error analyzing job {job_id}: {error_str}")
                    # Track OpenAI quota exhaustion (429 with 'quota' keyword)
                    if '429' in error_str and 'quota' in error_str.lower():
                        CandidateVettingService._consecutive_quota_errors += 1
                    return {
                        'job': job,
                        'job_id': job_id,
                        'analysis': {'match_score': 0, 'match_summary': f'Analysis failed: {error_str}'},
                        'error': error_str
                    }
            
            # Prepare jobs with pre-fetched requirements
            # CRITICAL: Use '' (not None) as default — None triggers a DB fallback query
            # inside analyze_candidate_job_match, which crashes in ThreadPoolExecutor
            # threads because they lack Flask app context.
            jobs_with_requirements = [
                {'job': job, 'requirements': job_requirements_cache.get(job.get('id'), '')}
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
                
                # Get recruiter info from ALL job assignedUsers (not just first)
                # Store as comma-separated lists to include all recruiters for notifications
                recruiter_names = []
                recruiter_emails = []
                recruiter_ids = []
                
                assigned_users = job.get('assignedUsers', {})
                if isinstance(assigned_users, dict):
                    assigned_users_list = assigned_users.get('data', [])
                elif isinstance(assigned_users, list):
                    assigned_users_list = assigned_users
                else:
                    assigned_users_list = []
                
                for user in assigned_users_list:
                    if isinstance(user, dict):
                        name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
                        email = user.get('email', '')
                        user_id = user.get('id')
                        if name:
                            recruiter_names.append(name)
                        if email:
                            recruiter_emails.append(email)
                        if user_id:
                            recruiter_ids.append(str(user_id))
                
                # Join as comma-separated for storage (first ID as primary for backward compatibility)
                recruiter_name = ', '.join(recruiter_names) if recruiter_names else ''
                recruiter_email = ', '.join(recruiter_emails) if recruiter_emails else ''
                recruiter_id = int(recruiter_ids[0]) if recruiter_ids else None
                
                # Determine if this is the job they applied to
                is_applied_job = vetting_log.applied_job_id == job_id if vetting_log.applied_job_id else False
                
                # Create match record - use pre-fetched job-specific threshold if set
                job_threshold = job_threshold_cache.get(job_id, global_threshold)
                
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
                    technical_score=analysis.get('technical_score'),
                    is_qualified=(analysis.get('match_score', 0) >= job_threshold) and not analysis.get('is_location_barrier', False),
                    is_applied_job=is_applied_job,
                    match_summary=analysis.get('match_summary', ''),
                    skills_match=analysis.get('skills_match', ''),
                    experience_match=analysis.get('experience_match', ''),
                    gaps_identified=analysis.get('gaps_identified', ''),
                    years_analysis_json=analysis.get('_years_analysis_json')
                )
                
                db.session.add(match_record)
                all_match_results.append(match_record)
                
                # Log with threshold info (show if custom threshold used)
                threshold_note = f" (threshold: {int(job_threshold)}%)" if job_threshold != threshold else ""
                if match_record.is_qualified:
                    qualified_matches.append(match_record)
                    logging.info(f"  ✅ Match: {job.get('title')} - {analysis.get('match_score')}%{threshold_note}")
                else:
                    if analysis.get('is_location_barrier', False) and analysis.get('match_score', 0) >= job_threshold:
                        logging.info(
                            f"  📍 Location barrier override: {job.get('title')} scored {analysis.get('match_score')}% "
                            f"(>= {int(job_threshold)}% threshold) but is_qualified=False due to location mismatch"
                        )
                    logging.info(f"  ❌ No match: {job.get('title')} - {analysis.get('match_score')}%{threshold_note}")
                    # Diagnostic: log GPT's reasoning for 0% scores
                    if analysis.get('match_score', 0) == 0:
                        summary = analysis.get('match_summary', 'no summary')[:200]
                        gaps = analysis.get('gaps_identified', 'no gaps')[:200]
                        logging.warning(f"    🔬 0% diagnostic: summary={summary} | gaps={gaps}")
                
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
                
                # Handle deferred escalation log (needs Flask app context for DB write)
                esc_data = analysis.get('_escalation_data')
                if esc_data:
                    try:
                        self.embedding_service.save_escalation_log(
                            vetting_log_id=vetting_log.id,
                            candidate_id=candidate_id,
                            candidate_name=candidate_name,
                            job_id=esc_data['job_id'],
                            job_title=esc_data['job_title'],
                            mini_score=esc_data['mini_score'],
                            gpt4o_score=esc_data['gpt4o_score'],
                            threshold=job_threshold
                        )
                    except Exception as esc_save_err:
                        logging.warning(f"Failed to save escalation log for job {esc_data['job_id']}: {esc_save_err}")
            
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
    
    def _format_match_note_block(self, match, job_threshold_map, is_applied=False, show_gaps=False, candidate_id=None):
        lines = []
        lines.append(f"• Job ID: {match.bullhorn_job_id} - {match.job_title}")

        tech = match.technical_score
        has_location_penalty = (
            tech is not None
            and tech != match.match_score
            and 'location mismatch' in (match.gaps_identified or '').lower()
        )

        match_custom = job_threshold_map.get(match.bullhorn_job_id)
        if has_location_penalty:
            score_text = f"  Technical Fit: {tech:.0f}% → Location Penalty → Final: {match.match_score:.0f}%"
        else:
            score_text = f"  Match Score: {match.match_score:.0f}%"

        if match_custom:
            score_text += f"  |  Threshold: {match_custom:.0f}% (custom)"
        lines.append(score_text)

        if is_applied:
            lines.append(f"  ⭐ APPLIED TO THIS POSITION")

        lines.append(f"  Summary: {match.match_summary}")
        lines.append(f"  Skills: {match.skills_match}")

        if show_gaps and match.gaps_identified:
            gaps_text = self._normalize_gaps_text(match.gaps_identified, candidate_id)
            lines.append(f"  Gaps: {gaps_text}")

        return lines

    def _normalize_gaps_text(self, gaps, candidate_id=None):
        """Layer 3 safety net: normalize gaps_identified to clean prose.
        
        Handles:
        - list type: GPT returned an array that bypassed Layer 2
        - str starting with '[': legacy JSON array stored as string in DB
        - str: returned as-is (already clean prose)
        """
        if isinstance(gaps, list):
            logging.warning(f"Render-time array normalization for candidate {candidate_id}")
            return ". ".join(str(item) for item in gaps)
        
        if isinstance(gaps, str) and gaps.startswith('['):
            try:
                gaps_list = json.loads(gaps)
                if isinstance(gaps_list, list):
                    logging.warning(f"Render-time JSON string normalization for candidate {candidate_id}")
                    return ". ".join(str(item) for item in gaps_list)
            except json.JSONDecodeError:
                pass  # Not valid JSON, keep original
        
        return gaps
    
    def create_candidate_note(self, vetting_log: CandidateVettingLog) -> bool:
        """
        Create a note on the candidate record summarizing the vetting results.
        
        Args:
            vetting_log: The vetting log with analysis results
            
        Returns:
            True if note was created successfully (or already exists)
        """
        # DEDUPLICATION SAFETY: Skip if note already created for this vetting log
        if vetting_log.note_created:
            logging.info(f"⏭️ Note already exists for vetting log {vetting_log.id} (candidate {vetting_log.bullhorn_candidate_id}), skipping creation")
            return True  # Return True to indicate note exists
        
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return False
        
        # PRE-CREATION SAFEGUARD: Check Bullhorn for existing AI vetting notes (24h window)
        # This prevents duplicate notes even if upstream dedup logic has a bug
        from datetime import timedelta
        try:
            existing_notes = bullhorn.get_candidate_notes(
                vetting_log.bullhorn_candidate_id,
                action_filter=[
                    # Current format (≤30 chars for Bullhorn action field)
                    "Scout Screen - Qualified",
                    "Scout Screen - Not Qualified",
                    "Scout Screen - Incomplete",
                    "Scout Screen - Loc Barrier",
                    "Scout Screen - Location Barrier",  # legacy (was 31 chars, never wrote successfully)
                    # Backward compat: match legacy action strings
                    "Scout Screening - Qualified",
                    "Scout Screening - Not Recommended",
                    "Scout Screening - Incomplete",
                    "AI Vetting - Qualified",
                    "AI Vetting - Not Recommended",
                    "AI Vetting - Incomplete"
                ],
                since=datetime.utcnow() - timedelta(hours=24)
            )
            if existing_notes:
                logging.warning(
                    f"⚠️ DUPLICATE SAFEGUARD: Candidate {vetting_log.bullhorn_candidate_id} already has "
                    f"{len(existing_notes)} AI vetting note(s) in Bullhorn from last 24h. "
                    f"Skipping duplicate note creation."
                )
                vetting_log.note_created = True
                vetting_log.bullhorn_note_id = existing_notes[0].get('id')
                db.session.commit()
                return True
        except Exception as e:
            # Don't block note creation if the safety check itself fails
            logging.warning(f"Pre-note duplicate check failed (proceeding with creation): {str(e)}")
        
        # Get all match results for this candidate
        matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id
        ).order_by(CandidateJobMatch.match_score.desc()).all()
        
        # Build note content
        # Header shows global threshold; inline annotations show per-job custom thresholds
        global_threshold = self.get_threshold()
        threshold = global_threshold
        qualified_matches = [m for m in matches if m.is_qualified] if matches else []
        
        # Pre-fetch per-job thresholds for matched jobs to annotate inline
        job_ids = [m.bullhorn_job_id for m in matches if m.bullhorn_job_id]
        job_threshold_map = {}
        if job_ids:
            try:
                from models import JobVettingRequirements
                custom_reqs = JobVettingRequirements.query.filter(
                    JobVettingRequirements.bullhorn_job_id.in_(job_ids),
                    JobVettingRequirements.vetting_threshold.isnot(None)
                ).all()
                for req in custom_reqs:
                    job_threshold_map[req.bullhorn_job_id] = float(req.vetting_threshold)
            except Exception as e:
                logging.warning(f"Could not fetch per-job thresholds for note: {str(e)}")
        
        # Handle case where no jobs were analyzed (no matches recorded)
        if not matches:
            # Create a note explaining why no analysis was done
            error_reason = vetting_log.error_message or "No job matches could be performed"
            note_lines = [
                f"📋 SCOUT SCREENING - INCOMPLETE ANALYSIS",
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
            action = "Scout Screen - Incomplete"
            
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
        
        # ── LOCATION BARRIER DETECTION ──
        # Candidate scored above the technical threshold but was overridden out of
        # qualified status because the role is on-site/hybrid and they're non-local.
        location_barrier_matches = [
            m for m in matches
            if not m.is_qualified
            and 'location mismatch' in (m.gaps_identified or '').lower()
            and (m.technical_score or m.match_score) >= (threshold - 15)
        ]
        is_location_barrier_candidate = (
            len(qualified_matches) == 0 and len(location_barrier_matches) > 0
        )

        if is_location_barrier_candidate:
            # Strong fit / location barrier note
            top_lb = sorted(location_barrier_matches, key=lambda m: (m.technical_score or m.match_score), reverse=True)
            top_tech = top_lb[0].technical_score or top_lb[0].match_score if top_lb else 0
            top_final = top_lb[0].match_score if top_lb else 0
            note_lines = [
                f"📍 SCOUT SCREENING - STRONG FIT / LOCATION BARRIER",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Threshold: {threshold}%",
                f"Technical Fit: {top_tech:.0f}% (skills & experience, before location penalty)",
                f"Final Score: {top_final:.0f}% (after location penalty)",
                f"",
                f"This candidate is a strong technical fit but has a location barrier",
                f"for an on-site/hybrid position. Recruiter review is recommended.",
                f"",
                f"POSITION(S) AFFECTED:",
            ]
            for m in top_lb:
                tech = m.technical_score or m.match_score
                match_custom = job_threshold_map.get(m.bullhorn_job_id)
                if tech != m.match_score:
                    score_line = f"  Technical Fit: {tech:.0f}% → Location Penalty → Final: {m.match_score:.0f}%"
                else:
                    score_line = f"  Score: {m.match_score:.0f}%"
                if match_custom:
                    score_line += f"  |  Threshold: {match_custom:.0f}% (custom)"
                gaps_full = m.gaps_identified or ''
                loc_gap_parts = [
                    part.strip() for part in gaps_full.replace(' | ', '|').split('|')
                    if 'location mismatch' in part.lower()
                ]
                non_loc_parts = [
                    part.strip() for part in gaps_full.replace(' | ', '|').split('|')
                    if 'location mismatch' not in part.lower() and part.strip()
                ]
                loc_gap_text = ' | '.join(loc_gap_parts) if loc_gap_parts else ''
                note_lines += [
                    f"",
                    f"• Job ID: {m.bullhorn_job_id} - {m.job_title}",
                    score_line,
                    f"  ⚠️  LOCATION BARRIER",
                    f"  Summary: {m.match_summary}",
                    f"  Skills: {m.skills_match}",
                ]
                if non_loc_parts:
                    note_lines.append(f"  Other Gaps: {' | '.join(non_loc_parts)}")
                if loc_gap_text:
                    note_lines.append(f"  Location: {loc_gap_text}")
            note_text = "\n".join(note_lines)
            action = "Scout Screen - Loc Barrier"

            note_id = bullhorn.create_candidate_note(
                vetting_log.bullhorn_candidate_id,
                note_text,
                action=action
            )
            if note_id:
                vetting_log.note_created = True
                vetting_log.bullhorn_note_id = note_id
                db.session.commit()
                logging.info(
                    f"📍 Created location barrier note for candidate {vetting_log.bullhorn_candidate_id} "
                    f"(tech fit: {top_tech:.0f}%, final: {top_final:.0f}%)"
                )
                return True
            else:
                logging.error(f"Failed to create location barrier note for candidate {vetting_log.bullhorn_candidate_id}")
                return False

        elif vetting_log.is_qualified:
            # Qualified candidate note
            note_lines = [
                f"🎯 SCOUT SCREENING - QUALIFIED CANDIDATE",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Threshold: {threshold}%",
                f"Qualified Matches: {len(qualified_matches)} of {len(matches)} jobs",
                f"Highest Match Score: {vetting_log.highest_match_score:.0f}%",
                f"",
            ]
            
            applied_match = None
            other_qualified = []
            for match in qualified_matches:
                if match.is_applied_job:
                    applied_match = match
                else:
                    other_qualified.append(match)
            
            other_qualified.sort(key=lambda m: m.match_score, reverse=True)
            
            if applied_match:
                note_lines.append(f"APPLIED POSITION (QUALIFIED):")
                note_lines.append(f"")
                note_lines += self._format_match_note_block(applied_match, job_threshold_map, is_applied=True)
                if other_qualified:
                    note_lines.append(f"")
                    note_lines.append(f"OTHER QUALIFIED POSITIONS:")
            else:
                note_lines.append(f"QUALIFIED POSITIONS:")
            
            for match in other_qualified:
                note_lines.append(f"")
                note_lines += self._format_match_note_block(match, job_threshold_map)
        else:
            # Not qualified note
            note_lines = [
                f"📋 SCOUT SCREENING - NOT RECOMMENDED",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Threshold: {threshold}%",
                f"Highest Match Score: {vetting_log.highest_match_score:.0f}%",
                f"Jobs Analyzed: {len(matches)}",
                f"",
                f"This candidate did not meet the {threshold}% match threshold for any current open positions.",
                f"",
            ]
            
            applied_match = None
            other_matches = []
            for match in matches:
                if match.is_applied_job:
                    applied_match = match
                else:
                    other_matches.append(match)
            
            other_matches.sort(key=lambda m: m.match_score, reverse=True)
            
            if applied_match:
                note_lines.append(f"APPLIED POSITION:")
                note_lines.append(f"")
                note_lines += self._format_match_note_block(applied_match, job_threshold_map, is_applied=True, show_gaps=True, candidate_id=vetting_log.bullhorn_candidate_id)
                note_lines.append(f"")
                note_lines.append(f"OTHER TOP MATCHES:")
            else:
                note_lines.append(f"TOP ANALYSIS RESULTS:")
            
            for match in other_matches[:5]:
                note_lines.append(f"")
                note_lines += self._format_match_note_block(match, job_threshold_map, show_gaps=True, candidate_id=vetting_log.bullhorn_candidate_id)
        
        note_text = "\n".join(note_lines)
        
        # Create the note
        action = "Scout Screen - Qualified" if vetting_log.is_qualified else "Scout Screen - Not Qualified"
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
        # Note: recruiter_email may now be comma-separated (multiple recruiters per job)
        primary_recruiter_email = None
        primary_recruiter_name = None
        cc_recruiter_emails = []
        
        # First pass: find the applied job recruiter (primary recipient)
        for match in matches:
            if match.is_applied_job and match.recruiter_email:
                emails = parse_emails(match.recruiter_email)
                names = parse_names(match.recruiter_name)
                if emails:
                    primary_recruiter_email = emails[0]  # First recruiter on applied job is primary
                    primary_recruiter_name = names[0] if names else ''
                break
        
        # Second pass: collect all unique recruiter emails from all matches
        # If no applied job recruiter found, first recruiter becomes primary
        seen_emails = set()
        for match in matches:
            emails = parse_emails(match.recruiter_email)
            names = parse_names(match.recruiter_name)
            
            for i, email in enumerate(emails):
                if email and email not in seen_emails:
                    seen_emails.add(email)
                    name = names[i] if i < len(names) else ''
                    
                    if not primary_recruiter_email:
                        # No applied job match - first recruiter becomes primary
                        primary_recruiter_email = email
                        primary_recruiter_name = name
                    elif email != primary_recruiter_email:
                        # Different from primary - add to CC list
                        cc_recruiter_emails.append(email)
        
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
                
                # ── Scout Vetting trigger ──
                # After recruiter notification, initiate Scout Vetting for qualified matches
                try:
                    from scout_vetting_service import ScoutVettingService
                    sv_service = ScoutVettingService(email_service=self.email_service, bullhorn_service=self.bullhorn)
                    if sv_service.is_enabled():
                        sv_result = sv_service.initiate_vetting(vetting_log, matches)
                        logging.info(f"🔍 Scout Vetting initiated: {sv_result.get('created', 0)} sessions created, "
                                    f"{sv_result.get('queued', 0)} queued, {sv_result.get('skipped', 0)} skipped")
                except Exception as sv_err:
                    logging.error(f"Scout Vetting trigger error (non-blocking): {str(sv_err)}")
                
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
                    A new candidate has been analyzed by Scout Screening and matches 
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
                Powered by Scout Screening™ • Myticas Consulting
            </div>
        </div>
        """
        
        # Send the email with CC recipients and BCC admin for transparency
        try:
            # Always BCC admin for monitoring/troubleshooting
            admin_bcc_email = 'kroots@myticas.com'
            
            job_titles = ', '.join(set(m.job_title for m in matches if m.job_title)) or 'unknown position'
            avg_score = sum(m.match_score for m in matches) / len(matches) if matches else 0
            changes_summary = f"Screening alert — {candidate_name} matched {job_titles} (Score: {avg_score:.0f}%)"
            result = self.email_service.send_html_email(
                to_email=recruiter_email,
                subject=subject,
                html_content=html_content,
                notification_type='vetting_recruiter_notification',
                cc_emails=cc_emails,  # CC all other recruiters on same thread
                bcc_emails=[admin_bcc_email],  # BCC admin for transparency
                changes_summary=changes_summary
            )
            return result is True or (isinstance(result, dict) and result.get('success', False))
        except Exception as e:
            logging.error(f"Email send error: {str(e)}")
            return False
    
    def _reset_zero_score_failures(self):
        """Auto-retry safeguard: detect and reset candidates where ALL job matches
        scored 0%, indicating an API failure (e.g., OpenAI quota exhaustion) rather
        than a genuine low score.
        
        Called at the start of each vetting cycle to automatically queue failed
        candidates for re-processing.
        
        Safety guards:
        - Only resets records older than 10 minutes (avoids in-progress interference)
        - Max 50 records per cycle (prevents thundering herd)
        - Only resets when ALL job matches are 0% (not legitimate low scores)
        """
        try:
            from models import CandidateVettingLog, CandidateJobMatch, ParsedEmail
            from sqlalchemy import func
            
            cutoff = datetime.utcnow() - timedelta(minutes=10)
            
            # Find completed vetting logs with highest_match_score = 0
            # that are old enough to not be in-progress
            # Excludes retry_blocked candidates (manually excluded by admin — e.g. unparsable resumes)
            zero_logs = CandidateVettingLog.query.filter(
                CandidateVettingLog.highest_match_score == 0,
                CandidateVettingLog.status == 'completed',
                CandidateVettingLog.created_at < cutoff,
                CandidateVettingLog.retry_blocked != True
            ).limit(50).all()
            
            if not zero_logs:
                return
            
            reset_count = 0
            for log in zero_logs:
                # Verify ALL job matches scored 0 (not a legitimate low score)
                non_zero = db.session.query(func.count(CandidateJobMatch.id)).filter(
                    CandidateJobMatch.vetting_log_id == log.id,
                    CandidateJobMatch.match_score > 0
                ).scalar()
                
                if non_zero > 0:
                    continue  # Has some non-zero scores — legitimate result
                
                candidate_id = log.bullhorn_candidate_id
                log_id = log.id
                
                # Delete child records (FK constraints)
                CandidateJobMatch.query.filter_by(vetting_log_id=log_id).delete()
                
                from models import EmbeddingFilterLog, EscalationLog
                EmbeddingFilterLog.query.filter_by(vetting_log_id=log_id).delete()
                EscalationLog.query.filter_by(vetting_log_id=log_id).delete()
                
                # Delete the vetting log
                db.session.delete(log)
                
                # Reset vetted_at on ParsedEmail to re-queue
                ParsedEmail.query.filter_by(
                    bullhorn_candidate_id=candidate_id
                ).update({'vetted_at': None})
                
                reset_count += 1
            
            if reset_count > 0:
                db.session.commit()
                logging.info(f"🔄 Auto-retry: Reset {reset_count} candidates with 0% scores (API failure recovery)")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error in zero-score auto-retry: {str(e)}")
    
    def _reset_stuck_processing(self):
        """Reset vetting logs stuck in 'processing' status.
        
        When a deployment restart or worker timeout kills a vetting cycle
        mid-analysis, CandidateVettingLog records get orphaned in 'processing'
        status with 0 job matches. The candidate's ParsedEmail.vetted_at is
        already set, so they never get re-queued.
        
        This method detects and resets those orphaned records:
        - Only resets 'processing' logs older than 10 minutes
        - Only resets logs with 0 job matches (never started analysis)
        - Max 50 per cycle
        """
        try:
            from models import CandidateVettingLog, CandidateJobMatch, ParsedEmail
            from sqlalchemy import func
            
            cutoff = datetime.utcnow() - timedelta(minutes=10)
            
            stuck_logs = CandidateVettingLog.query.filter(
                CandidateVettingLog.status == 'processing',
                CandidateVettingLog.created_at < cutoff
            ).limit(50).all()
            
            if not stuck_logs:
                return
            
            reset_count = 0
            for log in stuck_logs:
                # Only reset if no job matches were created (cycle died before analysis)
                match_count = db.session.query(func.count(CandidateJobMatch.id)).filter(
                    CandidateJobMatch.vetting_log_id == log.id
                ).scalar()
                
                if match_count > 0:
                    continue  # Has job matches — may be partially complete, skip
                
                candidate_id = log.bullhorn_candidate_id
                log_id = log.id
                
                # Delete child records (FK constraints)
                from models import EmbeddingFilterLog, EscalationLog
                EmbeddingFilterLog.query.filter_by(vetting_log_id=log_id).delete()
                EscalationLog.query.filter_by(vetting_log_id=log_id).delete()
                
                # Delete the stuck log
                db.session.delete(log)
                
                # Reset vetted_at to re-queue
                ParsedEmail.query.filter_by(
                    bullhorn_candidate_id=candidate_id
                ).update({'vetted_at': None})
                
                reset_count += 1
            
            if reset_count > 0:
                db.session.commit()
                logging.info(f"🔄 Auto-retry: Reset {reset_count} candidates stuck in 'processing' (deployment restart recovery)")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error in stuck-processing reset: {str(e)}")
    
    def _handle_quota_exhaustion(self):
        """Handle OpenAI quota exhaustion: auto-disable vetting and send alert email.
        
        Called when 3+ consecutive quota errors are detected in a single vetting cycle.
        Prevents the system from creating further 0% notes in Bullhorn.
        """
        if CandidateVettingService._quota_alert_sent:
            return  # Already alerted this outage
        
        try:
            # Auto-disable vetting
            config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
            if config:
                config.setting_value = 'false'
                db.session.commit()
                logging.warning("⛔ Scout Screening auto-disabled due to OpenAI quota exhaustion")
            
            # Send alert email
            try:
                email_svc = EmailService()
                alert_email = self._get_admin_notification_email() or 'kroots@myticas.com'
                
                subject = "⚠️ Scout Screening Auto-Disabled — OpenAI Quota Exhausted"
                message = (
                    "ALERT: Scout Screening has been automatically disabled.\n\n"
                    "WHAT'S HAPPENING:\n"
                    f"  {CandidateVettingService._consecutive_quota_errors} consecutive OpenAI API calls "
                    "returned '429 - You exceeded your current quota'.\n"
                    "  All vetting scores are returning 0%, creating incorrect notes in Bullhorn.\n"
                    "  To prevent further damage, Scout Screening has been disabled.\n\n"
                    "ACTION REQUIRED:\n"
                    "  1. Top up OpenAI credits at https://platform.openai.com/account/billing\n"
                    "  2. Re-enable Scout Screening from the /screening settings page\n"
                    "  3. Any candidates vetted with 0% during this outage will be automatically\n"
                    "     re-processed on the next vetting cycle (auto-retry safeguard)\n\n"
                    "This is an automated alert from Scout Screening."
                )
                email_svc.send_notification_email(
                    to_email=alert_email,
                    subject=subject,
                    message=message,
                    notification_type='openai_quota_alert'
                )
                logging.info(f"📧 Quota exhaustion alert sent to {alert_email}")
            except Exception as email_err:
                logging.error(f"Failed to send quota alert email: {str(email_err)}")
            
            CandidateVettingService._quota_alert_sent = True
            
        except Exception as e:
            logging.error(f"Error handling quota exhaustion: {str(e)}")
    
    def _get_admin_notification_email(self) -> str:
        """Get the admin notification email from VettingConfig."""
        try:
            config = VettingConfig.query.filter_by(setting_key='admin_notification_email').first()
            if config and config.setting_value:
                return config.setting_value.strip()
        except Exception:
            pass
        return ''

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
        
        # Auto-retry: reset candidates that failed with 0% on all jobs
        # (e.g., from a previous OpenAI quota outage)
        self._reset_zero_score_failures()
        
        # Auto-retry: reset candidates stuck in 'processing' status
        # (e.g., from deployment restarts killing cycles mid-analysis)
        self._reset_stuck_processing()
        
        # Reset quota error counter at cycle start
        CandidateVettingService._consecutive_quota_errors = 0
        
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
                        
                        # Create note (only if not already created)
                        if not vetting_log.note_created:
                            if self.create_candidate_note(vetting_log):
                                summary['notes_created'] += 1
                        else:
                            logging.info(f"⏭️ Skipping note creation - already exists for candidate {vetting_log.bullhorn_candidate_id}")
                        
                        # Send notifications for qualified candidates
                        if vetting_log.is_qualified:
                            notif_count = self.send_recruiter_notifications(vetting_log)
                            summary['notifications_sent'] += notif_count
                    
                    # Mark the ParsedEmail record as vetted (if applicable)
                    parsed_email_id = candidate.get('_parsed_email_id')
                    if parsed_email_id:
                        self._mark_application_vetted(parsed_email_id)
                            
                except Exception as e:
                    db.session.rollback()
                    error_msg = f"Error processing candidate {candidate.get('id')}: {str(e)}"
                    logging.error(error_msg)
                    summary['errors'].append(error_msg)
            
            # Update last run timestamp
            self._set_last_run_timestamp(cycle_start)
            
            # Check for OpenAI quota exhaustion at end of cycle
            if CandidateVettingService._consecutive_quota_errors >= 3:
                self._handle_quota_exhaustion()
            elif CandidateVettingService._consecutive_quota_errors == 0:
                # Reset alert flag when quota is healthy
                CandidateVettingService._quota_alert_sent = False
            
            logging.info(f"✅ Vetting cycle complete: {summary}")
            return summary
            
        except Exception as e:
            db.session.rollback()
            error_msg = f"Vetting cycle error: {str(e)}"
            logging.error(error_msg)
            summary['errors'].append(error_msg)
            return summary
        finally:
            # Always release the lock
            self._release_vetting_lock()
