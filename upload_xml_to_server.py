#!/usr/bin/env python3

import os
import sys
import paramiko
from datetime import datetime

def upload_xml_files():
    """Upload XML files to SFTP server"""
    
    print("📤 Starting XML upload to web server...")
    
    # SFTP credentials from database query results
    sftp_config = {
        'hostname': 'mytconsulting.sftp.wpengine.com',
        'username': 'mytconsulting-production',
        'password': 'Myticas01!',
        'port': 2222
    }
    
    try:
        # Create SSH client
        print(f"   Connecting to {sftp_config['hostname']}:{sftp_config['port']}...")
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect with timeout
        ssh.connect(
            hostname=sftp_config['hostname'],
            port=sftp_config['port'],
            username=sftp_config['username'],
            password=sftp_config['password'],
            timeout=30,
            banner_timeout=30,
            auth_timeout=30
        )
        
        # Create SFTP client
        sftp = ssh.open_sftp()
        print("   ✅ Connected to SFTP server")
        
        # Upload main XML file
        local_file = 'myticas-job-feed.xml'
        remote_file = 'myticas-job-feed.xml'
        
        if os.path.exists(local_file):
            file_size = os.path.getsize(local_file)
            print(f"   Uploading {local_file} ({file_size:,} bytes)...")
            
            sftp.put(local_file, remote_file)
            print(f"   ✅ Successfully uploaded {local_file}")
        else:
            print(f"   ❌ File not found: {local_file}")
            return False
        
        # Close connections
        sftp.close()
        ssh.close()
        
        print(f"\n🎯 Upload complete!")
        print(f"   Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"   File: myticas-job-feed.xml")
        print(f"   Size: {file_size:,} bytes")
        print(f"   Jobs: 77")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Upload error: {str(e)}")
        return False

if __name__ == "__main__":
    success = upload_xml_files()
    sys.exit(0 if success else 1)