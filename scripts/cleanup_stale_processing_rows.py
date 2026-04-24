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

Usage
-----
    # Dry-run (default) — prints what would be changed, makes no writes.
    python scripts/cleanup_stale_processing_rows.py

    # Apply the cleanup for real.
    python scripts/cleanup_stale_processing_rows.py --apply

    # Override the staleness threshold (default: 24 hours).
    python scripts/cleanup_stale_processing_rows.py --hours 6 --apply

The script reads DATABASE_URL from the environment, so it operates on
whichever database that variable points to (dev locally, prod when run
in the deployment console).
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

# Allow `python scripts/cleanup_stale_processing_rows.py` from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)


def main(hours: int, apply_changes: bool) -> int:
    if hours <= 0:
        logger.error("--hours must be a positive integer (got %r). Refusing to "
                     "operate on rows from 'the future' or rows newer than 'now'.",
                     hours)
        return 2

    from app import app, db
    from models import CandidateVettingLog

    cutoff = datetime.utcnow() - timedelta(hours=hours)

    with app.app_context():
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

        count = len(stale)
        logger.info(
            "Found %d row(s) stuck on 'processing' for more than %d hour(s) "
            "(cutoff: %s UTC).",
            count, hours, cutoff.isoformat(timespec='seconds'),
        )

        if count == 0:
            logger.info("Nothing to clean up.")
            return 0

        for row in stale:
            age_hours = (datetime.utcnow() - row.created_at).total_seconds() / 3600
            logger.info(
                "  - id=%s candidate=%s (%s) job=%s age=%.1fh",
                row.id, row.bullhorn_candidate_id,
                row.candidate_name or '?', row.applied_job_id, age_hours,
            )

        if not apply_changes:
            logger.info("Dry-run complete. Re-run with --apply to mark these rows 'failed'.")
            return 0

        marker = (
            "Stale processing row auto-recovered: worker did not complete "
            f"within {hours}h (likely crash, restart, or pre-sanitization "
            "NUL-byte flush failure). Marked 'failed' so dashboards reflect "
            "true in-flight count. NOTE: this script intentionally does NOT "
            "reset ParsedEmail.vetted_at — re-screening these candidates "
            "still requires the standard retry path or a manual re-trigger."
        )

        for row in stale:
            row.status = 'failed'
            row.error_message = marker

        db.session.commit()
        logger.info("Marked %d row(s) as 'failed'. Cleanup complete.", count)
        return 0


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
    sys.exit(main(args.hours, args.apply))
