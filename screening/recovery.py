"""
Recovery - Auto-retry safeguards for stuck/failed vetting runs.

Contains:
- _reset_zero_score_failures: Resets candidates with 0% scores (API failure recovery)
- _reset_stuck_processing: Resets orphaned 'processing' records (deployment restart recovery)
- _handle_quota_exhaustion: Auto-disables vetting on OpenAI quota exhaustion
"""

import logging
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
                    logging.info(
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
                logging.info(f"🔄 Auto-retry: {', '.join(parts)} candidates with 0% scores (API failure recovery)")
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
        if type(self)._quota_alert_sent:
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
                logging.info(f"📧 Quota exhaustion alert sent to {alert_email}")
            except Exception as email_err:
                logging.error(f"Failed to send quota alert email: {str(email_err)}")
            
            type(self)._quota_alert_sent = True
            
        except Exception as e:
            logging.error(f"Error handling quota exhaustion: {str(e)}")

