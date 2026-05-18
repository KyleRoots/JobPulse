"""Backfill Net Margin % across currently-open Bullhorn placements (T005).

Walks all Placements with status='Active' (the operational "open" cohort —
i.e. someone is currently working on the assignment) and runs them through
the same pipeline the live poller uses (`process_placement`). That means:

    - Audit log row written for every placement, success or skip
    - PATCH to customFloat1 only when the value actually changes
    - "no_change" placements are counted but not re-written (saves API calls)

Defaults are conservative and Bullhorn-friendly:

    - Page size 50 placements per Bullhorn query call
    - 1-second pause between batches (well under Bullhorn's ~5/sec throttle)
    - Re-auth handled by the underlying BullhornService

Usage:
    PYTHONPATH=. python scripts/backfill_placement_margin.py            # dry-run preview
    PYTHONPATH=. python scripts/backfill_placement_margin.py --execute  # actually run
    PYTHONPATH=. python scripts/backfill_placement_margin.py --execute --where "status='Active' OR status='Approved'"

The --where override lets you broaden or narrow the cohort without
re-coding (e.g. include 'Approved' future-start placements, or restrict
to a single client). Whatever you pass is sent straight to Bullhorn's
`/query/Placement?where=` API.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_placement_margin")


DEFAULT_WHERE = "status='Approved'"
DEFAULT_PAGE_SIZE = 50
DEFAULT_BATCH_PAUSE_SEC = 1.0


def _fetch_placement_ids(bh, where: str, page_size: int) -> List[int]:
    """Page through /query/Placement?where=... and return all matching ids.

    Uses raw HTTP because BullhornService.query_entity doesn't expose the
    `start` parameter we need for pagination.
    """
    if not bh.base_url or not bh.rest_token:
        if not bh.authenticate():
            raise RuntimeError("Bullhorn authentication failed")

    all_ids: List[int] = []
    start = 0
    url = f"{bh.base_url}query/Placement"

    while True:
        params = {
            "where": where,
            "fields": "id",
            "count": page_size,
            "start": start,
            "orderBy": "id",
            "BhRestToken": bh.rest_token,
        }
        r = bh.session.get(url, params=params, timeout=30)
        if r.status_code == 401:
            bh.rest_token = None
            if not bh.authenticate():
                raise RuntimeError("Bullhorn re-auth failed mid-query")
            params["BhRestToken"] = bh.rest_token
            r = bh.session.get(url, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(
                f"Bullhorn query failed: HTTP {r.status_code} body={r.text[:300]}"
            )
        payload = r.json()
        batch = payload.get("data", []) or []
        if not batch:
            break
        all_ids.extend(int(row["id"]) for row in batch if row.get("id") is not None)
        if len(batch) < page_size:
            break
        start += page_size

    return all_ids


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run the sweep. Without this flag, the script only "
             "previews the count of placements that would be touched.",
    )
    parser.add_argument(
        "--where",
        default=DEFAULT_WHERE,
        help=f"Bullhorn /query/Placement where-clause. Default: {DEFAULT_WHERE!r}",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Placements processed per batch (default: {DEFAULT_PAGE_SIZE}).",
    )
    parser.add_argument(
        "--batch-pause",
        type=float,
        default=DEFAULT_BATCH_PAUSE_SEC,
        help=f"Seconds to sleep between batches (default: {DEFAULT_BATCH_PAUSE_SEC}).",
    )
    args = parser.parse_args(argv[1:])

    from bullhorn_service import BullhornService

    bh = BullhornService()
    if not bh.authenticate():
        print("ERROR: Bullhorn authentication failed")
        return 3

    logger.info(f"Fetching placement IDs where: {args.where!r}")
    try:
        ids = _fetch_placement_ids(bh, args.where, args.page_size)
    except Exception as exc:
        logger.exception(f"Failed to fetch placement IDs: {exc}")
        return 4

    logger.info(f"Found {len(ids)} placements matching filter")

    if not args.execute:
        print("\n--- DRY RUN (use --execute to actually run) ---")
        print(f"Would process {len(ids)} placements")
        print(f"First 10 IDs: {ids[:10]}")
        print(f"Estimated runtime: ~{max(1, len(ids) // args.page_size)} batches "
              f"× ({args.page_size} placements + {args.batch_pause}s pause)")
        return 0

    # Real run — process each placement through the same pipeline the
    # live poller uses. Wrap each batch in its own try/except so a single
    # bad placement can't kill the whole sweep.
    from app import app
    from placement_margin.worker import process_placement

    summary = {"processed": 0, "patched": 0, "no_change": 0, "skipped_other": 0, "errored": 0}

    with app.app_context():
        for batch_start in range(0, len(ids), args.page_size):
            batch = ids[batch_start: batch_start + args.page_size]
            logger.info(
                f"Batch {batch_start // args.page_size + 1}: "
                f"placements {batch[0]}..{batch[-1]} ({len(batch)} items)"
            )
            for pid in batch:
                try:
                    row = process_placement(bh, pid, trigger="backfill")
                    summary["processed"] += 1
                    if row.write_success is True:
                        summary["patched"] += 1
                    elif row.write_skipped_reason == "no_change":
                        summary["no_change"] += 1
                    elif row.write_success is False:
                        summary["errored"] += 1
                    else:
                        summary["skipped_other"] += 1
                except Exception as exc:
                    logger.exception(f"placement {pid} raised: {exc}")
                    summary["errored"] += 1

            # Be a good neighbor to Bullhorn's rate limiter.
            if batch_start + args.page_size < len(ids):
                time.sleep(args.batch_pause)

    print("\n--- BACKFILL SUMMARY ---")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(
        f"\nAudit rows for this run: SELECT * FROM placement_margin_calc_log "
        f"WHERE trigger='backfill' ORDER BY created_at DESC LIMIT {summary['processed']};"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
