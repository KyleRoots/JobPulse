import logging
logger = logging.getLogger(__name__)
import json
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

DEFAULT_PLATFORM_AGE_CEILINGS = {
    'databricks': 8.0,
    'delta lake': 7.0,
    'azure synapse': 6.0,
    'azure synapse analytics': 6.0,
    'microsoft fabric': 3.0,
    'snowflake': 10.0,
    'dbt': 8.0,
    'data build tool': 8.0,
    'apache flink': 10.0,
    'kubernetes': 10.0,
    'apache kafka': 14.0,
    'terraform': 10.0,
    'docker': 12.0,
}

DEFAULT_AUDITOR_MODEL = 'gpt-5.4'
DEFAULT_QUALIFIED_SAMPLE_RATE = 10
DEFAULT_REVET_CAP_PER_24H = 2
DEFAULT_REVET_SCORE_TOLERANCE = 5.0


def get_platform_age_ceilings() -> Dict[str, float]:
    """Load PLATFORM_AGE_CEILINGS from VettingConfig with safe fallback.

    Looks up the `platform_age_ceilings` key (JSON-encoded dict of
    platform_name -> max_years_float). Returns the in-file defaults if
    the row is missing, the value is empty, or the JSON is malformed.
    """
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('platform_age_ceilings', None)
        if not raw or not isinstance(raw, str) or not raw.strip():
            return DEFAULT_PLATFORM_AGE_CEILINGS
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not parsed:
            return DEFAULT_PLATFORM_AGE_CEILINGS
        cleaned: Dict[str, float] = {}
        for k, v in parsed.items():
            try:
                f = float(v)
            except (ValueError, TypeError):
                continue
            if f != f or f <= 0 or f > 100:
                logger.warning(
                    f"⚠️ platform_age_ceilings: dropping out-of-range value "
                    f"{k!r}={v!r} (must be > 0 and <= 100 years)"
                )
                continue
            cleaned[str(k).lower()] = f
        return cleaned if cleaned else DEFAULT_PLATFORM_AGE_CEILINGS
    except Exception as e:
        logger.warning(
            f"⚠️ platform_age_ceilings load failed ({e!r}) — using in-file defaults"
        )
        return DEFAULT_PLATFORM_AGE_CEILINGS


def get_auditor_model() -> str:
    """Load the auditor model name from VettingConfig with safe fallback."""
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('quality_auditor_model', None)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception as e:
        logger.warning(
            f"⚠️ quality_auditor_model load failed ({e!r}) — using default"
        )
    return DEFAULT_AUDITOR_MODEL


def backfill_revet_new_score(
    candidate_id: int,
    vetting_log=None,
) -> int:
    """Back-fill ``VettingAuditLog.revet_new_score`` after a re-vet completes.

    ``_trigger_revet`` is asynchronous: it deletes the candidate's vetting
    logs and lets the next vetting cycle re-score them, so it cannot
    return the new score synchronously and the audit row is written with
    ``revet_new_score=NULL``. This helper is invoked from the vetting
    service once the new ``CandidateJobMatch`` rows have been committed;
    it locates every audit row for this candidate that was waiting on a
    re-vet score and copies the newly-scored ``match_score`` for the
    same applied job.

    Parameters
    ----------
    candidate_id:
        The Bullhorn candidate id whose audit rows should be back-filled.
    vetting_log:
        Optional ``CandidateVettingLog`` instance whose
        ``CandidateJobMatch`` rows should be used as the source of new
        scores. When omitted, the helper resolves the most recent
        completed vetting log for the candidate and reads its matches.

    Returns
    -------
    int
        Number of audit log rows that were updated.
    """
    from app import db
    from models import VettingAuditLog, CandidateJobMatch, CandidateVettingLog

    try:
        pending = VettingAuditLog.query.filter(
            VettingAuditLog.bullhorn_candidate_id == candidate_id,
            VettingAuditLog.action_taken == 'revet_triggered',
            VettingAuditLog.revet_new_score.is_(None),
        ).all()

        if not pending:
            return 0

        if vetting_log is not None and getattr(vetting_log, 'id', None):
            matches = CandidateJobMatch.query.filter_by(
                vetting_log_id=vetting_log.id
            ).all()
        else:
            latest = (
                CandidateVettingLog.query
                .filter_by(
                    bullhorn_candidate_id=candidate_id,
                    status='completed',
                )
                .order_by(CandidateVettingLog.id.desc())
                .first()
            )
            if not latest:
                return 0
            matches = CandidateJobMatch.query.filter_by(
                vetting_log_id=latest.id
            ).all()

        score_by_job: Dict[int, float] = {}
        for m in matches:
            if m.bullhorn_job_id is None or m.match_score is None:
                continue
            score_by_job[int(m.bullhorn_job_id)] = float(m.match_score)

        if not score_by_job:
            return 0

        backfilled = 0
        for audit_row in pending:
            if audit_row.job_id is None:
                continue
            new_score = score_by_job.get(int(audit_row.job_id))
            if new_score is None:
                continue
            audit_row.revet_new_score = new_score
            backfilled += 1

        if backfilled == 0:
            return 0

        db.session.commit()
        logger.info(
            f"🔁 Back-filled revet_new_score on {backfilled} audit row(s) "
            f"for candidate {candidate_id}"
        )
        return backfilled

    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning(
            f"⚠️ Failed to back-fill revet_new_score for candidate "
            f"{candidate_id}: {e!r}"
        )
        return 0


