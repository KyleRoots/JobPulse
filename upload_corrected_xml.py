#!/usr/bin/env python3
"""
Upload the corrected XML file to the live server
"""

import os
from ftp_service import FTPService

def upload_corrected_xml():
    """Upload the country-corrected XML file to SFTP server"""
    
    # Get SFTP credentials from environment (same as monitoring system uses)
    hostname = os.environ.get('SFTP_HOST')  # Using SFTP_HOST as shown in secrets
    username = os.environ.get('SFTP_USERNAME')
    password = os.environ.get('SFTP_PASSWORD')
    port = 2222  # Use port 2222 as confirmed working
    
    print(f"🚀 Uploading corrected XML to: {hostname}:{port}")
    print(f"📁 Username: {username}")
    
    if not all([hostname, username, password]):
        print("❌ SFTP credentials missing from environment")
        print(f"   hostname: {hostname}")
        print(f"   username: {username}")  
        print(f"   password: {'*' * len(password) if password else 'None'}")
        return False
    
    try:
        # Initialize SFTP service with port 2222 (confirmed working)
        sftp_service = FTPService(
            hostname=str(hostname),
            username=str(username),
            password=str(password),
            target_directory="/",
            port=port,
            use_sftp=True
        )
        
        # Test connection first
        print("🔗 Testing SFTP connection...")
        if not sftp_service.test_connection():
            print("❌ SFTP connection test failed")
            return False
        print("✅ SFTP connection test successful")
        
        # Upload the corrected XML file
        xml_file = 'myticas-job-feed.xml'
        if not os.path.exists(xml_file):
            print(f"❌ File not found: {xml_file}")
            return False
            
        file_size = os.path.getsize(xml_file)
        print(f"📤 Uploading {xml_file} ({file_size:,} bytes)...")
        
        success = sftp_service.upload_file(xml_file)
        if success:
            print("✅ Successfully uploaded corrected XML!")
            print("🌐 Country fields should now show proper names instead of IDs")
            return True
        else:
            print("❌ Upload failed")
            return False
            
    except Exception as e:
        print(f"❌ Upload error: {str(e)}")
        return False

if __name__ == "__main__":
    success = upload_corrected_xml()
    if success:
        print("\n🎉 Country fix successfully deployed to live server!")
    else:
        print("\n❌ Upload failed - country fix not deployed")