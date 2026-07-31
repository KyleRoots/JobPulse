#!/usr/bin/env python3
"""One-off recovery: force Bullhorn Indeed/Corporate Unpublish for job IDs.

Use when jobs were removed from tearsheet 1640 (Sponsored - STSI - Indeed)
but Bullhorn Publish Job UI still shows Indeed as Published — typically
because a failed sync unpublish was forgotten before pending_unpublish
tracking existed (Jul 2026), or jobs were never recorded in sync state.

Usage
-----
    # Dry-run (default) — print targets, do not call Bullhorn UI.
    python scripts/recover_indeed_unpublish.py 35409 35410

    # Apply unpublish against whichever env is loaded (local .env or Railway).
    python scripts/recover_indeed_unpublish.py 35409 35410 --apply

Requires the same env as the sync task:
    INDEED_TEARSHEET_PUBLISH_ENABLED=true
    BH_UI_USERNAME / BH_UI_PASSWORD (+ related BH_UI_* vars)
    Bullhorn REST credentials used by get_bullhorn_service()

On Railway (prod), from a one-off shell with the JobPulse service env:

    python scripts/recover_indeed_unpublish.py 35409 35410 --apply

Successful IDs are cleared from job_ids / fingerprints / pending_unpublish.
Failures are queued into pending_unpublish for the next 5-min sync retry.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Force Indeed/Corporate unpublish for JobOrder IDs',
    )
    parser.add_argument(
        'job_ids',
        nargs='+',
        type=int,
        help='Bullhorn JobOrder IDs to unpublish (e.g. 35409 35410)',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Actually call Bullhorn UI unpublish (default is dry-run)',
    )
    args = parser.parse_args(argv)

    job_ids = sorted({int(x) for x in args.job_ids})
    logger.info('Targets: %s (apply=%s)', job_ids, args.apply)

    if not args.apply:
        logger.info('Dry-run only. Re-run with --apply to unpublish.')
        print(json.dumps({'dry_run': True, 'job_ids': job_ids}, indent=2))
        return 0

    # Load .env when running locally (no-op if missing / already loaded).
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    from app import app
    from indeed_publish.sync import force_unpublish_jobs

    with app.app_context():
        result = force_unpublish_jobs(job_ids)
    print(json.dumps(result, indent=2))
    if result.get('errors'):
        logger.error('Completed with errors: %s', result['errors'])
        return 1
    logger.info('Unpublished: %s', result.get('unpublished'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
