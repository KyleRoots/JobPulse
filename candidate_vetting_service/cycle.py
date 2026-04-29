"""
Vetting cycle orchestrator — multi-source candidate detection, batch
optimization, parallel processing, post-processing (notes & notifications).
"""

import logging
from datetime import datetime
from typing import Dict

from concurrent.futures import ThreadPoolExecutor, as_completed

from app import db
from models import CandidateVettingLog

logger = logging.getLogger('candidate_vetting_service')


class VettingCycleMixin:
    """Mixin implementing the full vetting cycle orchestration."""

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
        db.session.expire_all()

        if not self.is_enabled():
            logger.info("Candidate vetting is disabled")
            return {'status': 'disabled'}

        if not self._acquire_vetting_lock():
            logger.info("Skipping vetting cycle - another cycle is in progress")
            return {'status': 'skipped', 'reason': 'cycle_in_progress'}

        logger.info("🚀 Starting candidate vetting cycle")
        cycle_start = datetime.utcnow()

        self._reset_zero_score_failures()

        self._reset_stuck_processing()

        type(self)._consecutive_quota_errors = 0

        batch_size = self._get_batch_size()
        logger.info(f"Using batch size: {batch_size}")

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
            candidates = self.detect_unvetted_applications(limit=batch_size)

            if not candidates:
                logger.info("No ParsedEmail records to vet, falling back to legacy detection")
                candidates = self.detect_new_applicants(since_minutes=10)
                if candidates and len(candidates) > batch_size:
                    candidates = candidates[:batch_size]
                summary['detection_method'] = 'bullhorn_search'

            pandologic_candidates = self.detect_pandologic_candidates(since_minutes=10)
            if pandologic_candidates:
                logger.info(f"🔵 Adding {len(pandologic_candidates)} Pandologic candidates to vetting queue")

                existing_ids = {c.get('id') for c in candidates}
                for pando_candidate in pandologic_candidates:
                    if pando_candidate.get('id') not in existing_ids:
                        candidates.append(pando_candidate)
                        existing_ids.add(pando_candidate.get('id'))

                if summary['detection_method'] == 'parsed_email':
                    summary['detection_method'] = 'parsed_email+pandologic'
                else:
                    summary['detection_method'] = 'bullhorn_search+pandologic'

            matador_candidates = self.detect_matador_candidates(since_minutes=10)
            if matador_candidates:
                logger.info(f"🟣 Adding {len(matador_candidates)} Matador candidates to vetting queue")

                existing_ids = {c.get('id') for c in candidates}
                for matador_candidate in matador_candidates:
                    if matador_candidate.get('id') not in existing_ids:
                        candidates.append(matador_candidate)
                        existing_ids.add(matador_candidate.get('id'))

                if summary['detection_method'] in ('parsed_email', 'bullhorn_search'):
                    summary['detection_method'] = f"{summary['detection_method']}+matador"
                else:
                    summary['detection_method'] = f"{summary['detection_method']}+matador"

            pando_note_candidates = self.detect_pandologic_note_candidates(since_minutes=10)
            if pando_note_candidates:
                logger.info(
                    f"📝 Adding {len(pando_note_candidates)} PandoLogic-note "
                    f"candidates to vetting queue"
                )
                existing_ids = {c.get('id') for c in candidates}
                for cand in pando_note_candidates:
                    if cand.get('id') not in existing_ids:
                        candidates.append(cand)
                        existing_ids.add(cand.get('id'))
                summary['detection_method'] = f"{summary['detection_method']}+pando_note"

            summary['candidates_detected'] = len(candidates)

            if not candidates:
                logger.info("No new candidates to process")
                self._set_last_run_timestamp(cycle_start)
                return summary

            seen_candidate_ids = set()
            unique_candidates = []
            for cand in candidates:
                cid = cand.get('id')
                if cid not in seen_candidate_ids:
                    seen_candidate_ids.add(cid)
                    unique_candidates.append(cand)
            if len(unique_candidates) < len(candidates):
                logger.info(f"🔄 Deduped candidates: {len(candidates)} → {len(unique_candidates)} unique Bullhorn IDs")
            candidates = unique_candidates

            batch_jobs = self.get_active_jobs_from_tearsheets()
            logger.info(f"📦 Batch job cache: {len(batch_jobs)} jobs loaded once for {len(candidates)} candidates")

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

            logger.info(f"✅ Parallel candidate processing complete: {len(candidate_results)} candidates")

            for result in candidate_results:
                candidate = result['candidate']
                vetting_log_id = result['vetting_log_id']
                status = result['status']
                error = result['error']

                if error:
                    error_msg = f"Error processing candidate {candidate.get('id')}: {error}"
                    logger.error(error_msg)
                    summary['errors'].append(error_msg)
                    continue

                try:
                    vetting_log = None
                    if vetting_log_id and status == 'completed':
                        vetting_log = CandidateVettingLog.query.get(vetting_log_id)
                        if not vetting_log:
                            logger.error(f"Vetting log {vetting_log_id} not found for post-processing")
                            continue

                        summary['candidates_processed'] += 1

                        if vetting_log.is_qualified:
                            summary['candidates_qualified'] += 1

                        if not vetting_log.note_created:
                            if self.create_candidate_note(vetting_log):
                                summary['notes_created'] += 1
                        else:
                            logger.info(f"⏭️ Skipping note creation - already exists for candidate {vetting_log.bullhorn_candidate_id}")

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
                    logger.error(error_msg)
                    summary['errors'].append(error_msg)

            self._set_last_run_timestamp(cycle_start)

            if type(self)._consecutive_quota_errors >= 3:
                self._handle_quota_exhaustion()
            elif type(self)._consecutive_quota_errors == 0:
                type(self)._quota_alert_sent = False

            logger.info(f"✅ Vetting cycle complete: {summary}")
            return summary

        except Exception as e:
            db.session.rollback()
            error_msg = f"Vetting cycle error: {str(e)}"
            logger.error(error_msg)
            summary['errors'].append(error_msg)
            return summary
        finally:
            self._release_vetting_lock()
