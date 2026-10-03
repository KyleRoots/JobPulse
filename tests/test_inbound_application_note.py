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
        job_id=79398,
        company_id=1001,
        recruiter_id=2002,
    )
    assert text.startswith('<b>Indeed Applicant</b> for ')
    assert 'Production Worker' in text
    assert 'entity=JobOrder&amp;id=79398' in text
    assert 'entity=ClientCorporation&amp;id=1001' in text
    assert 'entity=CorporateUser' not in text
    assert '<b>Laura Madsen</b>' in text
    assert 'SC Johnson Wisconsin' in text
    assert 'Laura Madsen' in text
    assert 'October 1, 2026 at 7:18 PM' in text
    assert text.endswith('Indeed Job Board')
    assert '<br>' in text
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


def test_combined_note_puts_summary_under_snapshot():
    from inbound_application_note import combine_application_note_text, resume_summary_body

    snapshot = build_application_note_text(
        source='Indeed Job Board',
        title='Experienced Machine Operators in West Point, MS!',
        company='Fabricators Supply, Llc',
        recruiter='Salem Barksdale',
        job_id=555,
        company_id=666,
        recruiter_id=777,
        applied_at=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
    )
    summary = resume_summary_body({
        'summary': 'Skilled Forklift Operator with 2 years of experience.',
        'skills': ['Material Handling'],
        'years_experience': 9,
    })
    text = combine_application_note_text(snapshot, summary)
    assert text.index('Indeed Applicant') < text.index('AI-Generated Resume Summary:')
    assert 'Skilled Forklift Operator' in text
    assert 'Key Skills: Material Handling' in text


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


def test_dedup_matches_ampersand_title_encoded_or_decoded():
    decoded = (
        '<b>Indeed Applicant</b> for <b><a href="'
        'https://www.bullhornstaffing.com/BullhornStaffing/OpenWindow.cfm'
        '?entity=JobOrder&id=80111">Welder/Tig& Mig</a></b> at <b>Woodard -C.M LLC</b>'
        '<br>Indeed Job Board'
    )
    encoded = (
        '<b>Indeed Applicant</b> for <b><a href="'
        'https://www.bullhornstaffing.com/BullhornStaffing/OpenWindow.cfm'
        '?entity=JobOrder&amp;id=80111">Welder/Tig&amp; Mig</a></b> at <b>Woodard -C.M LLC</b>'
        '<br>Indeed Job Board'
    )
    for comments in (decoded, encoded):
        existing = [{'action': APPLICATION_NOTE_ACTION, 'comments': comments}]
        assert snapshot_already_present(
            existing, title='Welder/Tig& Mig', company='Woodard -C.M LLC', job_id=80111
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
        'clientCorporation': {'id': 1001, 'name': 'SC Johnson Wisconsin'},
        'assignedUsers': {
            'data': [{'id': 2002, 'firstName': 'Laura', 'lastName': 'Madsen'}],
        },
    }
    bh.get_candidate_notes.return_value = []
    bh.create_candidate_note.return_value = 555
    note_id = write_application_received_note(
        bh, 3339038, 79398, source='Indeed Job Board',
        applied_at=datetime(2026, 10, 1, 23, 18, tzinfo=timezone.utc),
        summary_text='AI-Generated Resume Summary:\n\nCook with food-safety experience.',
    )
    assert note_id == 555
    args, kwargs = bh.create_candidate_note.call_args
    assert args[0] == 3339038
    assert args[2] == APPLICATION_NOTE_ACTION
    assert 'Indeed Applicant' in args[1]
    assert 'Production Worker' in args[1]
    assert 'entity=JobOrder&amp;id=79398' in args[1]
    assert args[1].index('Indeed Applicant') < args[1].index('AI-Generated Resume Summary:')


