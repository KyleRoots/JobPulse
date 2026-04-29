"""
Single-candidate processing pipeline — resume extraction, embedding pre-filter,
parallel job analysis, match record creation, and zero-score verification.
"""

import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

from concurrent.futures import ThreadPoolExecutor, as_completed

from app import db
from models import (
    CandidateVettingLog, CandidateJobMatch, JobVettingRequirements,
    EmbeddingFilterLog,
)
from utils.text_sanitization import sanitize_text

logger = logging.getLogger('candidate_vetting_service')


class CandidateProcessingMixin:
    """Mixin implementing the single-candidate vetting pipeline."""

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
        candidate_name = sanitize_text(f"{candidate.get('firstName', '')} {candidate.get('lastName', '')}".strip())
        candidate_email = sanitize_text(candidate.get('email', ''))

        logger.info(f"🔍 Processing candidate: {candidate_name} (ID: {candidate_id})")

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
            if 'UniqueViolation' in str(e) or 'unique constraint' in str(e).lower():
                existing_log = CandidateVettingLog.query.filter_by(
                    bullhorn_candidate_id=candidate_id
                ).first()
                if existing_log:
                    if existing_log.highest_match_score == 0 and existing_log.status == 'completed':
                        logger.info(
                            f"🔄 Re-analyzing candidate {candidate_id} ({candidate_name}) — "
                            f"previous vetting had 0% scores (likely from aggressive filter threshold)"
                        )
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
                        logger.info(f"⏭️ Candidate {candidate_id} already vetted with score {existing_log.highest_match_score}%")
                        return existing_log
                else:
                    logger.error(f"Failed to create vetting log for candidate {candidate_id}: {str(e)}")
                    return None
            else:
                logger.error(f"Failed to create vetting log for candidate {candidate_id}: {str(e)}")
                return None

        try:
            with type(self)._bullhorn_lock:
                submission = self.get_candidate_job_submission(candidate_id)
            if submission:
                job_order = submission.get('jobOrder', {})
                vetting_log.applied_job_id = job_order.get('id')
                vetting_log.applied_job_title = sanitize_text(job_order.get('title'))

            resume_text = None

            raw_description = candidate.get('description') if candidate else None
            logger.info(f"📄 Candidate description field present: {bool(raw_description)}, type: {type(raw_description).__name__}, length: {len(str(raw_description)) if raw_description else 0}")

            if raw_description:
                description = str(raw_description).strip()
                description = re.sub(r'<style[^>]*>.*?</style>', ' ', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'<script[^>]*>.*?</script>', ' ', description, flags=re.DOTALL | re.IGNORECASE)
                description = re.sub(r'<[^>]+>', ' ', description)
                description = re.sub(r'\s+', ' ', description).strip()

                logger.info(f"📄 After cleaning: {len(description)} chars, first 200: {description[:200]}")

                if len(description) >= 100:
                    resume_text = sanitize_text(description)
                    logger.info(f"📄 Using candidate description field: {len(resume_text)} chars")
                else:
                    logger.info(f"Description too short ({len(description)} chars), will try file download")
            else:
                logger.info(f"📄 No description field in candidate data - will try file download")

            if not resume_text:
                logger.info("Falling back to resume file download...")
                with type(self)._bullhorn_lock:
                    file_content, filename = self.get_candidate_resume(candidate_id)
                if file_content and filename:
                    resume_text = self.extract_resume_text(file_content, filename)
                    if resume_text:
                        logger.info(f"Extracted {len(resume_text)} characters from resume file")
                    else:
                        logger.warning(f"Could not extract text from resume: {filename}")
                else:
                    logger.warning(f"No resume file found for candidate {candidate_id}")

            if resume_text:
                vetting_log.resume_text = resume_text[:50000]

            if cached_jobs is not None:
                jobs = list(cached_jobs)
            else:
                jobs = self.get_active_jobs_from_tearsheets()

            if vetting_log.applied_job_id:
                applied_in_tearsheets = any(
                    j.get('id') == vetting_log.applied_job_id for j in jobs
                )
                if not applied_in_tearsheets:
                    try:
                        with type(self)._bullhorn_lock:
                            applied_job_data = self._fetch_applied_job(
                                self._get_bullhorn_service(),
                                vetting_log.applied_job_id
                            )
                        if applied_job_data:
                            jobs.append(applied_job_data)
                            logger.info(
                                f"🎯 Injected applied job {vetting_log.applied_job_id} "
                                f"({applied_job_data.get('title', 'Unknown')}) — "
                                f"not in monitored tearsheets"
                            )
                        else:
                            logger.warning(
                                f"⚠️ Applied job {vetting_log.applied_job_id} could not be "
                                f"fetched (closed/invalid) — will proceed without it"
                            )
                    except Exception as e:
                        logger.warning(
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

            cached_resume_text = vetting_log.resume_text

            candidate_location = None
            if candidate and isinstance(candidate.get('address'), dict):
                candidate_location = candidate.get('address')
                loc_parts = [candidate_location.get('city', ''), candidate_location.get('state', ''),
                            candidate_location.get('countryName', '') or candidate_location.get('country', '')]
                loc_str = ', '.join(filter(None, loc_parts))
                if loc_str:
                    logger.info(f"📍 Candidate location from Bullhorn: {loc_str}")
                else:
                    logger.info("📍 Candidate has address field but no city/state/country - AI will infer from resume")
            else:
                logger.info("📍 No address in Bullhorn record - AI will infer location from resume")

            threshold = self.get_threshold()
            qualified_matches = []
            all_match_results = []

            if not cached_resume_text or len(cached_resume_text.strip()) < 50:
                logger.error(f"❌ CRITICAL: Resume text missing or too short for candidate {candidate_id}")
                logger.error(f"   Resume text length: {len(cached_resume_text) if cached_resume_text else 0}")
                vetting_log.status = 'completed'
                vetting_log.error_message = 'Resume text too short for analysis'
                db.session.commit()
                return vetting_log

            existing_job_ids = set()
            existing_matches = CandidateJobMatch.query.filter_by(vetting_log_id=vetting_log.id).all()
            for match in existing_matches:
                existing_job_ids.add(match.bullhorn_job_id)

            jobs_to_analyze = [job for job in jobs if job.get('id') not in existing_job_ids]

            if not jobs_to_analyze:
                logger.info(f"All {len(jobs)} jobs already analyzed for this candidate")
                vetting_log.status = 'completed'
                vetting_log.analyzed_at = datetime.utcnow()
                db.session.commit()
                return vetting_log

            pre_filter_count = len(jobs_to_analyze)
            candidate_filter_info = {
                'id': candidate_id,
                'name': candidate_name
            }

            applied_job_entry = None
            if vetting_log.applied_job_id:
                for j in jobs_to_analyze:
                    if j.get('id') == vetting_log.applied_job_id:
                        applied_job_entry = j
                        break

            non_applied_jobs = (
                [j for j in jobs_to_analyze if j.get('id') != vetting_log.applied_job_id]
                if applied_job_entry else jobs_to_analyze
            )

            try:
                filtered_jobs, filtered_count = self.embedding_service.filter_relevant_jobs(
                    cached_resume_text, non_applied_jobs,
                    candidate_filter_info, vetting_log.id
                )

                if applied_job_entry:
                    if applied_job_entry not in filtered_jobs:
                        filtered_jobs.insert(0, applied_job_entry)
                        logger.info(
                            f"🎯 Applied job {vetting_log.applied_job_id} "
                            f"({applied_job_entry.get('title', 'Unknown')}) protected "
                            f"from embedding pre-filter — guaranteed GPT analysis"
                        )
                    else:
                        logger.info(
                            f"🎯 Applied job {vetting_log.applied_job_id} passed "
                            f"embedding filter naturally"
                        )

                jobs_to_analyze = filtered_jobs

                if filtered_count > 0:
                    logger.info(
                        f"🔍 Embedding pre-filter: {pre_filter_count} → {len(jobs_to_analyze)} jobs "
                        f"({filtered_count} filtered out)"
                    )
            except Exception as e:
                logger.error(f"⚠️ Embedding pre-filter error (bypassing filter): {str(e)}")

            if not jobs_to_analyze:
                logger.warning(
                    f"⚠️ Embedding pre-filter blocked ALL {pre_filter_count} jobs for "
                    f"candidate {candidate_id} ({candidate_name}). "
                    f"Falling back to top 5 jobs by similarity to avoid 0% scores."
                )
                try:
                    filter_logs = EmbeddingFilterLog.query.filter_by(
                        vetting_log_id=vetting_log.id
                    ).order_by(EmbeddingFilterLog.similarity_score.desc()).limit(5).all()

                    if filter_logs:
                        top_job_ids = {log.bullhorn_job_id for log in filter_logs}
                        jobs_to_analyze = [
                            job for job in jobs
                            if job.get('id') in top_job_ids
                        ]
                        top_sims = [f"{log.job_title}: {log.similarity_score:.4f}" for log in filter_logs]
                        logger.info(
                            f"🔄 Fallback: passing top {len(jobs_to_analyze)} jobs to GPT: "
                            f"{', '.join(top_sims)}"
                        )
                except Exception as fb_e:
                    logger.error(f"Fallback failed: {str(fb_e)}")

                if not jobs_to_analyze:
                    logger.info(f"All jobs filtered by embedding pre-filter for candidate {candidate_id} — no GPT calls needed")
                    vetting_log.status = 'completed'
                    vetting_log.analyzed_at = datetime.utcnow()
                    db.session.commit()
                    return vetting_log

            logger.info(f"🚀 Parallel analysis of {len(jobs_to_analyze)} jobs (skipping {len(existing_job_ids)} already analyzed)")
            logger.info(f"📄 Resume: {len(cached_resume_text)} chars, First 200: {cached_resume_text[:200]}")

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
                    logger.error(f"Error batch-fetching job requirements: {str(e)}")

            logger.info(f"📋 Pre-fetched requirements for {len(job_requirements_cache)} jobs")

            self.model = self._get_layer2_model()
            logger.info(f"🤖 Layer 2 model: {self.model}")

            escalation_range = self._get_escalation_range()
            global_threshold = self.get_threshold()
            prefetched_global_reqs = self._get_global_custom_requirements() or ''

            job_threshold_cache = {}
            job_prestige_boost_cache = {}
            try:
                batch_threshold_reqs = JobVettingRequirements.query.filter(
                    JobVettingRequirements.bullhorn_job_id.in_(batch_job_ids)
                ).all()
                for req in batch_threshold_reqs:
                    if req.vetting_threshold is not None:
                        job_threshold_cache[req.bullhorn_job_id] = float(req.vetting_threshold)
                    if req.employer_prestige_boost:
                        job_prestige_boost_cache[req.bullhorn_job_id] = True
            except Exception as e:
                logger.error(f"Error pre-fetching job thresholds: {str(e)}")

            def analyze_single_job(job_with_req):
                """Analyze one job match - called in parallel threads.

                Layer 2: Uses self.model (configurable via VettingConfig).
                Layer 3: If score falls in escalation range, re-analyzes with Layer 3 model.

                IMPORTANT: This runs in a ThreadPoolExecutor thread WITHOUT Flask app context.
                ALL database access must use pre-fetched values from the main thread.
                """
                job = job_with_req['job']
                prefetched_req = job_with_req['requirements']
                job_id = job.get('id')
                try:
                    analysis = self.analyze_candidate_job_match(
                        cached_resume_text, job, candidate_location,
                        prefetched_requirements=prefetched_req,
                        prefetched_global_requirements=prefetched_global_reqs
                    )

                    mini_score = analysis.get('match_score', 0)

                    esc_low, esc_high = escalation_range
                    if esc_low <= mini_score <= esc_high and self.model != 'gpt-5.4':
                        job_title = job.get('title', 'Unknown')
                        logger.info(
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

                            analysis['_escalation_data'] = {
                                'mini_score': mini_score,
                                'gpt4o_score': gpt4o_score,
                                'job_id': job_id,
                                'job_title': job_title
                            }

                            analysis = escalated_analysis
                            analysis['_escalation_data'] = {
                                'mini_score': mini_score,
                                'gpt4o_score': gpt4o_score,
                                'job_id': job_id,
                                'job_title': job_title
                            }

                        except Exception as esc_e:
                            logger.error(f"Escalation failed for job {job_id}: {str(esc_e)}")

                    return {
                        'job': job,
                        'job_id': job_id,
                        'analysis': analysis,
                        'error': None
                    }
                except Exception as e:
                    error_str = str(e)
                    logger.error(f"Error analyzing job {job_id}: {error_str}")
                    if '429' in error_str and 'quota' in error_str.lower():
                        type(self)._consecutive_quota_errors += 1
                    return {
                        'job': job,
                        'job_id': job_id,
                        'analysis': {'match_score': 0, 'match_summary': f'Analysis failed: {error_str}'},
                        'error': error_str
                    }

            jobs_with_requirements = [
                {'job': job, 'requirements': job_requirements_cache.get(job.get('id'), '')}
                for job in jobs_to_analyze
            ]

            analysis_results = []
            thread_cap = 8 if cached_jobs is not None else 15
            max_workers = min(thread_cap, len(jobs_to_analyze))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(analyze_single_job, jwr): jwr for jwr in jobs_with_requirements}

                for future in as_completed(futures):
                    result = future.result()
                    analysis_results.append(result)

            logger.info(f"✅ Parallel analysis complete: {len(analysis_results)} jobs processed")

            for result in analysis_results:
                job = result['job']
                job_id = result['job_id']
                analysis = result['analysis']

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

                recruiter_name = ', '.join(recruiter_names) if recruiter_names else ''
                recruiter_email = ', '.join(recruiter_emails) if recruiter_emails else ''
                recruiter_id = int(recruiter_ids[0]) if recruiter_ids else None

                is_applied_job = vetting_log.applied_job_id == job_id if vetting_log.applied_job_id else False

                job_threshold = job_threshold_cache.get(job_id, global_threshold)

                _prestige_employer = analysis.get('_prestige_employer')
                _prestige_boost_applied = False
                _final_score = analysis.get('match_score', 0)
                if (_prestige_employer
                    and job_prestige_boost_cache.get(job_id)
                    and not analysis.get('is_location_barrier', False)
                    and 'location mismatch' not in (analysis.get('gaps_identified', '') or '').lower()):
                    from screening.prompt_builder import PRESTIGE_BOOST_POINTS
                    _original = _final_score
                    _final_score = min(100, _final_score + PRESTIGE_BOOST_POINTS)
                    _prestige_boost_applied = True
                    logger.info(
                        f"  🏢 Prestige boost: {_prestige_employer} detected, "
                        f"score {_original}→{_final_score} (+{PRESTIGE_BOOST_POINTS}pts) for job {job_id}"
                    )

                match_record = CandidateJobMatch(
                    vetting_log_id=vetting_log.id,
                    bullhorn_job_id=job_id,
                    job_title=sanitize_text(job.get('title', '')),
                    job_location=sanitize_text(job.get('address', {}).get('city', '') if isinstance(job.get('address'), dict) else ''),
                    tearsheet_id=job.get('tearsheet_id'),
                    tearsheet_name=sanitize_text(job.get('tearsheet_name', '')),
                    recruiter_name=sanitize_text(recruiter_name),
                    recruiter_email=sanitize_text(recruiter_email),
                    recruiter_bullhorn_id=recruiter_id,
                    match_score=_final_score,
                    technical_score=analysis.get('technical_score'),
                    is_qualified=(_final_score >= job_threshold) and not analysis.get('is_location_barrier', False),
                    is_applied_job=is_applied_job,
                    match_summary=sanitize_text(analysis.get('match_summary', '')),
                    skills_match=sanitize_text(analysis.get('skills_match', '')),
                    experience_match=sanitize_text(self._build_experience_match(analysis)),
                    gaps_identified=sanitize_text(analysis.get('gaps_identified', '')),
                    years_analysis_json=sanitize_text(analysis.get('_years_analysis_json')),
                    prestige_employer=sanitize_text(_prestige_employer),
                    prestige_boost_applied=_prestige_boost_applied,
                )

                db.session.add(match_record)
                all_match_results.append(match_record)

                threshold_note = f" (threshold: {int(job_threshold)}%)" if job_threshold != threshold else ""
                if match_record.is_qualified:
                    qualified_matches.append(match_record)
                    logger.info(f"  ✅ Match: {job.get('title')} - {analysis.get('match_score')}%{threshold_note}")
                else:
                    if analysis.get('is_location_barrier', False) and analysis.get('match_score', 0) >= job_threshold:
                        logger.info(
                            f"  📍 Location barrier override: {job.get('title')} scored {analysis.get('match_score')}% "
                            f"(>= {int(job_threshold)}% threshold) but is_qualified=False due to location mismatch"
                        )
                    logger.info(f"  ❌ No match: {job.get('title')} - {analysis.get('match_score')}%{threshold_note}")
                    if analysis.get('match_score', 0) == 0:
                        summary = analysis.get('match_summary', 'no summary')[:200]
                        gaps = analysis.get('gaps_identified', 'no gaps')[:200]
                        logger.warning(f"    🔬 0% diagnostic: summary={summary} | gaps={gaps}")

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
                        logger.warning(f"Failed to save requirements for job {deferred['job_id']}: {save_err}")

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
                        logger.warning(f"Failed to save escalation log for job {esc_data['job_id']}: {esc_save_err}")

            if all_match_results and all(m.match_score == 0 for m in all_match_results):
                logger.info(
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
                                    match_record.match_summary = sanitize_text(
                                        f"[Verified] {reverify_result.get('revised_summary', match_record.match_summary)}"
                                    )
                                    match_record.gaps_identified = sanitize_text(
                                        reverify_result.get(
                                            'revised_gaps', match_record.gaps_identified
                                        )
                                    )
                                    job_threshold = job_threshold_cache.get(
                                        match_record.bullhorn_job_id, global_threshold
                                    )
                                    match_record.is_qualified = new_score >= job_threshold
                                    if match_record.is_qualified:
                                        qualified_matches.append(match_record)
                                    logger.info(
                                        f"  ✅ Re-verified {job.get('title')}: 0% → {new_score}% "
                                        f"(reason: {reverify_result.get('revision_reason', 'N/A')[:100]})"
                                    )
                                else:
                                    logger.info(
                                        f"  ✔️ Confirmed 0% for {job.get('title')}: "
                                        f"{reverify_result.get('confidence_reason', 'AI confirmed non-fit')[:100]}"
                                    )
                            except Exception as rv_err:
                                logger.warning(
                                    f"  ⚠️ Re-verification failed for job {match_record.bullhorn_job_id}: {rv_err}"
                                )
                except Exception as verify_err:
                    logger.warning(f"⚠️ Zero-score verification error for {candidate_name}: {verify_err}")

            vetting_log.status = 'completed'
            vetting_log.analyzed_at = datetime.utcnow()
            vetting_log.is_qualified = len(qualified_matches) > 0
            vetting_log.total_jobs_matched = len(qualified_matches)

            if all_match_results:
                vetting_log.highest_match_score = max(m.match_score for m in all_match_results)

            db.session.commit()

            logger.info(f"✅ Completed analysis for {candidate_name} (ID: {candidate_id}): {len(qualified_matches)} qualified matches out of {len(all_match_results)} jobs")

            try:
                from vetting_audit_service import backfill_revet_new_score
                backfill_revet_new_score(candidate_id, vetting_log=vetting_log)
            except Exception as backfill_err:
                logger.warning(
                    f"⚠️ Audit revet_new_score back-fill failed for "
                    f"candidate {candidate_id}: {backfill_err!r}"
                )

            return vetting_log

        except Exception as e:
            logger.error(f"Error processing candidate {candidate_id}: {str(e)}")
            try:
                vetting_log = db.session.merge(vetting_log)
                vetting_log.status = 'failed'
                vetting_log.error_message = sanitize_text(str(e)[:500])
                vetting_log.retry_count += 1
                db.session.commit()
            except Exception as merge_err:
                db.session.rollback()
                logger.error(f"Could not update vetting log for candidate {candidate_id}: {str(merge_err)}")
            return vetting_log
