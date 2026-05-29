"""Pure, dependency-free fraud-signal evaluators.

This module is intentionally free of Flask / SQLAlchemy / OpenAI imports so it
can be unit-tested in isolation (mirrors the `placement_margin.calculator`
pattern). The engine layer (`fraud_detection.engine`) gathers facts from the DB
and Bullhorn, then feeds them into these evaluators.

Each evaluator returns either a `FraudSignal` (or list thereof) describing a
detected risk indicator with a point weight + human-readable evidence, or
``None`` / ``[]`` when nothing is detected. `aggregate()` sums the weights into
a 0-100 risk score and assigns a band using configurable thresholds.

All signals here are DETERMINISTIC — zero OpenAI cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


class FraudRiskBand(str, Enum):
    """Risk classification bands. String-valued for clean DB persistence."""
    CLEAR = "clear"
    REVIEW = "review"
    HIGH_RISK = "high_risk"


# --- Point weights per signal (tunable constants, not user-facing) ----------
POINTS_DISPOSABLE_EMAIL = 25
POINTS_NAME_ANOMALY = 20
POINTS_PHONE_ANOMALY = 15
POINTS_EMAIL_SYNTAX = 15
POINTS_WORK_FUTURE_DATE = 25
POINTS_WORK_NEGATIVE_DURATION = 25
POINTS_WORK_OVERLAP = 20
POINTS_WORK_IMPLAUSIBLE_TENURE = 20
POINTS_RESUME_REUSE = 40
POINTS_IDENTITY_REUSE_PHONE = 35
POINTS_IDENTITY_REUSE_EMAIL = 35
POINTS_PROFILE_NEAR_DUPLICATE = 25
POINTS_VELOCITY = 15

# Default banding thresholds (overridable via VettingConfig).
DEFAULT_REVIEW_THRESHOLD = 40
DEFAULT_HIGH_RISK_THRESHOLD = 75

# Sane detection defaults (the engine may pass overrides).
DEFAULT_IDENTITY_REUSE_MIN_NAMES = 3        # distinct names sharing one contact
DEFAULT_VELOCITY_MIN_APPLICATIONS = 8       # applications within the window
DEFAULT_VELOCITY_WINDOW_HOURS = 24
DEFAULT_NEAR_DUP_SIMILARITY = 0.92          # cosine sim for "basically identical"
MAX_PLAUSIBLE_SINGLE_TENURE_YEARS = 55


@dataclass
class FraudSignal:
    """A single detected fraud indicator."""
    code: str
    label: str
    points: int
    evidence: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "points": self.points,
            "evidence": self.evidence,
            "details": self.details,
        }


@dataclass
class FraudAssessmentResult:
    """Aggregated outcome of all signals for one candidate."""
    risk_score: int
    risk_band: FraudRiskBand
    signals: List[FraudSignal] = field(default_factory=list)

    def signals_payload(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.signals]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"(https?://|www\.|\.com\b|\.net\b|\.ru\b|<a\s|</a>)", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(phone: Optional[str]) -> str:
    """Strip everything but digits (mirrors duplicate_merge_service)."""
    if not phone:
        return ""
    return re.sub(r"[^0-9]", "", str(phone))


def normalize_name(name: Optional[str]) -> str:
    """Lowercase, strip non-alpha (mirrors duplicate_merge_service)."""
    if not name:
        return ""
    return re.sub(r"[^a-z]", "", str(name).lower())


def _parse_date(value: Any) -> Optional[date]:
    """Best-effort parse of a work-history date into a `date`.

    Handles `date`/`datetime`, 4-digit years, ``YYYY-MM``, ``YYYY-MM-DD``,
    ``MM/YYYY``, and ``MM/DD/YYYY``. Returns ``None`` for present/current/empty
    or anything unparseable (caller treats None as "open-ended / unknown").
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip().lower()
    if not s or s in ("present", "current", "now", "ongoing", "n/a"):
        return None
    if isinstance(value, (int, float)) or re.fullmatch(r"\d{4}", s):
        try:
            year = int(float(s))
            if 1900 <= year <= 2100:
                return date(year, 1, 1)
        except (ValueError, OverflowError):
            return None
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%m/%d/%Y", "%m/%Y", "%Y/%m/%d", "%b %Y", "%B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Pure evaluators
# ---------------------------------------------------------------------------

