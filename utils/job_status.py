"""Shared job-status eligibility helpers.

Single source of truth for which Bullhorn JobOrder statuses are considered
ineligible for sponsored feeds, monitoring, and screening injection. All
call-sites must compare lowercase status strings against `INELIGIBLE_STATUSES`.
"""

from typing import Any, Mapping

INELIGIBLE_STATUSES = frozenset({
    'qualifying',
    'hold - covered',
    'hold - client hold',
    'offer out',
    'filled',
    'lost - competition',
    'lost - filled internally',
    'lost - funding',
    'canceled',
    'placeholder/ mpc',
    'archive',
    'closed',
    'archived',
    'covered',
    'lost',
})


def is_job_eligible(job: Mapping[str, Any]) -> bool:
    """Return True only if the Bullhorn job is open AND not in a closed/dead status.

    A job is considered ineligible when EITHER:
      * `isOpen` is false / falsy, OR
      * `status` (case-insensitive) is in `INELIGIBLE_STATUSES`.

    This mirrors the dashboard filter so screening, monitoring, and display
    can never disagree about whether a job is "open".
    """
    if not job:
        return False

    is_open = job.get('isOpen')
    if is_open is False or str(is_open).strip().lower() in ('false', 'closed', '0', 'none', ''):
        return False

    status = (job.get('status') or '').strip().lower()
    if status in INELIGIBLE_STATUSES:
        return False

    return True
