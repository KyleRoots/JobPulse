"""Auto-split from vetting_audit_service.py — see vetting_audit_service/__init__.py."""
import json
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

class _AuditCore:
    """Base mixin — initialization and shared state for VettingAuditService."""

    def __init__(self):
        self.openai_api_key = os.environ.get('OPENAI_API_KEY')
