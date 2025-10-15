import os
import sys
from datetime import datetime, timedelta
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment
import base64
import logging


class EmailService:
    """Handles email notifications for XML processing"""

    def __init__(self, db=None, EmailDeliveryLog=None):
        self.api_key = os.environ.get('SENDGRID_API_KEY')
        if not self.api_key:
            logging.error('SENDGRID_API_KEY environment variable not set')
            return

        self.sg = SendGridAPIClient(self.api_key)
        self.from_email = "kroots@myticas.com"  # Verified sender email
        self.db = db
        self.EmailDeliveryLog = EmailDeliveryLog

    def _check_recent_notification(self, notification_type: str, recipient_email: str, 
                                 monitor_name: str = None, schedule_name: str = None, 
                                 minutes_threshold: int = 5) -> bool:
        """
        Check if a similar notification was sent recently to prevent duplicates
        
        Args:
            notification_type: Type of notification to check
            recipient_email: Email address to check
            monitor_name: Monitor name for bullhorn notifications
            schedule_name: Schedule name for processing notifications  
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
                self.EmailDeliveryLog.created_at >= cutoff_time
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
            
            recent_notification = query.first()
            
            if recent_notification:
                logging.info(f"DUPLICATE PREVENTION: Blocking duplicate {notification_type} notification to {recipient_email} "
                           f"(last sent: {recent_notification.created_at}, within {minutes_threshold}min threshold)")
                return True
            
            return False
            
        except Exception as e:
            logging.error(f"Error checking for recent notifications: {str(e)}")
            # Fail-safe: allow sending if check fails
            return False
    
    def send_automated_upload_notification(self, to_email: str, total_jobs: int, 
                                         upload_details: dict, status: str = "success") -> bool:
        """
        Send notification for automated XML uploads (every 30 minutes)
        
        Args:
            to_email: Recipient email address
            total_jobs: Number of jobs in the upload
            upload_details: Dictionary with upload information
            status: Status of the upload ("success" or "error")
        
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            # Check for recent duplicate notifications (prevent spam)
            if self._check_recent_notification('automated_upload', to_email, minutes_threshold=25):
                return True  # Already sent recently, skip
            
            # Create subject line
            if status == "success":
                subject = f"✅ Automated Upload Complete - {total_jobs} Jobs Updated"
            else:
                subject = f"❌ Automated Upload Failed - Manual Action Required"
            
            # Create HTML content
            upload_time = upload_details.get('execution_time', 'Unknown')
            next_upload = upload_details.get('next_upload', 'In 30 minutes')
            xml_size = upload_details.get('xml_size', 'Unknown')
            
            html_content = f"""
            <html>
                <head>
                    <style>
                        body {{ font-family: Arial, sans-serif; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                        .header {{ background-color: {'#28a745' if status == 'success' else '#dc3545'}; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
                        .content {{ background-color: #f8f9fa; padding: 20px; border-radius: 0 0 8px 8px; }}
                        .details {{ background-color: white; padding: 15px; border-radius: 8px; margin: 15px 0; }}
                        .status-success {{ color: #28a745; font-weight: bold; }}
                        .status-error {{ color: #dc3545; font-weight: bold; }}
                        .footer {{ margin-top: 20px; padding-top: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #666; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>{'🚀' if status == 'success' else '⚠️'} Automated XML Upload Report</h1>
                        </div>
                        <div class="content">
                            <h2>Upload Summary</h2>
                            <div class="details">
                                <p><strong>Upload Time:</strong> {upload_time}</p>
                                <p><strong>Total Jobs:</strong> {total_jobs}</p>
                                <p><strong>XML File Size:</strong> {xml_size}</p>
                                <p><strong>Status:</strong> 
                                    <span class="status-{'success' if status == 'success' else 'error'}">
                                        {'✅ Successfully Uploaded' if status == 'success' else '❌ Upload Failed'}
                                    </span>
                                </p>
            """
            
            if status == "success":
                html_content += f"""
                                <p><strong>Next Upload:</strong> {next_upload}</p>
                            </div>
                            <div class="details">
                                <h3>✅ Upload Details</h3>
                                <p>Your XML job feed has been automatically uploaded to the server with the latest job listings.</p>
                                <p>The system will continue to upload fresh data every 30 minutes to ensure your job feed stays current.</p>
                            </div>
                """
            else:
                error_message = upload_details.get('upload_error', 'Unknown error')
                html_content += f"""
                                <p><strong>Error:</strong> {error_message}</p>
                            </div>
                            <div class="details">
                                <h3>⚠️ Action Required</h3>
                                <p><strong>Upload Failed:</strong> {error_message}</p>
                                <p><strong>Manual Override:</strong> Please use the manual download feature to get the XML file and upload it manually until this issue is resolved.</p>
                                <ul>
                                    <li>Go to the application dashboard</li>
                                    <li>Click "Generate & Download XML"</li>
                                    <li>Upload the file manually to your server</li>
                                    <li>Check SFTP settings in the application</li>
                                </ul>
                            </div>
                """
            
            html_content += f"""
                            <div class="footer">
                                <p>This is an automated message from your Job Feed System.</p>
                                <p>You can disable automated uploads in the system settings if you prefer manual workflow.</p>
                            </div>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            # Create and send email
            message = Mail(
                from_email=self.from_email,
                to_emails=to_email,
                subject=subject,
                html_content=html_content
            )
            
            # Send the email
            response = self.sg.send(message)
            
            # Log delivery
            if self.db and self.EmailDeliveryLog:
                try:
                    log_entry = self.EmailDeliveryLog(
                        notification_type='automated_upload',
                        recipient_email=to_email,
                        subject=subject,
                        delivery_status='sent' if response.status_code in [200, 202] else 'failed',
                        sendgrid_message_id=response.headers.get('X-Message-Id', ''),
                        changes_summary=f"Jobs: {total_jobs}, Status: {status}, Upload: {upload_details.get('upload_success', False)}",
                        error_message=None if response.status_code in [200, 202] else f"HTTP {response.status_code}"
                    )
                    self.db.session.add(log_entry)
                    self.db.session.commit()
                except Exception as log_error:
                    logging.error(f"Failed to log automated upload notification delivery: {str(log_error)}")
            
            return response.status_code in [200, 202]
            
        except Exception as e:
            logging.error(f"Failed to send automated upload notification: {str(e)}")
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
                    logging.info(f"DEDUPLICATION: Removed duplicate job ID {job_id} from notification")
                    
            except Exception as e:
                logging.error(f"Error deduplicating job: {e}")
                # Add job anyway to avoid losing data
                deduplicated.append(job)
        
        original_count = len(job_list)
        final_count = len(deduplicated)
        
        if original_count != final_count:
            logging.info(f"DEDUPLICATION: Reduced {original_count} jobs to {final_count} (removed {original_count - final_count} duplicates)")
        
        return deduplicated

    def send_processing_notification(self,
                                     to_email: str,
                                     schedule_name: str,
                                     jobs_processed: int,
                                     xml_file_path: str,
                                     original_filename: str,
                                     sftp_upload_success: bool = True) -> bool:
        """
        Send email notification with processed XML file attachment
        
        Args:
            to_email: Recipient email address
            schedule_name: Name of the schedule that was processed
            jobs_processed: Number of jobs processed
            xml_file_path: Path to the processed XML file
            original_filename: Original filename to preserve
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            if not self.api_key:
                logging.error("EmailService: No SendGrid API key available")
                return False
            
            # Check for recent duplicate notifications
            if self._check_recent_notification('scheduled_processing', to_email, schedule_name=schedule_name):
                logging.info(f"DUPLICATE PREVENTION: Skipping duplicate processing notification for {schedule_name}")
                return True  # Return True since we're intentionally not sending (not an error)
            # Read the XML file for attachment
            with open(xml_file_path, 'rb') as f:
                xml_content = f.read()

            # Create email
            status_icon = "✅" if sftp_upload_success else "❌"
            status_text = "Completed" if sftp_upload_success else "Not Complete"
            subject = f"Scheduled Reference Number Update {status_text}: {schedule_name}"

            html_content = f"""
            <html>
            <body>
                <h2>XML Processing {status_text}</h2>
                <p>Your scheduled XML processing has been completed{' successfully' if sftp_upload_success else ' with issues'}.</p>
                
                <h3>Processing Details:</h3>
                <ul>
                    <li><strong>Schedule:</strong> {schedule_name}</li>
                    <li><strong>Jobs Processed:</strong> {jobs_processed}</li>
                    <li><strong>File:</strong> {original_filename}</li>
                    <li><strong>Status:</strong> {status_icon} {status_text}</li>
                    <li><strong>SFTP Upload:</strong> {'✅ Successful' if sftp_upload_success else '❌ Failed'}</li>
                </ul>
                
                <p>The updated XML file with new reference numbers is attached to this email.</p>
            </body>
            </html>
            """

            text_content = f"""
            XML Processing {status_text}
            
            Your scheduled XML processing has been completed{' successfully' if sftp_upload_success else ' with issues'}.
            
            Processing Details:
            - Schedule: {schedule_name}
            - Jobs Processed: {jobs_processed}
            - File: {original_filename}
            - Status: {status_text}
            - SFTP Upload: {'Successful' if sftp_upload_success else 'Failed'}
            
            The updated XML file with new reference numbers is attached to this email.
            
            JobPulse™ Processing & Automation System
            """

            # Create the email message
            message = Mail(from_email=Email(self.from_email),
                           to_emails=To(to_email),
                           subject=subject,
                           html_content=Content("text/html", html_content),
                           plain_text_content=Content("text/plain",
                                                      text_content))

            # Add attachment
            encoded_file = base64.b64encode(xml_content).decode()
            attachment = Attachment(file_content=encoded_file,
                                    file_type="application/xml",
                                    file_name=original_filename,
                                    disposition="attachment")
            message.attachment = attachment

            # Send email
            response = self.sg.send(message)
            
            # Extract SendGrid message ID from response headers
            sendgrid_message_id = None
            try:
                if hasattr(response, 'headers') and hasattr(response.headers, 'get'):
                    sendgrid_message_id = response.headers.get('X-Message-Id')
            except Exception:
                pass
            
            # Log email delivery
            delivery_status = 'sent' if response.status_code == 202 else 'failed'
            error_msg = None if response.status_code == 202 else f"SendGrid returned status code: {response.status_code}"
            
            self._log_email_delivery(
                notification_type='scheduled_processing',
                recipient_email=to_email,
                delivery_status=delivery_status,
                sendgrid_message_id=sendgrid_message_id,
                error_message=error_msg,
                schedule_name=schedule_name,
                changes_summary=f"Processed {jobs_processed} jobs with {'successful' if sftp_upload_success else 'failed'} SFTP upload"
            )

            if response.status_code == 202:
                logging.info(f"Email sent successfully to {to_email}")
                return True
            else:
                logging.error(
                    f"Failed to send email. Status code: {response.status_code}"
                )
                return False

        except Exception as e:
            # Log failed email delivery
            self._log_email_delivery(
                notification_type='scheduled_processing',
                recipient_email=to_email,
                delivery_status='failed',
                error_message=str(e),
                schedule_name=schedule_name,
                changes_summary=f"Processing attempt for {jobs_processed} jobs"
            )
            logging.error(f"Error sending email: {str(e)}")
            return False

    def send_processing_error_notification(self, to_email: str,
                                           schedule_name: str,
                                           error_message: str) -> bool:
        """
        Send email notification when processing fails
        
        Args:
            to_email: Recipient email address
            schedule_name: Name of the schedule that failed
            error_message: Error message details
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            if not self.api_key:
                logging.error("EmailService: No SendGrid API key available")
                return False
            
            # Check for recent duplicate notifications
            if self._check_recent_notification('processing_error', to_email, schedule_name=schedule_name):
                logging.info(f"DUPLICATE PREVENTION: Skipping duplicate error notification for {schedule_name}")
                return True  # Return True since we're intentionally not sending (not an error)
            
            subject = f"XML Processing Failed: {schedule_name}"

            html_content = f"""
            <html>
            <body>
                <h2>XML Processing Failed</h2>
                <p>There was an issue processing your scheduled XML file.</p>
                
                <h3>Error Details:</h3>
                <ul>
                    <li><strong>Schedule:</strong> {schedule_name}</li>
                    <li><strong>Status:</strong> ❌ Failed</li>
                    <li><strong>Error:</strong> {error_message}</li>
                </ul>
                
                <p>Please check your XML file and schedule configuration, then try again.</p>
                
                <p>JobPulse™ Processing & Automation System</p>
            </body>
            </html>
            """

            text_content = f"""
            XML Processing Failed
            
            There was an issue processing your scheduled XML file.
            
            Error Details:
            - Schedule: {schedule_name}
            - Status: Failed
            - Error: {error_message}
            
            Please check your XML file and schedule configuration, then try again.
            
            JobPulse™ Processing & Automation System
            """

            # Create the email message
            message = Mail(from_email=Email(self.from_email),
                           to_emails=To(to_email),
                           subject=subject,
                           html_content=Content("text/html", html_content),
                           plain_text_content=Content("text/plain",
                                                      text_content))

            # Send email
            response = self.sg.send(message)
            
            # Extract SendGrid message ID from response headers
            sendgrid_message_id = None
            try:
                if hasattr(response, 'headers') and hasattr(response.headers, 'get'):
                    sendgrid_message_id = response.headers.get('X-Message-Id')
            except Exception:
                pass
            
            # Log email delivery
            delivery_status = 'sent' if response.status_code == 202 else 'failed'
            error_msg = None if response.status_code == 202 else f"SendGrid returned status code: {response.status_code}"
            
            self._log_email_delivery(
                notification_type='processing_error',
                recipient_email=to_email,
                delivery_status=delivery_status,
                sendgrid_message_id=sendgrid_message_id,
                error_message=error_msg,
                schedule_name=schedule_name,
                changes_summary=f"Processing error for schedule: {schedule_name}"
            )

            if response.status_code == 202:
                logging.info(
                    f"Error notification sent successfully to {to_email}")
                return True
            else:
                logging.error(
                    f"Failed to send error notification. Status code: {response.status_code}"
                )
                return False

        except Exception as e:
            # Log failed email delivery
            self._log_email_delivery(
                notification_type='processing_error',
                recipient_email=to_email,
                delivery_status='failed',
                error_message=str(e),
                schedule_name=schedule_name,
                changes_summary=f"Processing error notification attempt for {schedule_name}"
            )
            logging.error(f"Error sending error notification: {str(e)}")
            return False

    def send_reference_number_refresh_notification(self, to_email: str, schedule_name: str,
                                                  total_jobs: int, refresh_details: dict,
                                                  status: str = "success", error_message: str = None) -> bool:
        """
        Send notification after reference number refresh automation completes
        
        Args:
            to_email: Recipient email address
            schedule_name: Name of the schedule that triggered the refresh
            total_jobs: Total number of jobs processed
            refresh_details: Dictionary with details about the refresh operation
            status: 'success' or 'error'
            error_message: Error details if status is 'error'
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            if not self.api_key:
                logging.error("EmailService: No SendGrid API key available")
                return False
                
            # Check for recent duplicates (30-minute threshold for reference number refresh)
            if self._check_recent_notification('reference_number_refresh', to_email, 
                                             schedule_name=schedule_name, minutes_threshold=30):
                logging.info(f"DUPLICATE PREVENTION: Skipping duplicate reference number refresh notification for {schedule_name}")
                return True  # Skip duplicate
            
            if status == "success":
                subject = f"Reference Number Refresh Complete"
                
                # Simple, basic email content
                html_content = f"""
                <html>
                <body>
                    <p>All {total_jobs} reference numbers have been refreshed.</p>
                </body>
                </html>
                """
                
                text_content = f"All {total_jobs} reference numbers have been refreshed."
            else:
                subject = f"❌ Reference Number Refresh Failed - {schedule_name}"
                
                html_content = f"""
                <html>
                <body>
                    <h2>Reference Number Refresh Failed ❌</h2>
                    <p>The automated reference number refresh encountered an error.</p>
                    
                    <h3>Error Details:</h3>
                    <ul>
                        <li><strong>Schedule:</strong> {schedule_name}</li>
                        <li><strong>Status:</strong> ❌ ERROR</li>
                        <li><strong>Failure Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</li>
                    </ul>
                    
                    <h3>Error Message:</h3>
                    <p style="background-color: #f8f8f8; padding: 10px; border-left: 4px solid #ff0000;">
                        {error_message or 'Unknown error occurred'}
                    </p>
                    
                    <p>Please check the system logs and retry the operation manually if needed.</p>
                    
                    <hr>
                    <p><em>This is an automated notification from the Job Feed Reference Number Refresh system.</em></p>
                </body>
                </html>
                """
                
                text_content = f"""
Reference Number Refresh Automation Failed

Schedule: {schedule_name}
Status: ❌ ERROR
Failure Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

Error Details:
{error_message or 'Unknown error occurred'}

Please check the system logs and retry the operation manually if needed.

---
This is an automated notification from the Job Feed Reference Number Refresh system.
                """

            # Create and send email
            message = Mail(
                from_email=Email(self.from_email),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content),
                plain_text_content=Content("text/plain", text_content)
            )

            response = self.sg.send(message)
            
            # Extract SendGrid message ID from response headers
            sendgrid_message_id = None
            try:
                if hasattr(response, 'headers') and hasattr(response.headers, 'get'):
                    sendgrid_message_id = response.headers.get('X-Message-Id')
            except Exception:
                pass
            
            # Log successful delivery
            delivery_status = 'sent' if response.status_code == 202 else 'failed'
            error_msg = None if response.status_code == 202 else f"SendGrid returned status code: {response.status_code}"
            
            self._log_email_delivery(
                notification_type='reference_number_refresh',
                recipient_email=to_email,
                subject=subject,
                content=text_content,
                delivery_status=delivery_status,
                sendgrid_message_id=sendgrid_message_id,
                error_message=error_msg,
                schedule_name=schedule_name,
                changes_summary=f"Jobs: {total_jobs}, Status: {status}"
            )
            
            if response.status_code == 202:
                logging.info(f"📧 Reference number refresh notification sent to {to_email} for {schedule_name}")
                return True
            else:
                logging.error(f"Failed to send reference number refresh notification. Status code: {response.status_code}")
                return False
            
        except Exception as e:
            logging.error(f"Failed to send reference number refresh notification: {e}")
            
            # Log failed delivery
            self._log_email_delivery(
                notification_type='reference_number_refresh',
                recipient_email=to_email,
                subject=f"Reference Number Refresh - {schedule_name}",
                content="Failed to send notification",
                delivery_status='failed',
                error_message=str(e),
                schedule_name=schedule_name,
                changes_summary=f"Jobs: {total_jobs}, Status: {status}"
            )
            
            return False

    def send_bullhorn_notification(self,
                                   to_email: str,
                                   monitor_name: str,
                                   added_jobs: list,
                                   removed_jobs: list,
                                   modified_jobs: list = [],
                                   summary: dict = {},
                                   xml_sync_info: dict = {},
                                   rapid_changes: dict = None,
                                   rapid_change_alert: str = None) -> bool:
        """
        Send email notification for Bullhorn tearsheet changes
        
        Args:
            to_email: Recipient email address
            monitor_name: Name of the monitor/tearsheet
            added_jobs: List of jobs that were added
            removed_jobs: List of jobs that were removed
            modified_jobs: List of jobs that were modified (optional)
            summary: Summary statistics of changes (optional)
            xml_sync_info: Information about XML file updates (optional)
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            if not self.api_key:
                logging.error("EmailService: No SendGrid API key available")
                return False
            
            # Check for recent duplicate notifications
            if self._check_recent_notification('bullhorn_notification', to_email, monitor_name=monitor_name):
                logging.info(f"DUPLICATE PREVENTION: Skipping duplicate Bullhorn notification for {monitor_name}")
                return True  # Return True since we're intentionally not sending (not an error)

            # Prepare default values and type checking
            if modified_jobs is None:
                modified_jobs = []
            if summary is None:
                summary = {}
            if xml_sync_info is None:
                xml_sync_info = {}
            # Ensure xml_sync_info is a dictionary
            elif not isinstance(xml_sync_info, dict):
                logging.warning(
                    f"xml_sync_info received as {type(xml_sync_info)}, converting to empty dict"
                )
                xml_sync_info = {}

            # Debug logging to trace the issue
            logging.info(
                f"Email notification data - added_jobs type: {type(added_jobs)}, modified_jobs type: {type(modified_jobs)}"
            )
            if added_jobs:
                logging.info(
                    f"First added job type: {type(added_jobs[0])}, content: {added_jobs[0]}"
                )
            if modified_jobs:
                logging.info(
                    f"First modified job type: {type(modified_jobs[0])}, content: {modified_jobs[0]}"
                )

            # DEDUPLICATION: Remove duplicate job entries within the same notification
            added_jobs = self._deduplicate_job_list(added_jobs)
            removed_jobs = self._deduplicate_job_list(removed_jobs)
            modified_jobs = self._deduplicate_job_list(modified_jobs)
            
            # Calculate total changes after deduplication
            total_changes = len(added_jobs) + len(removed_jobs) + len(modified_jobs)

            # Prepare email content
            subject = f"ATS Job Change Alert: {monitor_name} ({total_changes} changes)"

            # Build simple email body - just the basics
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
                    <h2 style="color: #333; margin-top: 0;">ATS Job Changes</h2>
                    <p><strong>Tearsheet:</strong> {monitor_name}</p>
                    <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</p>
                </div>
            """
            
            # Add rapid change alert if detected
            if rapid_change_alert:
                html_content += f"""
                <div style="margin: 15px 0; padding: 15px; background-color: #fff3cd; border-left: 4px solid #ffc107; border-radius: 5px;">
                    <h3 style="color: #856404; margin: 0 0 10px 0;">⚠️ Rapid Changes Detected</h3>
                    <pre style="font-family: monospace; white-space: pre-wrap; margin: 0;">{rapid_change_alert}</pre>
                </div>
                """

            if added_jobs:
                html_content += f"""
                <div style="margin: 15px 0; padding: 15px; background-color: #d4edda; border-radius: 5px;">
                    <h3 style="color: #155724; margin: 0 0 10px 0;">Jobs Added ({len(added_jobs)})</h3>
                    <ul style="margin: 0; padding-left: 20px;">
                """
                for i, job in enumerate(added_jobs):
                    try:
                        # Handle both full Bullhorn objects and simplified job objects
                        if isinstance(job, dict):
                            job_id = job.get('id', 'N/A')
                            job_title = job.get('title', 'No title')

                            # Try to extract account manager information (for full objects)
                            account_manager = "Not specified"
                            if job.get('owner') and isinstance(
                                    job.get('owner'), dict):
                                first_name = job['owner'].get('firstName',
                                                              '').strip()
                                last_name = job['owner'].get('lastName',
                                                             '').strip()
                                if first_name or last_name:
                                    account_manager = f"{first_name} {last_name}".strip(
                                    )
                            elif job.get('assignedUsers') and isinstance(
                                    job.get('assignedUsers'), list) and len(
                                        job['assignedUsers']) > 0:
                                first_user = job['assignedUsers'][0]
                                if isinstance(first_user, dict):
                                    first_name = first_user.get(
                                        'firstName', '').strip()
                                    last_name = first_user.get('lastName',
                                                               '').strip()
                                    if first_name or last_name:
                                        account_manager = f"{first_name} {last_name}".strip(
                                        )
                        else:
                            # Handle string or other formats
                            job_id = str(job)
                            job_title = str(job)
                            account_manager = "Not specified"

                        html_content += f"""<li><strong>{job_title}</strong> (ID: {job_id})<br>
                                          <small style="color: #666;">Account Manager: {account_manager}</small></li>"""
                    except Exception as e:
                        logging.error(
                            f"Error processing added job {i}: {e}. Job data: {job}"
                        )
                        html_content += f"""<li><strong>Error processing job {i}</strong></li>"""

                html_content += "</ul></div>"

            if removed_jobs:
                html_content += f"""
                <div style="margin: 15px 0; padding: 15px; background-color: #f8d7da; border-radius: 5px;">
                    <h3 style="color: #721c24; margin: 0 0 10px 0;">Jobs Removed ({len(removed_jobs)})</h3>
                    <ul style="margin: 0; padding-left: 20px;">
                """
                for job in removed_jobs:
                    # Handle both full Bullhorn objects and simplified job objects
                    if isinstance(job, dict):
                        job_id = job.get('id', 'N/A')
                        job_title = job.get('title', 'No title')

                        # Try to extract account manager information (for full objects)
                        account_manager = "Not specified"
                        if job.get('owner') and isinstance(
                                job.get('owner'), dict):
                            first_name = job['owner'].get('firstName',
                                                          '').strip()
                            last_name = job['owner'].get('lastName',
                                                         '').strip()
                            if first_name or last_name:
                                account_manager = f"{first_name} {last_name}".strip(
                                )
                        elif job.get('assignedUsers') and isinstance(
                                job.get('assignedUsers'), list) and len(
                                    job['assignedUsers']) > 0:
                            first_user = job['assignedUsers'][0]
                            if isinstance(first_user, dict):
                                first_name = first_user.get('firstName',
                                                            '').strip()
                                last_name = first_user.get('lastName',
                                                           '').strip()
                                if first_name or last_name:
                                    account_manager = f"{first_name} {last_name}".strip(
                                    )
                    else:
                        # Handle string or other formats
                        job_id = str(job)
                        job_title = str(job)
                        account_manager = "Not specified"

                    html_content += f"""<li><strong>{job_title}</strong> (ID: {job_id})<br>
                                      <small style="color: #666;">Account Manager: {account_manager}</small></li>"""

                html_content += "</ul></div>"

            if modified_jobs:
                html_content += f"""
                <div style="margin: 15px 0; padding: 15px; background-color: #fff3cd; border-radius: 5px;">
                    <h3 style="color: #856404; margin: 0 0 10px 0;">Jobs Modified ({len(modified_jobs)})</h3>
                    <ul style="margin: 0; padding-left: 20px;">
                """
                for job in modified_jobs:
                    # Handle both full Bullhorn objects and simplified job objects
                    if isinstance(job, dict):
                        job_id = job.get('id', 'N/A')
                        job_title = job.get('title', 'No title')
                        changes = job.get('changes', [])

                        # Try to extract account manager information (for full objects)
                        account_manager = "Not specified"
                        if job.get('owner') and isinstance(
                                job.get('owner'), dict):
                            first_name = job['owner'].get('firstName',
                                                          '').strip()
                            last_name = job['owner'].get('lastName',
                                                         '').strip()
                            if first_name or last_name:
                                account_manager = f"{first_name} {last_name}".strip(
                                )
                        elif job.get('assignedUsers') and isinstance(
                                job.get('assignedUsers'), list) and len(
                                    job['assignedUsers']) > 0:
                            first_user = job['assignedUsers'][0]
                            if isinstance(first_user, dict):
                                first_name = first_user.get('firstName',
                                                            '').strip()
                                last_name = first_user.get('lastName',
                                                           '').strip()
                                if first_name or last_name:
                                    account_manager = f"{first_name} {last_name}".strip(
                                    )
                    else:
                        # Handle string or other formats
                        job_id = str(job)
                        job_title = str(job)
                        changes = []
                        account_manager = "Not specified"

                    html_content += f"<li><strong>{job_title}</strong> (ID: {job_id})<br>"
                    html_content += f"<small style='color: #666;'>Account Manager: {account_manager}</small>"

                    # Display field changes with before/after values
                    if changes:
                        html_content += "<br><small>Changes: "
                        change_details = []

                        # Handle both list of dictionaries and simple string format
                        if isinstance(changes, list):
                            for change in changes:
                                if isinstance(change, dict):
                                    field_name = change.get(
                                        'display_name',
                                        change.get('field', 'Unknown'))
                                    old_value = change.get(
                                        'from', change.get('old', ''))
                                    new_value = change.get(
                                        'to', change.get('new', ''))

                                    # Truncate long values for better readability
                                    if len(str(old_value)) > 50:
                                        old_value = str(old_value)[:47] + "..."
                                    if len(str(new_value)) > 50:
                                        new_value = str(new_value)[:47] + "..."

                                    change_details.append(
                                        f"<span style='color: #856404;'>{field_name}:</span> <span style='color: #dc3545; text-decoration: line-through;'>{old_value}</span> → <span style='color: #28a745; font-weight: bold;'>{new_value}</span>"
                                    )
                                else:
                                    # Handle string change entries
                                    change_details.append(
                                        f"<span style='color: #856404;'>{str(change)}</span>"
                                    )
                        else:
                            # Handle simple string format
                            change_details.append(
                                f"<span style='color: #856404;'>{str(changes)}</span>"
                            )

                        html_content += "; ".join(change_details)
                        html_content += "</small>"

                    html_content += "</li>"

                html_content += "</ul></div>"

            if not added_jobs and not removed_jobs and not modified_jobs:
                html_content += """
                <div style="margin: 20px 0; background-color: #e2e3e5; padding: 15px; border-radius: 5px;">
                    <p style="color: #6c757d; font-style: italic; margin: 0;">No changes detected in this check.</p>
                </div>
                """

            # Close email body
            html_content += """
                <p style="margin-top: 20px; font-size: 12px; color: #6c757d;">
                    JobPulse™ Processing & Automation System
                </p>
            </body>
            </html>
            """

            # Add XML sync information if available
            # Ensure xml_sync_info is a dictionary (handle cases where it might be a string or None)
            if xml_sync_info and isinstance(
                    xml_sync_info, dict) and xml_sync_info.get('success'):
                html_content += f"""
                <div style="background-color: #e8f8e8; padding: 15px; border-radius: 8px; margin-top: 20px;">
                    <h3 style="color: #2c3e50; margin-top: 0;">🔄 XML File Updates</h3>
                    <p style="font-size: 14px; margin-bottom: 5px;">
                        <strong>XML files have been automatically updated and uploaded:</strong>
                    </p>
                    <ul style="margin-left: 20px; margin-bottom: 0;">
                        <li><strong>{xml_sync_info.get('added_count', 0)}</strong> jobs added to XML</li>
                        <li><strong>{xml_sync_info.get('removed_count', 0)}</strong> jobs removed from XML</li>
                        <li><strong>{xml_sync_info.get('updated_count', 0)}</strong> jobs updated in XML</li>
                    </ul>
                    <p style="font-size: 12px; color: #666; margin-top: 10px; margin-bottom: 0;">
                        ✅ Reference numbers regenerated and files uploaded to web server
                    </p>
                </div>"""

            html_content += """
            </body>
            </html>
            """

            # Create plain text version
            text_content = f"""
ATS Job Changes
Tearsheet: {monitor_name}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

"""
            
            if added_jobs:
                text_content += f"Jobs Added ({len(added_jobs)}):\n"
                for i, job in enumerate(added_jobs):
                    if isinstance(job, dict):
                        job_id = job.get('id', 'N/A')
                        job_title = job.get('title', 'No title')
                        text_content += f"  • {job_title} (ID: {job_id})\n"
                text_content += "\n"
            
            if removed_jobs:
                text_content += f"Jobs Removed ({len(removed_jobs)}):\n"
                for job in removed_jobs:
                    if isinstance(job, dict):
                        job_id = job.get('id', 'N/A')
                        job_title = job.get('title', 'No title')
                        text_content += f"  • {job_title} (ID: {job_id})\n"
                text_content += "\n"
            
            if modified_jobs:
                text_content += f"Jobs Modified ({len(modified_jobs)}):\n"
                for job in modified_jobs:
                    if isinstance(job, dict):
                        job_id = job.get('id', 'N/A')
                        job_title = job.get('title', 'No title')
                        text_content += f"  • {job_title} (ID: {job_id})\n"
                text_content += "\n"
            
            text_content += "JobPulse™ Processing & Automation System"

            # Create email message
            message = Mail(from_email=Email(self.from_email),
                           to_emails=To(to_email),
                           subject=subject,
                           html_content=Content("text/html", html_content),
                           plain_text_content=Content("text/plain", text_content))

            # Send email
            response = self.sg.send(message)
            
            # Extract SendGrid message ID from response headers
            sendgrid_message_id = None
            try:
                if hasattr(response, 'headers') and hasattr(response.headers, 'get'):
                    sendgrid_message_id = response.headers.get('X-Message-Id')
            except Exception:
                pass
            
            # Log email delivery
            delivery_status = 'sent' if response.status_code == 202 else 'failed'
            error_msg = None if response.status_code == 202 else f"SendGrid returned status code: {response.status_code}"
            
            self._log_email_delivery(
                notification_type='bullhorn_notification',
                recipient_email=to_email,
                delivery_status=delivery_status,
                sendgrid_message_id=sendgrid_message_id,
                error_message=error_msg,
                changes_summary=f"Monitor: {monitor_name}, Changes: {summary.get('added_count', 0)} added, {summary.get('removed_count', 0)} removed, {summary.get('modified_count', 0)} modified"
            )

            if response.status_code == 202:
                logging.info(
                    f"Bullhorn notification sent successfully to {to_email}")
                return True
            else:
                logging.error(
                    f"Failed to send Bullhorn notification: {response.status_code}"
                )
                return False

        except Exception as e:
            # Log failed email delivery
            self._log_email_delivery(
                notification_type='bullhorn_notification',
                recipient_email=to_email,
                delivery_status='failed',
                error_message=str(e),
                changes_summary=f"Monitor: {monitor_name} - Failed to send notification"
            )
            logging.error(f"Failed to send Bullhorn notification: {str(e)}")
            import traceback
            logging.error(f"Full traceback: {traceback.format_exc()}")
            return False

    def send_job_change_notification(self, to_email: str, notification_type: str, 
                                   job_id: str, job_title: str, changes_summary: str = None) -> bool:
        """
        [DISABLED] Individual job change notifications have been disabled to prevent duplicate emails.
        Use send_bullhorn_notification() for bulk summary notifications instead.
        
        Args:
            to_email: Recipient email address
            notification_type: 'job_added', 'job_removed', or 'job_modified'
            job_id: Bullhorn job ID
            job_title: Job title for reference
            changes_summary: Summary of changes made
            
        Returns:
            bool: Always returns True (method disabled)
        """
        # DISABLED: Individual notifications were causing duplicate emails
        # Use send_bullhorn_notification() for bulk summary notifications instead
        logging.warning(f"INDIVIDUAL NOTIFICATIONS DISABLED: Attempted to send individual notification for job {job_id}. "
                       f"Individual notifications are disabled to prevent duplicates. Use send_bullhorn_notification() instead.")
        
        # Log the blocked attempt for tracking
        self._log_email_delivery(
            notification_type=f"disabled_{notification_type}",
            job_id=job_id,
            job_title=job_title,
            recipient_email=to_email,
            delivery_status='blocked',
            error_message="Individual notifications disabled - use bulk notifications instead",
            changes_summary=f"Blocked individual notification: {notification_type} for job {job_id}"
        )
        
        return True  # Return True to prevent error handling in calling code

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
            if not self.sendgrid_api_key:
                logging.warning("SendGrid API key not configured - cannot send email")
                return False

            from_email = Email("noreply@jobpulse.lyntrix.ai")
            to_email_obj = To(to_email)
            content = Content("text/plain", message)
            mail = Mail(from_email, to_email_obj, subject, content)

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
                logging.info(f"Generic notification sent successfully to {to_email}: {subject}")
                return True
            else:
                logging.error(f"Failed to send generic notification: {response.status_code}")
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
            logging.error(f"Failed to send generic notification: {str(e)}")
            return False

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
                logging.warning("EmailService: Database connection or EmailDeliveryLog model not available for logging")
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
            
            logging.info(f"Email delivery logged: {notification_type} to {recipient_email} - Status: {delivery_status}")
            
        except Exception as e:
            logging.error(f"Failed to log email delivery: {str(e)}")
            if self.db:
                self.db.session.rollback()
