"""
Job Management - Tearsheet job retrieval, requirements extraction, and sync.

Contains:
- get_active_jobs_from_tearsheets: Fetches jobs from monitored tearsheets
- extract_requirements_for_jobs: Batch AI requirement extraction
- check_and_refresh_changed_jobs: Detects and refreshes modified jobs
- sync_job_recruiter_assignments: Syncs recruiter changes to match records
- sync_requirements_with_active_jobs: Removes orphaned requirements
- refresh_empty_job_locations: One-time location backfill
- get_candidates_with_duplicates: Finds candidates with duplicate notes
- cleanup_duplicate_notes_batch: Deprecated cleanup stub
- _save_ai_interpreted_requirements: Persists AI requirements to DB
- get_active_job_ids: Cached set of active job IDs
- get_candidate_job_submission: Finds which job a candidate applied to
"""

import logging
import json
from datetime import datetime
from typing import Dict, List, Optional

from vetting.geo_utils import map_work_type
from app import db
from models import BullhornMonitor, CandidateJobMatch, CandidateVettingLog, GlobalSettings, JobVettingRequirements
from utils.text_sanitization import sanitize_text


class JobManagementMixin:
    """Tearsheet job retrieval, requirements extraction, and sync."""

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
                    match.recruiter_email = sanitize_text(current_data['emails'])
                    match.recruiter_name = sanitize_text(current_data['names'])
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
        if (type(self)._active_job_ids_cache is not None
                and now - type(self)._active_job_ids_cache_time < self._ACTIVE_JOB_IDS_TTL):
            return type(self)._active_job_ids_cache
        try:
            active_jobs = self.get_active_jobs_from_tearsheets()
            result = set(int(job.get('id')) for job in active_jobs if job.get('id'))
            type(self)._active_job_ids_cache = result
            type(self)._active_job_ids_cache_time = now
            return result
        except Exception as e:
            logging.error(f"Error getting active job IDs: {str(e)}")
            return type(self)._active_job_ids_cache or set()
    
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

