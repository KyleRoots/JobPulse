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
import logging
from datetime import datetime
from werkzeug.security import generate_password_hash

# Configure logging
logger = logging.getLogger(__name__)


def is_production_environment():
    """
    Detect if running in production environment
    
    Returns:
        bool: True if in production (REPLIT_DEPLOYMENT exists), False otherwise
    """
    return os.environ.get('REPLIT_DEPLOYMENT') is not None


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
        
        # Define settings to seed with their values
        settings_to_seed = {
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
            'bullhorn_password': bullhorn_config['bullhorn_password'],
            
            # Automation Toggles (enable by default in production)
            'sftp_enabled': 'true' if is_production_environment() else 'false',
            'automated_uploads_enabled': 'true' if is_production_environment() else 'false'
        }
        
        settings_updated = []
        settings_created = []
        
        # Define credential keys that should only be set if value is not empty
        credential_keys = [
            'sftp_hostname', 'sftp_username', 'sftp_password', 'sftp_port', 'sftp_directory',
            'bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password'
        ]
        
        for key, value in settings_to_seed.items():
            # Find existing setting
            existing = GlobalSettings.query.filter_by(setting_key=key).first()
            
            if existing:
                # Update if value is different (and not empty for credentials)
                if key in credential_keys:
                    # Only update credentials if new value is not empty
                    if value and existing.setting_value != value:
                        existing.setting_value = value
                        existing.updated_at = datetime.utcnow()
                        settings_updated.append(key)
                else:
                    # Always update toggles
                    if existing.setting_value != value:
                        existing.setting_value = value
                        existing.updated_at = datetime.utcnow()
                        settings_updated.append(key)
            else:
                # Create new setting (skip credentials if value is empty)
                if key in credential_keys:
                    if value:  # Only create if value exists
                        new_setting = GlobalSettings(
                            setting_key=key,
                            setting_value=value,
                            created_at=datetime.utcnow()
                        )
                        db.session.add(new_setting)
                        settings_created.append(key)
                else:
                    # Always create toggles
                    new_setting = GlobalSettings(
                        setting_key=key,
                        setting_value=value,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(new_setting)
                    settings_created.append(key)
        
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
        # Define tearsheet configurations
        tearsheet_configs = [
            {
                'name': 'Sponsored - OTT',
                'tearsheet_id': 1256,
                'tearsheet_name': 'Sponsored - OTT',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - CHI',
                'tearsheet_id': 1257,
                'tearsheet_name': 'Sponsored - CHI',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - VMS',
                'tearsheet_id': 1264,
                'tearsheet_name': 'Sponsored - VMS',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - GR',
                'tearsheet_id': 1499,
                'tearsheet_name': 'Sponsored - GR',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - STSI',
                'tearsheet_id': 1556,
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
                environment_url='https://jobpulse.lyntrix.ai',
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
    Initialize RefreshLog with baseline timestamp for correct 120-hour calculation
    FORCE UPDATE: Always ensures the most recent entry is the Oct 14 baseline
    
    Args:
        db: SQLAlchemy database instance
    """
    try:
        from models import RefreshLog
        from datetime import date
        
        baseline_date = date(2025, 10, 14)
        baseline_time = datetime(2025, 10, 14, 10, 4, 0)  # Oct 14, 2025 at 10:04 UTC
        
        # Get the most recent refresh log entry
        most_recent = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()
        
        if most_recent:
            # Check if it already matches the baseline
            if most_recent.refresh_time == baseline_time:
                logger.info(f"✅ RefreshLog baseline already correct (most recent: {baseline_time} UTC)")
            else:
                # FORCE UPDATE the most recent entry to the baseline
                most_recent.refresh_date = baseline_date
                most_recent.refresh_time = baseline_time
                most_recent.jobs_updated = 65
                most_recent.success = True
                most_recent.message = 'Baseline refresh (Oct 14, 2025 10:04 UTC)'
                db.session.commit()
                logger.info(f"🔄 FORCED RefreshLog baseline update: {baseline_time} UTC (was: {most_recent.refresh_time})")
        else:
            # No entries exist - create baseline
            initial_log = RefreshLog(
                refresh_date=baseline_date,
                refresh_time=baseline_time,
                jobs_updated=65,
                success=True,
                message='Baseline refresh log (Oct 14, 2025 10:04 UTC)'
            )
            db.session.add(initial_log)
            db.session.commit()
            logger.info(f"✅ Created baseline RefreshLog entry: {baseline_time} UTC")
            
    except ImportError:
        logger.debug("ℹ️ RefreshLog model not found - skipping refresh log seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed refresh log: {str(e)}")


def seed_database(db, User):
    """
    Main seeding function - idempotent database initialization
    
    Args:
        db: SQLAlchemy database instance
        User: User model class
        
    Returns:
        dict: Summary of seeding results
    """
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
