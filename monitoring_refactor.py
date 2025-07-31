#!/usr/bin/env python3
"""
Refactored monitoring solution - breaking down the complex process_bullhorn_monitors function
into smaller, manageable pieces for long-term sustainability
"""

from datetime import datetime, timedelta
import json
import os
from typing import List, Dict, Set, Tuple
from flask import current_app as app

class MonitoringService:
    """Simplified monitoring service that breaks down complex logic into manageable pieces"""
    
    def __init__(self, db_session):
        self.db = db_session
        self.bullhorn = None
        self.xml_service = None
        self.email_service = None
        # Import models and services
        from app import BullhornMonitor, BullhornActivity, GlobalSettings, ScheduleConfig
        from ftp_service import FTPService
        from xml_integration_service import XMLIntegrationService
        self.BullhornMonitor = BullhornMonitor
        self.BullhornActivity = BullhornActivity
        self.GlobalSettings = GlobalSettings
        self.FTPService = FTPService
        self.ScheduleConfig = ScheduleConfig
        self.xml_service = XMLIntegrationService()
    
    def process_all_monitors(self):
        """Main entry point - simplified monitoring process"""
        try:
            # Step 1: Fix overdue monitors
            self.fix_overdue_monitors()
            
            # Step 2: Get monitors due for checking
            due_monitors = self.get_due_monitors()
            if not due_monitors:
                app.logger.info("No monitors due for checking")
                return
            
            # Step 3: Initialize Bullhorn connection once
            if not self.init_bullhorn_connection():
                app.logger.error("Failed to connect to Bullhorn")
                return
            
            # Step 4: Process each monitor and collect all jobs
            all_jobs = []
            for monitor in due_monitors:
                jobs = self.process_single_monitor(monitor)
                all_jobs.extend(jobs)
            
            # Step 5: Run comprehensive sync with all collected jobs
            if due_monitors:  # Only sync if we processed monitors
                self.run_comprehensive_sync(all_jobs, due_monitors)
            
            # Step 6: Perform health check
            self.perform_health_check()
            
        except Exception as e:
            app.logger.error(f"Fatal error in monitoring service: {str(e)}")
            self.db.session.rollback()
    
    def fix_overdue_monitors(self):
        """Fix monitors that are overdue by more than 10 minutes"""
        current_time = datetime.utcnow()
        overdue_threshold = current_time - timedelta(minutes=10)
        
        overdue_monitors = self.BullhornMonitor.query.filter(
            self.BullhornMonitor.is_active == True,
            self.BullhornMonitor.next_check < overdue_threshold
        ).all()
        
        if overdue_monitors:
            app.logger.warning(f"Found {len(overdue_monitors)} overdue monitors, fixing timing...")
            for monitor in overdue_monitors:
                monitor.last_check = current_time
                monitor.next_check = current_time + timedelta(minutes=5)
            
            try:
                self.db.session.commit()
                app.logger.info("Timing corrections committed successfully")
            except Exception as e:
                app.logger.error(f"Failed to fix timing: {str(e)}")
                self.db.session.rollback()
    
    def get_due_monitors(self):
        """Get all monitors due for checking"""
        current_time = datetime.utcnow()
        return self.BullhornMonitor.query.filter(
            self.BullhornMonitor.is_active == True,
            self.BullhornMonitor.next_check <= current_time
        ).all()
    
    def init_bullhorn_connection(self) -> bool:
        """Initialize Bullhorn service connection"""
        try:
            # Import needed modules
            from app import GlobalSettings, get_bullhorn_service
            self.bullhorn = get_bullhorn_service()
            return self.bullhorn.test_connection()
        except Exception as e:
            app.logger.error(f"Bullhorn connection error: {str(e)}")
            return False
    
    def process_single_monitor(self, monitor):
        """Process a single monitor and return its jobs"""
        try:
            app.logger.info(f"Processing monitor: {monitor.name}")
            
            # Get current jobs
            if monitor.tearsheet_id == 0:
                current_jobs = self.bullhorn.get_jobs_by_query(monitor.tearsheet_name)
            else:
                current_jobs = self.bullhorn.get_tearsheet_jobs(monitor.tearsheet_id)
            
            # Compare with previous snapshot
            previous_jobs = []
            if monitor.last_job_snapshot:
                try:
                    previous_jobs = json.loads(monitor.last_job_snapshot)
                except:
                    pass
            
            # Find changes
            changes = self.bullhorn.compare_job_lists(previous_jobs, current_jobs)
            
            # Log changes as activities
            self.log_monitor_changes(monitor, changes, len(current_jobs))
            
            # Update monitor snapshot and timing
            monitor.last_job_snapshot = json.dumps(current_jobs)
            monitor.last_check = datetime.utcnow()
            monitor.calculate_next_check()
            
            # Commit monitor updates immediately
            try:
                self.db.session.commit()
                app.logger.info(f"Monitor {monitor.name} updated successfully")
            except Exception as e:
                app.logger.error(f"Failed to update monitor {monitor.name}: {str(e)}")
                self.db.session.rollback()
            
            return current_jobs
            
        except Exception as e:
            app.logger.error(f"Error processing monitor {monitor.name}: {str(e)}")
            self.log_error(monitor, str(e))
            return []
    
    def log_monitor_changes(self, monitor, changes, job_count):
        """Log monitor changes as activities"""
        added = changes.get('added', [])
        removed = changes.get('removed', [])
        modified = changes.get('modified', [])
        
        # Log individual job changes
        for job in added:
            activity = self.BullhornActivity(
                monitor_id=monitor.id,
                activity_type='job_added',
                job_id=str(job.get('id')),
                job_title=job.get('title', 'Unknown'),
                details=f"Job added to {monitor.name}"
            )
            self.db.session.add(activity)
        
        for job in removed:
            activity = self.BullhornActivity(
                monitor_id=monitor.id,
                activity_type='job_removed',
                job_id=str(job.get('id')),
                job_title=job.get('title', 'Unknown'),
                details=f"Job removed from {monitor.name}"
            )
            self.db.session.add(activity)
        
        for job in modified:
            activity = self.BullhornActivity(
                monitor_id=monitor.id,
                activity_type='job_modified',
                job_id=str(job.get('id')),
                job_title=job.get('title', 'Unknown'),
                details=f"Job modified in {monitor.name}"
            )
            self.db.session.add(activity)
        
        # Log check completed if no changes
        if not (added or removed or modified):
            activity = self.BullhornActivity(
                monitor_id=monitor.id,
                activity_type='check_completed',
                details=f"Checked {monitor.tearsheet_name}. Found {job_count} jobs. No changes detected."
            )
            self.db.session.add(activity)
    
    def run_comprehensive_sync(self, all_jobs, monitors):
        """Run comprehensive XML sync with all collected jobs"""
        try:
            app.logger.info(f"Starting comprehensive sync with {len(all_jobs)} total jobs")
            
            # Process main XML files
            xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
            
            for xml_file in xml_files:
                if os.path.exists(xml_file):
                    self.sync_xml_file(xml_file, all_jobs, monitors)
            
            # Upload to SFTP if enabled
            self.upload_to_sftp()
            
            # Send notifications if needed
            self.send_pending_notifications()
            
        except Exception as e:
            app.logger.error(f"Comprehensive sync error: {str(e)}")
    
    def sync_xml_file(self, xml_file, all_jobs, monitors):
        """Sync a single XML file with Bullhorn jobs"""
        try:
            # Get current job IDs from XML
            xml_job_ids = self.get_xml_job_ids(xml_file)
            
            # Get all job IDs from Bullhorn
            bullhorn_job_ids = {str(job.get('id')) for job in all_jobs if job.get('id')}
            
            # Find differences
            missing_jobs = bullhorn_job_ids - xml_job_ids
            orphaned_jobs = xml_job_ids - bullhorn_job_ids
            
            app.logger.info(f"XML sync for {xml_file}: {len(missing_jobs)} missing, {len(orphaned_jobs)} orphaned")
            
            # Add missing jobs
            for job_id in missing_jobs:
                job = next((j for j in all_jobs if str(j.get('id')) == job_id), None)
                if job:
                    # Determine monitor name for proper company mapping
                    monitor_name = self.get_monitor_name_for_job(job_id, monitors)
                    self.xml_service.add_job_to_xml(xml_file, job, monitor_name, skip_ai_classification=False)
            
            # Update existing jobs
            existing_jobs = xml_job_ids.intersection(bullhorn_job_ids)
            for job_id in existing_jobs:
                job = next((j for j in all_jobs if str(j.get('id')) == job_id), None)
                if job:
                    monitor_name = self.get_monitor_name_for_job(job_id, monitors)
                    self.xml_service.update_job_in_xml(xml_file, job, monitor_name)
            
            # Remove orphaned jobs
            for job_id in orphaned_jobs:
                self.xml_service.remove_job_from_xml(xml_file, job_id)
            
            app.logger.info(f"XML sync completed for {xml_file}")
            
        except Exception as e:
            app.logger.error(f"Error syncing {xml_file}: {str(e)}")
    
    def get_xml_job_ids(self, xml_file):
        """Extract job IDs from XML file"""
        job_ids = set()
        try:
            from lxml import etree
            parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
            tree = etree.parse(xml_file, parser)
            root = tree.getroot()
            
            # Try bhatsid elements first
            for bhatsid in root.xpath('.//bhatsid'):
                if bhatsid.text:
                    # Extract job ID from CDATA format like "<![CDATA[ 32079 ]]>"
                    text = bhatsid.text.strip()
                    # Remove CDATA wrapper if present
                    if text.startswith('<![CDATA[') and text.endswith(']]>'):
                        text = text[9:-3].strip()
                    # Remove any remaining brackets
                    text = text.strip('<>[]! ')
                    if text.isdigit():
                        job_ids.add(text)
            
            # Fallback to extracting from titles
            if not job_ids:
                import re
                for title in root.xpath('.//job/title'):
                    if title.text:
                        match = re.search(r'\((\d+)\)', title.text)
                        if match:
                            job_ids.add(match.group(1))
            
        except Exception as e:
            app.logger.error(f"Error parsing {xml_file}: {str(e)}")
        
        return job_ids
    
    def get_monitor_name_for_job(self, job_id, monitors):
        """Determine which monitor a job belongs to"""
        for monitor in monitors:
            if monitor.last_job_snapshot:
                try:
                    jobs = json.loads(monitor.last_job_snapshot)
                    if any(str(job.get('id')) == job_id for job in jobs):
                        return monitor.name
                except:
                    continue
        return "Unknown"
    
    def upload_to_sftp(self):
        """Upload XML files to SFTP server"""
        try:
            # Get SFTP settings
            settings = {}
            for setting in self.GlobalSettings.query.all():
                settings[setting.setting_key] = setting.setting_value
            
            if settings.get('sftp_enabled') != 'true' or not all(k in settings for k in ['sftp_hostname', 'sftp_username', 'sftp_password']):
                app.logger.info("SFTP settings not configured, skipping upload")
                return
            
            ftp_service = self.FTPService(
                hostname=settings['sftp_hostname'],
                username=settings['sftp_username'],
                password=settings['sftp_password'],
                target_directory=settings.get('sftp_directory', '/'),
                port=int(settings.get('sftp_port', '2222')),
                use_sftp=True
            )
            
            # Upload main XML file
            success = ftp_service.upload_file('myticas-job-feed.xml', 'myticas-job-feed.xml')
            if success:
                app.logger.info("SFTP upload successful")
                # Update last upload timestamp
                for schedule in self.ScheduleConfig.query.filter_by(is_active=True).all():
                    schedule.last_file_upload = datetime.utcnow()
                self.db.commit()
            else:
                app.logger.error(f"SFTP upload failed")
                
        except Exception as e:
            app.logger.error(f"SFTP upload error: {str(e)}")
    
    def send_pending_notifications(self):
        """Send email notifications for pending activities"""
        try:
            # Get pending notifications
            pending = self.BullhornActivity.query.filter_by(notification_sent=False).all()
            if not pending:
                return
            
            # Group by monitor
            by_monitor = {}
            for activity in pending:
                if activity.monitor_id not in by_monitor:
                    by_monitor[activity.monitor_id] = []
                by_monitor[activity.monitor_id].append(activity)
            
            # Send notifications for each monitor
            for monitor_id, activities in by_monitor.items():
                monitor = self.BullhornMonitor.query.get(monitor_id)
                if monitor:
                    self.send_monitor_notification(monitor, activities)
            
        except Exception as e:
            app.logger.error(f"Notification error: {str(e)}")
    
    def send_monitor_notification(self, monitor, activities):
        """Send notification for a specific monitor's activities"""
        try:
            from email_service import EmailService
            import time
            
            # Get email settings
            settings = {}
            for setting in self.GlobalSettings.query.all():
                settings[setting.setting_key] = setting.setting_value
            
            if settings.get('email_enabled') != 'true':
                return
            
            # Group activities by type
            added_jobs = []
            removed_jobs = []
            modified_jobs = []
            
            for activity in activities:
                if activity.activity_type == 'job_added' and activity.job_id:
                    added_jobs.append({
                        'id': activity.job_id,
                        'title': activity.job_title or 'Unknown'
                    })
                elif activity.activity_type == 'job_removed' and activity.job_id:
                    removed_jobs.append({
                        'id': activity.job_id,
                        'title': activity.job_title or 'Unknown'
                    })
                elif activity.activity_type == 'job_modified' and activity.job_id:
                    modified_jobs.append({
                        'id': activity.job_id,
                        'title': activity.job_title or 'Unknown',
                        'changes': activity.details or 'Updated'
                    })
            
            # Only send if there are actual job changes
            if not (added_jobs or removed_jobs or modified_jobs):
                return
            
            # Send email
            email_service = EmailService()
            time.sleep(15)  # Wait for XML to propagate
            
            success = email_service.send_bullhorn_notification(
                to_email=settings.get('email_address', 'kroots@myticas.com'),
                monitor_name=monitor.name,
                added_jobs=added_jobs,
                removed_jobs=removed_jobs,
                modified_jobs=modified_jobs,
                summary={
                    'added': len(added_jobs),
                    'removed': len(removed_jobs),
                    'modified': len(modified_jobs)
                }
            )
            
            if success:
                # Mark activities as sent
                for activity in activities:
                    activity.notification_sent = True
                self.db.commit()
                app.logger.info(f"Email sent for {monitor.name}: {len(activities)} activities")
            else:
                app.logger.error(f"Failed to send email for {monitor.name}")
                
        except Exception as e:
            app.logger.error(f"Email notification error: {str(e)}")
    
    def perform_health_check(self):
        """Perform final health check on all monitors"""
        try:
            all_monitors = self.BullhornMonitor.query.filter_by(is_active=True).all()
            healthy_count = 0
            
            for monitor in all_monitors:
                if monitor.next_check and monitor.next_check > datetime.utcnow():
                    healthy_count += 1
            
            app.logger.info(f"Health check: {healthy_count}/{len(all_monitors)} monitors have healthy timing")
            
        except Exception as e:
            app.logger.error(f"Health check error: {str(e)}")
    
    def log_error(self, monitor, error_message):
        """Log an error for a monitor"""
        activity = self.BullhornActivity(
            monitor_id=monitor.id,
            activity_type='error',
            details=f"Error: {error_message}"
        )
        self.db.session.add(activity)
        try:
            self.db.session.commit()
        except:
            self.db.session.rollback()


def process_bullhorn_monitors_simple():
    """Simplified entry point for APScheduler"""
    # Import inside function to avoid circular imports
    from app import app, db, BullhornMonitor, BullhornActivity, GlobalSettings, ScheduleConfig
    from xml_integration_service import XMLIntegrationService
    from bullhorn_service import BullhornService
    from email_service import EmailService
    from ftp_service import FTPService
    
    with app.app_context():
        service = MonitoringService(db.session)
        # Initialize services inside app context
        service.xml_service = XMLIntegrationService()
        service.email_service = EmailService()
        service.BullhornMonitor = BullhornMonitor
        service.BullhornActivity = BullhornActivity
        service.GlobalSettings = GlobalSettings
        service.ScheduledProcessing = ScheduleConfig
        service.process_all_monitors()