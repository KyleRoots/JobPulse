"""Monthly performance report orchestration.

End-to-end pipeline:
  1. Compute the report period (the most-recently-completed calendar month).
  2. Pull live metrics from production tables (vetting logs, matches, parsed emails).
  3. Auto-propose a placement count by intersecting Scout recommendations with
     Bullhorn Placement records for the same period (Option C, "match-based").
  4. Render the PNG via reports.generate_perf_report.render_report().
  5. Email a preview to admin recipients with a one-click "Confirm & Send" link.
  6. On confirmation OR after a 48-hour timeout, send the final report.

The 48-hour auto-send fallback is enforced by a second scheduled job that runs
hourly and finalizes any 'preview_sent' row older than 48 hours.
"""
import base64
import json
import logging
import os
import secrets
import tempfile
from datetime import datetime, date, timedelta

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment, FileContent, FileName, FileType, Disposition

from extensions import db
from models.reporting import MonthlyReportRun
from reports.generate_perf_report import render_report

logger = logging.getLogger(__name__)

FROM_EMAIL = 'noreply@scoutgenius.ai'
PUBLIC_BASE_URL = os.environ.get('OAUTH_REDIRECT_BASE_URL', 'https://app.scoutgenius.ai').rstrip('/')
AUTO_SEND_AFTER_HOURS = 48


def _previous_month_bounds(today: date | None = None) -> tuple[date, date, str]:
    today = today or date.today()
    first_of_this = today.replace(day=1)
    last_of_prev = first_of_this - timedelta(days=1)
    first_of_prev = last_of_prev.replace(day=1)
    label = first_of_prev.strftime('%B %Y')
    return first_of_prev, last_of_prev, label


def _query_metrics(period_start: date, period_end: date) -> dict:
    """Pull the dashboard numbers straight from the production DB."""
    from sqlalchemy import text

    end_inclusive = datetime.combine(period_end, datetime.max.time())
    start_dt = datetime.combine(period_start, datetime.min.time())

    row = db.session.execute(text("""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE status='completed') AS completed,
          COUNT(*) FILTER (WHERE is_qualified=true) AS qualified
        FROM candidate_vetting_log
        WHERE created_at >= :s AND created_at <= :e
          AND is_sandbox = false
    """), {'s': start_dt, 'e': end_inclusive}).first()
    resumes_total, resumes_completed, qualified = int(row.total or 0), int(row.completed or 0), int(row.qualified or 0)

    row = db.session.execute(text("""
        SELECT
          ROUND(AVG(EXTRACT(EPOCH FROM (analyzed_at - created_at)))::numeric, 1) AS avg_sec,
          ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (analyzed_at - created_at)))::numeric, 1) AS median_sec,
          ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (analyzed_at - created_at)))::numeric, 1) AS p95_sec
        FROM candidate_vetting_log
        WHERE created_at >= :s AND created_at <= :e
          AND analyzed_at IS NOT NULL
          AND is_sandbox = false
          AND EXTRACT(EPOCH FROM (analyzed_at - created_at)) BETWEEN 1 AND 600
    """), {'s': start_dt, 'e': end_inclusive}).first()
    avg_sec = float(row.avg_sec or 0)
    median_sec = float(row.median_sec or 0)
    p95_sec = float(row.p95_sec or 0)

    row = db.session.execute(text("""
        SELECT
          COUNT(*) FILTER (WHERE notification_sent=true) AS notifications,
          COUNT(DISTINCT vetting_log_id) FILTER (WHERE notification_sent=true) AS unique_recommended
        FROM candidate_job_match
        WHERE created_at >= :s AND created_at <= :e
    """), {'s': start_dt, 'e': end_inclusive}).first()
    notifications = int(row.notifications or 0)
    recommended_unique = int(row.unique_recommended or 0)

    row = db.session.execute(text("""
        SELECT
          COUNT(*) AS total_inbound,
          COUNT(*) FILTER (WHERE bullhorn_candidate_id IS NOT NULL) AS landed
        FROM parsed_email
        WHERE created_at >= :s AND created_at <= :e
    """), {'s': start_dt, 'e': end_inclusive}).first()
    inbound_parsed = int(row.total_inbound or 0)
    inbound_landed = int(row.landed or 0)

    rows = db.session.execute(text("""
        SELECT TO_CHAR(date_trunc('month', created_at), 'YYYY-MM') AS m,
               COUNT(*) AS screened,
               COUNT(*) FILTER (WHERE is_qualified=true) AS qual
        FROM candidate_vetting_log
        WHERE created_at >= :s AND created_at <= :e
          AND is_sandbox = false
        GROUP BY 1 ORDER BY 1
    """), {'s': start_dt, 'e': end_inclusive}).all()
    monthly_breakdown = [
        {
            'label': datetime.strptime(r.m, '%Y-%m').strftime('%B %Y'),
            'screened': int(r.screened),
            'qualified': int(r.qual),
        }
        for r in rows
    ]
    if not monthly_breakdown:
        monthly_breakdown = [{'label': period_start.strftime('%B %Y'), 'screened': 0, 'qualified': 0}]

    return {
        'period_label': period_start.strftime('%B %Y'),
        'resumes_total': resumes_total,
        'resumes_completed': resumes_completed,
        'qualified': qualified,
        'recommended_unique': recommended_unique,
        'notifications': notifications,
        'inbound_parsed': inbound_parsed,
        'inbound_landed': inbound_landed,
        'avg_sec': avg_sec,
        'median_sec': median_sec,
        'p95_sec': p95_sec,
        'monthly_breakdown': monthly_breakdown,
        # Recruiter-productivity baselines used for the savings tiles. These are
        # industry assumptions (NOT measured), persisted into the snapshot so the
        # admin can see and override them before the final report ships.
        'human_min_per_resume': 15,
        'recruiter_hourly': 50,
    }


