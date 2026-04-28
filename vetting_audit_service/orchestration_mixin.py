"""Auto-split from vetting_audit_service.py — see vetting_audit_service/__init__.py."""
import json
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

from .helpers import get_qualified_sample_rate

logger = logging.getLogger(__name__)

class OrchestrationMixin:
    """Audit cycle orchestration — entry points, candidate sampling, persistence, per-candidate dispatch."""

    def run_audit_cycle(self, batch_size=20):
        """Run an audit cycle covering both false-negative and false-positive cases.

        Phase 1 (existing): reviews recent Not-Qualified results and re-vets
        confirmed misfires (recency, location, gap, authorization, etc.).

        Phase 2 (new): samples a configurable percentage of Qualified results
        and reviews them for false positives — candidates who scored above the
        threshold but the resume actually fails mandatory requirements. Uses
        the same Tier-1 heuristic + Tier-2 AI confirmation pattern. Sample
        rate is read from VettingConfig.qualified_audit_sample_rate (default
        10%, 0 disables Phase 2 entirely).
        """
        from app import db
        from models import (
            CandidateVettingLog, CandidateJobMatch, VettingAuditLog,
            VettingConfig, ParsedEmail, EmbeddingFilterLog, EscalationLog,
            EmailDeliveryLog
        )

        summary = {
            'total_audited': 0,
            'issues_found': 0,
            'revets_triggered': 0,
            'revets_skipped_capped': 0,
            'revets_skipped_stable': 0,
            'qualified_audited': 0,
            'qualified_issues_found': 0,
            'details': []
        }

        try:
            already_audited = db.session.query(VettingAuditLog.candidate_vetting_log_id).subquery()

            not_qualified = CandidateVettingLog.query.filter(
                CandidateVettingLog.status == 'completed',
                CandidateVettingLog.is_qualified == False,
                CandidateVettingLog.is_sandbox == False,
                ~CandidateVettingLog.id.in_(
                    db.session.query(already_audited)
                )
            ).order_by(
                CandidateVettingLog.analyzed_at.desc()
            ).limit(batch_size).all()

            if not_qualified:
                logger.info(
                    f"🔍 Screening audit (Not Qualified phase): reviewing "
                    f"{len(not_qualified)} unaudited results"
                )
                for vetting_log in not_qualified:
                    self._process_candidate_audit(
                        vetting_log, mode='not_qualified', summary=summary
                    )
            else:
                logger.info("🔍 Screening audit: no new unaudited Not-Qualified results")

            sample_rate = get_qualified_sample_rate()
            if sample_rate > 0:
                qualified_sample = self._fetch_qualified_audit_sample(
                    batch_size, sample_rate
                )
                if qualified_sample:
                    logger.info(
                        f"🔍 Screening audit (Qualified false-positive phase): "
                        f"sampling {len(qualified_sample)} of recent Qualified results "
                        f"(sample rate: {sample_rate}%)"
                    )
                    for vetting_log in qualified_sample:
                        self._process_candidate_audit(
                            vetting_log, mode='qualified_false_positive', summary=summary
                        )
                else:
                    logger.info(
                        "🔍 Screening audit: no new Qualified results to sample"
                    )
            else:
                logger.info(
                    "🔍 Screening audit: Qualified-sample phase disabled "
                    "(qualified_audit_sample_rate=0)"
                )

            if summary['issues_found'] > 0 or summary['revets_triggered'] > 0:
                try:
                    self._send_audit_summary_email(summary)
                    summary['email_sent'] = True
                except Exception as e:
                    logger.error(f"❌ Screening audit email error: {str(e)}")
                    summary['email_sent'] = False

            logger.info(
                f"✅ Screening audit cycle complete: "
                f"{summary['total_audited']} total audited "
                f"({summary['qualified_audited']} were Qualified samples), "
                f"{summary['issues_found']} issues found, "
                f"{summary['revets_triggered']} re-vets triggered, "
                f"{summary['revets_skipped_capped']} re-vet(s) skipped (24h cap), "
                f"{summary['revets_skipped_stable']} re-vet(s) skipped (score stable)"
            )

        except Exception as e:
            logger.error(f"❌ Screening audit cycle failed: {str(e)}")

        return summary

    def _fetch_qualified_audit_sample(self, batch_size: int, sample_rate: int):
        """Fetch a sampled batch of recent Qualified results that haven't been audited.

        Pulls a candidate pool ~10x batch_size deep, filters to unaudited rows,
        then randomly samples (sample_rate%). Returns at most batch_size rows.
        """
        from app import db
        from models import CandidateVettingLog, VettingAuditLog

        already_audited = db.session.query(VettingAuditLog.candidate_vetting_log_id).subquery()

        pool_size = max(batch_size * 10, 50)
        pool = CandidateVettingLog.query.filter(
            CandidateVettingLog.status == 'completed',
            CandidateVettingLog.is_qualified == True,
            CandidateVettingLog.is_sandbox == False,
            ~CandidateVettingLog.id.in_(
                db.session.query(already_audited)
            )
        ).order_by(
            CandidateVettingLog.analyzed_at.desc()
        ).limit(pool_size).all()

        if not pool:
            return []

        sampled = [
            vl for vl in pool
            if random.randint(1, 100) <= sample_rate
        ]
        return sampled[:batch_size]

    def _commit_audit_log(self, audit_log) -> bool:
        """Persist a VettingAuditLog row with race-safe duplicate handling.

        The unique constraint on `candidate_vetting_log_id` (added in migration
        i3d4e5f6g7h8) guarantees that two overlapping audit cycles cannot both
        write a row for the same candidate. If a concurrent cycle has already
        inserted one, this method rolls back and returns False so the caller
        can treat it as "already audited, skip" instead of crashing the cycle.

        Only the duplicate-key violation on our specific unique constraint is
        swallowed — any other IntegrityError (e.g. NOT NULL, foreign key)
        is re-raised so genuine bugs aren't masked as "already audited."

        Returns True on successful commit, False on duplicate (already audited).
        """
        from app import db
        from sqlalchemy.exc import IntegrityError

        db.session.add(audit_log)
        try:
            db.session.commit()
            return True
        except IntegrityError as exc:
            db.session.rollback()
            if not self._is_duplicate_audit_log_error(exc):
                raise
            logger.info(
                f"🔁 Screening audit: candidate_vetting_log_id "
                f"{audit_log.candidate_vetting_log_id} already audited by a "
                f"concurrent cycle — skipping duplicate insert."
            )
            return False

    @staticmethod
    def _is_duplicate_audit_log_error(exc) -> bool:
        """Return True if `exc` is a duplicate-key violation on the
        VettingAuditLog unique constraint (Postgres or SQLite)."""
        candidate_strings = []
        orig = getattr(exc, 'orig', None)
        if orig is not None:
            diag = getattr(orig, 'diag', None)
            if diag is not None:
                cname = getattr(diag, 'constraint_name', None)
                if cname:
                    candidate_strings.append(cname)
            candidate_strings.append(str(orig))
        candidate_strings.append(str(exc))

        haystack = ' '.join(s for s in candidate_strings if s).lower()
        if 'uq_audit_log_vetting_id' in haystack:
            return True
        if (
            'unique constraint failed' in haystack
            and 'vetting_audit_log.candidate_vetting_log_id' in haystack
        ):
            return True
        return False

    def _process_candidate_audit(self, vetting_log, mode: str, summary: Dict):
        """Audit a single candidate. Handles applied_match lookup, heuristic
        checks, AI confirmation, action decision, and audit log persistence.

        mode='not_qualified' uses the existing _run_heuristic_checks (false
        negatives). mode='qualified_false_positive' uses the new
        _run_false_positive_checks (false positives).
        """
        from app import db
        from models import CandidateJobMatch, VettingAuditLog

        is_qualified_audit = (mode == 'qualified_false_positive')
        try:
            applied_match = CandidateJobMatch.query.filter_by(
                vetting_log_id=vetting_log.id,
                is_applied_job=True
            ).first()

            if not applied_match:
                applied_match = CandidateJobMatch.query.filter_by(
                    vetting_log_id=vetting_log.id
                ).order_by(CandidateJobMatch.match_score.desc()).first()

            if not applied_match:
                audit_log = VettingAuditLog(
                    candidate_vetting_log_id=vetting_log.id,
                    bullhorn_candidate_id=vetting_log.bullhorn_candidate_id,
                    candidate_name=vetting_log.candidate_name,
                    job_id=vetting_log.applied_job_id,
                    job_title=vetting_log.applied_job_title,
                    original_score=vetting_log.highest_match_score,
                    finding_type='no_issue',
                    action_taken='no_action',
                    audit_finding='No job match records found to audit'
                )
                if self._commit_audit_log(audit_log):
                    summary['total_audited'] += 1
                    if is_qualified_audit:
                        summary['qualified_audited'] += 1
                return

            if is_qualified_audit:
                suspected_issues = self._run_false_positive_checks(vetting_log, applied_match)
            else:
                suspected_issues = self._run_heuristic_checks(vetting_log, applied_match)

            if not suspected_issues:
                clean_finding = (
                    'Qualified false-positive checks passed — no issues detected'
                    if is_qualified_audit
                    else 'Heuristic checks passed — no issues detected'
                )
                audit_log = VettingAuditLog(
                    candidate_vetting_log_id=vetting_log.id,
                    bullhorn_candidate_id=vetting_log.bullhorn_candidate_id,
                    candidate_name=vetting_log.candidate_name,
                    job_id=applied_match.bullhorn_job_id,
                    job_title=applied_match.job_title,
                    original_score=applied_match.match_score,
                    finding_type='no_issue',
                    confidence='high',
                    action_taken='no_action',
                    audit_finding=clean_finding
                )
                if self._commit_audit_log(audit_log):
                    summary['total_audited'] += 1
                    if is_qualified_audit:
                        summary['qualified_audited'] += 1
                return

            logger.info(
                f"⚠️ Screening audit ({mode}): {len(suspected_issues)} suspected issue(s) "
                f"for candidate {vetting_log.bullhorn_candidate_id} "
                f"({vetting_log.candidate_name}) on job {applied_match.bullhorn_job_id}"
            )

            ai_finding = self._run_ai_audit(
                applied_match,
                vetting_log.resume_text or '',
                applied_match.job_title or vetting_log.applied_job_title or '',
                suspected_issues,
                mode=mode
            )

            finding_type = ai_finding.get('finding_type', 'no_issue')
            confidence = ai_finding.get('confidence', 'low')
            action_taken = 'no_action'
            revet_new_score = None
            audit_finding_text = ai_finding.get('reasoning', '')

            if confidence == 'high' and finding_type != 'no_issue':
                action_taken = 'revet_triggered'
            elif confidence == 'medium' and finding_type != 'no_issue':
                action_taken = 'flagged_for_review'

            # Re-vet suppression: cap repeated re-vets per (candidate, job)
            # and accept results that are already score-stable. Runs before
            # the audit row is written so the persisted action_taken
            # reflects what actually happened, not what we wanted to do.
            if action_taken == 'revet_triggered':
                skip_outcome = self._check_revet_caps_and_stability(
                    vetting_log.bullhorn_candidate_id,
                    applied_match.bullhorn_job_id,
                )
                if skip_outcome is not None:
                    skip_action, skip_reason = skip_outcome
                    action_taken = skip_action
                    audit_finding_text = (
                        f"{audit_finding_text}\n\n[Auditor] {skip_reason}"
                    ).strip()
                    if skip_action == 'revet_skipped_capped':
                        summary['revets_skipped_capped'] += 1
                    elif skip_action == 'revet_skipped_stable':
                        summary['revets_skipped_stable'] += 1

            audit_log = VettingAuditLog(
                candidate_vetting_log_id=vetting_log.id,
                bullhorn_candidate_id=vetting_log.bullhorn_candidate_id,
                candidate_name=vetting_log.candidate_name,
                job_id=applied_match.bullhorn_job_id,
                job_title=applied_match.job_title,
                original_score=applied_match.match_score,
                audit_finding=audit_finding_text,
                finding_type=finding_type,
                confidence=confidence,
                action_taken=action_taken,
                revet_new_score=revet_new_score
            )
            if not self._commit_audit_log(audit_log):
                return

            if action_taken == 'revet_triggered':
                revet_new_score = self._trigger_revet(
                    vetting_log.bullhorn_candidate_id,
                    vetting_log.id
                )
                try:
                    audit_log.revet_new_score = revet_new_score
                    db.session.commit()
                except Exception as e:
                    logger.warning(
                        f"⚠️ Screening audit: failed to update revet_new_score "
                        f"on audit log {audit_log.id}: {e!r}"
                    )
                    db.session.rollback()
                summary['revets_triggered'] += 1
                logger.info(
                    f"✅ Screening audit ({mode}): re-vet triggered for candidate "
                    f"{vetting_log.bullhorn_candidate_id} ({vetting_log.candidate_name}). "
                    f"Original score: {applied_match.match_score}%, "
                    f"New score: {revet_new_score}%"
                )

            summary['total_audited'] += 1
            if is_qualified_audit:
                summary['qualified_audited'] += 1
            if finding_type != 'no_issue':
                summary['issues_found'] += 1
                if is_qualified_audit:
                    summary['qualified_issues_found'] += 1
                summary['details'].append({
                    'candidate_id': vetting_log.bullhorn_candidate_id,
                    'candidate_name': vetting_log.candidate_name,
                    'job_id': applied_match.bullhorn_job_id,
                    'job_title': applied_match.job_title,
                    'original_score': applied_match.match_score,
                    'finding_type': finding_type,
                    'confidence': confidence,
                    'action_taken': action_taken,
                    'new_score': revet_new_score,
                    'audit_log_id': audit_log.id,
                    'mode': mode
                })

        except Exception as e:
            logger.error(
                f"❌ Screening audit error ({mode}) for candidate "
                f"{vetting_log.bullhorn_candidate_id}: {str(e)}"
            )
            try:
                audit_log = VettingAuditLog(
                    candidate_vetting_log_id=vetting_log.id,
                    bullhorn_candidate_id=vetting_log.bullhorn_candidate_id,
                    candidate_name=vetting_log.candidate_name,
                    finding_type='no_issue',
                    action_taken='no_action',
                    audit_finding=f'Audit error ({mode}): {str(e)}'
                )
                if self._commit_audit_log(audit_log):
                    summary['total_audited'] += 1
                    if is_qualified_audit:
                        summary['qualified_audited'] += 1
            except Exception:
                db.session.rollback()
