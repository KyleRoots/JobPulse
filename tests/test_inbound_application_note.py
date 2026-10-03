"""Qualified application snapshot note: job/company/owner/source for recruiters."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from inbound_application_note import (
    APPLICATION_NOTE_ACTION,
    applicant_label_for_source,
    build_application_note_text,
    format_applied_at,
    recruiter_from_job,
    snapshot_already_present,
    write_application_received_note,
)


def test_labels_follow_source():
    assert applicant_label_for_source('Indeed Job Board') == 'Indeed Applicant'
    assert applicant_label_for_source('Corporate Website') == 'Web Applicant'
    assert applicant_label_for_source('ZipRecruiter Job Board') == 'ZipRecruiter Applicant'
    assert applicant_label_for_source('LinkedIn Job Board') == 'LinkedIn Applicant'


def test_king_catron_preview_shape():
    applied = datetime(2026, 10, 1, 23, 18, tzinfo=timezone.utc)
    text = build_application_note_text(
        source='Indeed Job Board',
        title='Production Worker',
        company='SC Johnson Wisconsin',
        recruiter='Laura Madsen',
        applied_at=applied,
    )
    assert text.startswith(
        '<b>Indeed Applicant</b> for <b>Production Worker</b> at '
        '<b>SC Johnson Wisconsin</b> under <b>Laura Madsen</b> on '
    )
    assert 'October 1, 2026 at 7:18 PM' in text
    assert text.endswith('Indeed Job Board')
    assert 'AI-Generated Resume Summary' not in text


def test_web_applicant_preview_shape():
    applied = datetime(2026, 10, 1, 3, 27, tzinfo=timezone.utc)
    text = build_application_note_text(
        source='Corporate Website',
        title='Food Packaging Associate (All Shifts)',
        company='Garden Fresh Gourmet',
        recruiter='Monica Myska',
        applied_at=applied,
    )
    assert '<b>Web Applicant</b> for <b>Food Packaging Associate (All Shifts)</b>' in text
    assert 'Garden Fresh Gourmet' in text
    assert 'Monica Myska' in text
    assert text.endswith('Corporate Website')


def test_html_escapes_job_title():
    text = build_application_note_text(
        source='Indeed Job Board',
        title='Cook <script>',
        company='A & B',
        recruiter='Ann',
        applied_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert '<script>' not in text
    assert 'Cook &lt;script&gt;' in text
    assert 'A &amp; B' in text


def test_recruiter_prefers_assigned_user():
    job = {
        'assignedUsers': {
            'data': [{'firstName': 'Laura', 'lastName': 'Madsen'}],
        },
        'owner': {'firstName': 'Wendy', 'lastName': 'Phillips'},
    }
    assert recruiter_from_job(job) == 'Laura Madsen'


def test_dedup_matches_same_job_and_company():
    existing = [{
        'action': APPLICATION_NOTE_ACTION,
        'comments': build_application_note_text(
            source='Indeed Job Board',
            title='Production Worker',
            company='SC Johnson Wisconsin',
            recruiter='Laura Madsen',
            applied_at=datetime(2026, 10, 1, 23, 18, tzinfo=timezone.utc),
        ),
    }]
    assert snapshot_already_present(
        existing, title='Production Worker', company='SC Johnson Wisconsin'
    )
    assert not snapshot_already_present(
        existing, title='Food Packaging Associate (All Shifts)', company='Garden Fresh Gourmet'
    )


def test_format_applied_at_eastern():
    applied = datetime(2026, 10, 1, 23, 18, tzinfo=timezone.utc)
    assert format_applied_at(applied) == 'Thursday, October 1, 2026 at 7:18 PM'
    ms = int(applied.timestamp() * 1000)
    assert format_applied_at(ms) == 'Thursday, October 1, 2026 at 7:18 PM'


def test_write_skips_when_not_qualified(monkeypatch):
    monkeypatch.delenv('SCOUT_TENANT', raising=False)
    bh = MagicMock()
    assert write_application_received_note(bh, 1, 2, source='Indeed Job Board') is None
    bh.create_candidate_note.assert_not_called()


def test_write_creates_note_on_qualified(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    bh = MagicMock()
    bh.get_entity.return_value = {
        'id': 79398,
        'title': 'Production Worker',
        'clientCorporation': {'name': 'SC Johnson Wisconsin'},
        'assignedUsers': {'data': [{'firstName': 'Laura', 'lastName': 'Madsen'}]},
    }
    bh.get_candidate_notes.return_value = []
    bh.create_candidate_note.return_value = 555
    note_id = write_application_received_note(
        bh, 3339038, 79398, source='Indeed Job Board',
        applied_at=datetime(2026, 10, 1, 23, 18, tzinfo=timezone.utc),
    )
    assert note_id == 555
    args, kwargs = bh.create_candidate_note.call_args
    assert args[0] == 3339038
    assert args[2] == APPLICATION_NOTE_ACTION
    assert 'Indeed Applicant' in args[1]
    assert 'Production Worker' in args[1]


def test_write_skips_duplicate_on_qualified(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    bh = MagicMock()
    bh.get_entity.return_value = {
        'title': 'Production Worker',
        'clientCorporation': {'name': 'SC Johnson Wisconsin'},
    }
    bh.get_candidate_notes.return_value = [{
        'action': APPLICATION_NOTE_ACTION,
        'comments': build_application_note_text(
            source='Indeed Job Board',
            title='Production Worker',
            company='SC Johnson Wisconsin',
        ),
    }]
    assert write_application_received_note(
        bh, 3339038, 79398, source='Indeed Job Board'
    ) is None
    bh.create_candidate_note.assert_not_called()
