"""
XML feed configuration — tearsheet sets, filenames, and apply URL source params.

Tenant selection via ``SCOUT_TENANT``:

* unset / anything other than ``qualified_staffing`` → Myticas + STSI feeds
  (historical default; JobPulse production)
* ``qualified_staffing`` → Qualified Staffing packs only (JobPulse-Qualified)

v2 (myticas-job-feed-v2.xml): Myticas sponsored tearsheets + STSI LinkedIn (1531).
Channel feeds: STSI Indeed (1640) and ZipRecruiter (1641) only.

Qualified packs ship with empty tearsheet IDs until corp IDs are mapped; keep
XML/SFTP uploads OFF on that service until then.

Reference-number refresh uses ``all_xml_feed_tearsheet_ids()`` so v2 and channel
feeds rotate together on the 120-hour cycle.
"""
import os
from typing import Dict, List, Sequence, Tuple

# ---------------------------------------------------------------------------
# Shared channel source params
# ---------------------------------------------------------------------------
SOURCE_LINKEDIN = 'LinkedIn'
SOURCE_INDEED = 'Indeed'
SOURCE_ZIPRECRUITER = 'ZipRecruiter'

TENANT_QUALIFIED = 'qualified_staffing'


def get_scout_tenant() -> str:
    """Normalized SCOUT_TENANT (empty string = Myticas/STSI default service)."""
    return (os.environ.get('SCOUT_TENANT') or '').strip().lower()


def is_qualified_tenant() -> bool:
    return get_scout_tenant() == TENANT_QUALIFIED


# ---------------------------------------------------------------------------
# Myticas + STSI (default tenant) — Bullhorn One IDs
# ---------------------------------------------------------------------------
TEARSHEET_OTT = 1231
TEARSHEET_CHI = 1232
TEARSHEET_CLE = 1233
TEARSHEET_VMS = 1239
TEARSHEET_GR = 1474
TEARSHEET_STSI_LINKEDIN = 1531
TEARSHEET_STSI_INDEED = 1640
TEARSHEET_STSI_ZIPRECRUITER = 1641

V2_TEARSHEET_IDS = [
    TEARSHEET_OTT,
    TEARSHEET_CHI,
    TEARSHEET_CLE,
    TEARSHEET_VMS,
    TEARSHEET_GR,
    TEARSHEET_STSI_LINKEDIN,
]

TEARSHEET_MONITOR_MAPPING = {
    TEARSHEET_OTT: 'Sponsored - OTT',
    TEARSHEET_CHI: 'Sponsored - CHI',
    TEARSHEET_CLE: 'Sponsored - CLE',
    TEARSHEET_VMS: 'Sponsored - VMS',
    TEARSHEET_GR: 'Sponsored - GR',
    TEARSHEET_STSI_LINKEDIN: 'Sponsored - STSI - LinkedIn',
    TEARSHEET_STSI_INDEED: 'Sponsored - STSI - Indeed',
    TEARSHEET_STSI_ZIPRECRUITER: 'Sponsored - STSI - Zip Recruiter',
}

V2_FILENAME = 'myticas-job-feed-v2.xml'
V2_FILENAME_DEV = 'myticas-job-feed-v2-dev.xml'
STSI_INDEED_FILENAME = 'stsi-job-feed-indeed.xml'
STSI_INDEED_FILENAME_DEV = 'stsi-job-feed-indeed-dev.xml'
STSI_ZIPRECRUITER_FILENAME = 'stsi-job-feed-ziprecruiter.xml'
STSI_ZIPRECRUITER_FILENAME_DEV = 'stsi-job-feed-ziprecruiter-dev.xml'

V2_PUBLISHER_TITLE = 'Myticas Consulting'
V2_PUBLISHER_LINK = 'https://www.myticas.com'
STSI_PUBLISHER_TITLE = 'STSI'
STSI_PUBLISHER_LINK = 'https://www.stsigroup.com'

