"""Sanitize inbound email fields to ParsedEmail column limits.

Mass-blast recruiter emails sometimes put apply@ on a To: line with dozens
of other agencies. Storing the raw To string overflows varchar(255), poisons
the SQLAlchemy session, and blocks every subsequent applicant in the same
mailbox-pull cycle. Prefer the owned apply mailbox address when present.
"""
from __future__ import annotations

import re
from typing import Optional

_EMAIL_RE = re.compile(r'[\w.+-]+@[\w.-]+', re.I)


def clip_str(value: Optional[str], max_len: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return text
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + '...'


def extract_email_addrs(value: Optional[str]) -> list:
    if not value:
        return []
    return [m.group(0).lower() for m in _EMAIL_RE.finditer(str(value))]


def normalize_inbound_recipient(recipient: Optional[str], max_len: int = 255) -> str:
    """Pick a stable recipient for storage / Brand routing side effects.

    Prefer apply@* (owned intake). Fall back to the first address, then clip.
    """
    raw = (recipient or '').strip()
    if not raw:
        return ''
    addrs = extract_email_addrs(raw)
    preferred = next((a for a in addrs if a.startswith('apply@')), None)
    if preferred:
        return preferred[:max_len]
    if addrs:
        return addrs[0][:max_len]
    return clip_str(raw, max_len) or ''


def sanitize_parsed_email_fields(
    *,
    message_id: Optional[str],
    sender_email: Optional[str],
    recipient_email: Optional[str],
    subject: Optional[str] = None,
) -> dict:
    return {
        'message_id': clip_str(message_id, 255) if message_id else message_id,
        'sender_email': clip_str(sender_email or '', 255) or '',
        'recipient_email': normalize_inbound_recipient(recipient_email, 255),
        'subject': clip_str(subject, 500) if subject is not None else subject,
    }
