"""
Admin Backup Dashboard — view backup history and trigger on-demand backups.

Authorization: super-admin (current_user.is_admin) only.
"""

import logging
import threading
from datetime import datetime, timedelta

from flask import Blueprint, render_template, jsonify, abort, request
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

backup_bp = Blueprint("backup", __name__, url_prefix="/admin/backups")

STALE_RUNNING_TIMEOUT_MINUTES = 60


def _require_admin():
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)


def _expire_stale_running(db, BackupLog):
    cutoff = datetime.utcnow() - timedelta(minutes=STALE_RUNNING_TIMEOUT_MINUTES)
    stale = (
        BackupLog.query
        .filter_by(status="running")
        .filter(BackupLog.started_at < cutoff)
        .all()
    )
    for row in stale:
        row.status = "failed"
        row.error_message = f"Expired: still running after {STALE_RUNNING_TIMEOUT_MINUTES} minutes"
        row.completed_at = datetime.utcnow()
        logger.warning(f"Expired stale backup log id={row.id}")
    if stale:
        db.session.commit()


@backup_bp.route("/", methods=["GET"])
@login_required
def backup_dashboard():
    _require_admin()
    from extensions import db
    from models import BackupLog

    _expire_stale_running(db, BackupLog)

    logs = (
        BackupLog.query
        .order_by(BackupLog.started_at.desc())
        .limit(30)
        .all()
    )

    stats = {
        "total": BackupLog.query.count(),
        "success": BackupLog.query.filter_by(status="success").count(),
        "failed": BackupLog.query.filter_by(status="failed").count(),
        "last_success": (
            BackupLog.query
            .filter_by(status="success")
            .order_by(BackupLog.started_at.desc())
            .first()
        ),
    }

    return render_template("admin_backup.html", logs=logs, stats=stats)


@backup_bp.route("/run-now", methods=["POST"])
@login_required
def run_backup_now():
    _require_admin()
    from extensions import db
    from models import BackupLog

    _expire_stale_running(db, BackupLog)

    running = BackupLog.query.filter_by(status="running").first()
    if running:
        return jsonify({
            "status": "error",
            "message": "A backup is already running. Please wait for it to finish.",
        }), 409

    sentinel = BackupLog(
        status="running",
        triggered_by="manual",
        started_at=datetime.utcnow(),
    )
    db.session.add(sentinel)
    db.session.commit()
    sentinel_id = sentinel.id

    def _run_in_background(app, log_id):
        with app.app_context():
            from backup_service import BackupService
            svc = BackupService(app=app)
            svc.run_backup(triggered_by="manual", existing_log_id=log_id)

    from flask import current_app
    app = current_app._get_current_object()
    t = threading.Thread(
        target=_run_in_background, args=(app, sentinel_id), daemon=True
    )
    t.start()

    return jsonify({
        "status": "ok",
        "message": "Backup started. Refresh the page in a few minutes to see the result.",
    })