def auto_propose_placements(period_start: date, period_end: date) -> tuple[int, str]:
    """Cross-reference Scout-recommended candidates against Bullhorn Placement records.

    Returns (count, source) where source is one of:
      - 'bullhorn_match'  — successful Bullhorn API lookup
      - 'bullhorn_error'  — API failed; count is 0 and recruiter should override
      - 'no_candidates'   — no Scout recommendations in the period; count is 0
    """
    from sqlalchemy import text

    end_inclusive = datetime.combine(period_end, datetime.max.time())
    start_dt = datetime.combine(period_start, datetime.min.time())

    rows = db.session.execute(text("""
        SELECT DISTINCT cvl.bullhorn_candidate_id
        FROM candidate_job_match cjm
        JOIN candidate_vetting_log cvl ON cvl.id = cjm.vetting_log_id
        WHERE cjm.notification_sent = true
          AND cjm.created_at >= :s AND cjm.created_at <= :e
          AND cvl.bullhorn_candidate_id IS NOT NULL
    """), {'s': start_dt, 'e': end_inclusive}).all()

    candidate_ids = [int(r.bullhorn_candidate_id) for r in rows]
    if not candidate_ids:
        return 0, 'no_candidates'

    try:
        from bullhorn_service import BullhornService
        bh = BullhornService()
        if not bh.authenticate():
            logger.warning("auto_propose_placements: Bullhorn auth failed")
            return 0, 'bullhorn_error'

        period_start_ms = int(start_dt.timestamp() * 1000)
        period_end_ms = int(end_inclusive.timestamp() * 1000)

        placed_set = set()
        BATCH = 50
        for i in range(0, len(candidate_ids), BATCH):
            batch = candidate_ids[i:i + BATCH]
            id_list = ','.join(str(c) for c in batch)
            where = (
                f"candidate.id IN ({id_list}) "
                f"AND dateBegin >= {period_start_ms} "
                f"AND dateBegin <= {period_end_ms}"
            )
            placements = bh.query_entity(
                'Placement', where,
                fields='id,candidate(id),jobOrder(id),dateBegin',
                count=500,
            )
            for p in (placements or []):
                cand = (p.get('candidate') or {}).get('id')
                if cand:
                    placed_set.add(int(cand))

        return len(placed_set), 'bullhorn_match'

    except Exception as e:
        logger.error(f"auto_propose_placements failed: {e}", exc_info=True)
        return 0, 'bullhorn_error'