CHANNEL_FEEDS = (
    {
        'key': 'stsi_indeed',
        'tearsheet_ids': [TEARSHEET_STSI_INDEED],
        'source_channel': SOURCE_INDEED,
        'filename': STSI_INDEED_FILENAME,
        'filename_dev': STSI_INDEED_FILENAME_DEV,
        'allow_empty': True,
        'publisher_title': STSI_PUBLISHER_TITLE,
        'publisher_link': STSI_PUBLISHER_LINK,
    },
    {
        'key': 'stsi_ziprecruiter',
        'tearsheet_ids': [TEARSHEET_STSI_ZIPRECRUITER],
        'source_channel': SOURCE_ZIPRECRUITER,
        'filename': STSI_ZIPRECRUITER_FILENAME,
        'filename_dev': STSI_ZIPRECRUITER_FILENAME_DEV,
        'allow_empty': True,
        'publisher_title': STSI_PUBLISHER_TITLE,
        'publisher_link': STSI_PUBLISHER_LINK,
    },
)

# ---------------------------------------------------------------------------
# Qualified Staffing — IDs filled when corp tearsheets exist
# ---------------------------------------------------------------------------
# Novo UI find-results path: /tearsheet/{id}?name=...
# Confirmed 19 Sep 2026 from Qualified corp (clp2rd) network payloads.
# Jobs were still 0; do not enable SFTP upload until Bullhorn login works.
QUALIFIED_TEARSHEET_INDEED = 2
QUALIFIED_TEARSHEET_ZIPRECRUITER = 3
QUALIFIED_TEARSHEET_LINKEDIN = 4

QUALIFIED_V2_TEARSHEET_IDS: List[int] = [
    QUALIFIED_TEARSHEET_LINKEDIN,
]

QUALIFIED_TEARSHEET_MONITOR_MAPPING: Dict[int, str] = {
    QUALIFIED_TEARSHEET_INDEED: 'Sponsored - Indeed',
    QUALIFIED_TEARSHEET_ZIPRECRUITER: 'Sponsored - ZipRecruiter',
    QUALIFIED_TEARSHEET_LINKEDIN: 'Sponsored - LinkedIn',
}

QUALIFIED_V2_FILENAME = 'qualified-job-feed-v2.xml'
QUALIFIED_V2_FILENAME_DEV = 'qualified-job-feed-v2-dev.xml'
QUALIFIED_INDEED_FILENAME = 'qualified-job-feed-indeed.xml'
QUALIFIED_INDEED_FILENAME_DEV = 'qualified-job-feed-indeed-dev.xml'
QUALIFIED_ZIP_FILENAME = 'qualified-job-feed-ziprecruiter.xml'
QUALIFIED_ZIP_FILENAME_DEV = 'qualified-job-feed-ziprecruiter-dev.xml'

QUALIFIED_PUBLISHER_TITLE = 'Qualified Staffing'
QUALIFIED_PUBLISHER_LINK = 'https://www.q-staffing.com'
QUALIFIED_APPLY_HOST = 'qualified.scoutgenius.ai'
QUALIFIED_APPLY_EMAIL = 'apply@q-staffing.com'

# Channel feeds. IDs are the Qualified Novo tearsheets above.
QUALIFIED_CHANNEL_FEEDS = (
    {
        'key': 'qualified_indeed',
        'tearsheet_ids': [QUALIFIED_TEARSHEET_INDEED],
        'source_channel': SOURCE_INDEED,
        'filename': QUALIFIED_INDEED_FILENAME,
        'filename_dev': QUALIFIED_INDEED_FILENAME_DEV,
        'allow_empty': True,
        'publisher_title': QUALIFIED_PUBLISHER_TITLE,
        'publisher_link': QUALIFIED_PUBLISHER_LINK,
    },
    {
        'key': 'qualified_ziprecruiter',
        'tearsheet_ids': [QUALIFIED_TEARSHEET_ZIPRECRUITER],
        'source_channel': SOURCE_ZIPRECRUITER,
        'filename': QUALIFIED_ZIP_FILENAME,
        'filename_dev': QUALIFIED_ZIP_FILENAME_DEV,
        'allow_empty': True,
        'publisher_title': QUALIFIED_PUBLISHER_TITLE,
        'publisher_link': QUALIFIED_PUBLISHER_LINK,
    },
)


# ---------------------------------------------------------------------------
# Tenant accessors (prefer these over the Myticas module constants)
# ---------------------------------------------------------------------------

