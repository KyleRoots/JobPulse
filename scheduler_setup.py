"""
Scheduler Setup — APScheduler job definitions and configuration.

Contains:
- acquire_scheduler_lock: Attempt to acquire the primary-worker lock file
- release_scheduler_lock: Release the lock on process exit
- configure_scheduler_jobs: Register all background jobs with APScheduler
- process_bullhorn_monitors: Incremental Bullhorn tearsheet monitoring job (closure)
- Inline job runners: salesrep sync, dedup merge, OneDrive sync, candidate cleanup,
  incomplete rescreen, screening audit, stale platform ticket check

Note: process_scheduled_files (XML schedule-based processing) was removed here as it
has been disabled since the Enhanced 8-Step Monitor took over all XML update duties.
"""

import atexit
import fcntl
import os
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_LOCK_FILE = '/tmp/scoutgenius_scheduler.lock'
_lock_fd = None


def release_scheduler_lock():
    """Release the primary-worker scheduler lock on process exit."""
    global _lock_fd
    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            os.close(_lock_fd)
            logger.info("🔓 Released scheduler lock on process exit")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Error releasing scheduler lock: {e}")
        finally:
            _lock_fd = None


def acquire_scheduler_lock():
    """Try to acquire an exclusive non-blocking lock for the primary scheduler worker.

    Returns:
        bool: True if this process is the primary worker (lock acquired), False otherwise.
    """
    global _lock_fd
    worker_pid = os.getpid()
    print("🔒 SCHEDULER INIT: Attempting to acquire scheduler lock...", flush=True)
    try:
        fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_WRONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd = fd
        print(
            f"✅ SCHEDULER INIT: Process {worker_pid} acquired scheduler lock"
            " - will run as PRIMARY scheduler",
            flush=True,
        )
        logger.info(
            f"✅ Process {worker_pid} acquired scheduler lock - will run as PRIMARY scheduler"
        )
        atexit.register(release_scheduler_lock)
        return True
    except (IOError, OSError) as e:
        print(
            f"⚠️ SCHEDULER INIT: Process {worker_pid} could not acquire scheduler lock"
            f" (already held): {e}",
            flush=True,
        )
        logger.info(
            f"⚠️ Process {worker_pid} could not acquire scheduler lock"
            " - another scheduler is already running"
        )
        if _lock_fd is not None:
            os.close(_lock_fd)
            _lock_fd = None
        return False
    except Exception as e:  # noqa: BLE001
        print(f"❌ SCHEDULER INIT: Unexpected error during lock acquisition: {e}", flush=True)
        logger.error(f"❌ Unexpected scheduler lock error: {e}")
        return False


