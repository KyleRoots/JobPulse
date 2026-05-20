"""Monthly performance report scheduling, confirmation, and delivery state.

One row per scheduled monthly report run. Lifecycle:

  preview_sent  --(user clicks "Confirm & Send")-->  confirmed --> sent
       |
       +-------(48h elapsed with no action)-------------------->  auto_sent
       |
       +-------(any error along the way)-------------------------->  failed

Tokens are one-time-use URL-safe strings handed to the user in the preview
email; they map a click on the "Confirm & Send" button back to a specific
report row without requiring a fresh login.
"""
from datetime import datetime

from extensions import db


class MonthlyReportRun(db.Model):
    __tablename__ = 'monthly_report_run'

    id = db.Column(db.Integer, primary_key=True)

    period_start = db.Column(db.Date, nullable=False, index=True)
    period_end = db.Column(db.Date, nullable=False)
    period_label = db.Column(db.String(64), nullable=False)

    status = db.Column(db.String(20), nullable=False, default='preview_sent', index=True)
    confirmation_token = db.Column(db.String(64), unique=True, nullable=False, index=True)

    placements_auto_proposed = db.Column(db.Integer, nullable=True)
    placements_final = db.Column(db.Integer, nullable=True)
    placement_source = db.Column(db.String(20), nullable=True)

    recipient_emails = db.Column(db.Text, nullable=False, default='')

    metrics_snapshot_json = db.Column(db.Text, nullable=True)

    preview_sent_at = db.Column(db.DateTime, nullable=True)
    confirmed_at = db.Column(db.DateTime, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    auto_sent_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def recipient_list(self):
        if not self.recipient_emails:
            return []
        return [e.strip() for e in self.recipient_emails.split(',') if e.strip()]
