"""Normalize job screening requirements into a consistent bullet list.

Used by Configure Screening (UI + save), AI extraction persistence, and
Refine-with-AI so recruiters always see one requirement per line.
"""
from __future__ import annotations

import re
from typing import Optional


_BULLET_PREFIX_RE = re.compile(r'^(\s*[-*•]+\s+|\s*\d+[.)]\s+)')
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9])')


def normalize_requirements_to_bullets(text: Optional[str]) -> str:
    """Return requirements as ``- item`` lines (one requirement per line).

    Handles:
    - Existing bullet / numbered lists (re-prefixed cleanly)
    - Newline-separated plain lines
    - Prose / paragraph extracts (split on sentence boundaries)
    - Pipe-separated lists (``a | b | c``)
    - Legacy ``.. `` separators used by an older UI normalizer
    """
    if not text or not str(text).strip():
        return ''

    t = str(text).replace('\r\n', '\n').replace('\r', '\n').strip()
    t = t.replace('.. ', '\n')

    if '\n' in t:
        raw_lines = t.split('\n')
    elif '|' in t and t.count('|') >= 1:
        raw_lines = [p.strip() for p in t.split('|')]
    else:
        raw_lines = _SENTENCE_SPLIT_RE.split(t)
        if len(raw_lines) < 2:
            raw_lines = [t]

    out = []
    seen = set()
    for line in raw_lines:
        line = (line or '').strip()
        if not line:
            continue
        line = _BULLET_PREFIX_RE.sub('', line).strip()
        if not line:
            continue
        # Drop trailing list punctuation left by sentence split
        if len(line) > 1 and line.endswith('.') and line.count('.') == 1:
            # keep period for abbreviations like "U.S." — only strip if single final .
            pass
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(f'- {line}')

    return '\n'.join(out)
