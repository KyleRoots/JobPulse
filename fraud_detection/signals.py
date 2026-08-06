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

import hashlib
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
# LinkedIn profile URL reused across distinct candidate identities — strong, on
# par with phone/email identity reuse (a profile URL is hard to share by accident).
POINTS_LINKEDIN_REUSE = 35
# Truncated/incomplete name on its own — a small nudge, never enough to band by
# itself (a real person can have a short name). The weight lives in the composite.
POINTS_NAME_INCOMPLETE = 8
# "Third-party submission" composite: incomplete name + non-personal email. This
# is the agency-submitted-shell-profile pattern. Base + foreign-location amplifier
# tops out in the Review band (compliance flag), never High-Risk on its own.
POINTS_THIRD_PARTY_BASE = 32
POINTS_THIRD_PARTY_FOREIGN = 10
# Verbatim JD-mirror: graduated by the longest contiguous verbatim word-run lifted
# from the job description into the resume. NOT keyword overlap (which would punish
# genuinely qualified candidates) — only long, exact, copy-pasted passages.
POINTS_JD_MIRROR_LIGHT = 22
POINTS_JD_MIRROR_MODERATE = 40
POINTS_JD_MIRROR_HEAVY = 55
# AI-style writing markers are INFORMATIONAL ONLY (0 points): surfaced for context,
# never scored, never an accusation. Detectors are unreliable on resume/bullet text.
POINTS_AI_STYLE_MARKERS = 0
# Multi-submission drift: same contact / candidate ID, changing claims over time.
POINTS_SUBMISSION_DRIFT_LIGHT = 22
POINTS_SUBMISSION_DRIFT_MODERATE = 40
# Divergent Resume-typed files on the same Bullhorn candidate (soft Review advisory).
# Alone hits Review (≥40); not enough for High-Risk. Distinct from submission_drift
# (claim changes across applies) — this is file-content diversity on one profile.
POINTS_DIVERGENT_RESUME_VERSIONS = 40
# PDF Author/Producer signature reused across different candidate names.
POINTS_PDF_AUTHOR_REUSE = 35
# Soft amplifier when ModDate is very recent AND author reuse already fired.
POINTS_PDF_RECENT_RESKIN = 12
# Third-party contact validation (NeverBounce / Twilio Lookup).
POINTS_EMAIL_UNDELIVERABLE = 30
POINTS_PHONE_INVALID = 25
POINTS_PHONE_VOIP = 10
# LinkedIn soft cross-check (URL-only). Cap so these alone never reach High-Risk.
POINTS_LINKEDIN_URL_DEAD = 42
POINTS_LINKEDIN_NAME_MISMATCH = 25
LINKEDIN_SOFT_CODES = frozenset({"linkedin_url_dead", "linkedin_name_mismatch"})

# Divergent-resume tuning (word-token Jaccard on extracted text).
# Near-identical merge duplicates collapse first (same content saved twice).
RESUME_VERSION_NEAR_DUP_JACCARD = 0.90
# Clear divergence: min pairwise Jaccard among unique versions below this fires.
# Tuned so light tailoring (same career, different emphasis) stays quiet, while
# unrelated careers (e.g. ML vs marketing/CRM) on one candidate flag Review.
RESUME_VERSION_DIVERGENCE_JACCARD = 0.40
RESUME_VERSION_MIN_TOKENS = 40
RESUME_VERSION_MAX_FILES = 5
_RESUME_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)

# JD-mirror tuning: minimum contiguous word-run length to count as a verbatim lift
# (8 identical consecutive words is strongly copy-paste, not coincidental overlap),
# the run lengths that escalate the band, and the minimum JD size worth checking.
JD_MIRROR_MIN_RUN_WORDS = 8
JD_MIRROR_MODERATE_RUN_WORDS = 18
JD_MIRROR_HEAVY_RUN_WORDS = 30
JD_MIRROR_MIN_JD_WORDS = 40
# Words of surrounding context to capture on each side of a copied passage so a
# recruiter can locate it in the document, and a cap on the copied passage text
# itself so a very long lift doesn't produce an unwieldy note/email block.
JD_MIRROR_CONTEXT_WORDS = 8
JD_MIRROR_MAX_PASSAGE_CHARS = 400

# AI-style marker tuning: require a few em dashes (Word auto-converts the odd one)
# before surfacing the informational note, to keep it conservative.
AI_STYLE_MIN_EM_DASHES = 3

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


