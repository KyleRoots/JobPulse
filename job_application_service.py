"""
Job Application Service
Handles job application form processing and email submission
"""
import logging
import os
import base64
from datetime import datetime
from typing import Dict, List, Optional
from werkzeug.datastructures import FileStorage
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment
from resume_parser import ResumeParser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class JobApplicationService:
    """Service for processing job applications and sending emails"""
    
    def __init__(self):
        self.sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
        self.from_email = "info@myticas.com"
        self.to_email = "apply@myticas.com"
        self.resume_parser = ResumeParser()
        
        if self.sendgrid_api_key:
            self.sg = SendGridAPIClient(api_key=self.sendgrid_api_key)
        else:
            self.sg = None
            logger.warning("No SendGrid API key found - email sending disabled")
    
    def parse_resume(self, resume_file: FileStorage, quick_mode: bool = True) -> Dict[str, any]:
        """
        Parse resume file and extract candidate information
        
        Args:
            resume_file: Uploaded resume file
            quick_mode: If True (default), skip AI formatting for faster contact extraction
            
        Returns:
            Dict with parsing results
        """
        try:
            parsed_data = self.resume_parser.parse_resume(resume_file, quick_mode=quick_mode)
            return {
                'success': True,
                'parsed_info': parsed_data
            }
        except Exception as e:
            logger.error(f"Error parsing resume: {str(e)}")
            return {
                'success': False,
                'error': f'Error parsing resume: {str(e)}',
                'parsed_info': {}
            }
    
    def submit_application(self, application_data: Dict, resume_file: FileStorage, 
                          cover_letter_file: Optional[FileStorage] = None, 
                          request_host: Optional[str] = None) -> Dict[str, any]:
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
            
            # Detect if this is an STSI application based on domain
            is_stsi = request_host and 'stsigroup' in request_host.lower()
            
            html_content = self._build_application_email_html(application_data, is_stsi)
            text_content = self._build_application_email_text(application_data, is_stsi)
            
            # Create email message
            message = Mail(
                from_email=Email(self.from_email),
                to_emails=To(self.to_email),
                subject=subject,
                html_content=Content("text/html", html_content),
                plain_text_content=Content("text/plain", text_content)
            )
            
            # Add appropriate logo as inline attachment based on branding
            logo_attachment = self._create_logo_attachment(is_stsi)
            if logo_attachment:
                message.add_attachment(logo_attachment)
            
            # Add resume attachment
            if resume_file:
                resume_attachment = self._create_attachment(resume_file, "resume")
                if resume_attachment:
                    message.add_attachment(resume_attachment)
            
            # Add cover letter attachment if provided
            if cover_letter_file:
                cover_letter_attachment = self._create_attachment(cover_letter_file, "cover_letter")
                if cover_letter_attachment:
                    message.add_attachment(cover_letter_attachment)
            
            # Send email
            logger.info(f"📧 Attempting to send job application email via SendGrid...")
            logger.info(f"   From: {self.from_email}")
            logger.info(f"   To: {self.to_email}")
            logger.info(f"   Subject: {subject}")
            logger.info(f"   SendGrid API key present: {bool(self.sendgrid_api_key)}")
            
            response = self.sg.send(message)
            
            logger.info(f"📧 SendGrid response status code: {response.status_code}")
            logger.info(f"📧 SendGrid response headers: {dict(response.headers) if response.headers else 'None'}")
            
            if response.status_code == 202:
                logger.info(f"✅ Job application submitted successfully for {application_data['firstName']} {application_data['lastName']}")
                return {
                    'success': True,
                    'message': 'Application submitted successfully'
                }
            else:
                logger.error(f"❌ Failed to send application email: {response.status_code}")
                logger.error(f"   Response body: {response.body}")
                return {
                    'success': False,
                    'error': f'Failed to send application: HTTP {response.status_code}'
                }
                
        except Exception as e:
            logger.error(f"Error submitting application: {str(e)}")
            return {
                'success': False,
                'error': f'Error submitting application: {str(e)}'
            }
    
    def _build_application_email_html(self, data: Dict, is_stsi: bool = False) -> str:
        """Build HTML email content for job application"""
        
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        
        # Clean up job title by removing URL encoding
        import urllib.parse
        clean_job_title = urllib.parse.unquote(data['jobTitle']).replace('+', ' ')
        
        # Determine branding
        if is_stsi:
            logo_cid = "stsi_logo"
            company_name = "STSI (Staffing Technical Services Inc.)"
            alt_text = "STSI Group"
        else:
            logo_cid = "myticas_logo"
            company_name = "Myticas Consulting"
            alt_text = "Myticas Consulting"
        
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background-color: #f5f5f5; padding: 20px;">
            <div style="background-color: white; border-radius: 8px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                
                <!-- Company Branding -->
                <div style="text-align: center; margin-bottom: 10px; padding: 15px; background-color: #f8f9fa; border-radius: 8px; border: 1px solid #e9ecef;">
                    <img src="cid:{logo_cid}" alt="{alt_text}" style="max-width: 250px; height: auto; margin-bottom: 8px;">
                    <p style="margin: 0; color: #6c757d; font-size: 14px; font-style: italic;">Job posting is on behalf of {company_name}</p>
                </div>
                
                <!-- Job Information -->
                <div style="background-color: #f8f9fa; border-radius: 8px; padding: 20px; margin-bottom: 20px; border-left: 4px solid #667eea;">
                    <h2 style="color: #2c3e50; margin: 0 0 15px 0; font-size: 18px;">🎯 Position Details</h2>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px 0; color: #666; font-weight: bold; width: 30%;">Job Title:</td>
                            <td style="padding: 8px 0; color: #333;">{clean_job_title}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #666; font-weight: bold;">Bullhorn ID:</td>
                            <td style="padding: 8px 0; color: #333;">{data['jobId']}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #666; font-weight: bold;">Source:</td>
                            <td style="padding: 8px 0; color: #333;">{data.get('source', 'Direct')}</td>
                        </tr>
                    </table>
                </div>
                
                <!-- Candidate Information -->
                <div style="background-color: #f8f9fa; border-radius: 8px; padding: 20px; margin-bottom: 20px; border-left: 4px solid #28a745;">
                    <h2 style="color: #2c3e50; margin: 0 0 15px 0; font-size: 18px;">👤 Candidate Information</h2>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px 0; color: #666; font-weight: bold; width: 30%;">Name:</td>
                            <td style="padding: 8px 0; color: #333;">{data['firstName']} {data['lastName']}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #666; font-weight: bold;">Email:</td>
                            <td style="padding: 8px 0; color: #333;"><a href="mailto:{data['email']}">{data['email']}</a></td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #666; font-weight: bold;">Phone:</td>
                            <td style="padding: 8px 0; color: #333;"><a href="tel:{data['phone']}">{data['phone']}</a></td>
                        </tr>
                    </table>
                </div>
                
                <!-- Attachments -->
                <div style="background-color: #fff3cd; border-radius: 8px; padding: 20px; margin-bottom: 20px; border-left: 4px solid #ffc107;">
                    <h2 style="color: #856404; margin: 0 0 15px 0; font-size: 18px;">📎 Attachments</h2>
                    <p style="margin: 0; color: #856404;">Please see attached files for resume and cover letter (if provided).</p>
                </div>
                
                <!-- Footer -->
                <div style="text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee;">
                    <p style="margin: 0; font-size: 12px; color: #6c757d;">
                        JobPulse™ Processing & Automation System
                    </p>
                </div>
                
            </div>
        </body>
        </html>
        """
        
        return html_content
    
    def _build_application_email_text(self, data: Dict, is_stsi: bool = False) -> str:
        """Build plain text email content for job application"""
        
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        
        # Clean up job title by removing URL encoding
        import urllib.parse
        clean_job_title = urllib.parse.unquote(data['jobTitle']).replace('+', ' ')
        
        # Determine company name
        company_name = "STSI (Staffing Technical Services Inc.)" if is_stsi else "Myticas Consulting"
        
        text_content = f"""
