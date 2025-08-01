#!/usr/bin/env python3
"""
Upload the corrected XML files to SFTP server
"""

import os
import logging
from ftp_service import FTPService

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def upload_xml_files():
    """Upload XML files to SFTP server"""
    
    # Get SFTP credentials from environment
    hostname = os.environ.get('SFTP_HOSTNAME')
    username = os.environ.get('SFTP_USERNAME') 
    password = os.environ.get('SFTP_PASSWORD')
    port = int(os.environ.get('SFTP_PORT', '22'))
    
    print(f"SFTP Server: {hostname}:{port}")
    print(f"Username: {username}")
    
    if not all([hostname, username, password]):
        print("❌ SFTP credentials missing from environment")
        return False
    
    try:
        # Initialize SFTP service - ensure all credentials are strings
        sftp_service = FTPService(
            hostname=str(hostname),
            username=str(username),
            password=str(password),
            target_directory="/",
            port=port,
            use_sftp=True
        )
        
        # Test connection first
        print("Testing SFTP connection...")
        if not sftp_service.test_connection():
            print("❌ SFTP connection test failed")
            return False
        print("✅ SFTP connection test successful")
        
        # Upload both XML files with HTML fixes
        files_to_upload = [
            'myticas-job-feed.xml',
            'myticas-job-feed-scheduled.xml'
        ]
        
        upload_success = True
        
        for file_path in files_to_upload:
            if os.path.exists(file_path):
                print(f"📤 Uploading {file_path}...")
                
                # Get file size for progress info
                file_size = os.path.getsize(file_path)
                print(f"   File size: {file_size:,} bytes")
                
                success = sftp_service.upload_file(file_path)
                if success:
                    print(f"✅ Successfully uploaded {file_path}")
                else:
                    print(f"❌ Failed to upload {file_path}")
                    upload_success = False
            else:
                print(f"❌ File not found: {file_path}")
                upload_success = False
        
        return upload_success
        
    except Exception as e:
        print(f"❌ Upload error: {str(e)}")
        return False

if __name__ == "__main__":
    print("🚀 Starting XML file upload with HTML consistency fixes...")
    success = upload_xml_files()
    
    if success:
        print("\n🎉 All XML files uploaded successfully!")
        print("Your job feed files now have consistent HTML formatting.")
    else:
        print("\n❌ Upload process encountered errors.")