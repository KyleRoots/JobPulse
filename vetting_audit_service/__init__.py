"""vetting_audit_service package — composed from focused mixins.

Public import surface preserved:
    from vetting_audit_service import VettingAuditService
    from vetting_audit_service import (
        backfill_revet_new_score,
        get_platform_age_ceilings,
        get_auditor_model,
        get_qualified_sample_rate,
        get_revet_cap_per_24h,
        get_revet_score_tolerance,
        get_audit_cooldown_hours,
        DEFAULT_PLATFORM_AGE_CEILINGS,
        DEFAULT_AUDITOR_MODEL,
        DEFAULT_QUALIFIED_SAMPLE_RATE,
        DEFAULT_REVET_CAP_PER_24H,
        DEFAULT_REVET_SCORE_TOLERANCE,
        DEFAULT_AUDIT_COOLDOWN_HOURS,
        PLATFORM_AGE_CEILINGS,
        DOMAIN_KEYWORDS,
    )
"""
from .helpers import (
    DEFAULT_PLATFORM_AGE_CEILINGS,
    DEFAULT_AUDITOR_MODEL,
    DEFAULT_QUALIFIED_SAMPLE_RATE,
    DEFAULT_REVET_CAP_PER_24H,
    DEFAULT_REVET_SCORE_TOLERANCE,
    DEFAULT_AUDIT_COOLDOWN_HOURS,
    PLATFORM_AGE_CEILINGS,
    DOMAIN_KEYWORDS,
    get_platform_age_ceilings,
    get_auditor_model,
    backfill_revet_new_score,
    get_qualified_sample_rate,
    get_revet_cap_per_24h,
    get_revet_score_tolerance,
    get_audit_cooldown_hours,
)
from ._core import _AuditCore
from .orchestration_mixin import OrchestrationMixin
from .revet_mixin import RevetMixin
from .heuristics_mixin import HeuristicsMixin
from .ai_audit_mixin import AIAuditMixin
from .notification_mixin import NotificationMixin


class VettingAuditService(
    OrchestrationMixin,
    RevetMixin,
    HeuristicsMixin,
    AIAuditMixin,
    NotificationMixin,
    _AuditCore,
):
    """AI-powered quality auditor for Scout Screening results.

    Tier 1: Runs heuristic checks on recent Not Qualified results,
    confirms findings with AI, and auto-triggers re-vets for
    high-confidence misfires.
    """


__all__ = [
    "VettingAuditService",
    "backfill_revet_new_score",
    "get_platform_age_ceilings",
    "get_auditor_model",
    "get_qualified_sample_rate",
    "get_revet_cap_per_24h",
    "get_revet_score_tolerance",
    "get_audit_cooldown_hours",
    "DEFAULT_PLATFORM_AGE_CEILINGS",
    "DEFAULT_AUDITOR_MODEL",
    "DEFAULT_QUALIFIED_SAMPLE_RATE",
    "DEFAULT_REVET_CAP_PER_24H",
    "DEFAULT_REVET_SCORE_TOLERANCE",
    "DEFAULT_AUDIT_COOLDOWN_HOURS",
    "PLATFORM_AGE_CEILINGS",
    "DOMAIN_KEYWORDS",
]
