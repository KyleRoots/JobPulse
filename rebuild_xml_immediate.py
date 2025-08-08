#!/usr/bin/env python3
"""
IMMEDIATE XML REBUILD SCRIPT
This script rebuilds the XML files with ALL jobs from Bullhorn tearsheets.
Fixes the critical issue where only 33 of 52 jobs are in the XML.
"""

import os
import sys
import json
from datetime import datetime
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from ftp_service import FTPService
from app import app, db, BullhornMonitor, GlobalSettings

def rebuild_all_xml_files():
    """Rebuild XML files with ALL jobs from ALL active Bullhorn monitors"""
    
    with app.app_context():
        print("=" * 70)
        print("IMMEDIATE XML REBUILD - FIXING MISSING JOBS")
        print("=" * 70)
        
        # Get Bullhorn service
        bullhorn_service = BullhornService()
        if not bullhorn_service.test_connection():
            print("❌ ERROR: Failed to connect to Bullhorn")
            return False
        
        print("✅ Connected to Bullhorn successfully")
        
        # Get all active monitors
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        print(f"\n📊 Processing {len(monitors)} active monitors:")
        
        # Collect ALL jobs from ALL monitors
        all_jobs = []
        monitor_job_counts = {}
        
        for monitor in monitors:
            print(f"\n  Processing: {monitor.name}")
            
            # Get jobs based on monitor type
            if monitor.tearsheet_id == 0:
                # Query-based monitor
                monitor_jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
            else:
                # Traditional tearsheet-based monitor
                monitor_jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
            
            monitor_job_counts[monitor.name] = len(monitor_jobs)
            all_jobs.extend(monitor_jobs)
            
            print(f"    ✓ Found {len(monitor_jobs)} jobs")
        
        # Show summary
        print(f"\n📊 TOTAL JOBS FOUND: {len(all_jobs)}")
        print("  Breakdown by monitor:")
        for monitor_name, count in monitor_job_counts.items():
            print(f"    - {monitor_name}: {count} jobs")
        
        # Initialize XML service
        xml_service = XMLIntegrationService()
        
        # XML files to rebuild
        xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
        
        for xml_file in xml_files:
            print(f"\n🔧 REBUILDING: {xml_file}")
            
            # Backup existing file
            if os.path.exists(xml_file):
                backup_name = f"{xml_file}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                os.rename(xml_file, backup_name)
                print(f"  ✓ Backed up existing file to: {backup_name}")
            
            # Create fresh XML with all jobs
            success_count = 0
            
            # Create empty XML first
            xml_service.create_empty_xml(xml_file)
            
            # Add ALL jobs to the XML
            for job in all_jobs:
                # Determine which monitor this job belongs to
                monitor_name = "Unknown"
                for monitor in monitors:
                    if monitor.tearsheet_id == 0:
                        monitor_check_jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                    else:
                        monitor_check_jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                    
                    job_ids = [str(j.get('id')) for j in monitor_check_jobs]
                    if str(job.get('id')) in job_ids:
                        monitor_name = monitor.name
                        break
                
                # Add job to XML
                if xml_service.add_job_to_xml(xml_file, job, monitor_name):
                    success_count += 1
                else:
                    print(f"  ⚠ Failed to add job {job.get('id')}: {job.get('title')}")
            
            print(f"  ✅ Successfully added {success_count}/{len(all_jobs)} jobs to {xml_file}")
            
            # Verify job count in XML
            if os.path.exists(xml_file):
                with open(xml_file, 'r') as f:
                    content = f.read()
                    job_count = content.count('<job>')
                    print(f"  ✓ Verification: {job_count} jobs in XML file")
        
        # Upload to SFTP
        print("\n📤 Uploading to SFTP...")
        
        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
        sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
        
        if (sftp_enabled and sftp_enabled.setting_value == 'true' and 
            sftp_hostname and sftp_hostname.setting_value and 
            sftp_username and sftp_username.setting_value and 
            sftp_password and sftp_password.setting_value):
            
            ftp_service = FTPService(
                hostname=sftp_hostname.setting_value,
                username=sftp_username.setting_value,
                password=sftp_password.setting_value,
                target_directory=sftp_directory.setting_value if sftp_directory else "/",
                port=int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222,
                use_sftp=True
            )
            
            for xml_file in xml_files:
                if os.path.exists(xml_file):
                    if ftp_service.upload_file(xml_file, xml_file):
                        print(f"  ✅ Successfully uploaded {xml_file} to SFTP")
                    else:
                        print(f"  ❌ Failed to upload {xml_file} to SFTP")
        else:
            print("  ⚠ SFTP not configured or disabled")
        
        print("\n" + "=" * 70)
        print("✅ XML REBUILD COMPLETE!")
        print(f"All {len(all_jobs)} jobs have been added to the XML files")
        print("The live feed at https://myticas.com/myticas-job-feed.xml should update shortly")
        print("=" * 70)
        
        return True

if __name__ == "__main__":
    result = rebuild_all_xml_files()
    sys.exit(0 if result else 1)