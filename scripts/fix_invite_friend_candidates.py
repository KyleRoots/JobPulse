"""
One-off remediation script: rename Bullhorn candidates whose
firstName/lastName were corrupted to a CTA phrase ("Invite Friend").

Reads the truth-source name from ``parsed_email.candidate_name`` (the
inbound-email parser correctly extracted the real name from the email
subject) and pushes it to Bullhorn via ``bullhorn.update_candidate``.

Safe to re-run: only updates candidates whose CURRENT Bullhorn name
still matches a CTA phrase. Idempotent — already-fixed records are
skipped with a log line.

Usage:
    python -m scripts.fix_invite_friend_candidates
    python -m scripts.fix_invite_friend_candidates --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys

from app import app, db
from models import CandidateVettingLog, ParsedEmail
from utils.candidate_name_extraction import is_cta_phrase, split_full_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fix_invite_friend")


def find_corrupted_candidate_ids() -> dict[int, str]:
    """Return {bullhorn_candidate_id: truth_name} for candidates whose
    most-recent vetting_log name is a CTA phrase but whose linked
    parsed_email has a valid name."""
    rows = (
        db.session.query(
            CandidateVettingLog.bullhorn_candidate_id,
            CandidateVettingLog.candidate_name,
            ParsedEmail.candidate_name.label("parsed_name"),
        )
        .join(ParsedEmail, ParsedEmail.id == CandidateVettingLog.parsed_email_id)
        .filter(CandidateVettingLog.bullhorn_candidate_id.isnot(None))
        .all()
    )

    candidates: dict[int, str] = {}
    for bh_id, vetting_name, parsed_name in rows:
        if not bh_id or not vetting_name or not parsed_name:
            continue
        if not is_cta_phrase(vetting_name):
            continue
        if is_cta_phrase(parsed_name):
            continue
        # First wins — multiple vetting_log rows can map to the same
        # Bullhorn ID; we only need one truth-name.
        if bh_id not in candidates:
            candidates[bh_id] = parsed_name
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned updates without calling Bullhorn.")
    args = parser.parse_args()

    with app.app_context():
        from app import get_bullhorn_service

        targets = find_corrupted_candidate_ids()
        if not targets:
            logger.info("No corrupted candidates found. Nothing to do.")
            return 0

        logger.info(f"Found {len(targets)} corrupted candidate(s) to rename:")
        for bh_id, truth_name in targets.items():
            logger.info(f"  - Bullhorn ID {bh_id}: → '{truth_name}'")

        if args.dry_run:
            logger.info("Dry-run mode — no Bullhorn calls made.")
            return 0

        bullhorn = get_bullhorn_service()
        if not bullhorn.authenticate():
            logger.error("Bullhorn authentication failed. Aborting.")
            return 2

        success = 0
        failure = 0
        for bh_id, truth_name in targets.items():
            first, last = split_full_name(truth_name)
            if not first or not last:
                logger.warning(
                    f"Skipping Bullhorn ID {bh_id}: could not split '{truth_name}' "
                    f"into first/last."
                )
                failure += 1
                continue

            # Defense-in-depth: re-read the live Bullhorn record so we
            # don't overwrite a name that a human has already corrected.
            current = bullhorn.get_candidate(bh_id)
            current_first = (current or {}).get("firstName", "")
            current_last = (current or {}).get("lastName", "")
            current_combined = f"{current_first} {current_last}".strip()
            if not is_cta_phrase(current_combined):
                logger.info(
                    f"Bullhorn ID {bh_id} already has clean name "
                    f"'{current_combined}' — skipping."
                )
                continue

            payload = {
                "firstName": first,
                "lastName": last,
                "name": f"{first} {last}",
            }
            result_id = bullhorn.update_candidate(bh_id, payload)
            if result_id:
                logger.info(
                    f"Renamed Bullhorn ID {bh_id}: "
                    f"'{current_combined}' → '{first} {last}'"
                )
                success += 1
            else:
                logger.error(f"Failed to update Bullhorn ID {bh_id}.")
                failure += 1

        logger.info(f"Done. success={success} failure={failure}")
        return 0 if failure == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
