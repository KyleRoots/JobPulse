#!/usr/bin/env python3
"""
Fix XML sync to ensure all jobs from all monitors are in the XML file
This script will check all active monitors and sync any missing jobs
"""

import os
import sys
from datetime import datetime
from app import app, db, BullhornMonitor, ScheduleConfig
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
import json
import xml.etree.ElementTree as ET

def count_jobs_in_xml(xml_path):
    """Count the number of jobs in the XML file"""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        jobs = root.findall('.//job')
        return len(jobs)
    except Exception as e:
        print(f"Error counting jobs in XML: {e}")
        return -1

def get_all_job_ids_from_xml(xml_path):
    """Extract all job IDs from the XML file"""
    job_ids = set()
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        jobs = root.findall('.//job')
        
        for job in jobs:
            title_elem = job.find('title')
            if title_elem is not None and title_elem.text:
                # Extract job ID from title (format: "Title (job_id)")
                import re
                match = re.search(r'\((\d+)\)', title_elem.text)
                if match:
                    job_ids.add(match.group(1))
    except Exception as e:
        print(f"Error extracting job IDs from XML: {e}")
    
    return job_ids

def sync_all_monitors_to_xml():
    """Sync all jobs from all active monitors to the XML file"""
    with app.app_context():
        print("🔄 Starting comprehensive XML sync...")
        print("=" * 60)
        
        # Get all active monitors
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        print(f"Found {len(monitors)} active monitors")
        
        # Initialize Bullhorn service
        bs = BullhornService()
        if not bs.authenticate():
            print("❌ Failed to authenticate with Bullhorn")
            return False
        
        # Collect all current jobs from all monitors
        all_current_jobs = []
        job_ids_by_monitor = {}
        
        for monitor in monitors:
            print(f"\n📊 Checking {monitor.name}...")
            
            if monitor.tearsheet_id == 0:
                jobs = bs.get_jobs_by_query(monitor.tearsheet_name)
            else:
                jobs = bs.get_tearsheet_jobs(monitor.tearsheet_id)
            
            print(f"   Found {len(jobs)} jobs")
            all_current_jobs.extend(jobs)
            job_ids_by_monitor[monitor.name] = [str(job['id']) for job in jobs]
        
        print(f"\n📋 Total jobs across all monitors: {len(all_current_jobs)}")
        
        # Get the main XML file
        schedules = ScheduleConfig.query.filter_by(is_active=True).all()
        if not schedules:
            print("❌ No active schedules found")
            return False
        
        schedule = schedules[0]  # Use the first active schedule
        xml_path = schedule.file_path
        
        print(f"\n📄 XML file: {xml_path}")
        jobs_before = count_jobs_in_xml(xml_path)
        print(f"   Current job count: {jobs_before}")
        
        # Get job IDs currently in XML
        xml_job_ids = get_all_job_ids_from_xml(xml_path)
        print(f"   Job IDs in XML: {len(xml_job_ids)}")
        
        # Find missing jobs
        all_job_ids = {str(job['id']) for job in all_current_jobs}
        missing_job_ids = all_job_ids - xml_job_ids
        extra_job_ids = xml_job_ids - all_job_ids
        
        print(f"\n🔍 Analysis:")
        print(f"   Jobs that should be in XML: {len(all_job_ids)}")
        print(f"   Jobs currently in XML: {len(xml_job_ids)}")
        print(f"   Missing jobs to add: {len(missing_job_ids)}")
        print(f"   Extra jobs to remove: {len(extra_job_ids)}")
        
        if missing_job_ids:
            print(f"\n🆕 Missing job IDs: {sorted(missing_job_ids)}")
        
        if extra_job_ids:
            print(f"\n🗑️  Extra job IDs to remove: {sorted(extra_job_ids)}")
        
        # Initialize XML service
        xml_service = XMLIntegrationService()
        
        # Add missing jobs
        added_count = 0
        if missing_job_ids:
            print(f"\n➕ Adding {len(missing_job_ids)} missing jobs...")
            for job in all_current_jobs:
                if str(job['id']) in missing_job_ids:
                    print(f"   Adding job {job['id']}: {job.get('title', 'Unknown')}")
                    if xml_service.add_job_to_xml(xml_path, job):
                        added_count += 1
                    else:
                        print(f"   ❌ Failed to add job {job['id']}")
        
        # Remove extra jobs
        removed_count = 0
        if extra_job_ids:
            print(f"\n➖ Removing {len(extra_job_ids)} extra jobs...")
            for job_id in extra_job_ids:
                print(f"   Removing job {job_id}")
                if xml_service.remove_job_from_xml(xml_path, job_id):
                    removed_count += 1
                else:
                    print(f"   ❌ Failed to remove job {job_id}")
        
        # Process reference numbers
        if added_count > 0 or removed_count > 0:
            print(f"\n🔢 Processing reference numbers...")
            from xml_processor import XMLProcessor
            processor = XMLProcessor()
            
            try:
                processor.process_xml_file(xml_path, xml_path)
                print("   ✅ Reference numbers regenerated")
            except Exception as e:
                print(f"   ❌ Error processing reference numbers: {e}")
        
        # Final count
        jobs_after = count_jobs_in_xml(xml_path)
        
        print(f"\n📊 Final Results:")
        print(f"   Jobs before: {jobs_before}")
        print(f"   Jobs after: {jobs_after}")
        print(f"   Added: {added_count}")
        print(f"   Removed: {removed_count}")
        print(f"   Net change: {jobs_after - jobs_before}")
        
        # Upload to SFTP if changes were made
        if added_count > 0 or removed_count > 0:
            print(f"\n📤 Uploading to SFTP...")
            from ftp_service import FTPService
            from models import GlobalSettings
            
            # Get SFTP settings
            sftp_settings = {}
            settings_keys = ['sftp_hostname', 'sftp_username', 'sftp_password', 'sftp_directory', 'sftp_port']
            for key in settings_keys:
                setting = GlobalSettings.query.filter_by(setting_key=key).first()
                if setting:
                    sftp_settings[key] = setting.setting_value
            
            if all(k in sftp_settings for k in ['sftp_hostname', 'sftp_username', 'sftp_password']):
                ftp = FTPService(
                    hostname=sftp_settings['sftp_hostname'],
                    username=sftp_settings['sftp_username'],
                    password=sftp_settings['sftp_password'],
                    target_directory=sftp_settings.get('sftp_directory', '/'),
                    port=int(sftp_settings.get('sftp_port', 2222)),
                    use_sftp=True
                )
                
                if ftp.upload_file(xml_path, os.path.basename(xml_path)):
                    print("   ✅ Upload successful")
                else:
                    print("   ❌ Upload failed")
            else:
                print("   ⚠️  SFTP settings not configured")
        
        print("\n✅ Sync complete!")
        return True

if __name__ == '__main__':
    sync_all_monitors_to_xml()