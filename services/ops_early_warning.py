"""Phase 1 ops early-warning: health signals + Kyle email.

Closes the gap where `/admin/health` and connectivity-only vetting checks stay
green during silent failures (completed ParsedEmail with NULL bullhorn IDs,
screening stall while APScheduler reports running, protected jobs missing
expected runs). Observe-only — never auto-heals, never rotates credentials,
never rewrites qualify notes. See `.agents/memory/ops-early-warning.md`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SEVERITY_NONE = 'none'
SEVERITY_WARNING = 'warning'
SEVERITY_CRITICAL = 'critical'

_SEVERITY_RANK = {SEVERITY_NONE: 0, SEVERITY_WARNING: 1, SEVERITY_CRITICAL: 2}

DEFAULT_COOLDOWN_HOURS = 6.0
DEFAULT_RECIPIENT = 'kroots@myticas.com'

# Inbound: healthy write rate ~88–94% → null rate ~6–12%. Cliff outages ≈ 0% writes.
DEFAULT_INBOUND_WINDOW_HOURS = 2.0
DEFAULT_INBOUND_MIN_COMPLETED = 5
DEFAULT_INBOUND_NULL_WARN = 0.40
DEFAULT_INBOUND_NULL_CRITICAL = 0.70

# Screening stall — aligned with admin_health_service thresholds.
DEFAULT_STALL_OLDEST_WARN_MIN = 30.0
DEFAULT_STALL_OLDEST_CRITICAL_MIN = 60.0
DEFAULT_STALL_FAILED_WARN = 25
DEFAULT_STALL_FAILED_CRITICAL = 50
DEFAULT_STALL_ZERO_PROGRESS_MIN = 20.0

# Protected jobs: expected interval minutes × miss counts.
PROTECTED_JOB_INTERVALS_MIN = {
    'process_bullhorn_monitors': 5,
    'candidate_vetting_cycle': 1,
    'vetting_health_check': 10,
}
DEFAULT_MISS_WARN = 3
DEFAULT_MISS_CRITICAL = 6

# SFTP freshness when uploads enabled (minutes).
DEFAULT_SFTP_WARN_MIN = 60.0
DEFAULT_SFTP_CRITICAL_MIN = 360.0

CONFIG_ENABLED = 'ops_early_warning_enabled'
CONFIG_COOLDOWN = 'ops_early_warning_cooldown_hours'
CONFIG_EMAIL = 'ops_early_warning_email'
CONFIG_INBOUND_WINDOW = 'ops_early_warning_inbound_window_hours'
CONFIG_INBOUND_MIN = 'ops_early_warning_inbound_min_completed'
CONFIG_INBOUND_WARN = 'ops_early_warning_inbound_null_warn'
CONFIG_INBOUND_CRITICAL = 'ops_early_warning_inbound_null_critical'
CONFIG_STALL_WARN_MIN = 'ops_early_warning_stall_oldest_warn_min'
CONFIG_STALL_CRIT_MIN = 'ops_early_warning_stall_oldest_critical_min'
CONFIG_STALL_FAIL_WARN = 'ops_early_warning_stall_failed_warn'
CONFIG_STALL_FAIL_CRIT = 'ops_early_warning_stall_failed_critical'
CONFIG_STALL_ZERO_MIN = 'ops_early_warning_stall_zero_progress_min'
CONFIG_MISS_WARN = 'ops_early_warning_miss_warn'
CONFIG_MISS_CRIT = 'ops_early_warning_miss_critical'
CONFIG_SFTP_WARN = 'ops_early_warning_sftp_warn_min'
CONFIG_SFTP_CRIT = 'ops_early_warning_sftp_critical_min'

STATE_LAST_SENT_AT = 'ops_early_warning_last_sent_at'
STATE_LAST_SEVERITY = 'ops_early_warning_last_severity'
STATE_LAST_FINGERPRINT = 'ops_early_warning_last_fingerprint'
STATE_LAST_SUMMARY = 'ops_early_warning_last_summary'


@dataclass
class HealthSignal:
    """One evaluated ops signal."""
    key: str
    severity: str
    title: str
    detail: str
    metrics: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Pure decision helpers (unit-tested without DB) ────────────────────────────

def max_severity(severities: List[str]) -> str:
    best = SEVERITY_NONE
    for s in severities:
        if _SEVERITY_RANK.get(s, 0) > _SEVERITY_RANK[best]:
            best = s
    return best


def build_fingerprint(signals: List[HealthSignal]) -> str:
    """Stable identity of the active warn/critical set.

    Changing which signals fire (or their severities) yields a new fingerprint
    so a different incident is not suppressed by the previous cooldown.
    """
    parts = sorted(
        f'{s.key}:{s.severity}'
        for s in signals
        if s.severity in (SEVERITY_WARNING, SEVERITY_CRITICAL)
    )
    if not parts:
        return 'none'
    raw = '|'.join(parts)
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
    return f'{digest}:{raw}'


def classify_inbound_null_rate(
    null_rate: float,
    completed: int,
    *,
    min_completed: int,
    warn_rate: float,
    critical_rate: float,
) -> str:
    if completed < min_completed:
        return SEVERITY_NONE
    if null_rate >= critical_rate:
        return SEVERITY_CRITICAL
    if null_rate >= warn_rate:
        return SEVERITY_WARNING
    return SEVERITY_NONE


def classify_age_minutes(
    age_min: Optional[float],
    warn_min: float,
    critical_min: float,
) -> str:
    if age_min is None:
        return SEVERITY_NONE
    if age_min >= critical_min:
        return SEVERITY_CRITICAL
    if age_min >= warn_min:
        return SEVERITY_WARNING
    return SEVERITY_NONE


def classify_count(count: int, warn: int, critical: int) -> str:
    if count >= critical:
        return SEVERITY_CRITICAL
    if count >= warn:
        return SEVERITY_WARNING
    return SEVERITY_NONE


def classify_missed_runs(
    age_min: Optional[float],
    interval_min: float,
    miss_warn: int,
    miss_critical: int,
) -> str:
    if age_min is None or interval_min <= 0:
        return SEVERITY_NONE
    misses = age_min / interval_min
    if misses >= miss_critical:
        return SEVERITY_CRITICAL
    if misses >= miss_warn:
        return SEVERITY_WARNING
    return SEVERITY_NONE


def should_send_alert(
    severity: str,
    fingerprint: str,
    last_severity: Optional[str],
    last_fingerprint: Optional[str],
    last_sent_at: Optional[datetime],
    now: datetime,
    cooldown_hours: float,
) -> Tuple[bool, str]:
    """Fingerprint + cooldown gate (escalation always breaks through)."""
    if severity == SEVERITY_NONE:
        return False, 'no warn/critical signals'

    if last_sent_at is None:
        return True, 'no prior alert recorded'

    previous_rank = _SEVERITY_RANK.get(last_severity or SEVERITY_NONE, 0)
    if _SEVERITY_RANK[severity] > previous_rank:
        return True, (
            f'severity escalated {last_severity or SEVERITY_NONE} -> {severity}'
        )

    if fingerprint and fingerprint != (last_fingerprint or ''):
        return True, 'fingerprint changed (new or different signal set)'

    elapsed_hours = (now - last_sent_at).total_seconds() / 3600
    if elapsed_hours >= cooldown_hours:
        return True, f'cooldown elapsed ({elapsed_hours:.1f}h >= {cooldown_hours}h)'

    return False, (
        f'suppressed by fingerprint cooldown ({elapsed_hours:.1f}h < {cooldown_hours}h '
        f'since last {last_severity or "unknown"} / {last_fingerprint or "none"})'
    )


# ── Config / state ────────────────────────────────────────────────────────────

def _config_float(key: str, default: float) -> float:
    from models import VettingConfig
    raw = VettingConfig.get_value(key, None)
    if raw is None or str(raw).strip() == '':
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            'ops early-warning: %s=%r not numeric; using default %s', key, raw, default
        )
        return default


def _config_int(key: str, default: int) -> int:
    return int(_config_float(key, float(default)))


def _config_bool(key: str, default: bool) -> bool:
    from models import VettingConfig
    raw = VettingConfig.get_value(key, None)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def _read_state() -> Tuple[Optional[str], Optional[str], Optional[datetime]]:
    from models import VettingConfig
    last_severity = VettingConfig.get_value(STATE_LAST_SEVERITY, None)
    last_fingerprint = VettingConfig.get_value(STATE_LAST_FINGERPRINT, None)
    raw_sent_at = VettingConfig.get_value(STATE_LAST_SENT_AT, None)
    last_sent_at = None
    if raw_sent_at:
        try:
            last_sent_at = datetime.fromisoformat(str(raw_sent_at))
        except (TypeError, ValueError):
            logger.warning(
                'ops early-warning: could not parse %s=%r',
                STATE_LAST_SENT_AT, raw_sent_at,
            )
    return last_severity, last_fingerprint, last_sent_at


def resolve_recipient() -> str:
    from models import VettingConfig
    env = (os.environ.get('OPS_EARLY_WARNING_EMAIL') or '').strip()
    if env:
        return env
    cfg = (VettingConfig.get_value(CONFIG_EMAIL, '') or '').strip()
    if cfg:
        return cfg
    health = (VettingConfig.get_value('health_alert_email', '') or '').strip()
    if health:
        return health
    return DEFAULT_RECIPIENT


# ── Signal collectors ─────────────────────────────────────────────────────────

def _collect_inbound_null_bh(now: datetime) -> HealthSignal:
    from models import ParsedEmail
    from sqlalchemy import and_

    window_h = _config_float(CONFIG_INBOUND_WINDOW, DEFAULT_INBOUND_WINDOW_HOURS)
    min_completed = _config_int(CONFIG_INBOUND_MIN, DEFAULT_INBOUND_MIN_COMPLETED)
    warn_rate = _config_float(CONFIG_INBOUND_WARN, DEFAULT_INBOUND_NULL_WARN)
    critical_rate = _config_float(CONFIG_INBOUND_CRITICAL, DEFAULT_INBOUND_NULL_CRITICAL)
    if critical_rate < warn_rate:
        warn_rate, critical_rate = critical_rate, warn_rate

    since = now - timedelta(hours=window_h)
    base = ParsedEmail.query.filter(
        and_(
            ParsedEmail.status == 'completed',
            ParsedEmail.received_at >= since,
        )
    )
    completed = base.count()
    null_bh = base.filter(ParsedEmail.bullhorn_candidate_id.is_(None)).count()
    null_rate = (null_bh / completed) if completed else 0.0
    write_rate = 1.0 - null_rate if completed else None

    severity = classify_inbound_null_rate(
        null_rate, completed,
        min_completed=min_completed,
        warn_rate=warn_rate,
        critical_rate=critical_rate,
    )
    detail = (
        f'{null_bh}/{completed} completed ParsedEmail rows in last {window_h:g}h '
        f'have NULL bullhorn_candidate_id (null_rate={null_rate:.0%}'
        + (f', write_rate={write_rate:.0%}' if write_rate is not None else '')
        + '). Healthy baseline write rate ~88–94%.'
    )
    if completed < min_completed:
        detail += f' Below min sample ({min_completed}); signal held.'

    return HealthSignal(
        key='inbound_null_bh',
        severity=severity,
        title='Inbound → Bullhorn write rate',
        detail=detail,
        metrics={
            'completed': completed,
            'null_bh': null_bh,
            'null_rate': round(null_rate, 4),
            'write_rate': round(write_rate, 4) if write_rate is not None else None,
            'window_hours': window_h,
        },
    )


def _collect_screening_stall(now: datetime) -> HealthSignal:
    from models import CandidateVettingLog

    oldest_warn = _config_float(CONFIG_STALL_WARN_MIN, DEFAULT_STALL_OLDEST_WARN_MIN)
    oldest_crit = _config_float(CONFIG_STALL_CRIT_MIN, DEFAULT_STALL_OLDEST_CRITICAL_MIN)
    fail_warn = _config_int(CONFIG_STALL_FAIL_WARN, DEFAULT_STALL_FAILED_WARN)
    fail_crit = _config_int(CONFIG_STALL_FAIL_CRIT, DEFAULT_STALL_FAILED_CRITICAL)
    zero_progress_min = _config_float(CONFIG_STALL_ZERO_MIN, DEFAULT_STALL_ZERO_PROGRESS_MIN)
    if oldest_crit < oldest_warn:
        oldest_warn, oldest_crit = oldest_crit, oldest_warn
    if fail_crit < fail_warn:
        fail_warn, fail_crit = fail_crit, fail_warn

    vetting_on = _config_bool('vetting_enabled', True)

    inflight_q = CandidateVettingLog.query.filter(
        CandidateVettingLog.status.in_(['pending', 'processing'])
    )
    inflight = inflight_q.count()
    oldest = inflight_q.order_by(CandidateVettingLog.created_at.asc()).first()
    oldest_age = None
    if oldest and oldest.created_at:
        oldest_age = (now - oldest.created_at).total_seconds() / 60.0

    fail_cutoff = now - timedelta(hours=24)
    failed_24h = CandidateVettingLog.query.filter(
        CandidateVettingLog.status == 'failed',
        CandidateVettingLog.created_at >= fail_cutoff,
    ).count()

    progress_cutoff = now - timedelta(minutes=zero_progress_min)
    completed_recent = CandidateVettingLog.query.filter(
        CandidateVettingLog.status == 'completed',
        CandidateVettingLog.created_at >= progress_cutoff,
    ).count()

    age_sev = (
        classify_age_minutes(oldest_age, oldest_warn, oldest_crit)
        if inflight else SEVERITY_NONE
    )
    fail_sev = classify_count(failed_24h, fail_warn, fail_crit)

    zero_sev = SEVERITY_NONE
    if (
        vetting_on
        and inflight > 0
        and completed_recent == 0
        and oldest_age is not None
        and oldest_age >= zero_progress_min
    ):
        zero_sev = (
            SEVERITY_CRITICAL
            if oldest_age >= oldest_crit
            else SEVERITY_WARNING
        )

    severity = max_severity([age_sev, fail_sev, zero_sev])
    parts = [
        f'inflight={inflight}',
        (
            f'oldest_age_min={oldest_age:.1f}'
            if oldest_age is not None else 'oldest_age_min=n/a'
        ),
        f'failed_24h={failed_24h}',
        f'completed_last_{zero_progress_min:g}m={completed_recent}',
        f'vetting_enabled={vetting_on}',
    ]
    return HealthSignal(
        key='screening_stall',
        severity=severity,
        title='Screening stall',
        detail='; '.join(parts),
        metrics={
            'inflight': inflight,
            'oldest_age_min': round(oldest_age, 2) if oldest_age is not None else None,
            'failed_24h': failed_24h,
            'completed_recent': completed_recent,
            'vetting_enabled': vetting_on,
            'age_severity': age_sev,
            'failed_severity': fail_sev,
            'zero_progress_severity': zero_sev,
        },
    )


def _parse_last_run_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        ts = data.get('timestamp') if isinstance(data, dict) else None
        if not ts:
            return None
        return datetime.fromisoformat(str(ts).replace('Z', ''))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _collect_scheduler_misses(now: datetime) -> HealthSignal:
    from models import GlobalSettings

    miss_warn = _config_int(CONFIG_MISS_WARN, DEFAULT_MISS_WARN)
    miss_crit = _config_int(CONFIG_MISS_CRIT, DEFAULT_MISS_CRITICAL)
    if miss_crit < miss_warn:
        miss_warn, miss_crit = miss_crit, miss_warn

    worst = SEVERITY_NONE
    missed_jobs: List[Dict] = []
    details: List[str] = []

    for job_id, interval in PROTECTED_JOB_INTERVALS_MIN.items():
        raw = GlobalSettings.get_value(f'scheduler_last_run_{job_id}', None)
        last_dt = _parse_last_run_ts(raw)
        if last_dt is None:
            details.append(f'{job_id}: no last-run stamp yet')
            continue
        age_min = (now - last_dt).total_seconds() / 60.0
        sev = classify_missed_runs(age_min, float(interval), miss_warn, miss_crit)
        if sev != SEVERITY_NONE:
            missed_jobs.append({
                'job_id': job_id,
                'age_min': round(age_min, 1),
                'interval_min': interval,
                'approx_misses': round(age_min / interval, 1),
                'severity': sev,
            })
            details.append(
                f'{job_id}: last run {age_min:.0f}m ago '
                f'(~{age_min / interval:.1f}× {interval}m interval) → {sev}'
            )
            worst = max_severity([worst, sev])

    if not missed_jobs and all('no last-run' in d for d in details):
        detail = (
            'Protected jobs have no last-run stamps yet (common right after boot); '
            'not alarming.'
        )
        worst = SEVERITY_NONE
    elif not details:
        detail = 'All protected jobs have recent last-run stamps within tolerance.'
    else:
        detail = '; '.join(details)

    return HealthSignal(
        key='scheduler_missed',
        severity=worst,
        title='Protected scheduler jobs',
        detail=detail,
        metrics={
            'missed_jobs': missed_jobs,
            'miss_warn': miss_warn,
            'miss_critical': miss_crit,
        },
    )


def _parse_sftp_timestamp(raw: str) -> Optional[datetime]:
    raw = (raw or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S UTC', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace('Z', ''))
    except ValueError:
        return None


def _collect_sftp_freshness(now: datetime) -> HealthSignal:
    from models import GlobalSettings

    warn_min = _config_float(CONFIG_SFTP_WARN, DEFAULT_SFTP_WARN_MIN)
    crit_min = _config_float(CONFIG_SFTP_CRIT, DEFAULT_SFTP_CRITICAL_MIN)
    if crit_min < warn_min:
        warn_min, crit_min = crit_min, warn_min

    enabled_raw = (GlobalSettings.get_value('automated_uploads_enabled', 'false') or '').lower()
    enabled = enabled_raw == 'true'
    last_raw = GlobalSettings.get_value('last_sftp_upload_time', '') or ''
    last_dt = _parse_sftp_timestamp(last_raw)

    if not enabled:
        return HealthSignal(
            key='sftp_freshness',
            severity=SEVERITY_NONE,
            title='SFTP / feed freshness',
            detail='Automated uploads disabled — freshness signal skipped.',
            metrics={'enabled': False, 'last_raw': last_raw},
        )

    if not last_dt:
        return HealthSignal(
            key='sftp_freshness',
            severity=SEVERITY_WARNING,
            title='SFTP / feed freshness',
            detail='Uploads enabled but no last_sftp_upload_time recorded.',
            metrics={'enabled': True, 'last_raw': last_raw, 'age_min': None},
        )

    age_min = (now - last_dt).total_seconds() / 60.0
    severity = classify_age_minutes(age_min, warn_min, crit_min)
    return HealthSignal(
        key='sftp_freshness',
        severity=severity,
        title='SFTP / feed freshness',
        detail=f'Last SFTP upload {age_min:.0f}m ago ({last_raw}).',
        metrics={'enabled': True, 'last_raw': last_raw, 'age_min': round(age_min, 1)},
    )


def collect_signals(now: Optional[datetime] = None) -> List[HealthSignal]:
    """Run all Phase 1 collectors. Each collector is fail-soft."""
    now = now or datetime.utcnow()
    collectors = (
        _collect_inbound_null_bh,
        _collect_screening_stall,
        _collect_scheduler_misses,
        _collect_sftp_freshness,
    )
    signals: List[HealthSignal] = []
    for collector in collectors:
        try:
            signals.append(collector(now))
        except Exception as exc:
            logger.exception(
                'ops early-warning collector %s failed', collector.__name__
            )
            signals.append(HealthSignal(
                key=collector.__name__.replace('_collect_', ''),
                severity=SEVERITY_WARNING,
                title=collector.__name__,
                detail=f'Collector error: {type(exc).__name__}: {exc}'[:300],
                metrics={'collector_error': True},
            ))
    return signals


# ── Email ─────────────────────────────────────────────────────────────────────

def _build_alert_html(
    severity: str, fingerprint: str, signals: List[HealthSignal]
) -> str:
    banner = '#dc3545' if severity == SEVERITY_CRITICAL else '#ffc107'
    rows = []
    for s in signals:
        if s.severity == SEVERITY_NONE:
            continue
        colour = '#dc3545' if s.severity == SEVERITY_CRITICAL else '#856404'
        rows.append(
            f'<tr><td style="padding:8px; border-bottom:1px solid #eee; color:{colour};">'
            f'<strong>{s.severity.upper()}</strong></td>'
            f'<td style="padding:8px; border-bottom:1px solid #eee;"><strong>{s.title}</strong>'
            f'<br><span style="color:#555; font-size:13px;">{s.detail}</span></td></tr>'
        )
    body_rows = ''.join(rows) or '<tr><td colspan="2">No details</td></tr>'
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
      <h2 style="color:{banner};">{severity.upper()} — Scout Genius ops early-warning</h2>
      <p>One or more Phase 1 health signals crossed a warn/critical threshold.
      This alert is <strong>observe-only</strong> — nothing was auto-fixed.</p>
      <table style="border-collapse:collapse; width:100%; max-width:720px;">{body_rows}</table>
      <p style="color:#666; font-size:12px; margin-top:20px;">
        Fingerprint: <code>{fingerprint}</code><br>
        Dashboard: <a href="https://app.scoutgenius.ai/admin/health">/admin/health</a><br>
        Design: <code>.agents/memory/ops-early-warning.md</code><br>
        Never auto: BH credential rotate, qualify/note rewrite, auto-merge/deploy.
      </p>
    </body></html>
    """


