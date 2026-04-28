"""
Scout Genius — XMLIntegrationService package.

This is the modular successor to the former xml_integration_service.py monolith
(2,241 lines, 35 methods). The class is composed from focused mixins:

  - _XMLCore           : __init__, static LinkedIn formatters
  - MappingMixin       : Bullhorn job dict → XML field mapping + cleaners
  - ValidationMixin    : pre/post-write validation + change detection
  - FileOpsMixin       : safe write, backup rotation, sort, whitespace cleanup
  - JobsMixin          : add/remove/update single job, regenerate full feed
  - SyncMixin          : full feed sync + orphan detection/removal

Public import surface is preserved:

    from xml_integration_service import XMLIntegrationService

All 35 method signatures are unchanged. Behavior is identical: each method's
source bytes were spliced from the original file via AST.
"""
from xml_integration_service._core import _XMLCore
from xml_integration_service.mapping import MappingMixin
from xml_integration_service.validation import ValidationMixin
from xml_integration_service.file_ops import FileOpsMixin
from xml_integration_service.jobs import JobsMixin
from xml_integration_service.sync import SyncMixin


class XMLIntegrationService(
    SyncMixin,
    JobsMixin,
    MappingMixin,
    ValidationMixin,
    FileOpsMixin,
    _XMLCore,
):
    """Service for integrating Bullhorn job data with XML files.

    Composed from focused mixins — see xml_integration_service/__init__.py for layout.
    """
    pass


__all__ = ['XMLIntegrationService']
