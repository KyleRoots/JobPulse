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

import base64
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
    ResumeDocumentFingerprint,
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
            "contact_validation": _flag("fraud_contact_validation_enabled"),
            "linkedin_crosscheck": _flag(
                "fraud_linkedin_crosscheck_enabled", default="true"
            ),
            "review_threshold": review,
            "high_risk_threshold": high,
        }

    # ------------------------------------------------------------------- public
    # Paid NeverBounce / Twilio signal codes — stripped/replaced on enrichment.
    _CONTACT_SIGNAL_CODES = frozenset({
        "email_undeliverable",
        "phone_invalid",
        "phone_voip",
    })

    def assess(
        self,
        candidate: Dict[str, Any],
        vetting_log: Optional[CandidateVettingLog] = None,
        trigger: str = "screening",
        applied_job_description: Optional[str] = None,
        candidate_country: Optional[str] = None,
        job_country: Optional[str] = None,
        pdf_metadata: Optional[Dict[str, Any]] = None,
        include_contact_validation: bool = True,
    ) -> Optional[CandidateFraudAssessment]:
        """Score a candidate and persist an assessment row.

        The optional ``applied_job_description`` / ``candidate_country`` /
        ``job_country`` enable the job-relative signals (verbatim JD-mirror and
        the foreign-location amplifier). ``pdf_metadata`` enables document
        forensics. They are passed by the screening hook when available;
        absent, those signals simply don't fire (everything stays fail-soft
        and advisory).

        ``include_contact_validation`` controls NeverBounce/Twilio. Screening
        passes False on the early pre-score pass; paid checks run later via
        ``enrich_contact_validation`` only for Qualified candidates.

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
            _resume_reuse = self._count_resume_reuse(candidate_id, vetting_log)
            gathered.extend(fsig.evaluate_resume_reuse(
                genuine_identities=_resume_reuse.get("genuine"),
                duplicate_records=_resume_reuse.get("duplicates"),
            ))
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

            # --- multi-submission drift (same contact / candidate ID) --
            gathered.append(fsig.evaluate_submission_drift(
                self._gather_submission_drift(
                    candidate_id=candidate_id,
                    email=email,
                    phone=phone,
                    name=name,
                    linkedin_url=linkedin_url,
                    resume_text=resume_text,
                    vetting_log_id=vetting_log_id,
                )
            ))

            # --- divergent Resume-typed files on same BH candidate ------
            # Free / BH-local only (no NeverBounce). Fail-soft on fetch.
            gathered.append(fsig.evaluate_divergent_resume_versions(
                self._gather_resume_file_versions(candidate_id)
            ))

            # --- PDF metadata / author-signature reuse -----------------
            gathered.extend(self._gather_pdf_signals(
                pdf_metadata=pdf_metadata,
                candidate_id=candidate_id,
                name=name,
                resume_text=resume_text,
                vetting_log_id=vetting_log_id,
            ))

            # --- name completeness + third-party-submission composite --
            name_incomplete = fsig.is_incomplete_name(first, last)
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

            # --- optional contact validation (NeverBounce / Twilio) ----
            # Screening defers this until after qualification (see
            # enrich_contact_validation). Direct callers may still include it.
            if include_contact_validation and config.get("contact_validation"):
                gathered.extend(self._gather_contact_validation(email, phone))

            # --- soft LinkedIn URL cross-check (public, URL-only) ------
            if config.get("linkedin_crosscheck") and linkedin_url:
                gathered.extend(self._gather_linkedin_crosscheck(
                    linkedin_url, resume_name=name,
                ))

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

    def enrich_contact_validation(
        self,
        candidate: Dict[str, Any],
        vetting_log: Optional[CandidateVettingLog] = None,
    ) -> Optional[CandidateFraudAssessment]:
        """Run NeverBounce/Twilio and merge into an existing assessment.

        Intended for Qualified candidates only (caller gates on
        ``vetting_log.is_qualified``). Fail-soft: never raises. No-op when the
        contact-validation toggle is off, secrets are missing, or no prior
        assessment row exists for this vetting log.
        """
        try:
            config = self._load_config()
            if not config.get("contact_validation"):
                return None

            vetting_log_id = getattr(vetting_log, "id", None)
            if not vetting_log_id:
                return None

            email = self._candidate_email(candidate, vetting_log)
            phone = self._candidate_phone(candidate)
            candidate_id = candidate.get("id") if candidate else None

            contact_signals = self._gather_contact_validation(email, phone)
            # Still update when checks return no fired signals (valid email/phone)
            # so score stays consistent; empty list is fine.

            with Session(db.engine, expire_on_commit=False) as session:
                assessment = (
                    session.query(CandidateFraudAssessment)
                    .filter_by(vetting_log_id=vetting_log_id)
                    .order_by(CandidateFraudAssessment.id.desc())
                    .first()
                )
                if assessment is None:
                    logger.info(
                        "Contact enrichment skipped: no assessment for vetting_log %s",
                        vetting_log_id,
                    )
                    return None

                existing: List[fsig.FraudSignal] = []
                try:
                    raw = json.loads(assessment.signals_json or "[]")
                    if isinstance(raw, list):
                        for item in raw:
                            if not isinstance(item, dict) or not item.get("code"):
                                continue
                            if item["code"] in self._CONTACT_SIGNAL_CODES:
                                continue
                            existing.append(fsig.FraudSignal(
                                code=str(item["code"]),
                                label=str(item.get("label") or item["code"]),
                                points=int(item.get("points") or 0),
                                evidence=str(item.get("evidence") or ""),
                                details=item.get("details") or {},
                            ))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "Contact enrichment: could not parse signals_json for "
                        "assessment %s: %s", assessment.id, exc,
                    )
                    existing = []

                merged = existing + list(contact_signals)
                result = fsig.aggregate(
                    merged,
                    review_threshold=config["review_threshold"],
                    high_risk_threshold=config["high_risk_threshold"],
                )
                assessment.risk_score = result.risk_score
                assessment.risk_band = result.risk_band.value
                assessment.signals_json = json.dumps(result.signals_payload())
                session.commit()

            logger.info(
                "Contact validation enriched assessment %s for candidate %s: "
                "band=%s score=%s contact_signals=%d",
                assessment.id,
                candidate_id,
                assessment.risk_band,
                assessment.risk_score,
                len(contact_signals),
            )

            band = result.risk_band
            note_band_ok = (
                band == fsig.FraudRiskBand.HIGH_RISK
                or (
                    config["note_all_bands"]
                    and band in (fsig.FraudRiskBand.REVIEW, fsig.FraudRiskBand.CLEAR)
                )
            )
            if (
                config["note_enabled"]
                and note_band_ok
                and candidate_id
                and not assessment.note_created
            ):
                self._maybe_write_note(candidate_id, result, assessment)

            return assessment
        except Exception as exc:
            logger.warning(
                "Contact validation enrichment failed (advisory): %s",
                exc, exc_info=True,
            )
            return None

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
    def _count_resume_reuse(self, candidate_id, vetting_log) -> dict:
        """Classify OTHER candidate records that share this résumé's exact content.

        Uses Postgres ``md5(resume_text)`` over `candidate_vetting_log` so it
        works across distinct Bullhorn candidate IDs (the cache table can't — its
        content_hash is unique and byte-based). Each OTHER candidate record is
        classified by comparing its name + email to THIS candidate:

          - "duplicates": SAME normalized name AND SAME normalized email — the
            same person entered twice (e.g. a duplicate Bullhorn record). Benign;
            surfaced only as an informational "consider merging" note, never
            scored as fraud.
          - "genuine":    name OR email differs — a genuinely different identity
            using the same résumé. The actual fraud indicator.

        Returns ``{"genuine": [...], "duplicates": [...]}`` where each item is
        ``{"candidate_id", "name", "email", "last_seen"}``. Fail-soft: returns
        empty lists on any error.
        """
        empty = {"genuine": [], "duplicates": []}
        resume_text = getattr(vetting_log, "resume_text", None)
        if not resume_text or len(resume_text) < 200:
            return empty

        this_name = fsig.normalize_name(getattr(vetting_log, "candidate_name", None))
        this_email = (getattr(vetting_log, "candidate_email", None) or "").strip().lower()

        def _fmt_date(value):
            if value is None:
                return ""
            try:
                return value.date().isoformat()
            except AttributeError:
                return str(value)[:10]

        def _classify(rows):
            genuine, dupes = [], []
            for cid, nm, em, seen in rows:
                item = {
                    "candidate_id": int(cid) if cid is not None else None,
                    "name": nm or "",
                    "email": em or "",
                    "last_seen": _fmt_date(seen),
                }
                same_name = bool(this_name) and fsig.normalize_name(nm) == this_name
                same_email = bool(this_email) and (em or "").strip().lower() == this_email
                if same_name and same_email:
                    dupes.append(item)
                else:
                    genuine.append(item)
            return {"genuine": genuine, "duplicates": dupes}

        try:
            with Session(db.engine) as session:
                dialect = session.bind.dialect.name if session.bind else ""
                if dialect == "postgresql":
                    # Efficient server-side hashing on the live DB. DISTINCT ON
                    # collapses to the MOST-RECENT row per other candidate id so
                    # name/email/date are read from ONE coherent record (a plain
                    # GROUP BY + MAX() could mix fields across rows and misclassify
                    # a duplicate as genuine).
                    rows = session.execute(
                        text(
                            "SELECT DISTINCT ON (bullhorn_candidate_id) "
                            "bullhorn_candidate_id, "
                            "candidate_name AS name, "
                            "candidate_email AS email, "
                            "created_at AS last_seen "
                            "FROM candidate_vetting_log "
                            "WHERE resume_text IS NOT NULL "
                            "AND md5(resume_text) = md5(:rt) "
                            "AND bullhorn_candidate_id IS NOT NULL "
                            "AND (:cid IS NULL OR bullhorn_candidate_id <> :cid) "
                            "AND is_sandbox = false "
                            "ORDER BY bullhorn_candidate_id, created_at DESC NULLS LAST"
                        ),
                        {"rt": resume_text, "cid": candidate_id},
                    ).all()
                    return _classify(rows)

                # Dialect-agnostic fallback (e.g. SQLite in tests): hash in Python.
                target_hash = hashlib.md5(resume_text.encode("utf-8")).hexdigest()
                all_rows = (
                    session.query(
                        CandidateVettingLog.bullhorn_candidate_id,
                        CandidateVettingLog.candidate_name,
                        CandidateVettingLog.candidate_email,
                        CandidateVettingLog.created_at,
                        CandidateVettingLog.resume_text,
                    )
                    .filter(CandidateVettingLog.resume_text.isnot(None))
                    .filter(CandidateVettingLog.bullhorn_candidate_id.isnot(None))
                    .filter(CandidateVettingLog.is_sandbox.is_(False))
                    .all()
                )
                # Collapse to one (most-recent) row per other candidate id.
                seen_best = {}
                for cid, nm, em, created, rt in all_rows:
                    if candidate_id is not None and cid == candidate_id:
                        continue
                    if not rt or hashlib.md5(rt.encode("utf-8")).hexdigest() != target_hash:
                        continue
                    prev = seen_best.get(cid)
                    if prev is None or (
                        created is not None and (prev[2] is None or created > prev[2])
                    ):
                        seen_best[cid] = (nm, em, created)
                rows = [(cid, v[0], v[1], v[2]) for cid, v in seen_best.items()]
                return _classify(rows)
        except Exception as exc:  # pragma: no cover - DB dialect/edge
            logger.debug("resume-reuse query failed: %s", exc)
            return empty

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

    # ------------------------------------------ new Phase A/B/C gatherers
    def _gather_submission_drift(
        self, candidate_id, email, phone, name, linkedin_url, resume_text,
        vetting_log_id,
    ):
        """Compare current identity claims to prior vetting logs (180d)."""
        changes = []
        try:
            cutoff = datetime.utcnow() - timedelta(days=180)
            phone_norm = fsig.normalize_phone(phone)
            with Session(db.engine) as session:
                q = (
                    session.query(CandidateVettingLog)
                    .filter(CandidateVettingLog.is_sandbox.is_(False))
                    .filter(CandidateVettingLog.created_at >= cutoff)
                )
                if vetting_log_id:
                    q = q.filter(CandidateVettingLog.id != vetting_log_id)
                clauses = []
                if candidate_id:
                    clauses.append(
                        CandidateVettingLog.bullhorn_candidate_id == candidate_id
                    )
                if email:
                    clauses.append(
                        func.lower(CandidateVettingLog.candidate_email) == email.lower()
                    )
                if phone_norm and len(phone_norm) >= 10:
                    clauses.append(
                        CandidateVettingLog.candidate_phone == phone_norm
                    )
                if not clauses:
                    return []
                from sqlalchemy import or_
                priors = (
                    q.filter(or_(*clauses))
                    .order_by(CandidateVettingLog.created_at.desc())
                    .limit(10)
                    .all()
                )
            if not priors:
                return []

            cur_years = fsig.extract_max_years_claim(resume_text)
            cur_span = fsig.extract_year_span(resume_text)
            cur_name = fsig.normalize_name(name)
            cur_li = (linkedin_url or "").strip().lower()

            for prior in priors:
                prior_date = ""
                if prior.created_at:
                    try:
                        prior_date = prior.created_at.date().isoformat()
                    except Exception:
                        prior_date = str(prior.created_at)[:10]
                prior_name = fsig.normalize_name(prior.candidate_name)
                if cur_name and prior_name and cur_name != prior_name:
                    # Same contact, different display name — soft drift
                    # (stronger identity reuse is a separate signal).
                    if email or (phone_norm and len(phone_norm) >= 10):
                        changes.append({
                            "kind": "name_changed",
                            "summary": (
                                f"Display name changed since {prior_date or 'prior apply'} "
                                f"('{prior.candidate_name}' → '{name}')"
                            ),
                            "prior_date": prior_date,
                        })
                prior_li = (prior.candidate_linkedin_url or "").strip().lower()
                if cur_li and prior_li and cur_li != prior_li:
                    changes.append({
                        "kind": "linkedin_changed",
                        "summary": (
                            f"LinkedIn URL changed since {prior_date or 'prior apply'}"
                        ),
                        "prior_date": prior_date,
                    })
                prior_years = fsig.extract_max_years_claim(prior.resume_text)
                prior_span = fsig.extract_year_span(prior.resume_text)
                # Inflation: current claim ≥ prior + 3 years
                if (
                    cur_years is not None and prior_years is not None
                    and cur_years >= prior_years + 3
                ):
                    changes.append({
                        "kind": "years_inflation",
                        "summary": (
                            f"Claimed years rose from ~{prior_years} to ~{cur_years} "
                            f"since {prior_date or 'prior apply'}"
                        ),
                        "prior_date": prior_date,
                    })
                elif (
                    cur_span is not None and prior_span is not None
                    and cur_span >= prior_span + 4
                ):
                    changes.append({
                        "kind": "years_inflation",
                        "summary": (
                            f"Résumé year-span rose from ~{prior_span}y to ~{cur_span}y "
                            f"since {prior_date or 'prior apply'}"
                        ),
                        "prior_date": prior_date,
                    })
            # Deduplicate by kind (keep first / most recent prior)
            seen_kinds = set()
            deduped = []
            for ch in changes:
                k = ch.get("kind")
                if k in seen_kinds:
                    continue
                seen_kinds.add(k)
                deduped.append(ch)
            return deduped
        except Exception as exc:
            logger.debug("submission-drift gather failed: %s", exc)
            return []

    def _gather_resume_file_versions(self, candidate_id) -> List[Dict[str, Any]]:
        """Download newest Resume-typed Bullhorn files for divergence check.

        Caps at ``RESUME_VERSION_MAX_FILES`` (newest first). Uses cheap local
        text extraction only (no OCR / AI). Fail-soft: returns [] on any
        Bullhorn or parse failure so screening is never blocked.
        """
        if not candidate_id or self.bullhorn_service is None:
            return []
        try:
            from screening.candidate_data import list_resume_labeled_files
            files = self.bullhorn_service.get_entity_files(
                "Candidate", int(candidate_id),
            )
            labeled = list_resume_labeled_files(files)[:fsig.RESUME_VERSION_MAX_FILES]
            if len(labeled) < 2:
                # Need ≥2 files to possibly diverge; skip downloads.
                return []

            versions: List[Dict[str, Any]] = []
            for file_info in labeled:
                file_id = file_info.get("id")
                name = str(file_info.get("name") or f"resume_{file_id}")
                if not file_id:
                    continue
                content = self._download_candidate_file_bytes(
                    int(candidate_id), int(file_id),
                )
                if not content:
                    continue
                text = self._extract_resume_text_cheap(content, name)
                if not text:
                    continue
                versions.append({
                    "name": name[:200],
                    "text": text,
                    "file_id": int(file_id),
                })
            return versions
        except Exception as exc:
            logger.debug(
                "divergent-resume gather failed for candidate %s: %s",
                candidate_id, exc,
            )
            return []

    def _download_candidate_file_bytes(
        self, candidate_id: int, file_id: int,
    ) -> Optional[bytes]:
        """Fetch raw file bytes from Bullhorn (JSON envelope unwrapped)."""
        service = self.bullhorn_service
        if service is None or not getattr(service, "base_url", None):
            return None
        try:
            if not getattr(service, "rest_token", None):
                authenticate = getattr(service, "authenticate", None)
                if callable(authenticate) and not authenticate():
                    return None
            url = f"{service.base_url}file/Candidate/{candidate_id}/{file_id}"
            params = {"BhRestToken": service.rest_token}
            session = getattr(service, "session", None)
            if session is None:
                return None
            response = session.get(url, params=params, timeout=45)
            if response.status_code == 401:
                authenticate = getattr(service, "authenticate", None)
                if callable(authenticate) and authenticate():
                    params["BhRestToken"] = service.rest_token
                    response = session.get(url, params=params, timeout=45)
                else:
                    return None
            if response.status_code != 200 or not response.content:
                return None
            content = response.content
            if content.lstrip()[:1] == b"{" and b'"File"' in content[:200]:
                try:
                    data = response.json()
                    b64 = (data.get("File") or {}).get("fileContent") or ""
                    if not b64:
                        return None
                    content = base64.b64decode(b64)
                except Exception:
                    return None
            return content
        except Exception as exc:
            logger.debug(
                "divergent-resume file download failed %s/%s: %s",
                candidate_id, file_id, exc,
            )
            return None

    @staticmethod
    def _extract_resume_text_cheap(
        content: bytes, filename: str,
    ) -> Optional[str]:
        """Local PDF/DOCX/DOC/TXT extract only — no OCR / vision (keeps $0)."""
        if not content:
            return None
        try:
            from vetting.resume_utils import (
                extract_text_from_pdf,
                extract_text_from_docx,
                extract_text_from_doc,
                _detect_file_format,
            )
            from utils.text_sanitization import sanitize_text
            name = (filename or "").lower()
            fmt = _detect_file_format(content)
            text = None
            if name.endswith(".pdf") or fmt == "pdf":
                text = extract_text_from_pdf(content)
            elif name.endswith(".docx") or fmt == "docx":
                text = extract_text_from_docx(content)
                if (not text or len(text.strip()) < 10) and fmt == "doc":
                    text = extract_text_from_doc(content)
            elif name.endswith(".doc") or fmt == "doc":
                text = extract_text_from_doc(content)
            elif name.endswith(".txt") or name.endswith(".rtf"):
                for enc in ("utf-8", "latin-1"):
                    try:
                        text = content.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
            if text and len(text.strip()) >= 40:
                return sanitize_text(text)
            return None
        except Exception:
            return None

    def _gather_pdf_signals(
        self, pdf_metadata, candidate_id, name, resume_text, vetting_log_id,
    ):
        """Persist fingerprint + evaluate author-signature reuse."""
        try:
            from fraud_detection.pdf_meta import (
                pdf_signature, pdf_mod_is_recent, content_md5,
            )
            meta = {
                str(k).lower(): str(v)[:200]
                for k, v in (pdf_metadata or {}).items()
                if v
            }
            sig = pdf_signature(meta)
            if not sig:
                return []

            md5 = content_md5(resume_text)
            recent = pdf_mod_is_recent(meta)
            others = 0
            with Session(db.engine, expire_on_commit=False) as session:
                # Count other candidates with same signature + different name
                rows = (
                    session.query(
                        ResumeDocumentFingerprint.bullhorn_candidate_id,
                        ResumeDocumentFingerprint.candidate_name,
                    )
                    .filter(ResumeDocumentFingerprint.signature == sig)
                    .filter(ResumeDocumentFingerprint.bullhorn_candidate_id.isnot(None))
                    .limit(200)
                    .all()
                )
                cur_name = fsig.normalize_name(name)
                other_ids = set()
                for cid, nm in rows:
                    if candidate_id is not None and cid == candidate_id:
                        continue
                    if cur_name and fsig.normalize_name(nm) == cur_name:
                        continue
                    other_ids.add(cid)
                others = len(other_ids)

                session.add(ResumeDocumentFingerprint(
                    signature=sig,
                    author=(meta.get("author") or None),
                    creator=(meta.get("creator") or None),
                    producer=(meta.get("producer") or None),
                    mod_date=(meta.get("moddate") or None),
                    content_md5=md5 or None,
                    bullhorn_candidate_id=candidate_id,
                    candidate_name=(name or None) and name[:200],
                    vetting_log_id=vetting_log_id,
                ))
                session.commit()

            return fsig.evaluate_pdf_author_reuse(
                signature=sig,
                other_identities=others,
                recent_mod=recent and others >= 1,
            )
        except Exception as exc:
            logger.debug("pdf forensics gather failed: %s", exc)
            return []

    def _gather_contact_validation(self, email, phone):
        signals: List[Optional[fsig.FraudSignal]] = []
        try:
            from fraud_detection.contact_validation import run_contact_validation
            email_res, phone_res = run_contact_validation(email, phone)
            if email_res:
                signals.append(fsig.evaluate_email_undeliverable(
                    email, email_res.get("result"),
                ))
            if phone_res:
                signals.extend(fsig.evaluate_phone_validation(
                    phone,
                    valid=phone_res.get("valid"),
                    line_type=phone_res.get("line_type"),
                ))
        except Exception as exc:
            logger.debug("contact validation gather failed: %s", exc)
        return [s for s in signals if s is not None]

    def _gather_linkedin_crosscheck(self, linkedin_url, resume_name):
        try:
            from fraud_detection.linkedin_crosscheck import check_linkedin_profile
            result = check_linkedin_profile(linkedin_url)
            return fsig.evaluate_linkedin_url_status(
                linkedin_url=result.get("url") or linkedin_url,
                status=result.get("status"),
                profile_name=result.get("profile_name"),
                resume_name=resume_name,
            )
        except Exception as exc:
            logger.debug("linkedin crosscheck gather failed: %s", exc)
            return []

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

        questions = fsig.suggested_questions_for_signals(result.signals, limit=3)
        if questions and band in (
            fsig.FraudRiskBand.HIGH_RISK, fsig.FraudRiskBand.REVIEW,
        ):
            lines.append("")
            lines.append("Suggested verification questions:")
            for q in questions:
                lines.append(f"  • {q}")

        lines.append("")
        lines.append(
            "This is an advisory flag for recruiter judgement only; it does not "
            "block screening or submission. Please verify the candidate's "
            "details before proceeding."
        )
        return "\n".join(lines)
