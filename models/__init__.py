"""Scout Genius database models — re-exports from domain modules.

Maintains backward compatibility with the prior monolithic `models.py`.
Every class previously imported via `from models import X` continues to work.

Domain layout:
    user         — User auth + activity (User, PasswordResetToken, UserActivityLog, RecruiterMapping)
    scheduling   — XML schedules, processing logs, global settings, scheduler lock
    ats          — Bullhorn monitors, activity, tearsheet history, parsed emails, owner reassignment cooldown
    health       — Environment status / alerts, health checks, log monitoring, backups, OneDrive sync
    vetting      — Candidate vetting logs, job matches, requirements, config, scout vetting sessions, audit, escalation
    candidate    — Resume cache, profile embedding, merge log, fuzzy queue
    embedding    — Job-side embeddings + filter audit
    automation   — Automation Hub task / log / chat
    support      — Scout Support tickets, conversations, attachments, actions, knowledge hub
    prospector   — Scout Prospector profiles, runs, prospects
"""
from models.user import (
    AVAILABLE_MODULES,
    User,
    UserActivityLog,
    RecruiterMapping,
    PasswordResetToken,
)
from models.scheduling import (
    ScheduleConfig,
    ProcessingLog,
    JobReferenceNumber,
    RefreshLog,
    GlobalSettings,
    EmailParsingConfig,  # Backward-compat alias for GlobalSettings
    SchedulerLock,
)
from models.ats import (
    BullhornMonitor,
    BullhornActivity,
    TearsheetJobHistory,
    EmailDeliveryLog,
    ParsedEmail,
    OwnerReassignmentCooldown,
)
from models.health import (
    EnvironmentStatus,
    EnvironmentAlert,
    VettingHealthCheck,
    LogMonitoringRun,
    LogMonitoringIssue,
    BackupLog,
    OneDriveSyncFolder,
)
from models.vetting import (
    CandidateVettingLog,
    CandidateJobMatch,
    JobVettingRequirements,
    VettingConfig,
    EscalationLog,
    ScoutVettingSession,
    VettingConversationTurn,
    VettingAuditLog,
)
from models.candidate import (
    ParsedResumeCache,
    CandidateMergeLog,
    CandidateProfileEmbedding,
    FuzzyEvaluationQueue,
)
from models.embedding import (
    JobEmbedding,
    EmbeddingFilterLog,
    EmbeddingABLog,
)
from models.automation import (
    AutomationTask,
    AutomationLog,
    AutomationChat,
)
from models.support import (
    SupportContact,
    SupportTicket,
    SupportAttachment,
    SupportConversation,
    SupportAction,
    KnowledgeDocument,
    KnowledgeEntry,
)
from models.prospector import (
    ProspectorProfile,
    ProspectorRun,
    Prospect,
)
from models.openai_telemetry import OpenAICallLog
from models.cost_forecast import CostForecastOverride, CostForecastScenario

__all__ = [
    # user
    'AVAILABLE_MODULES', 'User', 'UserActivityLog', 'RecruiterMapping', 'PasswordResetToken',
    # scheduling
    'ScheduleConfig', 'ProcessingLog', 'JobReferenceNumber', 'RefreshLog',
    'GlobalSettings', 'EmailParsingConfig', 'SchedulerLock',
    # ats
    'BullhornMonitor', 'BullhornActivity', 'TearsheetJobHistory',
    'EmailDeliveryLog', 'ParsedEmail', 'OwnerReassignmentCooldown',
    # health
    'EnvironmentStatus', 'EnvironmentAlert', 'VettingHealthCheck',
    'LogMonitoringRun', 'LogMonitoringIssue', 'BackupLog', 'OneDriveSyncFolder',
    # vetting
    'CandidateVettingLog', 'CandidateJobMatch', 'JobVettingRequirements',
    'VettingConfig', 'EscalationLog', 'ScoutVettingSession',
    'VettingConversationTurn', 'VettingAuditLog',
    # candidate
    'ParsedResumeCache', 'CandidateMergeLog', 'CandidateProfileEmbedding', 'FuzzyEvaluationQueue',
    # embedding
    'JobEmbedding', 'EmbeddingFilterLog', 'EmbeddingABLog',
    # automation
    'AutomationTask', 'AutomationLog', 'AutomationChat',
    # support
    'SupportContact', 'SupportTicket', 'SupportAttachment',
    'SupportConversation', 'SupportAction', 'KnowledgeDocument', 'KnowledgeEntry',
    # prospector
    'ProspectorProfile', 'ProspectorRun', 'Prospect',
    # telemetry
    'OpenAICallLog',
]