def _admin_recipient_emails() -> list[str]:
    """Default recipient list: all super-admin users with a valid email."""
    from models import User
    admins = User.query.filter(User.is_admin == True, User.email.isnot(None)).all()
    return [u.email for u in admins if u.email and '@' in u.email]


def _send_email(to_emails: list[str], subject: str, html_body: str,
                attachment_path: str | None = None,
                attachment_filename: str = 'scout_genius_report.png') -> bool:
    api_key = os.environ.get('SENDGRID_API_KEY')
    if not api_key:
        logger.error("SENDGRID_API_KEY not set; cannot send report email")
        return False
    if not to_emails:
        logger.warning("No recipients; skipping send")
        return False

    sg = SendGridAPIClient(api_key)
    message = Mail(
        from_email=Email(FROM_EMAIL, 'Scout Genius'),
        to_emails=[To(e) for e in to_emails],
        subject=subject,
        html_content=Content('text/html', html_body),
    )
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, 'rb') as f:
            data = base64.b64encode(f.read()).decode()
        att = Attachment(
            FileContent(data),
            FileName(attachment_filename),
            FileType('image/png'),
            Disposition('attachment'),
        )
        message.attachment = att

    try:
        resp = sg.send(message)
        ok = 200 <= resp.status_code < 300
        logger.info(f"SendGrid status {resp.status_code} for monthly report to {to_emails}")
        return ok
    except Exception as e:
        logger.error(f"SendGrid send failed: {e}", exc_info=True)
        return False


def _preview_email_html(run: MonthlyReportRun, metrics: dict) -> str:
    confirm_url = f"{PUBLIC_BASE_URL}/admin/monthly-report/preview/{run.confirmation_token}"
    placement_source_text = {
        'bullhorn_match': 'auto-detected from Bullhorn Placement records for candidates Scout recommended this period',
        'bullhorn_error': '⚠️ Bullhorn lookup failed — please verify manually',
        'no_candidates': 'no recommendations in this period — please verify manually',
    }.get(run.placement_source, '')

    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:640px;margin:0 auto;padding:24px">
      <h2 style="color:#0d3b66">Scout Genius — Confirm your {run.period_label} report</h2>
      <p>Your monthly performance report is ready for review. Before it ships,
         please confirm the placement count.</p>

      <div style="background:#f0f4f8;border-left:4px solid #58a6ff;padding:16px;border-radius:4px;margin:20px 0">
        <p style="margin:0 0 8px"><strong>Suggested placement count: {run.placements_auto_proposed}</strong></p>
        <p style="margin:0;font-size:13px;color:#555">{placement_source_text}</p>
      </div>

      <p><strong>Quick numbers from {run.period_label}:</strong></p>
      <ul>
        <li>{metrics['resumes_completed']:,} resumes screened</li>
        <li>{metrics['recommended_unique']:,} candidates recommended</li>
        <li>{metrics['inbound_parsed']:,} inbound applications parsed</li>
        <li>Avg screening time: {metrics['avg_sec']:.1f}s/resume</li>
      </ul>

      <p style="margin:32px 0">
        <a href="{confirm_url}" style="background:#0d3b66;color:#fff;padding:14px 28px;
           text-decoration:none;border-radius:6px;font-weight:bold;display:inline-block">
          Confirm &amp; Send Final Report
        </a>
      </p>

      <p style="font-size:12px;color:#888">If you don't take action within
         {AUTO_SEND_AFTER_HOURS} hours, the report will be sent automatically
         with the suggested placement count above.</p>
    </body></html>
    """


def _final_email_html(run: MonthlyReportRun, was_auto_sent: bool) -> str:
    auto_footer = (
        f'<p style="font-size:12px;color:#a83232;background:#fff3f3;padding:8px;border-radius:4px">'
        f'Note: placement count auto-proposed from Bullhorn match data — not manually confirmed.</p>'
        if was_auto_sent else ''
    )
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:640px;margin:0 auto;padding:24px">
      <h2 style="color:#0d3b66">Scout Genius — {run.period_label} Report</h2>
      <p>Attached is your monthly performance report covering <strong>{run.period_label}</strong>.</p>
      <p><strong>Confirmed placements:</strong> {run.placements_final}</p>
      {auto_footer}
      <p style="font-size:12px;color:#888">Generated by Scout Genius automation hub.</p>
    </body></html>
    """


