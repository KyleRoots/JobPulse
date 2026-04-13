"""
Candidate Vetting Service - GPT-5.4 powered candidate-job matching engine

This service monitors new job applicants with "Online Applicant" status,
analyzes their resumes against all open positions in monitored tearsheets,
and notifies recruiters when candidates match at 80%+ threshold.
"""

import logging
import threading
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
from vetting.geo_utils import map_work_type


from screening import (
    PromptBuilderMixin,
    NoteBuilderMixin,
    NotificationMixin,
    CandidateDetectionMixin,
    JobManagementMixin,
    RecoveryMixin,
)


class CandidateVettingService(
    PromptBuilderMixin,
    NoteBuilderMixin,
    NotificationMixin,
    CandidateDetectionMixin,
    JobManagementMixin,
    RecoveryMixin,
):
    """
    GPT-5.4 powered candidate vetting system that:
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
        self.model = self._get_layer2_model()  # Default Layer 2 model, configurable via VettingConfig
        
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
            value = self.get_config_value('layer2_model', 'gpt-5.4')
            if value and value.strip():
                return value.strip()
        except Exception:
            pass
        return 'gpt-5.4'
    
    def _get_escalation_range(self) -> tuple:
        """Get escalation score range from VettingConfig.
        
        Returns:
            Tuple of (low, high) — scores within this range trigger GPT-5.4 re-analysis.
        """
        try:
            low = float(self.get_config_value('escalation_low', '60'))
            high = float(self.get_config_value('escalation_high', '85'))
            return (low, high)
        except (ValueError, TypeError):
            return (60.0, 85.0)
    
    def should_escalate_to_layer3(self, match_score: float) -> bool:
        """Check if a match score falls in the escalation range for Layer 3 re-analysis.
        
        Args:
            match_score: Layer 2 model match score.
            
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

    # TTL cache for get_active_job_ids — avoids 7+ Bullhorn API calls per page load
    _active_job_ids_cache: set = None
    _active_job_ids_cache_time: float = 0
    _ACTIVE_JOB_IDS_TTL = 300  # 5 minutes

    # OpenAI quota exhaustion tracking (class-level, shared across instances)
    _consecutive_quota_errors: int = 0
    _quota_alert_sent: bool = False
    _bullhorn_lock = threading.Lock()

    def process_candidate(self, candidate: Dict, cached_jobs: Optional[List[Dict]] = None) -> Optional[CandidateVettingLog]:
        """
        Process a single candidate through the full vetting pipeline.
        
        Args:
            candidate: Candidate dictionary from Bullhorn
            cached_jobs: Pre-loaded job list (shared across batch to avoid redundant API calls)
            
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
            db.session.refresh(vetting_log)
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
            # Get which job they applied to (thread-safe Bullhorn access)
            with CandidateVettingService._bullhorn_lock:
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
                with CandidateVettingService._bullhorn_lock:
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
            
            # Use cached jobs if provided (batch optimization), otherwise load fresh
            # Shallow copy so applied-job injection doesn't mutate the shared list
            if cached_jobs is not None:
                jobs = list(cached_jobs)
            else:
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
                        with CandidateVettingService._bullhorn_lock:
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
                
                Layer 2: Uses self.model (configurable via VettingConfig).
                Layer 3: If score falls in escalation range, re-analyzes with Layer 3 model.
                
                IMPORTANT: This runs in a ThreadPoolExecutor thread WITHOUT Flask app context.
                ALL database access must use pre-fetched values from the main thread.
                """
                job = job_with_req['job']
                prefetched_req = job_with_req['requirements']  # Pre-fetched from main thread
                job_id = job.get('id')
                try:
                    # Layer 2: Main analysis with self.model (configurable)
                    analysis = self.analyze_candidate_job_match(
                        cached_resume_text, job, candidate_location,
                        prefetched_requirements=prefetched_req,
                        prefetched_global_requirements=prefetched_global_reqs
                    )
                    
                    mini_score = analysis.get('match_score', 0)
                    
                    # Layer 3: Escalation check — re-analyze borderline with GPT-5
                    # Uses pre-fetched escalation_range (no DB access needed)
                    esc_low, esc_high = escalation_range
                    if esc_low <= mini_score <= esc_high and self.model != 'gpt-5.4':
                        job_title = job.get('title', 'Unknown')
                        logging.info(
                            f"⬆️ Escalating {candidate_name} × {job_title}: "
                            f"Layer 2 score={mini_score}% (in escalation range)"
                        )
                        try:
                            escalated_analysis = self.analyze_candidate_job_match(
                                cached_resume_text, job, candidate_location,
                                prefetched_requirements=prefetched_req,
                                model_override='gpt-5.4',
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
                            
                            # Use the GPT-5 result as the final analysis
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
            
            # Run parallel analysis - cap concurrent threads to respect API rate limits
            # When running in batch parallel mode (cached_jobs provided), use fewer threads
            # per candidate since multiple candidates run simultaneously (5 × 8 = 40 total)
            analysis_results = []
            thread_cap = 8 if cached_jobs is not None else 15
            max_workers = min(thread_cap, len(jobs_to_analyze))
            
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
                    experience_match=self._build_experience_match(analysis),
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
            
            # ZERO-SCORE VERIFICATION: If ALL jobs scored 0%, re-verify the top candidates
            # to confirm the AI is truly confident — a candidate who applied should have
            # at least some minimal relevance unless they're genuinely mismatched.
            if all_match_results and all(m.match_score == 0 for m in all_match_results):
                logging.info(
                    f"🔄 Zero-score verification: {candidate_name} scored 0% on all "
                    f"{len(all_match_results)} jobs — running re-verification on top matches"
                )
                try:
                    reverify_matches = all_match_results[:3]
                    
                    reverify_jobs = []
                    for m in reverify_matches:
                        job_match = next(
                            (j for j in jobs_to_analyze if j.get('id') == m.bullhorn_job_id),
                            None
                        )
                        if job_match:
                            reverify_jobs.append((m, job_match))
                    
                    if reverify_jobs:
                        for match_record, job in reverify_jobs:
                            try:
                                reverify_result = self._reverify_zero_score(
                                    cached_resume_text, job, candidate_location,
                                    match_record.match_summary or '',
                                    match_record.gaps_identified or '',
                                    prefetched_global_reqs
                                )
                                if reverify_result and reverify_result.get('revised_score', 0) > 0:
                                    old_score = match_record.match_score
                                    new_score = reverify_result['revised_score']
                                    match_record.match_score = new_score
                                    match_record.match_summary = (
                                        f"[Verified] {reverify_result.get('revised_summary', match_record.match_summary)}"
                                    )
                                    match_record.gaps_identified = reverify_result.get(
                                        'revised_gaps', match_record.gaps_identified
                                    )
                                    job_threshold = job_threshold_cache.get(
                                        match_record.bullhorn_job_id, global_threshold
                                    )
                                    match_record.is_qualified = new_score >= job_threshold
                                    if match_record.is_qualified:
                                        qualified_matches.append(match_record)
                                    logging.info(
                                        f"  ✅ Re-verified {job.get('title')}: 0% → {new_score}% "
                                        f"(reason: {reverify_result.get('revision_reason', 'N/A')[:100]})"
                                    )
                                else:
                                    logging.info(
                                        f"  ✔️ Confirmed 0% for {job.get('title')}: "
                                        f"{reverify_result.get('confidence_reason', 'AI confirmed non-fit')[:100]}"
                                    )
                            except Exception as rv_err:
                                logging.warning(
                                    f"  ⚠️ Re-verification failed for job {match_record.bullhorn_job_id}: {rv_err}"
                                )
                except Exception as verify_err:
                    logging.warning(f"⚠️ Zero-score verification error for {candidate_name}: {verify_err}")
            
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
            try:
                vetting_log = db.session.merge(vetting_log)
                vetting_log.status = 'failed'
                vetting_log.error_message = str(e)[:500]
                vetting_log.retry_count += 1
                db.session.commit()
            except Exception as merge_err:
                db.session.rollback()
                logging.error(f"Could not update vetting log for candidate {candidate_id}: {str(merge_err)}")
            return vetting_log
    

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
                self._set_last_run_timestamp(cycle_start)
                return summary
            
            # ═══════════════════════════════════════════════════════════════
            # DEDUPLICATE: Remove duplicate candidate IDs before parallel
            # processing. Multiple ParsedEmail records can reference the same
            # Bullhorn candidate — parallel threads for the same ID collide
            # on vetting log creation, causing session detachment errors.
            # ═══════════════════════════════════════════════════════════════
            seen_candidate_ids = set()
            unique_candidates = []
            for cand in candidates:
                cid = cand.get('id')
                if cid not in seen_candidate_ids:
                    seen_candidate_ids.add(cid)
                    unique_candidates.append(cand)
            if len(unique_candidates) < len(candidates):
                logging.info(f"🔄 Deduped candidates: {len(candidates)} → {len(unique_candidates)} unique Bullhorn IDs")
            candidates = unique_candidates
            
            # ═══════════════════════════════════════════════════════════════
            # BATCH OPTIMIZATION: Load tearsheet jobs ONCE for the entire batch
            # Previously this was called inside process_candidate() for EVERY
            # candidate, causing 300+ redundant Bullhorn API calls per batch.
            # ═══════════════════════════════════════════════════════════════
            batch_jobs = self.get_active_jobs_from_tearsheets()
            logging.info(f"📦 Batch job cache: {len(batch_jobs)} jobs loaded once for {len(candidates)} candidates")
            
            # ═══════════════════════════════════════════════════════════════
            # PARALLEL CANDIDATE PROCESSING
            # Process up to 5 candidates simultaneously. Each candidate's
            # AI analysis is independent, so parallelism is safe.
            # Post-processing (notes, notifications) runs sequentially
            # after all candidates complete to avoid Bullhorn API conflicts.
            # ═══════════════════════════════════════════════════════════════
            max_parallel_candidates = 5
            candidate_results = []
            
            from app import app as flask_app
            
            def process_candidate_thread(cand):
                with flask_app.app_context():
                    db.session.remove()
                    db.session().expire_on_commit = False
                    try:
                        vlog = self.process_candidate(cand, cached_jobs=batch_jobs)
                        if vlog:
                            db.session.expunge(vlog)
                            return {
                                'candidate': cand,
                                'vetting_log_id': vlog.id,
                                'status': vlog.status,
                                'is_qualified': vlog.is_qualified,
                                'note_created': vlog.note_created,
                                'bullhorn_candidate_id': vlog.bullhorn_candidate_id,
                                'error': None
                            }
                        return {'candidate': cand, 'vetting_log_id': None, 'status': None, 'is_qualified': False, 'note_created': False, 'bullhorn_candidate_id': None, 'error': None}
                    except Exception as e:
                        db.session.rollback()
                        return {'candidate': cand, 'vetting_log_id': None, 'status': None, 'is_qualified': False, 'note_created': False, 'bullhorn_candidate_id': None, 'error': str(e)}
            
            with ThreadPoolExecutor(max_workers=max_parallel_candidates) as executor:
                futures = {executor.submit(process_candidate_thread, c): c for c in candidates}
                for future in as_completed(futures):
                    candidate_results.append(future.result())
            
            logging.info(f"✅ Parallel candidate processing complete: {len(candidate_results)} candidates")
            
            for result in candidate_results:
                candidate = result['candidate']
                vetting_log_id = result['vetting_log_id']
                status = result['status']
                error = result['error']
                
                if error:
                    error_msg = f"Error processing candidate {candidate.get('id')}: {error}"
                    logging.error(error_msg)
                    summary['errors'].append(error_msg)
                    continue
                
                try:
                    vetting_log = None
                    if vetting_log_id and status == 'completed':
                        vetting_log = CandidateVettingLog.query.get(vetting_log_id)
                        if not vetting_log:
                            logging.error(f"Vetting log {vetting_log_id} not found for post-processing")
                            continue
                        
                        summary['candidates_processed'] += 1
                        
                        if vetting_log.is_qualified:
                            summary['candidates_qualified'] += 1
                        
                        if not vetting_log.note_created:
                            if self.create_candidate_note(vetting_log):
                                summary['notes_created'] += 1
                        else:
                            logging.info(f"⏭️ Skipping note creation - already exists for candidate {vetting_log.bullhorn_candidate_id}")
                        
                        if vetting_log.is_qualified:
                            notif_count = self.send_recruiter_notifications(vetting_log)
                            summary['notifications_sent'] += notif_count
                    
                    parsed_email_id = candidate.get('_parsed_email_id')
                    if parsed_email_id:
                        vetting_success = (
                            vetting_log is not None and
                            vetting_log.resume_text is not None and
                            len(vetting_log.resume_text or '') > 10 and
                            not vetting_log.error_message
                        )
                        self._mark_application_vetted(parsed_email_id, success=vetting_success)
                            
                except Exception as e:
                    db.session.rollback()
                    error_msg = f"Error post-processing candidate {candidate.get('id')}: {str(e)}"
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
