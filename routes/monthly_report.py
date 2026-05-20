"""Admin routes for the monthly performance report confirmation flow.

  GET  /admin/monthly-report/preview/<token>   — confirmation form
  POST /admin/monthly-report/send/<token>      — finalize + send
  POST /admin/monthly-report/trigger           — dev/admin: generate preview NOW
"""
import json
import logging

from flask import Blueprint, render_template, request, flash, redirect, url_for, abort
from flask_login import login_required, current_user

from extensions import db
from models.reporting import MonthlyReportRun
from reports.monthly_report_service import (
    generate_and_send_preview,
    finalize_and_send,
)

logger = logging.getLogger(__name__)
monthly_report_bp = Blueprint('monthly_report', __name__, url_prefix='/admin/monthly-report')


def _admin_only():
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        abort(403)


@monthly_report_bp.route('/preview/<token>', methods=['GET'])
@login_required
def preview(token: str):
    _admin_only()
    run = MonthlyReportRun.query.filter_by(confirmation_token=token).first()
    if not run:
        abort(404)
    metrics = json.loads(run.metrics_snapshot_json or '{}')
    return render_template(
        'monthly_report_confirm.html',
        run=run,
        metrics=metrics,
        placement_source_label={
            'bullhorn_match': 'Auto-detected from Bullhorn Placement records.',
            'bullhorn_error': '⚠️ Bullhorn lookup failed — please enter manually.',
            'no_candidates': 'No Scout recommendations in this period.',
        }.get(run.placement_source, ''),
        already_sent=run.status in ('sent', 'auto_sent'),
    )


@monthly_report_bp.route('/send/<token>', methods=['POST'])
@login_required
def send(token: str):
    _admin_only()
    run = MonthlyReportRun.query.filter_by(confirmation_token=token).first()
    if not run:
        abort(404)
    if run.status in ('sent', 'auto_sent'):
        flash('Report already sent.', 'info')
        return redirect(url_for('monthly_report.preview', token=token))

    try:
        placements_final = int(request.form.get('placements', '0'))
        if placements_final < 0:
            raise ValueError('negative')
    except (TypeError, ValueError):
        flash('Placement count must be a non-negative integer.', 'danger')
        return redirect(url_for('monthly_report.preview', token=token))

    metrics = json.loads(run.metrics_snapshot_json or '{}')
    try:
        human_min = request.form.get('human_min_per_resume')
        if human_min not in (None, ''):
            metrics['human_min_per_resume'] = max(1, int(human_min))
        hourly = request.form.get('recruiter_hourly')
        if hourly not in (None, ''):
            metrics['recruiter_hourly'] = max(1, int(hourly))
        run.metrics_snapshot_json = json.dumps(metrics)
    except (TypeError, ValueError):
        flash('Baseline values must be positive integers.', 'danger')
        return redirect(url_for('monthly_report.preview', token=token))

    recipients_raw = (request.form.get('recipients', '') or '').strip()
    recipients = [e.strip() for e in recipients_raw.split(',') if e.strip()] if recipients_raw else None
    if recipients is not None:
        run.recipient_emails = ','.join(recipients)
    db.session.commit()

    ok = finalize_and_send(run, placements_final, recipient_overrides=recipients)
    if ok:
        flash(f'Report sent successfully to {", ".join(recipients or run.recipient_list())}.', 'success')
    else:
        flash('Report send failed. Check logs.', 'danger')
    return redirect(url_for('monthly_report.preview', token=token))


@monthly_report_bp.route('/trigger', methods=['POST'])
@login_required
def trigger_now():
    """Admin button — generate a preview for last month right now (for testing)."""
    _admin_only()
    try:
        run = generate_and_send_preview()
        if run:
            flash(f'Preview generated and emailed for {run.period_label} (id={run.id}).', 'success')
            return redirect(url_for('monthly_report.preview', token=run.confirmation_token))
        flash('Preview generation returned no run.', 'warning')
    except Exception as e:
        logger.error(f"Manual preview trigger failed: {e}", exc_info=True)
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('dashboard.root'))
