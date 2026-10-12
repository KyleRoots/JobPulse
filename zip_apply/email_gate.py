"""Zip mailbox intake switch.

Webhook applications are the path we are proving. Zip notification emails
keep being parsed until ZIP_EMAIL_INTAKE_ENABLED is set false, and only
after that webhook path is deployed and a live applicant has landed.
The webhook itself is marked so this switch cannot drop it.
"""
from __future__ import annotations

import os

_WEBHOOK_MARKER = "X-Scout-Zip-Apply: webhook"


def zip_email_intake_enabled() -> bool:
    """True while Zip notification emails should still create candidates."""
    raw = (os.environ.get("ZIP_EMAIL_INTAKE_ENABLED") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def should_ignore_zip_mailbox(source: str, headers: str) -> bool:
    """Ignore a Zip board email once intake is turned off.

    Synthetic webhook payloads carry the marker and are never ignored here.
    """
    if source != "ZipRecruiter Job Board":
        return False
    if zip_email_intake_enabled():
        return False
    if _WEBHOOK_MARKER in (headers or ""):
        return False
    return True
