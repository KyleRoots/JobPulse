"""
Freeze Manager for XML Feed System
Manages the freeze state and controls when XML feed operations are allowed
"""

import os
import logging
from datetime import datetime

class FreezeManager:
    """Manages the freeze state of the XML feed system"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
    def is_frozen(self) -> bool:
        """
        Check if XML feed system is frozen
        
        Returns:
            bool: True if system is frozen, False otherwise
        """
        freeze_flag = os.environ.get('XML_FEED_FRZ', '').lower()
        is_frozen = freeze_flag in ['true', '1', 'yes', 'on']
        
        if is_frozen:
            self.logger.info("🔒 XML FEED FROZEN: All XML generation and upload operations are disabled")
        
        return is_frozen
    
    def check_operation(self, operation: str) -> bool:
        """
        Check if a specific operation is allowed
        
        Args:
            operation: Name of the operation (e.g., 'rebuild', 'upload', 'monitor')
            
        Returns:
            bool: True if operation is allowed, False if frozen
        """
        if self.is_frozen():
            self.logger.warning(f"⛔ Operation '{operation}' blocked - XML feed is frozen")
            return False
        return True
    
    def get_status(self) -> dict:
        """
        Get current freeze status information
        
        Returns:
            dict: Status information including freeze state and timestamp
        """
        is_frozen = self.is_frozen()
        return {
            'frozen': is_frozen,
            'freeze_flag': os.environ.get('XML_FEED_FRZ', 'not set'),
            'timestamp': datetime.now().isoformat(),
            'message': 'XML feed system is frozen' if is_frozen else 'XML feed system is active'
        }
    
    def send_alert(self, message: str, alert_type: str = 'info'):
        """
        Send alert to configured email if XML_ALERTS_EMAIL is set
        
        Args:
            message: Alert message to send
            alert_type: Type of alert (info, warning, error)
        """
        alerts_email = os.environ.get('XML_ALERTS_EMAIL')
        if not alerts_email:
            self.logger.debug(f"No XML_ALERTS_EMAIL configured, alert not sent: {message}")
            return
        
        try:
            from email_service import EmailService
            email_service = EmailService()
            
            subject_prefix = {
                'info': 'ℹ️ XML Feed Info',
                'warning': '⚠️ XML Feed Warning',
                'error': '❌ XML Feed Error'
            }.get(alert_type, 'XML Feed Alert')
            
            subject = f"{subject_prefix}: {message[:50]}..."
            
            # Send email alert
            email_service.send_email(
                to_email=alerts_email,
                subject=subject,
                body=f"""
                XML Feed System Alert
                
                Type: {alert_type.upper()}
                Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
                
                Message:
                {message}
                
                Current Status:
                - Freeze Flag: {os.environ.get('XML_FEED_FRZ', 'not set')}
                - System State: {'FROZEN' if self.is_frozen() else 'ACTIVE'}
                
                This is an automated alert from the XML Feed System.
                """
            )
            
            self.logger.info(f"Alert sent to {alerts_email}: {message}")
            
        except Exception as e:
            self.logger.error(f"Failed to send alert email: {str(e)}")
    
    def log_operation_attempt(self, operation: str, allowed: bool):
        """
        Log an operation attempt for auditing
        
        Args:
            operation: Name of the operation attempted
            allowed: Whether the operation was allowed
        """
        status = "ALLOWED" if allowed else "BLOCKED (frozen)"
        self.logger.info(f"Operation '{operation}' {status} at {datetime.now().isoformat()}")
        
        # Send alert if operation was blocked and it's a critical operation
        critical_operations = ['rebuild', 'upload', 'publish']
        if not allowed and operation in critical_operations:
            self.send_alert(
                f"Critical operation '{operation}' was blocked due to freeze state",
                'warning'
            )