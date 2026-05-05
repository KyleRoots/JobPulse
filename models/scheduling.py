"""Scheduling, processing logs, global settings, and scheduler-lock models."""
import os
from datetime import datetime, timedelta
from extensions import db


class ScheduleConfig(db.Model):
    """Configuration for automated XML processing schedules"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)  # Path to XML file to monitor
    original_filename = db.Column(db.String(255), nullable=True)  # Original filename to preserve
    schedule_days = db.Column(db.Integer, nullable=False, default=7)  # Days between runs
    last_run = db.Column(db.DateTime, nullable=True)
    next_run = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, default=True, index=True)

    # Email notification settings
    notification_email = db.Column(db.String(255), nullable=True)  # Email for notifications
    send_email_notifications = db.Column(db.Boolean, default=False)

    # Whether this schedule should trigger an SFTP upload (SFTP credentials come from Global Settings)
    auto_upload_ftp = db.Column(db.Boolean, default=False)

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

    __table_args__ = (
        db.Index('idx_processing_log_processed_at', 'processed_at'),
    )

    def __repr__(self):
        return f'<ProcessingLog {self.file_path} - {self.processing_type}>'


class JobReferenceNumber(db.Model):
    """Store job reference numbers for preservation across automated uploads"""
    id = db.Column(db.Integer, primary_key=True)
    bullhorn_job_id = db.Column(db.String(50), unique=True, nullable=False)  # bhatsid from XML
    reference_number = db.Column(db.String(50), nullable=False, index=True)  # The reference number to preserve
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
    """Global application settings including SFTP credentials and general configuration"""
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), unique=True, nullable=False)
    setting_value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(500), nullable=True)
    category = db.Column(db.String(50), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<GlobalSettings {self.setting_key}: {self.setting_value}>'

    @classmethod
    def get_value(cls, key, default=None):
        """Get a setting value by key"""
        setting = cls.query.filter_by(setting_key=key).first()
        return setting.setting_value if setting else default

    @classmethod
    def set_value(cls, key, value, description=None, category=None):
        """Set a setting value, creating if it doesn't exist"""
        from extensions import db
        setting = cls.query.filter_by(setting_key=key).first()
        if setting:
            setting.setting_value = str(value)
            if description:
                setting.description = description
            if category:
                setting.category = category
        else:
            setting = cls(setting_key=key, setting_value=str(value), description=description, category=category)
            db.session.add(setting)
        db.session.commit()
        return setting


# Backward-compat alias preserved from monolithic models.py
EmailParsingConfig = GlobalSettings


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
            except Exception:
                # Fallback to basic python logging if Flask context unavailable
                import logging
                logging.getLogger(__name__).error(f"Error cleaning up expired locks: {str(e)}")
            db.session.rollback()
            return 0

    def __repr__(self):
        return f'<SchedulerLock owner={self.owner_process_id} env={self.environment}>'