def configure_scheduler_jobs(app, scheduler, is_primary_worker):
    """Register all APScheduler background jobs. Call once after lock acquisition."""

    from extensions import db
    from models import GlobalSettings, BullhornMonitor, RefreshLog

    app.logger.info("📌 Process Scheduled XML Files job DISABLED - Enhanced 8-Step Monitor handles all XML updates")

    # ── Incremental Bullhorn Tearsheet Monitor ────────────────────────────────
    def process_bullhorn_monitors():
        """Process all active Bullhorn monitors using simplified incremental monitoring."""
        with app.app_context():
            try:
                from feeds.freeze_manager import FreezeManager
                freeze_mgr = FreezeManager()
                if freeze_mgr.is_frozen():
                    app.logger.info("🔒 XML FEED FROZEN: Skipping monitoring cycle")
                    return

                app.logger.info("🔄 INCREMENTAL MONITOR: Starting simplified monitoring cycle")

                from incremental_monitoring_service import IncrementalMonitoringService
                monitoring_service = IncrementalMonitoringService()

                db_monitors = BullhornMonitor.query.filter_by(is_active=True).all()

                if not db_monitors:
                    class MockMonitor:
                        def __init__(self, name, tearsheet_id):
                            self.name = name
                            self.tearsheet_id = tearsheet_id
                            self.is_active = True

                    db_monitors = [
                        MockMonitor('Sponsored - OTT', 1256),
                        MockMonitor('Sponsored - VMS', 1264),
                        MockMonitor('Sponsored - GR', 1499),
                        MockMonitor('Sponsored - CHI', 1257),
                        MockMonitor('Sponsored - STSI', 1556),
                    ]
                    app.logger.info(f"Using {len(db_monitors)} hardcoded tearsheet monitors (fallback)")
                else:
                    app.logger.info(f"Using {len(db_monitors)} database monitors")

                with app.app_context():
                    cycle_results = monitoring_service.run_monitoring_cycle()

                real_monitors = BullhornMonitor.query.filter_by(is_active=True).all()
                if real_monitors:
                    current_time = datetime.utcnow()
                    for monitor in real_monitors:
                        monitor.last_check = current_time
                        monitor.next_check = current_time + timedelta(minutes=5)
                    try:
                        db.session.commit()
                        app.logger.info(f"✅ Updated timing for {len(real_monitors)} monitors")
                    except Exception as e:
                        app.logger.error(f"Failed to update monitor timing: {e}")
                        db.session.rollback()

                app.logger.info(f"✅ Incremental monitoring completed: {cycle_results}")
                app.logger.info("📊 MONITOR CYCLE COMPLETE - Incremental monitoring handled all updates")

            except Exception as e:
                app.logger.error(f"❌ Incremental monitoring error: {str(e)}")
                db.session.rollback()
            finally:
                db.session.remove()

    # ── 5-Minute Tearsheet Monitor ────────────────────────────────────────────
    if is_primary_worker:
        try:
            scheduler.add_job(
                func=process_bullhorn_monitors,
                trigger=IntervalTrigger(minutes=5),
                id='process_bullhorn_monitors',
                name='5-Minute Tearsheet Monitor with Keyword Classification',
                replace_existing=True
            )
            print("✅ SCHEDULER INIT: 5-minute tearsheet monitoring job added", flush=True)
            app.logger.info("✅ 5-minute tearsheet monitoring ENABLED - provides UI visibility before 30-minute upload cycle")
        except Exception as e:
            print(f"❌ SCHEDULER INIT: Failed to add 5-minute monitoring job: {e}", flush=True)
            app.logger.error(f"Failed to add 5-minute monitoring job: {e}")

    # ── Monitor Health Check (every 2 hours) ──────────────────────────────────
    if is_primary_worker:
        from tasks import check_monitor_health
        scheduler.add_job(
            func=check_monitor_health,
            trigger=IntervalTrigger(hours=2),
            id='check_monitor_health',
            name='Monitor Health Check (Manual Workflow)',
            replace_existing=True
        )
        app.logger.info("Monitor health check enabled - periodic check every 2 hours for manual workflow")

    # ── Production Environment Monitoring (every 5 minutes) ───────────────────
    if is_primary_worker:
        from tasks import check_environment_status
        scheduler.add_job(
            func=check_environment_status,
            trigger=IntervalTrigger(minutes=5),
            id='environment_monitoring',
            name='Production Environment Monitoring',
            replace_existing=True
        )
        app.logger.info("Environment monitoring enabled - checking production status every 5 minutes")

    # ── Active Job IDs Cache Refresh (every 5 minutes) ────────────────────────
    if is_primary_worker:
        def refresh_active_job_ids_cache():
            """Refresh the CandidateVettingService active job IDs cache in the background."""
            with app.app_context():
                try:
                    import time
                    from candidate_vetting_service import CandidateVettingService
                    svc = CandidateVettingService()
                    active_jobs = svc.get_active_jobs_from_tearsheets()
                    result = set(int(job.get('id')) for job in active_jobs if job.get('id'))
                    CandidateVettingService._active_job_ids_cache = result
                    CandidateVettingService._active_job_ids_cache_time = time.time()
                    app.logger.info(f"🔄 Active job IDs cache refreshed: {len(result)} jobs")
                except Exception as e:
                    app.logger.error(f"Error refreshing active job IDs cache: {e}")

        scheduler.add_job(
            func=refresh_active_job_ids_cache,
            trigger=IntervalTrigger(minutes=5),
            id='refresh_active_job_ids',
            name='Active Job IDs Cache Refresh (5 min)',
            replace_existing=True
        )
        try:
            refresh_active_job_ids_cache()
        except Exception as e:
            app.logger.warning(f"Initial active job IDs cache warm failed: {e}")
        app.logger.info("Active job IDs background cache refresh enabled (5-min interval)")

    # ── Activity Retention Cleanup (daily at 3 AM UTC) ────────────────────────
    if is_primary_worker:
        from tasks import activity_retention_cleanup
        scheduler.add_job(
            func=activity_retention_cleanup,
            trigger='cron',
            hour=3,
            minute=0,
            id='activity_cleanup',
            name='Activity Retention Cleanup (15 days)',
            replace_existing=True
        )
        app.logger.info("📋 Scheduled activity retention cleanup (15 days)")

    # ── Nightly Database Backup (daily at 2 AM UTC) ──────────────────────────
    if is_primary_worker:
        def run_nightly_backup():
            with app.app_context():
                try:
                    from backup_service import BackupService
                    svc = BackupService(app=app)
                    result = svc.run_backup(triggered_by="scheduler")
                    app.logger.info(f"📦 Nightly backup: {result.get('status')}")
                except Exception as e:
                    app.logger.error(f"❌ Nightly backup failed: {e}")

        scheduler.add_job(
            func=run_nightly_backup,
            trigger='cron',
            hour=2,
            minute=0,
            id='nightly_database_backup',
            name='Nightly Database Backup (2 AM UTC)',
            replace_existing=True
        )
        app.logger.info("📦 Scheduled nightly database backup (2 AM UTC → OneDrive)")

    # ── Log Monitoring / Self-Healing ─────────────────────────────────────────
    if is_primary_worker:
        from tasks import log_monitoring_cycle
        log_monitor_interval = int(os.environ.get('LOG_MONITOR_INTERVAL_MINUTES', '15'))
        scheduler.add_job(
            func=log_monitoring_cycle,
            trigger='interval',
            minutes=log_monitor_interval,
            id='log_monitoring',
            name=f'Render Log Monitoring (Self-Healing) - {log_monitor_interval}min',
            replace_existing=True
        )
        app.logger.info(f"📊 Log monitoring enabled - checking Render logs every {log_monitor_interval} minutes")

    # ── Email Parsing Timeout Cleanup (every 5 minutes) ───────────────────────
    if is_primary_worker:
        from tasks import email_parsing_timeout_cleanup
        scheduler.add_job(
            func=email_parsing_timeout_cleanup,
            trigger='interval',
            minutes=5,
            id='email_parsing_timeout_cleanup',
            name='Email Parsing Timeout Cleanup (10 min)',
            replace_existing=True
        )
        app.logger.info("📧 Scheduled email parsing timeout cleanup (10 min threshold, every 5 min)")

    # ── Data Retention Cleanup (daily at 3 AM UTC) ────────────────────────────
    if is_primary_worker:
        from tasks import run_data_retention_cleanup
        scheduler.add_job(
            func=run_data_retention_cleanup,
            trigger='cron',
            hour=3,
            minute=0,
            id='data_retention_cleanup',
            name='Data Retention Cleanup (Daily)',
            replace_existing=True
        )
        app.logger.info("🧹 Scheduled data retention cleanup (daily at 3 AM UTC)")

    # ── Vetting System Health Check (every 10 minutes) ────────────────────────
    if is_primary_worker:
        from tasks import run_vetting_health_check
        scheduler.add_job(
            func=run_vetting_health_check,
            trigger='interval',
            minutes=10,
            id='vetting_health_check',
            name='Vetting System Health Check',
            replace_existing=True
        )
        app.logger.info("🩺 Scheduled vetting system health check (every 10 minutes)")

    # ── AI Candidate Vetting Cycle (every 1 minute) ───────────────────────────
    if is_primary_worker:
        from tasks import run_candidate_vetting_cycle
        scheduler.add_job(
            func=run_candidate_vetting_cycle,
            trigger='interval',
            minutes=1,
            id='candidate_vetting_cycle',
            name='AI Candidate Vetting Cycle',
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=False
        )
        app.logger.info("🎯 Scheduled AI candidate vetting cycle (every 1 minute)")

    # ── Automated XML Upload (every 30 minutes) ───────────────────────────────
    if is_primary_worker:
        from tasks import automated_upload
        print("📤 SCHEDULER INIT: Registering automated upload job (every 30 minutes)...", flush=True)
        try:
            scheduler.add_job(
                func=automated_upload,
                trigger=IntervalTrigger(minutes=30),
                id='automated_upload',
                name='Automated Upload (Every 30 Minutes)',
                replace_existing=True,
                misfire_grace_time=300,
                coalesce=False
            )
            print("✅ SCHEDULER INIT: Automated upload job registered successfully", flush=True)
            app.logger.info("📤 Scheduled automated uploads every 30 minutes")

            try:
                with app.app_context():
                    existing = GlobalSettings.query.filter_by(setting_key='next_sftp_upload_time').first()
                    seed_dt = datetime.now(timezone.utc) + timedelta(minutes=30)
                    seed_value = seed_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                    if not existing:
                        db.session.add(GlobalSettings(setting_key='next_sftp_upload_time', setting_value=seed_value))
                        db.session.commit()
                        app.logger.info(f"📤 Seeded initial next_sftp_upload_time: {seed_value}")
                    else:
                        try:
                            stored_dt = datetime.strptime(
                                existing.setting_value.strip(), '%Y-%m-%d %H:%M:%S UTC'
                            ).replace(tzinfo=timezone.utc)
                            if stored_dt <= datetime.now(timezone.utc) + timedelta(minutes=5):
                                existing.setting_value = seed_value
                                db.session.commit()
                                app.logger.info(f"📤 Refreshed stale next_sftp_upload_time: {seed_value}")
                        except Exception:
                            pass
            except Exception as seed_err:
                app.logger.warning(f"Could not seed next_sftp_upload_time: {seed_err}")

        except Exception as e:
            print(f"❌ SCHEDULER INIT: Failed to register automated upload job: {e}", flush=True)
            app.logger.error(f"Failed to register automated upload job: {e}")

    # ── LinkedIn Source Cleanup (hourly) ──────────────────────────────────────
    if is_primary_worker:
        from tasks import cleanup_linkedin_source
        scheduler.add_job(
            func=cleanup_linkedin_source,
            trigger=IntervalTrigger(hours=1),
            id='linkedin_source_cleanup',
            name='LinkedIn Source Cleanup (hourly)',
            replace_existing=True
        )
        app.logger.info("🔗 LinkedIn source cleanup enabled — runs hourly to update stale source tags")

    # ── Enforce Tearsheet Jobs Public (every 30 minutes) ─────────────────────
    if is_primary_worker:
        from tasks import enforce_tearsheet_jobs_public
        scheduler.add_job(
            func=enforce_tearsheet_jobs_public,
            trigger=IntervalTrigger(minutes=30),
            id='enforce_tearsheet_jobs_public',
            name='Enforce Tearsheet Jobs Public (Every 30 Minutes)',
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=False
        )
        app.logger.info("🌐 Enforce tearsheet jobs public enabled — runs every 30 minutes to set isPublic=true on all active tearsheet jobs")

    # ── Ownership Reassignment (every 5 minutes) ─────────────────────────────
    if is_primary_worker:
        from tasks import reassign_api_user_candidates
        scheduler.add_job(
            func=reassign_api_user_candidates,
            trigger=IntervalTrigger(minutes=5),
            id='owner_reassignment',
            name='API User → Recruiter Ownership Reassignment (5 min)',
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
        )
        app.logger.info("🔄 Owner reassignment task registered — runs every 5 minutes (gated on auto_reassign_owner_enabled)")

    # ── Requirements Maintenance (every 5 minutes) ────────────────────────────
    if is_primary_worker:
        from tasks import run_requirements_maintenance
        scheduler.add_job(
            func=run_requirements_maintenance,
            trigger=IntervalTrigger(minutes=5),
            id='requirements_maintenance',
            name='Requirements Maintenance — New & Modified Jobs (5 min)',
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=False
        )
        app.logger.info("🔍 Requirements maintenance enabled — auto-extracts for new jobs and re-interprets modified descriptions every 5 minutes")

    # ── Sales Rep Display Name Sync (every 30 minutes) ───────────────────────
    if is_primary_worker:
        from utils.bullhorn_helpers import get_bullhorn_service

        def run_salesrep_sync_job():
            with app.app_context():
                try:
                    from salesrep_sync_service import run_salesrep_sync
                    bullhorn = get_bullhorn_service()
                    result = run_salesrep_sync(bullhorn)
                    if result.get('updated', 0) > 0:
                        app.logger.info(
                            f"🏢 Sales Rep Sync: {result['updated']} companies updated "
                            f"(scanned {result['scanned']}, {result.get('errors', 0)} errors)"
                        )
                except Exception as e:
                    app.logger.error(f"Sales Rep Sync job error: {e}")
                finally:
                    db.session.remove()

        try:
            scheduler.add_job(
                func=run_salesrep_sync_job,
                trigger=IntervalTrigger(minutes=30),
                id='salesrep_sync',
                name='Sales Rep Display Name Sync (Every 30 Minutes)',
                replace_existing=True
            )
            print("✅ SCHEDULER INIT: Sales Rep sync job registered (every 30 minutes)", flush=True)
            app.logger.info("🏢 Scheduled Sales Rep display name sync every 30 minutes")
        except Exception as e:
            print(f"❌ SCHEDULER INIT: Failed to register Sales Rep sync job: {e}", flush=True)
            app.logger.error(f"Failed to register Sales Rep sync job: {e}")

    # ── Stale Platform Ticket Escalation Check (every 6 hours) ───────────────
    if is_primary_worker:
        def run_stale_platform_ticket_check():
            with app.app_context():
                try:
                    from scout_support_service import ScoutSupportService
                    svc = ScoutSupportService()
                    count = svc.check_stale_platform_tickets()
                    if count > 0:
                        app.logger.info(f"⏰ Stale platform ticket check: {count} ticket(s) escalated to admin")
                except Exception as e:
                    app.logger.error(f"Stale platform ticket check error: {e}")
                finally:
                    db.session.remove()

        try:
            scheduler.add_job(
                func=run_stale_platform_ticket_check,
                trigger=IntervalTrigger(hours=6),
                id='stale_platform_ticket_check',
                name='Stale Platform Ticket Escalation Check (Every 6 Hours)',
                replace_existing=True
            )
            print("✅ SCHEDULER INIT: Stale platform ticket check registered (every 6 hours)", flush=True)
            app.logger.info("⏰ Scheduled stale platform ticket escalation check every 6 hours")
        except Exception as e:
            print(f"❌ SCHEDULER INIT: Failed to register stale platform ticket check: {e}", flush=True)
            app.logger.error(f"Failed to register stale platform ticket check: {e}")

    # ── Duplicate Candidate Merge Check (every 60 minutes) ───────────────────
    if is_primary_worker:
        def run_duplicate_merge_check():
            with app.app_context():
                try:
                    from duplicate_merge_service import DuplicateMergeService
                    svc = DuplicateMergeService()
                    stats = svc.run_scheduled_check()
                    checked = stats.get('candidates_checked', 0)
                    merged = stats.get('merged', 0)
                    skipped = stats.get('skipped_below_threshold', stats.get('skipped', 0))
                    errors = stats.get('errors', 0)
                    fuzzy_checked = stats.get('fuzzy_candidates_checked', 0)
                    fuzzy_merged = stats.get('fuzzy_merged', 0)
                    fuzzy_skipped = stats.get('fuzzy_skipped', 0)
                    fuzzy_errors = stats.get('fuzzy_errors', 0)
                    app.logger.info(
                        f"🔀 Scheduled dedup: checked={checked}, "
                        f"merged={merged}, skipped={skipped}, errors={errors} | "
                        f"AI-fuzzy checked={fuzzy_checked}, merged={fuzzy_merged}, "
                        f"skipped={fuzzy_skipped}, errors={fuzzy_errors}"
                    )

                    try:
                        from models import AutomationTask, AutomationLog
                        import json as _json

                        task = AutomationTask.query.filter(
                            AutomationTask.config_json.contains('duplicate_merge_scan')
                        ).first()
                        if task:
                            total_merged = merged + fuzzy_merged
                            if total_merged > 0:
                                summary = (
                                    f"Checked {checked} candidate(s) — {merged} merged (exact), "
                                    f"{fuzzy_merged} merged (AI-fuzzy), {skipped} skipped"
                                )
                            else:
                                summary = f"Checked {checked} candidate(s) — no duplicates found"
                            total_errors = errors + fuzzy_errors
                            if total_errors > 0:
                                summary += f", {total_errors} error(s)"

                            log = AutomationLog(
                                automation_task_id=task.id,
                                status='success' if total_errors == 0 else 'warning',
                                message='Duplicate Merge Check (Scheduled)',
                                details_json=_json.dumps({
                                    'source': 'scheduled',
                                    'candidates_checked': checked,
                                    'merged': merged,
                                    'skipped': skipped,
                                    'errors': errors,
                                    'fuzzy_candidates_checked': fuzzy_checked,
                                    'fuzzy_merged': fuzzy_merged,
                                    'fuzzy_skipped': fuzzy_skipped,
                                    'fuzzy_errors': fuzzy_errors,
                                    'summary': summary,
                                })
                            )
                            db.session.add(log)
                            task.last_run_at = datetime.utcnow()
                            task.run_count = (task.run_count or 0) + 1
                            db.session.commit()
                    except Exception as log_err:
                        app.logger.warning(f"⚠️ Dedup check: could not write run history: {log_err}")

                except Exception as e:
                    app.logger.error(f"Scheduled duplicate merge check error: {e}")
                finally:
                    db.session.remove()

        try:
            scheduler.add_job(
                func=run_duplicate_merge_check,
                trigger=IntervalTrigger(minutes=60),
                id='duplicate_merge_check',
                name='Duplicate Candidate Merge Check (Every 60 Minutes)',
                replace_existing=True
            )
            print("✅ SCHEDULER INIT: Duplicate merge check registered (every 60 minutes)", flush=True)
            app.logger.info("🔀 Scheduled duplicate candidate merge check every 60 minutes")
        except Exception as e:
            print(f"❌ SCHEDULER INIT: Failed to register duplicate merge check: {e}", flush=True)
            app.logger.error(f"Failed to register duplicate merge check: {e}")

    # ── OneDrive Knowledge Sync (every 4 hours) ───────────────────────────────
    if is_primary_worker:
        def run_onedrive_sync():
            with app.app_context():
                try:
                    from onedrive_service import OneDriveService
                    from models import OneDriveSyncFolder
                    folders = OneDriveSyncFolder.query.filter_by(sync_enabled=True).count()
                    if folders > 0:
                        svc = OneDriveService()
                        stats = svc.sync_all_folders()
                        total = stats.get('total_synced', 0) + stats.get('total_updated', 0)
                        if total > 0:
                            app.logger.info(
                                f"☁️ OneDrive sync: {stats.get('total_synced', 0)} new, "
                                f"{stats.get('total_updated', 0)} updated across {stats.get('folders_synced', 0)} folder(s)"
                            )
                except Exception as e:
                    app.logger.error(f"OneDrive sync error: {e}")
                finally:
                    db.session.remove()

        try:
            scheduler.add_job(
                func=run_onedrive_sync,
                trigger=IntervalTrigger(hours=4),
                id='onedrive_knowledge_sync',
                name='OneDrive Knowledge Sync (Every 4 Hours)',
                replace_existing=True
            )
            print("✅ SCHEDULER INIT: OneDrive knowledge sync registered (every 4 hours)", flush=True)
            app.logger.info("☁️ Scheduled OneDrive knowledge sync every 4 hours")
        except Exception as e:
            print(f"❌ SCHEDULER INIT: Failed to register OneDrive sync: {e}", flush=True)
            app.logger.error(f"Failed to register OneDrive sync: {e}")

    # ── 120-Hour Reference Number Refresh ─────────────────────────────────────
    if is_primary_worker:
        from tasks import reference_number_refresh
        try:
            with app.app_context():
                last_refresh = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()

                if last_refresh:
                    calculated_next_run = last_refresh.refresh_time + timedelta(hours=120)
                    time_since_refresh = datetime.utcnow() - last_refresh.refresh_time
                    is_overdue = time_since_refresh > timedelta(hours=120)

                    if is_overdue:
                        calculated_next_run = datetime.utcnow() + timedelta(minutes=5)
                        app.logger.info(
                            f"⏰ Reference refresh: last_run={last_refresh.refresh_time.strftime('%Y-%m-%d %H:%M:%S UTC')}, "
                            f"next_run={calculated_next_run.strftime('%Y-%m-%d %H:%M:%S UTC')}, overdue=true "
                            f"(deferred to +5min, NOT firing inline on startup)"
                        )
                    else:
                        hours_until_next = (calculated_next_run - datetime.utcnow()).total_seconds() / 3600
                        app.logger.info(
                            f"📝 Reference refresh: last_run={last_refresh.refresh_time.strftime('%Y-%m-%d %H:%M:%S UTC')}, "
                            f"next_run={calculated_next_run.strftime('%Y-%m-%d %H:%M:%S UTC')}, overdue=false "
                            f"({hours_until_next:.1f}h remaining)"
                        )
                else:
                    calculated_next_run = datetime.utcnow() + timedelta(minutes=5)
                    app.logger.info(
                        f"🆕 Reference refresh: last_run=NONE, "
                        f"next_run={calculated_next_run.strftime('%Y-%m-%d %H:%M:%S UTC')}, "
                        f"no history found — deferred to +5min"
                    )

                scheduler.add_job(
                    func=reference_number_refresh,
                    trigger=IntervalTrigger(hours=120),
                    id='reference_number_refresh',
                    name='120-Hour Reference Number Refresh',
                    replace_existing=True,
                    next_run_time=calculated_next_run
                )
        except Exception as startup_error:
            app.logger.error(f"Failed to schedule reference refresh: {str(startup_error)}")

    # ── Candidate Data Cleanup (every 15 minutes, when enabled) ───────────────
    if is_primary_worker:
        def run_candidate_data_cleanup():
            """Scheduled cleanup: extract missing emails + reparse empty descriptions + fill occupations."""
            with app.app_context():
                try:
                    enabled = GlobalSettings.get_value('candidate_cleanup_enabled', 'false').lower() == 'true'
                    if not enabled:
                        return

                    batch_size = 50
                    try:
                        batch_size = int(GlobalSettings.get_value('candidate_cleanup_batch_size', '50'))
                    except (ValueError, TypeError):
                        pass

                    from automation_service import AutomationService
                    from bullhorn_service import BullhornService
                    from models import AutomationTask

                    svc = AutomationService()
                    bh = BullhornService()
                    bh.authenticate()
                    svc._bullhorn = bh

                    email_result = svc._builtin_email_extractor({
                        'dry_run': False,
                        'limit': batch_size,
                        'days_back': 3650,
                    })
                    email_updated = email_result.get('updated', 0) if isinstance(email_result, dict) else 0

                    reparse_result = svc._builtin_resume_reparser({
                        'dry_run': False,
                        'limit': batch_size,
                        'days_back': 3650,
                    })
                    reparse_updated = reparse_result.get('updated', 0) if isinstance(reparse_result, dict) else 0

                    occupation_updated = 0
                    try:
                        occupation_result = svc._builtin_occupation_extractor({
                            'dry_run': False,
                            'limit': batch_size,
                            'days_back': 30,
                        })
                        occupation_updated = occupation_result.get('updated', 0) if isinstance(occupation_result, dict) else 0
                    except Exception as oe:
                        app.logger.warning(f"🧹 Occupation extraction step failed: {oe}")

                    try:
                        occ_task = AutomationTask.query.filter(
                            AutomationTask.config_json.like('%"builtin_key": "occupation_extractor"%')
                        ).first()
                        if occ_task:
                            occ_task.last_run_at = datetime.utcnow()
                            occ_task.run_count = (occ_task.run_count or 0) + 1
                            db.session.commit()
                    except Exception:
                        db.session.rollback()

                    app.logger.info(
                        f"🧹 Candidate data cleanup cycle complete: "
                        f"emails_extracted={email_updated}, descriptions_reparsed={reparse_updated}, "
                        f"occupations_filled={occupation_updated} "
                        f"(batch_size={batch_size})"
                    )
                except Exception as e:
                    app.logger.error(f"❌ Candidate data cleanup error: {e}")
                finally:
                    db.session.remove()

        scheduler.add_job(
            func=run_candidate_data_cleanup,
            trigger=IntervalTrigger(minutes=15),
            id='candidate_data_cleanup',
            name='Candidate Data Cleanup (Every 15 Minutes)',
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=False
        )
        app.logger.info("🧹 Candidate data cleanup scheduled — runs every 15 minutes when enabled")

    # ── Incomplete Candidate Rescreen (every 15 minutes, when enabled) ────────
    if is_primary_worker:
        def run_incomplete_rescreen():
            """Reparse resumes for empty-description inbound candidates and re-queue for vetting."""
            with app.app_context():
                try:
                    enabled = GlobalSettings.get_value('incomplete_rescreen_enabled', 'false').lower() == 'true'
                    if not enabled:
                        return

                    from automation_service import AutomationService
                    from bullhorn_service import BullhornService

                    svc = AutomationService()
                    bh = BullhornService()
                    bh.authenticate()
                    svc._bullhorn = bh

                    result = svc._builtin_incomplete_rescreen({
                        'dry_run': False,
                        'batch_size': 20,
                    })
                    app.logger.info(
                        f"♻️  Incomplete rescreen cycle complete: {result.get('summary', '')}"
                    )
                except Exception as e:
                    app.logger.error(f"❌ Incomplete rescreen error: {e}")
                finally:
                    db.session.remove()

        scheduler.add_job(
            func=run_incomplete_rescreen,
            trigger=IntervalTrigger(minutes=15),
            id='incomplete_rescreen',
            name='Incomplete Candidate Rescreen (Every 15 Minutes)',
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=False
        )
        app.logger.info("♻️  Incomplete rescreen job scheduled — runs every 15 minutes when enabled")

    # ── Scout Screening Quality Audit (every 15 minutes, when enabled) ────────
    if is_primary_worker:
        def run_screening_audit():
            """AI quality auditor reviews recent Not Qualified results for scoring errors."""
            with app.app_context():
                try:
                    from models import VettingConfig
                    enabled = VettingConfig.get_value('screening_audit_enabled', 'false').lower() == 'true'
                    if not enabled:
                        return

                    from vetting_audit_service import VettingAuditService
                    svc = VettingAuditService()
                    result = svc.run_audit_cycle(batch_size=20)
                    app.logger.info(
                        f"🔍 Screening audit cycle: {result.get('total_audited', 0)} audited, "
                        f"{result.get('issues_found', 0)} issues, "
                        f"{result.get('revets_triggered', 0)} re-vets"
                    )

                    try:
                        from models import AutomationTask, AutomationLog
                        import json as _json

                        task = AutomationTask.query.filter(
                            AutomationTask.config_json.contains('screening_audit')
                        ).first()
                        if task:
                            issues = result.get('issues_found', 0)
                            revets = result.get('revets_triggered', 0)
                            audited = result.get('total_audited', 0)
                            email_sent = result.get('email_sent', False)
                            if issues > 0 or revets > 0:
                                summary = (
                                    f"Audited {audited} result(s) — {issues} issue(s) found, "
                                    f"{revets} re-vet(s) triggered"
                                )
                            else:
                                summary = f"Audited {audited} result(s) — no issues found"

                            log_details = {
                                'source': 'scheduled',
                                'total_audited': audited,
                                'issues_found': issues,
                                'revets_triggered': revets,
                                'summary': summary,
                            }
                            if email_sent:
                                log_details['email_delivered'] = True
                            elif issues > 0 or revets > 0:
                                log_details['email_delivered'] = False

                            log = AutomationLog(
                                automation_task_id=task.id,
                                status='success',
                                message='Screening Quality Audit (Scheduled)',
                                details_json=_json.dumps(log_details)
                            )
                            db.session.add(log)
                            task.last_run_at = datetime.utcnow()
                            task.run_count = (task.run_count or 0) + 1
                            db.session.commit()
                    except Exception as log_err:
                        app.logger.warning(f"⚠️ Screening audit: could not write run history: {log_err}")

                except Exception as e:
                    app.logger.error(f"❌ Screening audit error: {e}")
                    try:
                        from models import AutomationTask, AutomationLog
                        import json as _json
                        task = AutomationTask.query.filter(
                            AutomationTask.config_json.contains('screening_audit')
                        ).first()
                        if task:
                            log = AutomationLog(
                                automation_task_id=task.id,
                                status='error',
                                message='Screening Quality Audit (Scheduled)',
                                details_json=_json.dumps({'source': 'scheduled', 'error': str(e)})
                            )
                            db.session.add(log)
                            db.session.commit()
                    except Exception:
                        pass
                finally:
                    db.session.remove()

        scheduler.add_job(
            func=run_screening_audit,
            trigger=IntervalTrigger(minutes=15),
            id='screening_quality_audit',
            name='Scout Screening Quality Audit (15 min)',
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=False
        )
        app.logger.info("🔍 Screening quality audit job scheduled — runs every 15 minutes when enabled")

    # ── XML Change Monitor ────────────────────────────────────────────────────
    if is_primary_worker:
        app.logger.info("📧 XML Change Monitor: Auto-notifications DISABLED - notifications now sent only during manual downloads")
