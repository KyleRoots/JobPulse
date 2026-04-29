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

            vetting_service = CandidateVettingService()
            summary = vetting_service.run_vetting_cycle()

            if summary.get('status') != 'disabled':
                app.logger.info(f"Candidate vetting cycle completed: {summary.get('candidates_processed', 0)} processed, "
                              f"{summary.get('candidates_qualified', 0)} qualified, {summary.get('notifications_sent', 0)} notifications")

        except Exception as e:
            app.logger.error(f"Candidate vetting cycle error: {str(e)}")


def run_requirements_maintenance():
    """
    Scheduled job (every 5 minutes): keep AI job requirements up to date automatically.

    Two responsibilities:
      A) Re-interpret modified jobs — calls check_and_refresh_changed_jobs() which compares
         Bullhorn dateLastModified vs last_ai_interpretation and re-runs AI extraction
         for any job whose description has changed since the last interpretation.
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

            svc = CandidateVettingService()

            try:
                mod_results = svc.check_and_refresh_changed_jobs()
                refreshed = mod_results.get('jobs_refreshed', 0)
                if refreshed > 0:
                    logger.info(
                        f"Requirements maintenance [modified]: {refreshed} job(s) re-interpreted, "
                        f"{mod_results.get('jobs_skipped', 0)} unchanged"
                    )
            except Exception as mod_err:
                logger.error(f"Requirements maintenance [modified]: error — {mod_err}")

            try:
                active_jobs = svc.get_active_jobs_from_tearsheets()
                if not active_jobs:
                    return

                existing_ids = set(
                    r.bullhorn_job_id for r in
                    JobVettingRequirements.query.filter(
                        JobVettingRequirements.ai_interpreted_requirements.isnot(None)
                    ).with_entities(JobVettingRequirements.bullhorn_job_id).all()
                )

                new_jobs = [
                    j for j in active_jobs
                    if j.get('id') and int(j['id']) not in existing_ids
                ]

                if not new_jobs:
                    return

                logger.info(f"Requirements maintenance [new]: {len(new_jobs)} job(s) found without requirements — extracting...")

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
                    f"Requirements maintenance [new]: extracted={extract_results.get('extracted', 0)}, "
                    f"skipped={extract_results.get('skipped', 0)}, failed={extract_results.get('failed', 0)}"
                )

            except Exception as new_err:
                logger.error(f"Requirements maintenance [new]: error — {new_err}")

        except Exception as e:
            logger.error(f"run_requirements_maintenance: unexpected error — {e}")
