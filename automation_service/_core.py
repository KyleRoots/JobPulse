"""Core init, lazy Bullhorn property, and low-level Bullhorn URL/header helpers shared by every mixin.

Part of the `automation_service` package — the monolithic
`automation_service.py` (1,839 lines) was split into focused mixins so
each cluster of related builtins lives next to its helpers.
"""
import json
import logging
import threading
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from extensions import db
from models import AutomationTask, AutomationLog, AutomationChat

logger = logging.getLogger(__name__)


class _AutomationCore:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._bullhorn = None

    @property

    def bullhorn(self):
        if self._bullhorn is None:
            from bullhorn_service import BullhornService
            self._bullhorn = BullhornService()
        return self._bullhorn

    def _bh_headers(self):
        return {
            'BhRestToken': self.bullhorn.rest_token,
            'Content-Type': 'application/json'
        }

    def _bh_url(self):
        return self.bullhorn.base_url

