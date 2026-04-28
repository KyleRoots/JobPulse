"""Auto-split from vetting_audit_service.py — see vetting_audit_service/__init__.py."""
import json
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

from .helpers import get_revet_cap_per_24h, get_revet_score_tolerance

logger = logging.getLogger(__name__)

class RevetMixin:
    """Re-vet flow — cap/stability checks and triggering re-vets for confirmed misfires."""

    def _check_revet_caps_and_stability(
        self,
        candidate_id: int,
        job_id: Optional[int],
    ) -> Optional[tuple]:
        """Decide whether a freshly-flagged re-vet should actually fire.

        Two suppression rules — both keyed on the (candidate, job) pair so
        a candidate flagged on multiple jobs is still re-vetted per-job:

        1. **24h cap** — if there are already ``get_revet_cap_per_24h()``
           prior ``revet_triggered`` audit rows for the same candidate &
           job in the last 24 hours, suppress this re-vet. This is the
           direct fix for the Beatriz Vieitos thrash where 8 re-vets
           landed in a single 7-hour window.
        2. **Score stability** — if the most recent prior re-vet for the
           same (candidate, job) produced a ``revet_new_score`` within
           ``get_revet_score_tolerance()`` of its ``original_score``,
           accept the result and stop re-vetting. The previous attempt
           proved that re-screening doesn't materially move the score.

        When ``job_id`` is None the helper degrades to the candidate-only
        cap (a candidate without an applied-job binding can still be
        re-vet-thrashed; we still want to limit blast radius).

        Returns
        -------
        None
            Re-vet may proceed.
        tuple[str, str]
            ``(action_taken, human_reason)`` describing why the re-vet was
            suppressed. The caller writes ``action_taken`` to the audit
            log and prepends ``human_reason`` to the audit finding.
        """
        from models import VettingAuditLog
        from datetime import datetime, timedelta

        cap = get_revet_cap_per_24h()
        tolerance = get_revet_score_tolerance()
        cutoff = datetime.utcnow() - timedelta(hours=24)

        try:
            base_q = VettingAuditLog.query.filter(
                VettingAuditLog.bullhorn_candidate_id == candidate_id,
                VettingAuditLog.action_taken == 'revet_triggered',
                VettingAuditLog.created_at >= cutoff,
            )
            if job_id is not None:
                base_q = base_q.filter(VettingAuditLog.job_id == job_id)

            prior = base_q.order_by(VettingAuditLog.created_at.desc()).all()
        except Exception as e:
            # Never let an audit-history lookup error mask a genuine re-vet
            # decision — fail open and let the re-vet proceed.
            logger.warning(
                f"⚠️ Auditor revet-cap lookup failed for candidate "
                f"{candidate_id} job {job_id}: {e!r} — proceeding with re-vet"
            )
            return None

        if len(prior) >= cap:
            reason = (
                f"Suppressed re-vet — already re-vetted "
                f"{len(prior)} time(s) in the last 24h "
                f"(cap={cap}). Latest re-vet at "
                f"{prior[0].created_at.isoformat() if prior[0].created_at else '?'}; "
                f"flag for human review instead of looping."
            )
            logger.info(
                f"🛑 Auditor revet-cap: candidate {candidate_id} job {job_id} "
                f"hit 24h cap (count={len(prior)}, cap={cap}) — skipping re-vet"
            )
            return ('revet_skipped_capped', reason)

        if prior:
            latest = prior[0]
            prior_original = latest.original_score
            prior_new = latest.revet_new_score
            if (
                prior_original is not None
                and prior_new is not None
                and abs(float(prior_new) - float(prior_original)) <= tolerance
            ):
                reason = (
                    f"Suppressed re-vet — last re-vet moved the score from "
                    f"{float(prior_original):.0f}% to {float(prior_new):.0f}% "
                    f"(within ±{tolerance:.0f}-point tolerance). "
                    f"Result is score-stable; further re-vets unlikely to help."
                )
                logger.info(
                    f"🛑 Auditor revet-stability: candidate {candidate_id} "
                    f"job {job_id} prior re-vet stable "
                    f"({float(prior_original):.0f}%→{float(prior_new):.0f}%, "
                    f"tol=±{tolerance:.0f}) — skipping re-vet"
                )
                return ('revet_skipped_stable', reason)

        return None

    def _trigger_revet(self, candidate_id: int, original_log_id: int) -> Optional[float]:
        from app import db, app
        from models import (
            CandidateVettingLog, CandidateJobMatch, ParsedEmail,
            EmbeddingFilterLog, EscalationLog, VettingAuditLog
        )

        try:
            parsed_emails = ParsedEmail.query.filter(
                ParsedEmail.bullhorn_candidate_id == candidate_id,
                ParsedEmail.status == 'completed'
            ).all()

            if not parsed_emails:
                logger.warning(f"No ParsedEmail records for candidate {candidate_id}")
                return None

            pe_ids = [pe.id for pe in parsed_emails]

            vetting_logs = CandidateVettingLog.query.filter(
                CandidateVettingLog.parsed_email_id.in_(pe_ids)
            ).all()

            log_ids = [vl.id for vl in vetting_logs]

            if log_ids:
                EmbeddingFilterLog.query.filter(
                    EmbeddingFilterLog.vetting_log_id.in_(log_ids)
                ).delete(synchronize_session=False)

                EscalationLog.query.filter(
                    EscalationLog.vetting_log_id.in_(log_ids)
                ).delete(synchronize_session=False)

                CandidateJobMatch.query.filter(
                    CandidateJobMatch.vetting_log_id.in_(log_ids)
                ).delete(synchronize_session=False)

                CandidateVettingLog.query.filter(
                    CandidateVettingLog.id.in_(log_ids)
                ).delete(synchronize_session=False)

            for pe in parsed_emails:
                pe.vetted_at = None

            db.session.commit()

            logger.info(
                f"🔄 Audit re-vet: reset candidate {candidate_id} — "
                f"cleared {len(log_ids)} vetting logs, reset {len(pe_ids)} ParsedEmails. "
                f"Will be picked up by next vetting cycle."
            )

            return None

        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Audit re-vet failed for candidate {candidate_id}: {str(e)}")
            return None