def is_personal_email(email: Optional[str]) -> bool:
    """True when the address is a known free / personal webmail provider.

    Thin re-export of `email_providers.is_personal_email` so callers (and tests)
    have a single import surface. Lazy import keeps this module dependency-free.
    """
    if not email:
        return False
    from fraud_detection.email_providers import is_personal_email as _ipe
    return _ipe(email)


_LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9\-_%]+)",
    re.IGNORECASE,
)


def extract_linkedin_url(*sources: Optional[str]) -> str:
    """Return a canonical ``linkedin.com/in/<slug>`` URL from any source text.

    Scans each source (resume text, a stored profile field, etc.) in order and
    returns the first ``/in/`` profile it finds, normalized to a stable
    comparison key: lowercased, scheme/subdomain/query stripped, trailing slash
    removed. Returns '' when none is found. This is the *capture + format
    validation* step — only well-formed ``/in/`` profiles are captured, so the
    downstream reuse check never sees garbage.
    """
    for src in sources:
        if not src:
            continue
        m = _LINKEDIN_RE.search(str(src))
        if m:
            slug = m.group(1).rstrip("/").lower()
            if slug:
                return f"linkedin.com/in/{slug}"
    return ""


# A "name part" that is really just an initial: a single letter, optionally with
# a trailing period (e.g. "J", "J.", "B").
_INITIAL_RE = re.compile(r"^[A-Za-z]\.?$")


def is_incomplete_name(first: Optional[str], last: Optional[str]) -> bool:
    """True when the candidate's name is truncated to first-only or first+initial.

    Fires when a real first name is present but the surname is missing entirely
    (first-only) or is just a single initial. This is a weak, benign-on-its-own
    pattern (some people genuinely have short names) — its value is as one half
    of the third-party-submission composite. Requires the first name to look
    like a real word (≥2 letters) so single-token junk doesn't over-fire.
    """
    f = (first or "").strip()
    l = (last or "").strip()
    if len(re.sub(r"[^A-Za-z]", "", f)) < 2:
        return False
    if not l:
        return True
    return bool(_INITIAL_RE.match(l))


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


def _format_identities(items: Sequence[Dict[str, Any]], limit: int = 3) -> str:
    """Render a short, human-readable list of other identities for evidence text.

    Each item is a dict with optional ``name``, ``email`` and ``last_seen``.
    Caps the inline list (``limit``) so a note/email never balloons.
    """
    parts: List[str] = []
    for it in list(items)[:limit]:
        name = (str(it.get("name") or "").strip() or "Unknown name")
        email = str(it.get("email") or "").strip()
        when = str(it.get("last_seen") or "").strip()
        seg = name
        if email:
            seg += f" ({email})"
        if when:
            seg += f", applied {when}"
        parts.append(seg)
    rendered = "; ".join(parts)
    extra = len(items) - limit
    if extra > 0:
        rendered += f"; and {extra} more"
    return rendered


