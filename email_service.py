import os
import sys
from datetime import datetime
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
            
            Best regards,
            XML Processing System
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
                
                <p>Best regards,<br>
                XML Processing System</p>
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
            
            Best regards,
            XML Processing System
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
            logging.error(f"Error sending error notification: {str(e)}")
            return False

    def send_bullhorn_notification(self,
                                   to_email: str,
                                   monitor_name: str,
                                   added_jobs: list,
                                   removed_jobs: list,
                                   modified_jobs: list = [],
                                   summary: dict = {},
                                   xml_sync_info: dict = {}) -> bool:
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

            # Calculate total changes
            total_changes = len(added_jobs) + len(removed_jobs) + len(
                modified_jobs)

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
                    Best regards,<br>
                    Springboard™ XML Processing System
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

            # Create email message
            message = Mail(from_email=Email(self.from_email),
                           to_emails=To(to_email),
                           subject=subject,
                           html_content=Content("text/html", html_content))

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
        Send email notification for job changes (added, removed, modified)
        
        Args:
            to_email: Recipient email address
            notification_type: 'job_added', 'job_removed', or 'job_modified'
            job_id: Bullhorn job ID
            job_title: Job title for reference
            changes_summary: Summary of changes made
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            if not self.api_key:
                logging.error("EmailService: No SendGrid API key available")
                return False

            # Create subject and content based on notification type
            if notification_type == 'job_added':
                subject = f"New Job Added: {job_title} (ID: {job_id})"
                action_text = "added to"
                icon = "➕"
            elif notification_type == 'job_removed':
                subject = f"Job Removed: {job_title} (ID: {job_id})"
                action_text = "removed from"
                icon = "➖"
            elif notification_type == 'job_modified':
                subject = f"Job Modified: {job_title} (ID: {job_id})"
                action_text = "modified in"
                icon = "✏️"
            else:
                subject = f"Job Change: {job_title} (ID: {job_id})"
                action_text = "changed in"
                icon = "🔄"

            html_content = f"""
            <html>
            <body>
                <h2>{icon} Job Feed Update</h2>
                <p>A job has been <strong>{action_text}</strong> your XML feed.</p>
                
                <h3>Job Details:</h3>
                <ul>
                    <li><strong>Job ID:</strong> {job_id}</li>
                    <li><strong>Job Title:</strong> {job_title}</li>
                    <li><strong>Action:</strong> {notification_type.replace('_', ' ').title()}</li>
                    <li><strong>Timestamp:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</li>
                </ul>
                
                {f'<h3>Changes Summary:</h3><p>{changes_summary}</p>' if changes_summary else ''}
                
                <p>Your live XML feed at <a href="https://myticas.com/myticas-job-feed.xml">https://myticas.com/myticas-job-feed.xml</a> has been updated automatically.</p>
                
                <p>Best regards,<br>
                Job Feed Monitoring System</p>
            </body>
            </html>
            """

            text_content = f"""
            Job Feed Update

            A job has been {action_text} your XML feed.

            Job Details:
            - Job ID: {job_id}
            - Job Title: {job_title}
            - Action: {notification_type.replace('_', ' ').title()}
            - Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

            {f'Changes Summary: {changes_summary}' if changes_summary else ''}

            Your live XML feed at https://myticas.com/myticas-job-feed.xml has been updated automatically.

            Best regards,
            Job Feed Monitoring System
            """

            # Create the email message
            message = Mail(
                from_email=Email(self.from_email),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content),
                plain_text_content=Content("text/plain", text_content)
            )

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
                notification_type=notification_type,
                job_id=job_id,
                job_title=job_title,
                recipient_email=to_email,
                delivery_status=delivery_status,
                sendgrid_message_id=sendgrid_message_id,
                error_message=error_msg,
                changes_summary=changes_summary
            )

            if response.status_code == 202:
                logging.info(f"Job change notification sent successfully to {to_email} for job {job_id}")
                return True
            else:
                logging.error(f"Failed to send job change notification. Status code: {response.status_code}")
                return False

        except Exception as e:
            # Log failed email delivery
            self._log_email_delivery(
                notification_type=notification_type,
                job_id=job_id,
                job_title=job_title,
                recipient_email=to_email,
                delivery_status='failed',
                error_message=str(e),
                changes_summary=changes_summary
            )
            logging.error(f"Error sending job change notification: {str(e)}")
            return False

    def _log_email_delivery(self, notification_type: str, job_id: str = None, job_title: str = None,
                          recipient_email: str = "", delivery_status: str = "sent", 
                          sendgrid_message_id: str = None, error_message: str = None,
                          schedule_name: str = None, changes_summary: str = None):
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
