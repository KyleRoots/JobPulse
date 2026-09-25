"""Indeed Job Board follow-up: department blank-fill and summary note shape."""


def test_department_fills_blank_only():
    from tasks.indeed_inbound_enrich import department_update

    assert department_update('', 'Appleton') == 'Appleton'
    assert department_update(None, 'Appleton') == 'Appleton'
    assert department_update('Appleton', 'Appleton') is None
    assert department_update('Dalton', 'Appleton') is None
    assert department_update('', '') is None


def test_latest_job_id():
    from tasks.indeed_inbound_enrich import latest_job_id

    assert latest_job_id([
        {'jobOrder': {'id': 79341, 'title': 'Printshop'}},
    ]) == 79341
    assert latest_job_id([]) is None


def test_summary_note_matches_email_shape():
    from tasks.indeed_inbound_enrich import summary_note_text

    text = summary_note_text({
        'summary': 'Cook with food-safety experience.',
        'skills': ['Sanitation', 'Fryer'],
        'years_experience': 3,
    })
    assert text.startswith('AI-Generated Resume Summary:')
    assert 'Cook with food-safety experience.' in text
    assert 'Key Skills: Sanitation, Fryer' in text
    assert 'Experience: 3 years' in text
    assert summary_note_text({}) == ''
