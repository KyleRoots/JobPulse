"""Candidate vetting, screening, scout vetting sessions, audit, and config models."""
from datetime import datetime
from extensions import db
from utils.sqlalchemy_types import SafeString, SafeText


class CandidateVettingLog(db.Model):
    """Tracks candidates that have been analyzed by the AI vetting system"""
    id = db.Column(db.Integer, primary_key=True)
    bullhorn_candidate_id = db.Column(db.Integer, nullable=False, index=True)  # Not unique: multiple logs per candidate (one per application)
    candidate_name = db.Column(SafeString(255), nullable=True)
    candidate_email = db.Column(SafeString(255), nullable=True, index=True)
    applied_job_id = db.Column(db.Integer, nullable=True)  # Job they originally applied to
    applied_job_title = db.Column(SafeString(500), nullable=True)
    parsed_email_id = db.Column(db.Integer, nullable=True, index=True)  # Links to specific ParsedEmail that triggered vetting

    # Resume data
    resume_text = db.Column(SafeText, nullable=True)  # Extracted resume content
    resume_file_id = db.Column(db.Integer, nullable=True)  # Bullhorn file ID

    # Analysis status
    status = db.Column(db.String(50), default='pending')  # pending, processing, completed, failed
    is_qualified = db.Column(db.Boolean, default=False)  # True if matched 80%+ on any job
    highest_match_score = db.Column(db.Float, default=0.0)  # Best match score across all jobs
    total_jobs_matched = db.Column(db.Integer, default=0)  # Count of jobs with 80%+ match

    # Note tracking
    note_created = db.Column(db.Boolean, default=False)
    bullhorn_note_id = db.Column(db.Integer, nullable=True)

    # Notification tracking
    notifications_sent = db.Column(db.Boolean, default=False)
    notification_count = db.Column(db.Integer, default=0)  # Number of recruiters notified

    # Error handling
    error_message = db.Column(SafeText, nullable=True)
    retry_count = db.Column(db.Integer, default=0)
    retry_blocked = db.Column(db.Boolean, default=False, server_default='false')
    retry_block_reason = db.Column(db.String(500), nullable=True)

    # Sandbox flag
    is_sandbox = db.Column(db.Boolean, default=False, server_default='false')

    # Timestamps
    detected_at = db.Column(db.DateTime, default=datetime.utcnow)  # When candidate was detected
    analyzed_at = db.Column(db.DateTime, nullable=True)  # When AI analysis completed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_vetting_log_status_created', 'status', 'created_at'),
    )

    # Relationship to match results
    job_matches = db.relationship('CandidateJobMatch', backref='vetting_log', lazy='dynamic',
                                   cascade='all, delete-orphan')

    def __repr__(self):
        return f'<CandidateVettingLog {self.bullhorn_candidate_id} - {self.candidate_name}>'


class CandidateJobMatch(db.Model):
    """Stores individual job match scores for each candidate"""
    id = db.Column(db.Integer, primary_key=True)
    vetting_log_id = db.Column(db.Integer, db.ForeignKey('candidate_vetting_log.id'), nullable=False, index=True)

    # Job details
    bullhorn_job_id = db.Column(db.Integer, nullable=False, index=True)
    job_title = db.Column(SafeString(500), nullable=True)
    job_location = db.Column(SafeString(255), nullable=True)
    tearsheet_id = db.Column(db.Integer, nullable=True)
    tearsheet_name = db.Column(SafeString(255), nullable=True)

    # Recruiter info for notifications
    recruiter_name = db.Column(SafeString(255), nullable=True)
    recruiter_email = db.Column(SafeString(255), nullable=True)
    recruiter_bullhorn_id = db.Column(db.Integer, nullable=True)

    # Match analysis
    match_score = db.Column(db.Float, nullable=False, default=0.0)  # 0-100 percentage
    technical_score = db.Column(db.Float, nullable=True)  # 0-100 technical fit before location penalty
    is_qualified = db.Column(db.Boolean, default=False)  # True if score >= threshold
    is_applied_job = db.Column(db.Boolean, default=False)  # True if this is the job they applied to

    # AI-generated explanations
    match_summary = db.Column(SafeText, nullable=True)  # Brief summary of why they match
    skills_match = db.Column(SafeText, nullable=True)  # Skills alignment details
    experience_match = db.Column(SafeText, nullable=True)  # Experience alignment details
    gaps_identified = db.Column(SafeText, nullable=True)  # What's missing
    years_analysis_json = db.Column(SafeText, nullable=True)  # GPT's years-of-experience calculation (JSON for auditing)

    # Employer prestige tracking
    prestige_employer = db.Column(SafeString(255), nullable=True)
    prestige_boost_applied = db.Column(db.Boolean, default=False)

    # Notification tracking
    notification_sent = db.Column(db.Boolean, default=False)
    notification_sent_at = db.Column(db.DateTime, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_match_job_created', 'bullhorn_job_id', 'created_at'),
    )

    def __repr__(self):
        return f'<CandidateJobMatch {self.bullhorn_job_id} - {self.match_score}%>'


