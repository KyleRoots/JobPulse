import base64
import logging
from datetime import datetime

from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment

logger = logging.getLogger(__name__)


class XMLNotificationsMixin:

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
                                <p><strong>Total Jobs (v2 feed):</strong> {total_jobs}</p>
                                <p><strong>v2 File Size:</strong> {xml_size}</p>
            """

            pando_jobs = upload_details.get('pando_jobs_count')
            pando_size = upload_details.get('pando_xml_size')
            if pando_jobs is not None:
                html_content += f"""
                                <p><strong>Total Jobs (pando feed):</strong> {pando_jobs}</p>
                                <p><strong>Pando File Size:</strong> {pando_size}</p>
                """

            html_content += f"""
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
                                <p>Two XML job feeds have been automatically uploaded:</p>
                                <ul>
                                    <li><strong>myticas-job-feed-v2.xml</strong> — {total_jobs} jobs (STSI capped at 10 most recent)</li>
                                    <li><strong>myticas-job-feed-pando.xml</strong> — {pando_jobs if pando_jobs else 'N/A'} jobs (all jobs, no cap)</li>
                                </ul>
                                <p>The system will continue to upload fresh data every 30 minutes to ensure your job feeds stay current.</p>
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
                    logger.error(f"Failed to log automated upload notification delivery: {str(log_error)}")
            
            return response.status_code in [200, 202]
            
        except Exception as e:
            logger.error(f"Failed to send automated upload notification: {str(e)}")
            return False

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
                logger.error("EmailService: No SendGrid API key available")
                return False
            
            # Check for recent duplicate notifications
            if self._check_recent_notification('scheduled_processing', to_email, schedule_name=schedule_name):
                logger.info(f"DUPLICATE PREVENTION: Skipping duplicate processing notification for {schedule_name}")
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
            
            Scout Genius™ Automation Platform
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
                logger.info(f"Email sent successfully to {to_email}")
                return True
            else:
                logger.error(
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
            logger.error(f"Error sending email: {str(e)}")
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
                logger.error("EmailService: No SendGrid API key available")
                return False
            
            # Check for recent duplicate notifications
            if self._check_recent_notification('processing_error', to_email, schedule_name=schedule_name):
                logger.info(f"DUPLICATE PREVENTION: Skipping duplicate error notification for {schedule_name}")
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
                
                <p>Scout Genius™ Automation Platform</p>
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
            
            Scout Genius™ Automation Platform
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
                logger.info(
                    f"Error notification sent successfully to {to_email}")
                return True
            else:
                logger.error(
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
            logger.error(f"Error sending error notification: {str(e)}")
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
                logger.error("EmailService: No SendGrid API key available")
                return False
                
            # Check for recent duplicates (30-minute threshold for reference number refresh)
            if self._check_recent_notification('reference_number_refresh', to_email, 
                                             schedule_name=schedule_name, minutes_threshold=30):
                logger.info(f"DUPLICATE PREVENTION: Skipping duplicate reference number refresh notification for {schedule_name}")
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
                logger.info(f"📧 Reference number refresh notification sent to {to_email} for {schedule_name}")
                return True
            else:
                logger.error(f"Failed to send reference number refresh notification. Status code: {response.status_code}")
                return False
            
        except Exception as e:
            logger.error(f"Failed to send reference number refresh notification: {e}")
            
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

    def send_new_job_notification(self, to_email: str, job_id: str, job_title: str, 
                                  monitor_name: str = None) -> bool:
        """
        Send email notification when a new job is added to tearsheet monitoring
        
        Args:
            to_email: Recipient email address
            job_id: Bullhorn job ID
            job_title: Job title
            monitor_name: Name of the tearsheet monitor (optional)
        
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            if not self.api_key:
                logger.warning("SendGrid API key not configured - cannot send email")
                return False
            
            # Check for duplicate notification within 24 hours (1440 minutes)
            if self._check_recent_notification(
                'new_job_notification', 
                to_email, 
                job_id=job_id,
                minutes_threshold=1440  # 24 hours
            ):
                logger.info(f"DUPLICATE PREVENTION: Skipping duplicate new job notification for job {job_id}")
                return True  # Return True since we're intentionally not sending (not an error)
            
            # Create subject line with job ID
            subject = f"🆕 New Job Added - ID: {job_id}"
            
            # Create HTML content
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
            monitor_display = f" ({monitor_name})" if monitor_name else ""
            
            html_content = f"""
            <html>
                <head>
                    <style>
                        body {{ font-family: Arial, sans-serif; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                        .header {{ background-color: #28a745; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
                        .content {{ background-color: #f8f9fa; padding: 20px; border-radius: 0 0 8px 8px; }}
                        .job-details {{ background-color: white; padding: 15px; border-radius: 8px; margin: 15px 0; border-left: 4px solid #28a745; }}
                        .job-id {{ font-size: 18px; font-weight: bold; color: #28a745; margin-bottom: 10px; }}
                        .job-title {{ font-size: 16px; color: #333; margin-bottom: 10px; }}
                        .info {{ color: #666; font-size: 14px; margin: 5px 0; }}
                        .footer {{ margin-top: 20px; padding-top: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #666; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h2 style="margin: 0;">🆕 New Job Added to Tearsheet</h2>
                        </div>
                        <div class="content">
                            <p>A new job has been added to your tearsheet monitoring{monitor_display}.</p>
                            
                            <div class="job-details">
                                <div class="job-id">Job ID: {job_id}</div>
                                <div class="job-title">{job_title}</div>
                                <div class="info">⏰ Detected: {timestamp}</div>
                                {f'<div class="info">📋 Monitor: {monitor_name}</div>' if monitor_name else ''}
                            </div>
                            
                            <p style="margin-top: 20px;">
                                <strong>Search in Bullhorn:</strong> Use Job ID <code style="background: #f0f0f0; padding: 2px 6px; border-radius: 3px;">{job_id}</code> to find this job in your Bullhorn system.
                            </p>
                            
                            <div class="footer">
                                <p>This is an automated notification from your XML Job Feed monitoring system.</p>
                            </div>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            # Create plain text version
            text_content = f"""
New Job Added to Tearsheet{monitor_display}

Job ID: {job_id}
Title: {job_title}
Detected: {timestamp}
{f'Monitor: {monitor_name}' if monitor_name else ''}

Search in Bullhorn using Job ID: {job_id}

---
This is an automated notification from your XML Job Feed monitoring system.
            """
            
            # Send email via SendGrid
            message = Mail(
                from_email=Email(self.from_email),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content),
                plain_text_content=Content("text/plain", text_content)
            )
            
            response = self.sg.send(message)
            
            # Extract SendGrid message ID
            sendgrid_message_id = None
            if hasattr(response, 'headers'):
                sendgrid_message_id = response.headers.get('X-Message-Id')
            
            # Log successful delivery
            self._log_email_delivery(
                notification_type='new_job_notification',
                job_id=job_id,
                job_title=job_title,
                recipient_email=to_email,
                delivery_status='sent' if response.status_code == 202 else 'failed',
                sendgrid_message_id=sendgrid_message_id,
                error_message=None if response.status_code == 202 else f"SendGrid returned status code: {response.status_code}",
                changes_summary=f"New job added{monitor_display}: {job_title} (ID: {job_id})"
            )
            
            if response.status_code == 202:
                logger.info(f"✅ New job notification sent to {to_email} for job {job_id}")
                return True
            else:
                logger.error(f"❌ Failed to send new job notification: {response.status_code}")
                return False
                
        except Exception as e:
            # Log failed email delivery
            self._log_email_delivery(
                notification_type='new_job_notification',
                job_id=job_id,
                job_title=job_title,
                recipient_email=to_email,
                delivery_status='failed',
                error_message=str(e),
                changes_summary=f"New job added: {job_title} (ID: {job_id})"
            )
            logger.error(f"❌ Failed to send new job notification: {str(e)}")
            return False
