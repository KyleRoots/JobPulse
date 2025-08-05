# Copy the job application service from the main app
# This ensures the separate app has all the necessary logic

import os
import logging
from typing import Dict, Optional
from werkzeug.datastructures import FileStorage
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment
import base64
import mimetypes

# Import resume parser (will need to copy this file too)
from resume_parser import ResumeParser

logger = logging.getLogger(__name__)

class JobApplicationService:
    """Service for processing job applications and sending emails"""
    
    def __init__(self):
        self.sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
        self.from_email = "noreply@myticas.com"
        self.to_email = "apply@myticas.com"
        self.resume_parser = ResumeParser()
        
        if self.sendgrid_api_key:
            self.sg = SendGridAPIClient(api_key=self.sendgrid_api_key)
        else:
            self.sg = None
            logger.warning("No SendGrid API key found - email sending disabled")
    
    def parse_resume(self, resume_file: FileStorage) -> Dict[str, any]:
        """
        Parse resume file and extract candidate information
        
        Args:
            resume_file: Uploaded resume file
            
        Returns:
            Dict with parsing results
        """
        try:
            return self.resume_parser.parse_resume(resume_file)
        except Exception as e:
            logger.error(f"Error parsing resume: {str(e)}")
            return {
                'success': False,
                'error': f'Error parsing resume: {str(e)}',
                'parsed_data': {}
            }
    
    def submit_application(self, application_data: Dict, resume_file: FileStorage, 
                          cover_letter_file: Optional[FileStorage] = None) -> Dict[str, any]:
        """
        Submit job application by sending structured email
        
        Args:
            application_data: Form data (firstName, lastName, email, phone, jobId, jobTitle, source)
            resume_file: Resume file upload
            cover_letter_file: Optional cover letter file upload
            
        Returns:
            Dict with submission results
        """
        try:
            if not self.sg:
                raise ValueError("Email service not available - no SendGrid API key")
            
            # Create email content - decode URL encoding and format subject properly
            import urllib.parse
            clean_job_title = urllib.parse.unquote(application_data['jobTitle']).replace('+', ' ')
            source = application_data.get('source', 'Website')
            
            subject = f"{clean_job_title} ({application_data['jobId']}) - {application_data['firstName']} {application_data['lastName']} has applied on {source}"
            
            html_content = self._build_application_email_html(application_data)
            text_content = self._build_application_email_text(application_data)
            
            # Create email message
            message = Mail(
                from_email=Email(self.from_email),
                to_emails=To(self.to_email),
                subject=subject,
                html_content=Content("text/html", html_content),
                plain_text_content=Content("text/plain", text_content)
            )
            
            # Add Myticas logo as inline attachment
            try:
                logo_path = os.path.join('static', 'myticas-logo-bw-revised.png')
                if os.path.exists(logo_path):
                    with open(logo_path, 'rb') as logo_file:
                        logo_data = logo_file.read()
                        logo_attachment = Attachment(
                            file_content=base64.b64encode(logo_data).decode(),
                            file_type='image/png',
                            file_name='myticas-logo.png',
                            disposition='inline',
                            content_id='myticas-logo'
                        )
                        message.attachment = logo_attachment
            except Exception as logo_error:
                logger.warning(f"Could not attach logo: {logo_error}")
            
            # Add resume attachment
            try:
                resume_content = resume_file.read()
                resume_file.seek(0)  # Reset file pointer
                
                resume_attachment = Attachment(
                    file_content=base64.b64encode(resume_content).decode(),
                    file_type=resume_file.content_type or 'application/octet-stream',
                    file_name=resume_file.filename,
                    disposition='attachment'
                )
                
                if message.attachment:
                    message.attachment = [message.attachment, resume_attachment]
                else:
                    message.attachment = resume_attachment
                    
            except Exception as resume_error:
                logger.error(f"Error attaching resume: {resume_error}")
                return {
                    'success': False,
                    'error': f'Error processing resume attachment: {str(resume_error)}'
                }
            
            # Add cover letter if provided
            if cover_letter_file and cover_letter_file.filename:
                try:
                    cover_letter_content = cover_letter_file.read()
                    cover_letter_file.seek(0)
                    
                    cover_letter_attachment = Attachment(
                        file_content=base64.b64encode(cover_letter_content).decode(),
                        file_type=cover_letter_file.content_type or 'application/octet-stream',
                        file_name=cover_letter_file.filename,
                        disposition='attachment'
                    )
                    
                    if isinstance(message.attachment, list):
                        message.attachment.append(cover_letter_attachment)
                    else:
                        message.attachment = [message.attachment, cover_letter_attachment]
                        
                except Exception as cover_error:
                    logger.warning(f"Error attaching cover letter: {cover_error}")
            
            # Send email
            response = self.sg.send(message)
            
            if response.status_code in [200, 202]:
                logger.info(f"Application submitted successfully for job {application_data['jobId']} by {application_data['firstName']} {application_data['lastName']}")
                return {
                    'success': True,
                    'message': 'Your application has been submitted successfully! We will review it and get back to you soon.',
                    'message_id': response.headers.get('X-Message-Id', 'unknown')
                }
            else:
                logger.error(f"SendGrid API error: {response.status_code} - {response.body}")
                return {
                    'success': False,
                    'error': f'Email service error: {response.status_code}'
                }
                
        except Exception as e:
            logger.error(f"Error submitting application: {str(e)}")
            return {
                'success': False,
                'error': f'Error submitting application: {str(e)}'
            }
    
    def _build_application_email_html(self, application_data: Dict) -> str:
        """Build HTML email content for application"""
        import urllib.parse
        clean_job_title = urllib.parse.unquote(application_data['jobTitle']).replace('+', ' ')
        source = application_data.get('source', 'Website')
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>New Job Application - {clean_job_title}</title>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .logo {{ text-align: center; margin-bottom: 20px; }}
                .job-info {{ background: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
                .candidate-info {{ background: #e9ecef; padding: 15px; border-radius: 5px; }}
                .field {{ margin-bottom: 10px; }}
                .label {{ font-weight: bold; color: #495057; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="logo">
                    <img src="cid:myticas-logo" alt="Myticas Consulting" style="max-height: 60px;">
                </div>
                
                <div class="job-info">
                    <h2>New Job Application Received</h2>
                    <div class="field">
                        <span class="label">Position:</span> {clean_job_title}
                    </div>
                    <div class="field">
                        <span class="label">Job ID:</span> {application_data['jobId']}
                    </div>
                    <div class="field">
                        <span class="label">Source:</span> {source}
                    </div>
                </div>
                
                <div class="candidate-info">
                    <h3>Candidate Information</h3>
                    <div class="field">
                        <span class="label">Name:</span> {application_data['firstName']} {application_data['lastName']}
                    </div>
                    <div class="field">
                        <span class="label">Email:</span> {application_data['email']}
                    </div>
                    <div class="field">
                        <span class="label">Phone:</span> {application_data['phone']}
                    </div>
                </div>
                
                <p>Please find the candidate's resume and cover letter (if provided) attached to this email.</p>
                
                <hr>
                <p style="font-size: 12px; color: #6c757d; text-align: center;">
                    This application was submitted through the Myticas Consulting job application portal.
                </p>
            </div>
        </body>
        </html>
        """
        return html_content
    
    def _build_application_email_text(self, application_data: Dict) -> str:
        """Build plain text email content for application"""
        import urllib.parse
        clean_job_title = urllib.parse.unquote(application_data['jobTitle']).replace('+', ' ')
        source = application_data.get('source', 'Website')
        
        text_content = f"""
New Job Application Received

Position: {clean_job_title}
Job ID: {application_data['jobId']}
Source: {source}

Candidate Information:
Name: {application_data['firstName']} {application_data['lastName']}
Email: {application_data['email']}
Phone: {application_data['phone']}

Please find the candidate's resume and cover letter (if provided) attached to this email.

---
This application was submitted through the Myticas Consulting job application portal.
        """
        return text_content