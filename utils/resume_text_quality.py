"""Resume text quality helpers — detect mojibake / broken-font PDF extracts.

Visual PDFs with custom fonts or broken ToUnicode maps can yield long
streams of ASCII gibberish (e.g. ``c U=Zc= m& m Kb…``). Those extracts are
long enough to skip the empty-text OCR gate, poison Bullhorn ``description``,
and then poison screening location/skill analysis.

Shared by inbound resume parsing, vetting PDF extract, and screening.
"""

from __future__ import annotations

import re
from typing import Optional

# Legacy Word OLE / binary junk markers (automation resume reparser)
_OLE_GARBLED_PATTERNS = (
    'WW8Num',
    'OJQJ',
    '^J ',
    'phOJQJ',
    'OJQJo',
    'Num1z',
    'OJQJ^J',
)

# Characters that are rare in real resumes but common in broken ToUnicode maps
_WEIRD_CHARS = set('\\]^`|<>{}~\x00\x01\x1e\x1f')

_RESUME_ANCHORS = (
    'experience',
    'education',
    'skills',
    'summary',
    'university',
    'bachelor',
    'master',
    'engineer',
    'manager',
    'developer',
    'professional',
    'responsibility',
    'responsibilities',
    'employment',
    'certification',
    'project',
    'objective',
)

_COMMON_ENGLISH = (
    ' the ',
    ' and ',
    ' of ',
    ' to ',
    ' in ',
    ' for ',
    ' with ',
    ' a ',
    ' at ',
    ' as ',
)


def _strip_html(text: str) -> str:
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def is_garbled_resume_text(text: Optional[str], *, min_len: int = 80) -> bool:
    """
    Return True when *text* looks like a failed PDF extract / binary junk.

    Short / empty strings return False (callers handle those separately).
    """
    if not text:
        return False

    cleaned = _strip_html(str(text))
    if len(cleaned) < min_len:
        return False

    ole_hits = sum(1 for p in _OLE_GARBLED_PATTERNS if p in cleaned)
    if ole_hits >= 2:
        return True

    letters = sum(1 for c in cleaned if c.isalpha())
    if letters < 40:
        return True

    non_space = max(len(cleaned) - cleaned.count(' '), 1)
    weird = sum(1 for c in cleaned if c in _WEIRD_CHARS)
    weird_ratio = weird / non_space

    # Broken-font extracts often keep a few real section headers ("Experience")
    # while the body is symbol soup — weird density is the strong signal.
    if weird_ratio >= 0.04:
        return True

    lower = f' {cleaned.lower()} '
    resume_hits = sum(1 for t in _RESUME_ANCHORS if t in lower)
    english_hits = sum(1 for t in _COMMON_ENGLISH if t in lower)

    words = re.findall(r"[A-Za-z]{3,}", cleaned)
    if len(words) < 15:
        # Long text but almost no real words → garbled
        if len(cleaned) >= 200:
            return True
        return False

    vowel_words = sum(1 for w in words if re.search(r'[aeiouAEIOU]', w))
    vowel_ratio = vowel_words / len(words)

    # Few resume anchors + almost no English function words + weak vowels
    if resume_hits <= 1 and english_hits <= 1 and len(cleaned) >= 200:
        return True
    if vowel_ratio < 0.50 and english_hits <= 2 and resume_hits <= 2:
        return True

    # High density of "words" with no vowels (BZcm, QBdm, …)
    no_vowel = sum(1 for w in words if len(w) >= 4 and not re.search(r'[aeiouAEIOU]', w))
    if no_vowel / len(words) >= 0.35 and resume_hits <= 3:
        return True

    return False
