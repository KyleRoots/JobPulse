"""
Scout Genius — BullhornService package.

Note: `requests` is re-exported at the package level so legacy test patches
of the form `mock.patch('bullhorn_service.requests.get')` keep working
exactly as they did when this was a single .py module.

This is the modular successor to the former bullhorn_service.py monolith
(2,610 lines, 58 methods). The class is composed from focused mixins:

  - _BullhornCore       : __init__, class constants, low-level helpers
  - AuthMixin           : OAuth/REST authentication + connection test
  - JobsMixin           : JobOrder retrieval, search, comparison
  - TearsheetsMixin     : Tearsheet CRUD + member management
  - CandidatesMixin     : Candidate CRUD, file upload, work history, education
  - NotesMixin          : Candidate note retrieval and creation
  - EntitiesMixin       : Generic entity CRUD + meta/options/settings

Public import surface is preserved:

    from bullhorn_service import BullhornService

All ~58 method signatures are unchanged. Behavior is identical: each method's
source bytes were spliced from the original file via AST.
"""
import requests  # noqa: F401  (re-export — legacy patch path: bullhorn_service.requests)

from bullhorn_service._core import _BullhornCore
from bullhorn_service.auth import AuthMixin
from bullhorn_service.jobs import JobsMixin
from bullhorn_service.tearsheets import TearsheetsMixin
from bullhorn_service.candidates import CandidatesMixin
from bullhorn_service.notes import NotesMixin
from bullhorn_service.entities import EntitiesMixin


class BullhornService(
    AuthMixin,
    JobsMixin,
    TearsheetsMixin,
    CandidatesMixin,
    NotesMixin,
    EntitiesMixin,
    _BullhornCore,
):
    """Service for interacting with Bullhorn ATS/CRM API.

    Composed from focused mixins — see bullhorn_service/__init__.py for layout.
    """
    pass


__all__ = ['BullhornService']
