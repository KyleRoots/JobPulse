"""Feature flags for Myticas client onboarding notify (observe-only)."""
from __future__ import annotations

import os
from pathlib import Path

ACCOUNTING_EMAIL_DEFAULT = "accounting@myticas.com"
TEST_EMAIL_DEFAULT = "kroots@myticas.com"

CHECKLIST_FILENAME = "MYTICAS_New_Client_Onboarding_Checklist_Ottawa.docx"
CHECKLIST_RELATIVE = Path("data") / "client_onboarding" / CHECKLIST_FILENAME


def _flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    return _flag("CLIENT_OB_NOTIFY_ENABLED", True)


def is_live() -> bool:
    """When False, all mail goes to the test inbox (intended To/CC in the body)."""
    return _flag("CLIENT_OB_NOTIFY_LIVE", False)


def test_email() -> str:
    return (os.environ.get("CLIENT_OB_NOTIFY_TEST_EMAIL") or TEST_EMAIL_DEFAULT).strip()


def bcc_email() -> str:
    return (
        os.environ.get("CLIENT_OB_NOTIFY_BCC_EMAIL") or TEST_EMAIL_DEFAULT
    ).strip()


def accounting_email() -> str:
    return (
        os.environ.get("CLIENT_OB_NOTIFY_ACCOUNTING_EMAIL") or ACCOUNTING_EMAIL_DEFAULT
    ).strip()


def go_live_at_from_env():
    """Optional ISO timestamp. Companies with dateAdded before this are skipped."""
    raw = (os.environ.get("CLIENT_OB_NOTIFY_GO_LIVE_AT") or "").strip()
    if not raw:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def checklist_path() -> Path:
    override = (os.environ.get("CLIENT_OB_NOTIFY_CHECKLIST_PATH") or "").strip()
    if override:
        return Path(override)
    root = Path(__file__).resolve().parent.parent
    return root / CHECKLIST_RELATIVE
