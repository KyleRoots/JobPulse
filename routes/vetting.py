"""
Vetting Routes Blueprint — thin orchestrator.

This module defines the ``vetting_bp`` Flask blueprint and registers the
``scout_vetting`` module guard. All route handlers themselves live in the
``routes/vetting_handlers/`` package; the side-effect imports at the bottom
of this file pull each submodule in so its ``@vetting_bp.route(...)``
decorators register on the shared blueprint.

Backward compatibility:
- ``vetting_bp`` and ``get_db`` are still importable from ``routes.vetting``,
  matching the prior public surface (``app.py`` imports ``vetting_bp``;
  earlier handler bodies did ``from app import db`` via ``get_db()``).
- All registered endpoint names are unchanged (``vetting.vetting_settings``,
  ``vetting.embedding_audit``, etc.), so every ``url_for('vetting.X')``
  call in templates and other routes keeps resolving without modification.

Submodule layout (see routes/vetting_handlers/__init__.py for details):
- settings          — vetting settings page + save + manual health-check
- dispatch          — manual run / rescreen / re-vet / clean-slate operations
- diagnostics       — diagnostic JSON endpoints + activity monitor + lock/note ops
- email             — test notification emails (qualified candidate + digest)
- job_requirements  — per-job custom requirements / threshold / AI refresh
- embedding_audit   — embedding-filter audit page, CSV exports, retry controls
"""
from flask import Blueprint

from routes import register_module_guard

vetting_bp = Blueprint('vetting', __name__)
register_module_guard(vetting_bp, 'scout_vetting')


def get_db():
    """Get database instance from app context.

    Re-exported here so legacy ``from routes.vetting import get_db`` callers
    keep working. The handler submodules import the same helper from
    ``routes.vetting_handlers._shared`` to avoid circular-import risk.
    """
    from app import db
    return db


# ─── Register handlers via side-effect imports ───
# These imports MUST stay at the bottom: each submodule does
# ``from routes.vetting import vetting_bp`` and decorates handlers on it.
# Defining vetting_bp above first lets that partial-load reference resolve.
from routes.vetting_handlers import (  # noqa: E402, F401
    settings,
    dispatch,
    diagnostics,
    email,
    job_requirements,
    embedding_audit,
)
