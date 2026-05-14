"""
One-off remediation script: reclassify stuck Quality-Auditor revet rows.

Background
----------
``vetting_audit_service._trigger_revet`` resets ``parsed_email.vetted_at = None``
and deletes the existing ``CandidateVettingLog`` so the next vetting cycle
re-scores the candidate. But ``screening/detection.py`` filters the backlog by
``vetting_cutoff_date`` — so when the candidate's parsed_email is older than
the cutoff, the next cycle silently skips them and the audit row stays as
``action_taken='revet_triggered'`` with ``revet_new_score=NULL`` indefinitely.

The companion code change (vetting_audit_service/revet_mixin.py:
``_check_pre_cutoff_eligibility``) prevents NEW orphans by writing
``action_taken='revet_skipped_pre_cutoff'`` when the parsed_email predates
the cutoff. This script back-fills the EXISTING orphans the same way so the
``/admin/ai-cost/auditor`` funnel reflects reality.

Selection criteria (each row must satisfy ALL):
  * ``action_taken = 'revet_triggered'``
  * ``revet_new_score IS NULL``
  * ``created_at < NOW() - INTERVAL '24 hours'`` (skip rows still in flight)
  * Candidate has NO completed parsed_email at-or-after the cutoff
    (i.e. the next vetting cycle truly cannot rescore them)

Safe to re-run: only updates rows that still match the criteria. Idempotent —
already-reclassified rows are skipped.

Usage:
    # Preview what would be updated
    python -m scripts.backfill_stuck_revets --dry-run

    # Execute (default: all matching rows)
    python -m scripts.backfill_stuck_revets

    # Limit batch size (useful for staged rollout)
    python -m scripts.backfill_stuck_revets --limit 10
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from app import app, db
from models import ParsedEmail, VettingAuditLog
from screening.candidate_data import _resolve_vetting_cutoff


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_stuck_revets")


BACKFILL_PREFIX = "[Auditor backfill]"


def _candidate_has_post_cutoff_email(
    candidate_id: int,
    cutoff: datetime,
) -> bool:
    """True iff the candidate has a completed ParsedEmail at-or-after cutoff."""
    return db.session.query(
        ParsedEmail.query.filter(
            ParsedEmail.bullhorn_candidate_id == candidate_id,
            ParsedEmail.status == 'completed',
            ParsedEmail.received_at >= cutoff,
        ).exists()
    ).scalar()


def find_stuck_revets(
    cutoff: datetime,
    age_threshold: datetime,
    limit: Optional[int] = None,
) -> List[Tuple[VettingAuditLog, datetime]]:
    """Return [(audit_row, latest_pe_received_at), ...] for rows safe to backfill.

    A row is "safe to backfill" when:
      * it matches the stuck criteria, AND
      * the candidate has NO completed parsed_email at-or-after the cutoff
        (so the next vetting cycle definitely cannot rescore them).
    """
    candidates = (
        VettingAuditLog.query
        .filter(
            VettingAuditLog.action_taken == 'revet_triggered',
            VettingAuditLog.revet_new_score.is_(None),
            VettingAuditLog.created_at < age_threshold,
        )
        .order_by(VettingAuditLog.created_at.asc())
        .all()
    )

    safe: List[Tuple[VettingAuditLog, datetime]] = []
    for row in candidates:
        if row.bullhorn_candidate_id is None:
            continue

        if _candidate_has_post_cutoff_email(row.bullhorn_candidate_id, cutoff):
            logger.debug(
                f"Skip audit_id={row.id} candidate={row.bullhorn_candidate_id} "
                f"— has post-cutoff parsed_email; might still be picked up"
            )
            continue

        latest_pe = (
            ParsedEmail.query
            .filter(
                ParsedEmail.bullhorn_candidate_id == row.bullhorn_candidate_id,
                ParsedEmail.status == 'completed',
            )
            .order_by(ParsedEmail.received_at.desc().nullslast())
            .first()
        )
        latest_pe_at = (
            latest_pe.received_at
            if latest_pe is not None and latest_pe.received_at is not None
            else None
        )
        safe.append((row, latest_pe_at))
        if limit is not None and len(safe) >= limit:
            break

    return safe


def reclassify(
    row: VettingAuditLog,
    latest_pe_at: Optional[datetime],
    cutoff: datetime,
) -> None:
    """Mutate in-place; caller commits."""
    pe_str = (
        latest_pe_at.isoformat()
        if latest_pe_at is not None
        else "<no parsed_email>"
    )
    backfill_note = (
        f"\n\n{BACKFILL_PREFIX} Reclassified from 'revet_triggered' to "
        f"'revet_skipped_pre_cutoff' on {datetime.utcnow().isoformat()}Z. "
        f"Candidate's most recent completed parsed_email "
        f"received_at={pe_str} predates "
        f"vetting_cutoff_date={cutoff.isoformat()}; the original "
        f"_trigger_revet call deleted the candidate's vetting state but the "
        f"next vetting cycle silently skipped them via the cutoff filter, so "
        f"the row was orphaned. Marking as a pre-cutoff skip aligns the "
        f"funnel with reality. No new vetting will be performed; the "
        f"candidate's vetting state is gone."
    )
    row.action_taken = 'revet_skipped_pre_cutoff'
    row.audit_finding = (row.audit_finding or '') + backfill_note


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print what would be updated without committing.',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Maximum number of rows to process (default: all matching).',
    )
    parser.add_argument(
        '--age-hours',
        type=int,
        default=24,
        help='Only backfill rows older than this many hours (default: 24).',
    )
    args = parser.parse_args(argv)

    with app.app_context():
        cutoff = _resolve_vetting_cutoff()
        if cutoff is None:
            logger.error(
                "vetting_cutoff_date is unset — every candidate is in-scope "
                "and there's no semantic basis for a 'pre-cutoff' skip. "
                "Aborting."
            )
            return 2

        age_threshold = datetime.utcnow() - timedelta(hours=args.age_hours)

        logger.info(
            f"Resolving stuck rows: cutoff={cutoff.isoformat()}, "
            f"age_threshold={age_threshold.isoformat()} "
            f"(rows must be older than {args.age_hours}h), "
            f"limit={args.limit if args.limit else 'all'}"
        )

        rows = find_stuck_revets(cutoff, age_threshold, args.limit)
        if not rows:
            logger.info("No stuck rows found. Nothing to do.")
            return 0

        logger.info(f"Found {len(rows)} stuck row(s) eligible for backfill.")

        for row, latest_pe_at in rows:
            pe_repr = (
                latest_pe_at.isoformat() if latest_pe_at else '<none>'
            )
            logger.info(
                f"  audit_id={row.id} candidate={row.bullhorn_candidate_id} "
                f"({row.candidate_name!r}) job={row.job_id} "
                f"audit_at={row.created_at.isoformat() if row.created_at else '?'} "
                f"latest_pe_received_at={pe_repr}"
            )

        if args.dry_run:
            logger.info(f"--dry-run set; no changes committed.")
            return 0

        for row, latest_pe_at in rows:
            reclassify(row, latest_pe_at, cutoff)

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Commit failed: {e!r} — rolled back, no rows updated.")
            return 1

        logger.info(
            f"✅ Backfill complete: {len(rows)} row(s) reclassified to "
            f"'revet_skipped_pre_cutoff'."
        )
        return 0


if __name__ == '__main__':
    sys.exit(main())
