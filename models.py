from datetime import datetime, timedelta
import os
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db

class User(UserMixin, db.Model):
    """User model for authentication"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'

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
    
    # FTP/SFTP upload settings (DEPRECATED - now using Global Settings)
    ftp_hostname = db.Column(db.String(255), nullable=True)
    ftp_username = db.Column(db.String(100), nullable=True)
    ftp_password = db.Column(db.String(255), nullable=True)  # Consider encryption in production
    ftp_directory = db.Column(db.String(500), nullable=True, default="/")
    ftp_port = db.Column(db.Integer, nullable=True, default=21)
    use_sftp = db.Column(db.Boolean, default=False)
    auto_upload_ftp = db.Column(db.Boolean, default=False)  # Still used - indicates if this schedule should use SFTP
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_file_upload = db.Column(db.DateTime, nullable=True)  # Track when file was last uploaded/replaced
    
    def __repr__(self):
        return f'<ScheduleConfig {self.name}>'
    
    @property
    def interval_type(self):
        """Derive interval type from schedule_days for backward compatibility"""
        if self.schedule_days == 1:
            return 'daily'
        elif self.schedule_days == 7:
            return 'weekly'
        elif self.schedule_days < 1:
            return 'hourly'
        else:
            return 'weekly'  # Default for custom intervals
    
    def calculate_next_run(self):
        """Calculate the next run time based on schedule_days"""
        if self.last_run:
            self.next_run = self.last_run + timedelta(days=self.schedule_days)
        else:
            # If never run, schedule for next occurrence
            self.next_run = datetime.utcnow() + timedelta(days=self.schedule_days)
        return self.next_run
    
    @property
    def file_size(self):
        """Get the formatted file size of the XML file"""
        try:
            if self.file_path and os.path.exists(self.file_path):
                size_bytes = os.path.getsize(self.file_path)
                return self.format_file_size(size_bytes)
            return "N/A"
        except (OSError, IOError):
            return "N/A"
    
    @staticmethod
    def format_file_size(size_bytes):
        """Format file size in human-readable format"""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ["B", "KB", "MB", "GB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        
        if i == 0:  # Bytes
            return f"{int(size_bytes)} {size_names[i]}"
        else:
            return f"{size_bytes:.1f} {size_names[i]}"

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

class JobReferenceNumber(db.Model):
    """Store job reference numbers for preservation across automated uploads"""
    id = db.Column(db.Integer, primary_key=True)
    bullhorn_job_id = db.Column(db.String(50), unique=True, nullable=False)  # bhatsid from XML
    reference_number = db.Column(db.String(50), nullable=False)  # The reference number to preserve
    job_title = db.Column(db.String(500), nullable=True)  # For identification purposes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<JobReferenceNumber {self.bullhorn_job_id}: {self.reference_number}>'

class RefreshLog(db.Model):
    """Track reference number refresh completions"""
    id = db.Column(db.Integer, primary_key=True)
    refresh_date = db.Column(db.Date, nullable=False)
    refresh_time = db.Column(db.DateTime, nullable=False)
    jobs_updated = db.Column(db.Integer, nullable=False, default=0)
    processing_time = db.Column(db.Float, nullable=False, default=0.0)
    email_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<RefreshLog {self.refresh_date}>'

class GlobalSettings(db.Model):
    """Global application settings including SFTP credentials"""
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), unique=True, nullable=False)
    setting_value = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<GlobalSettings {self.setting_key}: {self.setting_value}>'

class BullhornMonitor(db.Model):
    """Configuration for Bullhorn tearsheet monitoring"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    tearsheet_id = db.Column(db.Integer, nullable=False)
    tearsheet_name = db.Column(db.String(255), nullable=True)  # Store tearsheet name for reference
    is_active = db.Column(db.Boolean, default=True)
    check_interval_minutes = db.Column(db.Integer, default=5)  # How often to check for changes
    last_check = db.Column(db.DateTime, nullable=True)
    next_check = db.Column(db.DateTime, nullable=False)
    notification_email = db.Column(db.String(255), nullable=True)
    send_notifications = db.Column(db.Boolean, default=True)
    
    # Store the last known job list as JSON
    last_job_snapshot = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<BullhornMonitor {self.name}>'
    
    def calculate_next_check(self):
        """Calculate the next check time based on interval"""
        if self.last_check:
            self.next_check = self.last_check + timedelta(minutes=self.check_interval_minutes)
        else:
            self.next_check = datetime.utcnow() + timedelta(minutes=self.check_interval_minutes)

