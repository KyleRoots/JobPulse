"""User auth + activity models."""
import json
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db

AVAILABLE_MODULES = ['scout_inbound', 'scout_screening', 'scout_vetting', 'scout_support', 'scout_prospector']


class User(UserMixin, db.Model):
    """User model for authentication"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_company_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), default='user')
    company = db.Column(db.String(100), default='Myticas Consulting')
    # Multi-tenant discriminator (Task #100): the Bullhorn environment this user
    # belongs to. Nullable → resolves to the default (Myticas) environment, so
    # existing users behave byte-for-byte as before. New customers' users carry
    # their own environment_id, which scopes the data they can see.
    environment_id = db.Column(
        db.Integer, db.ForeignKey('bullhorn_environment.id'),
        nullable=True, index=True,
    )
    bullhorn_user_id = db.Column(db.Integer, nullable=True)
    display_name = db.Column(db.String(255), nullable=True)
    subscribed_modules = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    last_active_at = db.Column(db.DateTime, nullable=True)
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False, server_default='0')
    locked_until = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_any_admin(self):
        """True if the user is a super-admin or a company admin."""
        return self.is_admin or self.is_company_admin

    @property
    def can_view_all_users(self):
        """True for super-admins and company admins — can see user accounts."""
        return self.is_admin or self.is_company_admin

    def get_visible_users(self):
        """Return users this admin can see. Super-admins see all; company admins see only their company."""
        if self.is_admin:
            return User.query.order_by(User.is_admin.desc(), User.username).all()
        if self.is_company_admin:
            return User.query.filter_by(company=self.company).order_by(User.is_admin.desc(), User.username).all()
        return []

    @property
    def effective_role(self):
        """Human-readable role label."""
        if self.is_admin:
            return 'super_admin'
        if self.is_company_admin:
            return 'company_admin'
        return 'user'

    def get_modules(self):
        """Return list of subscribed modules. Super-admins get all modules."""
        if self.is_admin:
            return AVAILABLE_MODULES[:]
        if not self.subscribed_modules:
            return []
        try:
            return json.loads(self.subscribed_modules)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_modules(self, modules):
        """Set subscribed modules from a list."""
        valid = [m for m in modules if m in AVAILABLE_MODULES]
        self.subscribed_modules = json.dumps(valid)

    def has_module(self, module_name):
        """Check if user has access to a specific module. Super-admins always have access."""
        if self.is_admin:
            return True
        return module_name in self.get_modules()

    def __repr__(self):
        return f'<User {self.username}>'


class UserActivityLog(db.Model):
    __tablename__ = 'user_activity_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    activity_type = db.Column(db.String(50), nullable=False, index=True)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship('User', backref=db.backref('activity_logs', lazy='dynamic'))

    def __repr__(self):
        return f'<UserActivityLog {self.activity_type} user={self.user_id}>'


class RecruiterMapping(db.Model):
    """Mapping of recruiter names to LinkedIn tags (#LI-XXX)"""
    id = db.Column(db.Integer, primary_key=True)
    recruiter_name = db.Column(db.String(255), nullable=False, unique=True)  # Full recruiter name
    linkedin_tag = db.Column(db.String(20), nullable=False)  # LinkedIn tag (e.g., #LI-RP1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<RecruiterMapping {self.recruiter_name}: {self.linkedin_tag}>'


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_token'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship('User', backref='reset_tokens')

    @property
    def is_valid(self):
        return not self.used and datetime.utcnow() < self.expires_at

    def __repr__(self):
        return f'<PasswordResetToken user_id={self.user_id} used={self.used}>'
