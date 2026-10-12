"""Remember feed reference numbers after they are replaced.

The published XML keeps one current number per job. Boards can keep a
posting under an older number. These helpers save that older number and
resolve either the current or a retired number to the Bullhorn job id.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Set

logger = logging.getLogger(__name__)


def _clean(value: Optional[str]) -> str:
    return str(value or "").strip()


def remember_retired_reference(reference_number: Optional[str], bullhorn_job_id: Optional[str]) -> bool:
    """Save a number that is about to be replaced. Returns True when a row is added."""
    from extensions import db
    from models.scheduling import JobReferenceAlias, JobReferenceNumber

    ref = _clean(reference_number)
    job_id = _clean(bullhorn_job_id)
    if not ref or not job_id:
        return False

    db.session.flush()
    current = JobReferenceNumber.query.filter_by(reference_number=ref).first()
    if current is not None and _clean(current.bullhorn_job_id) == job_id:
        return False

    existing = JobReferenceAlias.query.filter_by(reference_number=ref).first()
    if existing is not None:
        if _clean(existing.bullhorn_job_id) != job_id:
            logger.warning(
                "Retired reference %s already belongs to job %s; leaving it",
                ref,
                existing.bullhorn_job_id,
            )
        return False

    db.session.add(JobReferenceAlias(
        reference_number=ref,
        bullhorn_job_id=job_id,
        retired_at=datetime.utcnow(),
    ))
    return True


def bullhorn_job_id_for_reference(reference: Optional[str]) -> Optional[str]:
    """Current feed number first, then a retired number for the same job."""
    from models.scheduling import JobReferenceAlias, JobReferenceNumber

    ref = _clean(reference)
    if not ref:
        return None
    current = JobReferenceNumber.query.filter_by(reference_number=ref).first()
    if current is not None:
        job_id = _clean(current.bullhorn_job_id)
        if job_id.isdigit():
            return job_id
    retired = JobReferenceAlias.query.filter_by(reference_number=ref).first()
    if retired is None:
        return None
    job_id = _clean(retired.bullhorn_job_id)
    return job_id if job_id.isdigit() else None


def reserved_reference_numbers() -> Set[str]:
    """Numbers that must not be issued again: current and retired."""
    from models.scheduling import JobReferenceAlias, JobReferenceNumber

    try:
        current = {
            _clean(row.reference_number)
            for row in JobReferenceNumber.query.with_entities(JobReferenceNumber.reference_number)
        }
        retired = {
            _clean(row.reference_number)
            for row in JobReferenceAlias.query.with_entities(JobReferenceAlias.reference_number)
        }
    except Exception:
        logger.warning("Could not load reserved reference numbers", exc_info=True)
        return set()
    current.discard("")
    retired.discard("")
    return current | retired


def allocate_reference(used: Set[str], generator) -> str:
    """Pick a new reference that is not in ``used`` and not already reserved."""
    reserved = set(used) | reserved_reference_numbers()
    new_ref = generator()
    while new_ref in reserved:
        new_ref = generator()
    return new_ref
