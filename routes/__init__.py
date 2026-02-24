# Routes module for JobPulse
# Flask blueprints organized by feature area

from functools import wraps
from flask import redirect, url_for, jsonify, request
from flask_login import current_user


def admin_required(f):
    """Decorator that restricts access to admin users only.
    Non-admin users are redirected to the Scout Inbound dashboard.
    Must be used AFTER @login_required."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('scout_inbound.scout_inbound_dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def register_admin_guard(blueprint):
    """Register a before_request hook on a blueprint that blocks non-admin users.
    Guards ALL routes in the blueprint. Must be called after blueprint creation."""
    @blueprint.before_request
    def check_admin_access():
        if current_user.is_authenticated and not current_user.is_admin:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('scout_inbound.scout_inbound_dashboard'))
