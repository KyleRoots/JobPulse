#!/usr/bin/env python3

import os
import sys
from app import app, db
import paramiko

def get_setting(key):
    """Get setting value from key-value store"""
    from app import db
    result = db.session.execute(db.text("SELECT setting_value FROM global_settings WHERE setting_key = :key"), {"key": key}).fetchone()
    return result[0] if result else None

def upload_restored_xml():
    """Upload the restored XML file to SFTP server"""
    
    with app.app_context():
        try:
            # Get SFTP settings from database
            sftp_hostname = get_setting('sftp_hostname')
            sftp_username = get_setting('sftp_username')
            sftp_password = get_setting('sftp_password')
            sftp_port = get_setting('sftp_port')
            
            print(f"📤 Uploading restored XML to: {sftp_hostname}")
            
            # Create SFTP connection
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            ssh.connect(
                hostname=sftp_hostname,
                port=int(sftp_port),
                username=sftp_username,
                password=sftp_password
            )
            
            sftp = ssh.open_sftp()
            
            # Upload the restored XML file
            xml_file = 'myticas-job-feed.xml'
            local_size = os.path.getsize(xml_file)
            print(f"   Local file size: {local_size:,} bytes")
            
            sftp.put(xml_file, 'myticas-job-feed.xml')
            
            # Verify upload
            remote_stat = sftp.stat('myticas-job-feed.xml')
            remote_size = remote_stat.st_size
            print(f"   Remote file size: {remote_size:,} bytes")
            
            sftp.close()
            ssh.close()
            
            if local_size == remote_size:
                print("✅ Restored XML uploaded successfully")
                return True
            else:
                print("⚠️  Size mismatch after upload")
                return False
            
        except Exception as e:
            print(f"❌ Upload error: {str(e)}")
            return False

if __name__ == "__main__":
    success = upload_restored_xml()
    sys.exit(0 if success else 1)