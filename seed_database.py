"""
Database Seeding Script for Scout Genius Application

This script provides idempotent database seeding for production deployments.
It creates admin users, SFTP settings, Bullhorn credentials, tearsheet monitors,
and automation configuration automatically from environment variables.

Usage:
    - Automatically runs on app initialization in production
    - Can be run manually: python seed_database.py
    - Safe to run multiple times (idempotent)

Environment Variables:
    Admin User:
    - ADMIN_USERNAME: Admin user username (default: admin)
    - ADMIN_EMAIL: Admin user email (default: kroots@myticas.com)
    - ADMIN_PASSWORD: Admin user password (REQUIRED for production)

    SFTP Configuration (FIRST-RUN BOOTSTRAP ONLY):
    - SFTP_HOSTNAME: SFTP server hostname
    - SFTP_USERNAME: SFTP username
    - SFTP_PASSWORD: SFTP password
    - SFTP_PORT: SFTP port (default: 22)
    - SFTP_DIRECTORY: Upload directory (default: /)

    Bullhorn API Configuration (FIRST-RUN BOOTSTRAP ONLY):
    - BULLHORN_CLIENT_ID: Bullhorn OAuth client ID
    - BULLHORN_CLIENT_SECRET: Bullhorn OAuth client secret
    - BULLHORN_USERNAME: Bullhorn API username
    - BULLHORN_PASSWORD: Bullhorn API password

    NOTE: SFTP and Bullhorn credential env vars are used ONLY to seed empty
    rows on a fresh install. If these rows already exist in the database (i.e.
    the operator has set credentials via the UI), the seed will NEVER overwrite
    them regardless of env var values. To update credentials after initial
    bootstrap, use the Inbound Config settings page in the admin UI.

    System:
    - REPLIT_DEPLOYMENT: Detected automatically (production indicator)

What Gets Seeded:
    ✅ Admin user with credentials from environment
    ✅ SFTP settings for automated uploads (first-run bootstrap only)
    ✅ Bullhorn API credentials for job feed generation (first-run bootstrap only)
    ✅ Bullhorn tearsheet monitors
    ✅ Automation toggles (conservative defaults; user enables via UI)
    ✅ Environment monitoring (production only)

Preservation guarantees (no deploy can overwrite these):
    🔒 SFTP and Bullhorn credential rows (if they exist in the database)
    🔒 Automation toggles (sftp_enabled, automated_uploads_enabled, etc.)
    🔒 All vetting/screening configuration

────────────────────────────────────────────────────────────────────────────
Module structure (Apr 2026 split for maintainability):
    The actual seeder bodies live in the `seeding/` package:
        - seeding.guards     — fresh-prod-DB safety guard
        - seeding.config     — env-var-driven config providers
        - seeding.users      — admin + support-contact + Myticas/STSI users
        - seeding.settings   — GlobalSettings, VettingConfig, monitors,
                               recruiter mappings, built-in automations
        - seeding.migrations — schema migrations + data migrations + audits

    This module keeps:
        - The two environment-detection helpers
          (`is_production_environment`, `_is_test_environment`) — kept here
          as the canonical references because tests patch them via the
          `seed_database.X` namespace.
        - The four module-level constant lists (SUPPORT_CONTACTS_*,
          BUILTIN_AUTOMATIONS, STALE_BUILTIN_*) — kept here so existing
          imports like `from seed_database import SUPPORT_CONTACTS_STSI`
          continue to work unchanged.
        - The `seed_database()` orchestrator and `run_seeding()` runner
          (the same public entry points used by app.py and tests).

    Every public function previously defined here is re-exported below so
    every existing `from seed_database import X` import remains valid.
────────────────────────────────────────────────────────────────────────────
"""

import os
import logging
from datetime import datetime
from werkzeug.security import generate_password_hash  # noqa: F401  (kept for back-compat re-export)

# Configure logging
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Environment-detection helpers (canonical references)
#
# Kept in this module — not in seeding/ — so existing test patches like
#   patch('seed_database.is_production_environment', return_value=False)
#   patch('seed_database._is_test_environment', return_value=False)
# continue to apply at runtime. Code in seeding/* calls these via
# `seed_database.is_production_environment()` (late module reference)
# so the patched value is observed at call time.
# ─────────────────────────────────────────────────────────────

