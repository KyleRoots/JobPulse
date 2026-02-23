#!/usr/bin/env python3
"""
One-Time Cleanup: Remove duplicate AI Resume Summary notes from Bullhorn.

Scans candidates with ParsedEmail records in the last 24 hours and removes
redundant AI Resume Summary notes, keeping one canonical note per candidate.

Canonical note selection rules:
  1. If the candidate has an AI Vetting note in the last 24h, keep the
     AI Resume Summary immediately preceding it (the one that fed the vetting).
  2. If there is no AI Vetting note, keep the most recent AI Resume Summary
     (latest dateAdded) and treat older ones as duplicates.

Usage:
  # Dry-run (default) — shows what WOULD be deleted
  python scripts/cleanup_resume_summary_duplicates.py

  # Execute deletions
  python scripts/cleanup_resume_summary_duplicates.py --execute

  # Custom time window (in hours, default 24)
  python scripts/cleanup_resume_summary_duplicates.py --hours 48

Safety:
  - Dry-run by default — must pass --execute to delete
  - Never deletes the last remaining AI Resume Summary for a candidate
  - Idempotent — re-running skips already-deleted notes (isDeleted check)
  - Logs every deletion with note ID, candidate ID, and timestamp
"""

import sys
import os
import argparse
import logging
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def get_bullhorn_service():
    """Get authenticated BullhornService using database credentials."""
    from app import app, db
    from models import GlobalSettings
    from bullhorn_service import BullhornService

    with app.app_context():
        credentials = {}
        for key in ['bullhorn_client_id', 'bullhorn_client_secret',
                     'bullhorn_username', 'bullhorn_password']:
            setting = GlobalSettings.query.filter_by(setting_key=key).first()
            if setting and setting.setting_value:
                credentials[key] = setting.setting_value.strip()

        bullhorn = BullhornService(
            client_id=credentials.get('bullhorn_client_id'),
            client_secret=credentials.get('bullhorn_client_secret'),
            username=credentials.get('bullhorn_username'),
            password=credentials.get('bullhorn_password')
        )

        if not bullhorn.authenticate():
            logger.error("Failed to authenticate with Bullhorn")
            return None

        return bullhorn


def get_candidate_ids(hours: int) -> list:
    """Get candidate IDs from ParsedEmail records in the given time window."""
    from app import app, db
    from models import ParsedEmail

    with app.app_context():
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        records = db.session.query(
            ParsedEmail.bullhorn_candidate_id
        ).filter(
            ParsedEmail.bullhorn_candidate_id.isnot(None),
            ParsedEmail.processed_at >= cutoff
        ).distinct().all()

        return [r[0] for r in records]


def find_canonical_note(resume_notes: list, vetting_notes: list) -> dict:
    """
    Determine which AI Resume Summary note to keep.

    Args:
        resume_notes: List of AI Resume Summary notes, sorted by dateAdded asc
        vetting_notes: List of AI Vetting notes, sorted by dateAdded asc

    Returns:
        The canonical note dict to keep.

    Rules:
    1. If vetting notes exist, keep the resume summary immediately before
       the earliest vetting note.
    2. If no vetting notes, keep the most recent resume summary (latest dateAdded).
    """
    if not resume_notes:
        return None

    if vetting_notes:
        # Find the earliest vetting note timestamp
        earliest_vetting_time = vetting_notes[0].get('dateAdded', 0)

        # Find the resume summary immediately before it
        candidate_note = None
        for note in resume_notes:
            note_time = note.get('dateAdded', 0)
            if note_time <= earliest_vetting_time:
                candidate_note = note  # Keep updating — want the latest one BEFORE vetting
            else:
                break  # Past the vetting note, stop

        # If we found one before the vetting note, use it; otherwise use the most recent
        return candidate_note if candidate_note else resume_notes[-1]
    else:
        # No vetting notes — keep the most recent resume summary
        return resume_notes[-1]


