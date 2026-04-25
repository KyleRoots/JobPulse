"""SQLAlchemy column types that auto-sanitize text at the persistence boundary.

PostgreSQL TEXT/VARCHAR columns reject NUL bytes (``\\x00``). External data
sources — most commonly Bullhorn candidate descriptions, OpenAI completions,
and uploaded resumes — occasionally embed NUL bytes from PDF/Word artifacts
that cause flush failures and silently drop entire candidates from screening.

Historically every persistence boundary called :func:`sanitize_text` manually
before assignment. That works but is load-bearing on developer discipline:
one missed call and the bug returns. These ``TypeDecorator`` subclasses move
the sanitization into the column type itself so it runs automatically inside
SQLAlchemy regardless of caller behavior.

Usage::

    from utils.sqlalchemy_types import SafeText, SafeString

    class CandidateVettingLog(db.Model):
        candidate_name = db.Column(SafeString(255), nullable=True)
        resume_text = db.Column(SafeText, nullable=True)

The underlying database column type is unchanged — ``SafeText`` stores as
``TEXT`` and ``SafeString(N)`` stores as ``VARCHAR(N)``. No schema migration
is required when swapping in or out.

Existing manual ``sanitize_text()`` calls upstream are not removed; they
remain as defence in depth. They are simply no longer load-bearing.
"""
from typing import Optional

from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator

from utils.text_sanitization import sanitize_text


class SafeText(TypeDecorator):
    """``TEXT`` column that strips NUL bytes on every bind."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        return sanitize_text(value)


class SafeString(TypeDecorator):
    """``VARCHAR(length)`` column that strips NUL bytes on every bind.

    Accepts ``length`` so it remains a drop-in replacement for
    ``db.String(N)``::

        candidate_name = db.Column(SafeString(255), nullable=True)
    """

    impl = String
    cache_ok = True

    def __init__(self, length: Optional[int] = None, *args, **kwargs):
        super().__init__(length=length, *args, **kwargs)

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        return sanitize_text(value)
