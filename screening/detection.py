from __future__ import annotations
"""
Candidate Detection - Discovery of new candidates from Bullhorn and ParsedEmail.

Contains:
- detect_new_applicants: Finds Online Applicant candidates via Bullhorn search
- detect_pandologic_candidates: Finds Pandologic API candidates
- detect_unvetted_applications: Primary detection via ParsedEmail records
- _should_skip_candidate: Job-aware dedup logic
- _fetch_candidate_details: Fetches full candidate data from Bullhorn
- _fetch_applied_job: Fetches a single job for applied-job injection
- _mark_application_vetted: Marks ParsedEmail as vetted
- get_candidate_resume: Downloads resume file from Bullhorn
- extract_resume_text / _extract_text_from_*: Delegates to vetting.resume_utils
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app import db
from sqlalchemy import func, case
from models import CandidateVettingLog, ParsedEmail, VettingConfig
from vetting.resume_utils import (
    extract_resume_text as _extract_resume_text,
    extract_text_from_pdf as _extract_text_from_pdf,
    extract_text_from_docx as _extract_text_from_docx,
    extract_text_from_doc as _extract_text_from_doc,
)


class CandidateDetectionMixin:
    """Candidate discovery from Bullhorn and ParsedEmail."""

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
        """Mark a ParsedEmail record as vetted and reset retry counter on success"""
        try:
            parsed_email = ParsedEmail.query.get(parsed_email_id)
            if parsed_email:
                parsed_email.vetted_at = datetime.utcnow()
                if parsed_email.vetting_retry_count > 0:
                    parsed_email.vetting_retry_count = 0
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