def evaluate_disposable_email(email: Optional[str]) -> Optional[FraudSignal]:
    """Flag disposable/temporary email domains."""
    from fraud_detection.disposable_domains import extract_domain, is_disposable_domain
    if not email:
        return None
    if is_disposable_domain(email):
        return FraudSignal(
            code="disposable_email",
            label="Disposable email domain",
            points=POINTS_DISPOSABLE_EMAIL,
            evidence=f"Domain '{extract_domain(email)}' is a known disposable provider.",
            details={"domain": extract_domain(email)},
        )
    return None


def evaluate_contact_anomalies(
    name: Optional[str],
    email: Optional[str],
    phone: Optional[str],
) -> List[FraudSignal]:
    """Flag malformed/suspicious contact fields (URLs in name, junk phone, bad email)."""
    signals: List[FraudSignal] = []

    if name and (_URL_RE.search(name) or len(_DIGIT_RE.findall(name)) >= 3):
        signals.append(FraudSignal(
            code="name_anomaly",
            label="Suspicious name field",
            points=POINTS_NAME_ANOMALY,
            evidence="Name field contains a URL/markup or multiple digits.",
            details={"name": name[:120]},
        ))

    if email and not _EMAIL_RE.match(email.strip()):
        signals.append(FraudSignal(
            code="email_syntax",
            label="Malformed email address",
            points=POINTS_EMAIL_SYNTAX,
            evidence="Email address does not match a valid address pattern.",
            details={"email": email[:120]},
        ))

    digits = normalize_phone(phone)
    if digits:
        bare = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
        placeholder = (
            len(set(bare)) <= 1                      # all same digit
            or bare in ("1234567890", "0123456789")  # sequential
            or len(bare) < 7                          # too short to be real
        )
        if placeholder:
            signals.append(FraudSignal(
                code="phone_anomaly",
                label="Placeholder/invalid phone",
                points=POINTS_PHONE_ANOMALY,
                evidence="Phone number is a placeholder, sequential, or too short.",
                details={"phone_digits": digits},
            ))

    return signals


def evaluate_work_history(work_history: Optional[Sequence[Dict[str, Any]]]) -> List[FraudSignal]:
    """Flag impossible employment timelines.

    Each entry is expected to be a dict with ``start``/``end`` (any of the
    formats `_parse_date` understands) and optionally ``title``. Open-ended
    end dates (present/current) are treated as ongoing through today.
    """
    signals: List[FraudSignal] = []
    if not work_history:
        return signals

    today = date.today()
    intervals: List[tuple] = []  # (start, end, title)
    future_hits: List[str] = []
    negative_hits: List[str] = []
    implausible_hits: List[str] = []

    for entry in work_history:
        if not isinstance(entry, dict):
            continue
        start = _parse_date(entry.get("start") or entry.get("start_date") or entry.get("from"))
        end_raw = entry.get("end") or entry.get("end_date") or entry.get("to")
        end = _parse_date(end_raw)
        title = str(entry.get("title") or entry.get("role") or entry.get("company") or "role")

        if start and start > today:
            future_hits.append(f"{title} starts {start.isoformat()}")
        if end and end > today:
            future_hits.append(f"{title} ends {end.isoformat()}")

        if start and end and end < start:
            negative_hits.append(f"{title} ({start.isoformat()} → {end.isoformat()})")

        if start:
            eff_end = end or today
            if eff_end >= start:
                years = (eff_end - start).days / 365.25
                if years > MAX_PLAUSIBLE_SINGLE_TENURE_YEARS:
                    implausible_hits.append(f"{title} ~{years:.0f}y")
                intervals.append((start, eff_end, title))

    # Overlapping full-time roles: count pairwise overlaps with >180-day overlap.
    overlap_pairs: List[str] = []
    sorted_iv = sorted(intervals, key=lambda x: x[0])
    for i in range(len(sorted_iv)):
        for j in range(i + 1, len(sorted_iv)):
            a_start, a_end, a_title = sorted_iv[i]
            b_start, b_end, b_title = sorted_iv[j]
            if b_start >= a_end:
                continue
            overlap_days = (min(a_end, b_end) - b_start).days
            if overlap_days > 180:
                overlap_pairs.append(f"{a_title} ∥ {b_title} ({overlap_days}d)")

    if future_hits:
        signals.append(FraudSignal(
            code="work_future_date",
            label="Future-dated employment",
            points=POINTS_WORK_FUTURE_DATE,
            evidence="; ".join(future_hits[:3]),
            details={"hits": future_hits[:10]},
        ))
    if negative_hits:
        signals.append(FraudSignal(
            code="work_negative_duration",
            label="Negative employment duration",
            points=POINTS_WORK_NEGATIVE_DURATION,
            evidence="; ".join(negative_hits[:3]),
            details={"hits": negative_hits[:10]},
        ))
    if overlap_pairs:
        signals.append(FraudSignal(
            code="work_overlap",
            label="Overlapping full-time roles",
            points=POINTS_WORK_OVERLAP,
            evidence="; ".join(overlap_pairs[:3]),
            details={"pairs": overlap_pairs[:10]},
        ))
    if implausible_hits:
        signals.append(FraudSignal(
            code="work_implausible_tenure",
            label="Implausible single tenure",
            points=POINTS_WORK_IMPLAUSIBLE_TENURE,
            evidence="; ".join(implausible_hits[:3]),
            details={"hits": implausible_hits[:10]},
        ))

    return signals


