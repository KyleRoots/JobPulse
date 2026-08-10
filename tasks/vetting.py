import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def run_candidate_vetting_cycle():
    """Run the AI-powered candidate vetting cycle to analyze new applicants"""
    from app import app
    with app.app_context():
        try:
            from candidate_vetting_service import CandidateVettingService
            from models import VettingConfig

            config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
            if not config or config.setting_value.lower() != 'true':
                return  # Silently skip if disabled

            from utils.environment_runner import for_each_active_environment

            def _run(env):
                env_key = getattr(env, 'key', 'default')
                vetting_service = CandidateVettingService(
                    environment_id=(env.id if env else None)
                )
                summary = vetting_service.run_vetting_cycle()
                if summary.get('status') != 'disabled':
                    app.logger.info(
                        f"Candidate vetting cycle [{env_key}] completed: "
                        f"{summary.get('candidates_processed', 0)} processed, "
                        f"{summary.get('candidates_qualified', 0)} qualified, "
                        f"{summary.get('notifications_sent', 0)} notifications")
                return summary

            for_each_active_environment('candidate_vetting_cycle', _run, app.logger)

        except Exception as e:
            app.logger.error(f"Candidate vetting cycle error: {str(e)}")


def run_retry_failed_screening_notes(batch_size: int = 25) -> dict:
    """Retry Bullhorn Scout notes for completed screens that never got a note.

    Terry Vallo (4674305) pattern (Aug 10 2026): screening completed and
    scored (Location Review / NQ), ParsedEmail was marked vetted, and the
    self-screen cooldown blocked re-detection — but note_created stayed
    False, so Bullhorn showed only the AI Resume Summary. The manual
    /screening/retry-failed-notes endpoint already repairs this; this job
    runs the same repair on a schedule so misses self-heal.

    Also retries recruiter notifications when the note exists (or was just
    written) but notifications_sent is still False — covers Location Review
    and Qualified paths that the UI retry previously limited to is_qualified.

    Notification retries are intentionally limited to a recent analyzed_at
    window. Production has thousands of historical completed rows with
    notifications_sent=False (mostly ordinary NQs); without a time gate the
    first cycles would walk that backlog and risk recruiter email spam.
    """
    from datetime import datetime, timedelta

    from app import app

    summary = {
        'pending': 0,
        'notes_created': 0,
        'notes_failed': 0,
        'notifications_sent': 0,
        'errors': [],
    }

    with app.app_context():
        try:
            from models import CandidateVettingLog, VettingConfig
            from candidate_vetting_service import CandidateVettingService

            config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
            if not config or config.setting_value.lower() != 'true':
                summary['status'] = 'disabled'
                return summary

            batch_size = max(1, min(int(batch_size or 25), 100))
            # Keep note repairs available for older misses, but never fan out
            # recruiter emails for historical notifications_sent=False rows.
            notify_since = datetime.utcnow() - timedelta(hours=24)

            # Notes missing entirely
            missing_note_logs = (
                CandidateVettingLog.query.filter(
                    CandidateVettingLog.status == 'completed',
                    CandidateVettingLog.note_created == False,
                    CandidateVettingLog.is_sandbox != True,
                )
                .order_by(CandidateVettingLog.analyzed_at.desc())
                .limit(batch_size)
                .all()
            )

            # Notes present but recruiter notify never fired (Location Review /
            # Qualified). Cap separately so a backlog of silent NQs cannot
            # starve note repairs. Time-gate to recent screens only.
            missing_notif_logs = (
                CandidateVettingLog.query.filter(
                    CandidateVettingLog.status == 'completed',
                    CandidateVettingLog.note_created == True,
                    CandidateVettingLog.notifications_sent == False,
                    CandidateVettingLog.is_sandbox != True,
                    CandidateVettingLog.analyzed_at >= notify_since,
                    # Only rows that may still need a recruiter email:
                    # qualified OR location-review near-miss (highest_match
                    # near threshold). Broad NQ spam is intentionally skipped.
                    (
                        (CandidateVettingLog.is_qualified == True)
                        | (CandidateVettingLog.highest_match_score >= 65)
                    ),
                )
                .order_by(CandidateVettingLog.analyzed_at.desc())
                .limit(batch_size)
                .all()
            )

            summary['pending'] = len(missing_note_logs) + len(missing_notif_logs)
            if summary['pending'] == 0:
                summary['status'] = 'idle'
                return summary

            vetting_service = CandidateVettingService()

            for log in missing_note_logs:
                try:
                    if vetting_service.create_candidate_note(log):
                        summary['notes_created'] += 1
                        analyzed_at = log.analyzed_at
                        recent_enough = (
                            analyzed_at is not None and analyzed_at >= notify_since
                        )
                        if not log.notifications_sent and recent_enough:
                            try:
                                n = vetting_service.send_recruiter_notifications(log)
                                if n > 0:
                                    summary['notifications_sent'] += 1
                            except Exception as notif_err:
                                logger.error(
                                    "Note retry succeeded but notification failed for "
                                    f"candidate {log.bullhorn_candidate_id}: {notif_err}"
                                )
                    else:
                        summary['notes_failed'] += 1
                        logger.error(
                            "Scheduled note retry failed for candidate "
                            f"{log.bullhorn_candidate_id} vetting_log_id={log.id} "
                            "event=note_retry_failed"
                        )
                except Exception as e:
                    summary['notes_failed'] += 1
                    summary['errors'].append(str(e)[:200])
                    logger.error(
                        f"Scheduled note retry exception for candidate "
                        f"{log.bullhorn_candidate_id}: {e}"
                    )

            for log in missing_notif_logs:
                try:
                    n = vetting_service.send_recruiter_notifications(log)
                    if n > 0:
                        summary['notifications_sent'] += 1
                except Exception as e:
                    summary['errors'].append(str(e)[:200])
                    logger.error(
                        f"Scheduled notification retry exception for candidate "
                        f"{log.bullhorn_candidate_id}: {e}"
                    )

            logger.info(
                "Scheduled screening note retry: "
                f"notes_created={summary['notes_created']}, "
                f"notes_failed={summary['notes_failed']}, "
                f"notifications_sent={summary['notifications_sent']}, "
                f"pending_seen={summary['pending']} "
                "event=note_retry_cycle"
            )
            summary['status'] = 'ok'
            return summary

        except Exception as e:
            logger.error(f"run_retry_failed_screening_notes error: {e}")
            summary['status'] = 'error'
            summary['errors'].append(str(e)[:200])
            return summary


