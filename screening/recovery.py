"""
Recovery - Auto-retry safeguards for stuck/failed vetting runs.

Contains:
- _reset_zero_score_failures: Resets candidates with 0% scores (API failure recovery)
- _reset_stuck_processing: Reports stale 'processing' records; recovers only explicitly listed ones
- _handle_quota_exhaustion: Auto-disables vetting on OpenAI quota exhaustion
"""

import logging
logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from app import db
from sqlalchemy import func
from models import CandidateJobMatch, CandidateVettingLog, ParsedEmail, VettingConfig
from email_service import EmailService


class RecoveryMixin:
    """Auto-retry safeguards for stuck/failed vetting runs."""

    MAX_ZERO_SCORE_RETRIES = 2

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
        - Tracks retry count on ParsedEmail; blocks after MAX_ZERO_SCORE_RETRIES
          to prevent endless recycling of persistently failing candidates
        """
        try:
            from models import CandidateVettingLog, CandidateJobMatch, ParsedEmail
            from models import EmbeddingFilterLog, EscalationLog
            from sqlalchemy import func
            
            cutoff = datetime.utcnow() - timedelta(minutes=10)
            
            zero_logs = CandidateVettingLog.query.filter(
                CandidateVettingLog.highest_match_score == 0,
                CandidateVettingLog.status == 'completed',
                CandidateVettingLog.created_at < cutoff,
                CandidateVettingLog.retry_blocked != True
            ).limit(50).all()
            
            if not zero_logs:
                return
            
            reset_count = 0
            blocked_count = 0
            for log in zero_logs:
                total_matches = db.session.query(func.count(CandidateJobMatch.id)).filter(
                    CandidateJobMatch.vetting_log_id == log.id
                ).scalar()
                
                non_zero = db.session.query(func.count(CandidateJobMatch.id)).filter(
                    CandidateJobMatch.vetting_log_id == log.id,
                    CandidateJobMatch.match_score > 0
                ).scalar()
                
                if non_zero > 0 or (total_matches > 0 and not log.error_message):
                    continue
                
                candidate_id = log.bullhorn_candidate_id
                log_id = log.id
                
                max_retry = db.session.query(func.coalesce(func.max(ParsedEmail.vetting_retry_count), 0)).filter(
                    ParsedEmail.bullhorn_candidate_id == candidate_id
                ).scalar()
                
                if max_retry >= self.MAX_ZERO_SCORE_RETRIES:
                    log.retry_blocked = True
                    log.retry_block_reason = (
                        f"Auto-blocked after {max_retry + 1} consecutive 0% failures. "
                        f"Likely unparseable resume or persistent API error."
                    )
                    log.status = 'failed'
                    blocked_count += 1
                    logger.info(
                        f"🚫 Retry-blocked candidate {candidate_id} after {max_retry + 1} "
                        f"consecutive 0% failures (vetting log {log_id})"
                    )
                    continue
                
                CandidateJobMatch.query.filter_by(vetting_log_id=log_id).delete()
                EmbeddingFilterLog.query.filter_by(vetting_log_id=log_id).delete()
                EscalationLog.query.filter_by(vetting_log_id=log_id).delete()
                
                db.session.delete(log)
                
                ParsedEmail.query.filter_by(
                    bullhorn_candidate_id=candidate_id
                ).update({
                    'vetted_at': None,
                    'vetting_retry_count': ParsedEmail.vetting_retry_count + 1
                })
                
                reset_count += 1
            
            if reset_count > 0 or blocked_count > 0:
                db.session.commit()
                parts = []
                if reset_count > 0:
                    parts.append(f"reset {reset_count}")
                if blocked_count > 0:
                    parts.append(f"blocked {blocked_count}")
                logger.info(f"🔄 Auto-retry: {', '.join(parts)} candidates with 0% scores (API failure recovery)")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in zero-score auto-retry: {str(e)}")
    
    def _reset_stuck_processing(self, explicit_log_ids=None):
        """Surface vetting logs stuck in 'processing'; recover only on request.

        A 'processing' log whose lease heartbeat (``updated_at``, or
        ``created_at`` when it never heartbeated) is more than
        ``LEASE_TIMEOUT_MINUTES`` old has unresolved ownership: its worker
        may have died, or may be alive behind a stalled connection with paid
        calls in flight. Age alone does not tell the two apart, so the
        scheduled call (``explicit_log_ids=None``) changes nothing: it logs
        the stale ids and returns them. Their emails and audits stay pending
        until an operator recovers them.

        ``explicit_log_ids`` is that explicit recovery: each listed log that
        is still 'processing' and still stale is ended by
        ``recover_expired_lease`` under the candidate run lock (deleted
        without job matches, marked 'failed' with partial matches kept; its
        own ParsedEmail re-queued). A worker that is in fact still alive
        cannot commit afterwards; costs it already incurred are not undone.
        Returns the recovered ids. Max 50 per call.
        """
        try:
            from models import CandidateVettingLog
            from sqlalchemy import and_, or_
            from vetting_audit_service.run_claim import (
                LEASE_TIMEOUT_MINUTES, recover_expired_lease,
            )

            cutoff = datetime.utcnow() - timedelta(minutes=LEASE_TIMEOUT_MINUTES)

            query = db.session.query(CandidateVettingLog.id).filter(
                CandidateVettingLog.status == 'processing',
                or_(
                    CandidateVettingLog.updated_at < cutoff,
                    and_(
                        CandidateVettingLog.updated_at.is_(None),
                        CandidateVettingLog.created_at < cutoff,
                    ),
                ),
            )
            if explicit_log_ids is not None:
                query = query.filter(CandidateVettingLog.id.in_(list(explicit_log_ids)))
            stuck_ids = [row_id for (row_id,) in query.order_by(CandidateVettingLog.id).limit(50)]
            db.session.rollback()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in stuck-processing check: {str(e)}")
            return []

        if explicit_log_ids is None:
            if stuck_ids:
                logger.warning(
                    f"⚠️ {len(stuck_ids)} vetting log(s) stuck in 'processing' with "
                    f"unresolved ownership, awaiting explicit recovery: {stuck_ids}"
                )
            return stuck_ids

        recovered = []
        for log_id in stuck_ids:
            try:
                if recover_expired_lease(log_id, cutoff) is not None:
                    recovered.append(log_id)
            except Exception as e:
                db.session.rollback()
                logger.error(f"Error recovering stuck vetting log {log_id}: {str(e)}")

        if recovered:
            logger.info(f"🔄 Explicit recovery: ended {len(recovered)} stuck 'processing' vetting log(s): {recovered}")
        return recovered
    
    def _handle_quota_exhaustion(self):
        """Handle OpenAI quota exhaustion: auto-disable vetting and send alert email.
        
        Called when 3+ consecutive quota errors are detected in a single vetting cycle.
        Prevents the system from creating further 0% notes in Bullhorn.
        """
        if type(self)._quota_alert_sent:
            return  # Already alerted this outage
        
        try:
            # Auto-disable vetting
            config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
            if config:
                config.setting_value = 'false'
                db.session.commit()
                logger.warning("⛔ Scout Screening auto-disabled due to OpenAI quota exhaustion")
            
            # Send alert email
            try:
                from models import EmailDeliveryLog
                email_svc = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
                alert_email = self._get_admin_notification_email() or 'kroots@myticas.com'
                
                subject = "⚠️ Scout Screening Auto-Disabled — OpenAI Quota Exhausted"
                message = (
                    "ALERT: Scout Screening has been automatically disabled.\n\n"
                    "WHAT'S HAPPENING:\n"
                    f"  {type(self)._consecutive_quota_errors} consecutive OpenAI API calls "
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
                logger.info(f"📧 Quota exhaustion alert sent to {alert_email}")
            except Exception as email_err:
                logger.error(f"Failed to send quota alert email: {str(email_err)}")
            
            type(self)._quota_alert_sent = True
            
        except Exception as e:
            logger.error(f"Error handling quota exhaustion: {str(e)}")

