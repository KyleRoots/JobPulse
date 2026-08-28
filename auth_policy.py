"""Shared authentication policy (Phase 1 hardening).

Password rules apply to new passwords (reset, admin-set). Existing shorter
passwords remain valid until changed.
"""
from __future__ import annotations

MIN_PASSWORD_LENGTH = 12

# Flask session cookies (non-remember-me path when session.permanent=True)
SESSION_LIFETIME_HOURS = 12

# Flask-Login remember-me cookie
REMEMBER_ME_DAYS = 7


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Return (ok, user-facing error message)."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return (
            False,
            f'Password must be at least {MIN_PASSWORD_LENGTH} characters.',
        )
    return True, ''
