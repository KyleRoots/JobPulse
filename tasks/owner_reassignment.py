"""
Owner Reassignment Task
=======================
Scheduled task: find Bullhorn Candidate records whose owner is a known API
service account (Pandologic, Matador, Myticas, etc.) and reassign ownership to
the first human recruiter who interacted with the candidate (via notes or
other activity).

Configuration is read from VettingConfig at runtime:
  - auto_reassign_owner_enabled  bool   master toggle (default false)
  - api_user_ids                 str    comma-separated Bullhorn CorporateUser IDs
  - reassign_owner_note_enabled  bool   whether to leave a Bullhorn note (default true)

THREAD-SAFETY: Uses standalone requests.get/post — never bh.session.* — because
this runs in a background APScheduler thread and requests.Session is not thread-safe.
"""
import time
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import requests as _requests

from bullhorn_service import BullhornService

logger = logging.getLogger(__name__)

_CANDIDATE_FIELDS = (
    'id,firstName,lastName,email,owner(id,firstName,lastName)'
)
_NOTE_FIELDS = (
    'id,commentingPerson(id,firstName,lastName),dateAdded,action'
)


def _get_vetting_config(key: str, default: str = '') -> str:
    """Read a single VettingConfig value. Returns default if key is missing."""
    try:
        from models import VettingConfig
        row = VettingConfig.query.filter_by(setting_key=key).first()
        return row.setting_value if row else default
    except Exception:
        return default


def _parse_api_user_ids(raw: str) -> List[int]:
    """Parse a comma-separated string of Bullhorn CorporateUser IDs to a list of ints."""
    ids = []
    for part in raw.split(','):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


