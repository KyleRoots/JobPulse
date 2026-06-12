"""Helpers for running scheduled work across all active Bullhorn environments.

Multi-tenant scheduled jobs must run once per active ``BullhornEnvironment``.
``for_each_active_environment`` centralizes that loop with per-environment error
isolation so one tenant's failure never aborts the others.

Single-tenant invariant: with only the default (Myticas) environment seeded,
the callback is invoked exactly once with the default environment — byte-for-byte
the historical single-tenant behavior.
"""
import logging

logger = logging.getLogger(__name__)


def for_each_active_environment(job_name, fn, log=None):
    """Invoke ``fn(environment)`` once per active environment, isolating errors.

    Args:
        job_name: Human-readable job name used in error logs.
        fn: Callable taking a single ``BullhornEnvironment`` (or ``None`` when no
            environments are seeded — the legacy default-credential path).
        log: Optional logger; defaults to this module's logger.

    Returns:
        list[tuple[environment, result]]: ``result`` is ``None`` when that
        environment raised (the exception is logged, not propagated).
    """
    _log = log or logger
    try:
        from models import BullhornEnvironment
        environments = BullhornEnvironment.get_active()
    except Exception as e:
        _log.error(f"{job_name}: failed to list environments, using default path: {e}")
        environments = []

    if not environments:
        # No environments seeded yet — run once on the legacy default-credential
        # path so behavior is unchanged on a fresh / unseeded database.
        environments = [None]

    results = []
    for env in environments:
        env_key = getattr(env, 'key', 'default')
        try:
            results.append((env, fn(env)))
        except Exception as e:
            _log.error(
                f"{job_name}: environment '{env_key}' failed: {e}", exc_info=True
            )
            results.append((env, None))
    return results