def run_requirements_maintenance():
    """
    Scheduled job (every 5 minutes): keep AI job requirements up to date automatically.

    Two responsibilities:
      A) Re-interpret modified jobs — calls check_and_refresh_changed_jobs() which compares
         Bullhorn dateLastModified vs last_ai_interpretation, then gates AI re-extraction
         on a SHA-256 of the job description text (source_description_hash) so metadata-only
         Bullhorn bumps do not re-burn extraction tokens.
      B) Extract for new jobs — finds any jobs currently in monitored tearsheets that have
         no JobVettingRequirements record yet and extracts requirements via AI.

    In steady state (nothing changed, nothing new) this task makes only lightweight Bullhorn
    bulk-fetch calls and zero AI calls, so the 5-minute frequency is safe.

    THREAD-SAFETY: Runs inside app.app_context() — uses CandidateVettingService which manages
    its own Bullhorn session internally. No direct bh.session.* access here.
    """
    from app import app

    with app.app_context():
        try:
            from models import VettingConfig, JobVettingRequirements
            from candidate_vetting_service import CandidateVettingService

            vetting_enabled = VettingConfig.get_value('vetting_enabled', 'false')
            if str(vetting_enabled).lower() != 'true':
                return

            from utils.environment_runner import for_each_active_environment
            for_each_active_environment(
                'requirements_maintenance',
                lambda env: _run_requirements_maintenance_for_env(
                    env, CandidateVettingService, JobVettingRequirements
                ),
            )

        except Exception as e:
            logger.error(f"run_requirements_maintenance: unexpected error — {e}")


