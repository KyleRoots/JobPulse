"""Per-candidate run claims shared by audit re-vets and the vetting worker.

Protocol, per Bullhorn candidate ``c``:

* ``L(c)``: a transaction-scoped PostgreSQL advisory lock. Revet resets, the
  pending-revet detector's per-candidate decision, worker claim, lease
  renewal, lease recovery and fenced result commits each run as one short
  transaction that takes ``L(c)`` before re-reading its guards. No
  transaction stays open across Bullhorn or model calls.
* Lease: a ``CandidateVettingLog`` row with status ``processing``. Only
  ``claim_run`` creates one. Its id is the fencing token: a worker commits
  results only while that row is still ``processing`` (``commit_if_lease_held``),
  so a worker whose lease was recovered or failed cannot commit after
  replacement. Lease age is measured from the last heartbeat (``updated_at``),
  and only ``recover_expired_lease`` may end a lease nobody released.
* Audit binding: ``VettingAuditLog.revet_vetting_log_id``. ``claim_run`` binds
  every audit row still waiting on a score to the run it creates, inside the
  same transaction. A run therefore covers only audit requests that were
  committed before it started, and back-fill reads scores only from the bound
  run. An audit created while a run is in flight stays unbound and pending.

Invariants:

1. A reset never deletes a ``processing`` row, whatever its age; it defers.
2. Rows a reset deletes are the ones it read under ``L(c)``, with
   ``status <> 'processing'`` in the DELETE itself.
3. A queued item starts a run only if its queue entry is still open under
   ``L(c)``: a re-vet item's audit is still unbound (or bound to a dead run),
   and an email item's ParsedEmail has no vetting log yet.
4. A re-vet item does not start while another lease for the candidate is live.
5. Unknown guard state (lock or lookup failure) never leads to a delete,
   re-queue or claim.

Off PostgreSQL (the SQLite test database) the lock is a no-op: SQLite allows
one writer at a time, and nothing here claims concurrency safety from it.
"""

import logging
from datetime import datetime
from typing import Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# Distinct from the Scout session lock (0x5C0E7) so the two never contend.
CANDIDATE_RUN_LOCK_NAMESPACE = 0x52564554

LEASE_TIMEOUT_MINUTES = 10

OPEN_AUDIT_ACTIONS = ('revet_triggered', 'revet_skipped_stable')
LIVE_RUN_STATUSES = ('processing', 'completed')

CLAIM_OUTCOME_KEY = '_run_claim_outcome'


class CandidateLockUnavailable(RuntimeError):
    """The candidate lock could not be taken, so guard state is unknown."""


def acquire_candidate_lock(candidate_id: int) -> None:
    from sqlalchemy import text
    from app import db

    if db.session.get_bind().dialect.name != 'postgresql':
        return
    try:
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


def commit_if_lease_held(candidate_id: int, vetting_log_id: int) -> bool:
    """Commit the session's pending run results only while the lease holds.

    Rolls back and returns False when the lease was recovered, failed or
    deleted, or when the lock is unavailable.
    """
    from app import db

    try:
        with db.session.no_autoflush:
            acquire_candidate_lock(candidate_id)
            held = _lease_held(vetting_log_id)
    except CandidateLockUnavailable as e:
        logger.warning(f"⚠️ Result commit for vetting log {vetting_log_id} withheld: {e}")
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


def renew_lease(candidate_id: int, vetting_log_id: int) -> bool:
    """Heartbeat: push the lease's ``updated_at`` forward. False if it is lost."""
    from app import db
    from models import CandidateVettingLog

    try:
        with db.session.no_autoflush:
            acquire_candidate_lock(candidate_id)
            renewed = CandidateVettingLog.query.filter(
                CandidateVettingLog.id == vetting_log_id,
                CandidateVettingLog.status == 'processing',
            ).update({'updated_at': datetime.utcnow()}, synchronize_session=False)
    except CandidateLockUnavailable as e:
        logger.warning(f"⚠️ Lease renewal for vetting log {vetting_log_id} skipped: {e}")
        return True
    if renewed != 1:
        db.session.rollback()
        return False
    db.session.commit()
    return True


def recover_expired_lease(vetting_log_id: int, expired_before: datetime) -> Optional[Dict]:
    """End a lease whose holder stopped heartbeating before ``expired_before``.

    The row is deleted (so its ParsedEmail can be claimed again) only if it is
    still ``processing``, has no job matches, and its last heartbeat — or its
    creation time when it never heartbeated — is older than the cutoff. Rows
    with neither timestamp are left for manual cleanup. Audits bound to it are
    released, and at most one ParsedEmail is re-queued. Its worker, if still
    alive, is fenced out by ``commit_if_lease_held``. Commits; returns None
    when nothing was recovered.
    """
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
    acquire_candidate_lock(candidate_id)

    expired = (
        CandidateVettingLog.id == vetting_log_id,
        CandidateVettingLog.status == 'processing',
        or_(
            CandidateVettingLog.updated_at < expired_before,
            and_(
                CandidateVettingLog.updated_at.is_(None),
                CandidateVettingLog.created_at < expired_before,
            ),
        ),
    )
    row = (
        db.session.query(
            CandidateVettingLog.parsed_email_id, CandidateVettingLog.applied_job_id,
        )
        .filter(*expired)
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
    if match_count:
        db.session.rollback()
        return None

    released = VettingAuditLog.query.filter(
        VettingAuditLog.revet_vetting_log_id == vetting_log_id,
        VettingAuditLog.revet_new_score.is_(None),
    ).update({'revet_vetting_log_id': None}, synchronize_session=False)
    EmbeddingFilterLog.query.filter_by(vetting_log_id=vetting_log_id).delete(synchronize_session=False)
    EscalationLog.query.filter_by(vetting_log_id=vetting_log_id).delete(synchronize_session=False)
    if CandidateVettingLog.query.filter(*expired).delete(synchronize_session=False) != 1:
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
    }
