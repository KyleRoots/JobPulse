#!/usr/bin/env python3
"""
Emergency XML Rebuild - Creates clean XML from Bullhorn data
Since FTP uploads are timing out, this rebuilds XML from scratch to eliminate orphaned jobs.
"""

import os
import sys
import json
from datetime import datetime
from xml_integration_service import XMLIntegrationService
from bullhorn_service import BullhornService

def emergency_rebuild():
    """Rebuild XML from scratch using current Bullhorn data"""
    
    print("🚨 EMERGENCY XML REBUILD: Creating clean XML from Bullhorn...")
    
    try:
        # Connect to Bullhorn
        bullhorn = BullhornService(
            client_id=os.environ.get('BULLHORN_CLIENT_ID'),
            client_secret=os.environ.get('BULLHORN_CLIENT_SECRET'),
            username=os.environ.get('BULLHORN_USERNAME'),
            password=os.environ.get('BULLHORN_PASSWORD')
        )
        
        print("🔐 Authenticating with Bullhorn...")
        if not bullhorn.authenticate():
            print("❌ Failed to authenticate with Bullhorn")
            return False
        
        # Get monitored tearsheets
        tearsheet_ids = [1256, 1264, 1258, 1499, 1255]  # Your monitored tearsheets
        all_jobs = {}
        
        print("📋 Fetching jobs from all tearsheets...")
        for tearsheet_id in tearsheet_ids:
            try:
                jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
                print(f"   Tearsheet {tearsheet_id}: {len(jobs)} jobs")
                for job in jobs:
                    job_id = str(job.get('id'))
                    all_jobs[job_id] = job
            except Exception as e:
                print(f"   ⚠️ Error fetching tearsheet {tearsheet_id}: {str(e)}")
        
        print(f"📊 Total unique jobs found: {len(all_jobs)}")
        
        # Create clean XML
        xml_service = XMLIntegrationService()
        xml_file = 'myticas-job-feed.xml'
        
        # Backup current file
        backup_file = f"{xml_file}.emergency_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if os.path.exists(xml_file):
            import shutil
            shutil.copy2(xml_file, backup_file)
            print(f"💾 Backed up current XML to: {backup_file}")
        
        print("🔧 Building clean XML from Bullhorn data...")
        
        # Create XML root structure
        xml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<source>
</source>'''
        
        with open(xml_file, 'w', encoding='utf-8') as f:
            f.write(xml_content)
        
        # Add each job to XML
        jobs_added = 0
        for job_id, job_data in all_jobs.items():
            try:
                success = xml_service.add_job_to_xml(xml_file, job_data)
                if success:
                    jobs_added += 1
                    if jobs_added % 10 == 0:
                        print(f"   Added {jobs_added}/{len(all_jobs)} jobs...")
                else:
                    print(f"   ⚠️ Failed to add job {job_id}")
            except Exception as e:
                print(f"   ⚠️ Error adding job {job_id}: {str(e)}")
        
        print(f"✅ REBUILD COMPLETE: {jobs_added} jobs added to clean {xml_file}")
        
        # Verify the result
        with open(xml_file, 'r') as f:
            content = f.read()
        final_count = content.count('<job>')
        print(f"🔍 Verification: {final_count} jobs in rebuilt XML")
        
        if final_count == len(all_jobs):
            print("✅ SUCCESS: Clean XML rebuilt with correct job count")
            print("🎯 Next step: Upload this clean XML to replace the corrupted live version")
            return True
        else:
            print(f"⚠️ WARNING: Expected {len(all_jobs)} jobs but XML has {final_count}")
            return False
            
    except Exception as e:
        print(f"❌ REBUILD ERROR: {str(e)}")
        return False

if __name__ == "__main__":
    success = emergency_rebuild()
    sys.exit(0 if success else 1)