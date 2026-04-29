import logging
import traceback
from datetime import datetime

from sendgrid.helpers.mail import Mail, Email, To, Content

logger = logging.getLogger(__name__)


class BullhornNotificationMixin:

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
                logger.error("EmailService: No SendGrid API key available")
                return False
            
            # Check for recent duplicate notifications
            if self._check_recent_notification('bullhorn_notification', to_email, monitor_name=monitor_name):
                logger.info(f"DUPLICATE PREVENTION: Skipping duplicate Bullhorn notification for {monitor_name}")
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
                logger.warning(
                    f"xml_sync_info received as {type(xml_sync_info)}, converting to empty dict"
                )
                xml_sync_info = {}

            # Debug logging to trace the issue
            logger.info(
                f"Email notification data - added_jobs type: {type(added_jobs)}, modified_jobs type: {type(modified_jobs)}"
            )
            if added_jobs:
                logger.info(
                    f"First added job type: {type(added_jobs[0])}, content: {added_jobs[0]}"
                )
            if modified_jobs:
                logger.info(
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
                        logger.error(
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
                    Scout Genius™ Automation Platform
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
            
            text_content += "Scout Genius™ Automation Platform"

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
                logger.info(
                    f"Bullhorn notification sent successfully to {to_email}")
                return True
            else:
                logger.error(
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
            logger.error(f"Failed to send Bullhorn notification: {str(e)}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
