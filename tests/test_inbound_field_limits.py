"""Unit tests for inbound ParsedEmail field clipping / recipient normalize."""
from email_inbound_service.field_limits import (
    clip_str,
    normalize_inbound_recipient,
    sanitize_parsed_email_fields,
)


def test_normalize_prefers_apply_mailbox_in_mass_blast():
    blast = (
        'Myticas Apply <Apply@myticas.com>, prateek.gautam@irissoftware.com, '
        + ', '.join(f'user{i}@example.com' for i in range(80))
    )
    assert len(blast) > 255
    assert normalize_inbound_recipient(blast) == 'apply@myticas.com'


def test_sanitize_clips_sender_and_subject():
    safe = sanitize_parsed_email_fields(
        message_id='<' + ('a' * 300) + '@x.com>',
        sender_email='Name <' + ('b' * 300) + '@x.com>',
        recipient_email='apply@myticas.com',
        subject='S' * 600,
    )
    assert len(safe['message_id']) <= 255
    assert len(safe['sender_email']) <= 255
    assert len(safe['subject']) <= 500
    assert safe['recipient_email'] == 'apply@myticas.com'


def test_clip_str_short_unchanged():
    assert clip_str('hello', 255) == 'hello'