def test_write_updates_existing_note_with_links_and_summary(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    bh = MagicMock()
    bh.get_entity.return_value = {
        'id': 79398,
        'title': 'Production Worker',
        'clientCorporation': {'id': 1001, 'name': 'SC Johnson Wisconsin'},
        'assignedUsers': {
            'data': [{'id': 2002, 'firstName': 'Laura', 'lastName': 'Madsen'}],
        },
    }
    bh.get_candidate_notes.return_value = [
        {
            'id': 10,
            'action': APPLICATION_NOTE_ACTION,
            'comments': 'Indeed Applicant for <b>Production Worker</b> at <b>SC Johnson Wisconsin</b>',
        },
        {
            'id': 11,
            'action': 'AI Resume Summary',
            'comments': 'AI-Generated Resume Summary:\n\nWarehouse experience.',
        },
    ]
    bh.update_entity.return_value = True
    bh.delete_entity.return_value = True
    note_id = write_application_received_note(
        bh, 3339038, 79398, source='Indeed Job Board',
        applied_at=datetime(2026, 10, 1, 23, 18, tzinfo=timezone.utc),
    )
    assert note_id == 10
    bh.update_entity.assert_called_once()
    updated = bh.update_entity.call_args[0][2]['comments']
    assert 'entity=JobOrder&amp;id=79398' in updated
    assert 'AI-Generated Resume Summary:' in updated
    bh.delete_entity.assert_called_once()
    assert bh.delete_entity.call_args[0][1] == 11


def test_write_skips_when_links_and_summary_already_present(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    bh = MagicMock()
    bh.get_entity.return_value = {
        'id': 79398,
        'title': 'Production Worker',
        'clientCorporation': {'id': 1001, 'name': 'SC Johnson Wisconsin'},
    }
    comments = build_application_note_text(
        source='Indeed Job Board',
        title='Production Worker',
        company='SC Johnson Wisconsin',
        job_id=79398,
        company_id=1001,
    ) + '<br><br>AI-Generated Resume Summary:\n\nDone.'
    bh.get_candidate_notes.return_value = [{
        'id': 10,
        'action': APPLICATION_NOTE_ACTION,
        'comments': comments,
    }]
    assert write_application_received_note(
        bh, 3339038, 79398, source='Indeed Job Board'
    ) is None
    bh.create_candidate_note.assert_not_called()
    bh.update_entity.assert_not_called()


def test_write_strips_corporate_user_openwindow_link(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    bh = MagicMock()
    bh.get_entity.return_value = {
        'id': 79398,
        'title': 'Production Worker',
        'clientCorporation': {'id': 1001, 'name': 'SC Johnson Wisconsin'},
        'assignedUsers': {
            'data': [{'id': 2002, 'firstName': 'Laura', 'lastName': 'Madsen'}],
        },
    }
    old = build_application_note_text(
        source='Indeed Job Board',
        title='Production Worker',
        company='SC Johnson Wisconsin',
        recruiter='Laura Madsen',
        job_id=79398,
        company_id=1001,
    ).replace(
        '<b>Laura Madsen</b>',
        '<b><a href="https://www.bullhornstaffing.com/BullhornStaffing/OpenWindow.cfm?entity=CorporateUser&amp;id=2002">Laura Madsen</a></b>',
    ) + '<br><br>AI-Generated Resume Summary:\n\nDone.'
    bh.get_candidate_notes.return_value = [{
        'id': 10,
        'action': APPLICATION_NOTE_ACTION,
        'comments': old,
    }]
    bh.update_entity.return_value = True
    note_id = write_application_received_note(
        bh, 3339038, 79398, source='Indeed Job Board'
    )
    assert note_id == 10
    updated = bh.update_entity.call_args[0][2]['comments']
    assert 'entity=CorporateUser' not in updated
    assert '<b>Laura Madsen</b>' in updated
    assert 'entity=JobOrder&amp;id=79398' in updated


def test_write_collapses_duplicate_notes_for_same_job(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    bh = MagicMock()
    bh.get_entity.return_value = {
        'id': 79398,
        'title': 'Production Worker',
        'clientCorporation': {'id': 1001, 'name': 'SC Johnson Wisconsin'},
    }
    snapshot = build_application_note_text(
        source='Indeed Job Board',
        title='Production Worker',
        company='SC Johnson Wisconsin',
        job_id=79398,
        company_id=1001,
    )
    bh.get_candidate_notes.return_value = [
        {'id': 31, 'action': APPLICATION_NOTE_ACTION, 'comments': snapshot, 'dateAdded': 3},
        {
            'id': 20,
            'action': APPLICATION_NOTE_ACTION,
            'comments': snapshot + '<br><br>AI-Generated Resume Summary:\n\nWelder.',
            'dateAdded': 2,
        },
        {'id': 11, 'action': APPLICATION_NOTE_ACTION, 'comments': snapshot, 'dateAdded': 1},
    ]
    assert write_application_received_note(
        bh, 3339333, 79398, source='Indeed Job Board'
    ) is None
    bh.create_candidate_note.assert_not_called()
    deleted = [call.args[1] for call in bh.delete_entity.call_args_list]
    assert 11 in deleted
    assert 31 in deleted
    assert 20 not in deleted


def test_collapse_website_duplicates_does_not_rewrite(monkeypatch):
    from tasks.indeed_inbound_enrich import _collapse_website_duplicate_notes

    bh = MagicMock()
    snapshot = build_application_note_text(
        source='Corporate Website',
        title='Welder/Tig& Mig',
        company='Woodard -C.M LLC',
        job_id=80111,
        company_id=9,
    )
    bh.get_candidate_submissions.return_value = [
        {'jobOrder': {'id': 80111}, 'dateAdded': 1},
    ]
    bh.get_candidate_notes.return_value = [
        {'id': 1, 'action': APPLICATION_NOTE_ACTION, 'comments': snapshot, 'dateAdded': 1},
        {
            'id': 2,
            'action': APPLICATION_NOTE_ACTION,
            'comments': snapshot + '<br><br>AI-Generated Resume Summary:\n\nWelder.',
            'dateAdded': 2,
        },
        {'id': 3, 'action': APPLICATION_NOTE_ACTION, 'comments': snapshot, 'dateAdded': 3},
    ]
    bh.get_entity.return_value = {
        'id': 80111,
        'title': 'Welder/Tig& Mig',
        'clientCorporation': {'id': 9, 'name': 'Woodard -C.M LLC'},
    }
    assert _collapse_website_duplicate_notes(bh, {'id': 3339333, 'source': 'Corporate Website'})
    bh.create_candidate_note.assert_not_called()
    bh.update_entity.assert_not_called()
    deleted = [call.args[1] for call in bh.delete_entity.call_args_list]
    assert 1 in deleted
    assert 3 in deleted
    assert 2 not in deleted
