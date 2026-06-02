"""Fraud-detection orchestration engine.

Gathers deterministic facts from the database (resume reuse, identity reuse,
profile near-duplicates, application velocity, contact anomalies, disposable
emails), feeds them into the pure evaluators in `fraud_detection.signals`,
persists a `CandidateFraudAssessment` row, and — on High-Risk, when enabled —
writes a vendor-neutral note to Bullhorn.

Design tenets:
  * **Advisory only** — nothing here blocks or alters screening. The caller
    ignores the return value for control flow.
  * **Fail-soft** — every external touch is wrapped; a failure records an
    `evaluation_error` and returns a CLEAR result rather than raising.
  * **Zero AI cost** — all signals are deterministic. The only embeddings used
    are ones already cached by the normal pipeline; no new API calls are made.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app import db
from models import (
    CandidateFraudAssessment,
    CandidateVettingLog,
    CandidateProfileEmbedding,
    VettingConfig,
)
from fraud_detection import signals as fsig

logger = logging.getLogger("fraud_detection")

# Bound the embedding scan so the near-dup check can't turn the screening hook
# into an O(N) table walk on large datasets.
_EMBEDDING_SCAN_LIMIT = 2000
# Velocity window: count this candidate's applications in the last N hours.
_VELOCITY_WINDOW_HOURS = fsig.DEFAULT_VELOCITY_WINDOW_HOURS


class FraudSignalEngine:
    """Orchestrates fact-gathering + scoring + persistence for one candidate."""

    def __init__(self, bullhorn_service: Any = None):
        # Optional — only needed when a Bullhorn note must be written.
        self.bullhorn_service = bullhorn_service

    # ------------------------------------------------------------------ config
    def _load_config(self) -> Dict[str, Any]:
        """Read fraud settings from VettingConfig (string-valued)."""
        def _flag(key: str, default: str = "false") -> bool:
            return str(VettingConfig.get_value(key, default)).strip().lower() == "true"

        def _int(key: str, default: int) -> int:
            try:
                return int(str(VettingConfig.get_value(key, str(default))).strip())
            except (ValueError, TypeError):
                return default

        review = _int("fraud_review_threshold", fsig.DEFAULT_REVIEW_THRESHOLD)
        high = _int("fraud_high_risk_threshold", fsig.DEFAULT_HIGH_RISK_THRESHOLD)
        if review >= high:  # guard against inverted bands
            review, high = fsig.DEFAULT_REVIEW_THRESHOLD, fsig.DEFAULT_HIGH_RISK_THRESHOLD
        return {
            "enabled": _flag("fraud_detection_enabled"),
            "note_enabled": _flag("fraud_bullhorn_note_enabled"),
            "note_all_bands": _flag("fraud_note_all_bands_enabled"),
            "review_threshold": review,
            "high_risk_threshold": high,
        }

    # ------------------------------------------------------------------- public
    def assess(
        self,
        candidate: Dict[str, Any],
        vetting_log: Optional[CandidateVettingLog] = None,
        trigger: str = "screening",
        applied_job_description: Optional[str] = None,
        candidate_country: Optional[str] = None,
        job_country: Optional[str] = None,
    ) -> Optional[CandidateFraudAssessment]:
        """Score a candidate and persist an assessment row.

        The optional ``applied_job_description`` / ``candidate_country`` /
        ``job_country`` enable the job-relative signals (verbatim JD-mirror and
        the foreign-location amplifier). They are passed by the screening hook
        when the applied job is resolvable; absent, those signals simply don't
        fire (everything stays fail-soft and advisory).

        Returns the persisted `CandidateFraudAssessment` (or None if it could
        not be persisted). NEVER raises — callers treat the result as advisory.
        """
        config = self._load_config()
        candidate_id = candidate.get("id") if candidate else None
        name = self._candidate_name(candidate, vetting_log)
        first, last = self._candidate_first_last(candidate, vetting_log)
        email = self._candidate_email(candidate, vetting_log)
        phone = self._candidate_phone(candidate)
        resume_text = getattr(vetting_log, "resume_text", None)
        linkedin_url = self._candidate_linkedin(candidate, vetting_log)
        vetting_log_id = getattr(vetting_log, "id", None)

        evaluation_error: Optional[str] = None
        gathered: List[Optional[fsig.FraudSignal]] = []

        try:
            # --- deterministic, dependency-free signals -----------------
            gathered.append(fsig.evaluate_disposable_email(email))
            gathered.extend(fsig.evaluate_contact_anomalies(name, email, phone))
            gathered.extend(fsig.evaluate_work_history(self._extract_work_history(candidate)))

            # --- DB-derived signals (each fail-soft, zero AI cost) ------
            gathered.append(fsig.evaluate_resume_reuse(
                self._count_resume_reuse(candidate_id, vetting_log)))
            gathered.extend(fsig.evaluate_identity_reuse(
                distinct_names_for_email=self._count_distinct_names_for_email(email, candidate_id),
                distinct_names_for_phone=self._count_distinct_names_for_phone(phone, candidate_id),
            ))
            gathered.append(fsig.evaluate_profile_near_duplicate(
                *self._top_profile_similarity(candidate_id)))
            gathered.append(fsig.evaluate_velocity(
                self._count_recent_applications(candidate_id, email)))

            # --- LinkedIn profile reuse across identities (DB, $0) ------
            gathered.append(fsig.evaluate_linkedin(
                linkedin_url,
                self._count_distinct_identities_for_linkedin(linkedin_url, candidate_id),
            ))

            # --- name completeness + third-party-submission composite --
            name_incomplete = fsig.is_incomplete_name(first, last)
            # Third-party submission is specifically the legit-looking
            # corporate/agency-domain pattern, so it requires a PRESENT, valid,
            # non-personal, non-disposable email. A missing/malformed address is
            # NOT evidence of a third-party submission, and a disposable address
            # is a distinct (separately scored) signal — both are excluded so the
            # composite never fires on an unknown email or double-counts.
            from fraud_detection.disposable_domains import is_disposable_domain
            email_qualifies = bool(
                email and "@" in email and "." in email.split("@")[-1]
                and not fsig.is_personal_email(email)
                and not is_disposable_domain(email)
            )
            foreign_location = self._is_foreign_location(candidate_country, job_country)
            gathered.append(fsig.evaluate_name_completeness(first, last))
            gathered.append(fsig.evaluate_third_party_submission(
                name_incomplete=name_incomplete,
                email_personal=not email_qualifies,
                foreign_location=foreign_location,
            ))

            # --- verbatim JD-mirror (resume vs applied job description) -
            gathered.append(fsig.evaluate_jd_mirror(resume_text, applied_job_description))

            # --- informational only (0 points, never accuses) ----------
            gathered.append(fsig.evaluate_ai_style_markers(resume_text))
        except Exception as exc:  # pragma: no cover - defensive umbrella
            evaluation_error = f"signal gathering failed: {exc}"
            logger.warning("Fraud signal gathering error for candidate %s: %s",
                           candidate_id, exc, exc_info=True)

        result = fsig.aggregate(
            gathered,
            review_threshold=config["review_threshold"],
            high_risk_threshold=config["high_risk_threshold"],
        )

        assessment = self._persist(
            candidate_id=candidate_id,
            vetting_log_id=vetting_log_id,
            name=name,
            email=email,
            result=result,
            trigger=trigger,
            evaluation_error=evaluation_error,
        )

        # Vendor-neutral Bullhorn note policy (all gated by note_enabled):
        #   * High-Risk always qualifies.
        #   * Review/Clear qualify only when the separate all-bands toggle is on.
        # With the all-bands toggle OFF (its default), this is identical to the
        # historical High-Risk-only behavior.
        band = result.risk_band
        note_band_ok = (
            band == fsig.FraudRiskBand.HIGH_RISK
            or (
                config["note_all_bands"]
                and band in (fsig.FraudRiskBand.REVIEW, fsig.FraudRiskBand.CLEAR)
            )
        )
        if (
            assessment is not None
            and config["note_enabled"]
            and note_band_ok
            and candidate_id
        ):
            self._maybe_write_note(candidate_id, result, assessment)

        return assessment

    # ----------------------------------------------------------- identity bits
    @staticmethod
    def _candidate_name(candidate, vetting_log) -> str:
        if candidate:
            first = candidate.get("firstName") or ""
            last = candidate.get("lastName") or ""
            joined = f"{first} {last}".strip()
            if joined:
                return joined
            if candidate.get("name"):
                return str(candidate["name"]).strip()
        return (getattr(vetting_log, "candidate_name", None) or "").strip()

    @staticmethod
    def _candidate_email(candidate, vetting_log) -> str:
        if candidate and candidate.get("email"):
            return str(candidate["email"]).strip()
        return (getattr(vetting_log, "candidate_email", None) or "").strip()

    @staticmethod
    def _candidate_phone(candidate) -> str:
        if not candidate:
            return ""
        for key in ("phone", "mobile", "phone2", "phone3", "workPhone"):
            val = candidate.get(key)
            if val:
                return str(val).strip()
        return ""

    @staticmethod
    def _candidate_first_last(candidate, vetting_log):
        """Return (first, last) name parts for the name-completeness check.

        Prefers the structured Bullhorn firstName/lastName fields; falls back to
        splitting the stored display name when only that is available.
        """
        if candidate:
            first = (candidate.get("firstName") or "").strip()
            last = (candidate.get("lastName") or "").strip()
            if first or last:
                return first, last
        display = (getattr(vetting_log, "candidate_name", None) or "").strip()
        if display:
            parts = display.split()
            if len(parts) == 1:
                return parts[0], ""
            return parts[0], " ".join(parts[1:])
        return "", ""

    @staticmethod
    def _candidate_linkedin(candidate, vetting_log) -> str:
        """Canonical LinkedIn URL for the reuse check.

        Prefers the value captured on the vetting log (extracted universally from
        resume text upstream); falls back to scanning a couple of common Bullhorn
        custom fields if present. Returns '' when none is found.
        """
        stored = (getattr(vetting_log, "candidate_linkedin_url", None) or "").strip()
        if stored:
            return stored
        if candidate:
            return fsig.extract_linkedin_url(
                candidate.get("customText9"),
                candidate.get("description"),
            )
        return ""

    @staticmethod
    def _is_foreign_location(candidate_country, job_country) -> bool:
        """True when both countries are known and differ (soft amplifier only).

        Conservative: returns False whenever either side is missing, so the
        third-party composite never relies on an unknown location.
        """
        def _norm(c):
            return re.sub(r"[^a-z]", "", str(c or "").lower())
        cc, jc = _norm(candidate_country), _norm(job_country)
        if not cc or not jc:
            return False
        # Treat common US/UK aliases as equal to avoid false mismatches.
        aliases = {
            "unitedstates": "us", "usa": "us", "us": "us",
            "unitedstatesofamerica": "us",
            "unitedkingdom": "uk", "uk": "uk", "greatbritain": "uk",
        }
        cc = aliases.get(cc, cc)
        jc = aliases.get(jc, jc)
        return cc != jc

    def _count_distinct_identities_for_linkedin(self, linkedin_url, candidate_id) -> int:
        """Count OTHER candidate identities presenting the same LinkedIn URL.

        Reads the canonical `candidate_linkedin_url` column on
        `candidate_vetting_log` so the lookup is a plain indexed equality.
        Returns the number of DISTINCT other Bullhorn candidate IDs sharing the
        URL (the current candidate is excluded).
        """
        if not linkedin_url:
            return 0
        try:
            with Session(db.engine) as session:
                rows = (
                    session.query(CandidateVettingLog.bullhorn_candidate_id)
                    .filter(CandidateVettingLog.candidate_linkedin_url == linkedin_url)
                    .filter(CandidateVettingLog.bullhorn_candidate_id.isnot(None))
                    .filter(CandidateVettingLog.is_sandbox.is_(False))
                    .distinct()
                    .limit(200)
                    .all()
                )
            others = {
                r[0] for r in rows
                if r[0] is not None and (candidate_id is None or r[0] != candidate_id)
            }
            return len(others)
        except Exception as exc:  # pragma: no cover
            logger.debug("linkedin-reuse query failed: %s", exc)
            return 0

    @staticmethod
    def _extract_work_history(candidate) -> List[Dict[str, Any]]:
        """Pull a work-history list from the candidate dict if present.

        Bullhorn candidate payloads vary; we accept a handful of common shapes
        and tolerate their absence (returns []). Each item should expose some
        start/end keys that `signals._parse_date` understands.
        """
        if not candidate:
            return []
        for key in ("workHistory", "work_history", "employmentHistory", "_work_history"):
            val = candidate.get(key)
            if isinstance(val, list):
                return [v for v in val if isinstance(v, dict)]
            if isinstance(val, dict) and isinstance(val.get("data"), list):
                return [v for v in val["data"] if isinstance(v, dict)]
        return []

    # --------------------------------------------------------- DB-derived facts
    def _count_resume_reuse(self, candidate_id, vetting_log) -> int:
        """Count OTHER candidate identities sharing this resume's content.

        Uses Postgres ``md5(resume_text)`` over `candidate_vetting_log` so it
        works across distinct Bullhorn candidate IDs (the cache table can't —
        its content_hash is unique and byte-based). Returns the number of
        DISTINCT other candidate IDs whose stored resume text is identical.
        """
        resume_text = getattr(vetting_log, "resume_text", None)
        if not resume_text or len(resume_text) < 200:
            return 0
        try:
            with Session(db.engine) as session:
                dialect = session.bind.dialect.name if session.bind else ""
                if dialect == "postgresql":
                    # Efficient server-side hashing on the live DB.
                    row = session.execute(
                        text(
                            "SELECT COUNT(DISTINCT bullhorn_candidate_id) "
                            "FROM candidate_vetting_log "
                            "WHERE resume_text IS NOT NULL "
                            "AND md5(resume_text) = md5(:rt) "
                            "AND bullhorn_candidate_id IS NOT NULL "
                            "AND (:cid IS NULL OR bullhorn_candidate_id <> :cid) "
                            "AND is_sandbox = false"
                        ),
                        {"rt": resume_text, "cid": candidate_id},
                    ).scalar()
                    return int(row or 0)

                # Dialect-agnostic fallback (e.g. SQLite in tests): hash in Python.
                target_hash = hashlib.md5(resume_text.encode("utf-8")).hexdigest()
                rows = (
                    session.query(
                        CandidateVettingLog.bullhorn_candidate_id,
                        CandidateVettingLog.resume_text,
                    )
                    .filter(CandidateVettingLog.resume_text.isnot(None))
                    .filter(CandidateVettingLog.bullhorn_candidate_id.isnot(None))
                    .filter(CandidateVettingLog.is_sandbox.is_(False))
                    .all()
                )
                others = set()
                for cid, rt in rows:
                    if candidate_id is not None and cid == candidate_id:
                        continue
                    if rt and hashlib.md5(rt.encode("utf-8")).hexdigest() == target_hash:
                        others.add(cid)
                return len(others)
        except Exception as exc:  # pragma: no cover - DB dialect/edge
            logger.debug("resume-reuse query failed: %s", exc)
            return 0

    def _count_distinct_names_for_email(self, email, candidate_id) -> int:
        """Count distinct normalized names that have used this email address."""
        if not email:
            return 0
        try:
            with Session(db.engine) as session:
                rows = (
                    session.query(CandidateVettingLog.candidate_name)
                    .filter(func.lower(CandidateVettingLog.candidate_email) == email.lower())
                    .filter(CandidateVettingLog.is_sandbox.is_(False))
                    .distinct()
                    .limit(200)
                    .all()
                )
            names = {fsig.normalize_name(r[0]) for r in rows if r[0]}
            names.discard("")
            return len(names)
        except Exception as exc:  # pragma: no cover
            logger.debug("identity-reuse query failed: %s", exc)
            return 0

    def _count_distinct_names_for_phone(self, phone, candidate_id) -> int:
        """Count distinct normalized names that have used this phone number.

        Reads the pre-normalized `candidate_phone` column on
        `candidate_vetting_log` so the lookup is a plain indexed equality.
        Phone reuse across identities is a stronger fraud signal than email
        (harder to share by accident), but short/garbage numbers over-match,
        so anything under 10 digits is ignored.
        """
        normalized = fsig.normalize_phone(phone)
        if len(normalized) < 10:
            return 0
        try:
            with Session(db.engine) as session:
                rows = (
                    session.query(CandidateVettingLog.candidate_name)
                    .filter(CandidateVettingLog.candidate_phone == normalized)
                    .filter(CandidateVettingLog.is_sandbox.is_(False))
                    .distinct()
                    .limit(200)
                    .all()
                )
            names = {fsig.normalize_name(r[0]) for r in rows if r[0]}
            names.discard("")
            return len(names)
        except Exception as exc:  # pragma: no cover
            logger.debug("identity-reuse (phone) query failed: %s", exc)
            return 0

    def _count_recent_applications(self, candidate_id, email) -> int:
        """Count this candidate's vetting logs in the velocity window."""
        if not candidate_id and not email:
            return 0
        try:
            cutoff = datetime.utcnow() - timedelta(hours=_VELOCITY_WINDOW_HOURS)
            with Session(db.engine) as session:
                q = session.query(func.count(CandidateVettingLog.id)).filter(
                    CandidateVettingLog.created_at >= cutoff,
                    CandidateVettingLog.is_sandbox.is_(False),
                )
                if candidate_id:
                    q = q.filter(CandidateVettingLog.bullhorn_candidate_id == candidate_id)
                else:
                    q = q.filter(func.lower(CandidateVettingLog.candidate_email) == email.lower())
                return int(q.scalar() or 0)
        except Exception as exc:  # pragma: no cover
            logger.debug("velocity query failed: %s", exc)
            return 0

    def _top_profile_similarity(self, candidate_id):
        """Return (top_similarity, identity_differs) for near-dup detection.

        Compares this candidate's CACHED embedding (no new API call) against
        other cached embeddings, bounded to the most-recent N rows. Returns
        ``(None, False)`` when there's nothing to compare — which the evaluator
        treats as "no signal".
        """
        if not candidate_id:
            return (None, False)
        try:
            with Session(db.engine) as session:
                target_row = (
                    session.query(CandidateProfileEmbedding)
                    .filter_by(bullhorn_candidate_id=candidate_id)
                    .first()
                )
                if not target_row or not target_row.embedding_vector:
                    return (None, False)
                target_vec = json.loads(target_row.embedding_vector)
                if not target_vec:
                    return (None, False)
                target_norm = sum(v * v for v in target_vec) ** 0.5
                if target_norm == 0:
                    return (None, False)

                rows = (
                    session.query(CandidateProfileEmbedding)
                    .order_by(CandidateProfileEmbedding.updated_at.desc())
                    .limit(_EMBEDDING_SCAN_LIMIT)
                    .all()
                )
                best = None
                for row in rows:
                    if row.bullhorn_candidate_id == candidate_id:
                        continue
                    try:
                        vec = json.loads(row.embedding_vector)
                    except (TypeError, ValueError):
                        continue
                    if not vec:
                        continue
                    n = min(len(vec), len(target_vec))
                    v_use, t_use = vec[:n], target_vec[:n]
                    v_norm = sum(v * v for v in v_use) ** 0.5
                    if v_norm == 0:
                        continue
                    dot = sum(a * b for a, b in zip(t_use, v_use))
                    sim = max(-1.0, min(1.0, dot / (target_norm * v_norm)))
                    if best is None or sim > best:
                        best = sim
                if best is None:
                    return (None, False)
                return (best, True)
        except Exception as exc:  # pragma: no cover
            logger.debug("near-dup query failed: %s", exc)
            return (None, False)

    # ------------------------------------------------------------- persistence
    def _persist(
        self, candidate_id, vetting_log_id, name, email, result, trigger,
        evaluation_error,
    ) -> Optional[CandidateFraudAssessment]:
        try:
            # Isolated session with expire_on_commit=False so the returned row
            # remains usable (read-only) after the session closes, and a fraud
            # persistence error can NEVER touch the caller's vetting txn.
            with Session(db.engine, expire_on_commit=False) as session:
                assessment = CandidateFraudAssessment(
                    bullhorn_candidate_id=candidate_id,
                    vetting_log_id=vetting_log_id,
                    candidate_name=(name or None) and name[:200],
                    candidate_email=(email or None) and email[:255],
                    risk_score=result.risk_score,
                    risk_band=result.risk_band.value,
                    signals_json=json.dumps(result.signals_payload()),
                    trigger=trigger,
                    note_created=False,
                    evaluation_error=evaluation_error,
                )
                session.add(assessment)
                session.commit()
                return assessment
        except Exception as exc:
            logger.warning("Failed to persist fraud assessment for candidate %s: %s",
                           candidate_id, exc, exc_info=True)
            return None

    # --------------------------------------------------------------- bullhorn
    def _maybe_write_note(self, candidate_id, result, assessment) -> None:
        """Write a vendor-neutral, band-aware risk note to Bullhorn (fail-soft).

        Band gating is decided by the caller (`assess`); by default only
        High-Risk reaches here, but the all-bands toggle can extend it to
        Review/Clear. The note body adapts to the band via `_build_note_text`.
        """
        try:
            service = self.bullhorn_service
            if service is None:
                logger.info("Fraud note skipped: no Bullhorn service provided "
                            "(candidate %s)", candidate_id)
                return
            note_text = self._build_note_text(result)
            note_id = service.create_candidate_note(
                int(candidate_id),
                note_text,
                action="Candidate Risk Review",
            )
            if note_id:
                assessment.note_created = True
                assessment.bullhorn_note_id = int(note_id)
                # Persist the note linkage in an isolated session.
                with Session(db.engine, expire_on_commit=False) as session:
                    row = session.get(CandidateFraudAssessment, assessment.id)
                    if row is not None:
                        row.note_created = True
                        row.bullhorn_note_id = int(note_id)
                        session.commit()
        except Exception as exc:
            logger.warning("Failed to write fraud note for candidate %s: %s",
                           candidate_id, exc, exc_info=True)

    @staticmethod
    def _build_note_text(result) -> str:
        """Compose a concise, vendor-neutral note body, band-aware.

        High-Risk and Review list the contributing indicators; Clear states that
        no indicators were detected (clear candidates have no fired signals).
        """
        band = result.risk_band
        score = result.risk_score
        if band == fsig.FraudRiskBand.HIGH_RISK:
            header = ("Automated candidate-integrity review flagged this profile as "
                      f"HIGH RISK (risk score {score}/100).")
        elif band == fsig.FraudRiskBand.REVIEW:
            header = ("Automated candidate-integrity review flagged this profile for "
                      f"REVIEW (risk score {score}/100).")
        else:  # clear
            header = ("Automated candidate-integrity review found no risk indicators "
                      f"for this profile (risk score {score}/100 — Clear).")

        # Separate scored indicators from purely informational (0-point) notes
        # so an informational item (e.g. AI-style markers) is never presented as
        # a risk "indicator".
        scored = [s for s in result.signals if (s.points or 0) > 0]
        informational = [s for s in result.signals if (s.points or 0) == 0]

        lines = [header, ""]
        if scored:
            lines.append("Indicators detected:")
            for s in scored:
                evidence = f" — {s.evidence}" if s.evidence else ""
                lines.append(f"  • {s.label}{evidence}")
                # Additively document the verbatim copied passage for a
                # JD-mirror hit (what was lifted and where), without altering
                # any note gating or band logic. Only present when captured.
                details = getattr(s, "details", None) or {}
                passage = str(details.get("copied_text") or "").strip()
                if passage:
                    lines.append(f"      Copied passage: \"{passage}\"")
                    resume_ex = str(details.get("resume_excerpt") or "").strip()
                    jd_ex = str(details.get("jd_excerpt") or "").strip()
                    if resume_ex:
                        lines.append(f"      In résumé: …{resume_ex}…")
                    if jd_ex:
                        lines.append(f"      In job posting: …{jd_ex}…")
        else:
            lines.append("No risk indicators were detected across the integrity checks.")
        if informational:
            lines.append("")
            lines.append("Informational (not scored):")
            for s in informational:
                evidence = f" — {s.evidence}" if s.evidence else ""
                lines.append(f"  • {s.label}{evidence}")
        lines.append("")
        lines.append(
            "This is an advisory flag for recruiter judgement only; it does not "
            "block screening or submission. Please verify the candidate's "
            "details before proceeding."
        )
        return "\n".join(lines)