def _send_alert_email(recipient: str, subject: str, html_content: str) -> bool:
    api_key = os.environ.get('SENDGRID_API_KEY')
    if not api_key:
        logger.warning('ops early-warning: SENDGRID_API_KEY not configured; cannot send')
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
        logger.warning('ops early-warning: SendGrid returned %s', response.status_code)
        return False
    except Exception as exc:
        logger.error(
            'ops early-warning: send failed (%s: %s)', type(exc).__name__, exc
        )
        return False


# ── Orchestration ─────────────────────────────────────────────────────────────

def evaluate_signals(now: Optional[datetime] = None) -> Dict:
    """Collect signals and return a summary dict (no email). Used by admin tile."""
    now = now or datetime.utcnow()
    signals = collect_signals(now)
    severity = max_severity([s.severity for s in signals])
    fingerprint = build_fingerprint(signals)
    active = [s for s in signals if s.severity != SEVERITY_NONE]
    return {
        'evaluated_at': now.isoformat(),
        'severity': severity,
        'fingerprint': fingerprint,
        'signal_count': len(active),
        'signals': [s.to_dict() for s in signals],
        'active_titles': [s.title for s in active],
    }


def run_ops_early_warning_check(now: Optional[datetime] = None) -> Dict:
    """Evaluate Phase 1 signals and e-mail on warn/critical with fingerprint cooldown.

    Fail-soft: never raises into the scheduler.
    """
    from models import VettingConfig

    now = now or datetime.utcnow()
    result = {
        'evaluated': False,
        'severity': SEVERITY_NONE,
        'fingerprint': 'none',
        'alert_sent': False,
        'reason': '',
        'signals': [],
    }

    try:
        if not _config_bool(CONFIG_ENABLED, True):
            result['reason'] = f'disabled via {CONFIG_ENABLED}'
            return result

        signal_objs = collect_signals(now)
        severity = max_severity([s.severity for s in signal_objs])
        fingerprint = build_fingerprint(signal_objs)

        result.update({
            'evaluated': True,
            'severity': severity,
            'fingerprint': fingerprint,
            'signals': [s.to_dict() for s in signal_objs],
        })

        last_severity, last_fingerprint, last_sent_at = _read_state()
        cooldown = _config_float(CONFIG_COOLDOWN, DEFAULT_COOLDOWN_HOURS)

        try:
            VettingConfig.set_value(
                STATE_LAST_SUMMARY,
                json.dumps({
                    'evaluated_at': now.isoformat(),
                    'severity': severity,
                    'fingerprint': fingerprint,
                    'active': [
                        {'key': s.key, 'severity': s.severity, 'title': s.title}
                        for s in signal_objs if s.severity != SEVERITY_NONE
                    ],
                }),
            )
        except Exception:
            logger.exception('ops early-warning: failed to stamp last summary')

        if severity == SEVERITY_NONE:
            if last_severity and last_severity != SEVERITY_NONE:
                VettingConfig.set_value(STATE_LAST_SEVERITY, SEVERITY_NONE)
                VettingConfig.set_value(STATE_LAST_FINGERPRINT, 'none')
                logger.info('ops early-warning: recovered to healthy — cleared prior state')
            result['reason'] = 'no warn/critical signals'
            return result

        send, reason = should_send_alert(
            severity,
            fingerprint,
            last_severity,
            last_fingerprint,
            last_sent_at,
            now,
            cooldown,
        )
        result['reason'] = reason
        if not send:
            logger.info('ops early-warning: %s — %s', severity, reason)
            return result

        recipient = resolve_recipient()
        if not recipient:
            result['reason'] = 'no recipient configured'
            return result

        html = _build_alert_html(severity, fingerprint, signal_objs)
        titles = ', '.join(
            s.title for s in signal_objs if s.severity != SEVERITY_NONE
        ) or 'signals'
        subject = f'{severity.upper()} Scout Genius ops alert — {titles}'

        if _send_alert_email(recipient, subject, html):
            VettingConfig.set_value(STATE_LAST_SENT_AT, now.isoformat())
            VettingConfig.set_value(STATE_LAST_SEVERITY, severity)
            VettingConfig.set_value(STATE_LAST_FINGERPRINT, fingerprint)
            result['alert_sent'] = True
            logger.warning(
                'ops early-warning alert sent to %s: %s fingerprint=%s (%s)',
                recipient, severity, fingerprint, reason,
            )
        else:
            result['reason'] = 'send failed'

        return result

    except Exception as exc:
        logger.error(
            'ops early-warning check failed (%s: %s)',
            type(exc).__name__, exc, exc_info=True,
        )
        result['reason'] = f'error: {type(exc).__name__}'
        return result