def is_production_environment():
    """
    Detect if running in production environment

    Detection priority:
    1. APP_ENV=production (preferred for custom deployments)
    2. ENVIRONMENT=production (alternative)
    3. REPLIT_DEPLOYMENT exists (Replit-specific)

    Returns:
        bool: True if in production, False otherwise
    """
    app_env = os.environ.get('APP_ENV', '').lower()
    environment = os.environ.get('ENVIRONMENT', '').lower()
    replit_deployment = os.environ.get('REPLIT_DEPLOYMENT')

    return app_env == 'production' or environment == 'production' or replit_deployment is not None


def _is_test_environment():
    """True when running under pytest or a Flask test config.

    Detection is intentionally broad and uses signals that are set BEFORE
    any test module is imported, so the guard never trips during pytest
    collection (which happens before per-test fixtures like conftest.py's
    `app` fixture run):
      - `pytest` present in `sys.modules` — true the moment pytest boots
      - PYTEST_CURRENT_TEST env var — set by pytest during test execution
      - TESTING / FLASK_ENV env vars — set by conftest.py for runtime test code

    This matters in this Replit dev container because APP_ENV=production
    is set at the container level (which would otherwise make the guard
    fire whenever pytest imports the app).
    """
    import sys
    return (
        'pytest' in sys.modules
        or os.environ.get('TESTING', '').lower() == 'true'
        or os.environ.get('FLASK_ENV', '').lower() == 'testing'
        or os.environ.get('PYTEST_CURRENT_TEST') is not None
    )


# ─────────────────────────────────────────────────────────────
# Module-level constants
#
# Kept here (not in seeding/*) so existing imports continue to work:
#   from seed_database import SUPPORT_CONTACTS_STSI
#   from seed_database import SUPPORT_CONTACTS_MYTICAS
#   from seed_database import BUILTIN_AUTOMATIONS
# ─────────────────────────────────────────────────────────────

BUILTIN_AUTOMATIONS = [
    {
        "name": "Resume Re-Parser",
        "description": "Find recently added candidates with empty descriptions but attached resume files, and re-parse the resume text into Bullhorn.",
        "automation_type": "one-time",
        "builtin_key": "resume_reparser",
    },
    {
        "name": "Bulk Field Update",
        "description": "Update one or more fields on all Bullhorn records matching a search query. Supports any entity type (Candidate, JobOrder, etc). Always dry-run first to confirm scope before executing.",
        "automation_type": "one-time",
        "builtin_key": "update_field_bulk",
    },
    {
        "name": "Email Extractor",
        "description": "Find candidates with missing email addresses, download their resume files, and extract email addresses using PDF/DOCX text parsing. Only updates the email field — never touches description or other fields.",
        "automation_type": "one-time",
        "builtin_key": "email_extractor",
    },
    {
        "name": "Occupation/Title Extractor",
        "description": "Find candidates with missing occupation/title fields, download their resume files, and use AI to extract the most recent job title. Updates the occupation field in Bullhorn. Runs automatically as part of the 15-minute candidate cleanup cycle.",
        "automation_type": "one-time",
        "builtin_key": "occupation_extractor",
    },
    {
        "name": "Cleanup AI Notes",
        "description": "Delete AI vetting notes from candidate records. Finds notes matching actions: AI Vetting, AI Resume Summary, AI Vetted.",
        "automation_type": "one-time",
        "builtin_key": "cleanup_ai_notes",
    },
    {
        "name": "Cleanup Duplicate Notes",
        "description": "Remove duplicate 'AI Vetting - Not Recommended' notes. Keeps the original (oldest) note and deletes subsequent chained duplicates added within 60 minutes.",
        "automation_type": "one-time",
        "builtin_key": "cleanup_duplicate_notes",
    },
    {
        "name": "Find Zero Match",
        "description": "Find candidates with 'Highest Match Score: 0%' in vetting notes. Useful for identifying zero-score candidates that may need review.",
        "automation_type": "one-time",
        "builtin_key": "find_zero_match",
    },
    {
        "name": "Export Qualified",
        "description": "Export qualified candidates for specific jobs. Fetches all submissions for given job IDs and checks candidate notes for qualifying actions.",
        "automation_type": "one-time",
        "builtin_key": "export_qualified",
    },
    {
        "name": "Sales Rep Sync",
        "description": "Manually trigger Sales Rep display name sync. Resolves CorporateUser IDs in customText3 to display names in customText6. Normally runs automatically every 30 minutes.",
        "automation_type": "one-time",
        "builtin_key": "salesrep_sync",
    },
    {
        "name": "Retry Recruiter Notifications",
        "description": "Find qualified candidates whose recruiter notification email was never sent, and send it now. Defaults to candidates qualified today. Use since_date (YYYY-MM-DD) to extend the lookback window.",
        "automation_type": "one-time",
        "builtin_key": "retry_recruiter_notifications",
    },
    {
        "name": "Screening Quality Audit",
        "description": "AI-powered audit that reviews recent Not Qualified results for scoring errors (recency misfires, platform age violations, false gap claims). Automatically re-screens candidates and updates Bullhorn notes when high-confidence misfires are detected.",
        "automation_type": "one-time",
        "builtin_key": "screening_audit",
    },
    {
        "name": "Duplicate Candidate Merge",
        "description": "Scan all Bullhorn candidates for duplicates (matching email, phone) and auto-merge at 85%+ confidence. Transfers submissions, notes, and files to the primary record, then archives the duplicate. Skips candidates with active placements on both records.",
        "automation_type": "one-time",
        "builtin_key": "duplicate_merge_scan",
    },
]

