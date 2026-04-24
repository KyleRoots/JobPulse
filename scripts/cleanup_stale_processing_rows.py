"""Mark orphaned 'processing' CandidateVettingLog rows as 'failed'.

Background
----------
A CandidateVettingLog row is set to status='processing' when the screening
worker begins analysis and to status='completed' (or 'failed') when it finishes.
If the worker crashes mid-flight (NUL-byte flush failures, OOM, gunicorn
restart, etc.) the row can be left stuck on 'processing' forever, which
makes recruiter dashboards inaccurate (in-flight counts never decrement)
and prevents the candidate from being re-screened by retry logic.

This script finds rows that have been stuck on 'processing' for longer
than the configured threshold and marks them 'failed' with a clear
diagnostic message, restoring dashboard accuracy and re-enabling retry.

Usage (CLI)
-----------
    # Dry-run (default) — prints what would be changed, makes no writes.
    python scripts/cleanup_stale_processing_rows.py

    # Apply the cleanup for real.
    python scripts/cleanup_stale_processing_rows.py --apply

    # Override the staleness threshold (default: 24 hours).
    python scripts/cleanup_stale_processing_rows.py --hours 6 --apply

The script reads DATABASE_URL from the environment, so it operates on
whichever database that variable points to (dev locally, prod when run
in the deployment console).

Programmatic use
----------------
The same logic is exposed as ``cleanup_stale_processing(hours, apply_changes)``
so the super-admin trigger route in ``routes/triggers.py`` can reuse it
without shelling out. The function returns a structured dict suitable
for ``jsonify()`` and never raises on the "nothing to do" path.
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)


CLEANUP_MARKER_TEMPLATE = (
    "Stale processing row auto-recovered: worker did not complete "
    "within {hours}h (likely crash, restart, or pre-sanitization "
    "NUL-byte flush failure). Marked 'failed' so dashboards reflect "
    "true in-flight count. NOTE: this script intentionally does NOT "
    "reset ParsedEmail.vetted_at — re-screening these candidates "
    "still requires the standard retry path or a manual re-trigger."
)


def cleanup_stale_processing(hours: int, apply_changes: bool) -> dict:
    """Find (and optionally mark as failed) CandidateVettingLog rows
    that have been stuck on status='processing' for longer than ``hours``.

    Must be called inside an active Flask app_context (the trigger route
    already runs inside one; the CLI wrapper sets one up explicitly).

    Returns a dict shaped like::

        {
            'success': True,
            'hours': int,
            'cutoff_utc': '2026-04-23T21:08:19',
            'count': int,
            'rows': [
                {'id': int, 'bullhorn_candidate_id': str|int|None,
                 'candidate_name': str|None, 'applied_job_id': int|None,
                 'age_hours': float, 'created_at': iso8601 str},
                ...
            ],
            'applied': bool,           # True only if rows were actually written
            'dry_run': bool,           # True when no writes were performed
            'message': str,            # human-readable summary
        }

    On invalid input (``hours <= 0``) returns ``{'success': False,
    'error': '...'}`` and performs no DB work.
    """
    if hours <= 0:
        return {
            'success': False,
            'error': (
                f"hours must be a positive integer (got {hours!r}). Refusing "
                "to operate on rows from 'the future' or rows newer than 'now'."
            ),
        }

    from app import db
    from models import CandidateVettingLog

    cutoff = datetime.utcnow() - timedelta(hours=hours)

    stale = (
        CandidateVettingLog.query
        .filter(
            CandidateVettingLog.status == 'processing',
            CandidateVettingLog.analyzed_at.is_(None),
            CandidateVettingLog.created_at < cutoff,
        )
        .order_by(CandidateVettingLog.created_at.asc())
        .all()
    )

    now = datetime.utcnow()
    rows_payload = [
        {
            'id': row.id,
            'bullhorn_candidate_id': row.bullhorn_candidate_id,
            'candidate_name': row.candidate_name or None,
            'applied_job_id': row.applied_job_id,
            'age_hours': round((now - row.created_at).total_seconds() / 3600, 1),
            'created_at': row.created_at.isoformat(timespec='seconds'),
        }
        for row in stale
    ]

    count = len(stale)
    cutoff_iso = cutoff.isoformat(timespec='seconds')

    if count == 0:
        return {
            'success': True,
            'hours': hours,
            'cutoff_utc': cutoff_iso,
            'count': 0,
            'rows': [],
            'applied': False,
            'dry_run': not apply_changes,
            'message': (
                f"No rows stuck on 'processing' for more than {hours}h. "
                "Nothing to clean up."
            ),
        }

    if not apply_changes:
        return {
            'success': True,
            'hours': hours,
            'cutoff_utc': cutoff_iso,
            'count': count,
            'rows': rows_payload,
            'applied': False,
            'dry_run': True,
            'message': (
                f"Found {count} row(s) stuck on 'processing' for more than "
                f"{hours}h. Re-run with apply=true to mark them 'failed'."
            ),
        }

    marker = CLEANUP_MARKER_TEMPLATE.format(hours=hours)
    for row in stale:
        row.status = 'failed'
        row.error_message = marker

    db.session.commit()

    return {
        'success': True,
        'hours': hours,
        'cutoff_utc': cutoff_iso,
        'count': count,
        'rows': rows_payload,
        'applied': True,
        'dry_run': False,
        'message': f"Marked {count} row(s) as 'failed'. Cleanup complete.",
    }


def _run_cli(hours: int, apply_changes: bool) -> int:
    from app import app

    with app.app_context():
        result = cleanup_stale_processing(hours, apply_changes)

    if not result.get('success'):
        logger.error(result.get('error', 'Unknown error.'))
        return 2

    logger.info(
        "Found %d row(s) stuck on 'processing' for more than %d hour(s) "
        "(cutoff: %s UTC).",
        result['count'], result['hours'], result['cutoff_utc'],
    )

    for row in result['rows']:
        logger.info(
            "  - id=%s candidate=%s (%s) job=%s age=%.1fh",
            row['id'], row['bullhorn_candidate_id'],
            row['candidate_name'] or '?', row['applied_job_id'],
            row['age_hours'],
        )

    if result['count'] == 0:
        logger.info("Nothing to clean up.")
        return 0

    if not result['applied']:
        logger.info("Dry-run complete. Re-run with --apply to mark these rows 'failed'.")
        return 0

    logger.info("Marked %d row(s) as 'failed'. Cleanup complete.", result['count'])
    return 0


# Back-compat shim: the previous CLI exposed ``main(hours, apply_changes)``.
main = _run_cli


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--hours', type=int, default=24,
        help='Mark rows older than this many hours as failed (default: 24).',
    )
    parser.add_argument(
        '--apply', action='store_true',
        help='Actually apply the changes. Without this flag, runs in dry-run mode.',
    )
    args = parser.parse_args()
    sys.exit(_run_cli(args.hours, args.apply))
