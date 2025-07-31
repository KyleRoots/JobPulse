#!/usr/bin/env python3
"""
Send pending email notifications that are stuck
"""

from app import app, db, BullhornActivity, BullhornMonitor, GlobalSettings
from email_service import EmailService
import logging

logging.basicConfig(level=logging.INFO)

def send_pending_notifications():
    """Send pending notifications using correct email logic"""
    with app.app_context():
        try:
            # Get all pending notifications (excluding check_completed)
            pending = BullhornActivity.query.filter(
                BullhornActivity.notification_sent == False,
                BullhornActivity.activity_type.in_(['job_added', 'job_modified', 'job_removed'])
            ).all()
            
            print(f"📋 Found {len(pending)} pending job change notifications")
            
            if not pending:
                print("✅ No pending job change notifications to send")
                return True
            
            # Group by monitor
            monitor_activities = {}
            for activity in pending:
                if activity.monitor_id not in monitor_activities:
                    monitor_activities[activity.monitor_id] = []
                monitor_activities[activity.monitor_id].append(activity)
            
            email_service = EmailService()
            success_count = 0
            
            # Send notifications for each monitor
            for monitor_id, activities in monitor_activities.items():
                monitor = BullhornMonitor.query.get(monitor_id)
                if not monitor or not monitor.send_notifications:
                    # Mark as sent without emailing if notifications disabled
                    for activity in activities:
                        activity.notification_sent = True
                    continue
                
                # Get email address - use monitor's email or fallback
                to_email = monitor.notification_email
                if not to_email:
                    global_email_setting = GlobalSettings.query.filter_by(setting_key='notification_email').first()
                    to_email = global_email_setting.setting_value if global_email_setting else 'kroots@myticas.com'
                
                # Categorize job changes
                added_jobs = []
                removed_jobs = []
                modified_jobs = []
                
                for activity in activities:
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
                
                # Send email if there are job changes
                if added_jobs or removed_jobs or modified_jobs:
                    print(f"📧 Sending notification to {to_email} for {monitor.name}")
                    print(f"   Added: {len(added_jobs)}, Removed: {len(removed_jobs)}, Modified: {len(modified_jobs)}")
                    
                    success = email_service.send_bullhorn_notification(
                        to_email=to_email,
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
                        print(f"✅ Email sent successfully for {monitor.name}")
                    else:
                        print(f"❌ Email failed for {monitor.name}")
                
                # Mark all activities as sent 
                for activity in activities:
                    activity.notification_sent = True
            
            # Also mark check_completed activities as sent (no emails needed)
            check_completed = BullhornActivity.query.filter(
                BullhornActivity.notification_sent == False,
                BullhornActivity.activity_type == 'check_completed'
            ).all()
            
            for activity in check_completed:
                activity.notification_sent = True
            
            # Commit all changes
            db.session.commit()
            print(f"✅ Sent emails for {success_count} monitors, marked {len(check_completed)} check activities as processed")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error sending notifications: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    print("📤 Sending Pending Email Notifications...")
    success = send_pending_notifications()
    
    if success:
        print("\n🎉 Email notification system fixed and running!")
    else:
        print("\n❌ Email notification issues remain")