STALE_BUILTIN_KEYS = {"timestamp_correction"}
STALE_BUILTIN_NAMES = {"LinkedIn Source Updater", "Merge Timestamp Correction"}


SUPPORT_CONTACTS_MYTICAS = [
    {"first_name": "Kyle", "last_name": "Roots", "email": "kroots@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Innocent", "last_name": "Nangoma", "email": "inangoma@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Reem", "last_name": "Kouseibati", "email": "rkouseibati@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Michael", "last_name": "Theodossiou", "email": "michael.theodossiou@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Mike", "last_name": "Palermo", "email": "mpalermo@myticas.com", "department": "MYT-Chicago"},
    {"first_name": "Dominic", "last_name": "Scaletta", "email": "dscaletta@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Matheo", "last_name": "Theodossiou", "email": "matheo.theodossiou@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Adam", "last_name": "Gebara", "email": "agebara@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Amanda", "last_name": "Messina", "email": "amessina@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Dean", "last_name": "Theodossiou", "email": "dtheodossiou@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Nick", "last_name": "Theodossiou", "email": "ntheo@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Runa", "last_name": "Parmar", "email": "rparmar@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Sam", "last_name": "Osman", "email": "sosman@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Chris", "last_name": "Halkai", "email": "chalkai@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Jen", "last_name": "Jones", "email": "jjones@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Lisa", "last_name": "Keirsted", "email": "lisa@myticas.com", "department": "MYT-Ohio"},
    {"first_name": "Bryan", "last_name": "Chinzorig", "email": "bryanc@myticas.com", "department": "MYT-Chicago"},
    {"first_name": "Michael", "last_name": "Wujciak", "email": "mw@myticas.com", "department": "MYT-Chicago"},
    {"first_name": "Reena", "last_name": "Setya", "email": "rs@myticas.com", "department": "MYT-Chicago"},
    {"first_name": "Dan", "last_name": "Sifer", "email": "dsifer@myticas.com", "department": "MYT-Ohio"},
    {"first_name": "Anastasiya", "last_name": "Ivanova", "email": "ai@myticas.com", "department": "MYT-Chicago"},
    {"first_name": "Celine", "last_name": "Blattman", "email": "cblattman@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Anita", "last_name": "Barker", "email": "abarker@myticas.com", "department": "MYT-Ottawa"},
    {"first_name": "Christine", "last_name": "Carter", "email": "ccarter@cloverit.com", "department": "MYT-Clover"},
]

