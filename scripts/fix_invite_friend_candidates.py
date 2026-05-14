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
    # Auto-discover from local DB (works when dev DB has the parsed_email rows)
    python -m scripts.fix_invite_friend_candidates
    python -m scripts.fix_invite_friend_candidates --dry-run

    # Explicit overrides — for known cases when running against a workspace
    # whose local DB doesn't contain the affected rows (e.g. dev workspace
    # pointed at shared Bullhorn). Bullhorn is the only system mutated.
    python -m scripts.fix_invite_friend_candidates \\
        --candidate 3822915="Sujatha Devineni" \\
        --candidate 3817209="Sai Charan Mittapalli"
"""
from __future__ import annotations

import argparse
import logging
import sys

from app import app, db
from models import CandidateVettingLog, ParsedEmail
from utils.candidate_name_extraction import is_cta_phrase, split_full_name


# Hard-coded truth names for the original production failure (May 2026).
# Used when --use-known-cases is passed. Source: parsed_email.candidate_name
# from production DB at the time of triage (vetting_log IDs 6365/6364/5596/4777,
# parsed_email IDs 4149/4148/3637/3148).
KNOWN_CASES: dict[int, str] = {
    3822915: "Sujatha Devineni",
    3817209: "Sai Charan Mittapalli",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fix_invite_friend")


def find_corrupted_candidate_ids() -> dict[int, str]:
    """Return {bullhorn_candidate_id: truth_name} for candidates whose
    most-recent vetting_log name is a CTA phrase but whose linked
    parsed_email has a valid name.

    Ordered by ``CandidateVettingLog.created_at DESC`` so the most
    recently captured parsed_email truth-name wins per Bullhorn ID —
    avoids stale/older names overwriting newer ones when a candidate
    re-applied multiple times with different name spellings.
    """
    rows = (
        db.session.query(
            CandidateVettingLog.bullhorn_candidate_id,
            CandidateVettingLog.candidate_name,
            ParsedEmail.candidate_name.label("parsed_name"),
        )
        .join(ParsedEmail, ParsedEmail.id == CandidateVettingLog.parsed_email_id)
        .filter(CandidateVettingLog.bullhorn_candidate_id.isnot(None))
        .order_by(CandidateVettingLog.created_at.desc())
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
        # First wins per Bullhorn ID — rows are pre-sorted newest-first
        # above, so this picks the most recent valid parsed_email name.
        if bh_id not in candidates:
            candidates[bh_id] = parsed_name
    return candidates


def _parse_candidate_arg(value: str) -> tuple[int, str]:
    """Parse a ``--candidate ID=Full Name`` CLI value."""
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"Expected 'ID=Full Name', got {value!r}"
        )
    bh_id_raw, _, name = value.partition("=")
    try:
        bh_id = int(bh_id_raw.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid candidate id {bh_id_raw!r}: {exc}"
        )
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError(f"Missing name for id {bh_id}")
    return bh_id, name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned updates without calling Bullhorn.")
    parser.add_argument(
        "--candidate", action="append", type=_parse_candidate_arg, default=[],
        metavar='ID="Full Name"',
        help="Explicit (id, truth-name) override. May be repeated."
    )
    parser.add_argument(
        "--use-known-cases", action="store_true",
        help="Use the hard-coded KNOWN_CASES dict (the original May 2026 "
             "production failures). Safe to combine with --candidate.",
    )
    args = parser.parse_args()

    with app.app_context():
        from app import get_bullhorn_service

        targets: dict[int, str] = {}
        if args.use_known_cases:
            targets.update(KNOWN_CASES)
        for bh_id, name in args.candidate:
            targets[bh_id] = name
        if not targets:
            # Auto-discover from local DB
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
