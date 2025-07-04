from datetime import datetime, timedelta
import os

def create_models(db):
    """Create database models using the provided db instance"""
    
    class ScheduleConfig(db.Model):
        """Configuration for automated XML processing schedules"""
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(100), nullable=False)
        file_path = db.Column(db.String(500), nullable=False)  # Path to XML file to monitor
        original_filename = db.Column(db.String(255), nullable=True)  # Original filename to preserve
        schedule_days = db.Column(db.Integer, nullable=False, default=7)  # Days between runs
        last_run = db.Column(db.DateTime, nullable=True)
        next_run = db.Column(db.DateTime, nullable=False)
        is_active = db.Column(db.Boolean, default=True)
        
        # Email notification settings
        notification_email = db.Column(db.String(255), nullable=True)  # Email for notifications
        send_email_notifications = db.Column(db.Boolean, default=False)
        
        # FTP/SFTP upload settings
        ftp_hostname = db.Column(db.String(255), nullable=True)
        ftp_username = db.Column(db.String(100), nullable=True)
        ftp_password = db.Column(db.String(255), nullable=True)  # Consider encryption in production
        ftp_directory = db.Column(db.String(500), nullable=True, default="/")
        ftp_port = db.Column(db.Integer, nullable=True, default=21)
        use_sftp = db.Column(db.Boolean, default=False)
        auto_upload_ftp = db.Column(db.Boolean, default=False)
        
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
        
        def __repr__(self):
            return f'<ScheduleConfig {self.name}>'
        
        def calculate_next_run(self):
            """Calculate the next run time based on schedule_days"""
            if self.last_run:
                self.next_run = self.last_run + timedelta(days=self.schedule_days)
            else:
                # If never run, schedule for next occurrence
                self.next_run = datetime.utcnow() + timedelta(days=self.schedule_days)
            return self.next_run

    class ProcessingLog(db.Model):
        """Log of all processing operations"""
        id = db.Column(db.Integer, primary_key=True)
        schedule_config_id = db.Column(db.Integer, db.ForeignKey('schedule_config.id'), nullable=True)
        file_path = db.Column(db.String(500), nullable=False)
        processing_type = db.Column(db.String(20), nullable=False)  # 'scheduled' or 'manual'
        jobs_processed = db.Column(db.Integer, nullable=False, default=0)
        success = db.Column(db.Boolean, nullable=False)
        error_message = db.Column(db.Text, nullable=True)
        processed_at = db.Column(db.DateTime, default=datetime.utcnow)
        
        # Relationship
        schedule_config = db.relationship('ScheduleConfig', backref='processing_logs')
        
        def __repr__(self):
            return f'<ProcessingLog {self.file_path} - {self.processing_type}>'
    
    class GlobalSettings(db.Model):
        """Global application settings including SFTP credentials"""
        id = db.Column(db.Integer, primary_key=True)
        setting_key = db.Column(db.String(100), unique=True, nullable=False)
        setting_value = db.Column(db.Text, nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
        
        def __repr__(self):
            return f'<GlobalSettings {self.setting_key}: {self.setting_value}>'

    return ScheduleConfig, ProcessingLog, GlobalSettings