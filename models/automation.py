"""Automation Hub task / log / chat models."""
from datetime import datetime
from extensions import db


class AutomationTask(db.Model):
    __tablename__ = 'automation_task'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False, default='draft', index=True)
    automation_type = db.Column(db.String(100), nullable=True)
    schedule_cron = db.Column(db.String(100), nullable=True)
    config_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_run_at = db.Column(db.DateTime, nullable=True)
    next_run_at = db.Column(db.DateTime, nullable=True)
    run_count = db.Column(db.Integer, nullable=False, default=0)

    logs = db.relationship('AutomationLog', backref='task', lazy='dynamic', cascade='all, delete-orphan')
    chats = db.relationship('AutomationChat', backref='task', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<AutomationTask {self.id}: {self.name} ({self.status})>'


class AutomationLog(db.Model):
    __tablename__ = 'automation_log'
    id = db.Column(db.Integer, primary_key=True)
    automation_task_id = db.Column(db.Integer, db.ForeignKey('automation_task.id'), nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=True)
    details_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f'<AutomationLog {self.id}: task={self.automation_task_id} ({self.status})>'


class AutomationChat(db.Model):
    __tablename__ = 'automation_chat'
    id = db.Column(db.Integer, primary_key=True)
    automation_task_id = db.Column(db.Integer, db.ForeignKey('automation_task.id'), nullable=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<AutomationChat {self.id}: {self.role}>'
