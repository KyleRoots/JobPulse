"""Core base for XMLIntegrationService — __init__, class constants, and shared static helpers."""
import os
import logging
import re
import shutil
import time
import threading
import urllib.parse
import html
from typing import Dict, List, Optional
from datetime import datetime
try:
    from lxml import etree
except ImportError:
    from xml_safe_compat import safe_etree as etree
from xml_processor import XMLProcessor
from job_classification_service import JobClassificationService, InternalJobClassifier
from xml_safeguards import XMLSafeguards
from tearsheet_config import TearsheetConfig
from utils.field_mappers import map_employment_type, map_remote_type

logger = logging.getLogger(__name__)


class _XMLCore:
    """Base class holding __init__ and stateless LinkedIn formatters.

    The static methods (format_linkedin_recruiter_tag, sanitize_linkedin_recruiter_tag)
    are kept on the core class so they remain accessible as
    XMLIntegrationService.format_linkedin_recruiter_tag(...) for any
    consumer that calls them via the class rather than an instance.
    """

    @staticmethod
    def format_linkedin_recruiter_tag(assigned_users: List[Dict]) -> str:
        """
        Centralized LinkedIn recruiter tag formatter - ensures consistent format across all code paths
        Returns exactly "#LI-XXn" without trailing colons or names
        
        Args:
            assigned_users: List of assigned user dictionaries from Bullhorn
            
        Returns:
            str: LinkedIn tag in format "#LI-XXn" or empty string
        """
        if not assigned_users or not isinstance(assigned_users, list):
            return ""
        
        for user in assigned_users:
            if isinstance(user, dict) and user.get('linkedInCompanyID'):
                company_id = str(user.get('linkedInCompanyID', '')).strip()
                if company_id:
                    # Return strictly the LinkedIn tag without colons or names
                    return f"#LI-{company_id}"
        
        return ""
    @staticmethod
    def sanitize_linkedin_recruiter_tag(assignedrecruiter_value: str) -> str:
        """
        Defensive sanitizer to strip trailing colons and names from existing assignedrecruiter values
        Ensures format is exactly "#LI-XXn" with no trailing content
        
        Args:
            assignedrecruiter_value: Raw assignedrecruiter field value
            
        Returns:
            str: Sanitized LinkedIn tag or empty string if not a valid LinkedIn tag
        """
        if not assignedrecruiter_value:
            return ""
        
        # If it's a LinkedIn tag with or without trailing content, extract just the tag part without colon
        # Pattern matches #LI-[alphanumeric] and stops at word boundary to avoid punctuation
        linkedin_match = re.match(r'^\s*(#LI-[A-Za-z0-9]+)\b', assignedrecruiter_value.strip())
        if linkedin_match:
            return linkedin_match.group(1)  # Return just the tag part without colon
        
        # If it's not a LinkedIn tag, return empty string to prevent names from leaking
        return ""
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.xml_processor = XMLProcessor()
        self.job_classifier = JobClassificationService(use_ai=False)  # Keyword-only classification
        self.safeguards = XMLSafeguards()
        self._parser = etree.XMLParser(strip_cdata=False, recover=True)
        # Store field changes for notifications
        self._last_field_changes = {}
        # Cache for recruiter mappings
        self._recruiter_cache = {}
        # Thread lock for preventing concurrent XML modifications
        self._xml_lock = threading.Lock()
