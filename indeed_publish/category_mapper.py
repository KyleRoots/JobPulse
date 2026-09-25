"""Map a job's Bullhorn category / title to the closest Published Category."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from .categories import (
    DEFAULT_PUBLISHED_CATEGORY_ID,
    DEFAULT_PUBLISHED_CATEGORY_NAME,
    category_choices,
    category_id_by_name,
)

# Extra aliases → published category name (normalized later)
_ALIASES = {
    'software': 'IT/Software Development',
    'software development': 'IT/Software Development',
    'software engineer': 'IT/Software Development',
    'developer': 'IT/Software Development',
    'dev': 'IT/Software Development',
    'programmer': 'Programming/Development',
    'programming': 'Programming/Development',
    'web': 'Web Development',
    'frontend': 'Web Development',
    'front-end': 'Web Development',
    'backend': 'IT/Software Development',
    'full stack': 'IT/Software Development',
    'ux': 'User Experience',
    'ui/ux': 'User Experience',
    'qa': 'Quality Assurance',
    'sdet': 'Quality Assurance',
    'network engineer': 'Network',
    'sysadmin': 'Systems Engineer',
    'system administrator': 'Systems Engineer',
    'helpdesk': 'Desktop Support',
    'help desk': 'Desktop Support',
    'hr': 'Human Resources',
    'talent acquisition': 'Recruiting',
    'recruiter': 'Recruiting',
    'pm': 'Project Management',
    'project manager': 'Project Management',
    'business analyst': 'Business Analysis',
    'ba': 'Business Analysis',
    'bi': 'Business Intelligence',
    'data analyst': 'General Analyst',
    'sales': 'Sales/Business Dev.',
    'account executive': 'Sales/Business Dev.',
    'cybersecurity': 'Security',
    'info sec': 'Security',
    'warehouse associate': 'Warehouse',
    'logistics': 'Logistics/Transportation',
}

# Qualified light-industrial titles. Checked as a phrase inside the title
# (longest first). Myticas/STSI do not use this list; their unmatched jobs
# still fall back to IT/Software Development.
_QUALIFIED_TITLE_ALIASES: Tuple[Tuple[str, str], ...] = (
    ('food packaging', 'Food Services/Hospitality'),
    ('food production', 'Food Services/Hospitality'),
    ('school nutrition', 'Food Services/Hospitality'),
    ('nutrition', 'Food Services/Hospitality'),
    ('cherry picker', 'Warehouse'),
    ('material handler', 'Warehouse'),
    ('fork lift', 'Warehouse'),
    ('forklift', 'Warehouse'),
    ('palletizer', 'Warehouse'),
    ('picker', 'Warehouse'),
    ('packer', 'Warehouse'),
    ('stacker', 'Warehouse'),
    ('warehouse', 'Warehouse'),
    ('quality inspector', 'Quality Assurance'),
    ('call center', 'Customer Service'),
    ('route driver', 'Logistics/Transportation'),
    ('dispatcher', 'Logistics/Transportation'),
    ('machine operator', 'Manufacturing'),
    ('mig weld', 'Manufacturing'),
    ('welder', 'Manufacturing'),
    ('assembly', 'Manufacturing'),
    ('cnc', 'Manufacturing'),
    ('sanitation', 'Manufacturing'),
    ('janitorial', 'Manufacturing'),
    ('custodian', 'Manufacturing'),
    ('production', 'Manufacturing'),
    ('packaging', 'Manufacturing'),
    ('packager', 'Manufacturing'),
    ('fabricator', 'Manufacturing'),
    ('die setter', 'Manufacturing'),
    ('general labor', 'Manufacturing'),
    ('substitute teacher', 'Administrative'),
    ('absentee voting', 'Administrative'),
)


def _norm(text: str) -> str:
    text = (text or '').strip().lower()
    text = re.sub(r'[^a-z0-9/.\s+-]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _job_category_names(job: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    cats = job.get('categories') or {}
    data = cats.get('data') if isinstance(cats, dict) else cats
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get('name'):
                names.append(str(item['name']))
            elif isinstance(item, str):
                names.append(item)
    elif isinstance(cats, list):
        for item in cats:
            if isinstance(item, dict) and item.get('name'):
                names.append(str(item['name']))
    single = job.get('category') or job.get('publishedCategory')
    if isinstance(single, dict) and single.get('name'):
        names.append(str(single['name']))
    elif isinstance(single, str) and single.strip():
        names.append(single)
    return names


def _qualified_title_alias(job: Dict[str, Any]) -> Optional[Tuple[int, str, str]]:
    """Phrase match on title and Bullhorn category names. Qualified only."""
    texts = [_norm(str(job.get('title') or ''))]
    texts.extend(_norm(name) for name in _job_category_names(job))
    for text in texts:
        if not text:
            continue
        for key, target in _QUALIFIED_TITLE_ALIASES:
            if key in text:
                cid = category_id_by_name(target)
                if cid is not None:
                    return cid, target, f'qualified-alias:{key}->{target}'
    return None


def map_published_category(
    job: Dict[str, Any],
    *,
    min_ratio: float = 0.55,
    qualified: bool = False,
) -> Tuple[int, str, str]:
    """
    Return (category_id, category_name, reason).

    Strategy: exact → (Qualified title phrases) → alias → fuzzy against catalog
    using job categories + title.

    Unmatched Myticas/STSI jobs fall back to IT/Software Development.
    Unmatched Qualified jobs fall back to Manufacturing. The IT default was
    hiding light-industrial posts on Indeed.
    """
    choices = category_choices()
    title = str(job.get('title') or '')
    sources = _job_category_names(job) + ([title] if title else [])

    for raw in sources:
        exact_id = category_id_by_name(raw)
        if exact_id is not None:
            name = next((n for i, n in choices if i == exact_id), raw)
            return exact_id, name, f'exact:{raw}'

    if qualified:
        aliased = _qualified_title_alias(job)
        if aliased:
            return aliased

    for raw in sources:
        needle = _norm(raw)
        alias = _ALIASES.get(needle)
        if not alias:
            for key, target in _ALIASES.items():
                if key in needle or needle in key:
                    alias = target
                    break
        if alias:
            cid = category_id_by_name(alias)
            if cid is not None:
                return cid, alias, f'alias:{raw}->{alias}'

    best: Optional[Tuple[float, int, str, str]] = None
    for raw in sources:
        needle = _norm(raw)
        if not needle:
            continue
        for cid, name in choices:
            if name == 'Z-Skills':
                continue  # catch-all skill bucket — never prefer as publish category
            ratio = SequenceMatcher(None, needle, _norm(name)).ratio()
            # Boost token overlap
            needle_tokens = set(needle.replace('/', ' ').split())
            name_tokens = set(_norm(name).replace('/', ' ').split())
            if needle_tokens and name_tokens:
                overlap = len(needle_tokens & name_tokens) / max(len(needle_tokens), 1)
                ratio = max(ratio, 0.45 * ratio + 0.55 * overlap)
            if best is None or ratio > best[0]:
                best = (ratio, cid, name, raw)

    if best and best[0] >= min_ratio:
        ratio, cid, name, raw = best
        return cid, name, f'fuzzy:{raw}->{name}@{ratio:.2f}'

    if qualified:
        manufacturing_id = category_id_by_name('Manufacturing')
        if manufacturing_id is not None:
            return manufacturing_id, 'Manufacturing', 'fallback:qualified-manufacturing'

    return (
        DEFAULT_PUBLISHED_CATEGORY_ID,
        DEFAULT_PUBLISHED_CATEGORY_NAME,
        'fallback:default',
    )
