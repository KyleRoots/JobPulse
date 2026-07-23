"""Published Category catalog for Bullhorn Publish → Indeed (private label categories).

IDs/names captured from Bullhorn Categories admin (Jul 2026). Prefer 2000021 when
two rows share the name "IT/Software Development".
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# (id, name) — stable catalog used by fuzzy/AI closest-match mapping
PUBLISHED_CATEGORIES: Tuple[Tuple[int, str], ...] = (
    (2000001, 'Accounting'),
    (2000002, 'Administrative'),
    (2000003, 'Advertising'),
    (2000004, 'Architecture/Design'),
    (2000005, 'Banking/Finance'),
    (2000006, 'Biotech/R&D/Science'),
    (2000007, 'Business Analysis'),
    (2000008, 'Business Intelligence'),
    (2000009, 'Creative/Design'),
    (2000010, 'Customer Service'),
    (2000011, 'Database Administration'),
    (2000012, 'Desktop Support'),
    (2000013, 'Engineering'),
    (2000014, 'ERP'),
    (2000015, 'Food Services/Hospitality'),
    (2000016, 'General Analyst'),
    (2000017, 'Human Resources'),
    (2000018, 'Infrastructure'),
    (2000019, 'IT Asset Management'),
    (2000020, 'IT/Networking/Hardware'),
    (2000021, 'IT/Software Development'),  # preferred over duplicate 2000022
    (2000022, 'IT/Software Development'),
    (2000023, 'Legal'),
    (2000024, 'Logistics/Transportation'),
    (2000025, 'Management/Operations'),
    (2000026, 'Manufacturing'),
    (2000027, 'Marketing'),
    (2000028, 'Medical/Healthcare'),
    (2000029, 'Network'),
    (2000030, 'Procurement'),
    (2000031, 'Programming/Development'),
    (2000032, 'Project Management'),
    (2000033, 'Quality Assurance'),
    (2000034, 'Recruiting'),
    (2000035, 'Sales/Business Dev.'),
    (2000036, 'Security'),
    (2000037, 'Systems Engineer'),
    (2000038, 'Technical Writing'),
    (2000039, 'User Experience'),
    (2000040, 'Warehouse'),
    (2000041, 'Web Development'),
    (2000042, 'Z-Skills'),
)

# Prefer the category ID we successfully published with in the live PoC
PREFERRED_CATEGORY_IDS: Dict[str, int] = {
    'it/software development': 2000021,
}

DEFAULT_PUBLISHED_CATEGORY_ID = 2000021  # IT/Software Development
DEFAULT_PUBLISHED_CATEGORY_NAME = 'IT/Software Development'


def category_choices() -> List[Tuple[int, str]]:
    """Unique name→preferred-id choices for mapping (drops duplicate names)."""
    seen = set()
    out: List[Tuple[int, str]] = []
    for cid, name in PUBLISHED_CATEGORIES:
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        preferred = PREFERRED_CATEGORY_IDS.get(key)
        out.append((preferred or cid, name))
    return out


def category_id_by_name(name: str) -> int | None:
    if not name:
        return None
    key = name.strip().lower()
    if key in PREFERRED_CATEGORY_IDS:
        return PREFERRED_CATEGORY_IDS[key]
    for cid, cname in PUBLISHED_CATEGORIES:
        if cname.strip().lower() == key:
            return cid
    return None
