"""Tell Kyle when Zip Apply deliveries spike on Qualified.

More than 10 unmatched jobs, or more than 100 deliveries of any kind, in
the last hour sends one email. A second spike inside that hour does not
send again. Myticas and STSI do not create candidates from this webhook,
so they do not alert.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

UNMATCHED_LIMIT = 10
TOTAL_LIMIT = 100
COOLDOWN_KEY = "zip_apply_volume_alert_at"
_FALLBACK_RECIPIENT = "kroots@myticas.com"


def maybe_alert_zip_volume(now: Optional[datetime] = None) -> str:
    """Return sent, cooled, ok, skipped, or no_recipient. Does not raise."""
    from zip_apply.ingest import webhook_creates_candidates

    if not webhook_creates_candidates():
        return "skipped"
    moment = now or datetime.utcnow()
    since = moment - timedelta(hours=1)
    unmatched, total = _counts_since(since)
    if unmatched <= UNMATCHED_LIMIT and total <= TOTAL_LIMIT:
        return "ok"
    if _cooled_down(moment):
        return "cooled"
    recipient = alert_recipient()
    if not recipient:
        return "no_recipient"
    reason = []
    if unmatched > UNMATCHED_LIMIT:
        reason.append(f"{unmatched} unmatched")
    if total > TOTAL_LIMIT:
        reason.append(f"{total} deliveries")
    subject = "ZipRecruiter apply spike on Qualified"
    html = (
        "<p>Qualified ZipRecruiter Apply crossed the hourly limit "
        f"({', '.join(reason)} in the last hour).</p>"
        "<p>Unmatched applicants are listed in Scout at "
        '<a href="https://qualified.scoutgenius.ai/scout-inbound/zip-unmatched">'
        "Unmatched Zip applicants</a>. Nothing was attached to a job "
        "automatically from this alert.</p>"
    )
    try:
        deliver_zip_volume_alert(recipient, subject, html)
    except Exception:
        logger.exception("zip_apply volume alert failed")
        return "no_recipient"
    _remember_alert(moment)
    logger.info(
        "zip_apply volume alert sent unmatched=%s total=%s",
        unmatched,
        total,
    )
    return "sent"


def alert_recipient() -> str:
    from models.scheduling import GlobalSettings
    from models.vetting import VettingConfig

    for key_source in (
        lambda: VettingConfig.get_value("health_alert_email", ""),
        lambda: GlobalSettings.get_value("alert_email", ""),
    ):
        try:
            value = (key_source() or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return _FALLBACK_RECIPIENT


def deliver_zip_volume_alert(to_email: str, subject: str, html: str) -> None:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    message = Mail(
        from_email="noreply@myticas.com",
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )
    SendGridAPIClient(api_key=os.environ.get("SENDGRID_API_KEY")).send(message)


def _counts_since(since: datetime):
    from models.zip_apply_webhook import ZipApplyDelivery

    total = ZipApplyDelivery.query.filter(ZipApplyDelivery.created_at >= since).count()
    unmatched = ZipApplyDelivery.query.filter(
        ZipApplyDelivery.created_at >= since,
        ZipApplyDelivery.ingest_status == "unmatched_job",
    ).count()
    return unmatched, total


def _cooled_down(moment: datetime) -> bool:
    from models.scheduling import GlobalSettings

    raw = GlobalSettings.get_value(COOLDOWN_KEY, "") or ""
    try:
        previous = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return moment - previous < timedelta(hours=1)


def _remember_alert(moment: datetime) -> None:
    from extensions import db
    from models.scheduling import GlobalSettings

    stamp = moment.isoformat(timespec="seconds")
    bind = db.session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        GlobalSettings.set_value(COOLDOWN_KEY, stamp, category="zip_apply")
        return
    row = (
        GlobalSettings.query.filter_by(setting_key=COOLDOWN_KEY)
        .order_by(GlobalSettings.id.desc())
        .first()
    )
    if row is None:
        db.session.add(GlobalSettings(
            setting_key=COOLDOWN_KEY,
            setting_value=stamp,
            category="zip_apply",
        ))
    else:
        row.setting_value = stamp
    db.session.commit()
