"""Scheduled Qualified Indeed leak sweep and Monday digest."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_indeed_leak_sweep():
    from app import app

    with app.app_context():
        try:
            from indeed_publish.leak_runtime import run_sweep
            run_sweep()
        except Exception as exc:
            logger.error('indeed leak sweep: unexpected error — %s', exc)


def run_indeed_leak_digest():
    from app import app

    with app.app_context():
        try:
            from indeed_publish.leak_runtime import run_weekly_digest
            run_weekly_digest()
        except Exception as exc:
            logger.error('indeed leak digest: unexpected error — %s', exc)
