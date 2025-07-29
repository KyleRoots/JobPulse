
import os
from email_service import EmailService

def test_email_delivery():
    try:
        email_service = EmailService()
        
        # Test with a simple notification
        test_result = email_service.send_bullhorn_notification(
            to_email="test@example.com",  # This won't actually send
            monitor_name="Test Monitor",
            added_jobs=[],
            removed_jobs=[],
            modified_jobs=[],
            summary={"total_current": 0, "added_count": 0, "removed_count": 0},
            xml_sync_info={"status": "test", "sftp_upload": False}
        )
        
        return {"success": True, "can_create_email": True}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    result = test_email_delivery()
    print(f"Email test result: {result}")