def run_cleanup(hours: int = 24, execute: bool = False):
    """
    Main cleanup routine.

    Args:
        hours: Time window in hours (default 24)
        execute: If True, actually delete notes. If False, dry-run only.
    """
    mode = "EXECUTE" if execute else "DRY-RUN"
    logger.info(f"{'='*60}")
    logger.info(f"AI Resume Summary Duplicate Cleanup — {mode}")
    logger.info(f"Time window: last {hours} hours")
    logger.info(f"{'='*60}")

    # Get Bullhorn service
    bullhorn = get_bullhorn_service()
    if not bullhorn:
        logger.error("Cannot proceed without Bullhorn authentication")
        return

    # Get candidate IDs to scan
    candidate_ids = get_candidate_ids(hours)
    logger.info(f"Found {len(candidate_ids)} candidates with ParsedEmail records in last {hours}h")

    if not candidate_ids:
        logger.info("No candidates to scan — cleanup complete")
        return

    # Stats
    stats = {
        'candidates_scanned': 0,
        'candidates_with_duplicates': 0,
        'notes_deleted': 0,
        'notes_retained': 0,
        'errors': 0,
        'examples': [],  # (candidate_id, deleted_count, kept_note_id)
    }

    vetting_actions = [
        "Scout Screening - Qualified",
        "Scout Screening - Not Recommended",
        "Scout Screening - Incomplete",
        # Backward compat: match historical action strings
        "AI Vetting - Qualified",
        "AI Vetting - Not Recommended",
        "AI Vetting - Incomplete"
    ]
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    for candidate_id in candidate_ids:
        stats['candidates_scanned'] += 1
        try:
            # Fetch ALL notes for this candidate (need both resume summary and vetting)
            all_notes = bullhorn.get_candidate_notes(
                candidate_id,
                count=100  # Fetch more to ensure we get everything
            )

            if not all_notes:
                continue

            # Separate resume summary and vetting notes, filtered to time window
            cutoff_ms = int(cutoff.timestamp() * 1000)

            resume_notes = [
                n for n in all_notes
                if n.get('action') == 'AI Resume Summary'
                and n.get('dateAdded', 0) >= cutoff_ms
                and not n.get('isDeleted', False)
            ]

            vetting_notes = [
                n for n in all_notes
                if n.get('action') in vetting_actions
                and n.get('dateAdded', 0) >= cutoff_ms
                and not n.get('isDeleted', False)
            ]

            # Skip if 0 or 1 resume summary notes (no duplicates)
            if len(resume_notes) <= 1:
                if resume_notes:
                    stats['notes_retained'] += 1
                continue

            # Sort by dateAdded ascending
            resume_notes.sort(key=lambda x: x.get('dateAdded', 0))
            vetting_notes.sort(key=lambda x: x.get('dateAdded', 0))

            # Find the canonical note to keep
            canonical = find_canonical_note(resume_notes, vetting_notes)
            if not canonical:
                continue

            canonical_id = canonical.get('id')
            duplicates = [n for n in resume_notes if n.get('id') != canonical_id]

            if not duplicates:
                stats['notes_retained'] += 1
                continue

            stats['candidates_with_duplicates'] += 1
            stats['notes_retained'] += 1  # The canonical note

            canonical_time = datetime.utcfromtimestamp(
                canonical.get('dateAdded', 0) / 1000
            ).strftime('%Y-%m-%d %H:%M:%S UTC')

            logger.info(
                f"Candidate {candidate_id}: {len(resume_notes)} AI Resume Summary notes, "
                f"keeping note {canonical_id} ({canonical_time}), "
                f"{'deleting' if execute else 'would delete'} {len(duplicates)} duplicate(s)"
            )

            deleted_count = 0
            for dup_note in duplicates:
                dup_id = dup_note.get('id')
                dup_time = datetime.utcfromtimestamp(
                    dup_note.get('dateAdded', 0) / 1000
                ).strftime('%Y-%m-%d %H:%M:%S UTC')

                if execute:
                    try:
                        delete_url = f"{bullhorn.base_url}entity/Note/{dup_id}"
                        delete_data = {'isDeleted': True}
                        response = bullhorn.session.post(
                            delete_url,
                            json=delete_data,
                            params={'BhRestToken': bullhorn.rest_token},
                            timeout=10
                        )
                        if response.status_code == 200:
                            stats['notes_deleted'] += 1
                            deleted_count += 1
                            logger.info(f"  ✅ Deleted note {dup_id} ({dup_time})")
                        else:
                            stats['errors'] += 1
                            logger.warning(
                                f"  ❌ Failed to delete note {dup_id}: "
                                f"HTTP {response.status_code}"
                            )
                    except Exception as e:
                        stats['errors'] += 1
                        logger.error(f"  ❌ Error deleting note {dup_id}: {e}")
                else:
                    stats['notes_deleted'] += 1  # Count as "would delete" in dry-run
                    deleted_count += 1
                    logger.info(f"  [DRY-RUN] Would delete note {dup_id} ({dup_time})")

            if deleted_count > 0 and len(stats['examples']) < 10:
                stats['examples'].append((candidate_id, deleted_count, canonical_id))

        except Exception as e:
            stats['errors'] += 1
            logger.error(f"Error processing candidate {candidate_id}: {e}")

    # Print summary report
    logger.info(f"\n{'='*60}")
    logger.info(f"CLEANUP REPORT — {mode}")
    logger.info(f"{'='*60}")
    logger.info(f"Candidates scanned:              {stats['candidates_scanned']}")
    logger.info(f"Candidates with duplicates:       {stats['candidates_with_duplicates']}")
    logger.info(f"AI Resume Summary notes retained: {stats['notes_retained']}")
    verb = "deleted" if execute else "would delete"
    logger.info(f"AI Resume Summary notes {verb}:  {stats['notes_deleted']}")
    logger.info(f"Errors:                           {stats['errors']}")

    if stats['examples']:
        logger.info(f"\nExample candidates for spot-checking:")
        for cid, count, kept_id in stats['examples']:
            logger.info(f"  Candidate {cid}: {count} duplicate(s) removed, kept note {kept_id}")

    logger.info(f"{'='*60}")

    return stats


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Clean up duplicate AI Resume Summary notes in Bullhorn'
    )
    parser.add_argument(
        '--execute', action='store_true',
        help='Actually delete duplicates (default is dry-run)'
    )
    parser.add_argument(
        '--hours', type=int, default=24,
        help='Time window in hours (default: 24)'
    )
    args = parser.parse_args()

    run_cleanup(hours=args.hours, execute=args.execute)
