"""
Database Seeding Script for JobPulse Application

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
    
    SFTP Configuration:
    - SFTP_HOSTNAME: SFTP server hostname
    - SFTP_USERNAME: SFTP username
    - SFTP_PASSWORD: SFTP password
    - SFTP_PORT: SFTP port (default: 22)
    - SFTP_DIRECTORY: Upload directory (default: /)
    
    Bullhorn API Configuration:
    - BULLHORN_CLIENT_ID: Bullhorn OAuth client ID
    - BULLHORN_CLIENT_SECRET: Bullhorn OAuth client secret
    - BULLHORN_USERNAME: Bullhorn API username
    - BULLHORN_PASSWORD: Bullhorn API password
    
    System:
    - REPLIT_DEPLOYMENT: Detected automatically (production indicator)

What Gets Seeded:
    ✅ Admin user with credentials from environment
    ✅ SFTP settings for automated uploads
    ✅ Bullhorn API credentials for job feed generation
    ✅ 5 Bullhorn tearsheet monitors (1256, 1264, 1499, 1556, 1257)
    ✅ Automation toggles (enabled in production, disabled in dev)
    ✅ Environment monitoring (production only)
"""

import os
import json
import logging
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
        
        settings_updated = []
        settings_created = []
        
        # Process credential settings
        for key, value in credential_settings.items():
            existing = GlobalSettings.query.filter_by(setting_key=key).first()
            
            if existing:
                # Only update credentials if new value is not empty
                if value and existing.setting_value != value:
                    existing.setting_value = value
                    existing.updated_at = datetime.utcnow()
                    settings_updated.append(key)
            else:
                # Create new credential setting only if value exists
                if value:
                    new_setting = GlobalSettings(
                        setting_key=key,
                        setting_value=value,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(new_setting)
                    settings_created.append(key)
        
        # Process automation toggles separately - NEVER overwrite existing values
        # Defaults are always conservative (off) — user enables via UI
        automation_toggles = {
            'sftp_enabled': 'false',
            'automated_uploads_enabled': 'true'
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
        
        if settings_updated or settings_created:
            db.session.commit()
            
            if settings_created:
                logger.info(f"✅ Created settings: {', '.join(settings_created)}")
            if settings_updated:
                logger.info(f"🔄 Updated settings: {', '.join(settings_updated)}")
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
            # Layer 2 model (revertible to 'gpt-4o' via UI if quality drop detected)
            'layer2_model': 'gpt-4o',
            # Cutoff date: only process candidates received after this timestamp
            'vetting_cutoff_date': '',             # Empty by default; user sets via UI
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
    ]
    
    for table, column, col_type in migrations:
        try:
            # Check if column exists
            check_sql = text(f"""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = :table AND column_name = :column
            """)
            result = db.session.execute(check_sql, {'table': table, 'column': column})
            if result.fetchone() is None:
                # Column doesn't exist, add it
                alter_sql = text(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
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
            drop_sql = text(f'ALTER TABLE candidate_vetting_log DROP CONSTRAINT {constraint_name}')
            db.session.execute(drop_sql)
            db.session.commit()
            logger.info(f"✅ Dropped UNIQUE constraint {constraint_name} on candidate_vetting_log.bullhorn_candidate_id")
        else:
            logger.info("ℹ️ UNIQUE constraint on candidate_vetting_log.bullhorn_candidate_id already removed")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for dropping unique constraint: {str(e)}")


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
        "name": "Cleanup AI Notes",
        "description": "Find and delete AI vetting notes (AI Vetting, AI Resume Summary, AI Vetted) from candidate records. Always runs dry-run first.",
        "automation_type": "one-time",
        "builtin_key": "cleanup_ai_notes",
    },
    {
        "name": "Cleanup Duplicate Notes",
        "description": "Remove duplicate 'AI Vetting - Not Recommended' notes where multiple notes were added within 60 minutes of each other. Keeps the original, deletes chained duplicates.",
        "automation_type": "one-time",
        "builtin_key": "cleanup_duplicate_notes",
    },
    {
        "name": "Find Zero Match Candidates",
        "description": "Search recent vetting notes for candidates with 'Highest Match Score: 0%' that may need review or cleanup.",
        "automation_type": "query",
        "builtin_key": "find_zero_match",
    },
    {
        "name": "Export Qualified Candidates",
        "description": "Fetch all submissions for specified job IDs and identify candidates with qualifying vetting actions (Qualified, Recommended, Accept).",
        "automation_type": "query",
        "builtin_key": "export_qualified",
    },
    {
        "name": "Resume Re-Parser",
        "description": "Find recently added candidates with empty descriptions but attached resume files, and re-parse the resume text into Bullhorn.",
        "automation_type": "one-time",
        "builtin_key": "resume_reparser",
    },
    {
        "name": "Sales Rep Sync",
        "description": "Resolve CorporateUser IDs in customText3 to display names in customText6. Runs automatically every 30 minutes; use this for an immediate manual sync.",
        "automation_type": "scheduled",
        "builtin_key": "salesrep_sync",
    },
]


