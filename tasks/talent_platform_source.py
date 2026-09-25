"""Fill blank Candidate source for Qualified Talent Platform applicants.

The career site bounces some applies into Bullhorn Talent Platform. Those
records are owned by the Talent Platform API user and often land with a
blank ``source``. Origin is the corporate website, not a job board.

Blank-only: an existing source (ZipRecruiter, Indeed, etc.) is never
overwritten. Myticas/STSI no-op. Owner and status are not changed.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests as _requests

logger = logging.getLogger(__name__)

DEFAULT_OWNER_ID = 191
TARGET_SOURCE = 'Corporate Website'
MAX_UPDATES_PER_CYCLE = 200
PAGE_SIZE = 100


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or '').strip().lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'on')


def talent_platform_owner_id() -> int:
    raw = (os.environ.get('TALENT_PLATFORM_API_USER_ID') or '').strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_OWNER_ID


def source_is_blank(source: Any) -> bool:
    return not str(source or '').strip()


def source_update_payload(candidate: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Return a source update only when Source is blank."""
    if not source_is_blank(candidate.get('source')):
        return None
    return {'source': TARGET_SOURCE}


def backfill_talent_platform_source(*, dry_run: bool = False) -> Dict[str, Any]:
    """Scan Talent Platform-owned candidates and fill blank source."""
    summary: Dict[str, Any] = {
        'enabled': True,
        'found': 0,
        'eligible': 0,
        'updated': 0,
        'skipped': 0,
        'failed': 0,
        'dry_run': dry_run,
        'message': '',
    }

    try:
        from feeds.feed_config import is_qualified_tenant
        qualified = is_qualified_tenant()
    except Exception:
        qualified = False

    if not qualified:
        summary['enabled'] = False
        summary['message'] = 'skipped (not Qualified tenant)'
        logger.info('talent_platform_source: %s', summary['message'])
        return summary

    if not _env_flag('TALENT_PLATFORM_SOURCE_BACKFILL_ENABLED', True):
        summary['enabled'] = False
        summary['message'] = 'disabled (TALENT_PLATFORM_SOURCE_BACKFILL_ENABLED=false)'
        logger.info('talent_platform_source: %s', summary['message'])
        return summary

    owner_id = talent_platform_owner_id()
    summary['owner_id'] = owner_id

    from bullhorn_service import BullhornService

    bh = BullhornService()
    if not bh.authenticate():
        summary['message'] = 'Bullhorn authentication failed'
        logger.warning('talent_platform_source: %s', summary['message'])
        return summary

    headers = {
        'BhRestToken': bh.rest_token,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    search_url = f'{bh.base_url}search/Candidate'
    query = f'owner.id:{owner_id} AND isDeleted:false'

    updated = 0
    start = 0
    while updated < MAX_UPDATES_PER_CYCLE:
        response = _requests.get(
            search_url,
            headers=headers,
            params={
                'query': query,
                'fields': 'id,source,owner(id)',
                'count': PAGE_SIZE,
                'start': start,
                'sort': '-dateAdded',
            },
            timeout=30,
        )
        if response.status_code != 200:
            summary['message'] = f'search failed HTTP {response.status_code}'
            logger.warning('talent_platform_source: %s', summary['message'])
            break
        rows = (response.json() or {}).get('data') or []
        if not rows:
            break
        summary['found'] += len(rows)
        for row in rows:
            if updated >= MAX_UPDATES_PER_CYCLE:
                break
            payload = source_update_payload(row)
            if not payload:
                summary['skipped'] += 1
                continue
            summary['eligible'] += 1
            candidate_id = row.get('id')
            if dry_run:
                updated += 1
                continue
            try:
                upd = _requests.post(
                    f'{bh.base_url}entity/Candidate/{candidate_id}',
                    headers=headers,
                    json=payload,
                    timeout=15,
                )
                body = {}
                try:
                    body = upd.json()
                except Exception:
                    pass
                ok = (
                    upd.status_code in (200, 201)
                    and not body.get('errorCode')
                    and not body.get('errors')
                    and (
                        body.get('changeType') == 'UPDATE'
                        or body.get('changedEntityId') is not None
                    )
                )
                if ok:
                    updated += 1
                else:
                    summary['failed'] += 1
                    logger.warning(
                        'talent_platform_source: update failed id=%s status=%s',
                        candidate_id,
                        upd.status_code,
                    )
            except Exception as exc:
                summary['failed'] += 1
                logger.warning(
                    'talent_platform_source: error id=%s: %s', candidate_id, exc
                )
        if len(rows) < PAGE_SIZE:
            break
        start += len(rows)

    summary['updated'] = updated
    logger.info(
        'talent_platform_source: found=%s eligible=%s updated=%s skipped=%s failed=%s dry_run=%s',
        summary['found'],
        summary['eligible'],
        summary['updated'],
        summary['skipped'],
        summary['failed'],
        dry_run,
    )
    return summary


def run_talent_platform_source_backfill():
    from app import app

    with app.app_context():
        return backfill_talent_platform_source()