def get_qualified_sample_rate() -> int:
    """Load the Qualified false-positive sample rate (percent, 0-100).

    Returns the configured percentage of Qualified results to audit per
    cycle. 0 disables the Qualified audit branch entirely. Defaults to
    DEFAULT_QUALIFIED_SAMPLE_RATE (10%) if missing or malformed.
    """
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('qualified_audit_sample_rate', None)
        if raw is not None:
            value = int(str(raw).strip())
            if 0 <= value <= 100:
                return value
    except (ValueError, TypeError, Exception) as e:
        logger.warning(
            f"⚠️ qualified_audit_sample_rate load failed ({e!r}) — using default"
        )
    return DEFAULT_QUALIFIED_SAMPLE_RATE


def get_revet_cap_per_24h() -> int:
    """Maximum number of re-vets the auditor may trigger for the same
    (candidate, job) pair within any rolling 24-hour window.

    Reads ``auditor_revet_cap_per_24h`` from VettingConfig. Falls back to
    ``DEFAULT_REVET_CAP_PER_24H`` when the row is missing or malformed.
    Values < 1 are clamped to 1 (a value of 0 would disable the auditor's
    re-vet action entirely, which is what existing config flags are for).
    """
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('auditor_revet_cap_per_24h', None)
        if raw is not None:
            value = int(str(raw).strip())
            if value >= 1:
                return value
            return 1
    except (ValueError, TypeError, Exception) as e:
        logger.warning(
            f"⚠️ auditor_revet_cap_per_24h load failed ({e!r}) — using default"
        )
    return DEFAULT_REVET_CAP_PER_24H


def get_revet_score_tolerance() -> float:
    """Score-stability tolerance (in match-score points) used by the auditor
    to decide that a re-vet's new result is "close enough" to the original
    score that further re-vets are unlikely to change the verdict.

    Reads ``auditor_revet_score_tolerance`` from VettingConfig. Falls back
    to ``DEFAULT_REVET_SCORE_TOLERANCE`` (5.0) when missing or malformed.
    Negative values are clamped to 0.0.
    """
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('auditor_revet_score_tolerance', None)
        if raw is not None:
            value = float(str(raw).strip())
            if value >= 0:
                return value
            return 0.0
    except (ValueError, TypeError, Exception) as e:
        logger.warning(
            f"⚠️ auditor_revet_score_tolerance load failed ({e!r}) — using default"
        )
    return DEFAULT_REVET_SCORE_TOLERANCE


PLATFORM_AGE_CEILINGS = DEFAULT_PLATFORM_AGE_CEILINGS

