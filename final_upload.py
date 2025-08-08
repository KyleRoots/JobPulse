#!/usr/bin/env python3
"""
Upload corrected XML files to SFTP
"""

import os
import paramiko
from datetime import datetime

def upload_to_sftp():
    """Upload XML files to production SFTP"""
    
    # SFTP credentials from environment
    hostname = os.environ.get('SFTP_HOST')
    username = os.environ.get('SFTP_USERNAME')
    password = os.environ.get('SFTP_PASSWORD')
    
    if not all([hostname, username, password]):
        print("Error: SFTP credentials not found in environment")
        return False
    
    print(f"Connecting to SFTP: {hostname}")
    
    try:
        # Create SSH client
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect
        transport = paramiko.Transport((hostname, 2222))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        print("✅ Connected to SFTP server")
        
        # Upload both XML files
        xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
        
        for xml_file in xml_files:
            if os.path.exists(xml_file):
                print(f"\nUploading {xml_file}...")
                
                # Get file size
                file_size = os.path.getsize(xml_file)
                
                # Upload file
                sftp.put(xml_file, xml_file)
                
                print(f"  ✅ Uploaded {xml_file} ({file_size:,} bytes)")
            else:
                print(f"  ❌ File not found: {xml_file}")
        
        # Close connection
        sftp.close()
        transport.close()
        
        print("\n✅ SFTP upload complete!")
        return True
        
    except Exception as e:
        print(f"❌ SFTP error: {str(e)}")
        return False

def main():
    print("=" * 60)
    print("UPLOADING CORRECTED XML FILES TO PRODUCTION")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    success = upload_to_sftp()
    
    if success:
        print("\n" + "=" * 60)
        print("SUCCESS: XML files uploaded to production")
        print("The live feed at https://myticas.com/myticas-job-feed.xml")
        print("should now show:")
        print("  ✓ Job titles without IDs")
        print("  ✓ Proper CDATA formatting")
        print("  ✓ Country names instead of numeric IDs")
        print("=" * 60)
    else:
        print("\nUpload failed - please check credentials and retry")

if __name__ == "__main__":
    main()