def get_v2_tearsheet_ids() -> List[int]:
    if is_qualified_tenant():
        return list(QUALIFIED_V2_TEARSHEET_IDS)
    return list(V2_TEARSHEET_IDS)


def get_channel_feeds() -> Tuple[dict, ...]:
    if is_qualified_tenant():
        return tuple(dict(cfg) for cfg in QUALIFIED_CHANNEL_FEEDS)
    return tuple(dict(cfg) for cfg in CHANNEL_FEEDS)


def get_tearsheet_monitor_mapping() -> Dict[int, str]:
    if is_qualified_tenant():
        return dict(QUALIFIED_TEARSHEET_MONITOR_MAPPING)
    return dict(TEARSHEET_MONITOR_MAPPING)


def get_v2_filenames() -> Tuple[str, str]:
    """Return (production_filename, development_filename) for the v2 feed."""
    if is_qualified_tenant():
        return QUALIFIED_V2_FILENAME, QUALIFIED_V2_FILENAME_DEV
    return V2_FILENAME, V2_FILENAME_DEV


def get_v2_publisher() -> Tuple[str, str]:
    """Return (publisher_title, publisher_link) for the v2 feed header."""
    if is_qualified_tenant():
        return QUALIFIED_PUBLISHER_TITLE, QUALIFIED_PUBLISHER_LINK
    return V2_PUBLISHER_TITLE, V2_PUBLISHER_LINK


def get_default_apply_host() -> str:
    """Hostname (no scheme) for apply URLs when company is not STSI-mapped."""
    if is_qualified_tenant():
        return QUALIFIED_APPLY_HOST
    return 'apply.myticas.com'


def get_default_apply_email() -> str:
    if is_qualified_tenant():
        return QUALIFIED_APPLY_EMAIL
    return 'apply@myticas.com'


def indeed_native_publish_enabled() -> bool:
    """True when Indeed tearsheet Plan B (native CFC Publish) is live.

    Always False on the Qualified tenant until that corp has its own BH_UI_*
    and tearsheet wiring. When enabled on Myticas/STSI, the Indeed XML channel
    feed must not also syndicate the same tearsheet jobs.
    """
    if is_qualified_tenant():
        return False
    return os.environ.get('INDEED_TEARSHEET_PUBLISH_ENABLED', 'false').lower() in (
        '1', 'true', 'yes', 'on',
    )


def _is_indeed_channel_key(key: str) -> bool:
    return key in ('stsi_indeed', 'qualified_indeed') or key.endswith('_indeed')


def channel_feeds_for_upload() -> Tuple[dict, ...]:
    """Channel feeds to generate/upload this cycle.

    When Indeed native Plan B is enabled (Myticas/STSI only), the Indeed XML
    feed is still uploaded but forced empty so XML syndication retires without
    leaving a stale file that dual-lists against CFC Publish.
    """
    out = []
    park_indeed = indeed_native_publish_enabled()
    for cfg in get_channel_feeds():
        item = dict(cfg)
        if park_indeed and _is_indeed_channel_key(cfg.get('key', '')):
            item['force_empty'] = True
        out.append(item)
    return tuple(out)


def all_xml_feed_tearsheet_ids() -> List[int]:
    """Tearsheets covered by every published XML feed for this tenant.

    Used by the 120-hour reference-number refresh so Indeed/Zip-only jobs
    rotate refs alongside LinkedIn/v2 — not only the default V2 set.
    When Indeed native Plan B is enabled on Myticas, tearsheet 1640 is still
    included here so reference numbers keep rotating for CFC-published jobs.
    """
    ids = list(get_v2_tearsheet_ids())
    for feed_cfg in get_channel_feeds():
        for tid in feed_cfg['tearsheet_ids']:
            if tid not in ids:
                ids.append(tid)
    # Myticas native Indeed still needs 1640 in the rotation set even when the
    # XML channel is parked empty.
    if not is_qualified_tenant() and indeed_native_publish_enabled():
        if TEARSHEET_STSI_INDEED not in ids:
            ids.append(TEARSHEET_STSI_INDEED)
    return ids


def feeds_configured_for_tenant() -> bool:
    """True when this tenant has at least one tearsheet ID to publish."""
    return bool(all_xml_feed_tearsheet_ids())
