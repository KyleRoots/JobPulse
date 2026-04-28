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
"""

import os
import json
import logging
import re
from datetime import datetime
from werkzeug.security import generate_password_hash

# Configure logging
logger = logging.getLogger(__name__)


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
    if not is_production_environment():
        return

    # Skip when running under pytest / a Flask test config. Extracted to a
    # module-level helper so the guard tests can patch it to False to
    # exercise the real production-path logic.
    if _is_test_environment():
        return

    try:
        from models import User, VettingConfig
    except ImportError:
        logger.debug("ℹ️ Models not importable yet — skipping fresh-DB guard")
        return

    try:
        user_count = db.session.query(User).count()
        vetting_count = db.session.query(VettingConfig).count()
    except Exception as e:
        # If the query fails (e.g. tables not migrated yet), don't block
        # the deploy — the schema bootstrap path needs to run.
        logger.warning(
            f"⚠️ Fresh-DB guard could not query state ({e!s}); skipping check"
        )
        return

    if user_count > 0 or vetting_count > 0:
        # Has any pre-existing state — normal deploy path.
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


def get_admin_config():
    """
    Get admin user configuration from environment variables
    
    Returns:
        dict: Admin user configuration
    """
    # Get configuration from environment with safe defaults
    username = os.environ.get('ADMIN_USERNAME', 'admin')
    email = os.environ.get('ADMIN_EMAIL', 'kroots@myticas.com')
    password = os.environ.get('ADMIN_PASSWORD')
    
    # In production, password MUST be set via environment variable
    if is_production_environment() and not password:
        logger.error("❌ ADMIN_PASSWORD environment variable is required for production deployment")
        logger.error("Please set ADMIN_PASSWORD in your deployment secrets")
        raise ValueError("ADMIN_PASSWORD is required for production deployment")
    
    # Development fallback (only if not in production)
    if not password:
        password = 'admin123'  # Development-only default
        logger.warning("⚠️ Using default password for development - DO NOT use in production!")
    
    return {
        'username': username,
        'email': email,
        'password': password,
        'is_admin': True
    }


def create_admin_user(db, User):
    """
    Create admin user if it doesn't exist, or update if credentials changed (idempotent with rotation)
    
    Args:
        db: SQLAlchemy database instance
        User: User model class
        
    Returns:
        tuple: (user, created) - User object and boolean indicating if created
    """
    config = get_admin_config()
    
    # First, try to find existing admin user by is_admin=True (independent of current env values)
    # This ensures we update the same admin even if username/email change
    existing_user = User.query.filter_by(is_admin=True).first()
    
    # If no admin found by is_admin, try to find by current env values (for backwards compatibility)
    if not existing_user:
        existing_user = User.query.filter(
            (User.username == config['username']) | (User.email == config['email'])
        ).first()
    
    if existing_user:
        logger.info(f"✅ Admin user already exists: {existing_user.username} ({existing_user.email})")
        
        # Track what was updated for logging
        updates = []
        
        # Update admin status if needed
        if not existing_user.is_admin:
            existing_user.is_admin = True
            updates.append("admin status")
        
        # Update username if changed
        if existing_user.username != config['username']:
            old_username = existing_user.username
            existing_user.username = config['username']
            updates.append(f"username ({old_username} -> {config['username']})")
        
        # Update email if changed
        if existing_user.email != config['email']:
            old_email = existing_user.email
            existing_user.email = config['email']
            updates.append(f"email ({old_email} -> {config['email']})")
        
        # Always update password from current environment (enables rotation)
        # Note: We can't check if password changed (it's hashed), so always update
        existing_user.set_password(config['password'])
        updates.append("password (rotated from env)")
        
        if updates:
            db.session.commit()
            logger.info(f"🔄 Updated admin user: {', '.join(updates)}")
        else:
            logger.info(f"✅ Admin user up to date (no changes needed)")
        
        return existing_user, False
    
    # Create new admin user
    try:
        admin_user = User(
            username=config['username'],
            email=config['email'],
            is_admin=True,
            company='Myticas Consulting',
            created_at=datetime.utcnow()
        )
        admin_user.set_password(config['password'])
        
        db.session.add(admin_user)
        db.session.commit()
        
        env_type = "production" if is_production_environment() else "development"
        logger.info(f"✅ Created new admin user in {env_type}: {admin_user.username} ({admin_user.email})")
        
        return admin_user, True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Failed to create admin user: {str(e)}")
        raise


def get_sftp_config():
    """
    Get SFTP configuration from environment variables
    
    Returns:
        dict: SFTP configuration settings
    """
    return {
        'sftp_hostname': os.environ.get('SFTP_HOSTNAME', ''),
        'sftp_username': os.environ.get('SFTP_USERNAME', ''),
        'sftp_password': os.environ.get('SFTP_PASSWORD', ''),
        'sftp_port': os.environ.get('SFTP_PORT', '22'),
        'sftp_directory': os.environ.get('SFTP_DIRECTORY', '/')
    }


def get_bullhorn_config():
    """
    Get Bullhorn API configuration from environment variables
    
    Returns:
        dict: Bullhorn API configuration settings
    """
    return {
        'bullhorn_client_id': os.environ.get('BULLHORN_CLIENT_ID', ''),
        'bullhorn_client_secret': os.environ.get('BULLHORN_CLIENT_SECRET', ''),
        'bullhorn_username': os.environ.get('BULLHORN_USERNAME', ''),
        'bullhorn_password': os.environ.get('BULLHORN_PASSWORD', '')
    }


def seed_global_settings(db, GlobalSettings):
    """
    Seed global settings including SFTP config, Bullhorn credentials, and automation toggles
    
    Args:
        db: SQLAlchemy database instance
        GlobalSettings: GlobalSettings model class
    """
    try:
        sftp_config = get_sftp_config()
        bullhorn_config = get_bullhorn_config()
        
        # In production, validate that required Bullhorn credentials are present
        if is_production_environment():
            missing_bullhorn = []
            for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
                if not bullhorn_config.get(key):
                    missing_bullhorn.append(key.upper())
            
            if missing_bullhorn:
                error_msg = f"PRODUCTION DEPLOYMENT ERROR: Missing required Bullhorn secrets: {', '.join(missing_bullhorn)}"
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)
            
            # Validate SFTP credentials for production automation
            missing_sftp = []
            for key in ['sftp_hostname', 'sftp_username', 'sftp_password']:
                if not sftp_config.get(key):
                    missing_sftp.append(key.upper())
            
            if missing_sftp:
                error_msg = f"PRODUCTION DEPLOYMENT ERROR: Missing required SFTP secrets: {', '.join(missing_sftp)}"
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)
        
        # Define credential settings to seed (toggles handled separately to preserve user settings)
        credential_settings = {
            # SFTP Configuration
            'sftp_hostname': sftp_config['sftp_hostname'],
            'sftp_username': sftp_config['sftp_username'],
            'sftp_password': sftp_config['sftp_password'],
            'sftp_port': sftp_config['sftp_port'],
            'sftp_directory': sftp_config['sftp_directory'],
            
            # Bullhorn API Configuration
            'bullhorn_client_id': bullhorn_config['bullhorn_client_id'],
            'bullhorn_client_secret': bullhorn_config['bullhorn_client_secret'],
            'bullhorn_username': bullhorn_config['bullhorn_username'],
            'bullhorn_password': bullhorn_config['bullhorn_password']
        }
        
        settings_created = []

        # Process credential settings
        for key, value in credential_settings.items():
            existing = GlobalSettings.query.filter_by(setting_key=key).first()

            if existing:
                # PRESERVE existing credentials — NEVER overwrite values managed via the UI.
                # Production credentials are the source of truth; env vars only seed first-run
                # rows. This prevents a deployment from resetting credentials that an operator
                # has updated through the UI after the initial bootstrap.
                logger.info(f"ℹ️ Preserving credential setting: {key}")
                continue
            else:
                # Create new credential setting only if value exists (first-run bootstrap only)
                if value:
                    new_setting = GlobalSettings(
                        setting_key=key,
                        setting_value=value,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(new_setting)
                    settings_created.append(key)
        
        # Process automation toggles separately - NEVER overwrite existing values
        # Defaults are always conservative (off) — user enables via UI.
        # automated_uploads_enabled default flipped from 'true' to 'false' on
        # 2026-04-25 to match the documented "always conservative" contract and
        # prevent fresh-install Repls from auto-uploading SFTP feeds before the
        # operator has reviewed credentials. Existing prod values are preserved
        # by the existence check below; this only changes first-run behavior.
        automation_toggles = {
            'sftp_enabled': 'false',
            'automated_uploads_enabled': 'false',
            'candidate_cleanup_enabled': 'false',
            'candidate_cleanup_batch_size': '50',
        }
        
        for key, default_value in automation_toggles.items():
            existing = GlobalSettings.query.filter_by(setting_key=key).first()
            
            if existing:
                # PRESERVE user settings - NEVER overwrite existing toggle values
                # User controls these via UI, we only set defaults on first creation
                logger.info(f"ℹ️ Preserving user setting: {key}={existing.setting_value}")
                continue
            else:
                # Create new toggle with production-aware default (first run only)
                # Re-evaluate is_production_environment() at creation time for correctness
                new_setting = GlobalSettings(
                    setting_key=key,
                    setting_value=default_value,
                    created_at=datetime.utcnow()
                )
                db.session.add(new_setting)
                settings_created.append(key)
                logger.info(f"✅ Created automation toggle: {key}={default_value} (production={is_production_environment()})")
        
        if settings_created:
            db.session.commit()
            logger.info(f"✅ Created settings: {', '.join(settings_created)}")
        else:
            logger.info("✅ Global settings already up to date")
            
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed global settings: {str(e)}")
        raise


def seed_bullhorn_monitors(db, BullhornMonitor):
    """
    Seed BullhornMonitor tearsheet configurations (upsert behavior)
    
    Args:
        db: SQLAlchemy database instance
        BullhornMonitor: BullhornMonitor model class
    """
    try:
        # Define tearsheet configurations (Bullhorn One IDs - January 2026 migration)
        tearsheet_configs = [
            {
                'name': 'Sponsored - OTT',
                'tearsheet_id': 1231,
                'tearsheet_name': 'Sponsored - OTT',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - CHI',
                'tearsheet_id': 1232,
                'tearsheet_name': 'Sponsored - CHI',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - CLE',
                'tearsheet_id': 1233,
                'tearsheet_name': 'Sponsored - CLE',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - VMS',
                'tearsheet_id': 1239,
                'tearsheet_name': 'Sponsored - VMS',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - GR',
                'tearsheet_id': 1474,
                'tearsheet_name': 'Sponsored - GR',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - STSI',
                'tearsheet_id': 1531,
                'tearsheet_name': 'Sponsored - STSI',
                'notification_email': ''  # No email for STSI
            }
        ]
        
        monitors_created = []
        monitors_updated = []
        
        for config in tearsheet_configs:
            # Check if monitor already exists
            existing = BullhornMonitor.query.filter_by(
                tearsheet_id=config['tearsheet_id']
            ).first()
            
            if existing:
                # Update existing monitor to ensure correct configuration
                updates_made = []
                
                if existing.name != config['name']:
                    existing.name = config['name']
                    updates_made.append('name')
                    
                if existing.tearsheet_name != config['tearsheet_name']:
                    existing.tearsheet_name = config['tearsheet_name']
                    updates_made.append('tearsheet_name')
                
                if not existing.is_active:
                    existing.is_active = True
                    updates_made.append('is_active')
                
                if existing.check_interval_minutes != 5:
                    existing.check_interval_minutes = 5
                    updates_made.append('check_interval_minutes')
                
                if not existing.send_notifications:
                    existing.send_notifications = True
                    updates_made.append('send_notifications')
                
                # Update notification email if different
                if existing.notification_email != config['notification_email']:
                    existing.notification_email = config['notification_email']
                    updates_made.append('notification_email')
                
                if updates_made:
                    existing.updated_at = datetime.utcnow()
                    monitors_updated.append(f"{config['name']} ({', '.join(updates_made)})")
            else:
                # Create new monitor
                new_monitor = BullhornMonitor(
                    name=config['name'],
                    tearsheet_id=config['tearsheet_id'],
                    tearsheet_name=config['tearsheet_name'],
                    is_active=True,
                    check_interval_minutes=5,
                    notification_email=config['notification_email'],
                    send_notifications=True,
                    next_check=datetime.utcnow(),
                    created_at=datetime.utcnow()
                )
                db.session.add(new_monitor)
                monitors_created.append(config['name'])
        
        if monitors_created or monitors_updated:
            db.session.commit()
            
            if monitors_created:
                logger.info(f"✅ Created {len(monitors_created)} Bullhorn monitors: {', '.join(monitors_created)}")
            
            if monitors_updated:
                logger.info(f"🔄 Updated {len(monitors_updated)} Bullhorn monitors to production defaults")
        else:
            logger.info(f"✅ All 5 Bullhorn monitors already configured correctly")
        
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed Bullhorn monitors: {str(e)}")
        raise


def _default_platform_age_ceilings_json():
    """Return the JSON-encoded default PLATFORM_AGE_CEILINGS dict.

    Sourced from vetting_audit_service.DEFAULT_PLATFORM_AGE_CEILINGS so the
    seed default and the service default never drift apart. Falls back to an
    empty JSON object if the import fails (extremely unlikely in practice but
    keeps seed startup robust).
    """
    try:
        from vetting_audit_service import DEFAULT_PLATFORM_AGE_CEILINGS
        return json.dumps(DEFAULT_PLATFORM_AGE_CEILINGS)
    except Exception as e:
        logger.warning(f"⚠️ Failed to load default platform ceilings: {e!r}")
        return '{}'


def _load_global_screening_prompt():
    """
    Load the global screening prompt from the version-controlled config file.
    Falls back to empty string if the file is missing — never crashes the seed process.
    """
    prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'global_screening_prompt.txt')
    try:
        with open(prompt_path, 'r') as f:
            prompt = f.read().strip()
        if prompt:
            logger.info(f"✅ Loaded global screening prompt from {prompt_path} ({len(prompt)} chars)")
            return prompt
        else:
            logger.warning(f"⚠️ Global screening prompt file exists but is empty: {prompt_path}")
            return ''
    except FileNotFoundError:
        logger.warning(f"⚠️ Global screening prompt file not found: {prompt_path} — seeding empty string")
        return ''
    except Exception as e:
        logger.warning(f"⚠️ Failed to read global screening prompt: {str(e)} — seeding empty string")
        return ''


def seed_vetting_config(db, VettingConfig):
    """
    Seed AI Vetting configuration settings
    
    In production, vetting is ENABLED by default (vetting_enabled=true)
    In development, vetting is DISABLED by default (vetting_enabled=false)
    
    Like automation toggles, user settings are preserved - we only set defaults on first creation.
    
    Args:
        db: SQLAlchemy database instance
        VettingConfig: VettingConfig model class
    """
    is_prod = is_production_environment()
    
    try:
        # Define vetting settings with production-aware defaults
        # IMPORTANT: Every VettingConfig key that the app reads MUST be listed here
        # so it is recreated with a sensible default if the DB is ever reset.
        # Production defaults reflect actual operational values; dev defaults are
        # conservative to prevent accidental emails or processing.
        # Defaults are always conservative — user configures via UI.
        # No environment-conditional values for operational toggles.
        vetting_settings = {
            'vetting_enabled': 'false',           # Always off by default; user enables via UI
            'match_threshold': '80',
            'batch_size': '25',                    # Default batch size per 5-minute cycle
            'admin_notification_email': 'kroots@myticas.com',
            'health_alert_email': 'kroots@myticas.com',
            'send_recruiter_emails': 'false',      # Always off by default; user enables via UI
            # Embedding pre-filter settings (Layer 1)
            'embedding_filter_enabled': 'true',    # Killswitch: set to 'false' to bypass filter
            'embedding_similarity_threshold': '0.25',  # Conservative default; user tightens via UI
            # Escalation settings (Layer 3)
            'escalation_low': '60',
            'escalation_high': '85',
            # Layer 2 model (revertible via UI if quality drop detected)
            'layer2_model': 'gpt-5.4',
            # Cutoff date: only process candidates received after this timestamp
            'vetting_cutoff_date': '',             # Empty by default; user sets via UI
            # Screening quality audit
            'screening_audit_enabled': 'false',    # Off by default; user enables via UI
            # Quality auditor model — separately configurable from layer2_model
            # so an admin can run the auditor on a stronger/cheaper model
            # without affecting primary screening.
            'quality_auditor_model': 'gpt-5.4',
            # Platform-age ceilings (years) used by the heuristic pre-checks
            # to detect impossible "X years required" requirements when the
            # platform itself is younger than X. JSON-encoded dict of
            # platform_name (lowercase) -> max_years_float.
            'platform_age_ceilings': _default_platform_age_ceilings_json(),
            # Sample rate (percent, 0-100) for the new Qualified false-positive
            # audit phase. 0 disables it; 10 means 10% of recent Qualified
            # results are sampled per cycle.
            'qualified_audit_sample_rate': '10',
            # Recruiter-activity gate (Task D) — pause auto-vet when a human
            # recruiter has touched the candidate within the lookback window.
            # Killswitch and tunable window so admins can adjust without a deploy.
            'recruiter_activity_check_enabled': 'true',
            'recruiter_activity_lookback_minutes': '60',
            # Global screening instructions — loaded from version-controlled config file
            # so a DB reset restores the full prompt instead of wiping it.
            'global_custom_requirements': _load_global_screening_prompt(),
        }
        
        settings_created = []
        
        for key, default_value in vetting_settings.items():
            existing = VettingConfig.query.filter_by(setting_key=key).first()
            
            if existing:
                # PRESERVE user settings - NEVER overwrite existing values
                logger.info(f"ℹ️ Preserving vetting setting: {key}={existing.setting_value}")
                continue
            else:
                # Create new setting with production-aware default (first run only)
                new_setting = VettingConfig(
                    setting_key=key,
                    setting_value=default_value
                )
                db.session.add(new_setting)
                settings_created.append(key)
                logger.info(f"✅ Created vetting setting: {key}={default_value} (production={is_prod})")
        
        if settings_created:
            db.session.commit()
            logger.info(f"✅ Created vetting settings: {', '.join(settings_created)}")
        else:
            logger.info("✅ Vetting settings already up to date")
            
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed vetting config: {str(e)}")
        raise


def seed_environment_monitoring(db):
    """
    Initialize environment monitoring settings for production
    
    Args:
        db: SQLAlchemy database instance
    """
    try:
        from models import EnvironmentStatus
        
        # Check if production environment monitoring exists
        prod_env = EnvironmentStatus.query.filter_by(
            environment_name='production'
        ).first()
        
        if not prod_env:
            # Create production environment monitoring
            prod_env = EnvironmentStatus(
                environment_name='production',
                environment_url='https://app.scoutgenius.ai',
                is_active=True,
                check_interval_minutes=5,
                alert_email='kroots@myticas.com',
                status='unknown',
                created_at=datetime.utcnow()
            )
            db.session.add(prod_env)
            db.session.commit()
            logger.info("✅ Created production environment monitoring configuration")
        else:
            logger.info("✅ Production environment monitoring already configured")
            
    except ImportError:
        logger.debug("ℹ️ EnvironmentStatus model not found - skipping environment monitoring seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed environment monitoring: {str(e)}")


def seed_reference_refresh_log(db):
    """
    Initialize RefreshLog with baseline timestamp ONLY if table is empty
    NEVER overwrites existing refresh history - preserves real refresh data
    
    Args:
        db: SQLAlchemy database instance
    """
    try:
        from models import RefreshLog
        from datetime import date, timedelta
        
        # Use current time minus 120h so the scheduler will fire the first
        # refresh ~120h from deploy, not show a stale historical date.
        baseline_time = datetime.utcnow() - timedelta(hours=120)
        baseline_date = baseline_time.date()
        
        # Check if any refresh log entries exist
        existing_count = RefreshLog.query.count()
        
        if existing_count > 0:
            # Preserve existing refresh history - NEVER overwrite
            most_recent = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()
            logger.info(f"✅ RefreshLog has {existing_count} entries (most recent: {most_recent.refresh_time} UTC) - preserving history")
        else:
            # No entries exist - create baseline for new deployments only
            initial_log = RefreshLog(
                refresh_date=baseline_date,
                refresh_time=baseline_time,
                jobs_updated=65
            )
            db.session.add(initial_log)
            db.session.commit()
            logger.info(f"✅ Created baseline RefreshLog entry: {baseline_time} UTC (new deployment)")
            
    except ImportError:
        logger.debug("ℹ️ RefreshLog model not found - skipping refresh log seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed refresh log: {str(e)}")


def seed_recruiter_mappings(db, RecruiterMapping):
    """
    Seed recruiter to LinkedIn tag mappings (idempotent)
    
    Args:
        db: SQLAlchemy database instance
        RecruiterMapping: RecruiterMapping model class
    """
    # Master list of recruiter name to LinkedIn tag mappings
    recruiter_mappings = [
        ('Adam Gebara', '#LI-AG1'),
        ('Amanda Messina', '#LI-AM1'),
        ('Amanda Messina (Smith)', '#LI-AM1'),
        ('Austin Zachrich', '#LI-AZ1'),
        ('Bryan Chinzorig', '#LI-BC1'),
        ('Chris Carter', '#LI-CC1'),
        ('Christine Carter', '#LI-CC1'),
        ('Dan Sifer', '#LI-DS1'),
        ('Daniel Sifer', '#LI-DS1'),
        ('Dawn Geistert-Dixon', '#LI-DG1'),
        ('Dominic Scaletta', '#LI-DS2'),
        ('Innocent Nangoma', '#LI-IN1'),
        ('Jayne Kritschgau', '#LI-JK1'),
        ('Julie Johnson', '#LI-JJ1'),
        ('Kaniz Abedin', '#LI-KA1'),
        ('Kyle Roots', '#LI-KR1'),
        ('Kellie Miller', '#LI-KM1'),
        ('Lisa Keirsted', '#LI-DS1'),
        ('Lisa Mattis-Keirsted', '#LI-LM1'),
        ('Maddie Lewis', '#LI-ML1'),
        ('Madhu Sinha', '#LI-MS1'),
        ('Matheo Theodossiou', '#LI-MT1'),
        ('Michael Billiu', '#LI-MB1'),
        ('Michael Theodossiou', '#LI-MT2'),
        ('Michelle Corino', '#LI-MC1'),
        ('Mike Gebara', '#LI-MG1'),
        ('Mike Scalzitti', '#LI-MS2'),
        ('Myticas Recruiter', '#LI-RS1'),
        ('Nick Theodossiou', '#LI-NT1'),
        ('Rachel Mann', '#LI-RM1'),
        ('Rachelle Fite', '#LI-RF1'),
        ('Reena Setya', '#LI-RS2'),
        ('Runa Parmar', '#LI-RP1'),
        ('Ryan Green', '#LI-RG1'),
        ('Ryan Oliver', '#LI-RO1'),
        ('Sarah Ferris', '#LI-SF1'),
        ('Sarah Ferris CSP', '#LI-SF1'),
        ('Shikha Gurung', '#LI-SG1'),
        ('Tarra Dziurman', '#LI-TD1'),
        ('Tray Prewitt', '#LI-TP1'),
    ]
    
    mappings_created = 0
    mappings_updated = 0
    
    for recruiter_name, linkedin_tag in recruiter_mappings:
        existing_mapping = RecruiterMapping.query.filter_by(recruiter_name=recruiter_name).first()
        
        if existing_mapping:
            # Update if tag changed
            if existing_mapping.linkedin_tag != linkedin_tag:
                existing_mapping.linkedin_tag = linkedin_tag
                mappings_updated += 1
        else:
            # Create new mapping
            new_mapping = RecruiterMapping(
                recruiter_name=recruiter_name,
                linkedin_tag=linkedin_tag
            )
            db.session.add(new_mapping)
            mappings_created += 1
    
    db.session.commit()
    
    if mappings_created > 0:
        logger.info(f"✅ Created {mappings_created} new recruiter mappings")
    if mappings_updated > 0:
        logger.info(f"🔄 Updated {mappings_updated} recruiter mappings")
    if mappings_created == 0 and mappings_updated == 0:
        logger.info(f"✅ All {len(recruiter_mappings)} recruiter mappings already up to date")


def run_vetting_clean_slate(db):
    """
    ONE-TIME clean slate reset for AI vetting system.
    
    This function:
    1. Checks if clean slate has already been run (via flag in VettingConfig)
    2. If not run yet, clears all vetting history and marks existing emails as vetted
    3. Sets the flag so it never runs again
    
    This allows a fresh start where only NEW applications are vetted.
    """
    from models import VettingConfig, CandidateVettingLog, CandidateJobMatch, ParsedEmail
    
    # Check if clean slate has already been performed
    clean_slate_flag = VettingConfig.query.filter_by(setting_key='clean_slate_completed').first()
    
    if clean_slate_flag and clean_slate_flag.setting_value == 'true':
        logger.info("ℹ️ Vetting clean slate already completed - skipping")
        return
    
    logger.info("🧹 Running ONE-TIME vetting clean slate reset...")
    
    try:
        # Step 1: Delete all CandidateJobMatch records (child records first)
        match_count = CandidateJobMatch.query.delete()
        logger.info(f"  Deleted {match_count} CandidateJobMatch records")
        
        # Step 2: Delete all CandidateVettingLog records
        log_count = CandidateVettingLog.query.delete()
        logger.info(f"  Deleted {log_count} CandidateVettingLog records")
        
        # Step 3: Mark existing ParsedEmail records as vetted
        # IMPORTANT: Only mark records OLDER than 1 hour to avoid accidentally
        # marking new applications that arrive during deployment
        from datetime import datetime, timedelta
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        vetted_count = ParsedEmail.query.filter(
            ParsedEmail.vetted_at.is_(None),
            ParsedEmail.bullhorn_candidate_id.isnot(None),
            ParsedEmail.status == 'completed',
            ParsedEmail.received_at < one_hour_ago  # Only mark old records
        ).update({'vetted_at': datetime.utcnow()}, synchronize_session=False)
        logger.info(f"  Marked {vetted_count} existing ParsedEmail records (older than 1 hour) as vetted")
        
        # Step 4: Set the clean slate flag so this never runs again
        if clean_slate_flag:
            clean_slate_flag.setting_value = 'true'
            clean_slate_flag.updated_at = datetime.utcnow()
        else:
            new_flag = VettingConfig(
                setting_key='clean_slate_completed',
                setting_value='true',
                description='One-time clean slate reset completed on first deployment'
            )
            db.session.add(new_flag)
        
        db.session.commit()
        logger.info("✅ Vetting clean slate completed - only new applications will be vetted going forward")
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Clean slate failed: {str(e)}")
        raise


def run_schema_migrations(db):
    """
    Run any necessary schema migrations that db.create_all() won't handle.
    Adds missing columns to existing tables.
    """
    from sqlalchemy import text
    
    migrations = [
        # Add location columns to job_vetting_requirements (added Jan 2026)
        ("job_vetting_requirements", "job_location", "VARCHAR(255)"),
        ("job_vetting_requirements", "job_work_type", "VARCHAR(50)"),
        # Add years_analysis_json to candidate_job_match for auditability (added Feb 2026)
        ("candidate_job_match", "years_analysis_json", "TEXT"),
        # Add employer prestige boost toggle (added Apr 2026)
        ("job_vetting_requirements", "employer_prestige_boost", "BOOLEAN DEFAULT FALSE"),
        # Add employer prestige tracking to candidate_job_match (added Apr 2026)
        ("candidate_job_match", "prestige_employer", "VARCHAR(255)"),
        ("candidate_job_match", "prestige_boost_applied", "BOOLEAN DEFAULT FALSE"),
        # Add inline-editable requirements + provenance metadata (added Apr 2026)
        ("job_vetting_requirements", "edited_requirements", "TEXT"),
        ("job_vetting_requirements", "requirements_edited_at", "TIMESTAMP"),
        ("job_vetting_requirements", "requirements_edited_by", "VARCHAR(255)"),
    ]
    
    _SAFE_IDENTIFIER = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    _SAFE_COL_TYPE = re.compile(r'^[A-Z()0-9 ]+$')

    for table, column, col_type in migrations:
        try:
            if not (_SAFE_IDENTIFIER.match(table) and _SAFE_IDENTIFIER.match(column) and _SAFE_COL_TYPE.match(col_type)):
                logger.warning(f"⚠️ Migration skipped: unsafe identifier in {table}.{column} {col_type}")
                continue
            check_sql = text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = :table AND column_name = :column
            """)
            result = db.session.execute(check_sql, {'table': table, 'column': column})
            if result.fetchone() is None:
                alter_sql = text('ALTER TABLE ' + table + ' ADD COLUMN ' + column + ' ' + col_type)
                db.session.execute(alter_sql)
                db.session.commit()
                logger.info(f"✅ Added column {column} to {table}")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Migration skipped for {table}.{column}: {str(e)}")
    
    # Add is_company_admin to user table (added Feb 2026)
    # Handled separately because 'user' is a PostgreSQL reserved word requiring quoting
    try:
        check_sql = text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'user' AND column_name = 'is_company_admin'
        """)
        result = db.session.execute(check_sql)
        if result.fetchone() is None:
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN is_company_admin BOOLEAN DEFAULT FALSE'))
            db.session.commit()
            logger.info("✅ Added column is_company_admin to user table")
        else:
            logger.info("ℹ️ Column is_company_admin already exists on user table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for user.is_company_admin: {str(e)}")

    # Add last_active_at to user table (added March 2026 for Active Users tracking)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'user' AND column_name = 'last_active_at'
        """))
        if result.fetchone() is None:
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN last_active_at TIMESTAMP'))
            db.session.commit()
            logger.info("✅ Added column last_active_at to user table")
        else:
            logger.info("ℹ️ Column last_active_at already exists on user table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for user.last_active_at: {str(e)}")

    # Add is_sandbox to candidate_vetting_log (added Feb 2026 for Vetting Sandbox feature)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'candidate_vetting_log' AND column_name = 'is_sandbox'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                'ALTER TABLE candidate_vetting_log ADD COLUMN is_sandbox BOOLEAN NOT NULL DEFAULT FALSE'
            ))
            db.session.commit()
            logger.info("✅ Added column is_sandbox to candidate_vetting_log table")
        else:
            logger.info("ℹ️ Column is_sandbox already exists on candidate_vetting_log table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for candidate_vetting_log.is_sandbox: {str(e)}")

    # Add is_sandbox to scout_vetting_session (added Feb 2026 for Vetting Sandbox feature)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'scout_vetting_session' AND column_name = 'is_sandbox'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                'ALTER TABLE scout_vetting_session ADD COLUMN is_sandbox BOOLEAN NOT NULL DEFAULT FALSE'
            ))
            db.session.commit()
            logger.info("✅ Added column is_sandbox to scout_vetting_session table")
        else:
            logger.info("ℹ️ Column is_sandbox already exists on scout_vetting_session table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for scout_vetting_session.is_sandbox: {str(e)}")

    # Add thread_message_id to scout_vetting_session (added Apr 2026 for email thread history)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'scout_vetting_session' AND column_name = 'thread_message_id'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                'ALTER TABLE scout_vetting_session ADD COLUMN thread_message_id VARCHAR(255)'
            ))
            db.session.commit()
            logger.info("✅ Added column thread_message_id to scout_vetting_session table")
        else:
            logger.info("ℹ️ Column thread_message_id already exists on scout_vetting_session table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for scout_vetting_session.thread_message_id: {str(e)}")

    # Add resolution_note, resolved_by, and Send-to-Build workflow columns to support_ticket
    for col_name, col_type in [
        ('resolution_note', 'TEXT'),
        ('resolved_by', 'VARCHAR(255)'),
        ('sent_to_build_at', 'TIMESTAMP'),
        ('sent_to_build_by', 'VARCHAR(255)'),
        ('build_summary', 'TEXT'),
        ('deployed_at', 'TIMESTAMP'),
        ('deployed_by', 'VARCHAR(255)'),
        ('deploy_commit_link', 'VARCHAR(500)'),
    ]:
        try:
            check = text(f"SELECT column_name FROM information_schema.columns WHERE table_name='support_ticket' AND column_name='{col_name}'")
            exists = db.session.execute(check).fetchone()
            if not exists:
                db.session.execute(text(f"ALTER TABLE support_ticket ADD COLUMN {col_name} {col_type}"))
                db.session.commit()
                logger.info(f"✅ Added column {col_name} to support_ticket table")
            else:
                logger.info(f"ℹ️ Column {col_name} already exists on support_ticket table")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Migration skipped for support_ticket.{col_name}: {str(e)}")

    for col_name, col_type in [('onedrive_item_id', 'VARCHAR(255)'), ('onedrive_etag', 'VARCHAR(255)'), ('onedrive_folder_id', 'VARCHAR(255)')]:
        try:
            check = text(f"SELECT column_name FROM information_schema.columns WHERE table_name='knowledge_document' AND column_name='{col_name}'")
            exists = db.session.execute(check).fetchone()
            if not exists:
                db.session.execute(text(f"ALTER TABLE knowledge_document ADD COLUMN {col_name} {col_type}"))
                db.session.commit()
                logger.info(f"✅ Added column {col_name} to knowledge_document table")
            else:
                logger.info(f"ℹ️ Column {col_name} already exists on knowledge_document table")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Migration skipped for knowledge_document.{col_name}: {str(e)}")

    # Add vetting_retry_count to parsed_email (Apr 2026 - tracks 0% failure retries to prevent endless recycling)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'parsed_email' AND column_name = 'vetting_retry_count'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                "ALTER TABLE parsed_email ADD COLUMN vetting_retry_count INTEGER NOT NULL DEFAULT 0"
            ))
            db.session.commit()
            logger.info("✅ Added column vetting_retry_count to parsed_email table")
        else:
            logger.info("ℹ️ Column vetting_retry_count already exists on parsed_email table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for parsed_email.vetting_retry_count: {str(e)}")

    # Add conversation_id to support_attachment (Apr 2026 - links attachments to specific conversation entries)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'support_attachment' AND column_name = 'conversation_id'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                "ALTER TABLE support_attachment ADD COLUMN conversation_id INTEGER REFERENCES support_conversation(id)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_support_attachment_conversation_id ON support_attachment(conversation_id)"
            ))
            db.session.commit()
            logger.info("✅ Added column conversation_id to support_attachment table")
        else:
            logger.info("ℹ️ Column conversation_id already exists on support_attachment table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for support_attachment.conversation_id: {str(e)}")

    # Drop UNIQUE constraint on candidate_vetting_log.bullhorn_candidate_id
    # (Feb 2026 - allow multiple vetting logs per candidate for re-applications)
    try:
        # Check if the unique constraint still exists (PostgreSQL only)
        check_sql = text("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'candidate_vetting_log'
              AND constraint_type = 'UNIQUE'
              AND constraint_name LIKE '%bullhorn_candidate_id%'
        """)
        result = db.session.execute(check_sql)
        constraint = result.fetchone()
        if constraint:
            constraint_name = constraint[0]
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', constraint_name):
                logger.warning(f"⚠️ Unsafe constraint name: {constraint_name}")
            else:
                drop_sql = text('ALTER TABLE candidate_vetting_log DROP CONSTRAINT ' + constraint_name)
                db.session.execute(drop_sql)
            db.session.commit()
            logger.info(f"✅ Dropped UNIQUE constraint {constraint_name} on candidate_vetting_log.bullhorn_candidate_id")
        else:
            logger.info("ℹ️ UNIQUE constraint on candidate_vetting_log.bullhorn_candidate_id already removed")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for dropping unique constraint: {str(e)}")

    # Ensure pg_trgm extension and trigram GIN index on candidate_vetting_log.candidate_name
    # (March 2026 — for server-side candidate search on Scout Screening portal)
    # Managed here instead of Alembic because Replit's deployment auto-migration
    # generates incorrect SQL for GIN indexes (omits gin_trgm_ops operator class).
    # Index creation is guarded by REPLIT_DEPLOYMENT so it only runs in the deployed
    # production environment — not in the dev workspace — preventing Replit's schema
    # diff from detecting the index and generating a broken migration on next deploy.
    try:
        db.session.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        db.session.commit()
        logger.info("ℹ️ pg_trgm extension ensured")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ pg_trgm extension creation skipped: {str(e)}")

    if os.environ.get('REPLIT_DEPLOYMENT'):
        try:
            result = db.session.execute(text("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'candidate_vetting_log'
                  AND indexname = 'idx_vetting_log_candidate_name_trgm'
            """))
            if result.fetchone() is None:
                db.session.execute(text(
                    "CREATE INDEX idx_vetting_log_candidate_name_trgm "
                    "ON candidate_vetting_log USING gin (candidate_name gin_trgm_ops)"
                ))
                db.session.commit()
                logger.info("✅ Created trigram GIN index on candidate_vetting_log.candidate_name")
            else:
                logger.info("ℹ️ Trigram GIN index on candidate_vetting_log.candidate_name already exists")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Trigram index creation skipped: {str(e)}")
    else:
        logger.info("ℹ️ Trigram GIN index creation skipped in dev workspace (runs on deployed prod only)")

    try:
        result = db.session.execute(text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'candidate_job_match'
              AND indexname = 'idx_match_job_created'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                "CREATE INDEX idx_match_job_created "
                "ON candidate_job_match (bullhorn_job_id, created_at DESC)"
            ))
            db.session.commit()
            logger.info("✅ Created composite index idx_match_job_created on candidate_job_match")
        else:
            logger.info("ℹ️ Composite index idx_match_job_created already exists")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Composite index idx_match_job_created creation skipped: {str(e)}")


    prospector_tables = ['prospector_profile', 'prospector_run', 'prospect']
    for table_name in prospector_tables:
        try:
            result = db.session.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_name = :table_name AND table_schema = 'public'
            """), {'table_name': table_name})
            if result.fetchone():
                logger.info(f"ℹ️ Prospector table '{table_name}' exists")
            else:
                logger.warning(f"⚠️ Prospector table '{table_name}' missing — db.create_all() should create it")
        except Exception as e:
            logger.warning(f"⚠️ Prospector table check failed for '{table_name}': {str(e)}")


def log_critical_settings_state(db):
    """
    Log the current DB values of all admin-controlled settings at startup.
    
    This runs at the END of seed_database() so every deploy produces a single,
    easy-to-audit snapshot proving no setting was silently changed.
    """
    from models import VettingConfig, GlobalSettings
    
    try:
        vetting_keys = [
            'vetting_enabled', 'send_recruiter_emails',
            'embedding_filter_enabled', 'embedding_similarity_threshold',
            'vetting_cutoff_date', 'match_threshold', 'batch_size',
            'layer2_model',
        ]
        global_keys = [
            'sftp_enabled', 'automated_uploads_enabled',
        ]
        
        parts = []
        for key in vetting_keys:
            s = VettingConfig.query.filter_by(setting_key=key).first()
            parts.append(f"{key}={s.setting_value if s else 'MISSING'}")
        for key in global_keys:
            s = GlobalSettings.query.filter_by(setting_key=key).first()
            parts.append(f"{key}={s.setting_value if s else 'MISSING'}")
        
        logger.info(f"🔒 STARTUP AUDIT — critical settings: {' | '.join(parts)}")
    except Exception as e:
        logger.warning(f"⚠️ Startup audit logging failed: {str(e)}")



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


def migrate_legacy_custom_requirements(db):
    """One-time data migration: convert legacy `custom_requirements` rows to the
    new `edited_requirements` field (Apr 2026 — inline editable requirements).

    For rows where both `ai_interpreted_requirements` and `custom_requirements`
    were populated, the AI list is preserved and the recruiter additions are
    appended in a clearly-marked block so the screening engine continues to see
    both. For rows with only `custom_requirements`, that text becomes the
    edited list directly. Idempotent — only touches rows where
    `edited_requirements` is still NULL.
    """
    from sqlalchemy import text

    try:
        result = db.session.execute(text(
            """
            SELECT id, ai_interpreted_requirements, custom_requirements, updated_at
            FROM job_vetting_requirements
            WHERE edited_requirements IS NULL
              AND custom_requirements IS NOT NULL
              AND TRIM(custom_requirements) <> ''
            """
        ))
        rows = result.fetchall()
        migrated = 0
        for row in rows:
            row_id = row[0]
            ai_text = (row[1] or '').strip()
            custom_text = (row[2] or '').strip()
            updated_at = row[3]

            if not custom_text:
                continue

            if ai_text:
                edited_text = (
                    f"{ai_text}\n\n"
                    f"--- RECRUITER NOTES (migrated from legacy override) ---\n"
                    f"{custom_text}"
                )
            else:
                edited_text = custom_text

            db.session.execute(
                text(
                    """
                    UPDATE job_vetting_requirements
                    SET edited_requirements = :edited,
                        requirements_edited_at = COALESCE(:edited_at, NOW()),
                        requirements_edited_by = 'migrated'
                    WHERE id = :row_id
                    """
                ),
                {'edited': edited_text, 'edited_at': updated_at, 'row_id': row_id}
            )
            migrated += 1

        if migrated:
            db.session.commit()
            logger.info(
                f"✅ Migrated {migrated} legacy custom_requirements row(s) to edited_requirements"
            )
        else:
            logger.info("ℹ️ No legacy custom_requirements rows needed migration")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Legacy custom_requirements migration skipped: {str(e)}")


def seed_builtin_automations(db):
    try:
        from models import AutomationTask

        deleted = 0
        for task in AutomationTask.query.all():
            should_delete = False
            if task.name in STALE_BUILTIN_NAMES:
                should_delete = True
            elif task.config_json:
                try:
                    cfg = json.loads(task.config_json)
                    if cfg.get("builtin_key") in STALE_BUILTIN_KEYS:
                        should_delete = True
                except (json.JSONDecodeError, TypeError):
                    pass
            if should_delete:
                db.session.delete(task)
                deleted += 1
        if deleted > 0:
            db.session.commit()
            logger.info(f"🧹 Removed {deleted} stale automation record(s)")

        existing_keys = set()
        for task in AutomationTask.query.all():
            if task.config_json:
                try:
                    cfg = json.loads(task.config_json)
                    if cfg.get("builtin_key"):
                        existing_keys.add(cfg["builtin_key"])
                except (json.JSONDecodeError, TypeError):
                    pass

        created = 0
        for auto in BUILTIN_AUTOMATIONS:
            if auto["builtin_key"] in existing_keys:
                continue
            task = AutomationTask(
                name=auto["name"],
                description=auto["description"],
                automation_type=auto["automation_type"],
                status="draft",
                config_json=json.dumps({"builtin_key": auto["builtin_key"]}),
            )
            db.session.add(task)
            created += 1

        if created > 0:
            db.session.commit()
            logger.info(f"✅ Seeded {created} built-in automations")
        else:
            logger.info(f"✅ All {len(BUILTIN_AUTOMATIONS)} built-in automations already exist")
    except ImportError:
        logger.debug("ℹ️ AutomationTask model not found - skipping automation seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed built-in automations: {str(e)}")


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
    {"first_name": "Kaniz", "last_name": "Abedin", "email": "kabedin@stsigroup.com", "department": "STS-STSI"},
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


def _seed_brand_contacts(db, brand, contact_list):
    from models import SupportContact
    existing_by_email = {c.email: c for c in SupportContact.query.filter_by(brand=brand).all()}
    added = 0
    updated = 0
    removed = 0
    canonical_emails = {c['email'] for c in contact_list}
    for email, contact in existing_by_email.items():
        if email not in canonical_emails:
            db.session.delete(contact)
            removed += 1
            logger.info(f"🗑️ Removed stale {brand} support contact: {contact.first_name} {contact.last_name} ({email})")
    for contact_data in contact_list:
        if contact_data['email'] not in existing_by_email:
            contact = SupportContact(
                first_name=contact_data['first_name'],
                last_name=contact_data['last_name'],
                email=contact_data['email'],
                brand=brand,
                department=contact_data.get('department')
            )
            db.session.add(contact)
            added += 1
        else:
            existing = existing_by_email[contact_data['email']]
            if contact_data.get('department') and existing.department != contact_data['department']:
                existing.department = contact_data['department']
                updated += 1
    if added > 0 or updated > 0 or removed > 0:
        db.session.commit()
        logger.info(f"✅ {brand} support contacts: {added} added, {updated} updated, {removed} removed")
    else:
        logger.info(f"✅ All {len(contact_list)} {brand} support contacts already up to date")


def seed_support_contacts(db):
    try:
        _seed_brand_contacts(db, 'Myticas', SUPPORT_CONTACTS_MYTICAS)
        _seed_brand_contacts(db, 'STSI', SUPPORT_CONTACTS_STSI)
    except ImportError:
        logger.debug("ℹ️ SupportContact model not found - skipping support contact seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed support contacts: {str(e)}")


def ensure_module_subscriptions(db, User):
    """
    Ensure all non-admin users have scout_support and scout_screening in their
    module subscriptions. One-time upgrade — idempotent, only adds missing modules.
    """
    try:
        import json
        updated = 0
        users = User.query.filter_by(is_admin=False).all()
        for user in users:
            current = user.get_modules()
            if not current:
                continue
            needed = set()
            if 'scout_screening' not in current:
                needed.add('scout_screening')
            if 'scout_support' not in current:
                needed.add('scout_support')
            if needed:
                user.set_modules(current + list(needed))
                updated += 1
        if updated > 0:
            db.session.commit()
            logger.info(f"✅ Module subscriptions: added missing modules to {updated} users")
        else:
            logger.info(f"✅ All users already have correct module subscriptions")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to update module subscriptions: {str(e)}")


def seed_stsi_users(db, User):
    """
    Create locked login accounts for all STSI support contacts.
    Idempotent — skips contacts whose email already has a User account.
    All accounts are subscribed to scout_inbound, scout_screening, scout_support
    and locked until a welcome email is sent by the admin.
    """
    try:
        from datetime import datetime
        existing_emails = {u.email for u in User.query.all()}
        existing_usernames = {u.username for u in User.query.all()}

        created = 0
        for c in SUPPORT_CONTACTS_STSI:
            if c['email'] in existing_emails:
                continue

            first = c['first_name'][0].lower()
            last = c['last_name'].lower().replace(' ', '').replace('-', '')
            base_username = f'{first}{last}.stsi'
            username = base_username
            suffix = 2
            while username in existing_usernames:
                username = f'{base_username}{suffix}'
                suffix += 1

            full_name = f"{c['first_name']} {c['last_name']}"
            user = User(
                username=username,
                email=c['email'],
                display_name=full_name,
                company='STSI Group',
                is_admin=False,
                is_company_admin=False,
                password_hash='!locked',
                created_at=datetime.utcnow(),
            )
            user.set_modules(['scout_inbound', 'scout_screening', 'scout_support'])
            db.session.add(user)
            existing_usernames.add(username)
            existing_emails.add(c['email'])
            created += 1

        if created > 0:
            db.session.commit()
            logger.info(f"✅ STSI users: created {created} locked login accounts")
        else:
            logger.info(f"✅ All STSI user accounts already exist")

    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed STSI users: {str(e)}")


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
            lock = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            if lock and lock.setting_value == 'true':
                lock.setting_value = 'false'
                db.session.commit()
                logger.info("🔓 Auto-released stuck vetting lock on deploy")
            else:
                logger.debug("ℹ️ No stuck vetting lock found")
        except Exception as e:
            logger.warning(f"⚠️ Failed to check vetting lock: {str(e)}")
        
        # ONE-TIME: Upgrade layer2_model to gpt-5 (Mar 2026)
        # Upgraded from GPT-5.4 to GPT-5 for maximum accuracy
        try:
            upgrade_flag = VettingConfig.query.filter_by(setting_key='layer2_model_upgraded_to_54').first()
            if not upgrade_flag or upgrade_flag.setting_value != 'true':
                model_setting = VettingConfig.query.filter_by(setting_key='layer2_model').first()
                if model_setting and model_setting.setting_value in ('gpt-4o-mini', 'gpt-4o', 'gpt-5'):
                    old_model = model_setting.setting_value
                    model_setting.setting_value = 'gpt-5.4'
                    model_setting.updated_at = datetime.utcnow()
                    logger.info(f"🔄 Upgraded layer2_model from {old_model} → gpt-5.4")
                
                if upgrade_flag:
                    upgrade_flag.setting_value = 'true'
                else:
                    db.session.add(VettingConfig(
                        setting_key='layer2_model_upgraded_to_54',
                        setting_value='true',
                        description='One-time upgrade to GPT-5.4 — previous model API deprecated Mar 2026'
                    ))
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Failed to upgrade layer2_model: {str(e)}")
        
        seed_builtin_automations(db)

        migrate_legacy_custom_requirements(db)

        seed_support_contacts(db)

        seed_stsi_users(db, User)

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
