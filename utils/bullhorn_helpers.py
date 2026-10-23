import logging

logger = logging.getLogger(__name__)

# Bullhorn's Share link. The old cluster OpenWindow.cfm page now shows
# "This page is currently unavailable."
BULLHORN_RECORD_URL = "https://app.bullhornstaffing.com/openwindow"


def bullhorn_record_url(entity, record_id) -> str:
    """Public link that opens one Bullhorn record after login.

    Matches the Share link button: entity is Candidate, JobOrder,
    ClientCorporation, and the other record types Bullhorn puts in that URL.
    """
    if record_id is None or record_id == "":
        return ""
    try:
        record_id = int(record_id)
    except (TypeError, ValueError):
        return ""
    if record_id <= 0:
        return ""
    entity_name = str(entity or "").strip()
    if not entity_name:
        return ""
    return f"{BULLHORN_RECORD_URL}?entity={entity_name}&id={record_id}"


def get_bullhorn_service(environment=None):
    """Create a BullhornService for the given Bullhorn environment (tenant).

    This is the single source of truth for constructing authenticated
    BullhornService instances throughout the application. All code that needs a
    BullhornService should use this helper rather than manually loading
    credentials.

    Args:
        environment: An optional ``BullhornEnvironment`` instance. When ``None``
            the default (Myticas) environment is used. Credentials resolve from
            the environment's inline credential set when fully populated;
            otherwise they fall back to GlobalSettings. This preserves the
            historical single-tenant behavior byte-for-byte when the helper is
            called with no argument (the default environment carries no inline
            credentials, so the GlobalSettings path is taken exactly as before).

    Returns:
        BullhornService: An instance with credentials pre-loaded.
    """
    from bullhorn_service import BullhornService
    from models import GlobalSettings, BullhornEnvironment

    if environment is None:
        try:
            environment = BullhornEnvironment.get_default()
        except Exception as e:
            logger.error(f"Error resolving default Bullhorn environment: {str(e)}")
            environment = None

    credentials = None
    if environment is not None:
        try:
            credentials = environment.resolve_credentials()
        except Exception as e:
            logger.error(f"Error resolving environment credentials: {str(e)}")
            credentials = None

    if not credentials:
        # Default-environment / legacy path: credentials live in GlobalSettings.
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