class JobVettingRequirements(db.Model):
    """Per-job screening requirements.

    The screening pipeline reads `get_active_requirements()`, which returns the
    recruiter-edited list when present, otherwise the immutable AI-extracted
    snapshot. The legacy `custom_requirements` column is retained read-only for
    historical rows that pre-date inline editing (Apr 2026).
    """
    id = db.Column(db.Integer, primary_key=True)
    bullhorn_job_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    job_title = db.Column(db.String(255), nullable=True)
    job_location = db.Column(db.String(255), nullable=True)  # City, State, Country
    job_work_type = db.Column(db.String(50), nullable=True)  # On-site, Hybrid, Remote
    custom_requirements = db.Column(db.Text, nullable=True)  # DEPRECATED — kept read-only for legacy rows; new edits write to edited_requirements
    ai_interpreted_requirements = db.Column(db.Text, nullable=True)  # Immutable AI extraction snapshot from Bullhorn job description
    edited_requirements = db.Column(db.Text, nullable=True)  # Recruiter-edited requirements; takes precedence over AI extraction when set
    requirements_edited_at = db.Column(db.DateTime, nullable=True)  # When edited_requirements was last saved
    requirements_edited_by = db.Column(db.String(255), nullable=True)  # Email of the user who last edited
    vetting_threshold = db.Column(db.Integer, nullable=True)  # Custom threshold for this job (null = use global default)
    scout_vetting_enabled = db.Column(db.Boolean, nullable=True)  # null = follow global, True/False = per-job override
    employer_prestige_boost = db.Column(db.Boolean, default=False)  # Per-job toggle for prestige employer scoring boost
    last_ai_interpretation = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<JobVettingRequirements job_id={self.bullhorn_job_id}>'

    def get_active_requirements(self):
        """Return the requirements text the screening engine should use.

        Priority:
            1. `edited_requirements` — recruiter-edited list (Apr 2026+)
            2. `ai_interpreted_requirements` — immutable AI extraction
            3. `custom_requirements` — legacy override (historical rows only)
        """
        if self.edited_requirements and self.edited_requirements.strip():
            return self.edited_requirements
        if self.ai_interpreted_requirements and self.ai_interpreted_requirements.strip():
            return self.ai_interpreted_requirements
        if self.custom_requirements and self.custom_requirements.strip():
            return self.custom_requirements
        return None

    def has_recruiter_edits(self):
        """True when a recruiter has edited the requirements (vs. raw AI extraction)."""
        return bool(self.edited_requirements and self.edited_requirements.strip())


class VettingConfig(db.Model):
    """Configuration settings for the candidate vetting system"""
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), unique=True, nullable=False)
    setting_value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<VettingConfig {self.setting_key}>'

    @classmethod
    def get_value(cls, key, default=None):
        """Get a config value by key"""
        config = cls.query.filter_by(setting_key=key).first()
        return config.setting_value if config else default

    @classmethod
    def set_value(cls, key, value, description=None):
        """Set a config value, creating if it doesn't exist"""
        from extensions import db
        config = cls.query.filter_by(setting_key=key).first()
        if config:
            config.setting_value = str(value)
            if description:
                config.description = description
        else:
            config = cls(setting_key=key, setting_value=str(value), description=description)
            db.session.add(config)
        db.session.commit()
        return config


