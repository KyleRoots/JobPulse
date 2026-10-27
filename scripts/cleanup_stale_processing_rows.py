"""Preview stale 'processing' CandidateVettingLog rows; recover only explicitly
selected ones.

Background
----------
A CandidateVettingLog row is set to status='processing' when the screening
worker begins analysis and to status='completed' (or 'failed') when it finishes.
If the worker crashes mid-flight the row can be left stuck on 'processing',
which makes recruiter dashboards inaccurate and blocks re-screening. Age
alone cannot tell a dead worker from a live one behind a stalled connection,
so nothing here changes rows by age: the preview lists candidates, and only
row ids an operator explicitly selects are recovered, one at a time, through
the shared explicit-recovery primitive ``recover_expired_lease`` (candidate
run lock, still-'processing' and no-heartbeat recheck, partial matches kept,
at most one ParsedEmail re-queued, audits released).

Usage (CLI)
-----------
    # Dry-run only — prints the stale rows, makes no writes.
    python scripts/cleanup_stale_processing_rows.py [--hours 6]

The CLI never writes; ``--apply`` is refused. Recovery runs only through the
admin-guarded route ``POST /api/trigger/cleanup-stale-vetting`` with
``apply: true`` and an explicit ``ids`` list.

Programmatic use
----------------
``cleanup_stale_processing(hours, apply_changes, log_ids=None)`` returns a
structured dict suitable for ``jsonify()``. With ``apply_changes`` it refuses
(no DB work) unless ``log_ids`` is a valid explicit id list.
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


MAX_EXPLICIT_IDS = 50

RECOVERY_TRADEOFF = (
    "Recovered rows without job matches were removed and their email "
    "re-queued; rows with partial matches were marked 'failed' with the "
    "matches kept. If a recovered row's worker was in fact still running it "
    "can no longer commit, and any paid calls it already made are not undone."
)


def parse_explicit_log_ids(raw):
    """Returns ``(ids, None)`` for a non-empty list of positive integer ids
    (duplicates dropped, order kept, at most ``MAX_EXPLICIT_IDS``), else
    ``(None, error)``."""
    if not isinstance(raw, list) or not raw:
        return None, (
            "Field 'ids' must be a non-empty JSON list of vetting log ids to "
            "recover; nothing is recovered without explicit ids."
        )
    ids = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None, f"Field 'ids' must contain only positive integers, not {value!r}."
        if value not in ids:
            ids.append(value)
    if len(ids) > MAX_EXPLICIT_IDS:
        return None, f"At most {MAX_EXPLICIT_IDS} ids per request (got {len(ids)})."
    return ids, None


def cleanup_stale_processing(hours: int, apply_changes: bool, log_ids=None) -> dict:
    """Preview CandidateVettingLog rows stuck on status='processing' for
    longer than ``hours``; with ``apply_changes``, recover only ``log_ids``.

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

    With ``apply_changes`` the dict instead carries ``requested_ids`` and
    ``recovered`` / ``deferred`` / ``no_change`` lists; ``count`` is the
    number recovered. Each id is recovered only if it is a 'processing'
    row older than ``hours`` and, rechecked under the candidate run lock by
    ``recover_expired_lease``, still 'processing' with no heartbeat since
    the stricter of the ``hours`` cutoff and the lease timeout.

    On invalid input (``hours <= 0``, or ``apply_changes`` without valid
    ``log_ids``) returns ``{'success': False, 'error': '...'}`` and performs
    no DB work.
    """
    if hours <= 0:
        return {
            'success': False,
            'error': (
                f"hours must be a positive integer (got {hours!r}). Refusing "
                "to operate on rows from 'the future' or rows newer than 'now'."
            ),
            'applied': False,
        }

    if apply_changes:
        ids, error = parse_explicit_log_ids(log_ids)
        if error:
            return {'success': False, 'error': error, 'applied': False, 'dry_run': False}
        return _recover_explicit(hours, ids)

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
            'dry_run': True,
            'message': (
                f"Dry run, no changes made: no rows stuck on 'processing' for "
                f"more than {hours}h."
            ),
        }

    db.session.rollback()
    return {
        'success': True,
        'hours': hours,
        'cutoff_utc': cutoff_iso,
        'count': count,
        'rows': rows_payload,
        'applied': False,
        'dry_run': True,
        'message': (
            f"Dry run, no changes made: found {count} row(s) stuck on "
            f"'processing' for more than {hours}h. Their workers may still be "
            "alive; to recover selected rows re-submit with apply=true and "
            f"their ids (at most {MAX_EXPLICIT_IDS})."
        ),
    }


def _recover_explicit(hours: int, ids: list) -> dict:
    from app import db
    from models import CandidateVettingLog
    from vetting_audit_service.run_claim import (
        LEASE_TIMEOUT_MINUTES, CandidateLockUnavailable, recover_expired_lease,
    )

    now = datetime.utcnow()
    cutoff = now - timedelta(hours=hours)
    expired_before = min(cutoff, now - timedelta(minutes=LEASE_TIMEOUT_MINUTES))
    eligible = {
        row_id for (row_id,) in db.session.query(CandidateVettingLog.id).filter(
            CandidateVettingLog.id.in_(ids),
            CandidateVettingLog.status == 'processing',
            CandidateVettingLog.analyzed_at.is_(None),
            CandidateVettingLog.created_at < cutoff,
        )
    }
    db.session.rollback()

    recovered, deferred, no_change = [], [], []
    for log_id in ids:
        if log_id not in eligible:
            no_change.append({'id': log_id, 'reason': (
                f"not a 'processing' row older than {hours}h")})
            continue
        try:
            outcome = recover_expired_lease(log_id, expired_before)
        except CandidateLockUnavailable:
            db.session.rollback()
            deferred.append({'id': log_id, 'reason': 'candidate run lock unavailable; retry later'})
            continue
        except Exception as e:
            db.session.rollback()
            logger.exception("Explicit recovery of vetting log %s failed", log_id)
            deferred.append({'id': log_id, 'reason': f'recovery error ({type(e).__name__}); nothing changed'})
            continue
        if outcome is None:
            no_change.append({'id': log_id, 'reason': (
                "rechecked under the candidate run lock: no longer 'processing' "
                "or heartbeat newer than the cutoff")})
        else:
            recovered.append(dict(outcome, id=log_id))

    return {
        'success': True,
        'hours': hours,
        'cutoff_utc': cutoff.isoformat(timespec='seconds'),
        'expired_before_utc': expired_before.isoformat(timespec='seconds'),
        'requested_ids': ids,
        'recovered': recovered,
        'deferred': deferred,
        'no_change': no_change,
        'count': len(recovered),
        'rows': [],
        'applied': bool(recovered),
        'dry_run': False,
        'message': (
            f"Recovered {len(recovered)}, deferred {len(deferred)}, unchanged "
            f"{len(no_change)} of {len(ids)} requested row(s). "
            + (RECOVERY_TRADEOFF if recovered else "No rows were changed.")
        ),
    }


def _run_cli(hours: int, apply_changes: bool) -> int:
    if apply_changes:
        logger.error(
            "--apply is not supported: this CLI is dry-run only. Recover "
            "explicitly selected rows through the admin route "
            "POST /api/trigger/cleanup-stale-vetting (apply=true, ids=[...])."
        )
        return 2

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

    logger.info("Dry-run complete; no changes were made.")
    return 0


# Back-compat shim: the previous CLI exposed ``main(hours, apply_changes)``.
main = _run_cli


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--hours', type=int, default=24,
        help="List rows stuck on 'processing' longer than this (default: 24).",
    )
    parser.add_argument(
        '--apply', action='store_true',
        help='Refused: this CLI never writes; use the admin route with explicit ids.',
    )
    args = parser.parse_args()
    sys.exit(_run_cli(args.hours, args.apply))
