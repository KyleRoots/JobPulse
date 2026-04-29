import os
import base64
import logging
from datetime import datetime, timedelta

from sendgrid.helpers.mail import (
    Mail, Email, To, Cc, Bcc, Content, Attachment, Header, ReplyTo,
)

logger = logging.getLogger(__name__)


class _EmailServiceCore:

    def __init__(self, db=None, EmailDeliveryLog=None):
        self.from_email = "noreply@scoutgenius.ai"
        self.db = db
        self.EmailDeliveryLog = EmailDeliveryLog
        self.api_key = os.environ.get('SENDGRID_API_KEY')
        if not self.api_key:
            logger.error('SENDGRID_API_KEY environment variable not set')
            return

        import email_service as _pkg
        self.sg = _pkg.SendGridAPIClient(self.api_key)

    def _check_recent_notification(self, notification_type: str, recipient_email: str, 
                                 monitor_name: str = None, schedule_name: str = None,
                                 job_id: str = None,
                                 minutes_threshold: int = 5) -> bool:
        """
        Check if a similar notification was sent recently to prevent duplicates
        
        Args:
            notification_type: Type of notification to check
            recipient_email: Email address to check
            monitor_name: Monitor name for bullhorn notifications
            schedule_name: Schedule name for processing notifications
            job_id: Job ID for new_job_notification deduplication
            minutes_threshold: Time window in minutes to check for duplicates
            
        Returns:
            bool: True if recent notification found (duplicate), False if safe to send
        """
        try:
            if not self.db or not self.EmailDeliveryLog:
                # If no database connection, allow sending (fail-safe approach)
                return False
            
            # Calculate cutoff time
            cutoff_time = datetime.utcnow() - timedelta(minutes=minutes_threshold)
            
            # Build base query
            query = self.EmailDeliveryLog.query.filter(
                self.EmailDeliveryLog.notification_type == notification_type,
                self.EmailDeliveryLog.recipient_email == recipient_email,
                self.EmailDeliveryLog.delivery_status == 'sent',
                self.EmailDeliveryLog.sent_at >= cutoff_time
            )
            
            # Add specific filters based on notification type
            if notification_type == 'bullhorn_notification' and monitor_name:
                query = query.filter(
                    self.EmailDeliveryLog.changes_summary.contains(f"Monitor: {monitor_name}")
                )
            elif notification_type in ['scheduled_processing', 'processing_error'] and schedule_name:
                query = query.filter(
                    self.EmailDeliveryLog.schedule_name == schedule_name
                )
            elif notification_type == 'new_job_notification' and job_id:
                query = query.filter(
                    self.EmailDeliveryLog.job_id == str(job_id)
                )
            
            recent_notification = query.first()
            
            if recent_notification:
                logger.info(f"DUPLICATE PREVENTION: Blocking duplicate {notification_type} notification to {recipient_email} "
                           f"(last sent: {recent_notification.sent_at}, within {minutes_threshold}min threshold)")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking for recent notifications: {str(e)}")
            # Fail-safe: allow sending if check fails
            return False

    def _deduplicate_job_list(self, job_list):
        """
        Remove duplicate jobs from a list based on job ID
        
        Args:
            job_list: List of job objects (dicts or other formats)
            
        Returns:
            list: Deduplicated job list
        """
        if not job_list:
            return []
        
        seen_ids = set()
        deduplicated = []
        
        for job in job_list:
            try:
                # Extract job ID from different possible formats
                job_id = None
                if isinstance(job, dict):
                    job_id = job.get('id')
                elif hasattr(job, 'id'):
                    job_id = job.id
                elif isinstance(job, (str, int)):
                    job_id = str(job)
                
                # Only add if we haven't seen this job ID before
                if job_id and job_id not in seen_ids:
                    seen_ids.add(job_id)
                    deduplicated.append(job)
                elif not job_id:
                    # If no ID found, add anyway to avoid losing data
                    deduplicated.append(job)
                else:
                    logger.info(f"DEDUPLICATION: Removed duplicate job ID {job_id} from notification")
                    
            except Exception as e:
                logger.error(f"Error deduplicating job: {e}")
                # Add job anyway to avoid losing data
                deduplicated.append(job)
        
        original_count = len(job_list)
        final_count = len(deduplicated)
        
        if original_count != final_count:
            logger.info(f"DEDUPLICATION: Reduced {original_count} jobs to {final_count} (removed {original_count - final_count} duplicates)")
        
        return deduplicated

    def send_notification_email(self, to_email: str, subject: str, message: str, 
                                notification_type: str = 'generic') -> bool:
        """
        Send a generic notification email (for environment monitoring, alerts, etc.)
        
        Args:
            to_email: Recipient email address
            subject: Email subject line
            message: Email message body (plain text)
            notification_type: Type of notification for logging purposes
            
        Returns:
            bool: True if sent successfully, False otherwise
        """
        try:
            if not self.api_key:
                logger.warning("SendGrid API key not configured - cannot send email")
                return False

            from_email_obj = Email(self.from_email)
            to_email_obj = To(to_email)
            content = Content("text/plain", message)
            mail = Mail(from_email_obj, to_email_obj, subject, content)

            response = self.sg.client.mail.send.post(request_body=mail.get())
            
            # Extract SendGrid message ID from response headers
            sendgrid_message_id = None
            if hasattr(response, 'headers') and 'X-Message-Id' in response.headers:
                sendgrid_message_id = response.headers['X-Message-Id']
            
            # Determine delivery status
            delivery_status = 'sent' if response.status_code == 202 else 'failed'
            error_msg = None if response.status_code == 202 else f"Status code: {response.status_code}"
            
            # Log email delivery
            self._log_email_delivery(
                notification_type=notification_type,
                recipient_email=to_email,
                delivery_status=delivery_status,
                sendgrid_message_id=sendgrid_message_id,
                error_message=error_msg,
                subject=subject,
                content=message[:500]  # Store first 500 chars of message
            )

            if response.status_code == 202:
                logger.info(f"Generic notification sent successfully to {to_email}: {subject}")
                return True
            else:
                logger.error(f"Failed to send generic notification: {response.status_code}")
                return False

        except Exception as e:
            # Log failed email delivery
            self._log_email_delivery(
                notification_type=notification_type,
                recipient_email=to_email,
                delivery_status='failed',
                error_message=str(e),
                subject=subject
            )
            logger.error(f"Failed to send generic notification: {str(e)}")
            return False

    def send_html_email(self, to_email: str, subject: str, html_content: str,
                        notification_type: str = 'html_email',
                        cc_emails: list = None,
                        bcc_emails: list = None,
                        in_reply_to: str = None,
                        references: str = None,
                        reply_to: str = None,
                        from_name: str = None,
                        from_email: str = None,
                        changes_summary: str = None,
                        message_id: str = None,
                        attachments: list = None):
        try:
            if not self.api_key:
                logger.warning("SendGrid API key not configured - cannot send email")
                return {'success': False, 'message_id': None}

            from_addr = from_email or self.from_email
            if from_name:
                from_email_obj = Email(from_addr, from_name)
            else:
                from_email_obj = Email(from_addr)

            message = Mail(
                from_email=from_email_obj,
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content)
            )
            
            # Add Reply-To header (for Scout Vetting inbound routing)
            if reply_to:
                message.reply_to = ReplyTo(reply_to)
            
            if message_id:
                message.header = Header('Message-ID', message_id)
            if in_reply_to or references:
                if in_reply_to:
                    message.header = Header('In-Reply-To', in_reply_to)
                refs = references or in_reply_to
                if refs:
                    message.header = Header('References', refs)
            
            # Add CC recipients if provided
            if cc_emails:
                for cc_email in cc_emails:
                    if cc_email and cc_email != to_email:  # Don't CC the primary recipient
                        message.add_cc(Cc(cc_email))
                logger.info(f"Adding CC recipients: {cc_emails}")
            
            # Add BCC recipients if provided
            if bcc_emails:
                for bcc_email in bcc_emails:
                    if bcc_email and bcc_email != to_email:  # Don't BCC the primary recipient
                        message.add_bcc(Bcc(bcc_email))
                logger.info(f"Adding BCC recipients: {bcc_emails}")

            if attachments:
                for att in attachments:
                    sg_attachment = Attachment()
                    sg_attachment.file_content = base64.b64encode(att['data']).decode('utf-8')
                    sg_attachment.file_type = att.get('content_type', 'application/octet-stream')
                    sg_attachment.file_name = att.get('filename', 'attachment')
                    sg_attachment.disposition = 'attachment'
                    message.add_attachment(sg_attachment)
                logger.info(f"Adding {len(attachments)} attachment(s) to email")

            response = self.sg.client.mail.send.post(request_body=message.get())
            
            # Extract SendGrid message ID from response headers
            sendgrid_message_id = None
            if hasattr(response, 'headers') and 'X-Message-Id' in response.headers:
                sendgrid_message_id = response.headers['X-Message-Id']
            
            # Determine delivery status
            delivery_status = 'sent' if response.status_code == 202 else 'failed'
            error_msg = None if response.status_code == 202 else f"Status code: {response.status_code}"
            
            # Log email delivery
            self._log_email_delivery(
                notification_type=notification_type,
                recipient_email=to_email,
                delivery_status=delivery_status,
                error_message=error_msg,
                subject=subject,
                sendgrid_message_id=sendgrid_message_id,
                changes_summary=changes_summary
            )
            
            if response.status_code == 202:
                logger.info(f"HTML email sent successfully to {to_email}")
                return {'success': True, 'message_id': sendgrid_message_id}
            else:
                logger.error(f"Failed to send HTML email: {response.status_code}")
                return {'success': False, 'message_id': None}
                
        except Exception as e:
            # Log failed delivery
            self._log_email_delivery(
                notification_type=notification_type,
                recipient_email=to_email,
                delivery_status='failed',
                error_message=str(e),
                subject=subject
            )
            logger.error(f"Failed to send HTML email: {str(e)}")
            return {'success': False, 'message_id': None}

    def _log_email_delivery(self, notification_type: str, job_id: str = None, job_title: str = None,
                          recipient_email: str = "", delivery_status: str = "sent", 
                          sendgrid_message_id: str = None, error_message: str = None,
                          schedule_name: str = None, changes_summary: str = None,
                          subject: str = None, content: str = None):
        """
        Log email delivery to database
        
        Args:
            notification_type: Type of notification ('job_added', 'job_removed', 'job_modified', 'scheduled_processing')
            job_id: Bullhorn job ID (null for scheduled processing)
            job_title: Job title for reference
            recipient_email: Email address notification was sent to
            delivery_status: 'sent', 'failed', or 'pending'
            sendgrid_message_id: SendGrid message ID for tracking
            error_message: Error details if delivery failed
            schedule_name: For scheduled processing notifications
            changes_summary: Summary of changes that triggered the notification
        """
        try:
            if not self.db or not self.EmailDeliveryLog:
                logger.warning("EmailService: Database connection or EmailDeliveryLog model not available for logging")
                return

            log_entry = self.EmailDeliveryLog(
                notification_type=notification_type,
                job_id=str(job_id) if job_id else None,
                job_title=job_title,
                recipient_email=recipient_email,
                delivery_status=delivery_status,
                sendgrid_message_id=sendgrid_message_id,
                error_message=error_message,
                schedule_name=schedule_name,
                changes_summary=changes_summary
            )
            
            self.db.session.add(log_entry)
            self.db.session.commit()
            
            logger.info(f"Email delivery logged: {notification_type} to {recipient_email} - Status: {delivery_status}")
            
        except Exception as e:
            logger.error(f"Failed to log email delivery: {str(e)}")
            if self.db:
                self.db.session.rollback()