SUPPORT_CONTACTS_STSI = [
    {"first_name": "Kellie", "last_name": "Beveridge", "email": "kbeveridge@q-ptgroup.com", "department": "STS-STSI"},
    {"first_name": "Josh", "last_name": "Bocek", "email": "jbocek@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Veronica", "last_name": "Connolly", "email": "vconnolly@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Tarra", "last_name": "Dziuman", "email": "tdziurman@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Rachelle", "last_name": "Fite", "email": "rfite@q-staffing.com", "department": "STS-STSI"},
    {"first_name": "Dawn", "last_name": "Geistert-Dixon", "email": "dgdixon@q-staffing.com", "department": "STS-STSI"},
    {"first_name": "Julie", "last_name": "Johnson", "email": "jjohnson@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Rachel", "last_name": "Mann", "email": "rmann@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Kellie", "last_name": "Miller", "email": "kmiller@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Samantha", "last_name": "Odeh", "email": "sodeh@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Ryan", "last_name": "Oliver", "email": "roliver@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Tray", "last_name": "Prewitt", "email": "tprewitt@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Michael", "last_name": "Scalzitti", "email": "mscalzitti@stsigroup.com", "department": "STS-STSI"},
    {"first_name": "Emma", "last_name": "Valentine", "email": "evalentine@stsigroup.com", "department": "STS-STSI"},
]


# ─────────────────────────────────────────────────────────────
# Re-exports from the seeding/* package
#
# Order matters: imports below happen AFTER the helpers and constants
# above are defined, so submodules referencing `seed_database.X` via
# late lookup find them populated when their function bodies run.
# ─────────────────────────────────────────────────────────────

from seeding.guards import (  # noqa: E402
    FreshProductionDatabaseError,
    check_fresh_production_database_guard,
)
from seeding.config import (  # noqa: E402
    get_admin_config,
    get_sftp_config,
    get_bullhorn_config,
)
from seeding.users import (  # noqa: E402
    create_admin_user,
    _seed_brand_contacts,
    seed_support_contacts,
    seed_myticas_users,
    seed_stsi_users,
    ensure_module_subscriptions,
)
from seeding.settings import (  # noqa: E402
    seed_global_settings,
    seed_bullhorn_monitors,
    seed_vetting_config,
    seed_environment_monitoring,
    seed_reference_refresh_log,
    seed_recruiter_mappings,
    seed_builtin_automations,
    _default_platform_age_ceilings_json,
    _load_global_screening_prompt,
)
from seeding.migrations import (  # noqa: E402
    run_schema_migrations,
    migrate_legacy_custom_requirements,
    run_vetting_clean_slate,
    log_critical_settings_state,
)


# ─────────────────────────────────────────────────────────────
# Main orchestrator + standalone runner
# ─────────────────────────────────────────────────────────────

