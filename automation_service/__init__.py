"""Public surface for the automation_service package.

Composes `AutomationService` from the focused mixins and re-exports the
module-level constants (`LONG_RUNNING_BUILTINS`, `NO_BULLHORN_BUILTINS`)
that callers like `routes/automations.py` and `scheduler_setup.py` rely
on. The legacy import path `from automation_service import AutomationService`
continues to work after the split.
"""
import logging

from automation_service.constants import (
    LONG_RUNNING_BUILTINS,
    NO_BULLHORN_BUILTINS,
)
from automation_service._core import _AutomationCore
from automation_service.tasks_mixin import TasksMixin
from automation_service.dispatch_mixin import DispatchMixin
from automation_service.notes_mixin import NotesMixin
from automation_service.matching_mixin import MatchingMixin
from automation_service.resume_mixin import ResumeMixin
from automation_service.notifications_mixin import NotificationsMixin

logger = logging.getLogger(__name__)


class AutomationService(
    TasksMixin,
    DispatchMixin,
    NotesMixin,
    MatchingMixin,
    ResumeMixin,
    NotificationsMixin,
    _AutomationCore,
):
    """AutomationService — entry point for the Automation Hub builtins.

    Composed from focused mixins. See each mixin module for the cluster
    of methods it owns. The MRO places `_AutomationCore` last so its
    `__init__`, `bullhorn` property, and HTTP helpers can be overridden
    by other mixins without affecting boot order — currently no mixin
    overrides them.
    """
    pass


__all__ = ["AutomationService", "LONG_RUNNING_BUILTINS", "NO_BULLHORN_BUILTINS"]
