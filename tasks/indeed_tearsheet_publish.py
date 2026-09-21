"""Scheduled sync: tenant Indeed tearsheet ↔ native Indeed publish."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def sync_indeed_tearsheet_publish():
    """
    Diff Indeed tearsheet membership (1640 Myticas/STSI, 2 Qualified) and
    Publish/Republish/Unpublish via Bullhorn JobBoard CFC (Corporate + Indeed).
    Gated by INDEED_TEARSHEET_PUBLISH_ENABLED.
    """
    from app import app

    with app.app_context():
        try:
            from indeed_publish import IndeedTearsheetPublishService

            result = IndeedTearsheetPublishService().run_sync()
            logger.info(
                'sync_indeed_tearsheet_publish: %s',
                {
                    'enabled': result.get('enabled'),
                    'tearsheet_id': result.get('tearsheet_id'),
                    'published': result.get('published'),
                    'republished': result.get('republished'),
                    'unpublished': result.get('unpublished'),
                    'errors': result.get('errors'),
                    'message': result.get('message'),
                },
            )
        except Exception as exc:
            logger.error('sync_indeed_tearsheet_publish: unexpected error — %s', exc)
