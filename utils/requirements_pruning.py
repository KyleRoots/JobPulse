"""Debounced cleanup of `JobVettingRequirements` rows for absent tearsheet jobs.

Two paths delete requirement rows when a job leaves the active tearsheet set:
`incremental_monitoring_service._log_auto_removal_activity` and
`screening.job_management.JobManagementMixin.sync_requirements_with_active_jobs`.

Both used to delete on the very first miss. In July 2026 that turned into a
money loop: those paths reported the same ~7 jobs as removed every 5 minutes
while the requirements-maintenance path simultaneously saw those jobs as
*active* in the tearsheets, found no requirements row, and re-extracted them
with gpt-5.4. Roughly 88 extractions an hour, about $275/month, indefinitely.

The fix is to require a job to stay absent before its row is dropped. The first
miss only stamps `tearsheet_absent_since`; the delete happens once the job has
been continuously absent for the grace window. Seeing the job active again
clears the stamp. A flapping job therefore never loses its row, and a genuinely
removed job is still cleaned up, just a day later.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional

# One day of continuous absence. Long enough to absorb tearsheet flapping and
# Bullhorn API hiccups, short enough that genuinely removed jobs do not
# accumulate. Cycles run every 5 minutes, so this is ~288 consecutive misses.
ABSENCE_GRACE_HOURS = 24.0

_default_logger = logging.getLogger(__name__)


def mark_or_delete_absent_requirements(
    job_ids: Iterable[int],
    *,
    grace_hours: float = ABSENCE_GRACE_HOURS,
    logger: Optional[logging.Logger] = None,
    source: str = 'unknown',
) -> Dict[str, int]:
    """Stamp absent jobs, deleting only those absent past the grace window.

    Callers are expected to have already excluded rows they want to protect
    (for example rows carrying recruiter edits).

    Args:
        job_ids: Bullhorn job ids observed as absent from active tearsheets.
        grace_hours: How long a job must stay continuously absent before its
            requirements row is deleted.
        logger: Logger for the caller's module, so log lines stay attributable.
        source: Short label naming the calling path, for log correlation.

    Returns:
        Counts under ``marked`` (newly stamped this call) and ``deleted``.
    """
    from app import db
    from models import JobVettingRequirements

    log = logger or _default_logger
    stats = {'marked': 0, 'deleted': 0}

    ids = [int(j) for j in job_ids]
    if not ids:
        return stats

    now = datetime.utcnow()
    cutoff = now - timedelta(hours=grace_hours)

    rows = JobVettingRequirements.query.filter(
        JobVettingRequirements.bullhorn_job_id.in_(ids)
    ).all()

    for row in rows:
        absent_since = row.tearsheet_absent_since
        if absent_since is None:
            row.tearsheet_absent_since = now
            stats['marked'] += 1
        elif absent_since <= cutoff:
            db.session.delete(row)
            stats['deleted'] += 1

    if stats['marked']:
        log.info(
            f"⏳ Requirements cleanup [{source}]: {stats['marked']} job(s) "
            f"newly marked absent — row kept until {grace_hours:.0f}h of "
            f"continuous absence"
        )
    if stats['deleted']:
        log.info(
            f"🧹 Requirements cleanup [{source}]: deleted {stats['deleted']} "
            f"row(s) absent for more than {grace_hours:.0f}h"
        )

    return stats


def clear_absence_marks(active_job_ids: Iterable[int]) -> int:
    """Clear the absence stamp for jobs seen active again.

    Without this a job that briefly disappears would keep its original stamp
    and get deleted once the grace window elapsed, even though it came back.

    Returns:
        Number of rows cleared.
    """
    from app import db
    from models import JobVettingRequirements

    ids = [int(j) for j in active_job_ids]
    if not ids:
        return 0

    cleared = JobVettingRequirements.query.filter(
        JobVettingRequirements.bullhorn_job_id.in_(ids),
        JobVettingRequirements.tearsheet_absent_since.isnot(None),
    ).update(
        {JobVettingRequirements.tearsheet_absent_since: None},
        synchronize_session=False,
    )
    if cleared:
        db.session.commit()
    return cleared
