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

# Qualified private label (51284), from options/Category on 25 Sep 2026.
# The shared catalog above includes a duplicate IT/Software row at 2000022,
# which shifts every later ID by one versus this corp. Publishing those IDs
# stored Web Development for a Warehouse job. Myticas/STSI keep the shared catalog.
QUALIFIED_CATEGORY_IDS: Dict[str, int] = {
    'accounting': 2000001,
    'administrative': 2000002,
    'advertising': 2000003,
    'architecture/design': 2000004,
    'banking/finance': 2000005,
    'biotech/r&d/science': 2000006,
    'business analysis': 2000007,
    'business intelligence': 2000008,
    'creative/design': 2000009,
    'customer service': 2000010,
    'database administration': 2000011,
    'desktop support': 2000012,
    'engineering': 2000013,
    'erp': 2000014,
    'food services/hospitality': 2000015,
    'general analyst': 2000016,
    'human resources': 2000017,
    'infrastructure': 2000018,
    'it asset management': 2000019,
    'it/networking/hardware': 2000020,
    'it/software development': 2000021,
    'legal': 2000022,
    'logistics/transportation': 2000023,
    'management/operations': 2000024,
    'manufacturing': 2000025,
    'marketing': 2000026,
    'medical/healthcare': 2000027,
    'network': 2000028,
    'procurement': 2000029,
    'programming/development': 2000030,
    'project management': 2000031,
    'quality assurance': 2000032,
    'recruiting': 2000033,
    'sales/business dev.': 2000034,
    'security': 2000035,
    'systems engineer': 2000036,
    'technical writing': 2000037,
    'user experience': 2000038,
    'warehouse': 2000039,
    'web development': 2000040,
    'z-skills': 2000042,
    'z - skills': 2000042,
}


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


def qualified_category_id(name: str) -> int | None:
    """Category id for the Qualified private label. None if the name is unknown."""
    if not name:
        return None
    return QUALIFIED_CATEGORY_IDS.get(name.strip().lower())
