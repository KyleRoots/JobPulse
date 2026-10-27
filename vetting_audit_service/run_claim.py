"""Per-candidate run claims shared by audit re-vets and the vetting worker.

Protocol, per Bullhorn candidate ``c``:

* ``L(c)``: a transaction-scoped PostgreSQL advisory lock. Revet resets, the
  pending-revet detector's per-candidate decision, worker claim, lease
  recovery or release, audit back-fill and fenced result commits each run as
  one short transaction that takes ``L(c)`` before re-reading its guards. No
  transaction stays open across Bullhorn or model calls. Every wait is
  bounded by ``lock_timeout``: a timeout is unknown state, never success.
* Lock order: ``L(c)`` before any row lock, with one exception — a worker may
  already hold its own lease row (an autoflushed update) when it takes
  ``L(c)`` for its fenced commit. Recovery therefore waits on that row for
  less than PostgreSQL's ``deadlock_timeout`` and gives up, so the owner wins.
  Heartbeats take no advisory lock at all.
* Lease: a ``CandidateVettingLog`` row with status ``processing``. Only
  ``claim_run`` creates one. Its id is the fencing token: a worker commits
  results only while that row is still ``processing`` (``commit_if_lease_held``),
  so a worker whose lease was recovered or failed cannot commit after
  replacement. While the worker runs, ``RunLease`` heartbeats ``updated_at``
  from its own thread, including during blocking Bullhorn and model calls, so
  a lease only expires once its process has stopped heartbeating for
  ``LEASE_TIMEOUT_MINUTES``. Only ``recover_expired_lease`` may end a lease
  nobody released; ``release_lease`` ends the worker's own.
* Renewal result is ``held``, ``lost`` or ``unknown``. Lost or unknown stops
  the run: no new paid call starts, calls already in flight finish but their
  results are discarded, nothing is committed, and an ``unknown`` worker
  releases its lease (or leaves it to expire when even that fails). After an
  unknown renewal the heartbeat keeps renewing until the run stops or the
  lease is lost, so a transient database failure does not let recovery take
  the candidate while in-flight calls drain. A heartbeat that has not
  confirmed the lease for ``STALE_HELD_SECONDS`` (a stalled connection) is
  treated as unknown.
* Audit binding: ``VettingAuditLog.revet_vetting_log_id``. ``claim_run`` binds
  every audit row still waiting on a score to the run it creates, inside the
  same transaction. A run therefore covers only audit requests that were
  committed before it started. An audit created while a run is in flight
  stays unbound and pending.

Invariants:

1. A reset never deletes a ``processing`` row, whatever its age; it defers.
2. Rows a reset deletes are the ones it read under ``L(c)``, with
   ``status <> 'processing'`` in the DELETE itself.
3. A queued item starts a run only if its queue entry is still open under
   ``L(c)``: a re-vet item's audit is still unbound (or bound to a dead run),
   and an email item's ParsedEmail has no vetting log yet.
4. A re-vet item does not start while another lease for the candidate is live.
5. Unknown guard state (lock or lookup failure) never leads to a delete,
   re-queue, claim or score write.
6. An open audit's binding and ``revet_new_score`` change only under ``L(c)``,
   and a score is written only by ``write_bound_score``: a conditional update
   on the expected binding and a still-null score, from a run that is
   ``completed`` when re-read under the same lock.
7. Recovery never deletes job matches: an expired run with persisted matches
   is marked ``failed`` with them kept (its ParsedEmail id moved into the
   error message so the email can be claimed again), and its audits are
   released.

Off PostgreSQL (the SQLite test database) the lock is a no-op: SQLite allows
one writer at a time, and nothing here claims concurrency safety from it.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# Distinct from the Scout session lock (0x5C0E7) so the two never contend.
CANDIDATE_RUN_LOCK_NAMESPACE = 0x52564554

LEASE_TIMEOUT_MINUTES = 10
HEARTBEAT_INTERVAL_SECONDS = 60

LOCK_TIMEOUT_MS = 5000
# Below PostgreSQL's default deadlock_timeout (1s): recovery yields to a
# worker that holds its own lease row and is waiting for L(c).
RECOVERY_LOCK_TIMEOUT_MS = 500
RENEW_LOCK_TIMEOUT_MS = 5000
# Server-side bound on a renewal that is not waiting on a lock.
RENEW_STATEMENT_TIMEOUT_MS = 10000
# Neither timeout bounds a stalled network: a run whose last confirmed
# renewal is older than this refuses new paid calls, well inside the lease.
STALE_HELD_SECONDS = LEASE_TIMEOUT_MINUTES * 60 // 2
HEARTBEAT_STOP_TIMEOUT_SECONDS = 15.0

OPEN_AUDIT_ACTIONS = ('revet_triggered', 'revet_skipped_stable')
LIVE_RUN_STATUSES = ('processing', 'completed')

CLAIM_OUTCOME_KEY = '_run_claim_outcome'

LEASE_HELD = 'held'
LEASE_LOST = 'lost'
LEASE_UNKNOWN = 'unknown'


class CandidateLockUnavailable(RuntimeError):
    """The candidate lock could not be taken, so guard state is unknown."""


def _is_postgres() -> bool:
    from app import db

    return db.session.get_bind().dialect.name == 'postgresql'


def _set_lock_timeout(timeout_ms: int) -> None:
    from sqlalchemy import text
    from app import db

    if _is_postgres():
        db.session.execute(text(f"SET LOCAL lock_timeout = '{int(timeout_ms)}ms'"))


def acquire_candidate_lock(candidate_id: int, timeout_ms: Optional[int] = None) -> None:
    """Take ``L(c)``. The timeout (default ``LOCK_TIMEOUT_MS``) also bounds
    every later lock wait in the same transaction."""
    from sqlalchemy import text
    from app import db

    if not _is_postgres():
        return
    try:
        _set_lock_timeout(LOCK_TIMEOUT_MS if timeout_ms is None else timeout_ms)
        db.session.execute(
            text('SELECT pg_advisory_xact_lock(:ns, :cid)'),
            {'ns': CANDIDATE_RUN_LOCK_NAMESPACE, 'cid': int(candidate_id)},
        )
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        raise CandidateLockUnavailable(
            f"candidate {candidate_id} run lock unavailable "
            f"({type(e).__name__}: {e})"
        ) from e


def live_lease_ids(candidate_id: int) -> list:
    from app import db
    from models import CandidateVettingLog

    return [
        row_id for (row_id,) in db.session.query(CandidateVettingLog.id).filter(
            CandidateVettingLog.bullhorn_candidate_id == candidate_id,
            CandidateVettingLog.status == 'processing',
        )
    ]


def run_statuses(run_ids: Iterable[int]) -> Dict[int, Optional[str]]:
    from app import db
    from models import CandidateVettingLog

    ids = [i for i in set(run_ids) if i is not None]
    if not ids:
        return {}
    return dict(
        db.session.query(CandidateVettingLog.id, CandidateVettingLog.status)
        .filter(CandidateVettingLog.id.in_(ids))
        .all()
    )


def write_bound_score(audit_id: int, run_id: int, score: float) -> bool:
    """Set an open audit's score iff it is still bound to ``run_id`` and
    unscored. The caller holds ``L(c)`` and has re-read ``run_id`` as
    ``completed`` under it."""
    from models import VettingAuditLog

    return VettingAuditLog.query.filter(
        VettingAuditLog.id == audit_id,
        VettingAuditLog.revet_vetting_log_id == run_id,
        VettingAuditLog.revet_new_score.is_(None),
    ).update({'revet_new_score': score}, synchronize_session=False) == 1


def _bind_open_audits(candidate_id: int, run_id: int) -> int:
    """Bind audits awaiting a score that no live run covers to ``run_id``."""
    from app import db
    from models import VettingAuditLog

    open_rows = (
        db.session.query(VettingAuditLog.id, VettingAuditLog.revet_vetting_log_id)
        .filter(
            VettingAuditLog.bullhorn_candidate_id == candidate_id,
            VettingAuditLog.action_taken.in_(OPEN_AUDIT_ACTIONS),
            VettingAuditLog.revet_new_score.is_(None),
        )
        .all()
    )
    statuses = run_statuses(bound for _, bound in open_rows)
    to_bind = [
        audit_id for audit_id, bound in open_rows
        if bound is None or statuses.get(bound) not in LIVE_RUN_STATUSES
    ]
    if to_bind:
        VettingAuditLog.query.filter(VettingAuditLog.id.in_(to_bind)).update(
            {'revet_vetting_log_id': run_id}, synchronize_session=False,
        )
    return len(to_bind)


def _defer(candidate: Dict, reason: str) -> None:
    from app import db

    db.session.rollback()
    candidate[CLAIM_OUTCOME_KEY] = 'deferred'
    logger.info(f"⏸️ Run claim deferred for candidate {candidate.get('id')}: {reason}")


def claim_run(candidate: Dict, vetting_log):
    """Insert ``vetting_log`` as the candidate's lease if its queue entry is open.

    Returns the committed log, or None after rolling back when the claim is
    deferred (``candidate[CLAIM_OUTCOME_KEY] == 'deferred'``). The caller must
    then leave the queue entry untouched: the email stays unvetted and the
    audit stays pending. Raises ``CandidateLockUnavailable``.
    """
    from app import db
    from models import CandidateVettingLog, ParsedEmail, VettingAuditLog

    candidate_id = candidate.get('id')
    acquire_candidate_lock(candidate_id)

    audit_id = candidate.get('_pending_revet_audit_id')
    if audit_id is not None:
        audit = (
            db.session.query(
                VettingAuditLog.action_taken,
                VettingAuditLog.revet_new_score,
                VettingAuditLog.revet_vetting_log_id,
            )
            .filter(VettingAuditLog.id == audit_id)
            .first()
        )
        if audit is None or audit.action_taken != 'revet_triggered' or audit.revet_new_score is not None:
            _defer(candidate, f"audit row {audit_id} no longer awaits a re-vet")
            return None
        if audit.revet_vetting_log_id is not None and (
            run_statuses([audit.revet_vetting_log_id]).get(audit.revet_vetting_log_id)
            in LIVE_RUN_STATUSES
        ):
            _defer(
                candidate,
                f"audit row {audit_id} is covered by vetting log "
                f"{audit.revet_vetting_log_id}",
            )
            return None
        live = live_lease_ids(candidate_id)
        if live:
            _defer(candidate, f"vetting run(s) {live} in flight")
            return None

    parsed_email_id = candidate.get('_parsed_email_id')
    if parsed_email_id is not None:
        vetted_at = (
            db.session.query(ParsedEmail.vetted_at)
            .filter(ParsedEmail.id == parsed_email_id)
            .scalar()
        )
        claimed_by = (
            db.session.query(CandidateVettingLog.id)
            .filter(CandidateVettingLog.parsed_email_id == parsed_email_id)
            .first()
        )
        if vetted_at is not None or claimed_by is not None:
            _defer(
                candidate,
                f"ParsedEmail {parsed_email_id} already claimed "
                f"(vetting log {claimed_by[0] if claimed_by else None})",
            )
            return None

    try:
        db.session.add(vetting_log)
        db.session.flush()
        bound = _bind_open_audits(candidate_id, vetting_log.id)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    candidate[CLAIM_OUTCOME_KEY] = 'claimed'
    if bound:
        logger.info(
            f"🔗 Vetting log {vetting_log.id} covers {bound} pending audit "
            f"row(s) for candidate {candidate_id}"
        )
    return vetting_log


def _lease_held(vetting_log_id: int) -> bool:
    """Committed lease status, read beside the worker's own transaction.

    The worker's session may already have flushed its terminal status
    (autoflush), so reading through it would mistake the worker's own write
    for a lost lease. The caller holds ``L(c)``, so the committed row cannot
    change under this read.
    """
    from sqlalchemy import select
    from app import db
    from models import CandidateVettingLog

    table = CandidateVettingLog.__table__
    with db.engine.connect() as conn:
        status = conn.execute(
            select(table.c.status).where(table.c.id == vetting_log_id)
        ).scalar()
    return status == 'processing'


def commit_if_lease_held(candidate_id: int, vetting_log_id: int,
                         still_owned=None) -> bool:
    """Commit the session's pending run results only while the lease holds.

    Rolls back and returns False when the lease was recovered, failed or
    deleted, when the lock or the lease read is unavailable, or when
    ``still_owned`` (the worker's sticky local lease state) returns False
    under the lock.
    """
    from app import db

    try:
        with db.session.no_autoflush:
            acquire_candidate_lock(candidate_id)
            held = _lease_held(vetting_log_id)
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning(f"⚠️ Result commit for vetting log {vetting_log_id} withheld: {e}")
        return False
    if held and still_owned is not None and not still_owned():
        db.session.rollback()
        logger.warning(
            f"🛑 Vetting log {vetting_log_id} (candidate {candidate_id}) lease "
            f"renewal was lost or unknown during the run; discarding results"
        )
        return False
    if not held:
        db.session.rollback()
        logger.warning(
            f"🛑 Vetting log {vetting_log_id} (candidate {candidate_id}) lost its "
            f"lease; discarding this worker's results"
        )
        return False
    db.session.commit()
    return True


def renew_lease(candidate_id: int, vetting_log_id: int) -> str:
    """Heartbeat: push the lease's ``updated_at`` forward.

    Returns ``LEASE_HELD``, ``LEASE_LOST`` (the row is no longer
    ``processing``) or ``LEASE_UNKNOWN`` (lock wait timed out or the database
    failed; nothing was renewed). Commits the session's pending state in the
    same transaction as a successful conditional update, rolls back otherwise.
    """
    from app import db
    from models import CandidateVettingLog

    try:
        with db.session.no_autoflush:
            _set_lock_timeout(RENEW_LOCK_TIMEOUT_MS)
            if _is_postgres():
                from sqlalchemy import text
                db.session.execute(text(
                    f"SET LOCAL statement_timeout = '{int(RENEW_STATEMENT_TIMEOUT_MS)}ms'"))
            renewed = CandidateVettingLog.query.filter(
                CandidateVettingLog.id == vetting_log_id,
                CandidateVettingLog.status == 'processing',
            ).update({'updated_at': datetime.utcnow()}, synchronize_session=False)
        if renewed == 1:
            db.session.commit()
            return LEASE_HELD
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning(
            f"⚠️ Lease renewal for vetting log {vetting_log_id} (candidate "
            f"{candidate_id}) unknown: {type(e).__name__}: {e}"
        )
        return LEASE_UNKNOWN
    db.session.rollback()
    return LEASE_LOST


class RunLease:
    """A worker's view of its lease, with a heartbeat thread.

    ``beat`` renews from a fresh app context (its own session and connection),
    so it never touches the worker's transaction. The state only moves away
    from ``held``: once lost or unknown, ``allow_paid_call`` refuses for the
    rest of the run. Only ``lost`` ends the heartbeat early: after
    ``unknown`` it keeps renewing so the lease stays live while calls already
    in flight drain.
    """

    clock = staticmethod(time.monotonic)

    def __init__(self, app, candidate_id: int, vetting_log_id: int,
                 interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS):
        self.app = app
        self.candidate_id = candidate_id
        self.vetting_log_id = vetting_log_id
        self.interval_seconds = interval_seconds
        self.state = LEASE_HELD
        self.refused_calls = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._confirmed_at = self.clock()

    def record(self, status: str) -> str:
        with self._lock:
            if status == LEASE_HELD:
                self._confirmed_at = self.clock()
            if status == LEASE_LOST or (status == LEASE_UNKNOWN and self.state == LEASE_HELD):
                self.state = status
            return self.state

    def _check_stale(self) -> None:
        """Caller holds ``_lock``."""
        if self.state == LEASE_HELD and self.clock() - self._confirmed_at > STALE_HELD_SECONDS:
            self.state = LEASE_UNKNOWN
            logger.warning(
                f"⚠️ Vetting log {self.vetting_log_id}: no confirmed renewal for "
                f"over {STALE_HELD_SECONDS}s; lease state unknown"
            )

    @property
    def held(self) -> bool:
        with self._lock:
            self._check_stale()
            return self.state == LEASE_HELD

    def allow_paid_call(self) -> bool:
        with self._lock:
            self._check_stale()
            if self.state == LEASE_HELD:
                return True
            self.refused_calls += 1
            return False

    def beat(self) -> str:
        from app import db

        try:
            with self.app.app_context():
                try:
                    status = renew_lease(self.candidate_id, self.vetting_log_id)
                finally:
                    db.session.remove()
        except Exception as e:
            logger.warning(f"⚠️ Heartbeat for vetting log {self.vetting_log_id} failed: {e}")
            status = LEASE_UNKNOWN
        return self.record(status)

    def _wait(self) -> bool:
        """True when stopped."""
        return self._stop.wait(self.interval_seconds)

    def _run(self) -> None:
        warned = False
        while not self._wait():
            state = self.beat()
            if state != LEASE_HELD and not warned:
                warned = True
                logger.warning(
                    f"🛑 Heartbeat for vetting log {self.vetting_log_id} is "
                    f"{state}; no new paid calls for this run"
                )
            if state == LEASE_LOST:
                return

    def start(self) -> 'RunLease':
        self._thread = threading.Thread(
            target=self._run, name=f'lease-heartbeat-{self.vetting_log_id}', daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = HEARTBEAT_STOP_TIMEOUT_SECONDS) -> bool:
        """Stop the heartbeat, waiting at most ``timeout`` seconds for a
        renewal in progress. True when the thread has exited; a thread still
        stuck in the database is a daemon and its late result changes nothing
        the worker acts on."""
        self._stop.set()
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        if thread.is_alive():
            logger.warning(
                f"⚠️ Heartbeat for vetting log {self.vetting_log_id} did not stop "
                f"within {timeout}s; abandoning its thread"
            )
            return False
        return True


def _end_processing_run(vetting_log_id: int, expired_before: Optional[datetime],
                        reason: str) -> Optional[Dict]:
    from sqlalchemy import and_, func, or_
    from app import db
    from models import (
        CandidateJobMatch, CandidateVettingLog, EmbeddingFilterLog,
        EscalationLog, ParsedEmail, VettingAuditLog,
    )
    from .helpers import select_revet_parsed_email

    candidate_id = (
        db.session.query(CandidateVettingLog.bullhorn_candidate_id)
        .filter(CandidateVettingLog.id == vetting_log_id)
        .scalar()
    )
    if candidate_id is None:
        db.session.rollback()
        return None
    acquire_candidate_lock(candidate_id, timeout_ms=RECOVERY_LOCK_TIMEOUT_MS)

    predicate = [
        CandidateVettingLog.id == vetting_log_id,
        CandidateVettingLog.status == 'processing',
    ]
    if expired_before is not None:
        predicate.append(or_(
            CandidateVettingLog.updated_at < expired_before,
            and_(
                CandidateVettingLog.updated_at.is_(None),
                CandidateVettingLog.created_at < expired_before,
            ),
        ))
    row = (
        db.session.query(
            CandidateVettingLog.parsed_email_id, CandidateVettingLog.applied_job_id,
        )
        .filter(*predicate)
        .first()
    )
    if row is None:
        db.session.rollback()
        return None
    match_count = (
        db.session.query(func.count(CandidateJobMatch.id))
        .filter(CandidateJobMatch.vetting_log_id == vetting_log_id)
        .scalar()
    )

    released = VettingAuditLog.query.filter(
        VettingAuditLog.revet_vetting_log_id == vetting_log_id,
        VettingAuditLog.revet_new_score.is_(None),
    ).update({'revet_vetting_log_id': None}, synchronize_session=False)
    if match_count:
        # Unlinked from its email: an email linked to any failed log is
        # treated as already vetted, so the re-queue below would be dropped.
        ended = CandidateVettingLog.query.filter(*predicate).update({
            'status': 'failed',
            'parsed_email_id': None,
            'error_message': (
                f"{reason}; {match_count} partial job match(es) kept for review; "
                f"was ParsedEmail {row.parsed_email_id}"
            )[:500],
        }, synchronize_session=False)
    else:
        EmbeddingFilterLog.query.filter_by(vetting_log_id=vetting_log_id).delete(synchronize_session=False)
        EscalationLog.query.filter_by(vetting_log_id=vetting_log_id).delete(synchronize_session=False)
        ended = CandidateVettingLog.query.filter(*predicate).delete(synchronize_session=False)
    if ended != 1:
        db.session.rollback()
        return None

    requeued = None
    if row.parsed_email_id is not None:
        if ParsedEmail.query.filter(
            ParsedEmail.id == row.parsed_email_id,
            ParsedEmail.status == 'completed',
        ).update({'vetted_at': None}, synchronize_session=False):
            requeued = row.parsed_email_id
    elif not released:
        # Non-email intake: re-queue one email at most, never all of them.
        to_reset, _pending = select_revet_parsed_email(candidate_id, row.applied_job_id)
        if to_reset is not None:
            to_reset.vetted_at = None
            requeued = to_reset.id
    db.session.commit()
    return {
        'candidate_id': candidate_id,
        'audits_released': released,
        'parsed_email_id': requeued,
        'partial_matches_kept': match_count,
    }


def recover_expired_lease(vetting_log_id: int, expired_before: datetime) -> Optional[Dict]:
    """End a lease whose holder stopped heartbeating before ``expired_before``.

    Applies only while the row is still ``processing`` and its last heartbeat
    — or its creation time when it never heartbeated — is older than the
    cutoff; rows with neither timestamp are left for manual cleanup. A row
    without job matches is deleted so its ParsedEmail can be claimed again; a
    row with persisted partial matches is marked ``failed`` and its matches
    are kept. Either way audits bound to it are released and at most one
    ParsedEmail is re-queued. Its worker, if somehow still alive, is fenced
    out by ``commit_if_lease_held``. Commits; returns None when nothing was
    recovered. Raises ``CandidateLockUnavailable``.
    """
    return _end_processing_run(
        vetting_log_id, expired_before,
        f"lease expired (no heartbeat since before {expired_before.isoformat()})",
    )


def release_lease(vetting_log_id: int, reason: str) -> Optional[Dict]:
    """End this worker's own lease after it stopped on an unknown renewal,
    with the same row handling as recovery. None when the lease is already
    gone or the lock is unavailable (the lease then expires normally)."""
    try:
        return _end_processing_run(vetting_log_id, None, reason)
    except Exception as e:
        from app import db
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning(f"⚠️ Release of vetting log {vetting_log_id} failed; left to expire: {e}")
        return None
