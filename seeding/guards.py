"""Production-database safety guard for fresh deployments.

Halts boot if a production deploy points at an empty database without an
explicit `ALLOW_FRESH_PROD_SEED=true` override, preventing the seed
function from silently populating defaults (every toggle off, no users,
no history) over previously-configured production state.
"""

import os
import logging

logger = logging.getLogger(__name__)


class FreshProductionDatabaseError(RuntimeError):
    """Raised when the production app boots against a database with no
    user-set state. Halts startup so an empty database cannot be silently
    seeded with default settings (every toggle off, no users, no history).

    Override by setting ALLOW_FRESH_PROD_SEED=true. Use ONLY for the very
    first deploy of a brand-new environment, then unset.
    """
    pass


def check_fresh_production_database_guard(db):
    """Halt boot if production points at an empty database without an
    explicit override.

    This catches the failure mode where the deployed app gets repointed at
    a fresh PostgreSQL database (e.g. infrastructure swap, reprovision,
    DATABASE_URL change). Without this guard, the seed function would
    silently populate the empty database with default values (vetting
    disabled, recruiter emails off, SFTP off) and the operator would have
    no signal that historical data is gone.

    Behavior:
      - Development/test environments: no-op (empty DB on first run is
        normal in dev).
      - Production + DB has data: no-op.
      - Production + empty DB + ALLOW_FRESH_PROD_SEED=true: log a loud
        warning and proceed (intentional first deploy).
      - Production + empty DB + no override: raise FreshProductionDatabaseError
        and halt boot.

    "Empty" is defined as: zero rows in BOTH the User table AND the
    VettingConfig table. Both must be empty — a partial state still
    proceeds normally so we never block a deploy that has any meaningful
    user data.

    Args:
        db: SQLAlchemy database instance (must already have schema migrated).

    Raises:
        FreshProductionDatabaseError: if production + empty DB + no override.
    """
    # Late import so test patches on `seed_database.is_production_environment`
    # and `seed_database._is_test_environment` are observed at call time.
    import seed_database

    if not seed_database.is_production_environment():
        return

    # Skip when running under pytest / a Flask test config. Extracted to a
    # module-level helper so the guard tests can patch it to False to
    # exercise the real production-path logic.
    if seed_database._is_test_environment():
        return

    try:
        from models import User, VettingConfig
    except ImportError:
        logger.debug("ℹ️ Models not importable yet — skipping fresh-DB guard")
        return

    try:
        from models import GlobalSettings
    except ImportError:
        pass  # GlobalSettings optional — guard still runs on the two core tables

    try:
        user_count = db.session.query(User).count()
        vetting_count = db.session.query(VettingConfig).count()
        try:
            global_settings_count = db.session.query(GlobalSettings).count()
        except Exception:
            global_settings_count = 0
    except Exception as e:
        # If the query fails (e.g. tables not migrated yet), don't block
        # the deploy — the schema bootstrap path needs to run.
        logger.warning(
            f"⚠️ Fresh-DB guard could not query state ({e!s}); skipping check"
        )
        return

    if user_count > 0 or vetting_count > 0 or global_settings_count > 0:
        # Has any pre-existing state — normal deploy path.
        logger.debug(
            f"ℹ️ Fresh-DB guard: DB has data "
            f"(users={user_count}, vetting_config={vetting_count}, "
            f"global_settings={global_settings_count}) — normal deploy path"
        )
        return

    override = os.environ.get('ALLOW_FRESH_PROD_SEED', '').strip().lower()
    if override == 'true':
        logger.warning(
            "🚨 FRESH PRODUCTION DATABASE DETECTED — proceeding because "
            "ALLOW_FRESH_PROD_SEED=true is set. The seed function will "
            "populate this empty database with default values. After this "
            "deploy succeeds, REMOVE the ALLOW_FRESH_PROD_SEED env var so "
            "the guard is active for subsequent deploys."
        )
        return

    msg = (
        "\n"
        "================================================================\n"
        "🛑 ABORTING BOOT: production database appears to be empty.\n"
        "================================================================\n"
        f"  user_count = {user_count}\n"
        f"  vetting_config_count = {vetting_count}\n"
        f"  global_settings_count = {global_settings_count}\n"
        "\n"
        "This means the deployed application is pointing at a fresh\n"
        "PostgreSQL database with no historical data. Continuing would\n"
        "silently seed default values for every setting (Scout Screening\n"
        "OFF, Recruiter Emails OFF, SFTP OFF, etc.), erasing the operator's\n"
        "previous configuration.\n"
        "\n"
        "Most likely causes:\n"
        "  1. The DATABASE_URL secret on the deployment was changed or\n"
        "     reprovisioned and now points at a different database.\n"
        "  2. The previous database was deleted or replaced by infrastructure.\n"
        "  3. This is a legitimate first deploy of a brand-new environment.\n"
        "\n"
        "What to do:\n"
        "  - If you expect existing data: STOP. Verify the deployment\n"
        "    DATABASE_URL points at the correct database before redeploying.\n"
        "    Do not unblock this guard until you have confirmed the source\n"
        "    of truth.\n"
        "  - If this truly IS a first deploy of a new environment: set the\n"
        "    deployment env var ALLOW_FRESH_PROD_SEED=true, redeploy ONCE,\n"
        "    then REMOVE the env var so the guard is active for future\n"
        "    deploys.\n"
        "================================================================\n"
    )
    logger.error(msg)
    raise FreshProductionDatabaseError(msg)
