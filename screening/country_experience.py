"""Hard gate for country-scoped years-of-experience requirements.

When a job (or recruiter-edited Scout requirements) requires N years of
experience *in Canada* or *in the United States*, candidates who clearly
fall short must not qualify and must not fire recruiter qualify emails.

Detection is regex-based on requirements + JD text. Tenure estimate comes
from ``years_analysis`` keys the model is instructed to emit (e.g.
"Canadian professional experience"), with a fail-closed fallback when the
candidate's stated country does not match the required country and no
in-country years entry is present.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from screening.post_processing import (
    YEARS_CLOSE_MAX_SHORTFALL_YEARS,
    YEARS_CLOSE_MIN_RATIO,
    _safe_float,
    classify_years_tenure,
)

logger = logging.getLogger(__name__)

COUNTRY_CANADA = 'canada'
COUNTRY_US = 'united_states'

# Display labels used in gaps / logs
_COUNTRY_LABEL = {
    COUNTRY_CANADA: 'Canada',
    COUNTRY_US: 'United States',
}

# years_analysis skill keys we accept for each country (normalized match)
_ANALYSIS_KEY_HINTS = {
    COUNTRY_CANADA: (
        'canadian professional experience',
        'canadian work experience',
        'experience in canada',
        'years in canada',
        'canadian residency',
        'canadian tenure',
        'canada experience',
    ),
    COUNTRY_US: (
        'us professional experience',
        'u.s. professional experience',
        'united states professional experience',
        'us work experience',
        'u.s. work experience',
        'experience in the united states',
        'experience in the us',
        'years in the us',
        'years in the united states',
        'us residency',
        'united states experience',
    ),
}

# Requirement text patterns → (country, years group index)
_REQUIREMENT_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    # "5+ years of experience in Canada" / "5 years working in Canada"
    (
        re.compile(
            r'(\d+)\+?\s*years?\s+(?:of\s+)?'
            r'(?:(?:professional|relevant|work|industry)\s+)?'
            r'(?:experience\s+)?'
            r'(?:working\s+)?'
            r'(?:in|within)\s+Canada\b',
            re.IGNORECASE,
        ),
        COUNTRY_CANADA,
    ),
    (
        re.compile(
            r'(\d+)\+?\s*years?\s+(?:of\s+)?'
            r'(?:(?:professional|relevant|work|industry)\s+)?'
            r'(?:experience\s+)?'
            r'(?:working\s+)?'
            r'(?:in|within)\s+(?:the\s+)?(?:United\s+States|U\.?S\.?A?\.?)\b',
            re.IGNORECASE,
        ),
        COUNTRY_US,
    ),
    # "5+ years Canadian experience" / "minimum 5 years Canadian work experience"
    (
        re.compile(
            r'(\d+)\+?\s*years?\s+(?:of\s+)?'
            r'Canadian\s+(?:work\s+|professional\s+)?'
            r'(?:experience|residency|residence|tenure)\b',
            re.IGNORECASE,
        ),
        COUNTRY_CANADA,
    ),
    (
        re.compile(
            r'(\d+)\+?\s*years?\s+(?:of\s+)?'
            r'(?:US|U\.S\.|United\s+States)\s+(?:work\s+|professional\s+)?'
            r'(?:experience|residency|residence|tenure)\b',
            re.IGNORECASE,
        ),
        COUNTRY_US,
    ),
    # "must reside in Canada 5+ years" / "reside in Canada for 5 years"
    (
        re.compile(
            r'(?:must|require[ds]?|minimum|need(?:s)?\s+to)\s+'
            r'(?:reside|live|work|have\s+(?:lived|worked))\s+'
            r'(?:in\s+)?Canada\s+'
            r'(?:for\s+)?'
            r'(\d+)\+?\s*years?\b',
            re.IGNORECASE,
        ),
        COUNTRY_CANADA,
    ),
    (
        re.compile(
            r'(?:must|require[ds]?|minimum|need(?:s)?\s+to)\s+'
            r'(?:reside|live|work|have\s+(?:lived|worked))\s+'
            r'(?:in\s+)?(?:the\s+)?(?:United\s+States|U\.?S\.?A?\.?)\s+'
            r'(?:for\s+)?'
            r'(\d+)\+?\s*years?\b',
            re.IGNORECASE,
        ),
        COUNTRY_US,
    ),
    # "minimum 5 years in Canada"
    (
        re.compile(
            r'(?:minimum|at\s+least|require[ds]?)\s+(\d+)\+?\s*years?\s+'
            r'(?:in|within)\s+Canada\b',
            re.IGNORECASE,
        ),
        COUNTRY_CANADA,
    ),
    (
        re.compile(
            r'(?:minimum|at\s+least|require[ds]?)\s+(\d+)\+?\s*years?\s+'
            r'(?:in|within)\s+(?:the\s+)?(?:United\s+States|U\.?S\.?A?\.?)\b',
            re.IGNORECASE,
        ),
        COUNTRY_US,
    ),
)


def parse_country_experience_requirements(text: str) -> List[Tuple[str, float]]:
    """Return list of (country_key, required_years) from requirements/JD text."""
    if not text or not str(text).strip():
        return []
    found: Dict[str, float] = {}
    for pattern, country in _REQUIREMENT_PATTERNS:
        for match in pattern.finditer(text):
            try:
                years = float(match.group(1))
            except (TypeError, ValueError, IndexError):
                continue
            if years <= 0:
                continue
            prev = found.get(country)
            if prev is None or years > prev:
                found[country] = years
    return [(c, y) for c, y in found.items()]


def _normalize_country_label(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if s in ('canada', 'ca', 'can'):
        return COUNTRY_CANADA
    if s in (
        'united states', 'united states of america', 'usa', 'us', 'u.s.',
        'u.s.a.', 'u.s.a', 'america',
    ):
        return COUNTRY_US
    if 'canada' in s:
        return COUNTRY_CANADA
    if 'united states' in s or s.endswith(', us') or ', usa' in s:
        return COUNTRY_US
    return None


def _estimated_from_years_analysis(years_analysis: dict, country: str) -> Optional[float]:
    if not isinstance(years_analysis, dict):
        return None
    hints = _ANALYSIS_KEY_HINTS.get(country, ())
    best = None
    for skill, data in years_analysis.items():
        if not isinstance(data, dict):
            continue
        key = (skill or '').strip().lower()
        if not any(h in key for h in hints):
            # Also accept exact-ish: "canadian ..." anywhere in key
            if country == COUNTRY_CANADA and 'canada' not in key and 'canadian' not in key:
                continue
            if country == COUNTRY_US and 'united states' not in key and 'u.s' not in key:
                if not re.search(r'\bus\b', key):
                    continue
                # Avoid matching "business", "focus", etc. — require us + experience/work/years
                if not any(t in key for t in ('experience', 'work', 'years', 'tenure', 'residenc')):
                    continue
        estimated = _safe_float(data.get('estimated_years'), default=None)
        if estimated is None:
            continue
        if best is None or estimated > best:
            best = estimated
    return best


def enforce_country_experience_gate(
    result: dict,
    job_id,
    custom_requirements: Optional[str],
    job_description: Optional[str],
    candidate_country: Optional[str] = None,
) -> None:
    """Block qualify when country-scoped YOE clearly falls short.

    Sets ``_country_experience_blocks_qualify`` and also forces
    ``_years_tenure_blocks_qualify`` so existing qualify checks catch it.
    Caps match_score at 60 on clear shortfall (aligned with years hard gate).
    """
    combined = ' '.join(filter(None, [
        custom_requirements or '',
        result.get('key_requirements', '') or '',
        job_description or '',
    ]))
    requirements = parse_country_experience_requirements(combined)
    if not requirements:
        result['_country_experience_blocks_qualify'] = False
        return

    years_analysis = result.get('years_analysis', {})
    cand_country = _normalize_country_label(candidate_country)
    blocked = False
    gap_parts: List[str] = []

    for country, required in requirements:
        label = _COUNTRY_LABEL[country]
        estimated = _estimated_from_years_analysis(years_analysis, country)

        # Fail closed when candidate lives in a different country and the model
        # did not credit any in-country years — treat as 0.
        if estimated is None and cand_country and cand_country != country:
            estimated = 0.0
            logger.info(
                f"🚫 Country YOE: no '{label}' years_analysis for job {job_id}; "
                f"candidate country={cand_country!r} ≠ required → estimated=0"
            )

        if estimated is None:
            # Requirement present but model omitted the key and candidate
            # country is unknown or matches — do not invent a pass. Cap and
            # block until tenure is proven (Adam: nothing gets through).
            estimated = 0.0
            logger.info(
                f"🚫 Country YOE: missing '{label}' years_analysis for job {job_id}; "
                f"fail-closed estimated=0 (required={required:.0f}yr)"
            )

        status = classify_years_tenure(required, estimated)
        skill_label = f'{label} professional experience'

        if status == 'clear_shortfall':
            blocked = True
            gap_parts.append(
                f"CRITICAL: {skill_label} requires {required:.0f}yr in {label}, "
                f"candidate has ~{estimated:.1f}yr"
            )
            logger.info(
                f"🚫 Country YOE blocks qualify for job {job_id}: {skill_label} "
                f"~{estimated:.1f}yr < {required:.0f}yr required"
            )
        elif status == 'close':
            caveat = (
                f"YEARS CLOSE: {skill_label} dated tenure ~{estimated:.1f}yr vs "
                f"{required:.0f}yr required in {label} — recruiter should confirm"
            )
            gaps = result.get('gaps_identified', '') or ''
            if 'YEARS CLOSE:' not in gaps or skill_label not in gaps:
                result['gaps_identified'] = f"{gaps} | {caveat}" if gaps else caveat

    result['_country_experience_blocks_qualify'] = blocked
    if not blocked:
        return

    # Align with skill years hard gate: block qualify + cap score
    result['_years_tenure_blocks_qualify'] = True
    original = result.get('match_score', 0) or 0
    try:
        original_f = float(original)
    except (TypeError, ValueError):
        original_f = 0.0
    if original_f > 60:
        result['match_score'] = 60
        logger.info(
            f"📉 Country YOE hard gate: capped score {original_f:.0f}→60 for job {job_id}"
        )

    if gap_parts:
        existing = result.get('gaps_identified', '') or ''
        suffix = ' | '.join(gap_parts)
        if existing:
            result['gaps_identified'] = f"{existing} | {suffix}"
        else:
            result['gaps_identified'] = suffix


def country_experience_allows_qualify(analysis) -> bool:
    """False when country-scoped YOE clearly fails (blocks is_qualified)."""
    if not isinstance(analysis, dict):
        return True
    return not bool(analysis.get('_country_experience_blocks_qualify'))


__all__ = [
    'COUNTRY_CANADA',
    'COUNTRY_US',
    'parse_country_experience_requirements',
    'enforce_country_experience_gate',
    'country_experience_allows_qualify',
    'YEARS_CLOSE_MAX_SHORTFALL_YEARS',
    'YEARS_CLOSE_MIN_RATIO',
]
