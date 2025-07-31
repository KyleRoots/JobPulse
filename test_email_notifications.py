#!/usr/bin/env python3
"""
Test email notifications and send pending notifications
"""

from app import app, db, BullhornActivity, BullhornMonitor, GlobalSettings
from email_service import EmailService
import logging

logging.basicConfig(level=logging.INFO)

def test_email_service():
    """Test if email service is working"""
    with app.app_context():
        try:
            email_service = EmailService()
            
            # Get a test email from global settings
            settings = GlobalSettings.query.first()
            if not settings or not settings.email_address:
                print("❌ No email address configured in Global Settings")
                return False
            
            test_email = settings.email_address
            print(f"📧 Testing email service to: {test_email}")
            
            # Send a simple test
            success = email_service.send_bullhorn_notification(
                to_email=test_email,
                monitor_name="Email Test",
                added_jobs=[{"id": "TEST", "title": "Email Service Test Job"}],
                removed_jobs=[],
                modified_jobs=[],
                summary={"added": 1, "removed": 0, "modified": 0}
            )
            
            if success:
                print("✅ Email service test successful")
                return True
            else:
                print("❌ Email service test failed")
                return False
                
        except Exception as e:
            print(f"❌ Email service error: {str(e)}")
            return False

def send_pending_notifications():
    """Manually send pending notifications"""
    with app.app_context():
        try:
            # Get all pending notifications
            pending = BullhornActivity.query.filter_by(notification_sent=False).all()
            
            print(f"📋 Found {len(pending)} pending notifications")
            
            if not pending:
                print("✅ No pending notifications to send")
                return True
            
            # Group by monitor
            monitor_activities = {}
            for activity in pending:
                if activity.monitor_id not in monitor_activities:
                    monitor_activities[activity.monitor_id] = []
                monitor_activities[activity.monitor_id].append(activity)
            
            email_service = EmailService()
            settings = GlobalSettings.query.first()
            
            if not settings or not settings.email_address:
                print("❌ No email address configured")
                return False
            
            success_count = 0
            
            # Send notifications for each monitor
            for monitor_id, activities in monitor_activities.items():
                monitor = BullhornMonitor.query.get(monitor_id)
                if not monitor:
                    continue
                
                # Skip check_completed activities for email notifications
                relevant_activities = [a for a in activities if a.activity_type != 'check_completed']
                
                if not relevant_activities:
                    # Mark check_completed as sent without emailing
                    for activity in activities:
                        activity.notification_sent = True
                    continue
                
                # Categorize job changes
                added_jobs = []
                removed_jobs = []
                modified_jobs = []
                
                for activity in relevant_activities:
                    if activity.activity_type == 'job_added' and activity.job_id:
                        added_jobs.append({
                            'id': activity.job_id,
                            'title': activity.job_title or 'Unknown'
                        })
                    elif activity.activity_type == 'job_removed' and activity.job_id:
                        removed_jobs.append({
                            'id': activity.job_id,
                            'title': activity.job_title or 'Unknown'
                        })
                    elif activity.activity_type == 'job_modified' and activity.job_id:
                        modified_jobs.append({
                            'id': activity.job_id,
                            'title': activity.job_title or 'Unknown',
                            'changes': activity.details or 'Updated'
                        })
                
                # Only send if there are actual job changes
                if added_jobs or removed_jobs or modified_jobs:
                    print(f"📧 Sending notification for {monitor.name}")
                    print(f"   Added: {len(added_jobs)}, Removed: {len(removed_jobs)}, Modified: {len(modified_jobs)}")
                    
                    success = email_service.send_bullhorn_notification(
                        to_email=settings.email_address,
                        monitor_name=monitor.name,
                        added_jobs=added_jobs,
                        removed_jobs=removed_jobs,
                        modified_jobs=modified_jobs,
                        summary={
                            'added': len(added_jobs),
                            'removed': len(removed_jobs),
                            'modified': len(modified_jobs)
                        }
                    )
                    
                    if success:
                        success_count += 1
                        print(f"✅ Email sent for {monitor.name}")
                    else:
                        print(f"❌ Email failed for {monitor.name}")
                
                # Mark all activities as sent (even if email failed, to prevent spam)
                for activity in activities:
                    activity.notification_sent = True
            
            # Commit changes
            db.session.commit()
            print(f"✅ Processed notifications for {success_count} monitors")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error sending notifications: {str(e)}")
            return False

if __name__ == "__main__":
    print("🧪 Testing Email Notification System...")
    
    # Test email service first
    if test_email_service():
        print("\n📤 Sending pending notifications...")
        send_pending_notifications()
    else:
        print("\n❌ Email service test failed - check SendGrid configuration")