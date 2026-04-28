"""Module-level constants shared across the automation_service package.

Lives in its own module so mixins (`dispatch_mixin.py`) can import these
sets directly without depending on `__init__.py` (which would create a
circular-import / partial-import problem because `__init__.py` imports
the mixins to compose AutomationService).
"""

LONG_RUNNING_BUILTINS = {
    "update_field_bulk",
    "cleanup_ai_notes",
    "cleanup_duplicate_notes",
    "resume_reparser",
    "export_qualified",
    "email_extractor",
    "retry_recruiter_notifications",
    "screening_audit",
    "duplicate_merge_scan",
    "occupation_extractor",
}

NO_BULLHORN_BUILTINS = {
    "screening_audit",
}
