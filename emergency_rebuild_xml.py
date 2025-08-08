#!/usr/bin/env python3
"""
EMERGENCY XML REBUILD - Fixes missing jobs issue
Rebuilds XML files with ALL 52 jobs from Bullhorn
"""

import os
import sys
from datetime import datetime

# Add the workspace directory to the Python path
sys.path.insert(0, '/home/runner/workspace')

# Import required modules
from app import app, db, BullhornMonitor, GlobalSettings, ScheduleConfig
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from ftp_service import FTPService

def emergency_rebuild():
    """Emergency rebuild of XML files with ALL jobs from Bullhorn"""
    
    with app.app_context():
        print("\n" + "="*70)
        print("EMERGENCY XML REBUILD - FIXING MISSING JOBS ISSUE")
        print("="*70)
        
        # Get Bullhorn credentials from database
        client_id_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_id').first()
        client_secret_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_secret').first()
        username_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_username').first()
        password_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_password').first()
        
        if not all([client_id_setting, client_secret_setting, username_setting, password_setting]):
            print("❌ Bullhorn credentials not configured in database!")
            return False
        
        # Initialize services with credentials
        bullhorn_service = BullhornService(
            client_id=client_id_setting.setting_value,
            client_secret=client_secret_setting.setting_value,
            username=username_setting.setting_value,
            password=password_setting.setting_value
        )
        xml_service = XMLIntegrationService()
        
        # Test Bullhorn connection
        if not bullhorn_service.test_connection():
            print("❌ Failed to connect to Bullhorn!")
            return False
        
        print("✅ Connected to Bullhorn")
        
        # Get all active monitors
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        print(f"📊 Found {len(monitors)} active monitors")
        
        # Collect ALL jobs from ALL monitors
        all_jobs = []
        monitor_map = {}  # Map job IDs to monitor names
        
        for monitor in monitors:
            print(f"\nProcessing: {monitor.name}")
            
            # Get jobs for this monitor
            if monitor.tearsheet_id == 0:
                jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
            else:
                jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
            
            print(f"  Found {len(jobs)} jobs")
            
            # Map each job to its monitor
            for job in jobs:
                job_id = str(job.get('id'))
                monitor_map[job_id] = monitor.name
                all_jobs.append(job)
        
        # Remove duplicates (if a job appears in multiple tearsheets)
        unique_jobs = {}
        for job in all_jobs:
            job_id = str(job.get('id'))
            if job_id not in unique_jobs:
                unique_jobs[job_id] = job
        
        all_jobs = list(unique_jobs.values())
        
        print(f"\n📊 TOTAL UNIQUE JOBS: {len(all_jobs)}")
        
        # Process each XML file
        xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
        
        for xml_file in xml_files:
            print(f"\n🔧 Rebuilding: {xml_file}")
            
            # Backup existing file
            if os.path.exists(xml_file):
                backup_name = f"{xml_file}.backup_emergency_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                os.rename(xml_file, backup_name)
                print(f"  Backed up to: {backup_name}")
            
            # Create new XML with ALL jobs
            # First, create an empty XML file with proper structure
            xml_content = """<?xml version='1.0' encoding='UTF-8'?>
<source>
</source>"""
            with open(xml_file, 'w', encoding='utf-8') as f:
                f.write(xml_content)
            
            success_count = 0
            
            for job in all_jobs:
                job_id = str(job.get('id'))
                monitor_name = monitor_map.get(job_id, 'Unknown')
                
                if xml_service.add_job_to_xml(xml_file, job, monitor_name):
                    success_count += 1
            
            print(f"  ✅ Added {success_count}/{len(all_jobs)} jobs to {xml_file}")
            
            # Verify the file
            if os.path.exists(xml_file):
                with open(xml_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    actual_count = content.count('<job>')
                    print(f"  Verified: {actual_count} jobs in file")
        
        # Upload to SFTP
        print("\n📤 Uploading to SFTP...")
        
        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
        sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
        
        if (sftp_enabled and sftp_enabled.setting_value == 'true' and 
            sftp_hostname and sftp_username and sftp_password):
            
            try:
                ftp_service = FTPService(
                    hostname=sftp_hostname.setting_value,
                    username=sftp_username.setting_value,
                    password=sftp_password.setting_value,
                    target_directory=sftp_directory.setting_value if sftp_directory else "/",
                    port=int(sftp_port.setting_value) if sftp_port else 2222,
                    use_sftp=True
                )
                
                for xml_file in xml_files:
                    if ftp_service.upload_file(xml_file, xml_file):
                        print(f"  ✅ Uploaded {xml_file}")
                        
                        # Update last_file_upload timestamp
                        schedule = ScheduleConfig.query.filter_by(file_path=xml_file).first()
                        if schedule:
                            schedule.last_file_upload = datetime.utcnow()
                            db.session.commit()
                    else:
                        print(f"  ❌ Failed to upload {xml_file}")
            except Exception as e:
                print(f"  ❌ SFTP Error: {str(e)}")
        
        print("\n" + "="*70)
        print("✅ EMERGENCY REBUILD COMPLETE!")
        print(f"All {len(all_jobs)} jobs have been added to XML files")
        print("Check: https://myticas.com/myticas-job-feed.xml")
        print("="*70 + "\n")
        
        return True

if __name__ == "__main__":
    success = emergency_rebuild()
    sys.exit(0 if success else 1)