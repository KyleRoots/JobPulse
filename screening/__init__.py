"""
Scout Screening Engine - modular package.

Splits the monolithic CandidateVettingService into focused mixins:
- PromptBuilderMixin: AI prompt construction and GPT response post-processing
- NoteBuilderMixin: Bullhorn note formatting and creation
- NotificationMixin: Recruiter email notifications
- CandidateDetectionMixin: Candidate discovery from Bullhorn / ParsedEmail
- JobManagementMixin: Tearsheet job retrieval, requirements extraction, sync
- RecoveryMixin: Auto-retry for stuck/failed vetting runs
"""

from screening.prompt_builder import PromptBuilderMixin
from screening.note_builder import NoteBuilderMixin
from screening.notification import NotificationMixin
from screening.detection import CandidateDetectionMixin
from screening.job_management import JobManagementMixin
from screening.recovery import RecoveryMixin

__all__ = [
    'PromptBuilderMixin',
    'NoteBuilderMixin',
    'NotificationMixin',
    'CandidateDetectionMixin',
    'JobManagementMixin',
    'RecoveryMixin',
]
