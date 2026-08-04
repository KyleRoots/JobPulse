"""
Remap native Indeed Apply inbound Bullhorn fields.

Native Indeed Apply (Bullhorn JobBoard CFC / Plan B) creates candidates as:
  status=New Lead, source=Indeed, owner=Unassigned User

Email inbound (LinkedIn / Indeed emails / apply forms) instead creates:
  status=Online Applicant, source=Indeed Job Board (or LinkedIn Job Board),
  owner=Myticas API User (CorporateUser 1147490)

This task remaps the native Indeed shape to match email inbound so Scout
detectors and Owner Reassignment see a consistent inbound profile.

Ownership interaction with owner_reassignment
---------------------------------------------
owner_reassignment only considers candidates whose owner.id is in the
configured api_user_ids list (Myticas API User 1147490, Pandologic, Matador,
etc.). It then reassigns ownership to the first human recruiter who left a
note/activity.

This remapper therefore:
  - Sets Unassigned → Myticas API User so the candidate enters the same pool
    as LinkedIn Online Applicants.
  - Never overwrites a human/internal owner (activity may already have fired,
    or a recruiter may have claimed the record).
  - After Myticas API User is set, owner_reassignment continues to run on its
    own 5-minute schedule and can take over exactly as it does for other
    Online Applicant / Myticas API User inbound.

THREAD-SAFETY: Uses standalone requests.get/post — never bh.session.* —
because this runs in a background APScheduler thread.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests as _requests

logger = logging.getLogger(__name__)

# Same CorporateUser id used as the inbound/email safety-net API owner
# throughout JobPulse (duplicate_merge, PandoLogic guard, screening tests).
MYTICAS_API_USER_ID = 1147490
UNASSIGNED_OWNER_ID = 1

TARGET_STATUS = 'Online Applicant'
TARGET_SOURCE = 'Indeed Job Board'
SOURCE_EXACT = 'Indeed'  # native Apply only — not Resume Search / Job Board

DEFAULT_LOOKBACK_HOURS = 48
DEFAULT_BATCH_SIZE = 100
MAX_UPDATES_PER_CYCLE = 200

SEARCH_FIELDS = (
    'id,firstName,lastName,status,source,dateAdded,dateLastModified,'
    'owner(id,name)'
)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in ('true', '1', 'yes', 'on')


def _lookback_hours() -> float:
    raw = os.environ.get('INDEED_INBOUND_REMAP_LOOKBACK_HOURS', str(DEFAULT_LOOKBACK_HOURS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(DEFAULT_LOOKBACK_HOURS)
    return max(1.0, min(value, 168.0))  # clamp 1h–7d


def is_unassigned_owner(owner: Optional[Dict[str, Any]]) -> bool:
    """True when owner is missing, Unassigned User (id=1), or name says unassigned.

    Human/internal owners (and Myticas API User) return False so we never
    overwrite them.
    """
    if not owner or not isinstance(owner, dict):
        return True
    owner_id = owner.get('id')
    if owner_id is None or owner_id == '':
        return True
    try:
        if int(owner_id) == UNASSIGNED_OWNER_ID:
            return True
    except (TypeError, ValueError):
        pass
    name = str(owner.get('name') or '').strip().lower()
    return 'unassigned' in name


def is_native_indeed_source(source: Any) -> bool:
    """Exact source match for native Indeed Apply — excludes Resume Search / Job Board."""
    return str(source or '').strip() == SOURCE_EXACT


def build_indeed_inbound_remap_payload(
    candidate: Dict[str, Any],
    *,
    myticas_api_user_id: int = MYTICAS_API_USER_ID,
) -> Dict[str, Any]:
    """
    Build a partial Candidate update for fields that still need remapping.

    Returns {} when the candidate is already fully remapped or ineligible
    (wrong source, human owner with nothing else to fix, etc.).
    """
    if not is_native_indeed_source(candidate.get('source')):
        return {}

    updates: Dict[str, Any] = {
        # Exact native source "Indeed" → canonical inbound value.
        'source': TARGET_SOURCE,
    }

    # Only New Lead → Online Applicant (never stomp recruiter-set statuses).
    status = str(candidate.get('status') or '').strip()
    if status == 'New Lead':
        updates['status'] = TARGET_STATUS

    owner = candidate.get('owner')
    if is_unassigned_owner(owner):
        updates['owner'] = {'id': int(myticas_api_user_id)}

    return updates


def _search_query(since_ms: int) -> str:
    # Lucene: token Indeed matches Indeed* variants; exclude known non-Apply sources.
    # Post-filter still requires exact source == "Indeed".
    return (
        f'source:Indeed AND -source:"Indeed Job Board" '
        f'AND -source:"Indeed Resume Search" '
        f'AND dateLastModified:[{since_ms} TO *]'
    )


def _post_update_ok(response: _requests.Response) -> bool:
    if response.status_code not in (200, 201):
        return False
    try:
        body = response.json()
    except Exception:
        return False
    if body.get('errorCode') or body.get('errors'):
        return False
    return (
        body.get('changeType') == 'UPDATE'
        or body.get('changedEntityId') is not None
    )


def remap_indeed_inbound_fields(
    *,
    lookback_hours: Optional[float] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Find recent source=Indeed candidates and remap status/source/owner.

    Returns a summary dict for logging / tests.
    """
    summary: Dict[str, Any] = {
        'enabled': True,
        'lookback_hours': lookback_hours if lookback_hours is not None else _lookback_hours(),
        'found': 0,
        'eligible': 0,
        'updated': 0,
        'skipped_already_ok': 0,
        'skipped_human_owner_only': 0,
        'skipped_wrong_source': 0,
        'failed': 0,
        'dry_run': dry_run,
        'message': '',
    }

    if not _env_flag('INDEED_INBOUND_REMAP_ENABLED', True):
        summary['enabled'] = False
        summary['message'] = 'disabled (INDEED_INBOUND_REMAP_ENABLED=false)'
        logger.info('indeed_inbound_remap: %s', summary['message'])
        return summary

    from bullhorn_service import BullhornService

    bh = BullhornService()
    if not bh.authenticate():
        summary['message'] = 'Bullhorn authentication failed'
        logger.warning('indeed_inbound_remap: %s — skipping run', summary['message'])
        return summary

    hours = summary['lookback_hours']
    since_ms = int((datetime.utcnow() - timedelta(hours=hours)).timestamp() * 1000)
    query = _search_query(since_ms)

    base_url = bh.base_url
    headers = {
        'BhRestToken': bh.rest_token,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    search_url = f'{base_url}search/Candidate'

    candidates: List[Dict[str, Any]] = []
    start = 0
    page_size = DEFAULT_BATCH_SIZE

    while True:
        resp = _requests.get(
            search_url,
            headers=headers,
            params={
                'query': query,
                'fields': SEARCH_FIELDS,
                'count': page_size,
                'start': start,
                'sort': '-dateLastModified',
            },
            timeout=30,
        )
        if resp.status_code == 401:
            logger.warning(
                'indeed_inbound_remap: search HTTP 401 — re-authenticating once'
            )
            bh.rest_token = None
            if not bh.authenticate():
                summary['message'] = 'Bullhorn re-auth failed after 401'
                logger.error('indeed_inbound_remap: %s', summary['message'])
                return summary
            headers['BhRestToken'] = bh.rest_token
            resp = _requests.get(
                search_url,
                headers=headers,
                params={
                    'query': query,
                    'fields': SEARCH_FIELDS,
                    'count': page_size,
                    'start': start,
                    'sort': '-dateLastModified',
                },
                timeout=30,
            )
        if resp.status_code != 200:
            summary['message'] = f'search failed HTTP {resp.status_code}'
            logger.error('indeed_inbound_remap: %s', summary['message'])
            return summary

        page = resp.json().get('data') or []
        if not page:
            break
        candidates.extend(page)
        start += len(page)
        if len(page) < page_size or len(candidates) >= MAX_UPDATES_PER_CYCLE * 2:
            break
        time.sleep(0.05)

    summary['found'] = len(candidates)
    logger.info(
        'indeed_inbound_remap: found %s candidate(s) in %.0fh window (query=%r)',
        len(candidates),
        hours,
        query,
    )

    updated = 0
    for candidate in candidates:
        if updated >= MAX_UPDATES_PER_CYCLE:
            break

        cid = candidate.get('id')
        if not cid:
            continue

        if not is_native_indeed_source(candidate.get('source')):
            summary['skipped_wrong_source'] += 1
            continue

        payload = build_indeed_inbound_remap_payload(candidate)
        if not payload:
            summary['skipped_already_ok'] += 1
            continue

        # Human owner: remap status/source only — never touch owner.
        if 'owner' not in payload and not is_unassigned_owner(candidate.get('owner')):
            summary['skipped_human_owner_only'] += 1
            owner = candidate.get('owner') or {}
            logger.info(
                'indeed_inbound_remap: candidate %s has human owner '
                '(id=%s, name=%r) — remapping status/source only',
                cid,
                owner.get('id'),
                owner.get('name'),
            )

        summary['eligible'] += 1

        if dry_run:
            logger.info(
                'indeed_inbound_remap: [dry_run] would update %s → %s',
                cid,
                payload,
            )
            updated += 1
            continue

        try:
            upd = _requests.post(
                f'{base_url}entity/Candidate/{cid}',
                headers=headers,
                json=payload,
                timeout=15,
            )
            if upd.status_code == 401:
                bh.rest_token = None
                if bh.authenticate():
                    headers['BhRestToken'] = bh.rest_token
                    upd = _requests.post(
                        f'{base_url}entity/Candidate/{cid}',
                        headers=headers,
                        json=payload,
                        timeout=15,
                    )
            if _post_update_ok(upd):
                updated += 1
                logger.info(
                    'indeed_inbound_remap: updated candidate %s '
                    '(%s %s) fields=%s',
                    cid,
                    candidate.get('firstName'),
                    candidate.get('lastName'),
                    sorted(payload.keys()),
                )
            else:
                summary['failed'] += 1
                logger.warning(
                    'indeed_inbound_remap: update failed for %s — '
                    'HTTP %s body=%s',
                    cid,
                    upd.status_code,
                    (upd.text or '')[:300],
                )
        except Exception as exc:
            summary['failed'] += 1
            logger.warning(
                'indeed_inbound_remap: error updating %s — %s',
                cid,
                exc,
            )
        time.sleep(0.05)

    summary['updated'] = updated
    summary['message'] = (
        f'updated={updated} eligible={summary["eligible"]} '
        f'found={summary["found"]} failed={summary["failed"]}'
    )
    logger.info('indeed_inbound_remap: complete — %s', summary['message'])
    return summary


def run_indeed_inbound_remap():
    """APScheduler entrypoint."""
    from app import app

    with app.app_context():
        try:
            remap_indeed_inbound_fields()
        except Exception as exc:
            logger.error('indeed_inbound_remap: unexpected error — %s', exc)
