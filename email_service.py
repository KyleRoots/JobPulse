import os
import sys
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment
import base64
import logging

class EmailService:
    """Handles email notifications for XML processing"""
    
    def __init__(self):
        self.api_key = os.environ.get('SENDGRID_API_KEY')
        if not self.api_key:
            logging.error('SENDGRID_API_KEY environment variable not set')
            return
        
        self.sg = SendGridAPIClient(self.api_key)
        self.from_email = "kroots@myticas.com"  # Verified sender email
    
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
            subject = f"XML Processing {status_text}: {schedule_name}"
            
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
            message = Mail(
                from_email=Email(self.from_email),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content),
                plain_text_content=Content("text/plain", text_content)
            )
            
            # Add attachment
            encoded_file = base64.b64encode(xml_content).decode()
            attachment = Attachment(
                file_content=encoded_file,
                file_type="application/xml",
                file_name=original_filename,
                disposition="attachment"
            )
            message.attachment = attachment
            
            # Send email
            response = self.sg.send(message)
            
            if response.status_code == 202:
                logging.info(f"Email sent successfully to {to_email}")
                return True
            else:
                logging.error(f"Failed to send email. Status code: {response.status_code}")
                return False
                
        except Exception as e:
            logging.error(f"Error sending email: {str(e)}")
            return False
    
    def send_processing_error_notification(self, 
                                         to_email: str, 
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
            message = Mail(
                from_email=Email(self.from_email),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content),
                plain_text_content=Content("text/plain", text_content)
            )
            
            # Send email
            response = self.sg.send(message)
            
            if response.status_code == 202:
                logging.info(f"Error notification sent successfully to {to_email}")
                return True
            else:
                logging.error(f"Failed to send error notification. Status code: {response.status_code}")
                return False
                
        except Exception as e:
            logging.error(f"Error sending error notification: {str(e)}")
            return False
    
    def send_bullhorn_notification(self, 
                                 to_email: str, 
                                 monitor_name: str,
                                 added_jobs: list,
                                 removed_jobs: list,
                                 modified_jobs: list = None,
                                 summary: dict = None) -> bool:
        """
        Send email notification for Bullhorn tearsheet changes
        
        Args:
            to_email: Recipient email address
            monitor_name: Name of the monitor/tearsheet
            added_jobs: List of jobs that were added
            removed_jobs: List of jobs that were removed
            modified_jobs: List of jobs that were modified (optional)
            summary: Summary statistics of changes (optional)
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            if not self.api_key:
                logging.error("EmailService: No SendGrid API key available")
                return False
            
            # Prepare default values
            if modified_jobs is None:
                modified_jobs = []
            if summary is None:
                summary = {}
            
            # Calculate total changes
            total_changes = len(added_jobs) + len(removed_jobs) + len(modified_jobs)
            
            # Prepare email content
            subject = f"ATS Tearsheet Update: {monitor_name} ({total_changes} changes)"
            
            # Build email body
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
                    <h2 style="color: #333; margin-top: 0;">
                        🔄 ATS Tearsheet Update
                    </h2>
                    <p><strong>Monitor:</strong> {monitor_name}</p>
                    <p><strong>Checked at:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                    
                    <div style="background-color: #e9ecef; padding: 15px; border-radius: 5px; margin-top: 15px;">
                        <h4 style="margin: 0 0 10px 0; color: #495057;">📊 Change Summary</h4>
                        <div style="display: flex; gap: 20px; flex-wrap: wrap;">
                            <span style="color: #28a745; font-weight: bold;">✅ Added: {len(added_jobs)}</span>
                            <span style="color: #dc3545; font-weight: bold;">❌ Removed: {len(removed_jobs)}</span>
                            <span style="color: #fd7e14; font-weight: bold;">🔄 Modified: {len(modified_jobs)}</span>
                            <span style="color: #6c757d; font-weight: bold;">📈 Net Change: {summary.get('net_change', 0):+d}</span>
                        </div>
                    </div>
                </div>
            """
            
            if added_jobs:
                html_content += f"""
                <div style="margin: 20px 0; background-color: #d4edda; padding: 15px; border-radius: 5px; border-left: 4px solid #28a745;">
                    <h3 style="color: #155724; margin-bottom: 15px;">
                        ✅ Jobs Added ({len(added_jobs)})
                    </h3>
                    <ul style="padding-left: 20px; margin: 0;">
                """
                for job in added_jobs:
                    job_id = job.get('id', 'N/A')
                    job_title = job.get('title', 'No title')
                    company = job.get('clientCorporation', {}).get('name', 'N/A') if isinstance(job.get('clientCorporation'), dict) else 'N/A'
                    status = job.get('status', 'N/A')
                    html_content += f"""
                    <li style="margin-bottom: 12px; padding: 8px; background-color: #f8f9fa; border-radius: 4px;">
                        <div style="margin-bottom: 4px;">
                            <strong style="color: #155724;">{job_title}</strong>
                        </div>
                        <div style="margin-bottom: 4px;">
                            <span style="background-color: #28a745; color: white; padding: 2px 6px; border-radius: 3px; font-size: 12px; font-weight: bold;">
                                Job ID: {job_id}
                            </span>
                        </div>
                        <small style="color: #6c757d;">Company: {company} | Status: {status}</small>
                    </li>
                    """
                
                html_content += "</ul></div>"
            
            if removed_jobs:
                html_content += f"""
                <div style="margin: 20px 0; background-color: #f8d7da; padding: 15px; border-radius: 5px; border-left: 4px solid #dc3545;">
                    <h3 style="color: #721c24; margin-bottom: 15px;">
                        ❌ Jobs Removed ({len(removed_jobs)})
                    </h3>
                    <ul style="padding-left: 20px; margin: 0;">
                """
                for job in removed_jobs:
                    job_id = job.get('id', 'N/A')
                    job_title = job.get('title', 'No title')
                    company = job.get('clientCorporation', {}).get('name', 'N/A') if isinstance(job.get('clientCorporation'), dict) else 'N/A'
                    status = job.get('status', 'N/A')
                    html_content += f"""
                    <li style="margin-bottom: 12px; padding: 8px; background-color: #f8f9fa; border-radius: 4px;">
                        <div style="margin-bottom: 4px;">
                            <strong style="color: #721c24;">{job_title}</strong>
                        </div>
                        <div style="margin-bottom: 4px;">
                            <span style="background-color: #dc3545; color: white; padding: 2px 6px; border-radius: 3px; font-size: 12px; font-weight: bold;">
                                Job ID: {job_id}
                            </span>
                        </div>
                        <small style="color: #6c757d;">Company: {company} | Status: {status}</small>
                    </li>
                    """
                
                html_content += "</ul></div>"
            
            if modified_jobs:
                html_content += f"""
                <div style="margin: 20px 0; background-color: #fff3cd; padding: 15px; border-radius: 5px; border-left: 4px solid #fd7e14;">
                    <h3 style="color: #856404; margin-bottom: 15px;">
                        🔄 Jobs Modified ({len(modified_jobs)})
                    </h3>
                    <ul style="padding-left: 20px; margin: 0;">
                """
                for job in modified_jobs:
                    job_id = job.get('id', 'N/A')
                    job_title = job.get('title', 'No title')
                    changes = job.get('changes', [])
                    html_content += f"""
                    <li style="margin-bottom: 15px; padding: 8px; background-color: #f8f9fa; border-radius: 4px;">
                        <div style="margin-bottom: 4px;">
                            <strong style="color: #856404;">{job_title}</strong>
                        </div>
                        <div style="margin-bottom: 8px;">
                            <span style="background-color: #fd7e14; color: white; padding: 2px 6px; border-radius: 3px; font-size: 12px; font-weight: bold;">
                                Job ID: {job_id}
                            </span>
                        </div>
                        <div style="margin-bottom: 4px;">
                            <small style="color: #6c757d; font-weight: bold;">Changes:</small>
                        </div>
                        <ul style="margin-top: 5px; padding-left: 20px;">
                    """
                    for change in changes:
                        field = change['field']
                        from_val = change['from']
                        to_val = change['to']
                        html_content += f"<li style='margin-bottom: 2px;'><strong>{field}:</strong> {from_val} → {to_val}</li>"
                    html_content += "</ul></li>"
                
                html_content += "</ul></div>"
            
            if not added_jobs and not removed_jobs and not modified_jobs:
                html_content += """
                <div style="margin: 20px 0; background-color: #e2e3e5; padding: 15px; border-radius: 5px;">
                    <p style="color: #6c757d; font-style: italic; margin: 0;">No changes detected in this check.</p>
                </div>
                """
            
            html_content += f"""
                <div style="background-color: #e9ecef; padding: 15px; border-radius: 5px; margin-top: 20px;">
                    <h4 style="margin: 0 0 10px 0; color: #495057;">📋 Monitor Details</h4>
                    <p style="margin: 0 0 10px 0; font-size: 14px;">
                        <strong>Previous total:</strong> {summary.get('total_previous', 0)} jobs<br>
                        <strong>Current total:</strong> {summary.get('total_current', 0)} jobs<br>
                        <strong>Net change:</strong> {summary.get('net_change', 0):+d} jobs
                    </p>
                    <p style="margin: 0; font-size: 12px; color: #6c757d;">
                        This is an automated notification from your XML Processing System's ATS integration.
                    </p>
                    <p style="margin: 8px 0 0 0; font-size: 11px; color: #6c757d; font-style: italic;">
                        💡 Tip: Copy and paste the Job ID into Bullhorn's search to quickly locate and review any job.
                    </p>
                </div>
            </body>
            </html>
            """
            
            # Create email message
            message = Mail(
                from_email=Email(self.from_email),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content)
            )
            
            # Send email
            response = self.sg.send(message)
            
            if response.status_code == 202:
                logging.info(f"Bullhorn notification sent successfully to {to_email}")
                return True
            else:
                logging.error(f"Failed to send Bullhorn notification: {response.status_code}")
                return False
                
        except Exception as e:
            logging.error(f"Failed to send Bullhorn notification: {str(e)}")
            return False