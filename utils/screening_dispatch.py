"""
Screening Dispatch Helper

Single entry point for the manual-trigger flows (Run Now, Re-screen Recent,
Start Fresh, Process Backlog) to push a candidate-vetting cycle onto the
existing in-process APScheduler instead of running it inline on the
gunicorn request thread.

Why this exists
---------------
Before this helper, the four "kick off a screening cycle" routes called
`vetting_service.run_vetting_cycle()` synchronously on the request worker.
At ~5-15s per candidate, any moderately-sized batch could exceed the
gunicorn `--timeout=300` cap and SIGKILL the worker mid-batch — silently
truncating screening with no clean error.

The fix routes those routes through this helper, which advances the
already-registered periodic vetting job (`id='candidate_vetting_cycle'`,
runs every 1 minute) to fire immediately. The cycle then runs in
APScheduler's own background thread, completely independent of the
gunicorn worker timeout. The recruiter's request returns in <1s with a
"Screening started — watch the dashboard" toast, and the M2 health
dashboard's in-flight-vetting tile reflects progress.

Architectural notes
-------------------
- We use `scheduler.modify_job(next_run_time=now)` instead of
  `scheduler.add_job(...)` because the periodic job already exists.
  Multiple rapid clicks converge harmlessly: each call just re-points
  next_run_time to the current moment. APScheduler's existing
  `coalesce=False` setting on the job ensures it doesn't drop fires.
- If the periodic job is not registered (e.g. on a secondary worker
  process where `is_primary_worker` was False), we fall back to
  registering a one-shot job with the same callable. That one-shot
  job uses a fixed ID so duplicate clicks don't queue duplicate
  cycles — APScheduler raises `ConflictingIdError` on duplicate IDs
  and we treat that as "already queued" success.
- The helper never raises. It returns a result dict so callers can
  flash an appropriate user message regardless of outcome.

Return contract
---------------
Returns a dict shaped:
    {
        'enqueued': bool,        # True if the cycle is now scheduled
        'mode': str,             # 'advanced' | 'one_shot' | 'noop' | 'error'
        'reason': str,           # human-friendly reason string
        'reason_code': str,      # short machine-readable code
    }

Reason codes:
    'job_advanced'    → existing periodic job's next run pulled to now
    'one_shot_added'  → fallback one-shot job registered
    'already_queued'  → one-shot job already pending; click ignored
    'scheduler_down'  → scheduler not running; cycle will run on next
                         scheduled tick when scheduler comes back
    'error'           → unexpected exception (logged); cycle not enqueued
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


PERIODIC_JOB_ID = 'candidate_vetting_cycle'
ONE_SHOT_JOB_ID = 'candidate_vetting_cycle_oneshot'


def enqueue_vetting_now(reason: str = 'manual') -> Dict[str, Any]:
    """Push a candidate-vetting cycle onto the background scheduler.

    Args:
        reason: Short label describing what triggered this enqueue.
                Used only for logging — does not affect scheduling.

    Returns:
        Result dict (see module docstring for contract).
    """
    try:
        # Lazy import: routes/utils → app would be circular at module load.
        from app import scheduler
    except Exception as exc:  # noqa: BLE001
        logger.error(f"enqueue_vetting_now: cannot import scheduler ({exc})")
        return {
            'enqueued': False,
            'mode': 'error',
            'reason': 'Scheduler unavailable.',
            'reason_code': 'error',
        }

    if scheduler is None or not getattr(scheduler, 'running', False):
        logger.warning(
            f"enqueue_vetting_now({reason}): scheduler is not running; "
            "cycle will run on the next scheduled tick when scheduler resumes."
        )
        return {
            'enqueued': False,
            'mode': 'noop',
            'reason': (
                'Background scheduler is not running. Screening will '
                'resume automatically once the scheduler restarts.'
            ),
            'reason_code': 'scheduler_down',
        }

    # Path 1: the periodic job exists (primary worker) — advance it to now.
    existing = scheduler.get_job(PERIODIC_JOB_ID)
    if existing is not None:
        try:
            scheduler.modify_job(
                PERIODIC_JOB_ID,
                next_run_time=datetime.now(),
            )
            logger.info(
                f"🚀 enqueue_vetting_now({reason}): advanced periodic job "
                f"'{PERIODIC_JOB_ID}' to fire immediately."
            )
            return {
                'enqueued': True,
                'mode': 'advanced',
                'reason': 'Screening started in the background.',
                'reason_code': 'job_advanced',
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"enqueue_vetting_now({reason}): modify_job failed ({exc}); "
                "falling through to one-shot path."
            )
            # fall through to Path 2

    # Path 2: register a one-shot job. Fixed ID makes rapid duplicate
    # clicks idempotent — APScheduler raises on duplicate IDs.
    try:
        from tasks import run_candidate_vetting_cycle
        scheduler.add_job(
            func=run_candidate_vetting_cycle,
            trigger='date',
            run_date=datetime.now(),
            id=ONE_SHOT_JOB_ID,
            name='AI Candidate Vetting Cycle (one-shot manual)',
            replace_existing=False,
            misfire_grace_time=300,
            coalesce=True,
        )
        logger.info(
            f"🚀 enqueue_vetting_now({reason}): registered one-shot job "
            f"'{ONE_SHOT_JOB_ID}'."
        )
        return {
            'enqueued': True,
            'mode': 'one_shot',
            'reason': 'Screening started in the background.',
            'reason_code': 'one_shot_added',
        }
    except Exception as exc:  # noqa: BLE001
        # APScheduler raises ConflictingIdError when the same job ID is
        # already pending. We classify that as success: the cycle is on
        # its way; the duplicate click is harmless.
        exc_name = type(exc).__name__
        if exc_name == 'ConflictingIdError' or 'conflict' in str(exc).lower():
            logger.info(
                f"enqueue_vetting_now({reason}): one-shot job already "
                "queued — duplicate click ignored."
            )
            return {
                'enqueued': True,
                'mode': 'one_shot',
                'reason': 'Screening is already queued and will start shortly.',
                'reason_code': 'already_queued',
            }

        logger.error(
            f"enqueue_vetting_now({reason}): add_job failed ({exc_name}: {exc})"
        )
        return {
            'enqueued': False,
            'mode': 'error',
            'reason': f'Could not start screening: {exc}',
            'reason_code': 'error',
        }
