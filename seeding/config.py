"""Environment-driven config providers for admin/SFTP/Bullhorn.

These helpers translate environment variables into structured config
dictionaries consumed by the user/settings seeders. They never touch the
database — they only read from `os.environ`.
"""

import os
import logging

logger = logging.getLogger(__name__)


def get_admin_config():
    """
    Get admin user configuration from environment variables

    Returns:
        dict: Admin user configuration
    """
    # Late import so test patches on `seed_database.is_production_environment`
    # are observed at call time.
    import seed_database

    # Get configuration from environment with safe defaults
    username = os.environ.get('ADMIN_USERNAME', 'admin')
    email = os.environ.get('ADMIN_EMAIL', 'kroots@myticas.com')
    password = os.environ.get('ADMIN_PASSWORD')

    # In production, password MUST be set via environment variable
    if seed_database.is_production_environment() and not password:
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
