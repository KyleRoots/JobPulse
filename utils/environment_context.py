"""Per-request current-environment resolution and query scoping (Task #100 Step 6).

Multi-tenant views must show only the rows belonging to the viewer's
environment, while a super-admin gets an all-environment roll-up. The hard
invariant is that with a SINGLE active environment (the live Myticas tenant)
every view renders byte-for-byte as before. ``scope_query`` enforces that by
no-opping whenever there is at most one active environment — the SQL is then
identical to the pre-multi-tenant query.

Resolution priority for the current environment:
    1. ``current_user.environment_id`` (an explicitly-assigned tenant user)
    2. the request host -> Brand -> environment (apply/support sub-domains)
    3. the default (Myticas) environment

All paths are fail-soft: any error resolves to the default environment so a
resolver hiccup can never break a page.
"""
import logging

from flask import g, has_request_context, request

logger = logging.getLogger(__name__)


def _active_environment_count():
    """Number of active environments, memoized on ``g`` for the request."""
    if has_request_context() and hasattr(g, '_active_env_count'):
        return g._active_env_count
    count = 0
    try:
        from models import BullhornEnvironment
        count = BullhornEnvironment.query.filter_by(is_active=True).count()
    except Exception as e:
        logger.debug(f"active_environment_count failed, assuming single env: {e}")
        count = 0
    if has_request_context():
        g._active_env_count = count
    return count


def resolve_current_environment():
    """Resolve the BullhornEnvironment for the current request (fail-soft).

    Returns ``None`` only when no environment is seeded.
    """
    try:
        from models import BullhornEnvironment, Brand
        from flask_login import current_user

        # 1) Explicit per-user environment assignment.
        try:
            if getattr(current_user, 'is_authenticated', False):
                env_id = getattr(current_user, 'environment_id', None)
                if env_id:
                    env = BullhornEnvironment.query.get(env_id)
                    if env is not None:
                        return env
        except Exception:
            pass

        # 2) Host -> brand -> environment.
        try:
            if has_request_context() and request.host:
                brand = Brand.resolve_for_host(request.host)
                if brand is not None and brand.environment_id:
                    env = BullhornEnvironment.query.get(brand.environment_id)
                    if env is not None:
                        return env
        except Exception:
            pass

        # 3) Default environment.
        return BullhornEnvironment.get_default()
    except Exception as e:
        logger.debug(f"resolve_current_environment failed: {e}")
        return None


def current_environment():
    """Return the request's current environment, memoized on ``g``."""
    if has_request_context():
        if not hasattr(g, '_current_environment'):
            g._current_environment = resolve_current_environment()
        return g._current_environment
    return resolve_current_environment()


def _is_super_admin():
    try:
        from flask_login import current_user
        return bool(getattr(current_user, 'is_admin', False))
    except Exception:
        return False


def scope_query(query, model):
    """Scope a query to the current environment, preserving single-env parity.

    No-ops (returns the query unchanged) when:
      * there is at most one active environment (single-tenant invariant — the
        resulting SQL is byte-for-byte identical to the legacy query), OR
      * the viewer is a super-admin (all-environment roll-up), OR
      * the model has no ``environment_id`` column, OR
      * the current environment cannot be resolved.

    Otherwise filters ``model.environment_id == <current env id>``.
    """
    try:
        if not hasattr(model, 'environment_id'):
            return query
        if _active_environment_count() <= 1:
            return query
        if _is_super_admin():
            return query
        env = current_environment()
        if env is None:
            return query
        return query.filter(model.environment_id == env.id)
    except Exception as e:
        logger.debug(f"scope_query no-op (error): {e}")
        return query
