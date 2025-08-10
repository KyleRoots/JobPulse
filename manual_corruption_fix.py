#!/usr/bin/env python3
"""
Manual Corruption Fix - Emergency upload clean XML to fix 61 vs 52 job mismatch
This script manually uploads the clean local XML file to fix orphaned jobs on live server.
"""

import os
import sys
from ftp_service import FTPService

def manual_corruption_fix():
    """Manually upload clean XML to fix corruption on live server"""
    
    print("🚨 MANUAL CORRUPTION FIX: Starting emergency upload...")
    
    # Verify local file has correct job count
    xml_file = 'myticas-job-feed.xml'
    if not os.path.exists(xml_file):
        print(f"❌ ERROR: {xml_file} not found!")
        return False
    
    with open(xml_file, 'r') as f:
        content = f.read()
    
    local_job_count = content.count('<job>')
    print(f"📊 Local {xml_file} has {local_job_count} jobs")
    
    if local_job_count != 52:
        print(f"⚠️ WARNING: Expected 52 jobs but found {local_job_count}")
        response = input("Continue anyway? (y/N): ")
        if response.lower() != 'y':
            return False
    
    # Attempt FTP upload with multiple retries
    try:
        ftp_service = FTPService(
            hostname=os.environ.get('SFTP_HOST'),
            username=os.environ.get('SFTP_USERNAME'), 
            password=os.environ.get('SFTP_PASSWORD')
        )
        
        max_retries = 5
        for attempt in range(max_retries):
            print(f"🔄 Upload attempt {attempt + 1}/{max_retries}...")
            
            try:
                result = ftp_service.upload_file(xml_file, xml_file)
                if result:
                    print(f"✅ SUCCESS: {xml_file} uploaded successfully!")
                    print("🎯 Corruption fix complete - live server should now show 52 jobs")
                    return True
                else:
                    print(f"⚠️ Attempt {attempt + 1} failed")
            except Exception as e:
                print(f"⚠️ Attempt {attempt + 1} error: {str(e)}")
            
            if attempt < max_retries - 1:
                print("⏱️ Waiting 10 seconds before retry...")
                import time
                time.sleep(10)
        
        print("❌ All upload attempts failed!")
        return False
        
    except Exception as e:
        print(f"❌ FTP connection error: {str(e)}")
        return False

if __name__ == "__main__":
    success = manual_corruption_fix()
    sys.exit(0 if success else 1)