DOMAIN_KEYWORDS = [
    'data engineer', 'azure data', 'databricks', 'spark', 'etl', 'pipeline',
    'data lake', 'synapse', 'snowflake', 'cloud engineer', 'big data',
    'data warehouse', 'kafka', 'airflow', 'dbt', 'analytics engineer',
    'machine learning', 'ml engineer', 'ai engineer', 'software engineer',
    'devops', 'platform engineer', 'backend engineer', 'data architect',
    'full stack', 'frontend engineer', 'cloud architect', 'sre',
    'infrastructure engineer', 'data scientist', 'business intelligence',
]


class VettingAuditService:
    """AI-powered quality auditor for Scout Screening results.
    
    Tier 1: Runs heuristic checks on recent Not Qualified results,
    confirms findings with AI, and auto-triggers re-vets for
    high-confidence misfires.
    """

    def __init__(self):
        self.openai_api_key = os.environ.get('OPENAI_API_KEY')

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

    def _run_heuristic_checks(self, vetting_log, job_match) -> List[Dict]:
        issues = []

        gaps = (job_match.gaps_identified or '').lower()
        match_summary = (job_match.match_summary or '').lower()
        job_title = (job_match.job_title or '').lower()
        platform_age_ceilings = get_platform_age_ceilings()

        recency_phrases = [
            'career trajectory has shifted away',
            'not practiced relevant skills in their last two positions',
            'most recent professional activity is outside the target domain',
        ]
        for phrase in recency_phrases:
            if phrase in gaps:
                candidate_title = ''
                if vetting_log.resume_text:
                    lines = vetting_log.resume_text[:500].split('\n')
                    for line in lines[:10]:
                        line_lower = line.strip().lower()
                        if any(kw in line_lower for kw in DOMAIN_KEYWORDS):
                            candidate_title = line.strip()
                            break

                if candidate_title:
                    issues.append({
                        'check_type': 'recency_misfire',
                        'description': (
                            f"Gaps say '{phrase}' but candidate's resume header "
                            f"indicates current role: '{candidate_title}'. "
                            f"Possible recency gate misfire."
                        )
                    })
                    break

        experience_match = (job_match.experience_match or '')
        _recency_tag_idx = experience_match.find('[Recency:')
        if _recency_tag_idx >= 0:
            _recency_tag = experience_match[_recency_tag_idx:]
            if 'relevant=yes' in _recency_tag:
                _justification_idx = _recency_tag.find('justification:')
                if _justification_idx >= 0:
                    _justification_text = _recency_tag[_justification_idx + len('justification:'):].rstrip(']').strip()
                    _WEAK_PHRASES = [
                        'transferable skills', 'transferable',
                        'general experience', 'general work experience',
                        'work ethic', 'reliable', 'reliability',
                        'communication skills', 'teamwork',
                        'customer-facing', 'customer facing',
                        'soft skills', 'people skills',
                        'has work experience', 'has experience',
                    ]
                    _justification_lower = _justification_text.lower()
                    _is_weak = (
                        len(_justification_text) < 20
                        or any(wp in _justification_lower for wp in _WEAK_PHRASES)
                    )
                    if _is_weak:
                        issues.append({
                            'check_type': 'recency_misfire',
                            'description': (
                                f"AI marked most recent role as relevant but justification "
                                f"is weak or generic: '{_justification_text[:120]}'. "
                                f"Possible inflated recency classification."
                            )
                        })

        years_json_str = job_match.years_analysis_json
        if years_json_str:
            try:
                years_data = json.loads(years_json_str) if isinstance(years_json_str, str) else years_json_str
                if isinstance(years_data, dict):
                    for skill, data in years_data.items():
                        if not isinstance(data, dict):
                            continue
                        required = float(data.get('required_years', 0))
                        if required <= 0:
                            continue
                        skill_lower = skill.lower()
                        for platform_key, ceiling in platform_age_ceilings.items():
                            if platform_key in skill_lower and required > ceiling:
                                issues.append({
                                    'check_type': 'platform_age_violation',
                                    'description': (
                                        f"Job requires {required:.0f}yr of '{skill}' but "
                                        f"platform max is ~{ceiling:.0f}yr. "
                                        f"Impossible requirement may have inflated gap scoring."
                                    )
                                })
                                break
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        positive_indicators = [
            'strong technical skills', 'meets all', 'meets the mandatory',
            'well-aligned', 'strong match', 'closely aligned',
            'extensive experience', 'solid background', 'strong background'
        ]
        if job_match.match_score is not None and job_match.match_score < 40:
            if any(indicator in match_summary for indicator in positive_indicators):
                issues.append({
                    'check_type': 'score_inconsistency',
                    'description': (
                        f"AI summary uses positive language ('{match_summary[:100]}...') "
                        f"but score is only {job_match.match_score}%. "
                        f"Possible post-processing penalty was too aggressive."
                    )
                })

        if 'location mismatch' in gaps:
            tech_score = job_match.technical_score
            final_score = job_match.match_score or 0
            from models import VettingConfig
            threshold = VettingConfig.get_value('match_threshold', 80.0)
            try:
                threshold = float(threshold)
            except (ValueError, TypeError):
                threshold = 80.0

            if tech_score is not None and tech_score >= (threshold - 10) and not job_match.is_qualified:
                non_loc_gaps = [
                    part.strip() for part in gaps.replace(' | ', '|').split('|')
                    if 'location mismatch' not in part.lower() and part.strip()
                ]
                if not non_loc_gaps:
                    issues.append({
                        'check_type': 'location_score_consistency',
                        'description': (
                            f"Candidate has technical_score={tech_score:.0f}% "
                            f"(threshold={threshold:.0f}%) with location as the ONLY gap, "
                            f"but was marked Not Recommended (final={final_score:.0f}%). "
                            f"This may be a strong technical fit that should be Location Barrier instead."
                        )
                    })
            elif tech_score is None and 'location mismatch' in gaps:
                other_gaps = [
                    part.strip() for part in gaps.replace(' | ', '|').split('|')
                    if 'location mismatch' not in part.lower() and part.strip()
                ]
                if not other_gaps and final_score >= (threshold - 20):
                    issues.append({
                        'check_type': 'location_score_consistency',
                        'description': (
                            f"Location is the only gap but no technical_score recorded "
                            f"(pre two-phase scoring). Final score={final_score:.0f}% "
                            f"with threshold={threshold:.0f}%. Consider re-screening to "
                            f"capture separate technical vs. location scoring."
                        )
                    })

        if 'location mismatch: different country' in gaps:
            try:
                from models import JobVettingRequirements
                job_req = JobVettingRequirements.query.filter_by(
                    bullhorn_job_id=job_match.bullhorn_job_id
                ).first()
                if job_req and (job_req.job_work_type or '').strip().lower() == 'remote':
                    raw_location = (job_req.job_location or '').strip()
                    job_country = raw_location.lower()
                    if ',' in job_country:
                        job_country = job_country.split(',')[-1].strip()

                    summary_lower = (job_match.match_summary or '').lower()
                    resume_header = (vetting_log.resume_text or '')[:600].lower()

                    same_country_signals = []

                    positive_location_phrases = [
                        'meeting the location requirement',
                        'meets the location requirement',
                        'satisfies the location requirement',
                        'meets the remote location',
                        'eligible for remote work in',
                        'qualifies for the remote',
                    ]
                    for phrase in positive_location_phrases:
                        if phrase in summary_lower:
                            same_country_signals.append(f"summary says \"{phrase}\"")
                            break

                    if job_country:
                        affirmative_country_patterns = [
                            f"based in {job_country}",
                            f"located in {job_country}",
                            f"residing in {job_country}",
                            f"candidate is in {job_country}",
                            f"candidate is located in {job_country}",
                        ]
                        for pattern in affirmative_country_patterns:
                            if pattern in summary_lower:
                                same_country_signals.append(
                                    f"summary explicitly places candidate in '{job_country}' ({pattern!r})"
                                )
                                break

                        resume_first_line = resume_header.split('\n')[0] if '\n' in resume_header else resume_header[:120]
                        if f", {job_country}" in resume_first_line or resume_first_line.endswith(job_country):
                            same_country_signals.append(
                                f"resume first line contains '{job_country}' in location position"
                            )

                    if same_country_signals:
                        tech_display = f"{job_match.technical_score:.0f}%" if job_match.technical_score is not None else "N/A"
                        final_display = f"{job_match.match_score:.0f}%" if job_match.match_score is not None else "N/A"
                        issues.append({
                            'check_type': 'remote_location_misfire',
                            'description': (
                                f"Job {job_match.bullhorn_job_id} is Remote (location: {raw_location}). "
                                f"Gaps contain 'location mismatch: different country' but evidence suggests "
                                f"candidate is in the same country — {'; '.join(same_country_signals)}. "
                                f"Technical: {tech_display}, Final: {final_display}. "
                                f"Likely a location penalty misfire on a remote role."
                            )
                        })
            except Exception as _loc_err:
                logger.debug(f"remote_location_misfire check error: {_loc_err}")

        years_json_str2 = job_match.years_analysis_json
        if years_json_str2:
            try:
                years_data2 = json.loads(years_json_str2) if isinstance(years_json_str2, str) else years_json_str2
                if isinstance(years_data2, dict):
                    for skill, data in years_data2.items():
                        if not isinstance(data, dict):
                            continue
                        required = float(data.get('required_years', 0))
                        estimated = float(data.get('estimated_years', data.get('actual_years', 0)))
                        meets = data.get('meets_requirement', True)
                        if required > 0 and estimated >= required and meets is False:
                            issues.append({
                                'check_type': 'experience_undercounting',
                                'description': (
                                    f"AI's own years_analysis shows {estimated:.1f}yr estimated vs "
                                    f"{required:.1f}yr required for '{skill}', but meets_requirement=false. "
                                    f"Direct self-contradiction — candidate may have sufficient experience."
                                )
                            })
                            break
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        if 'employment gap' in gaps:
            resume_header = ''
            if vetting_log.resume_text:
                resume_header = vetting_log.resume_text[:2500].lower()
            employment_current_indicators = ['present', 'current', 'ongoing', 'to date', 'till date']
            if resume_header and any(indicator in resume_header for indicator in employment_current_indicators):
                issues.append({
                    'check_type': 'employment_gap_misfire',
                    'description': (
                        f"Gaps mention 'employment gap' but the candidate's resume header "
                        f"contains current-employment indicators (e.g. 'Present'/'Current'). "
                        f"Possible false gap penalty on an actively employed candidate."
                    )
                })

        auth_flag_phrases = [
            'work authorization cannot be inferred',
            'limited us work history',
            'limited u.s. work history',
            'work authorization unconfirmed',
        ]
        auth_flagged = any(phrase in gaps for phrase in auth_flag_phrases)
        auth_inferred = 'scout screening infers strong likelihood' in match_summary
        if auth_flagged and auth_inferred:
            issues.append({
                'check_type': 'authorization_misfire',
                'description': (
                    f"Gaps flag work authorization concern but match_summary contains "
                    f"'Scout Screening infers strong likelihood' of authorization. "
                    f"AI contradicted itself — authorization inference and gap scoring conflict."
                )
            })

        return issues

    def _run_false_positive_checks(self, vetting_log, job_match) -> List[Dict]:
        """Tier-1 heuristics for Qualified false-positive detection.

        Looks for cases where a candidate scored above the threshold but the
        AI's own outputs contain signals suggesting the score is too high
        (e.g., gaps mention 2+ mandatory skills missing, or summary uses
        negative qualifiers, or years_analysis shows experience well below
        what was required).

        Returns a list of suspected issues. An empty list means the candidate
        looks like a genuine Qualified result and the AI confirmation step
        will be skipped.
        """
        issues: List[Dict] = []

        if not job_match:
            return issues

        gaps = (job_match.gaps_identified or '').lower()
        match_summary = (job_match.match_summary or '').lower()
        score = job_match.match_score or 0

        if score < 50:
            return issues

        mandatory_indicator_phrases = [
            'mandatory skill', 'required skill', 'critical requirement',
            'core requirement', 'must have', 'must-have',
            'no experience with', 'no evidence of', 'lacks experience',
            'missing required',
        ]
        mandatory_gap_count = sum(
            1 for phrase in mandatory_indicator_phrases if phrase in gaps
        )
        if mandatory_gap_count >= 2:
            issues.append({
                'check_type': 'false_positive_skill_gap',
                'description': (
                    f"Score is {score}% (Qualified) but gaps_identified flags "
                    f"{mandatory_gap_count} mandatory-skill concerns: "
                    f"'{(job_match.gaps_identified or '')[:200]}'. "
                    f"Possible false positive — recruiter may receive a candidate who "
                    f"is actually missing required skills."
                )
            })

        negative_qualifiers = [
            'limited experience', 'lacks', 'no evidence',
            'minimal experience', 'minimal exposure', 'shallow experience',
            'brief exposure', 'no demonstrated', 'no proof of',
            'has not demonstrated',
        ]
        negative_hits = [phrase for phrase in negative_qualifiers if phrase in match_summary]
        if negative_hits and score >= 70:
            issues.append({
                'check_type': 'false_positive_negative_summary',
                'description': (
                    f"Score is {score}% (Qualified) but match_summary contains "
                    f"negative qualifier(s): {negative_hits}. "
                    f"Summary text: '{(job_match.match_summary or '')[:200]}'. "
                    f"Possible inflated score — summary describes a weaker fit than "
                    f"the score implies."
                )
            })

        years_json_str = job_match.years_analysis_json
        if years_json_str:
            try:
                years_data = json.loads(years_json_str) if isinstance(years_json_str, str) else years_json_str
                if isinstance(years_data, dict):
                    shortfalls = []
                    for skill, data in years_data.items():
                        if not isinstance(data, dict):
                            continue
                        try:
                            required = float(data.get('required_years', 0) or 0)
                            estimated = float(
                                data.get('estimated_years', data.get('actual_years', 0)) or 0
                            )
                        except (ValueError, TypeError):
                            continue
                        meets = data.get('meets_requirement', True)
                        if required >= 3 and meets is True and estimated < (required * 0.5):
                            shortfalls.append(
                                f"{skill}: {estimated:.1f}yr vs {required:.1f}yr required"
                            )
                    if shortfalls:
                        issues.append({
                            'check_type': 'false_positive_experience_short',
                            'description': (
                                f"Score is {score}% (Qualified) but years_analysis shows "
                                f"the candidate has less than half the required experience "
                                f"on at least one mandatory skill while still marked "
                                f"meets_requirement=true: {'; '.join(shortfalls[:3])}. "
                                f"AI may have over-credited transferable experience."
                            )
                        })
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        return issues

    def _run_ai_audit(self, job_match, resume_text: str, job_title: str,
                      suspected_issues: List[Dict], mode: str = 'not_qualified') -> Dict:
        if not self.openai_api_key:
            logger.error("OpenAI API key not available for screening audit")
            return {'finding_type': 'no_issue', 'confidence': 'low', 'reasoning': 'No API key'}

        issues_text = '\n'.join(
            f"- [{issue['check_type']}] {issue['description']}"
            for issue in suspected_issues
        )

        resume_snippet = resume_text[:4000] if resume_text else 'No resume text available'
        gaps = job_match.gaps_identified or 'None recorded'
        score = job_match.match_score or 0
        summary = job_match.match_summary or 'No summary'

        if mode == 'qualified_false_positive':
            prompt = f"""You are a quality auditor for an AI-powered candidate screening system.
A candidate was scored {score}% (Qualified — recommended to a recruiter) for the job: "{job_title}".

ORIGINAL AI ASSESSMENT:
- Match Summary: {summary}
- Gaps Identified: {gaps}

SUSPECTED FALSE-POSITIVE SIGNALS (flagged by heuristic pre-checks):
{issues_text}

CANDIDATE RESUME (first 4000 chars):
{resume_snippet}

YOUR TASK:
Review each suspected signal and determine if the candidate was OVER-SCORED — i.e.,
the original AI assessment looks favorable but the resume actually fails one or more
mandatory requirements.

For each suspected signal, consider:
1. If gaps mention multiple mandatory skills missing, are those skills genuinely absent from the resume? (If absent, the score should NOT be Qualified.)
2. If the summary uses negative language ("lacks", "limited experience", "no evidence"), does that contradict a score of {score}%?
3. If years_analysis shows large experience shortfalls (less than half required years), did the AI under-weight the requirement?
4. Is there a clear mandatory requirement (e.g., active security clearance, specific certification, location/work-authorization compliance) that the resume cannot satisfy?

Respond in JSON format:
{{
    "finding_type": "<false_positive_skill_gap | false_positive_experience_short | false_positive_negative_summary | false_positive_compliance | no_issue>",
    "confidence": "<high | medium | low>",
    "reasoning": "<2-3 sentence explanation of your finding>",
    "recommended_action": "<revet | flag_for_review | no_action>"
}}

IMPORTANT:
- Only return "high" confidence if the over-scoring is clear and unambiguous
- If multiple issues are confirmed, pick the MOST impactful one as finding_type
- "no_issue" means the Qualified score was correct despite the heuristic flag
- Be conservative: false alarms cost recruiters trust, so prefer "medium" / "low" over "high" when in doubt"""
        else:
            prompt = f"""You are a quality auditor for an AI-powered candidate screening system.
A candidate was scored {score}% (Not Qualified) for the job: "{job_title}".

ORIGINAL AI ASSESSMENT:
- Match Summary: {summary}
- Gaps Identified: {gaps}

SUSPECTED ISSUES (flagged by heuristic pre-checks):
{issues_text}

CANDIDATE RESUME (first 4000 chars):
{resume_snippet}

YOUR TASK:
Review each suspected issue and determine if the original AI assessment contains a genuine error.

For each issue, consider:
1. Is the candidate's CURRENT role relevant to the job domain? Check their most recent position.
2. Are any year-of-experience requirements physically impossible given the technology's age?
3. Does the AI summary contradict the score (positive language but low score)?
4. Are there skills mentioned in the resume that the AI incorrectly said were missing?
5. Does the years_analysis data show the candidate MEETS a requirement but the AI marked it as not met?
6. Was the candidate penalized for an employment gap even though their resume shows "Present" or "Current" employment?
7. Did the AI flag a work authorization concern but also state it infers strong authorization likelihood?

Respond in JSON format:
{{
    "finding_type": "<recency_misfire | platform_age_violation | false_gap_claim | score_inconsistency | experience_undercounting | employment_gap_misfire | authorization_misfire | no_issue>",
    "confidence": "<high | medium | low>",
    "reasoning": "<2-3 sentence explanation of your finding>",
    "recommended_action": "<revet | flag_for_review | no_action>"
}}

IMPORTANT:
- Only return "high" confidence if the error is clear and unambiguous
- If multiple issues are confirmed, pick the MOST impactful one as finding_type
- "no_issue" means the original assessment was correct despite the heuristic flag"""

        try:
            import httpx
            response = httpx.post(
                'https://api.openai.com/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {self.openai_api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': get_auditor_model(),
                    'messages': [
                        {'role': 'system', 'content': 'You are a quality auditor. Respond only in valid JSON.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'max_completion_tokens': 1500,
                    'response_format': {'type': 'json_object'}
                },
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
            content = result['choices'][0]['message']['content']
            return json.loads(content)

        except Exception as e:
            logger.error(f"❌ AI audit call failed: {str(e)}")
            return {
                'finding_type': 'no_issue',
                'confidence': 'low',
                'reasoning': f'AI audit call failed: {str(e)}',
                'recommended_action': 'no_action'
            }

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

    def _send_audit_summary_email(self, summary: Dict):
        from app import db as app_db
        from models import VettingConfig, EmailDeliveryLog
        from email_service import EmailService

        admin_email = VettingConfig.get_value('admin_notification_email', '')
        if not admin_email:
            logger.warning("No admin_notification_email configured — skipping audit summary email")
            return

        # Re-read the persisted revet_new_score from VettingAuditLog when
        # available so this email reflects any back-fill that landed
        # between the audit cycle and email send time. The synchronous
        # _trigger_revet path returns None (the next vetting cycle is
        # async), so the in-memory ``new_score`` will be None for fresh
        # re-vets; the back-fill helper updates the persisted column the
        # moment the next vetting cycle finishes.
        from models import VettingAuditLog
        details_html = ''
        if summary.get('details'):
            rows = ''
            for d in summary['details']:
                action_badge = {
                    'revet_triggered': '<span style="color: #22c55e;">✅ Re-vetted</span>',
                    'flagged_for_review': '<span style="color: #f59e0b;">⚠️ Flagged</span>',
                    'revet_skipped_capped': '<span style="color: #f59e0b;">⛔ Re-vet capped (24h)</span>',
                    'revet_skipped_stable': '<span style="color: #6b7280;">⏸ Re-vet skipped (stable)</span>',
                    'no_action': '<span style="color: #6b7280;">—</span>'
                }.get(d.get('action_taken', ''), '—')

                resolved_new_score = d.get('new_score')
                if resolved_new_score is None and d.get('audit_log_id'):
                    try:
                        audit_row = VettingAuditLog.query.get(d['audit_log_id'])
                        if audit_row is not None and audit_row.revet_new_score is not None:
                            resolved_new_score = audit_row.revet_new_score
                    except Exception as lookup_err:
                        logger.warning(
                            f"⚠️ Audit summary email: failed to re-read "
                            f"audit row {d.get('audit_log_id')}: {lookup_err!r}"
                        )

                new_score_str = (
                    f"{resolved_new_score:.0f}%"
                    if resolved_new_score is not None
                    else 'Pending'
                )

                rows += f"""<tr>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('candidate_name', 'Unknown')}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('job_title', 'Unknown')}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('original_score', 0):.0f}%</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{new_score_str}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('finding_type', '')}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{action_badge}</td>
                </tr>"""

            details_html = f"""
            <table style="width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 14px;">
                <thead>
                    <tr style="background: #1f2937; color: #e5e7eb;">
                        <th style="padding: 8px; text-align: left;">Candidate</th>
                        <th style="padding: 8px; text-align: left;">Job</th>
                        <th style="padding: 8px; text-align: left;">Original</th>
                        <th style="padding: 8px; text-align: left;">New</th>
                        <th style="padding: 8px; text-align: left;">Issue</th>
                        <th style="padding: 8px; text-align: left;">Action</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>"""

        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; background: #111827; color: #e5e7eb; padding: 24px; border-radius: 8px;">
            <h2 style="color: #f59e0b; margin-bottom: 16px;">🔍 Scout Screening Quality Audit</h2>
            <div style="background: #1f2937; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
                <p style="margin: 4px 0;"><strong>Results Audited:</strong> {summary['total_audited']}</p>
                <p style="margin: 4px 0;"><strong>Issues Found:</strong> {summary['issues_found']}</p>
                <p style="margin: 4px 0;"><strong>Re-vets Triggered:</strong> {summary['revets_triggered']}</p>
            </div>
            {details_html}
            <p style="color: #6b7280; font-size: 12px; margin-top: 24px;">
                This is an automated quality audit from Scout Genius™. 
                Re-vetted candidates will have updated Bullhorn notes once the next vetting cycle processes them.
            </p>
        </div>
        """

        email_service = EmailService(db=app_db, EmailDeliveryLog=EmailDeliveryLog)
        email_service.send_email(
            to_email=admin_email,
            subject=f"Scout Screening Audit: {summary['issues_found']} issue(s) found, {summary['revets_triggered']} re-vet(s) triggered",
            html_content=html_body,
            notification_type='screening_audit_summary'
        )
        logger.info(f"📧 Audit summary email sent to {admin_email}")
