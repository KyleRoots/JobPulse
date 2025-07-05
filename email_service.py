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
                                   original_filename: str) -> bool:
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
            logging.info(f"EmailService: Starting email notification to {to_email}")
            logging.info(f"EmailService: API Key present: {bool(self.api_key)}")
            
            if not self.api_key:
                logging.error("EmailService: No SendGrid API key available")
                return False
            # Read the XML file for attachment
            with open(xml_file_path, 'rb') as f:
                xml_content = f.read()
            
            # Create email
            subject = f"XML Processing Complete: {schedule_name}"
            
            html_content = f"""
            <html>
            <body>
                <h2>XML Processing Complete</h2>
                <p>Your scheduled XML processing has been completed successfully.</p>
                
                <h3>Processing Details:</h3>
                <ul>
                    <li><strong>Schedule:</strong> {schedule_name}</li>
                    <li><strong>Jobs Processed:</strong> {jobs_processed}</li>
                    <li><strong>File:</strong> {original_filename}</li>
                    <li><strong>Status:</strong> ✅ Completed</li>
                </ul>
                
                <p>The updated XML file with new reference numbers is attached to this email.</p>
                
                <p>Best regards,<br>
                XML Processing System</p>
            </body>
            </html>
            """
            
            text_content = f"""
            XML Processing Complete
            
            Your scheduled XML processing has been completed successfully.
            
            Processing Details:
            - Schedule: {schedule_name}
            - Jobs Processed: {jobs_processed}
            - File: {original_filename}
            - Status: Completed
            
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