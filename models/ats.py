"""Bullhorn / ATS integration models: monitors, activity, history, email logs, parsed emails, owner reassignment cooldown."""
from datetime import datetime, timedelta
from extensions import db


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
    activity_type = db.Column(db.String(50), nullable=False)  # 'job_added', 'job_removed', 'job_modified', 'check_completed', 'error'
    job_id = db.Column(db.Integer, nullable=True)  # Bullhorn job ID if applicable
    job_title = db.Column(db.String(255), nullable=True)
    account_manager = db.Column(db.String(255), nullable=True)  # Account manager/owner name
    details = db.Column(db.Text, nullable=True)  # JSON or text details
    notification_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    monitor = db.relationship('BullhornMonitor', backref='activities')  # Can be None for system-level activities

    __table_args__ = (
        db.Index('idx_activity_type_job_created', 'activity_type', 'job_id', 'created_at'),
        db.Index('idx_activity_created_at', 'created_at'),
    )

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
    notification_type = db.Column(db.String(50), nullable=False, index=True)  # 'job_added', 'job_removed', 'job_modified', 'scheduled_processing'
    job_id = db.Column(db.String(20), nullable=True)  # Bullhorn job ID (null for scheduled processing)
    job_title = db.Column(db.String(255), nullable=True)  # Job title for reference
    recipient_email = db.Column(db.String(255), nullable=False, index=True)  # Email address notification was sent to
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)  # When email was sent
    delivery_status = db.Column(db.String(20), default='sent', nullable=False)  # 'sent', 'failed', 'pending'
    sendgrid_message_id = db.Column(db.String(255), nullable=True)  # SendGrid message ID for tracking
    error_message = db.Column(db.Text, nullable=True)  # Error details if delivery failed

    # Additional context fields
    schedule_name = db.Column(db.String(100), nullable=True)  # For scheduled processing notifications
    changes_summary = db.Column(db.Text, nullable=True)  # Summary of changes that triggered the notification

    def __repr__(self):
        return f'<EmailDeliveryLog {self.notification_type} to {self.recipient_email}>'


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
    candidate_email = db.Column(db.String(255), nullable=True, index=True)
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
    vetted_at = db.Column(db.DateTime, nullable=True)  # Tracks when AI vetting was completed
    vetting_retry_count = db.Column(db.Integer, default=0, server_default='0')

    __table_args__ = (
        db.Index('idx_parsed_email_unvetted', 'status', 'vetted_at', 'bullhorn_candidate_id'),
    )

    def __repr__(self):
        return f'<ParsedEmail {self.id} from {self.source_platform} - {self.status}>'


class OwnerReassignmentCooldown(db.Model):
    """
    Per-candidate cooldown tracker for the Owner Reassignment task.

    Bandage to stop the 5-min reassignment cycle from re-evaluating the same
    ~5,000 Pandologic / Matador / Myticas candidates every cycle. When a
    candidate is evaluated and the outcome is a no-op (no human activity yet,
    or already owned by the correct user), we record it here. Subsequent
    cycles skip any candidate whose `last_evaluated_at` is within the
    configured cooldown window (default 24 h).

    Outcomes stored:
      - 'no_human_activity'  no recruiter notes found yet; check again later
      - 'already_correct'    owner is already the right human; nothing to do

    A successful reassign DELETES the row (the candidate is now resolved).
    A failed update leaves the row absent so the next cycle retries.

    Kill switches (VettingConfig):
      - owner_reassignment_cooldown_enabled (default 'true')
      - owner_reassignment_cooldown_hours   (default '24')
    """
    __tablename__ = 'owner_reassignment_cooldown'

    candidate_id = db.Column(db.BigInteger, primary_key=True)
    last_evaluated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, index=True
    )
    last_outcome = db.Column(db.String(40), nullable=False)
    evaluation_count = db.Column(db.Integer, nullable=False, default=1)

    def __repr__(self):
        return (
            f'<OwnerReassignmentCooldown candidate={self.candidate_id} '
            f'outcome={self.last_outcome} at={self.last_evaluated_at}>'
        )