def _find_first_human_interactor(
    base_url: str,
    headers: dict,
    candidate_id: int,
    api_user_ids: List[int],
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    Search the candidate's Bullhorn Notes for the earliest note written by a
    human (non-API) user and return:
      (corporateUser_id, firstName, lastName)

    Paginates through all notes (sorted by dateAdded ascending) so the first
    human interactor is found even when early pages contain only API-authored
    notes.

    Returns (None, None, None) if no human activity is found.
    """
    note_url = f"{base_url}search/Note"
    page_size = 50
    start = 0

    while True:
        params = {
            'query': f'candidates.id:{candidate_id}',
            'fields': _NOTE_FIELDS,
            'count': page_size,
            'start': start,
            'sort': 'dateAdded',
        }

        resp = None
        for attempt in range(2):
            try:
                resp = _requests.get(note_url, headers=headers, params=params, timeout=15)
                if resp.status_code == 200:
                    break
                if 500 <= resp.status_code < 600 and attempt == 0:
                    time.sleep(1)
                    continue
                logger.warning(
                    f"Note lookup for candidate {candidate_id}: "
                    f"HTTP {resp.status_code}"
                )
                return (None, None, None)
            except Exception as exc:
                if attempt == 0:
                    time.sleep(1)
                    continue
                logger.warning(
                    f"Note lookup exception for candidate {candidate_id}: {exc}"
                )
                return (None, None, None)

        if resp is None or resp.status_code != 200:
            return (None, None, None)

        page_data = resp.json()
        notes = page_data.get('data', [])

        for note in notes:
            person = note.get('commentingPerson') or {}
            person_id = person.get('id')
            if person_id and int(person_id) not in api_user_ids:
                return (
                    int(person_id),
                    person.get('firstName', ''),
                    person.get('lastName', ''),
                )

        total = page_data.get('total', len(notes))
        start += len(notes)
        if start >= total or len(notes) < page_size:
            break

        time.sleep(0.05)

    return (None, None, None)


def _build_note_text(
    candidate_first: str,
    candidate_last: str,
    old_owner_first: str,
    old_owner_last: str,
    new_owner_first: str,
    new_owner_last: str,
) -> str:
    old_name = f"{old_owner_first} {old_owner_last}".strip() or "API User"
    new_name = f"{new_owner_first} {new_owner_last}".strip() or "Unknown Recruiter"
    return (
        f"Owner Reassigned — {candidate_first} {candidate_last}\n\n"
        f"Previous owner: {old_name}\n"
        f"New owner: {new_name}\n"
        f"Reason: API service account detected; ownership transferred to the "
        f"first recruiter who interacted with this candidate.\n\n"
        f"This change was made automatically by Scout Genius."
    )


def preview_reassign_candidates(limit: int = 5) -> dict:
    """
    Read-only preview: return a list of up to `limit` candidates that WOULD be
    reassigned by the scheduled task, without making any changes to Bullhorn.

    Used by the Automation Test Center "Test Batch" UI.

    Returns a dict with keys:
      enabled         bool    whether the feature toggle is on
      api_user_ids    list    configured API user IDs
      candidates      list    [{candidate_id, name, current_owner,
                               would_reassign: bool, skip_reason,
                               new_owner, new_owner_id}]
      total_found     int     total API-owned candidates found (may exceed limit)
      error           str     only present on failure
    """
    from app import app

    with app.app_context():
        try:
            enabled = _get_vetting_config('auto_reassign_owner_enabled', 'false').lower() == 'true'
            api_user_ids = _parse_api_user_ids(_get_vetting_config('api_user_ids', ''))

            if not api_user_ids:
                return {
                    'enabled': enabled,
                    'api_user_ids': [],
                    'candidates': [],
                    'total_found': 0,
                    'error': 'No API user IDs configured. Add Bullhorn CorporateUser IDs in the Automation Hub.',
                }

            bh = BullhornService()
            if not bh.authenticate():
                return {
                    'enabled': enabled,
                    'api_user_ids': api_user_ids,
                    'candidates': [],
                    'total_found': 0,
                    'error': 'Bullhorn authentication failed.',
                }

            base_url = bh.base_url
            rest_token = bh.rest_token
            headers = {
                'BhRestToken': rest_token,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            }

            if len(api_user_ids) == 1:
                owner_clause = f'owner.id:{api_user_ids[0]}'
            else:
                owner_clause = '(' + ' OR '.join(f'owner.id:{uid}' for uid in api_user_ids) + ')'

            since_time = datetime.utcnow() - timedelta(days=30)
            since_ts = int(since_time.timestamp() * 1000)
            query = f'{owner_clause} AND dateLastModified:[{since_ts} TO *]'

            search_url = f"{base_url}search/Candidate"
            resp = _requests.get(
                search_url,
                headers=headers,
                params={
                    'query': query,
                    'fields': _CANDIDATE_FIELDS,
                    'count': max(limit, 10),
                    'start': 0,
                    'sort': '-dateLastModified',
                },
                timeout=30,
            )

            if resp.status_code != 200:
                return {
                    'enabled': enabled,
                    'api_user_ids': api_user_ids,
                    'candidates': [],
                    'total_found': 0,
                    'error': f'Bullhorn search failed: HTTP {resp.status_code}',
                }

            page_data = resp.json()
            all_candidates = page_data.get('data', [])
            total_found = page_data.get('total', len(all_candidates))
            sample = all_candidates[:limit]

            results = []
            for candidate in sample:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue

                c_first = candidate.get('firstName', '')
                c_last = candidate.get('lastName', '')
                old_owner = candidate.get('owner') or {}
                old_owner_id = old_owner.get('id')
                old_owner_name = f"{old_owner.get('firstName', '')} {old_owner.get('lastName', '')}".strip() or 'API User'

                recruiter_id, rec_first, rec_last = _find_first_human_interactor(
                    base_url, headers, candidate_id, api_user_ids
                )

                entry = {
                    'candidate_id': candidate_id,
                    'name': f"{c_first} {c_last}".strip(),
                    'current_owner': old_owner_name,
                    'current_owner_id': old_owner_id,
                    'would_reassign': False,
                    'skip_reason': None,
                }

                if recruiter_id is None:
                    entry['skip_reason'] = 'No human activity found'
                elif old_owner_id and int(old_owner_id) == int(recruiter_id):
                    entry['skip_reason'] = 'Already assigned to correct user'
                else:
                    new_owner_name = f"{rec_first} {rec_last}".strip() or str(recruiter_id)
                    entry['would_reassign'] = True
                    entry['new_owner'] = new_owner_name
                    entry['new_owner_id'] = recruiter_id

                results.append(entry)
                time.sleep(0.05)

            return {
                'enabled': enabled,
                'api_user_ids': api_user_ids,
                'candidates': results,
                'total_found': total_found,
            }

        except Exception as exc:
            logger.error(f"preview_reassign_candidates: unexpected error — {exc}", exc_info=True)
            return {
                'enabled': False,
                'api_user_ids': [],
                'candidates': [],
                'total_found': 0,
                'error': str(exc),
            }


def reassign_api_user_candidates(since_minutes: int = 30) -> dict:
    """
    Main entry point for the scheduled task.

    Runs inside an app context (APScheduler worker). Reads VettingConfig for
    the toggle, the configured API user IDs, and the note toggle. For each
    candidate owned by an API user account, finds the first human recruiter
    who interacted (via Bullhorn Notes) and updates the ownership record.

    Returns a dict with keys: reassigned, skipped, failed, errors (list of str).
    """
    from app import app

    with app.app_context():
        try:
            if _get_vetting_config('auto_reassign_owner_enabled', 'false').lower() != 'true':
                logger.debug("owner_reassignment: feature disabled — skipping run")
                return {'reassigned': 0, 'skipped': 0, 'failed': 0, 'errors': ['Feature is disabled. Enable the automation toggle first.']}

            api_user_ids = _parse_api_user_ids(
                _get_vetting_config('api_user_ids', '')
            )
            if not api_user_ids:
                logger.info(
                    "owner_reassignment: no API user IDs configured — skipping run. "
                    "Add Bullhorn CorporateUser IDs to the 'api_user_ids' setting."
                )
                return {'reassigned': 0, 'skipped': 0, 'failed': 0, 'errors': ['No API user IDs configured.']}

            note_enabled = (
                _get_vetting_config('reassign_owner_note_enabled', 'true').lower() == 'true'
            )

            bh = BullhornService()
            if not bh.authenticate():
                logger.warning("owner_reassignment: Bullhorn authentication failed — skipping run")
                return {'reassigned': 0, 'skipped': 0, 'failed': 0, 'errors': ['Bullhorn authentication failed.']}

            base_url = bh.base_url
            rest_token = bh.rest_token
            headers = {
                'BhRestToken': rest_token,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            }

            since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
            since_ts = int(since_time.timestamp() * 1000)

            if len(api_user_ids) == 1:
                owner_clause = f'owner.id:{api_user_ids[0]}'
            else:
                owner_clause = '(' + ' OR '.join(f'owner.id:{uid}' for uid in api_user_ids) + ')'

            query = f'{owner_clause} AND dateLastModified:[{since_ts} TO *]'

            search_url = f"{base_url}search/Candidate"
            page_size = 100
            start = 0
            candidates: list = []

            while True:
                search_params = {
                    'query': query,
                    'fields': _CANDIDATE_FIELDS,
                    'count': page_size,
                    'start': start,
                    'sort': '-dateLastModified',
                }
                resp = _requests.get(
                    search_url, headers=headers, params=search_params, timeout=30
                )
                if resp.status_code != 200:
                    logger.error(
                        f"owner_reassignment: candidate search failed "
                        f"HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                    return {'reassigned': 0, 'skipped': 0, 'failed': 0, 'errors': [f'Candidate search failed: HTTP {resp.status_code}']}

                page_data = resp.json()
                page_candidates = page_data.get('data', [])
                candidates.extend(page_candidates)

                total = page_data.get('total', len(candidates))
                start += len(page_candidates)
                if start >= total or len(page_candidates) < page_size:
                    break

                time.sleep(0.1)

            if not candidates:
                logger.info(
                    f"owner_reassignment: no API-owned candidates found in the last "
                    f"{since_minutes} minutes — nothing to do"
                )
                return {'reassigned': 0, 'skipped': 0, 'failed': 0, 'errors': []}

            logger.info(
                f"owner_reassignment: found {len(candidates)} API-owned candidate(s) "
                f"to evaluate (owner IDs: {api_user_ids})"
            )

            reassigned = 0
            skipped_no_activity = 0
            skipped_already_correct = 0
            failed = 0
            errors: list = []

            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue

                c_first = candidate.get('firstName', '')
                c_last = candidate.get('lastName', '')
                old_owner = candidate.get('owner') or {}
                old_owner_first = old_owner.get('firstName', '')
                old_owner_last = old_owner.get('lastName', '')

                recruiter_id, rec_first, rec_last = _find_first_human_interactor(
                    base_url, headers, candidate_id, api_user_ids
                )

                if recruiter_id is None:
                    logger.info(
                        f"owner_reassignment: skipping candidate {candidate_id} "
                        f"({c_first} {c_last}) — no human activity found"
                    )
                    skipped_no_activity += 1
                    continue

                old_owner_id = old_owner.get('id')
                if old_owner_id and int(old_owner_id) == int(recruiter_id):
                    logger.debug(
                        f"owner_reassignment: skipping candidate {candidate_id} "
                        f"({c_first} {c_last}) — already owned by correct user ({recruiter_id})"
                    )
                    skipped_already_correct += 1
                    continue

                try:
                    upd = _requests.post(
                        f"{base_url}entity/Candidate/{candidate_id}",
                        headers=headers,
                        json={'owner': {'id': int(recruiter_id)}},
                        timeout=15,
                    )
                    body = {}
                    try:
                        body = upd.json()
                    except Exception:
                        pass

                    if (upd.status_code in (200, 201)
                            and not body.get('errorCode')
                            and not body.get('errors')
                            and (body.get('changeType') == 'UPDATE'
                                 or body.get('changedEntityId') is not None)):

                        new_owner_first = rec_first or ''
                        new_owner_last = rec_last or ''
                        if not new_owner_first and not new_owner_last:
                            try:
                                user_resp = _requests.get(
                                    f"{base_url}entity/CorporateUser/{recruiter_id}",
                                    headers=headers,
                                    params={'fields': 'id,firstName,lastName'},
                                    timeout=10,
                                )
                                if user_resp.status_code == 200:
                                    ud = user_resp.json().get('data', {})
                                    new_owner_first = ud.get('firstName', '')
                                    new_owner_last = ud.get('lastName', '')
                            except Exception:
                                pass

                        old_name = f"{old_owner_first} {old_owner_last}".strip() or "API User"
                        new_name = f"{new_owner_first} {new_owner_last}".strip() or str(recruiter_id)
                        logger.info(
                            f"✅ owner_reassignment: candidate {candidate_id} "
                            f"({c_first} {c_last}) reassigned "
                            f"{old_name} → {new_name}"
                        )
                        reassigned += 1

                        if note_enabled:
                            try:
                                note_text = _build_note_text(
                                    c_first, c_last,
                                    old_owner_first, old_owner_last,
                                    new_owner_first, new_owner_last,
                                )
                                note_url = f"{base_url}entity/Note"
                                note_data = {
                                    'personReference': {'id': int(candidate_id)},
                                    'action': 'Owner Reassignment',
                                    'comments': note_text,
                                    'isDeleted': False,
                                    'candidates': [{'id': int(candidate_id)}],
                                }
                                _requests.put(
                                    note_url,
                                    headers=headers,
                                    json=note_data,
                                    timeout=30,
                                )
                            except Exception as note_err:
                                logger.warning(
                                    f"owner_reassignment: note creation failed for "
                                    f"candidate {candidate_id}: {note_err}"
                                )
                    else:
                        err_msg = f"Candidate {candidate_id}: HTTP {upd.status_code} — {body}"
                        logger.warning(f"owner_reassignment: update failed for candidate {candidate_id}: HTTP {upd.status_code} — {body}")
                        failed += 1
                        errors.append(err_msg)

                except Exception as rec_err:
                    err_msg = f"Candidate {candidate_id}: {rec_err}"
                    logger.error(f"owner_reassignment: error processing candidate {candidate_id}: {rec_err}")
                    failed += 1
                    errors.append(err_msg)

                time.sleep(0.1)

            skipped_total = skipped_no_activity + skipped_already_correct
            logger.info(
                f"owner_reassignment: complete — {reassigned} reassigned, "
                f"{skipped_no_activity} skipped (no human activity), "
                f"{skipped_already_correct} skipped (already correct), "
                f"{failed} failed"
            )
            return {
                'reassigned': reassigned,
                'skipped': skipped_total,
                'failed': failed,
                'errors': errors,
            }

        except Exception as e:
            logger.error(f"owner_reassignment: unexpected error — {e}", exc_info=True)
            return {'reassigned': 0, 'skipped': 0, 'failed': 0, 'errors': [str(e)]}


def run_owner_reassignment() -> dict:
    """
    Manual trigger for the owner reassignment batch, intended for on-demand
    use from the Automation Hub.

    Processes candidates modified in the last 30 days so it can serve as a
    backfill on day one before the scheduler takes over regular 30-minute runs.

    Returns a dict with keys: reassigned, skipped, failed, errors (list of str).
    """
    logger.info("owner_reassignment: manual live batch triggered via Automation Hub")
    return reassign_api_user_candidates(since_minutes=43200)


def run_owner_reassignment_daily() -> dict:
    """
    Daily deep sweep: re-evaluates all API-owned candidates modified in the
    last 90 days.  This catches late follow-ups — recruiters who interact with
    older candidates days or weeks after intake.

    Registered as a separate scheduler job (owner_reassignment_daily) that runs
    once per day.
    """
    logger.info("owner_reassignment_daily: starting 90-day deep sweep")
    return reassign_api_user_candidates(since_minutes=129600)
