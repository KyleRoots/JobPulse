#!/usr/bin/env python3
"""
Send a test email notification to verify the system is working
"""

from app import app, db, GlobalSettings
from email_service import EmailService
import logging

logging.basicConfig(level=logging.INFO)

def send_test_email():
    """Send a test email notification"""
    with app.app_context():
        try:
            email_service = EmailService()
            
            # Get notification email from global settings
            global_email_setting = GlobalSettings.query.filter_by(setting_key='notification_email').first()
            to_email = global_email_setting.setting_value if global_email_setting else 'kroots@myticas.com'
            
            print(f"📧 Sending test email to: {to_email}")
            
            # Create sample job data to test the email format
            sample_added_jobs = [
                {'id': 'TEST001', 'title': 'Senior Software Engineer - Test Job'},
                {'id': 'TEST002', 'title': 'Project Manager - Email System Test'}
            ]
            
            sample_modified_jobs = [
                {'id': 'TEST003', 'title': 'Data Analyst - Modified Test', 'changes': 'Updated salary range'}
            ]
            
            # Send test notification
            success = email_service.send_bullhorn_notification(
                to_email=to_email,
                monitor_name="Email System Test",
                added_jobs=sample_added_jobs,
                removed_jobs=[],
                modified_jobs=sample_modified_jobs,
                summary={
                    'added': len(sample_added_jobs),
                    'removed': 0,
                    'modified': len(sample_modified_jobs)
                }
            )
            
            if success:
                print("✅ Test email sent successfully!")
                print(f"   Recipient: {to_email}")
                print(f"   Subject: ATS Job Change Alert: Email System Test (3 changes)")
                print("   Check your inbox for the test notification.")
                return True
            else:
                print("❌ Test email failed to send")
                return False
                
        except Exception as e:
            print(f"❌ Error sending test email: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    print("🧪 Testing Email Notification System...")
    success = send_test_email()
    
    if success:
        print("\n🎉 Email system is working correctly!")
        print("The notification system is ready for production use.")
    else:
        print("\n❌ Email system test failed")
        print("Please check the SendGrid configuration and logs.")