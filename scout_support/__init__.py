"""
Scout Support Engine — modular package.

Splits the monolithic ScoutSupportService into focused mixins:
- EmailMixin: Email sending, quoted history, stakeholder notifications
- AIAnalysisMixin: AI understanding, clarification analysis, attachment extraction, vision
- ExecutionMixin: Bullhorn API action execution, entity CRUD, note creation
- AuditMixin: Audit note creation, change humanization
- ConversationMixin: Reply handling, classification, approval flow, admin Q&A
"""

from scout_support.email import EmailMixin
from scout_support.ai_analysis import AIAnalysisMixin
from scout_support.execution import ExecutionMixin
from scout_support.audit import AuditMixin
from scout_support.conversation import ConversationMixin

__all__ = [
    'EmailMixin',
    'AIAnalysisMixin',
    'ExecutionMixin',
    'AuditMixin',
    'ConversationMixin',
]