class EscalationLog(db.Model):
    """Tracks Layer 2 → Layer 3 escalation events for effectiveness analysis"""
    __tablename__ = 'escalation_log'

    id = db.Column(db.Integer, primary_key=True)
    vetting_log_id = db.Column(db.Integer, db.ForeignKey('candidate_vetting_log.id'), nullable=True, index=True)
    bullhorn_candidate_id = db.Column(db.Integer, nullable=False, index=True)
    candidate_name = db.Column(db.String(255), nullable=True)
    bullhorn_job_id = db.Column(db.Integer, nullable=False, index=True)
    job_title = db.Column(db.String(500), nullable=True)
    mini_score = db.Column(db.Float, nullable=False)  # Layer 2 model score
    gpt4o_score = db.Column(db.Float, nullable=False)  # Layer 3 model score
    score_delta = db.Column(db.Float, nullable=False)  # gpt4o_score - mini_score
    material_change = db.Column(db.Boolean, nullable=False, default=False)  # |delta| >= 5 points
    threshold_used = db.Column(db.Float, nullable=False)  # Job-specific or global threshold
    crossed_threshold = db.Column(db.Boolean, nullable=False, default=False)  # Recommendation changed
    escalated_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_escalation_log_escalated_at', 'escalated_at'),
        db.Index('idx_escalation_log_crossed', 'crossed_threshold'),
    )

    def __repr__(self):
        return f'<EscalationLog candidate={self.bullhorn_candidate_id} job={self.bullhorn_job_id} delta={self.score_delta}>'


class ScoutVettingSession(db.Model):
    """Tracks a conversational AI vetting session for a qualified candidate on a specific job.

    Created after Scout Screening qualifies a candidate. Uses multi-turn email
    conversations via GPT-5 to verify skills, experience, and availability
    before recruiter handoff.
    """
    __tablename__ = 'scout_vetting_session'

    id = db.Column(db.Integer, primary_key=True)
    vetting_log_id = db.Column(db.Integer, db.ForeignKey('candidate_vetting_log.id'), nullable=False, index=True)
    candidate_job_match_id = db.Column(db.Integer, db.ForeignKey('candidate_job_match.id'), nullable=True, index=True)

    # Candidate info (denormalized for query efficiency)
    bullhorn_candidate_id = db.Column(db.Integer, nullable=False, index=True)
    candidate_email = db.Column(db.String(255), nullable=False, index=True)
    candidate_name = db.Column(db.String(255), nullable=True)

    # Job info (denormalized)
    bullhorn_job_id = db.Column(db.Integer, nullable=False, index=True)
    job_title = db.Column(db.String(500), nullable=True)

    # Recruiter info (for handoff)
    recruiter_email = db.Column(db.String(255), nullable=True)
    recruiter_name = db.Column(db.String(255), nullable=True)

    # Session state
    # pending: created, waiting to send outreach
    # queued: waiting for slot (max 3 concurrent per candidate)
    # outreach_sent: initial email sent, awaiting reply
    # in_progress: candidate has replied, conversation active
    # qualified: vetting complete, candidate passed
    # not_qualified: vetting complete, candidate did not pass
    # unresponsive: no reply after follow-ups exhausted
    # declined: candidate declined to participate
    status = db.Column(db.String(50), nullable=False, default='pending')

    # Sandbox flag
    is_sandbox = db.Column(db.Boolean, default=False, server_default='false')

    # Vetting content
    vetting_questions_json = db.Column(db.Text, nullable=True)  # JSON array of generated questions
    answered_questions_json = db.Column(db.Text, nullable=True)  # JSON dict of question→answer
    current_turn = db.Column(db.Integer, nullable=False, default=0)
    max_turns = db.Column(db.Integer, nullable=False, default=5)  # Safety cap

    # Email cadence
    last_outreach_at = db.Column(db.DateTime, nullable=True)
    last_reply_at = db.Column(db.DateTime, nullable=True)
    follow_up_count = db.Column(db.Integer, nullable=False, default=0)  # 0/1/2 then unresponsive

    # Outcome
    outcome_summary = db.Column(db.Text, nullable=True)  # AI-generated final assessment
    outcome_score = db.Column(db.Float, nullable=True)  # 0-100 confidence

    # Bullhorn integration
    bullhorn_note_id = db.Column(db.Integer, nullable=True)
    note_created = db.Column(db.Boolean, nullable=False, default=False)
    handoff_sent = db.Column(db.Boolean, nullable=False, default=False)

    # Staggered outreach scheduling
    scheduled_outreach_at = db.Column(db.DateTime, nullable=True)

    # Mid-session requirements change flag
    requirements_changed_mid_session = db.Column(db.Boolean, nullable=True)

    # Email threading
    last_message_id = db.Column(db.String(255), nullable=True)  # For In-Reply-To header
    thread_message_id = db.Column(db.String(255), nullable=True)  # First message in thread (for References header)

    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_svs_candidate_job_status', 'bullhorn_candidate_id', 'bullhorn_job_id', 'status'),
        db.Index('idx_svs_status_outreach', 'status', 'last_outreach_at'),
        db.Index('idx_svs_status_updated', 'status', 'updated_at'),
    )

    # Relationships
    vetting_log = db.relationship('CandidateVettingLog', backref=db.backref('scout_vetting_sessions', lazy='dynamic'))
    candidate_job_match = db.relationship('CandidateJobMatch', backref=db.backref('scout_vetting_session', uselist=False))
    conversation_turns = db.relationship('VettingConversationTurn', backref='session', lazy='dynamic',
                                          cascade='all, delete-orphan',
                                          order_by='VettingConversationTurn.turn_number')

    def __repr__(self):
        return f'<ScoutVettingSession {self.id} candidate={self.bullhorn_candidate_id} job={self.bullhorn_job_id} status={self.status}>'


