"""Environment status, health checks, log monitoring, backup, and OneDrive sync models."""
from datetime import datetime
from extensions import db


class EnvironmentStatus(db.Model):
    """Track production environment up/down status for monitoring and alerting"""
    id = db.Column(db.Integer, primary_key=True)
    environment_name = db.Column(db.String(100), nullable=False, default='production', index=True)  # Environment being monitored
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


class VettingHealthCheck(db.Model):
    """Health check results for the vetting system"""
    id = db.Column(db.Integer, primary_key=True)
    check_time = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Component status (True = healthy, False = error)
    bullhorn_status = db.Column(db.Boolean, default=True)
    openai_status = db.Column(db.Boolean, default=True)
    database_status = db.Column(db.Boolean, default=True)
    scheduler_status = db.Column(db.Boolean, default=True)

    # Error details (null if healthy)
    bullhorn_error = db.Column(db.Text, nullable=True)
    openai_error = db.Column(db.Text, nullable=True)
    database_error = db.Column(db.Text, nullable=True)
    scheduler_error = db.Column(db.Text, nullable=True)

    # Overall status
    is_healthy = db.Column(db.Boolean, default=True)

    # Stats
    candidates_processed_today = db.Column(db.Integer, default=0)
    candidates_pending = db.Column(db.Integer, default=0)
    emails_sent_today = db.Column(db.Integer, default=0)
    last_successful_cycle = db.Column(db.DateTime, nullable=True)

    # Alert tracking
    alert_sent = db.Column(db.Boolean, default=False)
    alert_sent_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<VettingHealthCheck {self.check_time} healthy={self.is_healthy}>'


class LogMonitoringRun(db.Model):
    """Persists each log monitoring cycle run for transparency and audit trail"""
    id = db.Column(db.Integer, primary_key=True)
    run_time = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    logs_analyzed = db.Column(db.Integer, default=0)
    issues_found = db.Column(db.Integer, default=0)
    issues_auto_fixed = db.Column(db.Integer, default=0)
    issues_escalated = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), nullable=False, default='completed')  # 'healthy', 'issues_detected', 'error'
    time_range_start = db.Column(db.DateTime, nullable=True)
    time_range_end = db.Column(db.DateTime, nullable=True)

    # Tracking
    was_manual = db.Column(db.Boolean, default=False)  # True if triggered by "Run Now" button
    execution_time_ms = db.Column(db.Integer, nullable=True)  # How long the cycle took

    # Relationship to issues
    issues = db.relationship('LogMonitoringIssue', backref='run', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<LogMonitoringRun {self.id} at {self.run_time} - {self.status}>'


class LogMonitoringIssue(db.Model):
    """Persists all detected issues with resolution details for full transparency"""
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('log_monitoring_run.id'), nullable=False, index=True)
    detected_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Issue classification
    pattern_name = db.Column(db.String(200), nullable=False)  # e.g., "API rate limit hit"
    category = db.Column(db.String(50), nullable=False)  # 'auto_fix', 'auto_fix_notify', 'escalate', 'ignore'
    severity = db.Column(db.String(20), nullable=False, default='minor')  # 'minor', 'major', 'critical'
    description = db.Column(db.Text, nullable=True)
    occurrences = db.Column(db.Integer, default=1)
    sample_log = db.Column(db.Text, nullable=True)  # Sample log content for context

    # Resolution tracking
    status = db.Column(db.String(50), nullable=False, default='detected')  # 'detected', 'auto_fixed', 'escalated', 'resolved', 'ignored'
    resolution_action = db.Column(db.Text, nullable=True)  # Human-readable description of fix taken
    resolution_summary = db.Column(db.Text, nullable=True)  # AI-generated summary of what happened
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by = db.Column(db.String(100), nullable=True)  # 'system' or user email for manual resolutions

    # Index for efficient filtering
    __table_args__ = (
        db.Index('idx_issue_status_severity', 'status', 'severity'),
        db.Index('idx_issue_detected_at', 'detected_at'),
    )

    def __repr__(self):
        return f'<LogMonitoringIssue {self.pattern_name} - {self.status}>'

    @classmethod
    def get_severity_for_category(cls, category):
        """Determine severity based on issue category"""
        severity_map = {
            'auto_fix': 'minor',
            'auto_fix_notify': 'major',
            'escalate': 'critical',
            'ignore': 'minor'
        }
        return severity_map.get(category, 'minor')

    def mark_auto_fixed(self, action_description):
        """Mark this issue as auto-fixed with the action taken"""
        self.status = 'auto_fixed'
        self.resolution_action = action_description
        self.resolution_summary = f"Automatically resolved: {action_description}"
        self.resolved_at = datetime.utcnow()
        self.resolved_by = 'system'

    def mark_escalated(self, escalation_details=None):
        """Mark this issue as escalated for human review"""
        self.status = 'escalated'
        self.resolution_summary = escalation_details or "Escalated for human review"

    def mark_resolved(self, resolver_email, resolution_notes=None):
        """Mark an escalated issue as resolved by a human"""
        self.status = 'resolved'
        self.resolved_at = datetime.utcnow()
        self.resolved_by = resolver_email
        if resolution_notes:
            self.resolution_action = resolution_notes
            self.resolution_summary = f"Manually resolved by {resolver_email}: {resolution_notes}"


class BackupLog(db.Model):
    __tablename__ = 'backup_log'

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='running')
    file_name = db.Column(db.String(500), nullable=True)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)
    onedrive_item_id = db.Column(db.String(255), nullable=True)
    onedrive_web_url = db.Column(db.String(1000), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    triggered_by = db.Column(db.String(50), nullable=False, default='scheduler')

    __table_args__ = (
        db.Index('idx_backup_log_started_at', 'started_at'),
        db.Index('idx_backup_log_status', 'status'),
    )

    def __repr__(self):
        return f'<BackupLog {self.id}: {self.status} @ {self.started_at}>'


class OneDriveSyncFolder(db.Model):
    __tablename__ = 'onedrive_sync_folder'

    id = db.Column(db.Integer, primary_key=True)
    onedrive_folder_id = db.Column(db.String(255), nullable=False, unique=True)
    folder_name = db.Column(db.String(500), nullable=False)
    folder_path = db.Column(db.String(1000), nullable=True)
    sync_enabled = db.Column(db.Boolean, nullable=False, default=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    last_sync_files = db.Column(db.Integer, nullable=True, default=0)
    added_by = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<OneDriveSyncFolder {self.id} "{self.folder_name}">'
