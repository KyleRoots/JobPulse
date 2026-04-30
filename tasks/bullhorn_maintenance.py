import json
import time
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def start_scheduler_manual():
    """Manually start the scheduler and trigger monitoring"""
    from app import app, lazy_start_scheduler, process_bullhorn_monitors, scheduler
    from extensions import db
    from flask import jsonify
    try:
        from models import BullhornMonitor

        scheduler_started = lazy_start_scheduler()

        if scheduler_started:
            monitors = BullhornMonitor.query.filter_by(is_active=True).all()
            current_time = datetime.utcnow()
            for monitor in monitors:
                monitor.last_check = current_time
                monitor.next_check = current_time + timedelta(minutes=2)
            db.session.commit()

            try:
                process_bullhorn_monitors()
                message = f"Scheduler started. {len(monitors)} monitors activated with 2-minute intervals."
            except Exception as e:
                message = f"Scheduler started but monitoring failed: {str(e)}"
        else:
            message = "Scheduler was already running or failed to start"

        return jsonify({
            'success': True,
            'message': message,
            'scheduler_running': scheduler.running
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def cleanup_linkedin_source():
    """
    Hourly scheduled job: find any Bullhorn Candidate records whose source contains
    a LinkedIn variant (Linkedin, linkedin, LINKEDIN, etc.) but is NOT already
    "LinkedIn Job Board", and update them to "LinkedIn Job Board".

    THREAD-SAFETY: Uses standalone requests.get/post — never bh.session.* — because
    this runs in a background APScheduler thread and requests.Session is not thread-safe.
    """
    from app import app
    import requests as _requests

    with app.app_context():
        try:
            from bullhorn_service import BullhornService

            bh = BullhornService()
            if not bh.authenticate():
                logger.warning("linkedin_source_cleanup: Bullhorn authentication failed — skipping run")
                return

            base_url = bh.base_url
            rest_token = bh.rest_token
            headers = {
                "BhRestToken": rest_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            search_url = f"{base_url}search/Candidate"
            query = 'source:LinkedIn AND -source:"LinkedIn Job Board"'

            cycle_started_at = datetime.utcnow()
            count_resp = _requests.get(search_url, headers=headers, params={
                "query": query, "fields": "id", "count": 1, "start": 0,
            }, timeout=30)
            count_resp.raise_for_status()
            total = count_resp.json().get("total", 0)

            if total == 0:
                logger.info(
                    "linkedin_source_cleanup: [diagnostic] 0 records need updating "
                    "— nothing to do (query=%r)", query
                )
                return

            logger.info(
                "linkedin_source_cleanup: [diagnostic] found %s records to update "
                "(query=%r, batch_size=500)", f"{total:,}", query
            )

            succeeded = 0
            failed = 0
            start = 0
            batch_size = 500

            while start < total:
                fetch_resp = _requests.get(search_url, headers=headers, params={
                    "query": query, "fields": "id",
                    "count": batch_size, "start": start,
                }, timeout=30)
                fetch_resp.raise_for_status()
                record_ids = [r["id"] for r in fetch_resp.json().get("data", [])]

                if not record_ids:
                    break

                for record_id in record_ids:
                    try:
                        upd = _requests.post(
                            f"{base_url}entity/Candidate/{record_id}",
                            headers=headers,
                            json={"source": "LinkedIn Job Board"},
                            timeout=15,
                        )
                        body = {}
                        try:
                            body = upd.json()
                        except Exception:
                            pass
                        if (upd.status_code in (200, 201)
                                and not body.get("errorCode")
                                and not body.get("errors")
                                and (body.get("changeType") == "UPDATE"
                                     or body.get("changedEntityId") is not None)):
                            succeeded += 1
                        else:
                            failed += 1
                    except Exception as rec_err:
                        failed += 1
                        logger.warning(f"linkedin_source_cleanup: error on ID {record_id}: {rec_err}")

                start += len(record_ids)
                time.sleep(0.05)

            duration_s = (datetime.utcnow() - cycle_started_at).total_seconds()
            logger.info(
                "linkedin_source_cleanup: [diagnostic] complete — %s updated, %s failed, "
                "duration=%.1fs (each successful update touches dateLastModified, which "
                "may surface that candidate in the next owner_reassignment 30-min window)",
                f"{succeeded:,}", f"{failed:,}", duration_s
            )

        except Exception as e:
            logger.error(f"linkedin_source_cleanup: unexpected error — {e}")


def enforce_tearsheet_jobs_public():
    """
    Scheduled job (every 30 minutes): find all jobs in monitored tearsheets where
    isPublic is not true and set them to public.

    Runs automatically so any job added to a tearsheet without the isPublic flag
    set correctly is corrected within the next cycle — no manual intervention needed.

    THREAD-SAFETY: Uses standalone requests.get/post — never bh.session.* — because
    this runs in a background APScheduler thread and requests.Session is not thread-safe.
    """
    from app import app
    import requests as _requests

    from utils.job_status import INELIGIBLE_STATUSES

    with app.app_context():
        try:
            from models import BullhornMonitor
            from bullhorn_service import BullhornService

            monitors = BullhornMonitor.query.filter_by(is_active=True).all()
            tearsheet_ids = [m.tearsheet_id for m in monitors if m.tearsheet_id]
            snapshot_monitors = [m for m in monitors if not m.tearsheet_id]

            if not tearsheet_ids and not snapshot_monitors:
                logger.info("enforce_tearsheet_jobs_public: no active monitors configured — skipping")
                return

            bh = BullhornService()
            if not bh.authenticate():
                logger.warning("enforce_tearsheet_jobs_public: Bullhorn authentication failed — skipping run")
                return

            base_url = bh.base_url
            rest_token = bh.rest_token
            headers = {
                "BhRestToken": rest_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            snapshot_job_ids = set()
            if snapshot_monitors:
                import json as _json
                for sm in snapshot_monitors:
                    if sm.last_job_snapshot:
                        try:
                            snap = _json.loads(sm.last_job_snapshot)
                            for j in snap:
                                jid = j.get('id') if isinstance(j, dict) else j
                                if jid:
                                    snapshot_job_ids.add(int(jid))
                        except Exception:
                            pass
                if snapshot_job_ids:
                    logger.info(f"enforce_tearsheet_jobs_public: {len(snapshot_job_ids)} job(s) from snapshot-based monitors")

            search_url = f"{base_url}search/JobOrder"
            total = 0
            all_jobs = []

            if tearsheet_ids:
                tearsheet_clause = " OR ".join(str(tid) for tid in tearsheet_ids)
                query = f"tearsheets.id:({tearsheet_clause}) AND isPublic:0 AND NOT isDeleted:true"

                count_resp = _requests.get(search_url, headers=headers, params={
                    "query": query, "fields": "id", "count": 1, "start": 0,
                }, timeout=30)
                count_resp.raise_for_status()
                total = count_resp.json().get("total", 0)

            if snapshot_job_ids:
                for sjid in snapshot_job_ids:
                    try:
                        entity_resp = _requests.get(
                            f"{base_url}entity/JobOrder/{sjid}",
                            headers=headers,
                            params={"fields": "id,status,isPublic"},
                            timeout=15,
                        )
                        if entity_resp.status_code == 200:
                            jdata = entity_resp.json().get("data", {})
                            if jdata and not jdata.get("isPublic", True):
                                all_jobs.append(jdata)
                    except Exception:
                        pass

            if total == 0 and not all_jobs:
                logger.info("enforce_tearsheet_jobs_public: all tearsheet jobs are already public — nothing to do")
                _store_enforce_result(0, [], app)
                return

            if total > 0:
                logger.info(f"enforce_tearsheet_jobs_public: found {total:,} non-public job(s) across tearsheets {tearsheet_ids}")
                start = 0
                batch_size = 200
                while start < total:
                    fetch_resp = _requests.get(search_url, headers=headers, params={
                        "query": query, "fields": "id,status",
                        "count": batch_size, "start": start,
                    }, timeout=30)
                    fetch_resp.raise_for_status()
                    page = fetch_resp.json().get("data", [])
                    if not page:
                        break
                    all_jobs.extend(page)
                    start += len(page)
                    if len(page) < batch_size:
                        break
            if all_jobs and not tearsheet_ids:
                logger.info(f"enforce_tearsheet_jobs_public: found {len(all_jobs)} non-public job(s) from snapshot monitors")

            seen_ids = set()
            jobs_to_update = []
            for job in all_jobs:
                job_id = job.get("id")
                status = (job.get("status") or "").strip().lower()
                if job_id and job_id not in seen_ids and status not in INELIGIBLE_STATUSES:
                    seen_ids.add(job_id)
                    jobs_to_update.append(job_id)

            skipped = len(all_jobs) - len(jobs_to_update)
            if not jobs_to_update:
                logger.info(f"enforce_tearsheet_jobs_public: all {len(all_jobs)} non-public jobs have ineligible statuses — skipping updates")
                _store_enforce_result(0, [], app)
                return

            logger.info(f"enforce_tearsheet_jobs_public: will update {len(jobs_to_update)} job(s) (skipped {skipped} with ineligible status)")

            succeeded = 0
            failed = 0
            sample_updated = []

            for job_id in jobs_to_update:
                try:
                    upd = _requests.post(
                        f"{base_url}entity/JobOrder/{job_id}",
                        headers=headers,
                        json={"isPublic": 1},
                        timeout=15,
                    )
                    body = {}
                    try:
                        body = upd.json()
                    except Exception:
                        pass
                    if (upd.status_code in (200, 201)
                            and not body.get("errorCode")
                            and not body.get("errors")
                            and (body.get("changeType") == "UPDATE"
                                 or body.get("changedEntityId") is not None)):
                        succeeded += 1
                        if len(sample_updated) < 5:
                            sample_updated.append(job_id)
                    else:
                        failed += 1
                        logger.warning(
                            f"enforce_tearsheet_jobs_public: unexpected response for job {job_id}: "
                            f"HTTP {upd.status_code} — {body}"
                        )
                except Exception as rec_err:
                    failed += 1
                    logger.warning(f"enforce_tearsheet_jobs_public: error on job {job_id}: {rec_err}")

                time.sleep(0.05)

            logger.info(
                f"enforce_tearsheet_jobs_public: complete — {succeeded} updated, {failed} failed"
                + (f" | sample IDs: {sample_updated}" if sample_updated else "")
            )

            _store_enforce_result(succeeded, sample_updated, app)

        except Exception as e:
            logger.error(f"enforce_tearsheet_jobs_public: unexpected error — {e}")
            _store_enforce_result(0, [], app)


def _store_enforce_result(succeeded, sample_ids, app):
    try:
        from models import GlobalSettings
        from app import db
        result = json.dumps({
            "succeeded": succeeded,
            "sample_ids": sample_ids,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        setting = GlobalSettings.query.filter_by(setting_key='enforce_public_last_result').first()
        if setting:
            setting.setting_value = result
        else:
            setting = GlobalSettings(setting_key='enforce_public_last_result', setting_value=result)
            db.session.add(setting)
        db.session.commit()
    except Exception as e:
        logger.warning(f"enforce_tearsheet_jobs_public: could not store result — {e}")
