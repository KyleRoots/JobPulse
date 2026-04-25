"""
Super-admin operational health dashboard route.

Surfaces a holistic view of the platform (database, scheduler, Bullhorn auth,
OpenAI, vetting queue, SFTP uploads, OneDrive token). Lives under the parent
Scout Genius brand area — this is NOT a per-module page, so it deliberately
inherits from `base_layout.html` only and does not opt into module themes.

Authorization: super-admin (`current_user.is_admin`) only. Anonymous users
are redirected to login by `@login_required`; authenticated non-admins get
a 403 from `_require_admin()`.
"""

import logging

from flask import Blueprint, render_template, jsonify, abort
from flask_login import login_required, current_user

from services.admin_health_service import AdminHealthService

logger = logging.getLogger(__name__)

admin_health_bp = Blueprint('admin_health', __name__, url_prefix='/admin/health')


def _require_admin():
    """Mirror of routes/automations._require_admin — keep gates consistent."""
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)


@admin_health_bp.route('/', methods=['GET'])
@login_required
def health_dashboard():
    """Render the operational health tile grid."""
    _require_admin()
    svc = AdminHealthService()
    tiles = svc.collect_all()
    overall = svc.overall_status(tiles)
    return render_template(
        'admin_health.html',
        active_page='admin_health',
        tiles=tiles,
        overall_status=overall,
        last_checked=svc._now_iso,
    )


@admin_health_bp.route('/data', methods=['GET'])
@login_required
def health_data():
    """JSON payload for AJAX auto-refresh of the dashboard tiles."""
    _require_admin()
    svc = AdminHealthService()
    tiles = svc.collect_all()
    return jsonify({
        'tiles': [t.to_dict() for t in tiles],
        'overall_status': svc.overall_status(tiles),
        'last_checked': svc._now_iso,
    })
