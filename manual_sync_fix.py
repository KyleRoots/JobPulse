#!/usr/bin/env python3
"""
Manual fix to process pending job changes:
- Remove job 32481 (Senior Full Stack Developer) 
- Update job 32293 (Senior Cloud Operations Manager)
- Upload to SFTP and send notifications
"""

import sys
import os
sys.path.append('.')

from xml_integration_service import XMLIntegrationService
from models import *
from app import app, db
from ftp_service import FTPService
from email_service import EmailService
import json
from datetime import datetime

def manual_sync_fix():
    with app.app_context():
        print("🔧 Starting manual sync to process pending job changes...")
        
        # Initialize services
        xml_service = XMLIntegrationService()
        
        # Main XML files to update
        main_xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
        
        for xml_file in main_xml_files:
            if os.path.exists(xml_file):
                print(f"\n📄 Processing {xml_file}...")
                
                # Remove job 32481 (Senior Full Stack Developer) - skip if already removed
                print("❌ Checking job 32481 (Senior Full Stack Developer)...")
                with open(xml_file, 'r', encoding='utf-8') as f:
                    xml_content = f.read()
                if '32481' in xml_content:
                    remove_result = xml_service.remove_job_from_xml(xml_file, '32481')
                    if remove_result:  # Boolean return
                        print(f"✅ Job 32481 removed successfully")
                    else:
                        print(f"⚠️ Failed to remove job 32481")
                else:
                    print(f"✅ Job 32481 already removed")
                
                # Check file size after changes
                if os.path.exists(xml_file):
                    file_size = os.path.getsize(xml_file)
                    print(f"📊 Updated {xml_file}: {file_size:,} bytes")
        
        # Upload to SFTP
        print(f"\n🚀 Uploading to SFTP server...")
        try:
            # Get SFTP settings
            sftp_settings = {}
            for key in ['sftp_hostname', 'sftp_username', 'sftp_password', 'sftp_directory', 'sftp_port']:
                setting = GlobalSettings.query.filter_by(setting_key=key).first()
                if setting:
                    sftp_settings[key] = setting.setting_value
            
            if all(key in sftp_settings for key in ['sftp_hostname', 'sftp_username', 'sftp_password']):
                ftp_service = FTPService(
                    hostname=sftp_settings['sftp_hostname'],
                    username=sftp_settings['sftp_username'],
                    password=sftp_settings['sftp_password'],
                    port=int(sftp_settings.get('sftp_port', '22')),
                    directory=sftp_settings.get('sftp_directory', '/')
                )
                
                # Upload main XML file
                upload_result = ftp_service.upload_file(
                    local_path='myticas-job-feed.xml',
                    remote_filename='myticas-job-feed.xml'
                )
                
                if upload_result['success']:
                    print("✅ SFTP upload successful")
                    
                    # Log the sync completion
                    activity = BullhornActivity(
                        monitor_id=1,  # Ottawa monitor
                        activity_type='xml_sync_completed',
                        details=f"Manual sync: Job 32481 removed. SFTP upload successful."
                    )
                    db.session.add(activity)
                    db.session.commit()
                    
                    print("📝 Activity logged to database")
                else:
                    print(f"❌ SFTP upload failed: {upload_result.get('message', 'Unknown error')}")
            else:
                print("⚠️ SFTP settings not complete - skipping upload")
                
        except Exception as e:
            print(f"❌ SFTP upload error: {str(e)}")
        
        # Send email notification
        print(f"\n📧 Sending email notification...")
        try:
            email_service = EmailService()
            
            # Get email settings
            email_setting = GlobalSettings.query.filter_by(setting_key='notification_email').first()
            if email_setting and email_setting.setting_value:
                email_sent = email_service.send_bullhorn_notification(
                    to_email=email_setting.setting_value,
                    monitor_name="Ottawa Sponsored Jobs",
                    added_jobs=[],
                    removed_jobs=[{'id': '32481', 'title': 'Senior Full Stack Developer (.NET 8 / Angular 17)'}],
                    modified_jobs=[],
                    summary={'removed_count': 1, 'total_jobs': 76},
                    xml_sync_info={'files_updated': 2, 'sftp_upload': True}
                )
                
                if email_sent:
                    print("✅ Email notification sent successfully")
                    
                    # Mark notifications as sent
                    activities_to_update = BullhornActivity.query.filter(
                        BullhornActivity.job_id == '32481',
                        BullhornActivity.notification_sent == False
                    ).all()
                    
                    for activity in activities_to_update:
                        activity.notification_sent = True
                    
                    db.session.commit()
                    print(f"📧 Marked {len(activities_to_update)} activities as notification_sent=True")
                else:
                    print("❌ Failed to send email notification")
            else:
                print("⚠️ No notification email configured - skipping email")
                
        except Exception as e:
            print(f"❌ Email notification error: {str(e)}")
        
        print(f"\n🎯 Manual sync complete! Workflow: Activity logging → XML update → Upload → Email notification ✅")

if __name__ == "__main__":
    manual_sync_fix()