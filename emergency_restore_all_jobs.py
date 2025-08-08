#!/usr/bin/env python3
"""
EMERGENCY SCRIPT: Restore all jobs from Bullhorn to XML
This bypasses the monitoring system's safeguards to force-add all jobs
"""
import os
import sys
import logging
from datetime import datetime
from app import app, db, BullhornMonitor
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from ftp_service import FTPService
from app import GlobalSettings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def emergency_restore():
    """Emergency restore all jobs from Bullhorn to XML files"""
    with app.app_context():
        try:
            # Get all active monitors
            monitors = BullhornMonitor.query.filter_by(is_active=True).all()
            logger.info(f"Found {len(monitors)} active monitors")
            
            # Initialize services using the helper function
            from app import get_bullhorn_service
            bullhorn_service = get_bullhorn_service()
            if not bullhorn_service.test_connection():
                logger.error("Failed to connect to Bullhorn")
                return False
            
            xml_service = XMLIntegrationService()
            
            # Collect all jobs from all tearsheets
            all_jobs = []
            tearsheet_jobs = {}
            
            for monitor in monitors:
                logger.info(f"Processing monitor: {monitor.name} (Tearsheet ID: {monitor.tearsheet_id})")
                
                if monitor.tearsheet_id == 0:
                    # Query-based monitor
                    jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                else:
                    # Tearsheet-based monitor
                    jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                
                logger.info(f"  Found {len(jobs)} jobs")
                tearsheet_jobs[monitor.name] = jobs
                all_jobs.extend(jobs)
            
            logger.info(f"\n=== TOTAL: {len(all_jobs)} jobs from all tearsheets ===")
            
            # Backup current XML files
            xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
            for xml_file in xml_files:
                if os.path.exists(xml_file):
                    backup_name = f"{xml_file}.backup_emergency_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    os.rename(xml_file, backup_name)
                    logger.info(f"Backed up {xml_file} to {backup_name}")
            
            # Create fresh XML files with all jobs
            for xml_file in xml_files:
                logger.info(f"\n=== Rebuilding {xml_file} ===")
                
                # Create new XML file
                xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n<source>\n</source>'
                with open(xml_file, 'w', encoding='utf-8') as f:
                    f.write(xml_content)
                
                # Add all jobs to XML
                jobs_added = 0
                for monitor_name, jobs in tearsheet_jobs.items():
                    for job in jobs:
                        if xml_service.add_job_to_xml(xml_file, job, monitor_name):
                            jobs_added += 1
                            logger.info(f"  Added job {job.get('id')}: {job.get('title', 'Unknown')}")
                
                logger.info(f"✅ Added {jobs_added} jobs to {xml_file}")
            
            # Upload to SFTP
            logger.info("\n=== Uploading to SFTP ===")
            sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
            sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
            sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
            
            if sftp_hostname and sftp_username and sftp_password:
                ftp = FTPService(
                    hostname=sftp_hostname.setting_value,
                    username=sftp_username.setting_value,
                    password=sftp_password.setting_value,
                    port=2222,
                    use_sftp=True
                )
                
                for xml_file in xml_files:
                    if ftp.upload_file(xml_file, xml_file):
                        logger.info(f"✅ Uploaded {xml_file} to SFTP")
                    else:
                        logger.error(f"❌ Failed to upload {xml_file}")
            else:
                logger.warning("SFTP credentials not found in database")
            
            logger.info("\n=== EMERGENCY RESTORE COMPLETE ===")
            logger.info(f"Successfully restored {len(all_jobs)} jobs to XML files")
            return True
            
        except Exception as e:
            logger.error(f"Emergency restore failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    logger.info("=== STARTING EMERGENCY RESTORE ===")
    success = emergency_restore()
    if success:
        logger.info("✅ Emergency restore completed successfully")
        sys.exit(0)
    else:
        logger.error("❌ Emergency restore failed")
        sys.exit(1)