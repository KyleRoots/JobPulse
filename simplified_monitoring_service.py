"""
Simplified Tearsheet Monitoring Service
Monitors tearsheets for job additions, removals, and modifications only
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional, Set
from sqlalchemy import and_
from models import db, BullhornActivity, TearsheetJobHistory
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from email_service import EmailService
from ftp_service import FTPService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SimplifiedMonitoringService:
    def __init__(self):
        self.bullhorn = BullhornService()
        self.xml_service = XMLIntegrationService()
        self.email_service = EmailService()
        self.ftp_service = FTPService()
        
        # Fields to monitor for modifications
        self.monitored_fields = [
            'title',
            'publicDescription', 
            'address.city',
            'address.state',
            'address.countryName',
            'owner',  # assigned recruiter
            'onSite'  # remote type
        ]
        
    def get_tearsheet_job_data(self, tearsheet_id: int, tearsheet_name: str) -> Dict[int, Dict]:
        """Get all current jobs in a tearsheet with their key fields"""
        jobs = self.bullhorn.get_tearsheet_jobs(tearsheet_id)
        
        job_data = {}
        for job in jobs:
            job_id = job.get('id')
            if not job_id:
                continue
                
            # Extract key fields for comparison
            address = job.get('address', {})
            owner = job.get('owner', {})
            
            job_data[job_id] = {
                'id': job_id,
                'title': job.get('title', ''),
                'description': job.get('publicDescription', ''),
                'city': address.get('city', ''),
                'state': address.get('state', ''),
                'country': address.get('countryName', ''),
                'owner_name': f"{owner.get('firstName', '')} {owner.get('lastName', '')}".strip(),
                'remote_type': job.get('onSite', ''),
                'tearsheet_name': tearsheet_name,
                'tearsheet_id': tearsheet_id
            }
            
        return job_data
    
    def get_previous_job_state(self, tearsheet_id: int) -> Dict[int, Dict]:
        """Get the previous state of jobs in a tearsheet from history"""
        history_records = TearsheetJobHistory.query.filter_by(
            tearsheet_id=tearsheet_id,
            is_current=True
        ).all()
        
        previous_state = {}
        for record in history_records:
            previous_state[record.job_id] = {
                'id': record.job_id,
                'title': record.job_title,
                'description': record.job_description,
                'city': record.job_city,
                'state': record.job_state,
                'country': record.job_country,
                'owner_name': record.job_owner_name,
                'remote_type': record.job_remote_type
            }
            
        return previous_state
    
    def detect_job_modifications(self, previous_job: Dict, current_job: Dict) -> List[str]:
        """Detect which fields were modified between previous and current state"""
        modifications = []
        
        field_mapping = {
            'title': 'Job Title',
            'description': 'Description',
            'city': 'City',
            'state': 'State',
            'country': 'Country',
            'owner_name': 'Assigned Recruiter',
            'remote_type': 'Remote Type'
        }
        
        for field, display_name in field_mapping.items():
            previous_value = previous_job.get(field, '')
            current_value = current_job.get(field, '')
            
            # Normalize for comparison
            if field == 'description':
                # For descriptions, just check if they're substantially different
                if len(previous_value) != len(current_value):
                    modifications.append(f"{display_name} (length changed)")
            elif str(previous_value).strip() != str(current_value).strip():
                modifications.append(f"{display_name}: '{previous_value}' → '{current_value}'")
                
        return modifications
    
    def update_job_history(self, tearsheet_id: int, current_jobs: Dict[int, Dict]):
        """Update the job history table with current state"""
        # Mark all existing records as not current
        TearsheetJobHistory.query.filter_by(
            tearsheet_id=tearsheet_id,
            is_current=True
        ).update({'is_current': False})
        
        # Add new current records
        for job_id, job_data in current_jobs.items():
            history = TearsheetJobHistory(
                tearsheet_id=tearsheet_id,
                job_id=job_id,
                job_title=job_data['title'],
                job_description=job_data['description'],
                job_city=job_data['city'],
                job_state=job_data['state'],
                job_country=job_data['country'],
                job_owner_name=job_data['owner_name'],
                job_remote_type=job_data['remote_type'],
                is_current=True,
                timestamp=datetime.now()
            )
            db.session.add(history)
            
        db.session.commit()
    
    def process_tearsheet_changes(self, tearsheet_id: int, tearsheet_name: str) -> Dict:
        """Process all changes for a tearsheet and return summary"""
        logger.info(f"Processing tearsheet: {tearsheet_name} (ID: {tearsheet_id})")
        
        # Get current state from Bullhorn
        current_jobs = self.get_tearsheet_job_data(tearsheet_id, tearsheet_name)
        current_job_ids = set(current_jobs.keys())
        
        # Get previous state from history
        previous_jobs = self.get_previous_job_state(tearsheet_id)
        previous_job_ids = set(previous_jobs.keys())
        
        # Detect changes
        added_jobs = current_job_ids - previous_job_ids
        removed_jobs = previous_job_ids - current_job_ids
        potentially_modified = current_job_ids & previous_job_ids
        
        changes = {
            'added': [],
            'removed': [],
            'modified': []
        }
        
        # Process added jobs
        for job_id in added_jobs:
            job = current_jobs[job_id]
            changes['added'].append(job)
            
            # Log activity
            activity = BullhornActivity(
                job_id=job_id,
                tearsheet_id=tearsheet_id,
                tearsheet_name=tearsheet_name,
                action_type='added',
                job_title=job['title'],
                timestamp=datetime.now()
            )
            db.session.add(activity)
            
            # Add to XML
            logger.info(f"Adding job {job_id} to XML")
            self.xml_service.add_job_to_xml(job_id)
        
        # Process removed jobs
        for job_id in removed_jobs:
            job = previous_jobs[job_id]
            job['tearsheet_name'] = tearsheet_name
            changes['removed'].append(job)
            
            # Log activity
            activity = BullhornActivity(
                job_id=job_id,
                tearsheet_id=tearsheet_id,
                tearsheet_name=tearsheet_name,
                action_type='removed',
                job_title=job['title'],
                timestamp=datetime.now()
            )
            db.session.add(activity)
            
            # Remove from XML
            logger.info(f"Removing job {job_id} from XML")
            self.xml_service.remove_job_from_xml(job_id)
        
        # Process potentially modified jobs
        for job_id in potentially_modified:
            previous = previous_jobs[job_id]
            current = current_jobs[job_id]
            
            modifications = self.detect_job_modifications(previous, current)
            
            if modifications:
                job_info = current.copy()
                job_info['modifications'] = modifications
                changes['modified'].append(job_info)
                
                # Log activity
                activity = BullhornActivity(
                    job_id=job_id,
                    tearsheet_id=tearsheet_id,
                    tearsheet_name=tearsheet_name,
                    action_type='modified',
                    job_title=current['title'],
                    timestamp=datetime.now(),
                    details='; '.join(modifications)
                )
                db.session.add(activity)
                
                # Update in XML
                logger.info(f"Updating job {job_id} in XML")
                self.xml_service.update_job_in_xml(job_id)
        
        # Update history with current state
        self.update_job_history(tearsheet_id, current_jobs)
        
        # Commit all database changes
        db.session.commit()
        
        return changes
    
    def send_notifications(self, tearsheet_name: str, changes: Dict):
        """Send email notifications for changes"""
        if not any(changes.values()):
            return
            
        # Build email content
        subject = f"Bullhorn Tearsheet Updates - {tearsheet_name}"
        body_parts = []
        
        if changes['added']:
            body_parts.append(f"<h3>Jobs Added ({len(changes['added'])})</h3>")
            for job in changes['added']:
                body_parts.append(f"<li><strong>{job['title']}</strong> (ID: {job['id']})")
                body_parts.append(f"<br>Location: {job['city']}, {job['state']}, {job['country']}")
                body_parts.append(f"<br>Remote Type: {job['remote_type']}")
                body_parts.append(f"<br>Recruiter: {job['owner_name']}</li><br>")
        
        if changes['removed']:
            body_parts.append(f"<h3>Jobs Removed ({len(changes['removed'])})</h3>")
            for job in changes['removed']:
                body_parts.append(f"<li><strong>{job['title']}</strong> (ID: {job['id']})</li>")
        
        if changes['modified']:
            body_parts.append(f"<h3>Jobs Modified ({len(changes['modified'])})</h3>")
            for job in changes['modified']:
                body_parts.append(f"<li><strong>{job['title']}</strong> (ID: {job['id']})")
                body_parts.append("<br>Changes:")
                for mod in job['modifications']:
                    body_parts.append(f"<br>  • {mod}")
                body_parts.append("</li><br>")
        
        body = ''.join(body_parts)
        
        # Send email
        self.email_service.send_notification_email(subject, body)
        logger.info(f"Sent notification email for {tearsheet_name}")
    
    def upload_xml_files(self):
        """Upload XML files to FTP and ensure synchronization"""
        import os
        
        try:
            # Upload main XML file
            if os.path.exists('myticas-job-feed.xml'):
                self.ftp_service.upload_file('myticas-job-feed.xml', 'myticas-job-feed.xml')
                logger.info("Uploaded main XML file to FTP")
                
            # Copy to scheduled version for consistency
            if os.path.exists('myticas-job-feed.xml'):
                import shutil
                shutil.copy2('myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml')
                logger.info("Synchronized scheduled XML file")
                
        except Exception as e:
            logger.error(f"Error uploading XML files: {str(e)}")
    
    def monitor_all_tearsheets(self):
        """Monitor all configured tearsheets for changes"""
        tearsheets = [
            (1256, "Ottawa Sponsored Jobs"),
            (1264, "VMS Sponsored Jobs"),
            (1499, "Clover Sponsored Jobs"),
            (1258, "Cleveland Sponsored Jobs"),
            (1257, "Chicago Sponsored Jobs")
        ]
        
        if not self.bullhorn.authenticate():
            logger.error("Failed to authenticate with Bullhorn")
            return
            
        total_changes = {
            'added': 0,
            'removed': 0,
            'modified': 0
        }
        
        for tearsheet_id, tearsheet_name in tearsheets:
            try:
                changes = self.process_tearsheet_changes(tearsheet_id, tearsheet_name)
                
                # Update totals
                total_changes['added'] += len(changes['added'])
                total_changes['removed'] += len(changes['removed'])
                total_changes['modified'] += len(changes['modified'])
                
                # Send notifications if there are changes
                if any(changes.values()):
                    self.send_notifications(tearsheet_name, changes)
                    
            except Exception as e:
                logger.error(f"Error processing tearsheet {tearsheet_name}: {str(e)}")
        
        # Upload XML files if there were any changes
        if any(total_changes.values()):
            self.upload_xml_files()
            logger.info(f"Monitoring complete. Total changes: {total_changes}")
        else:
            logger.info("Monitoring complete. No changes detected.")


# For testing
if __name__ == "__main__":
    import os
    os.environ['DATABASE_URL'] = 'postgresql://user:pass@localhost/db'
    
    monitor = SimplifiedMonitoringService()
    monitor.monitor_all_tearsheets()