def seed_builtin_automations(db):
    try:
        from models import AutomationTask
        existing = {t.config_json: t for t in AutomationTask.query.all() if t.config_json}
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
                status="active" if auto["automation_type"] == "scheduled" else "draft",
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
    {"first_name": "Nick", "last_name": "Theodossiou", "email": "ntheodossiou@myticas.com", "department": "MYT-Ottawa"},
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


def seed_support_contacts(db):
    try:
        from models import SupportContact
        existing_by_email = {c.email: c for c in SupportContact.query.filter_by(brand='Myticas').all()}
        added = 0
        updated = 0
        for contact_data in SUPPORT_CONTACTS_MYTICAS:
            if contact_data['email'] not in existing_by_email:
                contact = SupportContact(
                    first_name=contact_data['first_name'],
                    last_name=contact_data['last_name'],
                    email=contact_data['email'],
                    brand='Myticas',
                    department=contact_data.get('department')
                )
                db.session.add(contact)
                added += 1
            else:
                existing = existing_by_email[contact_data['email']]
                if contact_data.get('department') and existing.department != contact_data['department']:
                    existing.department = contact_data['department']
                    updated += 1
        if added > 0 or updated > 0:
            db.session.commit()
            logger.info(f"✅ Support contacts: {added} added, {updated} updated with department")
        else:
            logger.info(f"✅ All {len(SUPPORT_CONTACTS_MYTICAS)} Myticas support contacts already up to date")
    except ImportError:
        logger.debug("ℹ️ SupportContact model not found - skipping support contact seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed support contacts: {str(e)}")


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
        
        # ONE-TIME: Upgrade layer2_model from gpt-4o-mini to gpt-4o (Feb 2026)
        # Improves arithmetic accuracy for years-of-experience calculations
        try:
            upgrade_flag = VettingConfig.query.filter_by(setting_key='layer2_model_upgraded_to_4o').first()
            if not upgrade_flag or upgrade_flag.setting_value != 'true':
                model_setting = VettingConfig.query.filter_by(setting_key='layer2_model').first()
                if model_setting and model_setting.setting_value == 'gpt-4o-mini':
                    model_setting.setting_value = 'gpt-4o'
                    model_setting.updated_at = datetime.utcnow()
                    logger.info("🔄 Upgraded layer2_model from gpt-4o-mini → gpt-4o")
                
                # Set flag so this never runs again
                if upgrade_flag:
                    upgrade_flag.setting_value = 'true'
                else:
                    db.session.add(VettingConfig(
                        setting_key='layer2_model_upgraded_to_4o',
                        setting_value='true',
                        description='One-time upgrade to GPT-4o for better arithmetic (Feb 2026)'
                    ))
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Failed to upgrade layer2_model: {str(e)}")
        
        seed_builtin_automations(db)

        seed_support_contacts(db)

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