class BullhornActivity(db.Model):
    """Log of Bullhorn monitoring activities"""
    id = db.Column(db.Integer, primary_key=True)
    monitor_id = db.Column(db.Integer, db.ForeignKey('bullhorn_monitor.id'), nullable=True)  # Nullable for system-level activities
    activity_type = db.Column(db.String(20), nullable=False)  # 'job_added', 'job_removed', 'job_modified', 'check_completed', 'error'
    job_id = db.Column(db.Integer, nullable=True)  # Bullhorn job ID if applicable
    job_title = db.Column(db.String(255), nullable=True)
    account_manager = db.Column(db.String(255), nullable=True)  # Account manager/owner name
    details = db.Column(db.Text, nullable=True)  # JSON or text details
    notification_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    monitor = db.relationship('BullhornMonitor', backref='activities')  # Can be None for system-level activities
    
    @classmethod
    def check_duplicate_activity(cls, activity_type: str, job_id: int, minutes_threshold: int = 5):
        """
        Check if similar activity exists within time threshold to prevent duplicates
        
        Args:
            activity_type: Type of activity to check
            job_id: Job ID to check
            minutes_threshold: Time window in minutes to check for duplicates
            
        Returns:
            bool: True if duplicate found, False if safe to create
        """
        if not job_id:
            return False
            
        cutoff_time = datetime.utcnow() - timedelta(minutes=minutes_threshold)
        
        duplicate = cls.query.filter(
            cls.activity_type == activity_type,
            cls.job_id == job_id,
            cls.created_at >= cutoff_time
        ).first()
        
        return duplicate is not None
    
    def __repr__(self):
        return f'<BullhornActivity {self.activity_type} - {self.job_title}>'

class TearsheetJobHistory(db.Model):
    """Track job state history for tearsheets to detect modifications"""
    id = db.Column(db.Integer, primary_key=True)
    tearsheet_id = db.Column(db.Integer, nullable=False)
    job_id = db.Column(db.Integer, nullable=False)
    job_title = db.Column(db.String(500), nullable=True)
    job_description = db.Column(db.Text, nullable=True)
    job_city = db.Column(db.String(255), nullable=True)
    job_state = db.Column(db.String(255), nullable=True)
    job_country = db.Column(db.String(255), nullable=True)
    job_owner_name = db.Column(db.String(255), nullable=True)
    job_remote_type = db.Column(db.String(100), nullable=True)
    is_current = db.Column(db.Boolean, default=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Index for efficient lookups
    __table_args__ = (
        db.Index('idx_tearsheet_job_current', 'tearsheet_id', 'job_id', 'is_current'),
    )
    
    def __repr__(self):
        return f'<TearsheetJobHistory tearsheet={self.tearsheet_id} job={self.job_id}>'

class EmailDeliveryLog(db.Model):
    """Log of email notifications sent for job changes"""
    id = db.Column(db.Integer, primary_key=True)
    notification_type = db.Column(db.String(50), nullable=False)  # 'job_added', 'job_removed', 'job_modified', 'scheduled_processing'
    job_id = db.Column(db.String(20), nullable=True)  # Bullhorn job ID (null for scheduled processing)
    job_title = db.Column(db.String(255), nullable=True)  # Job title for reference
    recipient_email = db.Column(db.String(255), nullable=False)  # Email address notification was sent to
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)  # When email was sent
    delivery_status = db.Column(db.String(20), default='sent', nullable=False)  # 'sent', 'failed', 'pending'
    sendgrid_message_id = db.Column(db.String(255), nullable=True)  # SendGrid message ID for tracking
    error_message = db.Column(db.Text, nullable=True)  # Error details if delivery failed
    
    # Additional context fields
    schedule_name = db.Column(db.String(100), nullable=True)  # For scheduled processing notifications
    changes_summary = db.Column(db.Text, nullable=True)  # Summary of changes that triggered the notification
    
    def __repr__(self):
        return f'<EmailDeliveryLog {self.notification_type} to {self.recipient_email}>'