Job posting is on behalf of {company_name}

POSITION DETAILS:
Job Title: {clean_job_title}
Bullhorn ID: {data['jobId']}
Source: {data.get('source', 'Direct')}

CANDIDATE INFORMATION:
Name: {data['firstName']} {data['lastName']}
Email: {data['email']}
Phone: {data['phone']}

ATTACHMENTS:
Please see attached files for resume and cover letter (if provided).

---
JobPulse™ Processing & Automation System
        """
        
        return text_content.strip()
    
    def _create_attachment(self, file: FileStorage, file_type: str) -> Optional[Attachment]:
        """Create email attachment from uploaded file"""
        try:
            # Read file content
            file.seek(0)  # Reset file pointer
            file_content = file.read()
            
            # Encode file content
            encoded_content = base64.b64encode(file_content).decode()
            
            # Determine MIME type
            filename = file.filename.lower()
            if filename.endswith('.pdf'):
                mime_type = "application/pdf"
            elif filename.endswith('.docx'):
                mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            elif filename.endswith('.doc'):
                mime_type = "application/msword"
            else:
                mime_type = "application/octet-stream"
            
            # Create attachment
            attachment = Attachment(
                file_content=encoded_content,
                file_type=mime_type,
                file_name=file.filename,
                disposition="attachment"
            )
            
            return attachment
            
        except Exception as e:
            logger.error(f"Error creating {file_type} attachment: {str(e)}")
            return None
    
    def _create_logo_attachment(self, is_stsi: bool = False) -> Optional[Attachment]:
        """Create inline logo attachment for email based on branding"""
        try:
            # Determine which logo to use
            if is_stsi:
                logo_path = "static/stsi-logo.png"
                logo_filename = "stsi-logo.png"
                content_id = "stsi_logo"
            else:
                logo_path = "static/myticas-logo.png"
                logo_filename = "myticas-logo.png"
                content_id = "myticas_logo"
            
            if not os.path.exists(logo_path):
                logger.warning(f"Logo file not found: {logo_path} - skipping logo attachment")
                return None
            
            # Read logo file
            with open(logo_path, 'rb') as logo_file:
                logo_content = logo_file.read()
            
            # Encode logo content
            encoded_logo = base64.b64encode(logo_content).decode()
            
            # Create inline attachment
            logo_attachment = Attachment(
                file_content=encoded_logo,
                file_type="image/png",
                file_name=logo_filename,
                disposition="inline",
                content_id=content_id
            )
            
            return logo_attachment
            
        except Exception as e:
            logger.error(f"Error creating logo attachment: {str(e)}")
            return None