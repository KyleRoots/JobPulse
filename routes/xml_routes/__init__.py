import logging
from flask import Blueprint
from routes import register_admin_guard

logger = logging.getLogger(__name__)

xml_routes_bp = Blueprint('xml_routes', __name__)
register_admin_guard(xml_routes_bp)

ALLOWED_EXTENSIONS = {'xml'}


def allowed_file(filename):
    """Check if file has allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


from routes.xml_routes import feed_ops     # noqa: E402, F401
from routes.xml_routes import automation   # noqa: E402, F401
from routes.xml_routes import test_center  # noqa: E402, F401
