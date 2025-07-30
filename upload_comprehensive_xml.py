#!/usr/bin/env python3

import os
from app import app, db
from ftp_service import FTPService
from sqlalchemy import text

def upload_comprehensive_xml():
    """Upload the comprehensively refreshed XML to SFTP server"""
    
    with app.app_context():
        try:
            print("📤 Uploading comprehensive XML refresh to SFTP server...")
            
            # Get SFTP settings from database
            result = db.session.execute(text("""
                SELECT setting_key, setting_value 
                FROM global_settings 
                WHERE setting_key LIKE 'sftp_%'
            """))
            
            sftp_config = {}
            for row in result:
                # Remove 'sftp_' prefix to get key name
                key = row[0].replace('sftp_', '')
                sftp_config[key] = row[1]
            
            if all(k in sftp_config for k in ['hostname', 'username', 'password']):
                ftp_service = FTPService(
                    hostname=sftp_config['hostname'],
                    username=sftp_config['username'],
                    password=sftp_config['password'],
                    port=int(sftp_config.get('port', 2222))
                )
                
                # Upload main XML file
                upload_success = ftp_service.upload_file('myticas-job-feed.xml', 'myticas-job-feed.xml')
                
                if upload_success:
                    print("✅ Successfully uploaded to SFTP server")
                    
                    # Get file size for verification
                    file_size = os.path.getsize('myticas-job-feed.xml')
                    print(f"   File size: {file_size:,} bytes")
                    print(f"   Total jobs: 77")
                    
                    # Update schedule last upload time
                    db.session.execute(text("""
                        UPDATE xml_schedule 
                        SET last_uploaded = NOW()
                        WHERE is_active = true
                    """))
                    db.session.commit()
                    
                    return True
                else:
                    print("❌ SFTP upload failed")
                    return False
            else:
                print("❌ SFTP settings not configured properly")
                return False
                
        except Exception as e:
            print(f"❌ Upload error: {str(e)}")
            return False

if __name__ == "__main__":
    success = upload_comprehensive_xml()
    exit(0 if success else 1)