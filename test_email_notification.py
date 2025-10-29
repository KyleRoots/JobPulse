"""
Quick test script to send a sample new job notification email
"""
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import EmailDeliveryLog
from email_service import EmailService

def send_test_email():
    """Send a test new job notification"""
    with app.app_context():
        # Initialize email service
        email_service = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
        
        # Test job data
        test_job_id = "12345678"
        test_job_title = "Senior Software Engineer - Python/Flask (Remote)"
        test_monitor_name = "Sponsored - OTT"
        recipient_email = "kroots@myticas.com"
        
        print(f"📧 Sending test email notification...")
        print(f"   To: {recipient_email}")
        print(f"   Job ID: {test_job_id}")
        print(f"   Title: {test_job_title}")
        print(f"   Monitor: {test_monitor_name}")
        print()
        
        # Send the email
        success = email_service.send_new_job_notification(
            to_email=recipient_email,
            job_id=test_job_id,
            job_title=test_job_title,
            monitor_name=test_monitor_name
        )
        
        if success:
            print("✅ Test email sent successfully!")
            print(f"   Check {recipient_email} for the notification")
        else:
            print("❌ Failed to send test email")
            print("   Check logs for details")

if __name__ == "__main__":
    send_test_email()
