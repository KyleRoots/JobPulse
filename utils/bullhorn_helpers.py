import logging

logger = logging.getLogger(__name__)


def get_bullhorn_service():
    """Create a BullhornService instance with credentials loaded from GlobalSettings.
    
    This is the single source of truth for constructing authenticated BullhornService
    instances throughout the application. All code that needs a BullhornService should
    use this helper rather than manually loading credentials.
    
    Returns:
        BullhornService: An instance with credentials pre-loaded from the database.
    """
    from bullhorn_service import BullhornService
    from models import GlobalSettings

    credentials = {}
    for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
        try:
            setting = GlobalSettings.query.filter_by(setting_key=key).first()
            if setting and setting.setting_value:
                credentials[key] = setting.setting_value.strip()
        except Exception as e:
            logger.error(f"Error loading credential {key}: {str(e)}")

    return BullhornService(
        client_id=credentials.get('bullhorn_client_id'),
        client_secret=credentials.get('bullhorn_client_secret'),
        username=credentials.get('bullhorn_username'),
        password=credentials.get('bullhorn_password')
    )


def get_email_service():
    """Create an EmailService instance with database logging support.
    
    Returns:
        EmailService: An instance configured for sending and logging emails.
    """
    from app import db
    from email_service import EmailService
    from models import EmailDeliveryLog

    return EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