def _run_requirements_maintenance_for_env(env, CandidateVettingService, JobVettingRequirements):
    """Requirements maintenance for a single environment.

    With the default (single) environment this scopes to that environment's id,
    which is the only id present, so the behavior is unchanged.
    """
    env_key = getattr(env, 'key', 'default')
    env_id = env.id if env else None
    svc = CandidateVettingService(environment_id=env_id)

    try:
        mod_results = svc.check_and_refresh_changed_jobs()
        refreshed = mod_results.get('jobs_refreshed', 0)
        if refreshed > 0:
            logger.info(
                f"Requirements maintenance [{env_key}][modified]: {refreshed} job(s) re-interpreted, "
                f"{mod_results.get('jobs_skipped', 0)} unchanged"
            )
    except Exception as mod_err:
        logger.error(f"Requirements maintenance [{env_key}][modified]: error — {mod_err}")

    try:
        active_jobs = svc.get_active_jobs_from_tearsheets()
        if not active_jobs:
            return

        # Defense in depth: Bullhorn's Search index can retain stale
        # tearsheet associations after a closed/on-hold job was removed.
        # Never spend AI extraction tokens on an ineligible job even if that
        # stale membership slips past the lower-level reconciliation.
        from utils.job_status import is_job_eligible
        active_jobs = [job for job in active_jobs if is_job_eligible(job)]
        if not active_jobs:
            return

        # These jobs are demonstrably active, so drop any absence stamp the
        # auto-removal path left on them. The two paths disagree about the same
        # jobs every cycle; this is the side that has just proven them present.
        from utils.requirements_pruning import clear_absence_marks
        clear_absence_marks(
            [int(j['id']) for j in active_jobs if j.get('id')]
        )

        existing_query = JobVettingRequirements.query.filter(
            JobVettingRequirements.ai_interpreted_requirements.isnot(None)
        )
        if env_id is not None:
            existing_query = existing_query.filter(
                JobVettingRequirements.environment_id == env_id
            )
        existing_ids = set(
            r.bullhorn_job_id for r in
            existing_query.with_entities(JobVettingRequirements.bullhorn_job_id).all()
        )

        new_jobs = [
            j for j in active_jobs
            if j.get('id') and int(j['id']) not in existing_ids
        ]

        if not new_jobs:
            return

        logger.info(f"Requirements maintenance [{env_key}][new]: {len(new_jobs)} job(s) found without requirements — extracting...")

        jobs_payload = []
        for job in new_jobs:
            job_address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
            job_city = job_address.get('city', '')
            job_state = job_address.get('state', '')
            job_country = job_address.get('countryName', '') or job_address.get('country', '')
            job_location = ', '.join(filter(None, [job_city, job_state, job_country]))

            on_site_value = job.get('onSite', 1)
            if isinstance(on_site_value, list):
                on_site_value = on_site_value[0] if on_site_value else 1
            if isinstance(on_site_value, (int, float)):
                work_type_map = {1: 'On-site', 2: 'Hybrid', 3: 'Remote'}
                job_work_type = work_type_map.get(int(on_site_value), 'On-site')
            else:
                onsite_str = str(on_site_value).lower().strip() if on_site_value else ''
                if 'remote' in onsite_str or onsite_str == 'offsite':
                    job_work_type = 'Remote'
                elif 'hybrid' in onsite_str:
                    job_work_type = 'Hybrid'
                else:
                    job_work_type = 'On-site'

            jobs_payload.append({
                'id': job.get('id'),
                'title': job.get('title', ''),
                'description': job.get('publicDescription', '') or job.get('description', ''),
                'location': job_location,
                'work_type': job_work_type,
            })

        extract_results = svc.extract_requirements_for_jobs(jobs_payload)
        logger.info(
            f"Requirements maintenance [{env_key}][new]: extracted={extract_results.get('extracted', 0)}, "
            f"skipped={extract_results.get('skipped', 0)}, failed={extract_results.get('failed', 0)}"
        )

    except Exception as new_err:
        logger.error(f"Requirements maintenance [{env_key}][new]: error — {new_err}")
