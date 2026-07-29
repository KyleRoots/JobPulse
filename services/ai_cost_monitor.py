"""Scheduled OpenAI spend monitoring with e-mail alerting.

Detection gap this closes (Jul 2026): ``AdminHealthService.tile_ai_cost_24h``
already computed an amber/red cost status, but it only ever rendered into an
HTTP response. Two runaway screening loops (an auditor re-vet that re-screened
one candidate every ~3 minutes, and a requirements delete/re-extract churn)
burned roughly $6.4k/month of avoidable spend for six days and notified nobody,
because every reader of ``openai_call_log`` required a human to load a page.

This module asks the same question on a schedule and routes the answer through
the SendGrid path the vetting health check already uses. It is deliberately
read-only with respect to screening: it observes spend, it never throttles or
blocks a call.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Sized against the corrected run rate (~$50-90/day at Jul 2026 applicant
# volume). Warn is roughly 2x a busy clean day so normal peaks stay quiet;
# critical is roughly 3x, which both Jul 2026 loops cleared for six days.
DEFAULT_WARN_USD_24H = 150.0
DEFAULT_CRITICAL_USD_24H = 250.0
DEFAULT_COOLDOWN_HOURS = 6.0

SEVERITY_NONE = 'none'
SEVERITY_WARNING = 'warning'
SEVERITY_CRITICAL = 'critical'

_SEVERITY_RANK = {SEVERITY_NONE: 0, SEVERITY_WARNING: 1, SEVERITY_CRITICAL: 2}

CONFIG_ENABLED = 'ai_cost_alert_enabled'
CONFIG_WARN_USD = 'ai_cost_alert_warn_usd_24h'
CONFIG_CRITICAL_USD = 'ai_cost_alert_critical_usd_24h'
CONFIG_COOLDOWN_HOURS = 'ai_cost_alert_cooldown_hours'
CONFIG_EMAIL = 'ai_cost_alert_email'

STATE_LAST_SENT_AT = 'ai_cost_alert_last_sent_at'
STATE_LAST_SEVERITY = 'ai_cost_alert_last_severity'

# A screened candidate is scored against ~10 jobs after the embedding
# pre-filter. Shown as context in the alert body because a spend spike driven
# by a loop looks very different from one driven by applicant volume.
HEALTHY_CALLS_PER_RUN = 10.0


# ── Pure decision logic (unit-tested without a database) ──────────────────────

def classify_severity(total_usd: float, warn_usd: float, critical_usd: float) -> str:
    """Map 24h spend onto a severity band."""
    if total_usd >= critical_usd:
        return SEVERITY_CRITICAL
    if total_usd >= warn_usd:
        return SEVERITY_WARNING
    return SEVERITY_NONE


def should_send_alert(
    severity: str,
    last_severity: Optional[str],
    last_sent_at: Optional[datetime],
    now: datetime,
    cooldown_hours: float,
) -> Tuple[bool, str]:
    """Decide whether to e-mail, and say why.

    Escalation from warning to critical bypasses the cooldown: the whole point
    of the critical band is that it should not wait out a quiet window.
    """
    if severity == SEVERITY_NONE:
        return False, 'spend below warning threshold'

    if last_sent_at is None:
        return True, 'no prior alert recorded'

    previous_rank = _SEVERITY_RANK.get(last_severity or SEVERITY_NONE, 0)
    if _SEVERITY_RANK[severity] > previous_rank:
        return True, f'severity escalated {last_severity or SEVERITY_NONE} -> {severity}'

    elapsed_hours = (now - last_sent_at).total_seconds() / 3600
    if elapsed_hours >= cooldown_hours:
        return True, f'cooldown elapsed ({elapsed_hours:.1f}h >= {cooldown_hours}h)'

    return False, (
        f'suppressed by cooldown ({elapsed_hours:.1f}h < {cooldown_hours}h '
        f'since last {last_severity or "unknown"} alert)'
    )


# ── Configuration helpers ─────────────────────────────────────────────────────

def _config_float(key: str, default: float) -> float:
    from models import VettingConfig
    raw = VettingConfig.get_value(key, None)
    if raw is None or str(raw).strip() == '':
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            f"AI cost alert: {key}={raw!r} is not numeric; using default {default}"
        )
        return default


def _config_bool(key: str, default: bool) -> bool:
    from models import VettingConfig
    raw = VettingConfig.get_value(key, None)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def _read_state() -> Tuple[Optional[str], Optional[datetime]]:
    from models import VettingConfig
    last_severity = VettingConfig.get_value(STATE_LAST_SEVERITY, None)
    raw_sent_at = VettingConfig.get_value(STATE_LAST_SENT_AT, None)
    last_sent_at = None
    if raw_sent_at:
        try:
            last_sent_at = datetime.fromisoformat(str(raw_sent_at))
        except (TypeError, ValueError):
            logger.warning(
                f"AI cost alert: could not parse {STATE_LAST_SENT_AT}={raw_sent_at!r}; "
                f"treating as no prior alert"
            )
    return last_severity, last_sent_at


# ── Data access ───────────────────────────────────────────────────────────────

def _fetch_spend_window(since: datetime) -> Dict:
    from extensions import db
    from sqlalchemy import text
    row = db.session.execute(
        text(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total, "
            "COUNT(*) AS calls "
            "FROM openai_call_log WHERE created_at >= :since"
        ),
        {'since': since},
    ).fetchone()
    return {
        'total_usd': float(row[0] or 0) if row else 0.0,
        'calls': int(row[1] or 0) if row else 0,
    }


def _fetch_top_sites(since: datetime, limit: int = 6) -> List[Dict]:
    from extensions import db
    from sqlalchemy import text
    rows = db.session.execute(
        text(
            "SELECT call_site_id, COUNT(*) AS calls, "
            "COALESCE(SUM(estimated_cost_usd), 0) AS cost "
            "FROM openai_call_log WHERE created_at >= :since "
            "GROUP BY call_site_id ORDER BY cost DESC LIMIT :limit"
        ),
        {'since': since, 'limit': limit},
    ).fetchall()
    return [
        {
            'site': r[0] or 'unknown',
            'calls': int(r[1] or 0),
            'cost': float(r[2] or 0),
        }
        for r in rows
    ]


def _fetch_calls_per_run(since: datetime) -> Optional[float]:
    """Scoring calls divided by screening runs — the loop fingerprint.

    Returns None when there is no screening activity to divide by, which is a
    legitimate state (quiet window) rather than an error.
    """
    from extensions import db
    from sqlalchemy import text
    row = db.session.execute(
        text(
            "SELECT "
            "(SELECT COUNT(*) FROM openai_call_log "
            " WHERE call_site_id = 'screening.scoring' AND created_at >= :since), "
            "(SELECT COUNT(*) FROM candidate_vetting_log WHERE created_at >= :since)"
        ),
        {'since': since},
    ).fetchone()
    if not row:
        return None
    scoring_calls = int(row[0] or 0)
    runs = int(row[1] or 0)
    if runs <= 0:
        return None
    return scoring_calls / runs


# ── Alert composition and delivery ────────────────────────────────────────────

def _build_alert_html(
    severity: str,
    total_usd: float,
    calls: int,
    warn_usd: float,
    critical_usd: float,
    top_sites: List[Dict],
    calls_per_run: Optional[float],
) -> str:
    banner_colour = '#dc3545' if severity == SEVERITY_CRITICAL else '#ffc107'
    threshold_crossed = critical_usd if severity == SEVERITY_CRITICAL else warn_usd

    site_rows = ''.join(
        f'<tr><td style="padding:4px 12px 4px 0;">{s["site"]}</td>'
        f'<td style="padding:4px 12px 4px 0; text-align:right;">{s["calls"]:,}</td>'
        f'<td style="padding:4px 0; text-align:right;">${s["cost"]:,.2f}</td></tr>'
        for s in top_sites
    )

    if calls_per_run is None:
        ratio_block = (
            '<p>No screening runs in the window, so the calls-per-run ratio is '
            'not meaningful. Spend is coming from somewhere other than candidate '
            'screening.</p>'
        )
    elif calls_per_run > HEALTHY_CALLS_PER_RUN * 2:
        ratio_block = (
            f'<div style="background:#f8d7da; border-left:4px solid #dc3545; padding:10px; margin:10px 0;">'
            f'<strong>Loop signature detected.</strong> Scoring calls per screening run is '
            f'<strong>{calls_per_run:.1f}</strong> against a healthy baseline of about '
            f'{HEALTHY_CALLS_PER_RUN:.0f}. That pattern means work is being paid for and '
            f'discarded, which is a different problem from high applicant volume.</div>'
        )
    else:
        ratio_block = (
            f'<div style="background:#d4edda; border-left:4px solid #28a745; padding:10px; margin:10px 0;">'
            f'Scoring calls per screening run is <strong>{calls_per_run:.1f}</strong>, close to the '
            f'healthy baseline of about {HEALTHY_CALLS_PER_RUN:.0f}. The spend looks driven by '
            f'applicant volume rather than a runaway loop.</div>'
        )

    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; color:#333;">
        <h2 style="color:{banner_colour};">{severity.upper()} — OpenAI spend alert</h2>
        <p>Estimated OpenAI spend over the last 24 hours has crossed the
        {severity} threshold of <strong>${threshold_crossed:,.2f}</strong>.</p>

        <div style="background:#fff3cd; border-left:4px solid {banner_colour}; padding:15px; margin:15px 0;">
            <strong>Last 24 hours:</strong> ${total_usd:,.2f} across {calls:,} API call(s)<br>
            <strong>Warning threshold:</strong> ${warn_usd:,.2f} &nbsp;·&nbsp;
            <strong>Critical threshold:</strong> ${critical_usd:,.2f}
        </div>

        {ratio_block}

        <p><strong>Top call sites (last 24h):</strong></p>
        <table style="border-collapse:collapse; font-size:14px;">
            <tr style="border-bottom:1px solid #ddd;">
                <th style="text-align:left; padding:4px 12px 4px 0;">Call site</th>
                <th style="text-align:right; padding:4px 12px 4px 0;">Calls</th>
                <th style="text-align:right; padding:4px 0;">Cost</th>
            </tr>
            {site_rows}
        </table>

        <p style="color:#666; font-size:12px; margin-top:20px;">
            Automated alert from Scout Screening cost monitoring. Repeat alerts are
            suppressed by a cooldown; an escalation from warning to critical always
            sends immediately. Open the
            <a href="https://app.scoutgenius.ai/admin/ai-cost">AI cost dashboard</a>
            for the full per-site breakdown.
        </p>
    </body>
    </html>
    """