def evaluate_resume_reuse(distinct_other_identities: int) -> Optional[FraudSignal]:
    """Flag when the same resume content hash is tied to other identities.

    ``distinct_other_identities`` is the count of OTHER candidate identities
    (different name/email/phone) sharing this candidate's resume content hash.
    """
    if distinct_other_identities and distinct_other_identities >= 1:
        return FraudSignal(
            code="resume_reuse",
            label="Resume reused across identities",
            points=POINTS_RESUME_REUSE,
            evidence=f"Identical resume content is linked to {distinct_other_identities} other identity(ies).",
            details={"other_identities": int(distinct_other_identities)},
        )
    return None


def evaluate_identity_reuse(
    distinct_names_for_phone: int = 0,
    distinct_names_for_email: int = 0,
    min_names: int = DEFAULT_IDENTITY_REUSE_MIN_NAMES,
) -> List[FraudSignal]:
    """Flag one phone/email shared across many distinct candidate names."""
    signals: List[FraudSignal] = []
    if distinct_names_for_phone and distinct_names_for_phone >= min_names:
        signals.append(FraudSignal(
            code="identity_reuse_phone",
            label="Phone reused across identities",
            points=POINTS_IDENTITY_REUSE_PHONE,
            evidence=f"One phone number maps to {distinct_names_for_phone} distinct names.",
            details={"distinct_names": int(distinct_names_for_phone)},
        ))
    if distinct_names_for_email and distinct_names_for_email >= min_names:
        signals.append(FraudSignal(
            code="identity_reuse_email",
            label="Email reused across identities",
            points=POINTS_IDENTITY_REUSE_EMAIL,
            evidence=f"One email address maps to {distinct_names_for_email} distinct names.",
            details={"distinct_names": int(distinct_names_for_email)},
        ))
    return signals


def evaluate_profile_near_duplicate(
    top_similarity: Optional[float],
    identity_differs: bool,
    threshold: float = DEFAULT_NEAR_DUP_SIMILARITY,
) -> Optional[FraudSignal]:
    """Flag a near-identical profile embedding tied to a different identity."""
    if top_similarity is None or not identity_differs:
        return None
    if top_similarity >= threshold:
        return FraudSignal(
            code="profile_near_duplicate",
            label="Near-identical profile, different identity",
            points=POINTS_PROFILE_NEAR_DUPLICATE,
            evidence=f"Profile is {top_similarity:.2f} cosine-similar to a different identity.",
            details={"similarity": round(float(top_similarity), 4)},
        )
    return None


def evaluate_velocity(
    application_count: int,
    window_hours: int = DEFAULT_VELOCITY_WINDOW_HOURS,
    min_applications: int = DEFAULT_VELOCITY_MIN_APPLICATIONS,
) -> Optional[FraudSignal]:
    """Flag burst application activity from the same identity."""
    if application_count and application_count >= min_applications:
        return FraudSignal(
            code="application_velocity",
            label="High application velocity",
            points=POINTS_VELOCITY,
            evidence=f"{application_count} applications within {window_hours}h.",
            details={"count": int(application_count), "window_hours": int(window_hours)},
        )
    return None


def aggregate(
    signals: Sequence[Optional[FraudSignal]],
    review_threshold: int = DEFAULT_REVIEW_THRESHOLD,
    high_risk_threshold: int = DEFAULT_HIGH_RISK_THRESHOLD,
) -> FraudAssessmentResult:
    """Sum signal weights into a capped 0-100 score and assign a band."""
    clean = [s for s in signals if s is not None]
    score = min(100, sum(s.points for s in clean))

    if score >= high_risk_threshold:
        band = FraudRiskBand.HIGH_RISK
    elif score >= review_threshold:
        band = FraudRiskBand.REVIEW
    else:
        band = FraudRiskBand.CLEAR

    return FraudAssessmentResult(risk_score=score, risk_band=band, signals=clean)
