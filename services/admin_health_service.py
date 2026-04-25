"""
Admin Health Service — Scout Genius super-admin operational dashboard.

Read-only collector that gathers status tiles for `/admin/health`. Each tile
method MUST swallow exceptions and downgrade to status='unknown' so the
dashboard remains functional even when an upstream check is broken.

This module performs zero writes — it only queries existing models and
in-process state (scheduler, OneDrive token cache).
"""

import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)


# Thresholds (minutes) — when a "last successful X" timestamp ages past these
# bounds, the tile transitions green → amber → red. Tuned conservatively so
# normal operating cadence stays green.
FRESH_GREEN_MIN = 60          # within last hour = green
FRESH_AMBER_MIN = 6 * 60      # 1-6h = amber, beyond = red

VETTING_INFLIGHT_AMBER = 25   # >25 in flight = amber
VETTING_INFLIGHT_RED = 100    # >100 in flight = red
VETTING_INFLIGHT_OLD_MIN = 30 # an in-flight record older than 30min = stuck

VETTING_FAILED_AMBER = 5      # >5 failures in 24h = amber
VETTING_FAILED_RED = 25       # >25 failures in 24h = red

ONEDRIVE_AMBER_MIN = 30       # token expiring within 30min = amber


@dataclass
class HealthTile:
    """Display contract for a single dashboard tile.

    status:
        green   - healthy
        amber   - degraded but functional
        red     - broken / requires attention
        unknown - check itself failed; do not infer health
    """
    key: str
    label: str
    icon: str                       # font-awesome class, e.g. 'fa-database'
    status: str                     # 'green' | 'amber' | 'red' | 'unknown'
    value: str = ''                 # primary display value
    subtext: str = ''               # context line under value
    remediation: str = ''           # remediation hint when not green
    last_checked: str = ''          # ISO timestamp of when this tile ran

    def to_dict(self) -> dict:
        return asdict(self)


def _format_age(dt: Optional[datetime], now: datetime) -> str:
    """Render a naive-UTC datetime as a relative age string."""
    if not dt:
        return 'never'
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return 'just now'
    if seconds < 60:
        return f'{seconds}s ago'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes}m ago'
    hours = minutes // 60
    if hours < 24:
        return f'{hours}h ago'
    days = hours // 24
    return f'{days}d ago'


def _status_from_age(dt: Optional[datetime], now: datetime) -> str:
    """Map a 'last successful' timestamp into a green/amber/red bucket."""
    if not dt:
        return 'red'
    minutes = (now - dt).total_seconds() / 60
    if minutes < 0 or minutes <= FRESH_GREEN_MIN:
        return 'green'
    if minutes <= FRESH_AMBER_MIN:
        return 'amber'
    return 'red'