def generate_and_send_preview() -> MonthlyReportRun | None:
    """Scheduled entrypoint — runs 1st of each month at 9 AM."""
    period_start, period_end, label = _previous_month_bounds()

    existing = MonthlyReportRun.query.filter_by(period_start=period_start).first()
    if existing and existing.status in ('sent', 'auto_sent', 'confirmed'):
        logger.info(f"Monthly report for {label} already finalized; skipping")
        return existing

    metrics = _query_metrics(period_start, period_end)
    auto_placements, source = auto_propose_placements(period_start, period_end)

    run = existing or MonthlyReportRun(
        period_start=period_start,
        period_end=period_end,
        period_label=label,
        confirmation_token=secrets.token_urlsafe(32),
        recipient_emails=','.join(_admin_recipient_emails()),
    )
    run.placements_auto_proposed = auto_placements
    run.placement_source = source
    run.metrics_snapshot_json = json.dumps(metrics)
    run.status = 'preview_sent'
    run.preview_sent_at = datetime.utcnow()

    if not existing:
        db.session.add(run)
    db.session.commit()

    recipients = run.recipient_list()
    if not recipients:
        logger.warning(f"No admin recipients for monthly report {run.id}; preview not sent")
        return run

    html = _preview_email_html(run, metrics)
    ok = _send_email(recipients, f"[Scout Genius] Confirm your {label} report", html)
    if not ok:
        run.status = 'failed'
        run.error_message = 'preview email send failed'
        db.session.commit()
    logger.info(f"Monthly report preview sent for {label} (run id={run.id}, ok={ok})")
    return run


def finalize_and_send(run: MonthlyReportRun, placements_final: int,
                      recipient_overrides: list[str] | None = None,
                      was_auto_sent: bool = False) -> bool:
    """Render the final PNG with the confirmed placement count and email it."""
    metrics = json.loads(run.metrics_snapshot_json or '{}')
    metrics['placements'] = int(placements_final)
    metrics['period_label'] = run.period_label

    run.placements_final = int(placements_final)
    if was_auto_sent:
        run.status = 'auto_sent'
        run.auto_sent_at = datetime.utcnow()
    else:
        run.status = 'confirmed'
        run.confirmed_at = datetime.utcnow()
    db.session.commit()

    recipients = recipient_overrides if recipient_overrides is not None else run.recipient_list()
    if not recipients:
        run.status = 'failed'
        run.error_message = 'no recipients for final send'
        db.session.commit()
        return False

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        out_path = tmp.name
    try:
        render_report(metrics, out_path)
        fname = f"scout_genius_report_{run.period_start.strftime('%Y_%m')}.png"
        html = _final_email_html(run, was_auto_sent)
        ok = _send_email(
            recipients,
            f"[Scout Genius] {run.period_label} Performance Report",
            html,
            attachment_path=out_path,
            attachment_filename=fname,
        )
        if ok and not was_auto_sent:
            run.status = 'sent'
            run.sent_at = datetime.utcnow()
        elif not ok:
            run.status = 'failed'
            run.error_message = 'final email send failed'
        db.session.commit()
        return ok
    finally:
        try:
            os.unlink(out_path)
        except Exception:
            pass


def sweep_auto_send_overdue() -> int:
    """Hourly sweep — auto-send any preview_sent rows older than 48h."""
    cutoff = datetime.utcnow() - timedelta(hours=AUTO_SEND_AFTER_HOURS)
    overdue = MonthlyReportRun.query.filter(
        MonthlyReportRun.status == 'preview_sent',
        MonthlyReportRun.preview_sent_at < cutoff,
    ).all()
    sent_count = 0
    for run in overdue:
        try:
            ok = finalize_and_send(
                run,
                placements_final=run.placements_auto_proposed or 0,
                was_auto_sent=True,
            )
            if ok:
                sent_count += 1
        except Exception as e:
            logger.error(f"Auto-send failed for run {run.id}: {e}", exc_info=True)
            run.status = 'failed'
            run.error_message = f'auto_send exception: {e}'
            db.session.commit()
    if sent_count:
        logger.info(f"Auto-sent {sent_count} overdue monthly report(s)")
    return sent_count
