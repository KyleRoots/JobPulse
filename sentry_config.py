"""
Sentry SDK initialization for JobPulse.

Captures unhandled exceptions and enables performance tracing on all Flask
routes. Completely disabled when SENTRY_DSN is not set (local dev / CI) or
when the app environment is not 'production'.

Env vars:
    SENTRY_DSN                   – Project DSN (required to enable)
    SENTRY_TRACES_SAMPLE_RATE    – Fraction of requests traced (0.0–1.0, default 0.2)
    SENTRY_PROFILES_SAMPLE_RATE  – Fraction of traced requests profiled (default 0.1)
"""

import os
import logging

logger = logging.getLogger(__name__)


def init_sentry(app):
    """Initialise Sentry if DSN is configured and environment is production.

    Must be called **before** blueprint registration so the SDK middleware
    wraps every route.

    Returns True if Sentry was initialised, False otherwise.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    environment = app.config.get("ENVIRONMENT", "development")

    if not dsn:
        logger.info("Sentry disabled – SENTRY_DSN not set")
        return False

    if environment != "production":
        logger.info(
            "Sentry disabled – environment is '%s' (requires 'production')",
            environment,
        )
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        traces_sample_rate = float(
            os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.2")
        )
        profiles_sample_rate = float(
            os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.1")
        )

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=os.environ.get("GIT_SHA", "unknown"),
            integrations=[
                FlaskIntegration(),
                SqlalchemyIntegration(),
            ],
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            # Scrub sensitive headers automatically
            send_default_pii=False,
        )

        logger.info(
            "Sentry initialised (traces=%.0f%%, profiles=%.0f%%)",
            traces_sample_rate * 100,
            profiles_sample_rate * 100,
        )
        return True

    except ImportError:
        logger.warning(
            "sentry-sdk not installed – Sentry integration skipped"
        )
        return False
    except Exception as exc:
        logger.error("Failed to initialise Sentry: %s", exc)
        return False
