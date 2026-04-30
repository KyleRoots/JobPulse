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
import json as _json
import time
import logging
import threading
from collections import Counter
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import requests as _requests

from bullhorn_service import BullhornService

logger = logging.getLogger(__name__)

# Source labels used by _write_run_history to decide whether to always-write
# (manual + daily sweep) or apply the signal-only noise filter (5-min cycle).
SOURCE_SCHEDULED_5MIN = 'scheduled_5min'
SOURCE_SCHEDULED_DAILY = 'scheduled_daily'
SOURCE_MANUAL_LIVE_BATCH = 'manual_live_batch'

# [diagnostic] Tracks the set of candidate IDs returned by the previous
# 5-min cycle so we can measure how much overlap exists cycle-over-cycle.
# A high overlap percentage means the same records are being touched
# repeatedly (i.e. some other job keeps re-modifying them); low overlap
# means a steady stream of genuinely new modifications.
#
# Concurrency: the reassignment job is gated by the primary-worker
# scheduler lock and APScheduler's default max_instances=1, so two
# 5-min cycles cannot legitimately run at the same time. The lock below
# is belt-and-suspenders for any future code path that calls into this
# function from another thread (manual triggers, etc.). Read-only
# diagnostic, no behavior impact.
_PREV_5MIN_CANDIDATE_IDS: set = set()
_PREV_5MIN_CYCLE_AT: Optional[datetime] = None
_PREV_5MIN_LOCK = threading.Lock()

_SOURCE_DISPLAY = {
    SOURCE_SCHEDULED_5MIN: 'Owner Reassignment (5 min)',
    SOURCE_SCHEDULED_DAILY: 'Owner Reassignment (Daily Sweep)',
    SOURCE_MANUAL_LIVE_BATCH: 'Owner Reassignment (Manual Live Batch)',
}

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


# ──────────────────────────────────────────────────────────────────────────
# Per-candidate cooldown bandage
# ──────────────────────────────────────────────────────────────────────────
# The 5-min cycle was re-evaluating the same ~5,000 Pandologic / Matador /
# Myticas candidates every cycle (99-100% overlap, confirmed via diagnostic
# logs). Each evaluation costs a paginated Bullhorn Notes search per
# candidate, so the cycle was running 15-20 minutes and consuming API quota
# for repeated no-ops.
#
# This bandage records the outcome of every no-op evaluation in the
# `owner_reassignment_cooldown` table and skips any candidate whose previous
# no-op was within the cooldown window (default 24 h). A successful
# reassign deletes the row so the candidate disappears from the cooldown
# pool entirely. A failed update leaves the row absent so the next cycle
# retries cleanly.
#
# Kill switches (VettingConfig keys, runtime-tunable):
#   - owner_reassignment_cooldown_enabled  ('true' | 'false', default 'true')
#   - owner_reassignment_cooldown_hours    (int as string, default '24')
#
# Outcomes recorded:
#   - 'no_human_activity'  candidate has no recruiter notes yet
#   - 'already_correct'    owner is already the right human

_COOLDOWN_NO_ACTIVITY = 'no_human_activity'
_COOLDOWN_ALREADY_CORRECT = 'already_correct'


def _cooldown_enabled() -> bool:
    """Read the cooldown kill switch. Defaults to enabled."""
    return _get_vetting_config(
        'owner_reassignment_cooldown_enabled', 'true'
    ).strip().lower() != 'false'


def _cooldown_hours() -> int:
    """Read the cooldown window in hours. Defaults to 24. Clamped to [1, 720]."""
    raw = _get_vetting_config('owner_reassignment_cooldown_hours', '24').strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 24
    if value < 1:
        return 1
    if value > 720:  # 30 days
        return 720
    return value


