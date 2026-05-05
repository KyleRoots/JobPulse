"""Scout Prospector profile / run / prospect models."""
import json
from datetime import datetime
from extensions import db


class ProspectorProfile(db.Model):
    __tablename__ = 'prospector_profile'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    company = db.Column(db.String(100), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    industries = db.Column(db.Text, nullable=True)
    company_sizes = db.Column(db.Text, nullable=True)
    geographies = db.Column(db.Text, nullable=True)
    job_types = db.Column(db.Text, nullable=True)
    hiring_signals = db.Column(db.Text, nullable=True)
    additional_criteria = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('prospector_profiles', lazy='dynamic'))
    runs = db.relationship('ProspectorRun', backref='profile', lazy='dynamic', cascade='all, delete-orphan')

    def get_industries(self):
        if not self.industries:
            return []
        try:
            return json.loads(self.industries)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_industries(self, values):
        self.industries = json.dumps(values) if values else None

    def get_company_sizes(self):
        if not self.company_sizes:
            return []
        try:
            return json.loads(self.company_sizes)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_company_sizes(self, values):
        self.company_sizes = json.dumps(values) if values else None

    def get_geographies(self):
        if not self.geographies:
            return []
        try:
            return json.loads(self.geographies)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_geographies(self, values):
        self.geographies = json.dumps(values) if values else None

    def get_job_types(self):
        if not self.job_types:
            return []
        try:
            return json.loads(self.job_types)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_job_types(self, values):
        self.job_types = json.dumps(values) if values else None

    def get_hiring_signals(self):
        if not self.hiring_signals:
            return []
        try:
            return json.loads(self.hiring_signals)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_hiring_signals(self, values):
        self.hiring_signals = json.dumps(values) if values else None

    def __repr__(self):
        return f'<ProspectorProfile {self.id}: {self.name}>'


class ProspectorRun(db.Model):
    __tablename__ = 'prospector_run'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('prospector_profile.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    company = db.Column(db.String(100), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    prospects_found = db.Column(db.Integer, default=0)
    search_query_used = db.Column(db.Text, nullable=True)
    ai_summary = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('prospector_runs', lazy='dynamic'))
    prospects = db.relationship('Prospect', backref='run', lazy='dynamic', cascade='all, delete-orphan')

    __table_args__ = (
        db.Index('idx_prospector_run_status', 'status'),
        db.Index('idx_prospector_run_created', 'created_at'),
    )

    def __repr__(self):
        return f'<ProspectorRun {self.id}: {self.status}>'


class Prospect(db.Model):
    __tablename__ = 'prospect'
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('prospector_run.id'), nullable=False, index=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('prospector_profile.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    company = db.Column(db.String(100), nullable=False, index=True)
    company_name = db.Column(db.String(300), nullable=False)
    industry = db.Column(db.String(200), nullable=True)
    estimated_size = db.Column(db.String(100), nullable=True)
    location = db.Column(db.String(300), nullable=True)
    website = db.Column(db.String(500), nullable=True)
    key_contacts = db.Column(db.Text, nullable=True)
    hiring_activity = db.Column(db.Text, nullable=True)
    fit_reason = db.Column(db.Text, nullable=True)
    qualification_score = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), nullable=False, default='new')
    notes = db.Column(db.Text, nullable=True)
    source_urls = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('prospects', lazy='dynamic'))
    profile_ref = db.relationship('ProspectorProfile', backref=db.backref('prospects', lazy='dynamic'))

    __table_args__ = (
        db.Index('idx_prospect_status', 'status'),
        db.Index('idx_prospect_score', 'qualification_score'),
        db.Index('idx_prospect_company_name', 'company_name'),
    )

    VALID_STATUSES = ['new', 'hot', 'warm', 'cold', 'contacted', 'not_interested']

    def get_key_contacts(self):
        if not self.key_contacts:
            return []
        try:
            raw = json.loads(self.key_contacts)
            if not isinstance(raw, list):
                return []
            normalized = []
            for item in raw:
                if isinstance(item, dict):
                    normalized.append({
                        'name': item.get('name', ''),
                        'title': item.get('title', ''),
                        'linkedin': item.get('linkedin', ''),
                        'email': item.get('email', ''),
                        'phone': item.get('phone', ''),
                    })
                elif isinstance(item, str):
                    normalized.append({
                        'name': '',
                        'title': item,
                        'linkedin': '',
                        'email': '',
                        'phone': '',
                    })
            return normalized
        except (json.JSONDecodeError, TypeError):
            return []

    def get_source_urls(self):
        if not self.source_urls:
            return []
        try:
            return json.loads(self.source_urls)
        except (json.JSONDecodeError, TypeError):
            return []

    def __repr__(self):
        return f'<Prospect {self.id}: {self.company_name} ({self.status})>'
