"""Env / constants for Indeed tearsheet → Bullhorn UI JobBoard publish."""

from __future__ import annotations

import os

from feeds.feed_config import TEARSHEET_STSI_INDEED

TEARSHEET_ID = TEARSHEET_STSI_INDEED  # 1640

STATE_KEY = 'indeed_tearsheet_publish_state_1640'
LAST_RESULT_KEY = 'indeed_tearsheet_publish_last_result'

DEFAULT_BASE_URL = 'https://cls45.bullhornstaffing.com'
DEFAULT_PRIVATE_LABEL_ID = '52989'
DEFAULT_JOB_URL_TEMPLATE = 'https://myticas.com/jobs/{job_id}'
DEFAULT_NOTIFY_EMAIL = 'kroots@myticas.com'


def env_flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or '').strip().lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'on')


def config_from_env() -> dict:
    return {
        'enabled': env_flag('INDEED_TEARSHEET_PUBLISH_ENABLED', False),
        'username': (os.environ.get('BH_UI_USERNAME') or '').strip(),
        'password': os.environ.get('BH_UI_PASSWORD') or '',
        'base_url': (os.environ.get('BH_UI_BASE_URL') or DEFAULT_BASE_URL).rstrip('/'),
        'private_label_id': (os.environ.get('BH_UI_PRIVATE_LABEL_ID') or DEFAULT_PRIVATE_LABEL_ID).strip(),
        'encryption_key': (os.environ.get('BH_UI_ENCRYPTION_KEY') or 'novo').strip(),
        'job_url_template': (
            os.environ.get('BH_CAREER_PORTAL_JOB_URL_TEMPLATE') or DEFAULT_JOB_URL_TEMPLATE
        ).strip(),
        'notify_email': (
            os.environ.get('INDEED_TEARSHEET_PUBLISH_NOTIFY_EMAIL') or DEFAULT_NOTIFY_EMAIL
        ).strip(),
        'current_user_id': (os.environ.get('BH_UI_CURRENT_USER_ID') or '').strip() or None,
        'tearsheet_id': TEARSHEET_ID,
    }
