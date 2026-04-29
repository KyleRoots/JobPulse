from sendgrid import SendGridAPIClient

from ._core import _EmailServiceCore
from .xml_notifications_mixin import XMLNotificationsMixin
from .bullhorn_notification_mixin import BullhornNotificationMixin


class EmailService(
    XMLNotificationsMixin,
    BullhornNotificationMixin,
    _EmailServiceCore,
):
    """Handles email notifications for XML processing"""


__all__ = [
    "EmailService",
    "SendGridAPIClient",
]