class AdminHealthService:
    """Collects health-tile data for the super-admin operational dashboard.

    Construct once per request — `_now` is captured at construction so every
    tile in a single dashboard render shares a consistent "as of" timestamp.
    """

    def __init__(self):
        self._now = datetime.utcnow()
        self._now_iso = self._now.isoformat() + 'Z'

    def collect_all(self) -> List[HealthTile]:
        """Return every tile in canonical display order."""
        collectors = [
            self.tile_database,
            self.tile_scheduler,
            self.tile_bullhorn_auth,
            self.tile_openai,
            self.tile_inflight_vetting,
            self.tile_failed_vetting_24h,
            self.tile_sftp_uploads,
            self.tile_onedrive_token,
        ]
        tiles: List[HealthTile] = []
        for collector in collectors:
            try:
                tile = collector()
            except Exception as exc:
                # Last-resort safety net — collectors are supposed to swallow
                # their own errors but if one slips through we don't break the
                # whole dashboard. Log loudly so the bug gets noticed.
                logger.exception('admin_health collector %s raised', collector.__name__)
                tile = HealthTile(
                    key=collector.__name__.replace('tile_', ''),
                    label=collector.__name__.replace('tile_', '').replace('_', ' ').title(),
                    icon='fa-question-circle',
                    status='unknown',
                    value='Check failed',
                    subtext=str(exc)[:120],
                    remediation='Inspect application logs for the full traceback.',
                    last_checked=self._now_iso,
                )
            tiles.append(tile)
        return tiles

    # ------------------------------------------------------------------ tiles

    def tile_database(self) -> HealthTile:
        """Live SELECT 1 against the application database."""
        from extensions import db
        from sqlalchemy import text
        try:
            db.session.execute(text('SELECT 1')).scalar()
            return HealthTile(
                key='database',
                label='Database',
                icon='fa-database',
                status='green',
                value='Connected',
                subtext='SELECT 1 succeeded',
                last_checked=self._now_iso,
            )
        except Exception as exc:
            return HealthTile(
                key='database',
                label='Database',
                icon='fa-database',
                status='red',
                value='Disconnected',
                subtext=str(exc)[:160],
                remediation='Verify DATABASE_URL and Postgres reachability; restart workflow if pool is exhausted.',
                last_checked=self._now_iso,
            )

    def tile_scheduler(self) -> HealthTile:
        """APScheduler running state + scheduled-job count."""
        try:
            from app import scheduler
            running = bool(scheduler.running)
            try:
                jobs_count = len(scheduler.get_jobs())
            except Exception:
                jobs_count = 0
            if running:
                status = 'green' if jobs_count > 0 else 'amber'
                return HealthTile(
                    key='scheduler',
                    label='Scheduler',
                    icon='fa-clock',
                    status=status,
                    value='Running',
                    subtext=f'{jobs_count} scheduled job(s)',
                    remediation='' if status == 'green' else 'Scheduler is up but no jobs are registered — check scheduler_setup.py.',
                    last_checked=self._now_iso,
                )
            return HealthTile(
                key='scheduler',
                label='Scheduler',
                icon='fa-clock',
                status='red',
                value='Stopped',
                subtext='Background jobs are not running',
                remediation='Restart the Start application workflow to recover the scheduler.',
                last_checked=self._now_iso,
            )
        except Exception as exc:
            return HealthTile(
                key='scheduler',
                label='Scheduler',
                icon='fa-clock',
                status='unknown',
                value='Check failed',
                subtext=str(exc)[:160],
                remediation='Inspect scheduler import path in app.py.',
                last_checked=self._now_iso,
            )

    def tile_bullhorn_auth(self) -> HealthTile:
        """Latest VettingHealthCheck.bullhorn_status (populated each cycle by tasks.run_vetting_health_check)."""
        return self._tile_from_vetting_health(
            key='bullhorn_auth',
            label='Bullhorn Auth',
            icon='fa-handshake',
            status_attr='bullhorn_status',
            error_attr='bullhorn_error',
            ok_value='Authenticated',
            fail_value='Auth failure',
            remediation_ok='',
            remediation_fail='Refresh credentials in ATS Credentials and re-run vetting health check.',
        )

    def tile_openai(self) -> HealthTile:
        """Latest VettingHealthCheck.openai_status — proxy for OpenAI API reachability."""
        # Quick local check first: if the key isn't even configured, skip the
        # round-trip to the health-check table.
        if not os.environ.get('OPENAI_API_KEY'):
            return HealthTile(
                key='openai',
                label='OpenAI',
                icon='fa-brain',
                status='red',
                value='No API key',
                subtext='OPENAI_API_KEY environment variable is not set',
                remediation='Add OPENAI_API_KEY in the environment-secrets pane.',
                last_checked=self._now_iso,
            )
        return self._tile_from_vetting_health(
            key='openai',
            label='OpenAI',
            icon='fa-brain',
            status_attr='openai_status',
            error_attr='openai_error',
            ok_value='Reachable',
            fail_value='API failure',
            remediation_ok='',
            remediation_fail='Check OpenAI quota/status page; verify OPENAI_API_KEY validity.',
        )

    def _tile_from_vetting_health(
        self,
        *,
        key: str,
        label: str,
        icon: str,
        status_attr: str,
        error_attr: str,
        ok_value: str,
        fail_value: str,
        remediation_ok: str,
        remediation_fail: str,
    ) -> HealthTile:
        """Shared helper for tiles backed by VettingHealthCheck columns."""
        try:
            from models import VettingHealthCheck
            latest = VettingHealthCheck.query.order_by(VettingHealthCheck.check_time.desc()).first()
            if not latest:
                return HealthTile(
                    key=key,
                    label=label,
                    icon=icon,
                    status='unknown',
                    value='No data',
                    subtext='Vetting health check has not run yet',
                    remediation='Trigger a vetting health check to populate this tile.',
                    last_checked=self._now_iso,
                )

            ok = bool(getattr(latest, status_attr, False))
            err = getattr(latest, error_attr, None) or ''
            age_status = _status_from_age(latest.check_time, self._now)
            age_str = _format_age(latest.check_time, self._now)

            if ok:
                # Even if the last check passed, stale data should show amber.
                final_status = 'green' if age_status == 'green' else 'amber'
                subtext = f'Last check {age_str}'
                remediation = '' if final_status == 'green' else 'Health check is stale — verify the scheduler is running.'
                return HealthTile(
                    key=key,
                    label=label,
                    icon=icon,
                    status=final_status,
                    value=ok_value,
                    subtext=subtext,
                    remediation=remediation or remediation_ok,
                    last_checked=self._now_iso,
                )
            return HealthTile(
                key=key,
                label=label,
                icon=icon,
                status='red',
                value=fail_value,
                subtext=(err[:160] if err else f'Last check {age_str}'),
                remediation=remediation_fail,
                last_checked=self._now_iso,
            )
        except Exception as exc:
            return HealthTile(
                key=key,
                label=label,
                icon=icon,
                status='unknown',
                value='Check failed',
                subtext=str(exc)[:160],
                remediation='Inspect VettingHealthCheck table and tasks.run_vetting_health_check.',
                last_checked=self._now_iso,
            )

    def tile_inflight_vetting(self) -> HealthTile:
        """Count of CandidateVettingLog rows currently in pending/processing state."""
        try:
            from models import CandidateVettingLog
            inflight_q = CandidateVettingLog.query.filter(
                CandidateVettingLog.status.in_(['pending', 'processing'])
            )
            count = inflight_q.count()
            oldest = inflight_q.order_by(CandidateVettingLog.created_at.asc()).first()
            oldest_age_min = None
            if oldest and oldest.created_at:
                oldest_age_min = (self._now - oldest.created_at).total_seconds() / 60

            if count == 0:
                return HealthTile(
                    key='inflight_vetting',
                    label='In-Flight Vetting',
                    icon='fa-stream',
                    status='green',
                    value='0',
                    subtext='Queue is idle',
                    last_checked=self._now_iso,
                )

            # Stuck-record check overrides volume-based amber/red.
            stuck = oldest_age_min is not None and oldest_age_min >= VETTING_INFLIGHT_OLD_MIN
            if count >= VETTING_INFLIGHT_RED or stuck:
                status = 'red'
            elif count >= VETTING_INFLIGHT_AMBER:
                status = 'amber'
            else:
                status = 'green'

            subtext = f'Oldest queued {_format_age(oldest.created_at, self._now)}' if oldest else 'No timestamps available'
            remediation = ''
            if stuck:
                remediation = 'Oldest in-flight record is over 30 minutes old — restart the workflow or trigger recovery.'
            elif status == 'amber':
                remediation = 'Queue is building — verify scheduler throughput.'
            elif status == 'red':
                remediation = 'Queue is severely backed up — investigate Bullhorn/OpenAI throughput.'

            return HealthTile(
                key='inflight_vetting',
                label='In-Flight Vetting',
                icon='fa-stream',
                status=status,
                value=str(count),
                subtext=subtext,
                remediation=remediation,
                last_checked=self._now_iso,
            )
        except Exception as exc:
            return HealthTile(
                key='inflight_vetting',
                label='In-Flight Vetting',
                icon='fa-stream',
                status='unknown',
                value='Check failed',
                subtext=str(exc)[:160],
                remediation='Inspect CandidateVettingLog table.',
                last_checked=self._now_iso,
            )

    def tile_failed_vetting_24h(self) -> HealthTile:
        """Count of CandidateVettingLog failures in the last 24 hours."""
        try:
            from models import CandidateVettingLog
            cutoff = self._now - timedelta(hours=24)
            count = CandidateVettingLog.query.filter(
                CandidateVettingLog.status == 'failed',
                CandidateVettingLog.created_at >= cutoff,
            ).count()

            if count == 0:
                return HealthTile(
                    key='failed_vetting_24h',
                    label='Vetting Failures (24h)',
                    icon='fa-exclamation-triangle',
                    status='green',
                    value='0',
                    subtext='No failures in the last 24 hours',
                    last_checked=self._now_iso,
                )

            if count >= VETTING_FAILED_RED:
                status = 'red'
                remediation = 'High failure rate — inspect Bullhorn auth, OpenAI quota, and recent error_message values.'
            elif count >= VETTING_FAILED_AMBER:
                status = 'amber'
                remediation = 'Failure rate elevated — review CandidateVettingLog.error_message for recent patterns.'
            else:
                status = 'green'
                remediation = ''

            return HealthTile(
                key='failed_vetting_24h',
                label='Vetting Failures (24h)',
                icon='fa-exclamation-triangle',
                status=status,
                value=str(count),
                subtext='Trailing 24-hour window',
                remediation=remediation,
                last_checked=self._now_iso,
            )
        except Exception as exc:
            return HealthTile(
                key='failed_vetting_24h',
                label='Vetting Failures (24h)',
                icon='fa-exclamation-triangle',
                status='unknown',
                value='Check failed',
                subtext=str(exc)[:160],
                remediation='Inspect CandidateVettingLog table.',
                last_checked=self._now_iso,
            )

    def tile_sftp_uploads(self) -> HealthTile:
        """Last successful SFTP upload time from GlobalSettings."""
        try:
            from models import GlobalSettings
            enabled_setting = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
            enabled = bool(enabled_setting and (enabled_setting.setting_value or '').lower() == 'true')

            last_setting = GlobalSettings.query.filter_by(setting_key='last_sftp_upload_time').first()
            last_raw = (last_setting.setting_value if last_setting else '') or ''

            if not enabled:
                return HealthTile(
                    key='sftp_uploads',
                    label='SFTP Uploads',
                    icon='fa-cloud-upload-alt',
                    status='amber',
                    value='Disabled',
                    subtext=f'Last upload: {last_raw or "never"}',
                    remediation='Automated uploads are off. Enable in the automation hub if intentional this is harmless.',
                    last_checked=self._now_iso,
                )

            # Parse the recorded timestamp using the formats the app already writes.
            last_dt = self._parse_sftp_timestamp(last_raw)
            if not last_dt:
                return HealthTile(
                    key='sftp_uploads',
                    label='SFTP Uploads',
                    icon='fa-cloud-upload-alt',
                    status='red',
                    value='No uploads',
                    subtext='Automation is enabled but no last_sftp_upload_time recorded',
                    remediation='Trigger a manual SFTP upload from the automation hub to verify connectivity.',
                    last_checked=self._now_iso,
                )

            status = _status_from_age(last_dt, self._now)
            age = _format_age(last_dt, self._now)
            remediation = ''
            if status == 'amber':
                remediation = 'Last upload is older than 1 hour — check SFTP credentials and scheduler.'
            elif status == 'red':
                remediation = 'No successful upload in 6+ hours — verify SFTP host reachability and credentials.'
            return HealthTile(
                key='sftp_uploads',
                label='SFTP Uploads',
                icon='fa-cloud-upload-alt',
                status=status,
                value=age,
                subtext=last_raw,
                remediation=remediation,
                last_checked=self._now_iso,
            )
        except Exception as exc:
            return HealthTile(
                key='sftp_uploads',
                label='SFTP Uploads',
                icon='fa-cloud-upload-alt',
                status='unknown',
                value='Check failed',
                subtext=str(exc)[:160],
                remediation='Inspect GlobalSettings for last_sftp_upload_time.',
                last_checked=self._now_iso,
            )

    @staticmethod
    def _parse_sftp_timestamp(raw: str) -> Optional[datetime]:
        if not raw:
            return None
        raw = raw.strip()
        for fmt in ('%Y-%m-%d %H:%M:%S UTC', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    def tile_onedrive_token(self) -> HealthTile:
        """OneDrive access-token connection state + expiry countdown."""
        try:
            from onedrive_service import OneDriveService
            svc = OneDriveService()
            try:
                svc.get_access_token()
            except Exception as exc:
                return HealthTile(
                    key='onedrive_token',
                    label='OneDrive Token',
                    icon='fa-cloud',
                    status='red',
                    value='Disconnected',
                    subtext=str(exc)[:160],
                    remediation='Reconnect OneDrive in Replit Connections.',
                    last_checked=self._now_iso,
                )

            expires_at_ts = getattr(svc, '_token_expires_at', None)
            if not expires_at_ts:
                return HealthTile(
                    key='onedrive_token',
                    label='OneDrive Token',
                    icon='fa-cloud',
                    status='green',
                    value='Connected',
                    subtext='Token expiry unknown',
                    last_checked=self._now_iso,
                )

            expires_dt = datetime.utcfromtimestamp(expires_at_ts)
            minutes_left = (expires_dt - self._now).total_seconds() / 60
            if minutes_left <= 0:
                status = 'red'
                value = 'Expired'
                remediation = 'Token has expired — next request will trigger refresh; reconnect if it fails.'
            elif minutes_left <= ONEDRIVE_AMBER_MIN:
                status = 'amber'
                value = f'{int(minutes_left)}m left'
                remediation = 'Token is expiring soon — auto-refresh should kick in on next request.'
            else:
                status = 'green'
                value = 'Connected'
                remediation = ''

            return HealthTile(
                key='onedrive_token',
                label='OneDrive Token',
                icon='fa-cloud',
                status=status,
                value=value,
                subtext=f'Expires {_format_age(expires_dt, self._now)} from now' if minutes_left > 0 else 'Expired',
                remediation=remediation,
                last_checked=self._now_iso,
            )
        except Exception as exc:
            return HealthTile(
                key='onedrive_token',
                label='OneDrive Token',
                icon='fa-cloud',
                status='unknown',
                value='Check failed',
                subtext=str(exc)[:160],
                remediation='Inspect onedrive_service.py and Replit OneDrive connection.',
                last_checked=self._now_iso,
            )

    # --------------------------------------------------------------- summary

    def overall_status(self, tiles: List[HealthTile]) -> str:
        """Roll up tile statuses into a single dashboard banner color."""
        statuses = {t.status for t in tiles}
        if 'red' in statuses:
            return 'red'
        if 'amber' in statuses:
            return 'amber'
        if 'unknown' in statuses:
            return 'amber'
        return 'green'