class RecruiterMapping(db.Model):
    """Mapping of recruiter names to LinkedIn tags (#LI-XXX)"""
    id = db.Column(db.Integer, primary_key=True)
    recruiter_name = db.Column(db.String(255), nullable=False, unique=True)  # Full recruiter name
    linkedin_tag = db.Column(db.String(20), nullable=False)  # LinkedIn tag (e.g., #LI-RP1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<RecruiterMapping {self.recruiter_name}: {self.linkedin_tag}>'

class SchedulerLock(db.Model):
    """Lock to ensure only one scheduler instance runs automated jobs"""
    id = db.Column(db.Integer, primary_key=True, default=1)  # Only one row allowed
    owner_process_id = db.Column(db.String(100), nullable=False)  # Process/instance identifier
    acquired_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    environment = db.Column(db.String(20), nullable=False)  # 'production' or 'dev'
    
    @classmethod
    def acquire_lock(cls, process_id, environment, duration_minutes=5):
        """Try to acquire the scheduler lock for specified duration"""
        expires_at = datetime.utcnow() + timedelta(minutes=duration_minutes)
        
        try:
            # Try to get existing lock
            lock = cls.query.filter_by(id=1).first()
            
            if lock:
                # Check if lock has expired or is from same environment
                if lock.expires_at < datetime.utcnow() or lock.environment != environment:
                    # Lock expired or different environment - take it over
                    lock.owner_process_id = process_id
                    lock.acquired_at = datetime.utcnow()
                    lock.expires_at = expires_at
                    lock.environment = environment
                    db.session.commit()
                    return True
                else:
                    # Lock still active for same environment
                    return False
            else:
                # No lock exists - create new one
                new_lock = cls(
                    owner_process_id=process_id,
                    expires_at=expires_at,
                    environment=environment
                )
                db.session.add(new_lock)
                db.session.commit()
                return True
                
        except Exception:
            db.session.rollback()
            return False
    
    @classmethod
    def renew_lock(cls, process_id, duration_minutes=5):
        """Renew the lock if we own it"""
        try:
            lock = cls.query.filter_by(id=1, owner_process_id=process_id).first()
            if lock:
                lock.expires_at = datetime.utcnow() + timedelta(minutes=duration_minutes)
                db.session.commit()
                return True
            return False
        except Exception:
            db.session.rollback()
            return False
    
    @classmethod
    def release_lock(cls, process_id, environment=None):
        """Release the lock if we own it"""
        try:
            query = cls.query.filter_by(id=1, owner_process_id=process_id)
            if environment:
                query = query.filter_by(environment=environment)
            
            lock = query.first()
            if lock:
                db.session.delete(lock)
                db.session.commit()
                return True
            return False
        except Exception:
            db.session.rollback()
            return False
    
    @classmethod
    def cleanup_expired_locks(cls, environment=None):
        """Clean up locks that have passed their expires_at time (safe TTL-based cleanup)"""
        try:
            from flask import current_app
            now = datetime.utcnow()
            query = cls.query.filter(cls.id == 1, cls.expires_at < now)
            if environment:
                query = query.filter(cls.environment == environment)
            
            expired_locks = query.all()
            
            if expired_locks:
                deleted_count = 0
                for lock in expired_locks:
                    current_app.logger.info(f"🧹 Cleaning up expired lock: owner={lock.owner_process_id}, env={lock.environment}, expired_at={lock.expires_at}")
                    db.session.delete(lock)
                    deleted_count += 1
                db.session.commit()
                return deleted_count
            return 0
        except Exception as e:
            try:
                from flask import current_app
                current_app.logger.error(f"Error cleaning up expired locks: {str(e)}")
            except:
                # Fallback to basic python logging if Flask context unavailable
                import logging
                logging.getLogger(__name__).error(f"Error cleaning up expired locks: {str(e)}")
            db.session.rollback()
            return 0
    
    def __repr__(self):
        return f'<SchedulerLock owner={self.owner_process_id} env={self.environment}>'