def seed_database(db, User):
    """
    Main seeding function - idempotent database initialization

    Args:
        db: SQLAlchemy database instance
        User: User model class

    Returns:
        dict: Summary of seeding results
    """
    # Run schema migrations first
    try:
        run_schema_migrations(db)
    except Exception as e:
        logger.warning(f"⚠️ Schema migrations failed: {str(e)}")

    # FRESH PROD DB GUARD: refuse to silently seed defaults into an empty
    # production database. Set ALLOW_FRESH_PROD_SEED=true to override for
    # legitimate first deploys. See check_fresh_production_database_guard
    # for full rationale.
    check_fresh_production_database_guard(db)

    try:
        null_company_count = db.session.execute(
            db.text("UPDATE \"user\" SET company = 'Myticas Consulting' WHERE company IS NULL")
        ).rowcount
        if null_company_count > 0:
            db.session.commit()
            logger.info(f"🔄 Backfilled company='Myticas Consulting' for {null_company_count} user(s) with NULL company")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Company backfill failed: {str(e)}")

    env_type = "production" if is_production_environment() else "development"
    logger.info(f"🌱 Starting database seeding for {env_type} environment...")

    results = {
        'environment': env_type,
        'admin_created': False,
        'sftp_configured': False,
        'bullhorn_configured': False,
        'monitors_configured': False,
        'errors': []
    }

    try:
        # Create admin user (idempotent)
        admin_user, created = create_admin_user(db, User)
        results['admin_created'] = created
        results['admin_username'] = admin_user.username
        results['admin_email'] = admin_user.email

        # Seed global settings including SFTP and Bullhorn config
        try:
            from models import GlobalSettings
            seed_global_settings(db, GlobalSettings)
            results['sftp_configured'] = True
            results['bullhorn_configured'] = True
        except ImportError:
            logger.debug("ℹ️ GlobalSettings model not found - skipping settings seeding")
        except ValueError as e:
            # Re-raise validation errors (missing secrets in production)
            logger.error(f"❌ Production validation failed: {str(e)}")
            raise
        except Exception as e:
            logger.warning(f"⚠️ Failed to seed global settings: {str(e)}")
            raise

        # Seed Bullhorn monitors
        try:
            from models import BullhornMonitor
            seed_bullhorn_monitors(db, BullhornMonitor)
            results['monitors_configured'] = True
        except ImportError:
            logger.debug("ℹ️ BullhornMonitor model not found - skipping monitor seeding")
        except Exception as e:
            logger.error(f"❌ Failed to seed Bullhorn monitors: {str(e)}")
            raise

        # Seed environment monitoring (production only)
        if is_production_environment():
            seed_environment_monitoring(db)

        # Seed reference refresh log with baseline timestamp
        seed_reference_refresh_log(db)

        # Seed recruiter mappings (LinkedIn tags)
        try:
            from models import RecruiterMapping
            seed_recruiter_mappings(db, RecruiterMapping)
            results['recruiter_mappings_configured'] = True
        except ImportError:
            logger.debug("ℹ️ RecruiterMapping model not found - skipping recruiter mapping seeding")
        except Exception as e:
            logger.warning(f"⚠️ Failed to seed recruiter mappings: {str(e)}")

        # Seed AI Vetting settings (enabled by default in production)
        try:
            from models import VettingConfig
            seed_vetting_config(db, VettingConfig)
            results['vetting_configured'] = True
        except ImportError:
            logger.debug("ℹ️ VettingConfig model not found - skipping vetting config seeding")
        except Exception as e:
            logger.warning(f"⚠️ Failed to seed vetting config: {str(e)}")

        # ONE-TIME CLEAN SLATE: Reset vetting data for fresh start
        # This runs once and sets a flag to prevent re-running
        try:
            run_vetting_clean_slate(db)
        except Exception as e:
            logger.warning(f"⚠️ Vetting clean slate check failed: {str(e)}")

        # Startup audit: log current values of all critical flags
        # This runs AFTER all seeding is complete, so any unexpected changes
        # would be visible in logs for every deploy.
        log_critical_settings_state(db)

        # AUTO-RELEASE: Release any stuck vetting lock on deploy
        # When Render restarts the service, the previous lock becomes stale
        try:
            from models import VettingConfig
            lock = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            if lock and lock.setting_value == 'true':
                lock.setting_value = 'false'
                db.session.commit()
                logger.info("🔓 Auto-released stuck vetting lock on deploy")
            else:
                logger.debug("ℹ️ No stuck vetting lock found")
        except Exception as e:
            logger.warning(f"⚠️ Failed to check vetting lock: {str(e)}")

        seed_builtin_automations(db)

        migrate_legacy_custom_requirements(db)

        seed_support_contacts(db)

        seed_stsi_users(db, User)
        seed_myticas_users(db, User)

        ensure_module_subscriptions(db, User)

        logger.info(f"✅ Database seeding completed successfully for {env_type}")

    except Exception as e:
        error_msg = f"Database seeding failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        results['errors'].append(error_msg)
        raise

    return results


def run_seeding():
    """
    Standalone seeding execution (for manual runs)
    """
    from app import app, db
    from models import User

    with app.app_context():
        results = seed_database(db, User)

        # Print summary
        print("\n" + "="*70)
        print("DATABASE SEEDING SUMMARY")
        print("="*70)
        print(f"Environment: {results['environment']}")
        print(f"Admin Created: {'Yes' if results['admin_created'] else 'No (already exists)'}")
        print(f"Admin Username: {results.get('admin_username', 'N/A')}")
        print(f"Admin Email: {results.get('admin_email', 'N/A')}")
        print(f"SFTP Configured: {'Yes' if results.get('sftp_configured', False) else 'No'}")
        print(f"Bullhorn Configured: {'Yes' if results.get('bullhorn_configured', False) else 'No'}")
        print(f"Monitors Configured: {'Yes' if results.get('monitors_configured', False) else 'No'}")

        if results['errors']:
            print(f"\nErrors: {len(results['errors'])}")
            for error in results['errors']:
                print(f"  - {error}")
        else:
            print("\nStatus: SUCCESS ✅")

        print("="*70 + "\n")


if __name__ == '__main__':
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    run_seeding()