def evaluate_resume_reuse(
    genuine_identities: Optional[Sequence[Dict[str, Any]]] = None,
    duplicate_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[FraudSignal]:
    """Résumé-content reuse signals (returns 0, 1 or 2 signals).

    ``genuine_identities`` — OTHER candidate records sharing this résumé whose
    name AND email BOTH differ from this candidate (a genuinely different person
    using the same résumé). These SCORE ``POINTS_RESUME_REUSE`` and carry the
    specific name/email/date as evidence so a recruiter can verify the claim
    instead of reading a bare count.

    ``duplicate_records`` — OTHER candidate records sharing this résumé that have
    the SAME name and email (the same person entered twice, e.g. a duplicate
    Bullhorn record). These are INFORMATIONAL ONLY (0 points) — surfaced as a
    "consider merging" note, never scored as fraud — so a benign duplicate record
    no longer trips a Review-band fraud flag.
    """
    genuine = [g for g in (genuine_identities or []) if g]
    dupes = [d for d in (duplicate_records or []) if d]
    out: List[FraudSignal] = []

    if genuine:
        n = len(genuine)
        out.append(FraudSignal(
            code="resume_reuse",
            label="Resume reused across identities",
            points=POINTS_RESUME_REUSE,
            evidence=(
                f"Identical résumé content was also submitted under "
                f"{n} different identity(ies): {_format_identities(genuine)}."
            ),
            details={"other_identities": n, "identities": list(genuine)},
        ))

    if dupes:
        n = len(dupes)
        out.append(FraudSignal(
            code="resume_duplicate_record",
            label="Résumé also on a duplicate candidate record",
            points=0,  # informational only — same person, not a fraud indicator
            evidence=(
                f"Identical résumé content also appears on {n} other candidate "
                f"record(s) for the same person ({_format_identities(dupes)}) — "
                f"likely a duplicate to merge, not a fraud indicator."
            ),
            details={"duplicate_records": n, "identities": list(dupes)},
        ))

    return out


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


def evaluate_linkedin(
    linkedin_url: Optional[str],
    distinct_other_identities: int,
) -> Optional[FraudSignal]:
    """Flag a LinkedIn profile URL claimed across multiple candidate identities.

    ``distinct_other_identities`` is the count of OTHER candidate records that
    present the same canonical profile URL. One profile URL is hard to share by
    accident, so reuse across identities is a strong signal — on par with phone
    or email reuse. A profile present on a single identity is the normal case and
    produces no signal.
    """
    if not linkedin_url or distinct_other_identities < 1:
        return None
    others = int(distinct_other_identities)
    return FraudSignal(
        code="linkedin_reuse",
        label="LinkedIn profile reused across identities",
        points=POINTS_LINKEDIN_REUSE,
        evidence=(
            f"Same LinkedIn profile appears on {others} other candidate "
            f"{'identity' if others == 1 else 'identities'}"
        ),
        details={"linkedin_url": linkedin_url, "other_identities": others},
    )


def evaluate_name_completeness(
    first: Optional[str], last: Optional[str],
) -> Optional[FraudSignal]:
    """Small nudge for a truncated name (first-only or first + single initial).

    Benign on its own — never enough to band a candidate. The real weight comes
    from the third-party-submission composite. Returns None for complete names.
    """
    if not is_incomplete_name(first, last):
        return None
    f = (first or "").strip()
    l = (last or "").strip()
    shown = f"{f} {l}".strip()
    return FraudSignal(
        code="name_incomplete",
        label="Incomplete candidate name",
        points=POINTS_NAME_INCOMPLETE,
        evidence=(f"Name captured as '{shown}'" if shown else "Surname missing")
                 + (" (surname is a single initial)" if l else " (no surname)"),
        details={"first": f, "last": l},
    )


def evaluate_third_party_submission(
    name_incomplete: bool,
    email_personal: bool,
    foreign_location: bool = False,
) -> Optional[FraudSignal]:
    """Composite flag for the agency-submitted-shell-profile pattern.

    Fires only when a truncated name is paired with a NON-personal email domain
    (corporate / agency / custom) — the classic third-party submission shape.
    A foreign-location mismatch is a soft amplifier (NOT a standalone trigger;
    cross-country relocation is already handled by screening's Location Review
    tier). Tops out in the Review band — this is a compliance/verification nudge,
    never a High-Risk fraud accusation on its own.
    """
    if not (name_incomplete and not email_personal):
        return None
    points = POINTS_THIRD_PARTY_BASE
    reasons = ["truncated name with a non-personal email domain"]
    if foreign_location:
        points += POINTS_THIRD_PARTY_FOREIGN
        reasons.append("candidate location differs from the role's country")
    return FraudSignal(
        code="third_party_submission",
        label="Possible third-party submission",
        points=points,
        evidence="; ".join(reasons).capitalize(),
        details={"foreign_location": bool(foreign_location)},
    )


def _word_tokens(text: Optional[str]) -> List[str]:
    """Lowercase alphanumeric word tokens (drops punctuation/whitespace)."""
    if not text:
        return []
    return re.findall(r"[a-z0-9]+", str(text).lower())


def _word_tokens_with_spans(text: Optional[str]) -> List[tuple]:
    """Like ``_word_tokens`` but keeps each token's char span into the original
    text so a matched run can be reconstructed verbatim (original casing and
    punctuation) for recruiter-facing display.

    Returns a list of ``(lowercased_token, start_char, end_char)`` tuples.
    """
    if not text:
        return []
    s = str(text)
    return [(m.group(0).lower(), m.start(), m.end())
            for m in re.finditer(r"[A-Za-z0-9]+", s)]


def _collapse_ws(text: str) -> str:
    """Collapse runs of whitespace/newlines to single spaces for clean inline
    display in emails and Bullhorn notes."""
    return re.sub(r"\s+", " ", str(text)).strip()


def _build_mirror_excerpt(
    source_text: str,
    spans: List[tuple],
    run_start: int,
    run_len: int,
) -> Dict[str, str]:
    """Reconstruct the copied passage plus a bounded context window from one
    document, mapping matched token positions back to the original text.

    Returns a dict with ``passage`` (the verbatim copied text) and ``excerpt``
    (passage + surrounding context, whitespace-collapsed). Both are drawn from
    the ORIGINAL source so casing/punctuation are preserved.
    """
    last = run_start + run_len - 1
    passage_start = spans[run_start][1]
    passage_end = spans[last][2]
    passage = _collapse_ws(source_text[passage_start:passage_end])
    if len(passage) > JD_MIRROR_MAX_PASSAGE_CHARS:
        passage = passage[:JD_MIRROR_MAX_PASSAGE_CHARS].rstrip() + "…"

    ctx_start_tok = max(0, run_start - JD_MIRROR_CONTEXT_WORDS)
    ctx_end_tok = min(len(spans) - 1, last + JD_MIRROR_CONTEXT_WORDS)
    excerpt = _collapse_ws(source_text[spans[ctx_start_tok][1]:spans[ctx_end_tok][2]])
    return {"passage": passage, "excerpt": excerpt}


def evaluate_jd_mirror(
    resume_text: Optional[str],
    job_description: Optional[str],
) -> Optional[FraudSignal]:
    """Flag a resume that lifts long verbatim passages from the job description.

    Measures the LONGEST contiguous run of identical consecutive words shared
    between the resume and the JD — i.e. copy-paste, not keyword overlap. A
    genuinely qualified candidate naturally shares individual skill keywords with
    a JD; they do NOT reproduce 8/18/30-word stretches of the posting verbatim.
    Graduated weight by run length. Requires a JD of meaningful length to bother.
    """
    # Keep char spans so the longest matched run can be reconstructed verbatim
    # from the ORIGINAL text (casing/punctuation intact) for recruiter display.
    r_spans = _word_tokens_with_spans(resume_text)
    j_spans = _word_tokens_with_spans(job_description)
    r_tokens = [t[0] for t in r_spans]
    j_tokens = [t[0] for t in j_spans]
    if len(j_tokens) < JD_MIRROR_MIN_JD_WORDS or len(r_tokens) < JD_MIRROR_MIN_RUN_WORDS:
        return None

    n = JD_MIRROR_MIN_RUN_WORDS
    # Index every n-gram position in the JD by its tuple, then walk the resume
    # extending each match as far as it stays verbatim. Bounded and linear-ish
    # for the text sizes involved (resume capped at 50k chars upstream).
    jd_ngram_starts: Dict[tuple, List[int]] = {}
    for i in range(len(j_tokens) - n + 1):
        jd_ngram_starts.setdefault(tuple(j_tokens[i:i + n]), []).append(i)

    longest_run = 0
    best_ri = -1   # resume token index where the longest run starts
    best_js = -1   # JD token index where the longest run starts
    ri = 0
    r_len = len(r_tokens)
    while ri <= r_len - n:
        key = tuple(r_tokens[ri:ri + n])
        starts = jd_ngram_starts.get(key)
        if not starts:
            ri += 1
            continue
        best_here = n
        best_here_js = starts[0]
        for js in starts:
            run = n
            while (ri + run < r_len and js + run < len(j_tokens)
                   and r_tokens[ri + run] == j_tokens[js + run]):
                run += 1
            if run > best_here:
                best_here = run
                best_here_js = js
        if best_here > longest_run:
            longest_run = best_here
            best_ri = ri
            best_js = best_here_js
        # Skip past this matched run to keep the scan bounded.
        ri += max(best_here - n + 1, 1)

    if longest_run < JD_MIRROR_MIN_RUN_WORDS:
        return None

    if longest_run >= JD_MIRROR_HEAVY_RUN_WORDS:
        points = POINTS_JD_MIRROR_HEAVY
    elif longest_run >= JD_MIRROR_MODERATE_RUN_WORDS:
        points = POINTS_JD_MIRROR_MODERATE
    else:
        points = POINTS_JD_MIRROR_LIGHT

    # Reconstruct the copied passage + a bounded context window from BOTH
    # documents so a recruiter can see exactly what was lifted and where, even
    # for a Clear-band candidate. Defensive: never let display capture break the
    # signal itself.
    details: Dict[str, Any] = {"longest_run_words": longest_run}
    try:
        if best_ri >= 0 and best_js >= 0:
            resume_ex = _build_mirror_excerpt(str(resume_text), r_spans, best_ri, longest_run)
            jd_ex = _build_mirror_excerpt(str(job_description), j_spans, best_js, longest_run)
            # copied_text == the resume-side verbatim passage (primary, kept for
            # the note); jd_passage == the same run as it reads in the posting
            # (may differ in casing/punctuation), so each excerpt highlights its
            # own source rather than a shared string.
            details["copied_text"] = resume_ex["passage"]
            details["jd_passage"] = jd_ex["passage"]
            details["resume_excerpt"] = resume_ex["excerpt"]
            details["jd_excerpt"] = jd_ex["excerpt"]
    except Exception:  # pragma: no cover - display reconstruction is best-effort
        pass

    return FraudSignal(
        code="jd_mirror",
        label="Resume mirrors the job description verbatim",
        points=points,
        evidence=(f"Longest verbatim passage copied from the posting: "
                  f"{longest_run} consecutive words"),
        details=details,
    )


def evaluate_ai_style_markers(
    resume_text: Optional[str],
) -> Optional[FraudSignal]:
    """INFORMATIONAL ONLY (0 points): note stylistic markers common in AI text.

    Surfaces, without accusing or scoring, when a resume shows writing markers
    frequently produced by AI assistants (notably em dashes). AI-detection on
    short resume/bullet text is unreliable, so this NEVER contributes points and
    NEVER bands a candidate — it is context for a recruiter's own judgement only.
    Conservative: requires several em dashes before surfacing.
    """
    if not resume_text:
        return None
    text_s = str(resume_text)
    em_dashes = text_s.count("\u2014")  # —
    if em_dashes < AI_STYLE_MIN_EM_DASHES:
        return None
    markers = [f"{em_dashes} em dashes"]
    return FraudSignal(
        code="ai_style_markers",
        label="Writing-style markers common in AI-assisted text",
        points=POINTS_AI_STYLE_MARKERS,
        evidence="Informational only — " + ", ".join(markers)
                 + ". Not an accusation; AI-style detection is unreliable.",
        details={"em_dashes": em_dashes, "informational": True},
    )


# ---------------------------------------------------------------------------
# Multi-submission drift / PDF forensics / contact / LinkedIn soft checks
# ---------------------------------------------------------------------------

_YEARS_CLAIM_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_YEAR_TOKEN_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def extract_max_years_claim(text: Optional[str]) -> Optional[int]:
    """Best-effort max 'N years' claim from résumé text (deterministic)."""
    if not text:
        return None
    vals = [int(m.group(1)) for m in _YEARS_CLAIM_RE.finditer(str(text))]
    vals = [v for v in vals if 1 <= v <= 50]
    return max(vals) if vals else None


def extract_year_span(text: Optional[str]) -> Optional[int]:
    """Difference between earliest and latest 4-digit years in the text."""
    if not text:
        return None
    years = [int(m.group(1)) for m in _YEAR_TOKEN_RE.finditer(str(text))]
    years = [y for y in years if 1970 <= y <= date.today().year + 1]
    if len(years) < 2:
        return None
    span = max(years) - min(years)
    return span if 0 < span <= 55 else None


def evaluate_submission_drift(
    changes: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[FraudSignal]:
    """Flag claim drift across prior submissions for the same contact/ID.

    ``changes`` is a list of human-readable change dicts gathered by the engine,
    each with at least ``kind`` and ``summary``. Graduated weight by severity.
    """
    items = [c for c in (changes or []) if isinstance(c, dict) and c.get("summary")]
    if not items:
        return None
    kinds = {str(c.get("kind") or "") for c in items}
    moderate_kinds = {"years_inflation", "linkedin_changed", "name_changed"}
    is_moderate = bool(kinds & moderate_kinds) and (
        len(items) >= 2 or "years_inflation" in kinds
    )
    points = (
        POINTS_SUBMISSION_DRIFT_MODERATE if is_moderate
        else POINTS_SUBMISSION_DRIFT_LIGHT
    )
    evidence = "; ".join(str(c["summary"]) for c in items[:3])
    prior_date = next(
        (str(c.get("prior_date") or "") for c in items if c.get("prior_date")),
        "",
    )
    return FraudSignal(
        code="submission_drift",
        label="Claims changed across prior submissions",
        points=points,
        evidence=evidence,
        details={
            "changes": list(items)[:10],
            "prior_date": prior_date,
            "severity": "moderate" if is_moderate else "light",
        },
    )


def resume_content_tokens(text: Optional[str]) -> frozenset:
    """Lowercased alphanumeric word tokens for résumé overlap comparison."""
    if not text:
        return frozenset()
    return frozenset(_RESUME_TOKEN_RE.findall(str(text).lower()))


def jaccard_token_overlap(a: frozenset, b: frozenset) -> float:
    """Jaccard similarity of two token sets; 1.0 when both empty."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    union = len(a | b)
    if union == 0:
        return 1.0
    return len(a & b) / union


def dedupe_near_identical_resume_versions(
    versions: Sequence[Dict[str, Any]],
    near_dup_jaccard: float = RESUME_VERSION_NEAR_DUP_JACCARD,
    min_tokens: int = RESUME_VERSION_MIN_TOKENS,
) -> List[Dict[str, Any]]:
    """Collapse near-identical résumé versions (hash or high Jaccard).

    Each item needs ``name`` and ``text`` (or precomputed ``tokens``).
    Returns unique representatives in input order. Short/empty parses are
    dropped so garbage extractions cannot fabricate divergence.
    """
    unique: List[Dict[str, Any]] = []
    for raw in versions or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "")
        tokens = raw.get("tokens")
        if not isinstance(tokens, frozenset):
            tokens = resume_content_tokens(text)
        if len(tokens) < min_tokens:
            continue
        content_hash = str(raw.get("content_hash") or "")
        if not content_hash and text:
            content_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        is_dup = False
        for kept in unique:
            if content_hash and content_hash == kept.get("content_hash"):
                is_dup = True
                break
            if jaccard_token_overlap(tokens, kept["tokens"]) >= near_dup_jaccard:
                is_dup = True
                break
        if is_dup:
            continue
        unique.append({
            "name": str(raw.get("name") or "resume")[:200],
            "text": text,
            "tokens": tokens,
            "content_hash": content_hash,
            "file_id": raw.get("file_id"),
        })
    return unique


def evaluate_divergent_resume_versions(
    versions: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    near_dup_jaccard: float = RESUME_VERSION_NEAR_DUP_JACCARD,
    divergence_jaccard: float = RESUME_VERSION_DIVERGENCE_JACCARD,
    min_tokens: int = RESUME_VERSION_MIN_TOKENS,
) -> Optional[FraudSignal]:
    """Soft Review advisory when Resume-typed files clearly diverge in content.

    Near-identical duplicates (merge copies / re-uploads) are collapsed first.
    Fires only when ≥2 unique versions remain and the *minimum* pairwise
    Jaccard word overlap is below ``divergence_jaccard`` (default 0.40).
    """
    unique = dedupe_near_identical_resume_versions(
        versions or [],
        near_dup_jaccard=near_dup_jaccard,
        min_tokens=min_tokens,
    )
    if len(unique) < 2:
        return None

    min_overlap = 1.0
    worst_pair = (unique[0]["name"], unique[1]["name"])
    for i in range(len(unique)):
        for j in range(i + 1, len(unique)):
            overlap = jaccard_token_overlap(unique[i]["tokens"], unique[j]["tokens"])
            if overlap < min_overlap:
                min_overlap = overlap
                worst_pair = (unique[i]["name"], unique[j]["name"])

    if min_overlap >= divergence_jaccard:
        return None

    pct = int(round(min_overlap * 100))
    names = [v["name"] for v in unique[:4]]
    name_list = ", ".join(f"'{n}'" for n in names)
    if len(unique) > 4:
        name_list += f" (+{len(unique) - 4} more)"
    return FraudSignal(
        code="divergent_resume_versions",
        label="Clearly divergent résumé files on this candidate",
        points=POINTS_DIVERGENT_RESUME_VERSIONS,
        evidence=(
            f"{len(unique)} distinct Resume-typed files with low content overlap "
            f"(~{pct}% word overlap between '{worst_pair[0]}' and '{worst_pair[1]}'). "
            f"Files: {name_list}. Confirm which career history applies."
        ),
        details={
            "unique_versions": len(unique),
            "min_jaccard": round(min_overlap, 3),
            "divergence_threshold": divergence_jaccard,
            "worst_pair": list(worst_pair),
            "names": names,
        },
    )


def evaluate_pdf_author_reuse(
    signature: Optional[str],
    other_identities: int = 0,
    recent_mod: bool = False,
) -> List[FraudSignal]:
    """Flag PDF Author/Producer signature shared across different names."""
    out: List[FraudSignal] = []
    if not signature or other_identities < 1:
        return out
    n = int(other_identities)
    out.append(FraudSignal(
        code="pdf_author_reuse",
        label="PDF metadata signature reused across identities",
        points=POINTS_PDF_AUTHOR_REUSE,
        evidence=(
            f"Same PDF Author/Producer fingerprint appears on {n} other "
            f"candidate {'identity' if n == 1 else 'identities'}."
        ),
        details={"signature": signature[:120], "other_identities": n},
    ))
    if recent_mod:
        out.append(FraudSignal(
            code="pdf_recent_reskin",
            label="Recently modified PDF with shared metadata signature",
            points=POINTS_PDF_RECENT_RESKIN,
            evidence=(
                "PDF ModDate is recent and the Author/Producer signature is "
                "shared with other identities — possible agency reskin."
            ),
            details={"signature": signature[:120], "recent_mod": True},
        ))
    return out


def evaluate_email_undeliverable(
    email: Optional[str],
    result: Optional[str],
) -> Optional[FraudSignal]:
    """Flag NeverBounce (or similar) undeliverable / invalid email results."""
    if not email or not result:
        return None
    # Only hard-fail on clear negatives — not "unknown"/"catchall".
    hard = {"invalid", "disposable", "undeliverable"}
    r = str(result).strip().lower()
    if r not in hard:
        return None
    return FraudSignal(
        code="email_undeliverable",
        label="Email failed deliverability check",
        points=POINTS_EMAIL_UNDELIVERABLE,
        evidence=f"Contact validation result for this address: {r}.",
        details={"result": r},
    )


def evaluate_phone_validation(
    phone: Optional[str],
    valid: Optional[bool] = None,
    line_type: Optional[str] = None,
) -> List[FraudSignal]:
    """Twilio Lookup outcomes: invalid line and soft VOIP flag."""
    out: List[FraudSignal] = []
    if not phone:
        return out
    if valid is False:
        out.append(FraudSignal(
            code="phone_invalid",
            label="Phone failed carrier lookup",
            points=POINTS_PHONE_INVALID,
            evidence="Phone number is invalid or unassigned per carrier lookup.",
            details={"line_type": line_type or ""},
        ))
        return out
    lt = (line_type or "").strip().lower()
    if lt in ("voip", "nonfixedvoip", "non_fixed_voip"):
        out.append(FraudSignal(
            code="phone_voip",
            label="Phone is VOIP / virtual line",
            points=POINTS_PHONE_VOIP,
            evidence=f"Carrier lookup classifies this number as {lt}.",
            details={"line_type": lt},
        ))
    return out


def evaluate_linkedin_url_status(
    linkedin_url: Optional[str],
    status: Optional[str],
    profile_name: Optional[str] = None,
    resume_name: Optional[str] = None,
) -> List[FraudSignal]:
    """Soft LinkedIn cross-check for a URL already on the résumé.

    ``status``: 'ok' | 'dead' | 'private' | 'blocked' | 'error'
    Name mismatch only fires when status is ok and both names are present with
    a strong mismatch. Soft signals alone are capped below High-Risk in
    ``aggregate``.
    """
    out: List[FraudSignal] = []
    if not linkedin_url or not status:
        return out
    st = str(status).strip().lower()
    if st == "dead":
        out.append(FraudSignal(
            code="linkedin_url_dead",
            label="LinkedIn profile URL unreachable",
            points=POINTS_LINKEDIN_URL_DEAD,
            evidence=(
                f"Résumé LinkedIn URL ({linkedin_url}) returned 404/removed. "
                "LinkedIn may be outdated — verify on call."
            ),
            details={"linkedin_url": linkedin_url, "status": st},
        ))
        return out
    if st in ("private", "blocked", "error"):
        out.append(FraudSignal(
            code="linkedin_unverified",
            label="LinkedIn profile could not be verified",
            points=0,  # informational — private/blocked is common
            evidence=(
                f"Could not verify public profile ({st}). "
                "LinkedIn may be outdated or restricted — verify on call."
            ),
            details={
                "linkedin_url": linkedin_url,
                "status": st,
                "informational": True,
            },
        ))
        return out
    if st == "ok" and profile_name and resume_name:
        pn = normalize_name(profile_name)
        rn = normalize_name(resume_name)
        if pn and rn and pn != rn and pn not in rn and rn not in pn:
            out.append(FraudSignal(
                code="linkedin_name_mismatch",
                label="LinkedIn profile name mismatch",
                points=POINTS_LINKEDIN_NAME_MISMATCH,
                evidence=(
                    f"Public profile name '{profile_name}' does not match "
                    f"résumé name '{resume_name}'. Wrong profile or outdated "
                    "LinkedIn — verify on call."
                ),
                details={
                    "linkedin_url": linkedin_url,
                    "profile_name": profile_name[:120],
                    "resume_name": resume_name[:120],
                },
            ))
    return out


# Static recruiter call questions keyed by signal code (no AI cost).
_QUESTION_TEMPLATES: Dict[str, List[str]] = {
    "resume_reuse": [
        "Ask them to confirm they did not authorize other identities to use this résumé.",
    ],
    "identity_reuse_phone": [
        "Ask them to confirm they own this phone number and explain any shared use.",
    ],
    "identity_reuse_email": [
        "Ask them to confirm they own this email address and explain any shared use.",
    ],
    "linkedin_reuse": [
        "Ask them to open their LinkedIn and confirm they own the profile URL on the résumé.",
    ],
    "jd_mirror": [
        "Ask them to explain the copied posting wording in their own words with a concrete project example.",
    ],
    "submission_drift": [
        "Walk through what changed in their experience since their prior application.",
    ],
    "divergent_resume_versions": [
        "Confirm which résumé version reflects their current target role and "
        "walk through the career history that applies.",
    ],
    "pdf_author_reuse": [
        "Ask how this PDF résumé was prepared and whether a third party edited it.",
    ],
    "pdf_recent_reskin": [
        "Ask when this résumé file was last updated and by whom.",
    ],
    "third_party_submission": [
        "Confirm they submitted this application themselves (not via an agency shell).",
    ],
    "email_undeliverable": [
        "Confirm a working email address they check daily.",
    ],
    "phone_invalid": [
        "Confirm a working phone number and preferred contact method.",
    ],
    "phone_voip": [
        "Confirm this is their primary contact number (VOIP lines are fine if reachable).",
    ],
    "linkedin_url_dead": [
        "Ask for a current LinkedIn URL or explain why the résumé link is unreachable.",
    ],
    "linkedin_name_mismatch": [
        "Confirm the LinkedIn profile on the résumé is theirs and matches their legal name.",
    ],
    "disposable_email": [
        "Ask for a permanent professional or personal email (not a temporary inbox).",
    ],
    "work_future_date": [
        "Clarify employment dates that appear to be in the future on the résumé.",
    ],
    "work_overlap": [
        "Clarify overlapping full-time roles and which was primary.",
    ],
}


def suggested_questions_for_signals(
    signals: Sequence[Optional[FraudSignal]],
    limit: int = 3,
) -> List[str]:
    """Return up to ``limit`` concrete verification questions for fired signals."""
    questions: List[str] = []
    seen = set()
    scored = sorted(
        [s for s in signals if s is not None and (s.points or 0) > 0],
        key=lambda s: s.points,
        reverse=True,
    )
    for sig in scored:
        templates = _QUESTION_TEMPLATES.get(sig.code) or []
        # Specialize submission_drift with prior date when available.
        if sig.code == "submission_drift":
            prior = (sig.details or {}).get("prior_date") or ""
            if prior:
                templates = [
                    f"Walk through what changed in their experience since their "
                    f"prior application on {prior}."
                ]
        for q in templates:
            if q not in seen:
                seen.add(q)
                questions.append(q)
                if len(questions) >= limit:
                    return questions
    return questions


def aggregate(
    signals: Sequence[Optional[FraudSignal]],
    review_threshold: int = DEFAULT_REVIEW_THRESHOLD,
    high_risk_threshold: int = DEFAULT_HIGH_RISK_THRESHOLD,
) -> FraudAssessmentResult:
    """Sum signal weights into a capped 0-100 score and assign a band.

    Soft LinkedIn-only scores are capped below High-Risk so a stale/wrong
    profile never alone becomes a hard fraud accusation.
    """
    clean = [s for s in signals if s is not None]
    score = min(100, sum(s.points for s in clean))

    scored = [s for s in clean if (s.points or 0) > 0]
    if scored and all(s.code in LINKEDIN_SOFT_CODES for s in scored):
        score = min(score, high_risk_threshold - 1)

    if score >= high_risk_threshold:
        band = FraudRiskBand.HIGH_RISK
    elif score >= review_threshold:
        band = FraudRiskBand.REVIEW
    else:
        band = FraudRiskBand.CLEAR

    return FraudAssessmentResult(risk_score=score, risk_band=band, signals=clean)