class VettingConversationTurn(db.Model):
    """Individual email exchange in a Scout Vetting conversation.

    Stores both outbound (system→candidate) and inbound (candidate→system) messages,
    along with AI classification of intent and extracted answers.
    """
    __tablename__ = 'vetting_conversation_turn'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('scout_vetting_session.id'), nullable=False, index=True)
    turn_number = db.Column(db.Integer, nullable=False)
    direction = db.Column(db.String(10), nullable=False)  # 'outbound' or 'inbound'

    # Email content
    email_subject = db.Column(db.String(500), nullable=True)
    email_body = db.Column(db.Text, nullable=True)

    # AI analysis (for inbound messages)
    ai_intent = db.Column(db.String(50), nullable=True)  # answer, question, decline, unrelated, etc.
    ai_reasoning = db.Column(db.Text, nullable=True)
    questions_asked_json = db.Column(db.Text, nullable=True)  # Questions posed in this turn
    answers_extracted_json = db.Column(db.Text, nullable=True)  # Answers extracted from candidate reply

    # Email threading
    message_id = db.Column(db.String(255), nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<VettingConversationTurn session={self.session_id} turn={self.turn_number} dir={self.direction}>'


class VettingAuditLog(db.Model):
    """Tracks AI quality audit findings for Scout Screening results"""
    id = db.Column(db.Integer, primary_key=True)
    candidate_vetting_log_id = db.Column(db.Integer, nullable=False)
    bullhorn_candidate_id = db.Column(db.Integer, index=True, nullable=False)
    candidate_name = db.Column(db.String(255), nullable=True)
    job_id = db.Column(db.Integer, nullable=True)
    job_title = db.Column(db.String(500), nullable=True)
    original_score = db.Column(db.Float, nullable=True)
    audit_finding = db.Column(db.Text, nullable=True)
    finding_type = db.Column(db.String(50), nullable=False, default='no_issue')
    confidence = db.Column(db.String(20), nullable=True)
    action_taken = db.Column(db.String(50), nullable=False, default='no_action')
    revet_new_score = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            'candidate_vetting_log_id',
            name='uq_audit_log_vetting_id',
        ),
    )

    def __repr__(self):
        return f'<VettingAuditLog candidate={self.bullhorn_candidate_id} type={self.finding_type}>'


class RecruiterNotificationPref(db.Model):
    """Per-recruiter-per-job notification preference.

    Built May 2026 for the per-recruiter Location-Review opt-out toggle.
    Designed extensibly: `notification_type` lets us bolt on more toggle
    types (prestige, threshold, etc.) without a schema change. Default
    behavior when no row exists is ON for every notification_type — so
    the table only carries explicit OFF records, keeping it small.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    bullhorn_job_id = db.Column(db.Integer, nullable=False, index=True)
    notification_type = db.Column(db.String(64), nullable=False, default='location_review', index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True, server_default='true')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'bullhorn_job_id', 'notification_type',
            name='uq_recruiter_notif_pref',
        ),
    )

    def __repr__(self):
        return (f'<RecruiterNotificationPref user={self.user_id} '
                f'job={self.bullhorn_job_id} type={self.notification_type} '
                f'enabled={self.enabled}>')
