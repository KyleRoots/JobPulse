"""
Candidate Vetting Service - GPT-5.4 powered candidate-job matching engine

This service monitors new job applicants with "Online Applicant" status,
analyzes their resumes against all open positions in monitored tearsheets,
and notifies recruiters when candidates match at 80%+ threshold.
"""

import logging
import threading

from bullhorn_service import BullhornService
from email_service import EmailService
from vetting.geo_utils import map_work_type

from app import db
from models import (
    CandidateVettingLog, CandidateJobMatch, VettingConfig,
    BullhornMonitor, GlobalSettings, JobVettingRequirements, ParsedEmail,
    EmailDeliveryLog,
)

from screening import (
    PromptBuilderMixin,
    NoteBuilderMixin,
    NotificationMixin,
    CandidateDetectionMixin,
    JobManagementMixin,
    RecoveryMixin,
)

from .config import VettingConfigMixin
from .locking import VettingLockMixin
from .processing import CandidateProcessingMixin
from .cycle import VettingCycleMixin

logger = logging.getLogger(__name__)


class CandidateVettingService(
    VettingConfigMixin,
    VettingLockMixin,
    CandidateProcessingMixin,
    VettingCycleMixin,
    PromptBuilderMixin,
    NoteBuilderMixin,
    NotificationMixin,
    CandidateDetectionMixin,
    JobManagementMixin,
    RecoveryMixin,
):
    """
    GPT-5.4 powered candidate vetting system that:
    1. Detects new Online Applicant candidates in Bullhorn
    2. Extracts and analyzes their resumes
    3. Compares against all jobs in monitored tearsheets
    4. Creates notes on all candidates (qualified and not)
    5. Sends email notifications for qualified matches (80%+)
    """

    _active_job_ids_cache: set = None
    _active_job_ids_cache_time: float = 0
    _ACTIVE_JOB_IDS_TTL = 300

    _consecutive_quota_errors: int = 0
    _quota_alert_sent: bool = False
    _bullhorn_lock = threading.Lock()

    def __init__(self, bullhorn_service: BullhornService = None):
        self.bullhorn = bullhorn_service
        self.email_service = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
        self.openai_client = None
        self._init_openai()

        self.match_threshold = 80.0
        self.check_interval_minutes = 5
        self.model = self._get_layer2_model()

        from embedding_service import EmbeddingService
        self.embedding_service = EmbeddingService()


__all__ = ['CandidateVettingService', 'map_work_type']
