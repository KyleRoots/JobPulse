"""Indeed campaign-group tags appended to published job descriptions.

Myticas/STSI use a single ``#INDShow`` marker. Qualified maps Bullhorn
``correlatedCustomText1`` (branch / department) to an ATSA campaign tag.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# Myticas / STSI (unchanged)
MYTICAS_CAMPAIGN_TAG = '#INDShow'

# Qualified: department label (correlatedCustomText1) → exact tag string.
# Keys are normalized (lowercase, collapsed whitespace). Aliases included.
_QUALIFIED_DEPARTMENT_TAGS = {
    'marysville': '#INDMary',
    'calhoun': '#INDCal',
    'appleton': '#IND-WH',
    'appleton (wi)': '#IND-WH',
    'cookeville': '#INDCoo',
    'dalton': '#INDDal',
    'flint': '#INDFli',
    'flint (brighton)': '#INDFli',
    'harrisonburg': '#INDHar',
    'lapeer': '#INDLap',
    'new albany': '#INDNewA',
    'saginaw': '#INDSag',
    'cartersville': '#INDCar',
    'chattanooga': '#INDChat',
    'grand rapids': '#INDGrand',
    'katy': '#INDKat',
    'southfield': '#INDLiv',
    'technical': '#INDQT',
    'qpt': '#INDQT',
    'wake forest': '#INDWak',
    'richmond': '#INDRic',
    'conyers': '#INDCon',
    'warner robins': '#INDWar',
    'columbus': '#INDCol',
    'columbus-key': '#INDCol',
    'columbus key': '#INDCol',
    # Columbus-KIT is the Conyers campaign. Columbus-Key is Columbus (#INDCol).
    'columbus-kit': '#INDCon',
    'columbus kit': '#INDCon',
    'port huron': '#INDMary',
    'internal': '#INDWin',
    'internal reqs': '#INDWin',
    'marietta': '#INDMara',
}

# Tags we may need to strip before writing the correct one (legacy + mapped).
_STRIP_TAGS = frozenset(
    [
        MYTICAS_CAMPAIGN_TAG,
        *_QUALIFIED_DEPARTMENT_TAGS.values(),
        # Legacy Appleton / unhashed variants seen in live descriptions
        '#IND-W',
        'INDMary',
        'INDCal',
        'INDCoo',
        'INDDal',
        'INDFli',
        'INDHar',
        'INDLap',
        'INDNewA',
        'INDSag',
        'INDCar',
        'INDChat',
        'INDGrand',
        'INDKat',
        'INDLiv',
        'INDQT',
        'INDWak',
        'INDRic',
        'INDCon',
        'INDWar',
        'INDCol',
        'INDWin',
        'INDMara',
        'INDShow',
    ]
)


def _norm_department(value: str) -> str:
    text = (value or '').strip().lower()
    text = re.sub(r'\s+', ' ', text)
    return text


def resolve_qualified_campaign_tag(department: Optional[str]) -> Optional[str]:
    """Return the exact campaign tag for a Qualified department, or None."""
    key = _norm_department(department or '')
    if not key:
        return None
    return _QUALIFIED_DEPARTMENT_TAGS.get(key)


def strip_campaign_tags(html: str) -> str:
    """Remove known campaign markers from the end / trailing nodes of a description."""
    text = html or ''
    if not text:
        return text

    # Prefer whole-tag removals (with optional leading spaces / HTML wrappers).
    for tag in sorted(_STRIP_TAGS, key=len, reverse=True):
        escaped = re.escape(tag)
        patterns = (
            rf'(?:\s|&nbsp;)*{escaped}\s*$',
            rf'(?:<br\s*/?>|\s)*{escaped}(?:</[^>]+>)*\s*$',
            rf'<div[^>]*>\s*{escaped}\s*</div>\s*$',
            rf'<span[^>]*>\s*{escaped}\s*</span>\s*$',
            rf'<p[^>]*>\s*{escaped}\s*</p>\s*$',
        )
        for pat in patterns:
            text = re.sub(pat, '', text, flags=re.IGNORECASE | re.MULTILINE)
    return text.rstrip()


def apply_campaign_tag(html: str, tag: str) -> str:
    """Strip prior campaign tags, then append ``   {tag}`` when missing."""
    tag = (tag or '').strip()
    if not tag:
        return html or ''
    if not (html or '').strip():
        return html or ''
    cleaned = strip_campaign_tags(html or '')
    if not cleaned.strip():
        # Description was only a campaign tag; keep empty rather than re-tag alone.
        return cleaned
    if tag in cleaned:
        return cleaned
    return f'{cleaned}   {tag}'


def campaign_tag_for_job(job: dict, *, qualified: bool) -> Tuple[Optional[str], str]:
    """
    Return (tag, reason).

    reason is ``myticas``, ``department:<label>``, or an error reason when tag is None.
    """
    if not qualified:
        return MYTICAS_CAMPAIGN_TAG, 'myticas'
    dept = (job.get('correlatedCustomText1') or '').strip()
    tag = resolve_qualified_campaign_tag(dept)
    if tag:
        return tag, f'department:{dept}'
    if not dept:
        return None, 'missing correlatedCustomText1 (department)'
    return None, f'unmapped department:{dept}'
