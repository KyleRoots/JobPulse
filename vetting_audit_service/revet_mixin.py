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
    """Re-vet flow — cap/stability/cutoff checks and triggering re-vets for confirmed misfires."""

    def _check_pre_cutoff_eligibility(
        self,
        candidate_id: int,
    ) -> Optional[tuple]:
        """Refuse re-vets when the candidate's underlying ParsedEmail predates
        the configured ``vetting_cutoff_date``.

        ``_trigger_revet`` resets ``parsed_email.vetted_at = None`` and deletes
        the existing ``CandidateVettingLog``, expecting the next vetting cycle
        to re-score the candidate. But ``screening/detection.py`` filters the
        backlog by ``vetting_cutoff_date`` — so if the candidate's parsed_email
        was received BEFORE the cutoff, the next cycle silently skips them
        forever and the audit row stays as ``revet_triggered`` /
        ``revet_new_score=NULL`` indefinitely.

        Pre-flight: look up the candidate's most-recent completed
        ParsedEmail. If its ``received_at`` is before the active cutoff,
        suppress the re-vet with ``action_taken='revet_skipped_pre_cutoff'``
        instead of nuking state we cannot rebuild.

        Fail-open: if the cutoff is unset, the lookup throws, or the
        ParsedEmail row cannot be found, return None and let the re-vet
        proceed (current behaviour preserved).

        Returns
        -------
        None
            Re-vet may proceed.
        tuple[str, str]
            ``('revet_skipped_pre_cutoff', human_reason)``.
        """
        try:
            from screening.candidate_data import _resolve_vetting_cutoff
            cutoff = _resolve_vetting_cutoff()
        except Exception as e:
            logger.warning(
                f"⚠️ Auditor pre-cutoff lookup failed for candidate "
                f"{candidate_id} (cutoff resolver raised {e!r}) — proceeding "
                f"with re-vet"
            )
            return None

        if cutoff is None:
            return None

        try:
            from models import ParsedEmail
            latest_pe = (
                ParsedEmail.query
                .filter(
                    ParsedEmail.bullhorn_candidate_id == candidate_id,
                    ParsedEmail.status == 'completed',
                )
                .order_by(ParsedEmail.received_at.desc().nullslast())
                .first()
            )
        except Exception as e:
            logger.warning(
                f"⚠️ Auditor pre-cutoff lookup failed for candidate "
                f"{candidate_id} (parsed_email query raised {e!r}) — "
                f"proceeding with re-vet"
            )
            return None

        if latest_pe is None or latest_pe.received_at is None:
            return None

        if latest_pe.received_at >= cutoff:
            return None

        reason = (
            f"Suppressed re-vet — candidate's most recent parsed_email "
            f"received_at={latest_pe.received_at.isoformat()} predates "
            f"vetting_cutoff_date={cutoff.isoformat()}. Resetting vetted_at "
            f"would orphan the candidate (next vetting cycle would skip "
            f"them via the cutoff filter). Audit observed but no action "
            f"taken; bump the cutoff back if you want this candidate "
            f"re-scored."
        )
        logger.info(
            f"🛑 Auditor pre-cutoff: candidate {candidate_id} "
            f"parsed_email received_at={latest_pe.received_at.isoformat()} "
            f"< cutoff={cutoff.isoformat()} — skipping re-vet"
        )
        return ('revet_skipped_pre_cutoff', reason)

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
        """Reset the candidate's vetting state so the next cycle re-scores them.

        Async by design: the actual re-score is performed by the next
        ``CandidateVettingService.run_vetting_cycle`` invocation, so this
        helper returns ``None`` and lets ``backfill_revet_new_score`` populate
        ``revet_new_score`` once the new ``CandidateJobMatch`` rows land.

        Delegates to ``clear_candidate_vetting_state`` (shared with
        ``detect_pending_revet_candidates``) so the cascade is identical
        whether the auditor triggered the revet directly or the safety-net
        detector re-enqueues a stuck audit row later.
        """
        from app import db
        from .helpers import clear_candidate_vetting_state

        try:
            stats = clear_candidate_vetting_state(candidate_id)
            logger.info(
                f"🔄 Audit re-vet: reset candidate {candidate_id} — "
                f"cleared {stats['vetting_logs_deleted']} vetting logs, "
                f"reset {stats['parsed_emails_reset']} ParsedEmails. "
                f"Will be picked up by next vetting cycle."
            )
            return None

        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Audit re-vet failed for candidate {candidate_id}: {str(e)}")
            return None