class EnvironmentStatus(db.Model):
    """Track production environment up/down status for monitoring and alerting"""
    id = db.Column(db.Integer, primary_key=True)
    environment_name = db.Column(db.String(100), nullable=False, default='production')  # Environment being monitored
    environment_url = db.Column(db.String(500), nullable=False)  # URL to monitor
    current_status = db.Column(db.String(20), nullable=False, default='unknown')  # 'up', 'down', 'unknown'
    last_check_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_status_change = db.Column(db.DateTime, nullable=True)  # When status last changed
    consecutive_failures = db.Column(db.Integer, default=0)  # Track consecutive failed checks
    total_downtime_minutes = db.Column(db.Float, default=0.0)  # Track total downtime
    
    # Check configuration
    check_interval_minutes = db.Column(db.Integer, default=5)  # How often to check
    timeout_seconds = db.Column(db.Integer, default=30)  # Request timeout
    alert_email = db.Column(db.String(255), nullable=False, default='kroots@myticas.com')
    
    # Alert settings
    alert_on_down = db.Column(db.Boolean, default=True)
    alert_on_recovery = db.Column(db.Boolean, default=True)
    
    # Tracking fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<EnvironmentStatus {self.environment_name}: {self.current_status}>'
    
    @property
    def is_up(self):
        """Check if environment is currently up"""
        return self.current_status == 'up'
    
    @property
    def is_down(self):
        """Check if environment is currently down"""
        return self.current_status == 'down'
    
    @property
    def uptime_percentage(self):
        """Calculate uptime percentage over the last 30 days"""
        if not self.created_at:
            return 100.0
        
        # Simple calculation based on total downtime vs time since creation
        days_since_creation = (datetime.utcnow() - self.created_at).total_seconds() / 86400
        if days_since_creation <= 0:
            return 100.0
        
        downtime_days = self.total_downtime_minutes / 1440  # Convert minutes to days
        uptime_percentage = max(0, 100 - (downtime_days / days_since_creation * 100))
        return round(uptime_percentage, 2)

class EnvironmentAlert(db.Model):
    """Log of environment alerts sent for down/up notifications"""
    id = db.Column(db.Integer, primary_key=True)
    environment_status_id = db.Column(db.Integer, db.ForeignKey('environment_status.id'), nullable=False)
    alert_type = db.Column(db.String(20), nullable=False)  # 'down', 'up', 'degraded'
    alert_message = db.Column(db.Text, nullable=False)  # Full alert message content
    recipient_email = db.Column(db.String(255), nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    delivery_status = db.Column(db.String(20), default='sent', nullable=False)  # 'sent', 'failed', 'pending'
    sendgrid_message_id = db.Column(db.String(255), nullable=True)  # SendGrid tracking
    error_message = db.Column(db.Text, nullable=True)  # Error details if delivery failed
    
    # Context fields
    downtime_duration = db.Column(db.Float, nullable=True)  # Minutes of downtime (for recovery alerts)
    error_details = db.Column(db.Text, nullable=True)  # Technical error details
    
    # Relationship
    environment_status = db.relationship('EnvironmentStatus', backref='alerts')
    
    def __repr__(self):
        return f'<EnvironmentAlert {self.alert_type} to {self.recipient_email}>'


class ParsedEmail(db.Model):
    """Track emails received and parsed by the inbound email parsing system"""
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(255), unique=True, nullable=True)
    sender_email = db.Column(db.String(255), nullable=False)
    recipient_email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(500), nullable=True)
    source_platform = db.Column(db.String(50), nullable=True)  # 'Dice', 'LinkedIn Job Board', etc.
    bullhorn_job_id = db.Column(db.Integer, nullable=True)
    
    # Candidate info extracted from email
    candidate_name = db.Column(db.String(255), nullable=True)
    candidate_email = db.Column(db.String(255), nullable=True)
    candidate_phone = db.Column(db.String(50), nullable=True)
    
    # Processing status
    status = db.Column(db.String(50), default='received')  # 'received', 'processing', 'completed', 'failed', 'duplicate'
    processing_notes = db.Column(db.Text, nullable=True)
    
    # Bullhorn integration results
    bullhorn_candidate_id = db.Column(db.Integer, nullable=True)
    bullhorn_submission_id = db.Column(db.Integer, nullable=True)
    is_duplicate_candidate = db.Column(db.Boolean, default=False)
    duplicate_confidence = db.Column(db.Float, nullable=True)  # 0.0 to 1.0
    
    # Resume file info
    resume_filename = db.Column(db.String(255), nullable=True)
    resume_file_id = db.Column(db.Integer, nullable=True)  # Bullhorn file ID after upload
    
    # Timestamps
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<ParsedEmail {self.id} from {self.source_platform} - {self.status}>'


class EmailParsingConfig(db.Model):
    """Configuration for email parsing service"""
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), unique=True, nullable=False)
    setting_value = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<EmailParsingConfig {self.setting_key}>'