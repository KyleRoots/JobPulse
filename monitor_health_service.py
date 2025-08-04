#!/usr/bin/env python3
"""
Monitor Health Service
=====================
Automated monitoring and alerting for overdue tearsheet monitors
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict
from email_service import EmailService


class MonitorHealthService:
    """Service for monitoring tearsheet monitor health and sending alerts"""
    
    def __init__(self, db_session, global_settings_model, bullhorn_monitor_model):
        self.db = db_session
        self.GlobalSettings = global_settings_model
        self.BullhornMonitor = bullhorn_monitor_model
        self.logger = logging.getLogger(__name__)
        # Initialize email service with database logging support
        self.email_service = None
        
    def check_monitor_health(self) -> Dict:
        """
        Check all active monitors for overdue status and send notifications
        
        Returns:
            Dict with health check results and actions taken
        """
        try:
            current_time = datetime.utcnow()
            overdue_threshold = timedelta(minutes=10)  # Alert if overdue by 10+ minutes
            
            # Get all active monitors
            active_monitors = self.BullhornMonitor.query.filter_by(is_active=True).all()
            
            if not active_monitors:
                self.logger.info("No active monitors found for health check")
                return {'status': 'no_monitors', 'overdue_count': 0}
            
            overdue_monitors = []
            corrected_monitors = []
            
            # Check each monitor for overdue status
            for monitor in active_monitors:
                if not monitor.next_check:
                    # Monitor has no schedule - fix immediately
                    monitor.next_check = current_time
                    monitor.last_check = current_time
                    corrected_monitors.append({
                        'name': monitor.tearsheet_name,
                        'issue': 'no_schedule',
                        'action': 'reset_timing'
                    })
                    continue
                
                time_overdue = current_time - monitor.next_check
                
                if time_overdue > overdue_threshold:
                    # Monitor is significantly overdue
                    minutes_overdue = time_overdue.total_seconds() / 60
                    
                    overdue_monitors.append({
                        'name': monitor.tearsheet_name,
                        'monitor_id': monitor.id,
                        'minutes_overdue': minutes_overdue,
                        'last_check': monitor.last_check,
                        'next_check': monitor.next_check,
                        'interval': monitor.check_interval
                    })
                    
                    # Auto-correct timing if severely overdue (>30 minutes)
                    if minutes_overdue > 30:
                        monitor.next_check = current_time
                        monitor.last_check = current_time
                        corrected_monitors.append({
                            'name': monitor.tearsheet_name,
                            'issue': f'severely_overdue_{minutes_overdue:.0f}min',
                            'action': 'auto_corrected'
                        })
            
            # Commit any timing corrections
            if corrected_monitors:
                self.db.commit()
                self.logger.info(f"Auto-corrected timing for {len(corrected_monitors)} monitors")
            
            # Send notification if there are overdue monitors
            notification_sent = False
            if overdue_monitors:
                notification_sent = self._send_overdue_notification(overdue_monitors, corrected_monitors)
            
            return {
                'status': 'completed',
                'timestamp': current_time.isoformat(),
                'total_monitors': len(active_monitors),
                'overdue_count': len(overdue_monitors),
                'corrected_count': len(corrected_monitors),
                'overdue_monitors': overdue_monitors,
                'corrected_monitors': corrected_monitors,
                'notification_sent': notification_sent
            }
            
        except Exception as e:
            self.logger.error(f"Monitor health check failed: {str(e)}")
            return {
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def _send_overdue_notification(self, overdue_monitors: List[Dict], corrected_monitors: List[Dict]) -> bool:
        """
        Send email notification about overdue monitors
        
        Args:
            overdue_monitors: List of overdue monitor details
            corrected_monitors: List of auto-corrected monitors
            
        Returns:
            bool: True if notification was sent successfully
        """
        try:
            # Get notification email from global settings
            notification_email = self._get_notification_email()
            if not notification_email:
                self.logger.warning("No notification email configured - skipping overdue alert")
                return False
            
            # Get email service with database logging support
            from app import get_email_service
            email_service = get_email_service()
            
            # Prepare email content
            subject = f"🚨 JOBPULSE™ Monitor Alert: {len(overdue_monitors)} Overdue Monitors"
            
            # Build detailed email body
            email_body = self._build_overdue_email_body(overdue_monitors, corrected_monitors)
            
            # Send notification
            success = email_service.send_email(
                to_email=notification_email,
                subject=subject,
                html_content=email_body
            )
            
            if success:
                self.logger.info(f"Overdue monitor notification sent to {notification_email}")
            else:
                self.logger.error("Failed to send overdue monitor notification")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Failed to send overdue notification: {str(e)}")
            return False
    
    def _get_notification_email(self) -> str:
        """Get notification email from global settings"""
        try:
            setting = self.GlobalSettings.query.filter_by(setting_key='notification_email').first()
            return setting.setting_value if setting and setting.setting_value else ""
        except Exception as e:
            self.logger.error(f"Failed to get notification email: {str(e)}")
            return ""
    
    def _build_overdue_email_body(self, overdue_monitors: List[Dict], corrected_monitors: List[Dict]) -> str:
        """Build HTML email body for overdue monitor notification"""
        
        current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        html_body = f"""
        <html>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5;">
            <div style="max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 25px; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0; font-size: 24px; font-weight: 600;">🚨 JobPulse™ Monitor Alert</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">Monitor Health Check - {current_time}</p>
                </div>
                
                <!-- Alert Summary -->
                <div style="padding: 25px; border-bottom: 1px solid #eee;">
                    <div style="background-color: #fff3cd; border: 1px solid #ffeaa7; border-radius: 6px; padding: 15px; margin-bottom: 20px;">
                        <h3 style="color: #856404; margin: 0 0 10px 0; font-size: 18px;">⚠️ Action Required</h3>
                        <p style="color: #856404; margin: 0; font-size: 14px;">
                            <strong>{len(overdue_monitors)} monitor(s)</strong> are significantly overdue and may require immediate attention.
                        </p>
                    </div>
                </div>
                
                <!-- Overdue Monitors -->
                <div style="padding: 25px; border-bottom: 1px solid #eee;">
                    <h3 style="color: #333; margin: 0 0 15px 0; font-size: 18px;">🔴 Overdue Monitors</h3>
        """
        
        for monitor in overdue_monitors:
            severity = "CRITICAL" if monitor['minutes_overdue'] > 30 else "WARNING"
            severity_color = "#dc3545" if severity == "CRITICAL" else "#fd7e14"
            
            html_body += f"""
                    <div style="background-color: #f8f9fa; border-left: 4px solid {severity_color}; padding: 15px; margin-bottom: 10px; border-radius: 0 6px 6px 0;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <h4 style="margin: 0; color: #333; font-size: 16px;">{monitor['name']}</h4>
                            <span style="background-color: {severity_color}; color: white; padding: 4px 8px; border-radius: 12px; font-size: 12px; font-weight: 600;">{severity}</span>
                        </div>
                        <p style="margin: 0; font-size: 13px; color: #666;">
                            <strong>Overdue:</strong> {monitor['minutes_overdue']:.0f} minutes<br>
                            <strong>Last Check:</strong> {monitor['last_check'].strftime('%Y-%m-%d %H:%M:%S UTC') if monitor['last_check'] else 'Never'}<br>
                            <strong>Expected:</strong> Every {monitor['interval']} minutes
                        </p>
                    </div>
            """
        
        # Auto-corrected monitors section
        if corrected_monitors:
            html_body += f"""
                </div>
                
                <!-- Auto-Corrected Monitors -->
                <div style="padding: 25px; border-bottom: 1px solid #eee;">
                    <h3 style="color: #333; margin: 0 0 15px 0; font-size: 18px;">🔧 Auto-Corrected</h3>
                    <p style="color: #666; margin: 0 0 15px 0; font-size: 14px;">The following monitors were automatically reset:</p>
            """
            
            for monitor in corrected_monitors:
                html_body += f"""
                    <div style="background-color: #d1ecf1; border-left: 4px solid #17a2b8; padding: 12px; margin-bottom: 8px; border-radius: 0 6px 6px 0;">
                        <p style="margin: 0; font-size: 13px; color: #0c5460;">
                            <strong>{monitor['name']}</strong> - {monitor['action']} ({monitor['issue']})
                        </p>
                    </div>
                """
        
        # Troubleshooting section
        html_body += f"""
                </div>
                
                <!-- Troubleshooting Actions -->
                <div style="padding: 25px;">
                    <h3 style="color: #333; margin: 0 0 15px 0; font-size: 18px;">🔧 Recommended Actions</h3>
                    
                    <div style="background-color: #e7f3ff; border: 1px solid #b8daff; border-radius: 6px; padding: 15px; margin-bottom: 15px;">
                        <h4 style="color: #004085; margin: 0 0 10px 0; font-size: 14px;">Immediate Steps:</h4>
                        <ol style="color: #004085; margin: 0; padding-left: 20px; font-size: 13px;">
                            <li>Check the JobPulse™ ATS Monitoring dashboard</li>
                            <li>Verify APScheduler is running properly</li>
                            <li>Review recent activity logs for error patterns</li>
                            <li>Test Bullhorn API connectivity</li>
                        </ol>
                    </div>
                    
                    <div style="background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 6px; padding: 15px;">
                        <h4 style="color: #495057; margin: 0 0 10px 0; font-size: 14px;">System Information:</h4>
                        <p style="color: #6c757d; margin: 0; font-size: 12px;">
                            This alert was automatically generated by the JobPulse™ Monitor Health Service.<br>
                            Monitors are checked every 15 minutes for overdue status (threshold: 10+ minutes).
                        </p>
                    </div>
                </div>
                
            </div>
        </body>
        </html>
        """
        
        return html_body