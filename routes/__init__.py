# Routes module for JobPulse
# Flask blueprints organized by feature area

from functools import wraps
from flask import redirect, url_for, jsonify, request
from flask_login import current_user


MODULE_ROUTES = {
    'scout_inbound': 'scout_inbound.scout_inbound_dashboard',
    'scout_screening': 'vetting.vetting_settings',
    'scout_vetting': 'vetting.vetting_settings',
}


def _get_user_landing():
    """Return the URL for the first module the user has access to, or login."""
    if not current_user.is_authenticated:
        return url_for('auth.login')
    if current_user.is_admin:
        return url_for('dashboard.dashboard_redirect')
    modules = current_user.get_modules()
    for mod in modules:
        endpoint = MODULE_ROUTES.get(mod)
        if endpoint:
            return url_for(endpoint)
    return url_for('auth.login')


def admin_required(f):
    """Decorator that restricts access to admin users only.
    Non-admin users are redirected to their landing page.
    Must be used AFTER @login_required."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(_get_user_landing())
        return f(*args, **kwargs)
    return decorated_function


def module_required(*module_names):
    """Decorator that restricts access to users subscribed to at least one of the given modules.
    Admins always have access. Must be used AFTER @login_required."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if current_user.is_admin:
                return f(*args, **kwargs)
            if any(current_user.has_module(m) for m in module_names):
                return f(*args, **kwargs)
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Module access required'}), 403
            return redirect(_get_user_landing())
        return decorated_function
    return decorator


def register_admin_guard(blueprint):
    """Register a before_request hook on a blueprint that blocks non-admin users.
    Guards ALL routes in the blueprint. Must be called after blueprint creation."""
    @blueprint.before_request
    def check_admin_access():
        if current_user.is_authenticated and not current_user.is_admin:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(_get_user_landing())


def register_module_guard(blueprint, *module_names):
    """Register a before_request hook that allows access only if the user has at least
    one of the specified modules. Admins always pass. Unauthenticated requests pass
    through (handled by @login_required on individual routes)."""
    @blueprint.before_request
    def check_module_access():
        if not current_user.is_authenticated:
            return
        if current_user.is_admin:
            return
        if any(current_user.has_module(m) for m in module_names):
            return
        if request.is_json or request.path.startswith('/api/'):
            return jsonify({'error': 'Module access required'}), 403
        return redirect(_get_user_landing())
