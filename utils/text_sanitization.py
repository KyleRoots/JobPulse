"""Shared text sanitization helpers.

PostgreSQL TEXT/VARCHAR columns reject NUL bytes (0x00). Resume parsers,
Bullhorn description fields, and recruiter copy-paste from PDFs/Word docs
occasionally embed NUL bytes that cause flush failures. Apply
``sanitize_text`` at every persistence boundary for fields sourced from
external systems (Bullhorn, OpenAI, user input) before assigning to ORM
columns.
"""
from typing import Optional


def sanitize_text(text: Optional[str]) -> Optional[str]:
    """Strip NUL bytes from a string before persistence.

    PostgreSQL does not allow ``\\x00`` in text values. Returns the input
    unchanged when ``text`` is None or empty. Coerces non-str inputs to
    str so callers can pass values straight from upstream payloads
    without pre-checking type.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    if '\x00' not in text:
        return text
    return text.replace('\x00', '')
