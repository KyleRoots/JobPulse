"""
XML Change Monitor Service

Monitors the live XML file for any changes to job data by comparing snapshots.
Sends email notifications when changes are detected.
"""

import os
import json
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from lxml import etree
# Email service will be passed in from app.py

class XMLChangeMonitor:
    def __init__(self, xml_file_url: str = "https://myticas.com/myticas-job-feed-v2.xml"):
        self.xml_url = xml_file_url
        self.snapshot_file = "xml_snapshot.json"
        self.logger = logging.getLogger(__name__)
        
    def extract_job_data_from_xml(self, xml_content: str) -> Dict[str, Dict]:
        """Extract all job data from XML content for comparison"""
        try:
            root = etree.fromstring(xml_content.encode('utf-8'))
            jobs = {}
            
            for job in root.xpath('.//job'):
                # Extract job ID (try multiple fields)
                job_id = None
                
                # Try bhatsid first
                bhatsid = job.xpath('.//bhatsid')
                if bhatsid and bhatsid[0].text:
                    job_id = bhatsid[0].text.strip()
                else:
                    # Try to extract from URL
                    url_elem = job.xpath('.//url')
                    if url_elem and url_elem[0].text and 'jobId=' in url_elem[0].text:
                        job_id = url_elem[0].text.split('jobId=')[-1].strip()
                
                if not job_id:
                    continue
                
                # Extract all job fields
                job_data = {
                    'id': job_id,
                    'title': self._get_text_content(job.xpath('.//title')),
                    'company': self._get_text_content(job.xpath('.//company')),
                    'description': self._get_text_content(job.xpath('.//description')),
                    'location': self._get_text_content(job.xpath('.//location')),
                    'country': self._get_text_content(job.xpath('.//country')),
                    'date': self._get_text_content(job.xpath('.//date')),
                    'referencenumber': self._get_text_content(job.xpath('.//referencenumber')),
                    'remotetype': self._get_text_content(job.xpath('.//remotetype')),
                    'url': self._get_text_content(job.xpath('.//url')),
                    'salary': self._get_text_content(job.xpath('.//salary')),
                    'jobfunction': self._get_text_content(job.xpath('.//jobfunction')),
                    'jobindustries': self._get_text_content(job.xpath('.//jobindustries')),
                    'senioritylevel': self._get_text_content(job.xpath('.//senioritylevel'))
                }
                
                jobs[job_id] = job_data
                
            return jobs
        except Exception as e:
            self.logger.error(f"Error extracting job data from XML: {str(e)}")
            return {}
    
    def _get_text_content(self, elements: List) -> str:
        """Safely extract text content from XML elements"""
        if not elements or not elements[0].text:
            return ""
        
        content = elements[0].text.strip()
        
        # Remove CDATA wrapper if present
        if content.startswith('<![CDATA[') and content.endswith(']]>'):
            content = content[9:-3]
            
        return content
    
    def download_live_xml(self) -> Optional[str]:
        """Download the current live XML file"""
        try:
            import requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; Myticas Job Feed Monitor/1.0)',
                'Accept': 'application/xml, text/xml, */*',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive'
            }
            response = requests.get(self.xml_url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.text
        except Exception as e:
            self.logger.error(f"Error downloading live XML: {str(e)}")
            return None
    
    def load_previous_snapshot(self) -> Dict[str, Dict]:
        """Load the previous XML snapshot from disk"""
        try:
            if os.path.exists(self.snapshot_file):
                with open(self.snapshot_file, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            self.logger.error(f"Error loading previous snapshot: {str(e)}")
            return {}
    
    def save_current_snapshot(self, jobs_data: Dict[str, Dict]) -> None:
        """Save the current job data as a snapshot"""
        try:
            snapshot = {
                'timestamp': datetime.utcnow().isoformat(),
                'jobs': jobs_data
            }
            with open(self.snapshot_file, 'w') as f:
                json.dump(snapshot, f, indent=2)
        except Exception as e:
            self.logger.error(f"Error saving snapshot: {str(e)}")
    
    def compare_snapshots(self, previous_jobs: Dict[str, Dict], current_jobs: Dict[str, Dict]) -> Dict:
        """Compare two job snapshots and detect changes"""
        changes = {
            'added': [],
            'removed': [],
            'modified': [],
            'total_changes': 0
        }
        
        # CRITICAL: These fields should NEVER trigger modification notifications
        # They are set once and remain static (except during weekly automation for reference numbers)
        STATIC_FIELDS = {
            'referencenumber',     # Only changes for new jobs or weekly automation
            'jobfunction',         # AI classification - set once for new jobs
            'jobindustries',       # AI classification - set once for new jobs  
            'senioritylevel'       # AI classification - set once for new jobs
        }
        
        previous_ids = set(previous_jobs.keys())
        current_ids = set(current_jobs.keys())
        
        # Find added jobs
        added_ids = current_ids - previous_ids
        for job_id in added_ids:
            changes['added'].append(current_jobs[job_id])
        
        # Find removed jobs
        removed_ids = previous_ids - current_ids
        for job_id in removed_ids:
            changes['removed'].append(previous_jobs[job_id])
        
        # Find modified jobs
        common_ids = current_ids & previous_ids
        for job_id in common_ids:
            current_job = current_jobs[job_id]
            previous_job = previous_jobs[job_id]
            
            # Compare each field EXCEPT static fields
            field_changes = []
            for field in current_job.keys():
                # Skip ID and static fields when detecting modifications
                if field == 'id' or field in STATIC_FIELDS:
                    continue
                    
                if current_job.get(field, '') != previous_job.get(field, ''):
                    field_changes.append({
                        'field': field,
                        'old_value': previous_job.get(field, ''),
                        'new_value': current_job.get(field, '')
                    })
            
            if field_changes:
                changes['modified'].append({
                    'job': current_job,
                    'changes': field_changes
                })
        
        changes['total_changes'] = len(changes['added']) + len(changes['removed']) + len(changes['modified'])
        
        return changes
    
    def send_change_notification(self, changes: Dict, notification_email: str, email_service=None) -> bool:
        """Send email notification about detected changes"""
        try:
            if changes['total_changes'] == 0:
                self.logger.info("No changes detected, skipping email notification")
                return True
            
            if not email_service:
                self.logger.error("Email service not provided")
                return False
            
            # Prepare email content
            subject = f"Job Feed Update: {changes['total_changes']} Changes Detected"
            
            # Format changes for email
            added_jobs = []
            for job in changes['added']:
                added_jobs.append({
                    'id': job['id'],
                    'title': job['title'],
                    'company': job['company'],
                    'location': job['location']
                })
            
            removed_jobs = []
            for job in changes['removed']:
                removed_jobs.append({
                    'id': job['id'],
                    'title': job['title'],
                    'company': job['company'],
                    'location': job['location']
                })
            
            modified_jobs = []
            for mod in changes['modified']:
                job = mod['job']
                change_summary = []
                for change in mod['changes']:
                    change_summary.append(f"{change['field']}: {change['old_value'][:50]}... → {change['new_value'][:50]}...")
                
                modified_jobs.append({
                    'id': job['id'],
                    'title': job['title'],
                    'company': job['company'],
                    'changes': change_summary
                })
            
            # Send notification
            success = email_service.send_bullhorn_notification(
                to_email=notification_email,
                monitor_name="Live XML Monitor",
                added_jobs=added_jobs,
                removed_jobs=removed_jobs,
                modified_jobs=modified_jobs,
                summary={
                    'total_changes': changes['total_changes'],
                    'added_count': len(changes['added']),
                    'removed_count': len(changes['removed']),
                    'modified_count': len(changes['modified'])
                }
            )
            
            if success:
                self.logger.info(f"Change notification sent successfully to {notification_email}")
            else:
                self.logger.warning(f"Failed to send change notification to {notification_email}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error sending change notification: {str(e)}")
            return False
    
    def monitor_xml_changes(self, notification_email: str, email_service=None, enable_email_notifications: bool = True) -> Dict:
        """Main monitoring function - download, compare, and notify"""
        try:
            self.logger.info("🔍 XML CHANGE MONITOR: Starting live XML monitoring cycle")
            
            # Download current live XML
            current_xml = self.download_live_xml()
            if not current_xml:
                self.logger.error("Failed to download live XML")
                return {'success': False, 'error': 'Failed to download XML'}
            
            # Extract job data from current XML
            current_jobs = self.extract_job_data_from_xml(current_xml)
            self.logger.info(f"🔍 XML MONITOR: Extracted {len(current_jobs)} jobs from live XML")
            
            # Load previous snapshot
            previous_snapshot = self.load_previous_snapshot()
            previous_jobs = previous_snapshot.get('jobs', {})
            
            if previous_jobs:
                self.logger.info(f"🔍 XML MONITOR: Comparing with previous snapshot ({len(previous_jobs)} jobs)")
                
                # Compare snapshots
                changes = self.compare_snapshots(previous_jobs, current_jobs)
                
                self.logger.info(f"🔍 XML MONITOR: Changes detected:")
                self.logger.info(f"    ➕ Added: {len(changes['added'])} jobs")
                self.logger.info(f"    ➖ Removed: {len(changes['removed'])} jobs")
                self.logger.info(f"    🔄 Modified: {len(changes['modified'])} jobs")
                self.logger.info(f"    📊 Total changes: {changes['total_changes']}")
                
                # Send notification if changes detected and email notifications are enabled
                if changes['total_changes'] > 0 and enable_email_notifications:
                    email_sent = self.send_change_notification(changes, notification_email, email_service)
                    self.logger.info(f"📧 EMAIL NOTIFICATION: {'Sent successfully' if email_sent else 'Failed to send'}")
                elif changes['total_changes'] > 0:
                    self.logger.info(f"📧 EMAIL NOTIFICATION: Skipped (disabled) - {changes['total_changes']} changes detected")
                else:
                    self.logger.info("📧 EMAIL NOTIFICATION: No changes detected, no email sent")
            else:
                self.logger.info("🔍 XML MONITOR: No previous snapshot found, initializing monitoring")
                changes = {'added': [], 'removed': [], 'modified': [], 'total_changes': 0}
            
            # Save current snapshot for next comparison
            self.save_current_snapshot(current_jobs)
            
            return {
                'success': True,
                'changes': changes,
                'current_job_count': len(current_jobs),
                'previous_job_count': len(previous_jobs)
            }
            
        except Exception as e:
            self.logger.error(f"Error in XML change monitoring: {str(e)}")
            return {'success': False, 'error': str(e)}

    def monitor_xml_changes_with_content(self, xml_content: str, notification_email: str, email_service=None, enable_email_notifications: bool = True) -> Dict:
        """Monitor XML changes using provided XML content instead of downloading from website"""
        try:
            self.logger.info("🔍 XML CHANGE MONITOR: Starting monitoring cycle with provided XML content")
            
            # Extract job data from provided XML content
            current_jobs = self.extract_job_data_from_xml(xml_content)
            self.logger.info(f"🔍 XML MONITOR: Extracted {len(current_jobs)} jobs from generated XML")
            
            # Load previous snapshot
            previous_snapshot = self.load_previous_snapshot()
            previous_jobs = previous_snapshot.get('jobs', {})
            
            if previous_jobs:
                self.logger.info(f"🔍 XML MONITOR: Comparing with previous snapshot ({len(previous_jobs)} jobs)")
                
                # Compare snapshots
                changes = self.compare_snapshots(previous_jobs, current_jobs)
                
                self.logger.info(f"🔍 XML MONITOR: Changes detected:")
                self.logger.info(f"    ➕ Added: {len(changes['added'])} jobs")
                self.logger.info(f"    ➖ Removed: {len(changes['removed'])} jobs")
                self.logger.info(f"    🔄 Modified: {len(changes['modified'])} jobs")
                self.logger.info(f"    📊 Total changes: {changes['total_changes']}")
                
                # Send notification if changes detected and email notifications are enabled
                email_sent = False
                if changes['total_changes'] > 0 and enable_email_notifications:
                    email_sent = self.send_change_notification(changes, notification_email, email_service)
                    self.logger.info(f"📧 EMAIL NOTIFICATION: {'Sent successfully' if email_sent else 'Failed to send'}")
                elif changes['total_changes'] > 0:
                    self.logger.info(f"📧 EMAIL NOTIFICATION: Skipped (disabled) - {changes['total_changes']} changes detected")
                else:
                    self.logger.info("📧 EMAIL NOTIFICATION: No changes detected, no email sent")
            else:
                self.logger.info("🔍 XML MONITOR: No previous snapshot found, initializing monitoring")
                changes = {'added': [], 'removed': [], 'modified': [], 'total_changes': 0}
                email_sent = False
            
            # Save current snapshot for next comparison
            self.save_current_snapshot(current_jobs)
            
            return {
                'success': True,
                'changes': changes,
                'current_job_count': len(current_jobs),
                'previous_job_count': len(previous_jobs),
                'email_sent': email_sent
            }
            
        except Exception as e:
            self.logger.error(f"Error in XML change monitoring with content: {str(e)}")
            return {'success': False, 'error': str(e), 'email_sent': False}

def create_xml_monitor():
    """Factory function to create XML monitor instance"""
    return XMLChangeMonitor()