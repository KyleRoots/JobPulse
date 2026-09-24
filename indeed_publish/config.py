"""Env / constants for Indeed tearsheet → Bullhorn UI JobBoard publish."""

from __future__ import annotations

import os

from feeds.feed_config import (
    QUALIFIED_TEARSHEET_INDEED,
    TEARSHEET_STSI_INDEED,
    is_qualified_tenant,
)

# Legacy module aliases (Myticas/STSI defaults). Prefer config_from_env().
TEARSHEET_ID = TEARSHEET_STSI_INDEED  # 1640
STATE_KEY = 'indeed_tearsheet_publish_state_1640'
LAST_RESULT_KEY = 'indeed_tearsheet_publish_last_result'
NOTIFY_STAMP_KEY = 'indeed_tearsheet_publish_last_notify'

DEFAULT_BASE_URL = 'https://cls45.bullhornstaffing.com'
DEFAULT_PRIVATE_LABEL_ID_MYTICAS = '52989'
DEFAULT_PRIVATE_LABEL_ID_QUALIFIED = '51284'
DEFAULT_JOB_URL_TEMPLATE_MYTICAS = 'https://myticas.com/jobs/{job_id}'
# ID-only career portal path; redirects to the SEO slug URL.
DEFAULT_JOB_URL_TEMPLATE_QUALIFIED = 'https://jobs.q-staffing.com/jobs/{job_id}'
DEFAULT_NOTIFY_EMAIL = 'kroots@myticas.com'


def env_flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or '').strip().lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'on')


def config_from_env() -> dict:
    """Tenant-aware Indeed native publish config.

    Myticas/STSI: tearsheet 1640, private label 52989, myticas.com job URLs,
    REPUBLISH for new membership and fingerprint changes.

    Qualified: tearsheet 2, private label 51284, jobs.q-staffing.com job URLs
    (ID-only path that redirects to the SEO slug), ADDCHANGE for first
    membership publish and REPUBLISH for later fingerprint updates.
    """
    qualified = is_qualified_tenant()
    tearsheet_id = (
        QUALIFIED_TEARSHEET_INDEED if qualified else TEARSHEET_STSI_INDEED
    )
    default_pl = (
        DEFAULT_PRIVATE_LABEL_ID_QUALIFIED
        if qualified
        else DEFAULT_PRIVATE_LABEL_ID_MYTICAS
    )
    # Prefer explicit override. Qualified default is the ID-only portal URL
    # Bullhorn/Indeed expect (e.g. .../jobs/79341 → SEO redirect). Empty
    # string is still allowed via BH_CAREER_PORTAL_JOB_URL_TEMPLATE='' if a
    # corp must publish without a jobUrl.
    if 'BH_CAREER_PORTAL_JOB_URL_TEMPLATE' in os.environ:
        job_url_template = (os.environ.get('BH_CAREER_PORTAL_JOB_URL_TEMPLATE') or '').strip()
    elif qualified:
        job_url_template = DEFAULT_JOB_URL_TEMPLATE_QUALIFIED
    else:
        job_url_template = DEFAULT_JOB_URL_TEMPLATE_MYTICAS

    return {
        'enabled': env_flag('INDEED_TEARSHEET_PUBLISH_ENABLED', False),
        'username': (os.environ.get('BH_UI_USERNAME') or '').strip(),
        'password': os.environ.get('BH_UI_PASSWORD') or '',
        'base_url': (os.environ.get('BH_UI_BASE_URL') or DEFAULT_BASE_URL).rstrip('/'),
        'private_label_id': (
            os.environ.get('BH_UI_PRIVATE_LABEL_ID') or default_pl
        ).strip(),
        'encryption_key': (os.environ.get('BH_UI_ENCRYPTION_KEY') or 'novo').strip(),
        'job_url_template': job_url_template,
        'notify_email': (
            os.environ.get('INDEED_TEARSHEET_PUBLISH_NOTIFY_EMAIL') or DEFAULT_NOTIFY_EMAIL
        ).strip(),
        'current_user_id': (os.environ.get('BH_UI_CURRENT_USER_ID') or '').strip() or None,
        'tearsheet_id': tearsheet_id,
        'state_key': f'indeed_tearsheet_publish_state_{tearsheet_id}',
        'last_result_key': LAST_RESULT_KEY,
        'notify_stamp_key': NOTIFY_STAMP_KEY,
        # Qualified live capture: first publish after unpublish/new = ADDCHANGE;
        # Myticas capture: REPUBLISH for both membership add and content refresh.
        'membership_publish_operation': 'ADDCHANGE' if qualified else 'REPUBLISH',
        'republish_operation': 'REPUBLISH',
    }