def _fetch_active_cooldown_ids(candidate_ids: List[int]) -> set:
    """
    Return the subset of `candidate_ids` whose cooldown is still active
    (last_evaluated_at within the window). Empty set if cooldown disabled or
    on any DB failure (fail-open: a broken cooldown table must NEVER block
    the legitimate reassignment work).
    """
    if not candidate_ids or not _cooldown_enabled():
        return set()
    try:
        from app import db
        from models import OwnerReassignmentCooldown
        cutoff = datetime.utcnow() - timedelta(hours=_cooldown_hours())
        rows = (
            db.session.query(OwnerReassignmentCooldown.candidate_id)
            .filter(OwnerReassignmentCooldown.candidate_id.in_(candidate_ids))
            .filter(OwnerReassignmentCooldown.last_evaluated_at >= cutoff)
            .all()
        )
        return {int(r[0]) for r in rows}
    except Exception as exc:
        logger.warning(
            f"owner_reassignment: cooldown lookup failed (fail-open) — {exc}"
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return set()


def _flush_cooldown_outcomes(outcomes: List[Tuple[int, str]]) -> None:
    """
    Upsert a batch of (candidate_id, outcome) pairs into the cooldown table.
    Uses PostgreSQL INSERT ... ON CONFLICT to bump `last_evaluated_at` and
    increment `evaluation_count` when the row already exists.

    Caller has typically just done `db.session.remove()` so this write runs
    on a fresh connection. Failures are logged and swallowed — the cooldown
    is a bandage, never a blocker.

    The kill switch (`owner_reassignment_cooldown_enabled=false`) fully
    disables writes too; we never want to "secretly" populate the cooldown
    table while the operator believes the bandage is off.
    """
    if not outcomes or not _cooldown_enabled():
        return
    try:
        from app import db
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from models import OwnerReassignmentCooldown
        now = datetime.utcnow()
        # Dedupe: PostgreSQL raises "ON CONFLICT DO UPDATE command cannot
        # affect row a second time" if the same candidate_id appears twice
        # in a single VALUES batch. Keep the *last* outcome seen (loop order
        # is the natural "most recent decision wins" semantic).
        deduped: dict = {}
        for cid, outcome in outcomes:
            deduped[int(cid)] = outcome
        rows = [
            {
                'candidate_id': cid,
                'last_evaluated_at': now,
                'last_outcome': outcome,
                'evaluation_count': 1,
            }
            for cid, outcome in deduped.items()
        ]
        stmt = pg_insert(OwnerReassignmentCooldown).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=['candidate_id'],
            set_={
                'last_evaluated_at': stmt.excluded.last_evaluated_at,
                'last_outcome': stmt.excluded.last_outcome,
                'evaluation_count': (
                    OwnerReassignmentCooldown.evaluation_count + 1
                ),
            },
        )
        db.session.execute(stmt)
        db.session.commit()
    except Exception as exc:
        logger.warning(
            f"owner_reassignment: cooldown flush failed for {len(outcomes)} "
            f"row(s) — {exc}"
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def _clear_cooldown_for_candidate(candidate_id: int) -> None:
    """
    Drop the cooldown row for a candidate that was just successfully
    reassigned. Best-effort; failures are logged and ignored.

    Honors the kill switch — if cooldown is disabled the operator's
    expectation is "no DB writes from the bandage at all," which includes
    DELETEs.
    """
    if not _cooldown_enabled():
        return
    try:
        from app import db
        from models import OwnerReassignmentCooldown
        db.session.query(OwnerReassignmentCooldown).filter_by(
            candidate_id=int(candidate_id)
        ).delete(synchronize_session=False)
        db.session.commit()
    except Exception as exc:
        logger.debug(
            f"owner_reassignment: could not clear cooldown row for "
            f"candidate {candidate_id} — {exc}"
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def _get_or_create_owner_task_id() -> Optional[int]:
    """
    Find (or lazily create) the AutomationTask row used to anchor Run History
    entries for owner reassignment cycles. Returns the task id, or None if the
    DB write fails (in which case we just skip the history write).

    Uses a single shared task row across all three sources (5-min, daily,
    manual live batch); the source label is captured in the log details so the
    panel still shows which path produced each row.

    Race-safety: if two threads (e.g. the scheduler and a manual button click)
    hit a fresh DB simultaneously, both could try to insert. We mitigate by:
      1. Always selecting the LOWEST id when multiple rows exist (canonical row).
      2. On IntegrityError or any commit failure, rolling back and re-querying.
    """
    from app import db
    from models import AutomationTask
    config_marker = '"builtin_key": "owner_reassignment"'

    def _find_canonical():
        return AutomationTask.query.filter(
            AutomationTask.config_json.contains(config_marker)
        ).order_by(AutomationTask.id.asc()).first()

    try:
        task = _find_canonical()
        if task:
            return task.id

        task = AutomationTask(
            name='Owner Reassignment',
            description=(
                'API User → Recruiter ownership reassignment. Runs every 5 minutes '
                '(30-min lookback), nightly at 02:00 UTC (90-day deep sweep), and '
                'on demand from the Automation Hub Live Batch button.'
            ),
            status='active',
            automation_type='scheduled',
            schedule_cron='*/5 * * * *',
            config_json=_json.dumps({'builtin_key': 'owner_reassignment'}),
        )
        db.session.add(task)
        try:
            db.session.commit()
            return task.id
        except Exception as commit_err:
            db.session.rollback()
            logger.info(
                f"owner_reassignment: insert race detected ({commit_err}); "
                "falling back to existing row"
            )
            existing = _find_canonical()
            return existing.id if existing else None
    except Exception as exc:
        logger.warning(f"owner_reassignment: could not get/create AutomationTask row — {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


# Cap reassigned-ID list stored in details_json so a runaway batch can't bloat
# the log row. The summary still reports the true total via 'reassigned' count.
_MAX_IDS_IN_DETAILS = 200


def _write_run_history(result: dict, source: str) -> None:
    """
    Write an AutomationLog row so the Automation Hub's Run History panel shows
    owner reassignment activity.

    Noise filter (5-min cycle only):
      - Suppresses no-op cycles (no candidates touched, no errors) to avoid
        flooding the panel with 288 empty rows per day.
      - Anything with a real signal (reassigned > 0, failed > 0, errors,
        or guard-rail failures the operator should see) is always written.

    Daily sweep + manual live batch always write — those are meaningful
    checkpoints regardless of outcome.
    """
    try:
        reassigned = int(result.get('reassigned', 0))
        failed = int(result.get('failed', 0))
        errors = result.get('errors') or []
        reassigned_ids = result.get('reassigned_ids') or []
        skipped = int(result.get('skipped', 0))
        cooldown_skipped = int(result.get('cooldown_skipped', 0))

        is_signal = reassigned > 0 or failed > 0 or bool(errors)
        always_write = source in (SOURCE_SCHEDULED_DAILY, SOURCE_MANUAL_LIVE_BATCH)
        if not (is_signal or always_write):
            return

        # The candidate-processing loop can run for 15-20+ minutes between the
        # initial VettingConfig reads and this write. By that time PostgreSQL
        # has typically closed the idle SSL connection, so the next DB op
        # would fail with "SSL connection has been closed unexpectedly". Drop
        # the stale session here so the write below pulls a fresh connection
        # from the pool. Without this, daily-sweep + manual-batch Run History
        # rows silently fail to commit.
        from app import db as _db_pre
        try:
            _db_pre.session.remove()
        except Exception:
            pass

        task_id = _get_or_create_owner_task_id()
        if task_id is None:
            return

        if errors and reassigned == 0 and failed == 0:
            status = 'error'
        elif failed > 0:
            status = 'warning'
        else:
            status = 'success'

        message = _SOURCE_DISPLAY.get(source, 'Owner Reassignment')
        summary = (
            f"{reassigned} reassigned, {skipped} skipped, "
            f"{cooldown_skipped} cooldown-skipped, {failed} failed"
        )

        ids_total = len(reassigned_ids)
        ids_truncated = ids_total > _MAX_IDS_IN_DETAILS
        details = {
            'source': source,
            'reassigned': reassigned,
            'skipped': skipped,
            'cooldown_skipped': cooldown_skipped,
            'failed': failed,
            'reassigned_candidate_ids': reassigned_ids[:_MAX_IDS_IN_DETAILS],
            'reassigned_ids_total': ids_total,
            'reassigned_ids_truncated': ids_truncated,
            'summary': summary,
        }
        if errors:
            details['errors'] = [str(e)[:300] for e in errors[:10]]

        from app import db
        from models import AutomationTask, AutomationLog
        log = AutomationLog(
            automation_task_id=task_id,
            status=status,
            message=message,
            details_json=_json.dumps(details),
        )
        db.session.add(log)

        task = AutomationTask.query.get(task_id)
        if task:
            task.last_run_at = datetime.utcnow()
            task.run_count = (task.run_count or 0) + 1

        db.session.commit()
    except Exception as exc:
        logger.warning(f"owner_reassignment: could not write run history row — {exc}")
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def _early_return(
    source: str,
    reason: str,
    *,
    log_for_5min: bool = False,
) -> dict:
    """
    Build the early-return dict for guard-rail exits and write a Run History
    row when appropriate.

    Policy:
      - Daily sweep + manual live batch: ALWAYS surface guard-rail failures
        in Run History (the user/operator wants to know why the run no-op'd).
      - 5-min cycle: only surface when `log_for_5min=True` (e.g. auth or
        search failures — operator-actionable). Routine "feature disabled"
        or "no IDs configured" stay silent for the 5-min cycle since those
        are intentional configuration states, not errors.
    """
    result = {
        'reassigned': 0, 'skipped': 0, 'cooldown_skipped': 0, 'failed': 0,
        'errors': [reason], 'reassigned_ids': [],
    }
    is_user_initiated = source in (SOURCE_SCHEDULED_DAILY, SOURCE_MANUAL_LIVE_BATCH)
    if is_user_initiated or log_for_5min:
        _write_run_history(result, source)
    return result


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

            # Cooldown state for the previewed sample so the operator can
            # see which candidates would be skipped by the live cycle.
            sample_ids = [c.get('id') for c in sample if c.get('id')]
            cooldown_active = _fetch_active_cooldown_ids(sample_ids)
            cooldown_on = _cooldown_enabled()
            cooldown_window_h = _cooldown_hours()

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

                in_cooldown = (
                    cooldown_on and int(candidate_id) in cooldown_active
                )

                entry = {
                    'candidate_id': candidate_id,
                    'name': f"{c_first} {c_last}".strip(),
                    'current_owner': old_owner_name,
                    'current_owner_id': old_owner_id,
                    'would_reassign': False,
                    'skip_reason': None,
                    'in_cooldown': in_cooldown,
                }

                if in_cooldown:
                    # Live cycle would short-circuit here; don't pay the
                    # Bullhorn-notes-search cost in the preview either.
                    entry['skip_reason'] = (
                        f'In cooldown — last evaluated within '
                        f'{cooldown_window_h} h'
                    )
                    results.append(entry)
                    continue

                recruiter_id, rec_first, rec_last = _find_first_human_interactor(
                    base_url, headers, candidate_id, api_user_ids
                )

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
                'cooldown_enabled': cooldown_on,
                'cooldown_window_hours': cooldown_window_h,
                'cooldown_active_in_sample': len(cooldown_active),
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


def reassign_api_user_candidates(
    since_minutes: int = 30,
    source: str = SOURCE_SCHEDULED_5MIN,
) -> dict:
    """
    Main entry point for the scheduled task.

    Runs inside an app context (APScheduler worker). Reads VettingConfig for
    the toggle, the configured API user IDs, and the note toggle. For each
    candidate owned by an API user account, finds the first human recruiter
    who interacted (via Bullhorn Notes) and updates the ownership record.

    The `source` param controls Run History behavior:
      - SOURCE_SCHEDULED_5MIN  → noise-filtered (only writes on signal)
      - SOURCE_SCHEDULED_DAILY → always writes (daily checkpoint)
      - SOURCE_MANUAL_LIVE_BATCH → always writes (user-initiated)

    Returns a dict with keys:
      reassigned        int        count of successful reassigns
      skipped           int        count of skipped candidates
      failed            int        count of failed updates
      errors            list[str]  per-candidate error messages
      reassigned_ids    list[int]  Bullhorn candidate IDs that were updated
    """
    from app import app

    with app.app_context():
        try:
            if _get_vetting_config('auto_reassign_owner_enabled', 'false').lower() != 'true':
                logger.debug("owner_reassignment: feature disabled — skipping run")
                # 5-min: silent (intentional config state, not noise-worthy).
                # Daily/manual: surface so operator sees why the run no-op'd.
                return _early_return(
                    source,
                    'Feature is disabled. Enable the automation toggle first.',
                )

            api_user_ids = _parse_api_user_ids(
                _get_vetting_config('api_user_ids', '')
            )
            if not api_user_ids:
                logger.info(
                    "owner_reassignment: no API user IDs configured — skipping run. "
                    "Add Bullhorn CorporateUser IDs to the 'api_user_ids' setting."
                )
                return _early_return(source, 'No API user IDs configured.')

            note_enabled = (
                _get_vetting_config('reassign_owner_note_enabled', 'true').lower() == 'true'
            )

            bh = BullhornService()
            if not bh.authenticate():
                logger.warning("owner_reassignment: Bullhorn authentication failed — skipping run")
                # Auth failure is operator-actionable — surface even on the
                # 5-min cycle so a broken token doesn't fail silently for hours.
                return _early_return(
                    source,
                    'Bullhorn authentication failed.',
                    log_for_5min=True,
                )

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
                    # Bullhorn API failure is operator-actionable — surface
                    # on every source including the 5-min cycle.
                    return _early_return(
                        source,
                        f'Candidate search failed: HTTP {resp.status_code}',
                        log_for_5min=True,
                    )

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
                empty_result = {
                    'reassigned': 0, 'skipped': 0, 'cooldown_skipped': 0,
                    'failed': 0, 'errors': [], 'reassigned_ids': [],
                }
                _write_run_history(empty_result, source)
                return empty_result

            logger.info(
                f"owner_reassignment: found {len(candidates)} API-owned candidate(s) "
                f"to evaluate (owner IDs: {api_user_ids})"
            )

            # ──────────────────────────────────────────────────────────────
            # Cooldown filter — skip candidates whose previous no-op
            # evaluation is still inside the cooldown window. This is the
            # bandage that prevents the 5-min cycle from re-walking the
            # same ~5,000 records every time. Fail-open: any DB error
            # returns an empty cooldown set so the legitimate work proceeds.
            # ──────────────────────────────────────────────────────────────
            cooldown_skipped_count = 0
            if _cooldown_enabled():
                all_ids = [c.get('id') for c in candidates if c.get('id')]
                cooldown_active_ids = _fetch_active_cooldown_ids(all_ids)
                if cooldown_active_ids:
                    pre_filter_count = len(candidates)
                    candidates = [
                        c for c in candidates
                        if int(c.get('id') or 0) not in cooldown_active_ids
                    ]
                    cooldown_skipped_count = pre_filter_count - len(candidates)
                    logger.info(
                        f"owner_reassignment: cooldown filter — "
                        f"skipped {cooldown_skipped_count:,} of "
                        f"{pre_filter_count:,} candidate(s) "
                        f"(window: {_cooldown_hours()} h); "
                        f"{len(candidates):,} remain to evaluate"
                    )
                else:
                    logger.debug(
                        "owner_reassignment: cooldown filter — no active "
                        "cooldown rows touched this batch"
                    )
            else:
                logger.info(
                    "owner_reassignment: cooldown disabled via "
                    "owner_reassignment_cooldown_enabled=false — evaluating "
                    "all candidates"
                )

            # ──────────────────────────────────────────────────────────────
            # [diagnostic] Owner breakdown + cycle-over-cycle overlap.
            # Goal: identify which API user owns the bulk of churning
            # candidates, and whether the same IDs keep re-appearing each
            # 5-min cycle (i.e. some other job is re-touching them).
            # Read-only; no behavior impact. See replit.md follow-ups.
            # ──────────────────────────────────────────────────────────────
            try:
                owner_counts: Counter = Counter()
                owner_names: dict = {}
                for c in candidates:
                    o = c.get('owner') or {}
                    oid = o.get('id')
                    if oid is None:
                        continue
                    owner_counts[oid] += 1
                    if oid not in owner_names:
                        owner_names[oid] = (
                            f"{o.get('firstName', '')} {o.get('lastName', '')}"
                        ).strip() or '(unnamed)'

                breakdown_str = ', '.join(
                    f"{owner_names.get(oid, '?')}(id={oid})={cnt:,}"
                    for oid, cnt in owner_counts.most_common()
                )
                logger.info(
                    f"owner_reassignment: [diagnostic] owner breakdown over "
                    f"last {since_minutes}min — {breakdown_str}"
                )

                if source == SOURCE_SCHEDULED_5MIN:
                    global _PREV_5MIN_CANDIDATE_IDS, _PREV_5MIN_CYCLE_AT
                    current_ids = {c.get('id') for c in candidates if c.get('id')}
                    with _PREV_5MIN_LOCK:
                        prev_ids_snapshot = _PREV_5MIN_CANDIDATE_IDS
                        prev_at_snapshot = _PREV_5MIN_CYCLE_AT
                        _PREV_5MIN_CANDIDATE_IDS = current_ids
                        _PREV_5MIN_CYCLE_AT = datetime.utcnow()

                    if prev_ids_snapshot:
                        overlap = current_ids & prev_ids_snapshot
                        new_ids = current_ids - prev_ids_snapshot
                        dropped_ids = prev_ids_snapshot - current_ids
                        overlap_pct = (
                            (len(overlap) / len(current_ids) * 100.0)
                            if current_ids else 0.0
                        )
                        prev_age_s = (
                            (datetime.utcnow() - prev_at_snapshot).total_seconds()
                            if prev_at_snapshot else 0
                        )
                        logger.info(
                            f"owner_reassignment: [diagnostic] cycle overlap — "
                            f"current={len(current_ids):,}, "
                            f"prev={len(prev_ids_snapshot):,} "
                            f"({prev_age_s:.0f}s ago), "
                            f"repeated={len(overlap):,} ({overlap_pct:.1f}%), "
                            f"new={len(new_ids):,}, dropped={len(dropped_ids):,}"
                        )
                    else:
                        logger.info(
                            f"owner_reassignment: [diagnostic] cycle overlap — "
                            f"first 5-min cycle seen, baseline only "
                            f"(current={len(current_ids):,})"
                        )
            except Exception as diag_err:
                # Diagnostics must never break the main flow.
                logger.debug(
                    f"owner_reassignment: [diagnostic] logging error (ignored): {diag_err}"
                )

            reassigned = 0
            skipped_no_activity = 0
            skipped_already_correct = 0
            failed = 0
            errors: list = []
            reassigned_ids: list = []
            # (candidate_id, outcome) tuples buffered during the loop and
            # flushed in a single bulk-upsert after the loop completes.
            cooldown_outcomes: List[Tuple[int, str]] = []
            successful_reassign_ids: List[int] = []

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
                    try:
                        cooldown_outcomes.append(
                            (int(candidate_id), _COOLDOWN_NO_ACTIVITY)
                        )
                    except (TypeError, ValueError):
                        pass
                    continue

                old_owner_id = old_owner.get('id')
                if old_owner_id and int(old_owner_id) == int(recruiter_id):
                    logger.debug(
                        f"owner_reassignment: skipping candidate {candidate_id} "
                        f"({c_first} {c_last}) — already owned by correct user ({recruiter_id})"
                    )
                    skipped_already_correct += 1
                    try:
                        cooldown_outcomes.append(
                            (int(candidate_id), _COOLDOWN_ALREADY_CORRECT)
                        )
                    except (TypeError, ValueError):
                        pass
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
                        try:
                            reassigned_ids.append(int(candidate_id))
                            successful_reassign_ids.append(int(candidate_id))
                        except (TypeError, ValueError):
                            reassigned_ids.append(candidate_id)

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
                f"{cooldown_skipped_count} cooldown-skipped, "
                f"{failed} failed"
            )

            # Flush cooldown bookkeeping. Drop the stale long-lived session
            # first so these writes run on a fresh connection (mirrors the
            # pattern in _write_run_history). Failures are swallowed inside
            # the helpers — cooldown bookkeeping must never block the run.
            try:
                from app import db as _db_pre_cooldown
                try:
                    _db_pre_cooldown.session.remove()
                except Exception:
                    pass

                if cooldown_outcomes:
                    _flush_cooldown_outcomes(cooldown_outcomes)
                    logger.info(
                        f"owner_reassignment: cooldown flush — recorded "
                        f"{len(cooldown_outcomes):,} no-op outcome(s) "
                        f"(window: {_cooldown_hours()} h)"
                    )
                for cid in successful_reassign_ids:
                    _clear_cooldown_for_candidate(cid)
            except Exception as cooldown_err:
                logger.warning(
                    f"owner_reassignment: cooldown bookkeeping wrapper "
                    f"failed (non-fatal) — {cooldown_err}"
                )

            result = {
                'reassigned': reassigned,
                'skipped': skipped_total,
                'cooldown_skipped': cooldown_skipped_count,
                'failed': failed,
                'errors': errors,
                'reassigned_ids': reassigned_ids,
            }
            _write_run_history(result, source)
            return result

        except Exception as e:
            logger.error(f"owner_reassignment: unexpected error — {e}", exc_info=True)
            error_result = {
                'reassigned': 0, 'skipped': 0, 'cooldown_skipped': 0,
                'failed': 0, 'errors': [str(e)], 'reassigned_ids': [],
            }
            _write_run_history(error_result, source)
            return error_result
        finally:
            # The candidate loop can run far longer than PostgreSQL's idle
            # connection timeout, leaving any session opened during the
            # initial VettingConfig reads in a half-dead state. Without this
            # explicit cleanup, Flask-SQLAlchemy's app-context teardown
            # triggers do_rollback() on a closed SSL connection, which
            # APScheduler logs as "raised an exception" after every cycle.
            try:
                from app import db as _db_post
                _db_post.session.remove()
            except Exception:
                pass


def run_owner_reassignment() -> dict:
    """
    Manual trigger for the owner reassignment batch, intended for on-demand
    use from the Automation Hub.

    Processes candidates modified in the last 30 days so it can serve as a
    backfill on day one before the scheduler takes over regular 30-minute runs.

    Returns a dict with keys: reassigned, skipped, failed, errors, reassigned_ids.
    """
    logger.info("owner_reassignment: manual live batch triggered via Automation Hub")
    return reassign_api_user_candidates(
        since_minutes=43200,
        source=SOURCE_MANUAL_LIVE_BATCH,
    )


def run_owner_reassignment_daily() -> dict:
    """
    Daily deep sweep: re-evaluates all API-owned candidates modified in the
    last 90 days.  This catches late follow-ups — recruiters who interact with
    older candidates days or weeks after intake.

    Registered as a separate scheduler job (owner_reassignment_daily) that runs
    once per day.
    """
    logger.info("owner_reassignment_daily: starting 90-day deep sweep")
    return reassign_api_user_candidates(
        since_minutes=129600,
        source=SOURCE_SCHEDULED_DAILY,
    )
