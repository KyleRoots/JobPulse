#!/usr/bin/env python3
"""
Script to add <bhatsid> nodes to existing XML file
"""

import logging
import os
import sys
from xml_processor import XMLProcessor
from ftp_service import FTPService
from email_service import EmailService
from app import app, db
from models import create_models

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    """Add bhatsid nodes to XML file and upload to SFTP"""
    
    # Initialize processor
    processor = XMLProcessor()
    
    # File paths
    input_file = 'myticas-job-feed-dice.xml'
    output_file = 'myticas-job-feed-dice.xml'  # Same file, will be overwritten
    
    # Check if input file exists
    if not os.path.exists(input_file):
        logger.error(f"Input file {input_file} does not exist")
        return False
    
    # Create backup before modification
    backup_file = f"{input_file}.backup.before_bhatsid"
    import shutil
    shutil.copy2(input_file, backup_file)
    logger.info(f"Created backup: {backup_file}")
    
    # Add bhatsid nodes
    logger.info("Adding <bhatsid> nodes to all jobs...")
    result = processor.add_bhatsid_nodes(input_file, output_file)
    
    if not result['success']:
        logger.error(f"Failed to add bhatsid nodes: {result['error']}")
        return False
    
    logger.info(f"Successfully added bhatsid nodes to {result['nodes_added']} jobs")
    
    # Now upload to SFTP
    logger.info("Uploading updated XML file to SFTP server...")
    
    # Get SFTP credentials from database
    with app.app_context():
        try:
            # Get models
            User, ScheduleConfig, ProcessingLog, GlobalSettings, BullhornMonitor, BullhornActivity = create_models(db)
            
            # Try to find the DICE Job Feed Update schedule
            schedule = ScheduleConfig.query.filter_by(name='DICE Job Feed Update').first()
            
            if not schedule:
                logger.error("Could not find DICE Job Feed Update schedule in database")
                return False
            
            # Get SFTP settings from GlobalSettings
            sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
            sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
            sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
            sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
            sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
            sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
            
            if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
                logger.error("SFTP is not enabled in GlobalSettings")
                return False
            
            # Initialize FTP service
            ftp_service = FTPService()
            
            # Upload file
            upload_success = ftp_service.upload_file(
                local_file_path=output_file,
                remote_filename='myticas-job-feed-dice.xml',
                sftp_host=sftp_hostname.setting_value if sftp_hostname else None,
                sftp_username=sftp_username.setting_value if sftp_username else None,
                sftp_password=sftp_password.setting_value if sftp_password else None,
                sftp_port=int(sftp_port.setting_value) if sftp_port else 22,
                sftp_directory=sftp_directory.setting_value if sftp_directory else "/"
            )
            
            if upload_success:
                logger.info("Successfully uploaded updated XML file to SFTP server")
                
                # Update the schedule's last_file_upload timestamp
                from datetime import datetime
                schedule.last_file_upload = datetime.utcnow()
                db.session.commit()
                
                logger.info("Updated schedule last_file_upload timestamp")
                
                # Get email settings
                email_enabled = GlobalSettings.query.filter_by(setting_key='email_notifications_enabled').first()
                email_address = GlobalSettings.query.filter_by(setting_key='default_notification_email').first()
                
                if email_enabled and email_enabled.setting_value == 'true' and email_address:
                    # Send email notification
                    email_service = EmailService()
                    email_result = email_service.send_email(
                        to_email=email_address.setting_value,
                        subject='XML File Updated with <bhatsid> Nodes',
                        body=f"""
XML file has been successfully updated with <bhatsid> nodes!

Summary:
- Total jobs processed: {result['jobs_processed']}
- bhatsid nodes added: {result['nodes_added']}
- File uploaded to SFTP: Success
- Upload timestamp: {schedule.last_file_upload}

All jobs now have <bhatsid> nodes containing their job IDs extracted from title brackets.
This template will be used for all future job additions from Bullhorn.
                        """.strip()
                    )
                    
                    if email_result:
                        logger.info("Email notification sent successfully")
                    else:
                        logger.warning("Failed to send email notification")
                else:
                    logger.info("Email notifications disabled or no email address configured")
                
                return True
            else:
                logger.error("Failed to upload updated XML file to SFTP server")
                return False
                
        except Exception as e:
            logger.error(f"Error uploading to SFTP: {str(e)}")
            return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)