def _send_alert_email(recipient: str, subject: str, html_content: str) -> bool:
    api_key = os.environ.get('SENDGRID_API_KEY')
    if not api_key:
        logger.warning("AI cost alert: SENDGRID_API_KEY not configured; cannot send")
        return False
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email='noreply@myticas.com',
            to_emails=recipient,
            subject=subject,
            html_content=html_content,
        )
        response = sendgrid.SendGridAPIClient(api_key=api_key).send(message)
        if response.status_code in (200, 202):
            return True
        logger.warning(
            f"AI cost alert: SendGrid returned {response.status_code}"
        )
        return False
    except Exception as e:
        logger.error(f"AI cost alert: send failed ({type(e).__name__}: {e})")
        return False


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_ai_cost_alert_check(now: Optional[datetime] = None) -> Dict:
    """Evaluate 24h OpenAI spend and e-mail when it crosses a threshold.

    Fail-soft by contract: every failure path returns a result dict instead of
    raising, so a monitoring problem can never take down the scheduler or
    interfere with screening.
    """
    from models import VettingConfig

    now = now or datetime.utcnow()
    result = {
        'evaluated': False,
        'severity': SEVERITY_NONE,
        'total_usd': 0.0,
        'alert_sent': False,
        'reason': '',
    }

    try:
        if not _config_bool(CONFIG_ENABLED, True):
            result['reason'] = 'disabled via ai_cost_alert_enabled'
            return result

        warn_usd = _config_float(CONFIG_WARN_USD, DEFAULT_WARN_USD_24H)
        critical_usd = _config_float(CONFIG_CRITICAL_USD, DEFAULT_CRITICAL_USD_24H)
        cooldown_hours = _config_float(CONFIG_COOLDOWN_HOURS, DEFAULT_COOLDOWN_HOURS)

        # A critical threshold below the warning one would make the warning
        # band unreachable. Trust the larger of the two as critical.
        if critical_usd < warn_usd:
            logger.warning(
                f"AI cost alert: critical (${critical_usd}) below warning "
                f"(${warn_usd}); swapping so the bands stay ordered"
            )
            warn_usd, critical_usd = critical_usd, warn_usd

        since = now - timedelta(hours=24)
        window = _fetch_spend_window(since)
        total_usd = window['total_usd']
        severity = classify_severity(total_usd, warn_usd, critical_usd)

        result.update({
            'evaluated': True,
            'severity': severity,
            'total_usd': total_usd,
            'calls': window['calls'],
        })

        last_severity, last_sent_at = _read_state()

        if severity == SEVERITY_NONE:
            # Recovery: drop the stamp so the next genuine spike alerts
            # immediately instead of waiting out a stale cooldown.
            if last_severity and last_severity != SEVERITY_NONE:
                VettingConfig.set_value(STATE_LAST_SEVERITY, SEVERITY_NONE)
                logger.info(
                    f"✅ AI cost alert: spend recovered to ${total_usd:,.2f}/24h "
                    f"(warn ${warn_usd:,.2f}) — cleared {last_severity} state"
                )
            result['reason'] = 'spend below warning threshold'
            return result

        send, reason = should_send_alert(
            severity, last_severity, last_sent_at, now, cooldown_hours
        )
        result['reason'] = reason

        if not send:
            logger.info(
                f"AI cost alert: {severity} at ${total_usd:,.2f}/24h — {reason}"
            )
            return result

        recipient = (
            VettingConfig.get_value(CONFIG_EMAIL, '')
            or VettingConfig.get_value('health_alert_email', '')
        )
        if not recipient:
            logger.warning(
                f"AI cost alert: {severity} at ${total_usd:,.2f}/24h but no "
                f"recipient configured ({CONFIG_EMAIL} / health_alert_email)"
            )
            result['reason'] = 'no recipient configured'
            return result

        top_sites = _fetch_top_sites(since)
        calls_per_run = _fetch_calls_per_run(since)

        html = _build_alert_html(
            severity, total_usd, window['calls'],
            warn_usd, critical_usd, top_sites, calls_per_run,
        )
        subject = (
            f"{severity.upper()} OpenAI spend alert — "
            f"${total_usd:,.2f} in 24h"
        )

        if _send_alert_email(recipient, subject, html):
            VettingConfig.set_value(STATE_LAST_SENT_AT, now.isoformat())
            VettingConfig.set_value(STATE_LAST_SEVERITY, severity)
            result['alert_sent'] = True
            logger.warning(
                f"💸 AI cost alert sent to {recipient}: {severity} — "
                f"${total_usd:,.2f} over 24h ({reason})"
            )
        else:
            # Deliberately do not stamp state on a failed send, so the next
            # cycle retries rather than silently swallowing the incident.
            result['reason'] = 'send failed'

        return result

    except Exception as e:
        logger.error(
            f"AI cost alert check failed ({type(e).__name__}: {e})",
            exc_info=True,
        )
        result['reason'] = f'error: {type(e).__name__}'
        return result
