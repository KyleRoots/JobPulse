"""
Database Seeding Script for JobPulse Application

This script provides idempotent database seeding for production deployments.
It creates admin users, SFTP settings, and automation configuration automatically
from environment variables.

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
    
    System:
    - REPLIT_DEPLOYMENT: Detected automatically (production indicator)
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


def seed_global_settings(db, GlobalSettings):
    """
    Seed global settings including SFTP config and automation toggles
    
    Args:
        db: SQLAlchemy database instance
        GlobalSettings: GlobalSettings model class
    """
    try:
        sftp_config = get_sftp_config()
        
        # Define settings to seed with their values
        settings_to_seed = {
            # SFTP Configuration
            'sftp_hostname': sftp_config['sftp_hostname'],
            'sftp_username': sftp_config['sftp_username'],
            'sftp_password': sftp_config['sftp_password'],
            'sftp_port': sftp_config['sftp_port'],
            'sftp_directory': sftp_config['sftp_directory'],
            
            # Automation Toggles (enable by default in production)
            'sftp_enabled': 'true' if is_production_environment() else 'false',
            'automated_uploads_enabled': 'true' if is_production_environment() else 'false'
        }
        
        settings_updated = []
        settings_created = []
        
        for key, value in settings_to_seed.items():
            # Find existing setting
            existing = GlobalSettings.query.filter_by(setting_key=key).first()
            
            if existing:
                # Update if value is different (and not empty for SFTP credentials)
                if key in ['sftp_hostname', 'sftp_username', 'sftp_password', 'sftp_port', 'sftp_directory']:
                    # Only update SFTP settings if new value is not empty
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
                # Create new setting (skip SFTP if value is empty)
                if key in ['sftp_hostname', 'sftp_username', 'sftp_password', 'sftp_port', 'sftp_directory']:
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
        'errors': []
    }
    
    try:
        # Create admin user (idempotent)
        admin_user, created = create_admin_user(db, User)
        results['admin_created'] = created
        results['admin_username'] = admin_user.username
        results['admin_email'] = admin_user.email
        
        # Seed global settings including SFTP config
        try:
            from models import GlobalSettings
            seed_global_settings(db, GlobalSettings)
            results['sftp_configured'] = True
        except ImportError:
            logger.debug("ℹ️ GlobalSettings model not found - skipping SFTP seeding")
        except Exception as e:
            logger.warning(f"⚠️ Failed to seed global settings: {str(e)}")
        
        # Seed environment monitoring (production only)
        if is_production_environment():
            seed_environment_monitoring(db)
        
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
        print("\n" + "="*60)
        print("DATABASE SEEDING SUMMARY")
        print("="*60)
        print(f"Environment: {results['environment']}")
        print(f"Admin Created: {'Yes' if results['admin_created'] else 'No (already exists)'}")
        print(f"Admin Username: {results.get('admin_username', 'N/A')}")
        print(f"Admin Email: {results.get('admin_email', 'N/A')}")
        print(f"SFTP Configured: {'Yes' if results.get('sftp_configured', False) else 'No'}")
        
        if results['errors']:
            print(f"\nErrors: {len(results['errors'])}")
            for error in results['errors']:
                print(f"  - {error}")
        else:
            print("\nStatus: SUCCESS ✅")
        
        print("="*60 + "\n")


if __name__ == '__main__':
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    run_seeding()
