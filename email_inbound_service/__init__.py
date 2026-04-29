from ._core import _InboundCore
from .extraction_mixin import ExtractionMixin
from .ai_mixin import AIMixin
from .resume_mixin import ResumeMixin
from .processing_mixin import ProcessingMixin


class EmailInboundService(
    ProcessingMixin,
    ResumeMixin,
    AIMixin,
    ExtractionMixin,
    _InboundCore,
):
    """Service for processing inbound emails from job boards and creating Bullhorn candidates"""


__all__ = ["EmailInboundService"]
