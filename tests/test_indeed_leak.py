"""Qualified Indeed leak rules: detection, grace, removal, guardrails, digest."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from indeed_publish.leak import (
    MODE_ENFORCE,
    MODE_OFF,
    MODE_REPORT,
    LeakRun,
    cannot_publish_reason,
    digest_buckets,
    digest_is_due,
    jobs_from_applicant_submissions,
    notice_message,
    notice_recipients,
    render_digest,
    select_sweep_jobs,
    stripped_description_update,
)

EASTERN = ZoneInfo('America/New_York')
NOW = datetime(2026, 10, 29, 22, 0, tzinfo=timezone.utc)
OPS = 'kroots@myticas.com'


def _user(uid, first, last, email):
    return {'id': uid, 'firstName': first, 'lastName': last, 'email': email}


def _job(jid=34869, **overrides):
    job = {
        'id': jid,
        'title': 'CNC Operator',
        'status': 'Accepting Candidates',
        'isOpen': True,
        'correlatedCustomText1': 'Lapeer',
        'assignedUsers': {'data': [_user(258, 'Susan', 'Badour', 'susan@example.com')]},
        'owner': _user(31, 'Kim', 'Maze', 'kim@example.com'),
        'publicDescription': '<p>Run the mill.</p>',
    }
    job.update(overrides)
    return job


def _drive(
    jobs,
    *,
    mode=MODE_REPORT,
    now=NOW,
    state=None,
    tearsheet=(100,),
    status=None,
    on_tearsheet=None,
    grace_hours=24,
    exempt_job_ids=None,
    exempt_departments=None,
    read_error='',
    mismatch_applications=None,
):
    emails = []
    removed = []
    state = state if state is not None else {}

    def send_email(to, subject, body):
        emails.append({'to': to, 'subject': subject, 'body': body})
        return True

    def remove_indeed(job):
        removed.append(int(job['id']))

    def publication(job_id):
        if status is None:
            return {'indeed_published': True}
        return status(job_id)

    def membership(job_id):
        if on_tearsheet is None:
            return False
        return on_tearsheet(job_id)

    run = LeakRun(
        mode=mode,
        now=now,
        state=state,
        grace_hours=grace_hours,
        exempt_job_ids=set(exempt_job_ids or []),
        exempt_departments=set(exempt_departments or []),
        notify_email=OPS,
        send_email=send_email,
        remove_indeed=remove_indeed,
        publication_status=publication,
        job_on_tearsheet=membership,
    )
    run.mismatch_applications = mismatch_applications or {}
    if read_error or tearsheet is not None:
        stopped = run.guard_tearsheet(tearsheet, read_error=read_error)
        if stopped:
            return run, emails, removed
    run.consider(jobs)
    return run, emails, removed


def test_report_mode_records_leak_without_email_or_removal():
    run, emails, removed = _drive([_job()])
    assert run.result['leaks'] == [34869]
    assert emails == []
    assert removed == []
    record = run.state['jobs']['34869']
    assert record['first_seen'] == '2026-10-29T22:00:00Z'
    assert record['grace_deadline'] is None
    assert record['department'] == 'Lapeer'
    assert record['last_action'] == 'detected'
    assert record['open'] is True


def test_mode_off_does_not_check_status():
    calls = []

    def status(job_id):
        calls.append(job_id)
        return {'indeed_published': True}

    run, emails, removed = _drive([_job()], mode=MODE_OFF, status=status)
    assert calls == []
    assert emails == []
    assert removed == []
    assert run.state.get('jobs', {}) == {}


def test_enforce_removes_ineligible_job_from_indeed_only():
    job = _job(status='Hold - Covered', isOpen=True)
    run, emails, removed = _drive([job], mode=MODE_ENFORCE)
    assert removed == [34869]
    assert emails == []
    assert run.state['jobs']['34869']['last_action'] == 'removed'
    assert run.state['jobs']['34869']['last_result'] == 'ineligible'


def test_enforce_open_job_gets_one_grace_notice_and_is_not_removed():
    run, emails, removed = _drive([_job(), _job()], mode=MODE_ENFORCE)
    assert removed == []
    assert len(emails) == 1
    assert emails[0]['to'] == ['susan@example.com']
    assert '34869' in emails[0]['subject']
    assert 'Sponsored - Indeed' in emails[0]['body']
    assert '24 hours' in emails[0]['body']
    assert 'career portal' in emails[0]['body']
    deadline = run.state['jobs']['34869']['grace_deadline']
    assert deadline == '2026-10-30T22:00:00Z'
    assert run.result['noticed'] == [34869]


def test_second_pass_during_grace_does_not_email_again():
    run, emails, _removed = _drive([_job()], mode=MODE_ENFORCE)
    again, more, removed = _drive(
        [_job()],
        mode=MODE_ENFORCE,
        now=NOW + timedelta(hours=1),
        state=run.state,
    )
    assert more == []
    assert removed == []
    assert again.result['noticed'] == []
    assert len(emails) == 1


def test_grace_end_removes_when_still_live_and_off_tearsheet():
    run, _emails, _removed = _drive([_job()], mode=MODE_ENFORCE)
    later, _emails2, removed = _drive(
        [_job()],
        mode=MODE_ENFORCE,
        now=NOW + timedelta(hours=24),
        state=run.state,
    )
    assert removed == [34869]
    assert later.state['jobs']['34869']['last_result'] == 'grace ended'


def test_custom_grace_period():
    run, emails, _removed = _drive([_job()], mode=MODE_ENFORCE, grace_hours=1)
    assert '1 hour' in emails[0]['body']
    _mid, more, removed = _drive(
        [_job()],
        mode=MODE_ENFORCE,
        now=NOW + timedelta(minutes=59),
        state=run.state,
        grace_hours=1,
    )
    assert more == []
    assert removed == []
    _end, _more, removed = _drive(
        [_job()],
        mode=MODE_ENFORCE,
        now=NOW + timedelta(hours=1),
        state=run.state,
        grace_hours=1,
    )
    assert removed == [34869]


def test_switching_report_to_enforce_starts_a_fresh_grace():
    run, emails, removed = _drive([_job()], mode=MODE_REPORT)
    assert emails == [] and removed == []
    first_seen = run.state['jobs']['34869']['first_seen']
    later, emails, removed = _drive(
        [_job()],
        mode=MODE_ENFORCE,
        now=NOW + timedelta(days=3),
        state=run.state,
    )
    assert removed == []
    assert len(emails) == 1
    assert later.state['jobs']['34869']['first_seen'] == first_seen
    assert later.state['jobs']['34869']['grace_deadline'] == '2026-11-02T22:00:00Z'


def test_added_to_tearsheet_before_grace_ends_is_left_for_sync():
    run, _emails, _removed = _drive([_job()], mode=MODE_ENFORCE)
    later, emails, removed = _drive(
        [],
        mode=MODE_ENFORCE,
        now=NOW + timedelta(hours=2),
        state=run.state,
        tearsheet=(34869, 100),
    )
    assert removed == []
    assert emails == []
    assert later.state['jobs']['34869']['open'] is False
    assert later.state['jobs']['34869']['last_action'] == 'resolved'


def test_recheck_skips_removal_when_indeed_is_no_longer_live():
    calls = {'n': 0}

    def status(_job_id):
        calls['n'] += 1
        return {'indeed_published': calls['n'] == 1}

    _run, _emails, removed = _drive(
        [_job(status='Closed', isOpen=False)],
        mode=MODE_ENFORCE,
        status=status,
    )
    assert removed == []
    assert calls['n'] == 2


def test_recheck_skips_removal_when_job_joins_tearsheet():
    _run, _emails, removed = _drive(
        [_job(status='Filled', isOpen=False)],
        mode=MODE_ENFORCE,
        on_tearsheet=lambda _jid: True,
    )
    assert removed == []


def test_notice_goes_to_every_assigned_recruiter_except_unassigned_and_blank():
    job = _job(assignedUsers={'data': [
        _user(258, 'Susan', 'Badour', 'susan@example.com'),
        _user(251, 'Stacy', 'Peterson', 'stacy@example.com'),
        _user(1, 'Unassigned', 'User', 'dead@example.com'),
        _user(270, 'Rachelle', 'Fite', ''),
    ]})
    _run, emails, _removed = _drive([job], mode=MODE_ENFORCE)
    assert len(emails) == 1
    assert emails[0]['to'] == ['susan@example.com', 'stacy@example.com']


def test_notice_falls_back_to_owner_then_ops():
    owner_job = _job(assignedUsers={'data': [
        _user(1, 'Unassigned', 'User', 'dead@example.com'),
    ]})
    _run, emails, _removed = _drive([owner_job], mode=MODE_ENFORCE)
    assert [item['to'] for item in emails] == [['kim@example.com']]

    ops_job = _job(
        id=38327,
        assignedUsers={'data': [_user(1, 'Unassigned', 'User', '')]},
        owner=_user(1, 'Unassigned', 'User', 'nobody@example.com'),
    )
    _run, emails, _removed = _drive([ops_job], mode=MODE_ENFORCE)
    assert [item['to'] for item in emails] == [[OPS]]
    assert '38327' in emails[0]['body']


def test_failed_notice_does_not_start_grace():
    def send_email(_to, _subject, _body):
        return False

    run = LeakRun(
        mode=MODE_ENFORCE,
        now=NOW,
        state={},
        notify_email=OPS,
        send_email=send_email,
        remove_indeed=lambda _job: None,
        publication_status=lambda _jid: {'indeed_published': True},
        job_on_tearsheet=lambda _jid: False,
    )
    assert run.guard_tearsheet([100]) is False
    run.consider([_job()])
    record = run.state['jobs']['34869']
    assert record['grace_deadline'] is None
    assert record['last_action'] == 'failed'
    assert run.result['removed'] == []


def test_exemptions_by_job_and_department_are_not_removed():
    run, emails, removed = _drive(
        [_job(), _job(jid=50, correlatedCustomText1='Appleton')],
        mode=MODE_ENFORCE,
        exempt_job_ids={34869},
        exempt_departments={'appleton'},
    )
    assert removed == []
    assert emails == []
    assert set(run.result['exempt']) == {34869, 50}
    assert run.state['jobs']['34869']['last_action'] == 'exempt'


def test_tearsheet_read_failure_and_zero_jobs_take_no_action():
    run, emails, removed = _drive([], read_error='HTTP 500', tearsheet=None)
    assert run.result['aborted'] is True
    assert run.result['guardrail'] == 'tearsheet_read_failed'
    assert removed == []
    assert emails[0]['to'] == OPS

    run, emails, removed = _drive([_job()], tearsheet=[])
    assert run.result['guardrail'] == 'tearsheet_empty'
    assert removed == []
    assert 'zero jobs' in emails[0]['body']


def test_membership_drop_over_50_percent_stops_the_run():
    closed = _job(status='Closed', isOpen=False)
    state = {'last_successful_tearsheet_count': 100, 'jobs': {}}
    run, emails, removed = _drive(
        [closed],
        mode=MODE_ENFORCE,
        state=state,
        tearsheet=list(range(50)),
    )
    assert run.result['aborted'] is False
    assert removed == [34869]

    state = {'last_successful_tearsheet_count': 100, 'jobs': {}}
    run, emails, removed = _drive(
        [closed],
        mode=MODE_ENFORCE,
        state=state,
        tearsheet=list(range(49)),
    )
    assert run.result['guardrail'] == 'tearsheet_membership_drop'
    assert removed == []
    assert '100' in emails[0]['body']
    assert state['last_successful_tearsheet_count'] == 100


def test_status_failure_rate_above_20_percent_takes_no_action():
    def flaky(job_id):
        if job_id == 1:
            raise RuntimeError('timeout')
        return {'indeed_published': True}

    jobs = [_job(jid=i, status='Closed', isOpen=False) for i in range(1, 6)]
    run, emails, removed = _drive(jobs, mode=MODE_ENFORCE, status=flaky)
    assert run.result['aborted'] is False
    assert removed

    def worse(job_id):
        if job_id == 1:
            raise RuntimeError('timeout')
        return {'indeed_published': True}

    jobs = [_job(jid=i, status='Closed', isOpen=False) for i in range(1, 5)]
    run, emails, removed = _drive(jobs, mode=MODE_ENFORCE, status=worse)
    assert run.result['guardrail'] == 'status_failure_rate'
    assert removed == []
    assert emails[0]['to'] == OPS
    assert run.state.get('last_successful_at') is None


def test_guardrail_email_is_not_repeated_the_same_day():
    state = {}
    _run, emails, _removed = _drive([], tearsheet=[], state=state)
    assert len(emails) == 1
    _run, more, _removed = _drive(
        [],
        tearsheet=[],
        state=state,
        now=NOW + timedelta(hours=2),
    )
    assert more == []
    _run, later, _removed = _drive(
        [],
        tearsheet=[],
        state=state,
        now=NOW + timedelta(hours=25),
    )
    assert len(later) == 1


def test_sweep_candidate_set_is_open_jobs_plus_recent_and_not_on_tearsheet():
    open_jobs = [_job(jid=1), _job(jid=2)]
    recent = [_job(jid=2), _job(jid=3, isOpen=False, status='Closed')]
    chosen = select_sweep_jobs(open_jobs, recent, {2})
    assert [job['id'] for job in chosen] == [1, 3]


def test_applicant_fast_path_collects_each_submitted_job_once():
    ids = jobs_from_applicant_submissions({
        10: [{'jobOrder': {'id': 34869}}, {'jobOrder': {'id': 50}}],
        11: [{'jobOrder': {'id': 34869}}],
        12: [{}],
    })
    assert ids == [34869, 50]


def test_not_live_is_not_a_leak():
    run, emails, removed = _drive(
        [_job()],
        mode=MODE_ENFORCE,
        status=lambda _jid: {'indeed_published': False},
    )
    assert run.result['leaks'] == []
    assert emails == []
    assert removed == []


def test_removal_failure_is_persisted_and_not_counted_twice_in_one_pass():
    def remove(_job):
        raise RuntimeError('Indeed DELETE failed')

    run = LeakRun(
        mode=MODE_ENFORCE,
        now=NOW,
        state={},
        notify_email=OPS,
        send_email=lambda *_a: True,
        remove_indeed=remove,
        publication_status=lambda _jid: {'indeed_published': True},
        job_on_tearsheet=lambda _jid: False,
    )
    run.guard_tearsheet([100])
    run.consider([_job(status='Canceled', isOpen=False), _job(status='Canceled', isOpen=False)])
    assert run.result['removed'] == []
    assert len(run.result['failed']) == 1
    assert run.state['jobs']['34869']['last_action'] == 'failed'


def test_indeed_delete_includes_a_description_parameter():
    from indeed_publish.leak import indeed_delete_description

    assert indeed_delete_description(_job()) == '<p>Run the mill.</p>'
    assert indeed_delete_description(
        _job(publicDescription='', description='')
    ) == ' '


def test_campaign_tag_strips_from_the_published_description_field():
    update = stripped_description_update({
        'id': 9,
        'correlatedCustomText1': 'Flint',
        'publicDescription': '<p>Work</p>   #INDFli',
        'description': 'internal',
    })
    assert update == {'publicDescription': '<p>Work</p>'}
    assert stripped_description_update({
        'id': 9,
        'correlatedCustomText1': 'Appleton',
        'publicDescription': '<p>Work</p> #IND-WH',
    }) is None
    assert stripped_description_update({
        'id': 9,
        'correlatedCustomText1': 'Flint',
        'description': '<p>Work</p>',
    }) is None


def test_strip_on_tearsheet_leave_respects_mode_and_tenant(monkeypatch):
    from indeed_publish.leak_runtime import strip_campaign_tag_on_tearsheet_leave

    job = {
        'id': 9,
        'correlatedCustomText1': 'Flint',
        'publicDescription': '<p>Work</p>   #INDFli',
    }
    bh = type('BH', (), {'update_job_order': lambda self, jid, data: True})()

    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    monkeypatch.setenv('INDEED_LEAK_ENFORCEMENT_MODE', 'report')
    assert strip_campaign_tag_on_tearsheet_leave(bh, job, 2) is False

    monkeypatch.setenv('INDEED_LEAK_ENFORCEMENT_MODE', 'enforce')
    assert strip_campaign_tag_on_tearsheet_leave(bh, dict(job), 1640) is False
    written = {}

    class Writer:
        def update_job_order(self, jid, data):
            written['jid'] = jid
            written['data'] = data
            return True

    assert strip_campaign_tag_on_tearsheet_leave(Writer(), dict(job), 2) is True
    assert written['jid'] == 9
    assert written['data']['publicDescription'] == '<p>Work</p>'

    monkeypatch.setenv('SCOUT_TENANT', '')
    assert strip_campaign_tag_on_tearsheet_leave(Writer(), dict(job), 2) is False


def test_cannot_publish_reasons():
    assert cannot_publish_reason(_job(publicDescription='', description='')) == (
        'empty description'
    )
    assert cannot_publish_reason(_job(
        publicDescription='',
        description='',
        assignedUsers={'data': []},
        owner=_user(1, 'Unassigned', 'User', ''),
    )) == 'empty description, no response user'
    assert cannot_publish_reason(_job()) is None


def test_digest_waits_until_monday_november_2_and_skips_empty_recruiters():
    assert digest_is_due(datetime(2026, 10, 29, 16, 0, tzinfo=EASTERN), {}) is False
    assert digest_is_due(datetime(2026, 11, 2, 7, 59, tzinfo=EASTERN), {}) is False
    monday = datetime(2026, 11, 2, 8, 0, tzinfo=EASTERN)
    assert digest_is_due(monday, {}) is True
    state = {'last_digest_at': '2026-11-02T13:00:00Z', 'jobs': {}}
    assert digest_is_due(monday + timedelta(hours=2), state) is False

    live = [{
        'job_id': 53,
        'title': 'Welder',
        'department': 'Flint',
        'recipients': ['stacy@example.com'],
    }]
    leaks = [{
        'job_id': 34869,
        'title': 'CNC Operator',
        'department': 'Lapeer',
        'recipients': ['susan@example.com'],
        'detail': '',
    }]
    blocked = [{
        'job_id': 23267,
        'title': '2nd shift slicing',
        'department': 'Harrisonburg',
        'recipients': ['wendi@example.com'],
        'detail': 'empty description',
    }]
    full = render_digest(
        now=monday,
        mode=MODE_REPORT,
        live_tearsheet=live,
        leaks=leaks,
        removed=[],
        in_grace=[],
        failed=[],
        cannot_publish=blocked,
    )
    assert 'Live via Sponsored - Indeed: 1' in full
    assert '34869' in full
    assert '23267' in full
    assert 'empty description' in full
    assert 'Mode: report' in full
    assert 'On Sponsored - Indeed but not live on Indeed: 0' in full
    assert 'Live on Indeed but Bullhorn says unpublished: 0' in full

    susan = render_digest(
        now=monday,
        mode=MODE_REPORT,
        live_tearsheet=live,
        leaks=leaks,
        removed=[],
        in_grace=[],
        failed=[],
        cannot_publish=blocked,
        audience='susan@example.com',
    )
    assert '34869' in susan
    assert '23267' not in susan
    assert '53' not in susan
    assert 'Live on Indeed but Bullhorn says unpublished' not in susan

    assert render_digest(
        now=monday,
        mode=MODE_REPORT,
        live_tearsheet=live,
        leaks=leaks,
        removed=[],
        in_grace=[],
        failed=[],
        cannot_publish=blocked,
        audience='nobody@example.com',
    ) is None


def test_digest_buckets_keep_grace_removals_and_failures():
    state = {
        'last_digest_at': None,
        'jobs': {
            '34869': {
                'job_id': 34869,
                'title': 'CNC',
                'department': 'Lapeer',
                'open': True,
                'last_action': 'notice',
                'grace_deadline': '2026-10-30T22:00:00Z',
                'recipients': ['susan@example.com'],
                'events': [
                    {'at': '2026-10-29T22:00:00Z', 'action': 'notice', 'result': 'grace started'},
                ],
            },
            '9': {
                'job_id': 9,
                'title': 'Closed role',
                'department': 'Flint',
                'open': False,
                'last_action': 'removed',
                'recipients': ['stacy@example.com'],
                'events': [
                    {'at': '2026-10-29T22:00:00Z', 'action': 'removed', 'result': 'ineligible'},
                    {'at': '2026-10-29T22:00:00Z', 'action': 'failed', 'result': 'remove: earlier'},
                ],
            },
        },
        'last_status_failures': [{'job_id': 4, 'result': 'status: timeout'}],
    }
    buckets = digest_buckets(state, NOW)
    assert [row['job_id'] for row in buckets['leaks']] == [34869]
    assert [row['job_id'] for row in buckets['in_grace']] == [34869]
    assert any(row['job_id'] == 9 for row in buckets['removed'])
    assert any(row['job_id'] == 9 for row in buckets['failed'])
    assert any(row['job_id'] == 4 for row in buckets['failed'])


def test_a_run_with_nothing_to_check_keeps_earlier_status_failures():
    state = {
        'jobs': {},
        'last_status_failures': [{'job_id': 4, 'result': 'status: timeout'}],
    }
    run, _emails, _removed = _drive([], state=state, tearsheet=[100])
    assert run.state['last_status_failures'] == [{'job_id': 4, 'result': 'status: timeout'}]


def test_notice_message_names_the_job():
    text = notice_message(_job(), 24)
    assert '34869' in text
    assert 'CNC Operator' in text
    assert 'Lapeer' in text
    assert 'removed from Indeed in 24 hours' in text


def test_auto_unpublish_hook_asks_to_strip_the_tag(monkeypatch):
    from indeed_publish.sync import unpublish_job_after_tearsheet_remove

    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'false')
    monkeypatch.setenv('INDEED_LEAK_ENFORCEMENT_MODE', 'enforce')
    called = {}

    def strip(bh, job, tearsheet_id):
        called['tearsheet_id'] = tearsheet_id
        called['job_id'] = job['id']
        return True

    class BH:
        def authenticate(self):
            return True

    monkeypatch.setattr(
        'utils.bullhorn_helpers.get_bullhorn_service',
        lambda: BH(),
    )
    monkeypatch.setattr(
        'indeed_publish.sync.IndeedTearsheetPublishService._fetch_job_detail',
        lambda self, bh, job_id: {'id': job_id, 'publicDescription': '<p>x</p> #INDLap'},
    )
    monkeypatch.setattr(
        'indeed_publish.leak_runtime.strip_campaign_tag_on_tearsheet_leave',
        strip,
    )
    assert unpublish_job_after_tearsheet_remove(34869, 2) is False
    assert called == {'tearsheet_id': 2, 'job_id': 34869}


def test_leak_mode_defaults_to_report(monkeypatch):
    from indeed_publish.leak import grace_hours, leak_mode

    monkeypatch.delenv('INDEED_LEAK_ENFORCEMENT_MODE', raising=False)
    assert leak_mode() == MODE_REPORT
    monkeypatch.setenv('INDEED_LEAK_ENFORCEMENT_MODE', 'enforce')
    assert leak_mode() == MODE_ENFORCE
    monkeypatch.setenv('INDEED_LEAK_ENFORCEMENT_MODE', 'nope')
    assert leak_mode() == MODE_REPORT
    monkeypatch.setenv('INDEED_LEAK_GRACE_HOURS', '6')
    assert grace_hours() == 6


def _rest_token_error():
    import requests
    return requests.ConnectionError(
        'Max retries exceeded with url: '
        'https://rest.example/entity/Tearsheet/2/jobOrders'
        '?BhRestToken=SECRET&access_token=AAA&refresh_token=BBB'
        '&code=CCC&password=DDD'
    )


def test_connection_error_does_not_carry_the_rest_token(app, caplog):
    """A URL in a connection error must not reach email, state, results, or logs."""
    import json
    import logging
    from unittest.mock import patch

    from indeed_publish.leak import sanitize_leak_text

    secret_bits = ('SECRET', 'AAA', 'BBB', 'CCC', 'DDD')
    saved = {}
    emails = []

    def send_email(_to, _subject, body):
        emails.append(body)
        return True

    def save_state(state):
        saved['json'] = json.dumps(state)

    with caplog.at_level(logging.DEBUG):
        with patch('indeed_publish.leak_runtime.is_qualified_tenant', return_value=True), \
             patch('indeed_publish.leak_runtime.leak_mode', return_value='report'), \
             patch('indeed_publish.leak_runtime._bh', return_value=object()), \
             patch('indeed_publish.leak_runtime._ui_client', return_value=object()), \
             patch('indeed_publish.leak_runtime._tearsheet_ids', side_effect=_rest_token_error()), \
             patch('indeed_publish.leak_runtime._save_state', save_state), \
             patch('indeed_publish.leak_runtime._send_email', send_email):
            from indeed_publish.leak_runtime import run_sweep
            result = run_sweep()

        with patch('indeed_publish.leak_runtime.run_sweep', side_effect=_rest_token_error()):
            from tasks.indeed_leak import run_indeed_leak_sweep
            run_indeed_leak_sweep()

    closed = _job(status='Closed', isOpen=False)

    def on_sheet(_job_id):
        raise _rest_token_error()

    remove_run = LeakRun(
        mode=MODE_ENFORCE,
        now=NOW,
        state={},
        notify_email=OPS,
        publication_status=lambda _job_id: {'indeed_published': True},
        job_on_tearsheet=on_sheet,
    )
    assert remove_run.guard_tearsheet([100]) is False
    remove_run.consider([closed])

    blob = '\n'.join([
        json.dumps(result),
        saved.get('json', ''),
        '\n'.join(emails),
        caplog.text,
        json.dumps(remove_run.state),
        json.dumps(remove_run.result),
    ])
    for bit in secret_bits:
        assert bit not in blob
    assert 'BhRestToken=' not in blob
    assert '?' not in result['guardrail_detail']
    assert result['guardrail_detail'] == 'ConnectionError'
    assert 'ConnectionError' in emails[0]
    event = remove_run.state['jobs']['34869']['events'][-1]
    assert event['action'] == 'failed'
    assert 'ConnectionError' in event['result']
    assert 'SECRET' not in event['detail']
    assert remove_run.result['failed'][0]['result'] == 'recheck: ConnectionError'

    outside_url = sanitize_leak_text(
        'BhRestToken=SECRET access_token=AAA refresh_token=BBB code=CCC password=DDD'
    )
    for bit in secret_bits:
        assert bit not in outside_url


def test_tearsheet_and_job_reads_send_rest_token_as_header(monkeypatch):
    import indeed_publish.leak_runtime as runtime

    calls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {'data': [], 'total': 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, dict(params or {}), dict(headers or {})))
        return _Resp()

    monkeypatch.setattr(runtime.requests, 'get', fake_get)
    bh = type('BH', (), {
        'base_url': 'https://rest.example/',
        'rest_token': 'SECRET',
    })()
    assert runtime._tearsheet_ids(bh) == set()
    assert runtime._query_jobs(bh, 'isOpen=true AND isDeleted=false') == []
    assert runtime._fetch_job(bh, 68837) is None
    assert len(calls) == 3
    for url, params, headers in calls:
        assert headers.get('BhRestToken') == 'SECRET'
        assert 'BhRestToken' not in params
        assert 'SECRET' not in url
        assert 'SECRET' not in str(params)


def test_deep_sweep_keeps_closed_jobs_the_hourly_window_misses():
    from indeed_publish.leak import deep_sweep_where, select_deep_sweep_jobs

    now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    where = deep_sweep_where(now)
    cutoff = int((now - timedelta(days=180)).timestamp() * 1000)
    assert 'isDeleted=false AND isOpen=false' in where
    assert f'dateLastModified>={cutoff}' in where
    assert f'dateClosed>={cutoff}' in where

    closed = _job(jid=79378, status='Filled', isOpen=False, title='Office Assistant')
    other = _job(jid=68837, status='Filled', isOpen=False, title='General Labor')
    on_sheet = _job(jid=100, status='Filled', isOpen=False)
    still_open = _job(jid=3, isOpen=True)
    deleted = _job(jid=4, status='Filled', isOpen=False, isDeleted=True)
    chosen = select_deep_sweep_jobs(
        [closed, other, on_sheet, still_open, deleted],
        {100},
    )
    assert [job['id'] for job in chosen] == [68837, 79378]
    hourly = select_sweep_jobs([_job()], [], set())
    assert [job['id'] for job in hourly] == [34869]
    assert 79378 not in {job['id'] for job in hourly}


def test_fast_path_and_deep_sweep_jobs_are_in_the_monday_digest():
    closed = _job(jid=68837, status='Filled', isOpen=False, title='General Labor')
    fast = _job(jid=79378, status='Filled', isOpen=False, title='Office Assistant')
    run = LeakRun(
        mode=MODE_REPORT,
        now=NOW,
        state={},
        notify_email=OPS,
        publication_status=lambda _job_id: {'indeed_published': True},
    )
    assert run.guard_tearsheet([100]) is False
    run.consider([closed, fast])
    buckets = digest_buckets(run.state, datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc))
    assert {row['job_id'] for row in buckets['leaks']} == {68837, 79378}
    text = render_digest(
        now=datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc),
        mode=MODE_REPORT,
        live_tearsheet=[],
        leaks=buckets['leaks'],
        removed=buckets['removed'],
        in_grace=buckets['in_grace'],
        failed=buckets['failed'],
        cannot_publish=[],
    )
    assert '68837' in text
    assert '79378' in text


def test_deep_sweep_removes_a_closed_leak_immediately_in_enforce():
    run, emails, removed = _drive(
        [_job(jid=79378, status='Filled', isOpen=False)],
        mode=MODE_ENFORCE,
    )
    assert removed == [79378]
    assert emails == []
    assert run.state['jobs']['79378']['last_action'] == 'removed'


def test_deep_sweep_uses_the_same_tearsheet_guardrail(monkeypatch):
    import json
    from unittest.mock import patch

    saved = {}
    emails = []

    def send_email(_to, _subject, body):
        emails.append(body)
        return True

    def save_state(state):
        saved['json'] = json.dumps(state)

    def fail_query(*_args, **_kwargs):
        raise AssertionError('job query must not run after a tearsheet failure')

    with patch('indeed_publish.leak_runtime.is_qualified_tenant', return_value=True), \
         patch('indeed_publish.leak_runtime.leak_mode', return_value='report'), \
         patch('indeed_publish.leak_runtime._bh', return_value=object()), \
         patch('indeed_publish.leak_runtime._ui_client', return_value=object()), \
         patch('indeed_publish.leak_runtime._tearsheet_ids', side_effect=_rest_token_error()), \
         patch('indeed_publish.leak_runtime._query_jobs', fail_query), \
         patch('indeed_publish.leak_runtime._save_state', save_state), \
         patch('indeed_publish.leak_runtime._send_email', send_email):
        from indeed_publish.leak_runtime import run_deep_sweep
        result = run_deep_sweep()

    blob = json.dumps(result) + saved.get('json', '') + '\n'.join(emails)
    assert result['guardrail'] == 'tearsheet_read_failed'
    assert result['guardrail_detail'] == 'ConnectionError'
    assert 'SECRET' not in blob
    assert result.get('removed', []) == []


def _ms(moment):
    return int(moment.timestamp() * 1000)


def test_mismatch_records_an_application_more_than_48_hours_after_unpublish():
    stopped = NOW - timedelta(days=3)
    status = {
        'indeed_published': False,
        'indeed_date_last_published': _ms(stopped),
    }
    run, emails, removed = _drive(
        [_job(jid=49606, title='Entry Level Manufacturing Associate')],
        mode=MODE_REPORT,
        status=lambda _jid: status,
        mismatch_applications={49606: [NOW]},
    )
    assert removed == []
    assert emails == []
    assert run.result['mismatches'] == [49606]
    assert run.result['leaks'] == []
    record = run.state['jobs']['49606']
    assert record['open'] is True
    assert record['last_action'] == 'mismatch'
    assert record['never_published'] is False
    assert record['application_dates'] == ['2026-10-29T22:00:00Z']
    buckets = digest_buckets(run.state, datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc))
    text = render_digest(
        now=datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc),
        mode=MODE_REPORT,
        live_tearsheet=[],
        leaks=buckets['leaks'],
        removed=[],
        in_grace=[],
        failed=[],
        cannot_publish=[],
        mismatches=buckets['mismatches'],
    )
    assert 'Live on Indeed but Bullhorn says unpublished: 1' in text
    assert '49606' in text
    assert 'unpublished Oct 26, 2026 6:00 PM ET' in text
    assert 'applications Oct 29, 2026 6:00 PM ET' in text
    assert 'Leaks found: 0' in text


def test_application_within_48_hours_of_unpublish_is_not_a_mismatch():
    stopped = NOW - timedelta(hours=48)
    status = {
        'indeed_published': False,
        'indeed_date_last_published': _ms(stopped),
    }
    run, emails, removed = _drive(
        [_job(jid=49606)],
        mode=MODE_ENFORCE,
        status=lambda _jid: status,
        mismatch_applications={49606: [NOW]},
    )
    assert run.result['mismatches'] == []
    assert emails == []
    assert removed == []
    assert '49606' not in run.state.get('jobs', {})


def test_never_published_job_with_an_indeed_application_is_a_mismatch():
    status = {'indeed_published': False, 'indeed_date_last_published': None}
    run, _emails, removed = _drive(
        [_job(jid=79511, isOpen=False, status='Accepting Candidates')],
        mode=MODE_REPORT,
        status=lambda _jid: status,
        mismatch_applications={79511: [NOW - timedelta(days=1)]},
    )
    assert removed == []
    assert run.result['mismatches'] == [79511]
    assert run.state['jobs']['79511']['never_published'] is True
    buckets = digest_buckets(run.state, NOW)
    assert 'unpublished never' in buckets['mismatches'][0]['detail']


def test_enforce_resends_the_indeed_delete_at_most_once_per_day():
    stopped = NOW - timedelta(days=4)
    status = {
        'indeed_published': False,
        'indeed_date_last_published': _ms(stopped),
    }
    apps = {49606: [NOW]}
    run, emails, removed = _drive(
        [_job(jid=49606)],
        mode=MODE_ENFORCE,
        status=lambda _jid: status,
        mismatch_applications=apps,
    )
    assert removed == [49606]
    assert emails == []
    assert run.result['mismatch_deletes'] == [49606]
    assert run.state['jobs']['49606']['open'] is True
    assert run.state['jobs']['49606']['last_action'] == 'mismatch'

    again, _emails, removed_again = _drive(
        [_job(jid=49606)],
        mode=MODE_ENFORCE,
        now=NOW + timedelta(hours=1),
        state=run.state,
        status=lambda _jid: status,
        mismatch_applications={49606: [NOW + timedelta(hours=1)]},
    )
    assert removed_again == []
    assert again.result['mismatch_deletes'] == []
    assert again.state['jobs']['49606']['open'] is True

    later, _emails, removed_later = _drive(
        [_job(jid=49606)],
        mode=MODE_ENFORCE,
        now=NOW + timedelta(hours=25),
        state=again.state,
        status=lambda _jid: status,
        mismatch_applications={49606: [NOW + timedelta(hours=25)]},
    )
    assert removed_later == [49606]
    assert later.result['mismatch_deletes'] == [49606]


def test_hourly_sweep_does_not_clear_a_mismatch_record():
    state = {
        'jobs': {
            '49606': {
                'job_id': 49606,
                'open': True,
                'last_action': 'mismatch',
                'title': 'Entry Level',
                'department': 'Wake Forest',
                'unpublished_at': '2026-10-26T14:41:00Z',
                'never_published': False,
                'application_dates': ['2026-10-29T13:38:48Z'],
                'events': [],
            },
        },
    }
    run, _emails, removed = _drive(
        [_job(jid=49606)],
        mode=MODE_REPORT,
        state=state,
        status=lambda _jid: {'indeed_published': False, 'indeed_date_last_published': _ms(NOW)},
    )
    assert removed == []
    assert run.result['mismatches'] == []
    assert run.state['jobs']['49606']['open'] is True
    assert run.state['jobs']['49606']['last_action'] == 'mismatch'
    assert run.state['jobs']['49606']['application_dates'] == ['2026-10-29T13:38:48Z']


def test_tearsheet_member_is_not_a_mismatch_leak():
    stopped = NOW - timedelta(days=5)
    run, _emails, removed = _drive(
        [_job(jid=49606)],
        mode=MODE_ENFORCE,
        tearsheet=(49606,),
        status=lambda _jid: {
            'indeed_published': False,
            'indeed_date_last_published': _ms(stopped),
        },
        mismatch_applications={49606: [NOW]},
    )
    assert removed == []
    assert run.result['mismatches'] == []


def test_indeed_board_applications_ignore_other_sources():
    from indeed_publish.leak import indeed_board_applications

    found = indeed_board_applications([
        {
            'source': 'Indeed Job Board',
            'dateAdded': _ms(NOW),
            'jobOrder': {'id': 49606},
        },
        {
            'source': 'Indeed',
            'dateAdded': _ms(NOW),
            'jobOrder': {'id': 79511},
        },
        {
            'source': 'ZipRecruiter Job Board',
            'dateAdded': _ms(NOW),
            'jobOrder': {'id': 1},
        },
    ])
    assert list(found) == [49606]
    from indeed_publish.leak import jobs_with_recent_indeed_applications
    recent = jobs_with_recent_indeed_applications(
        {49606: [NOW, NOW - timedelta(days=3)], 1: [NOW - timedelta(days=3)]},
        NOW,
        lookback_minutes=20,
    )
    assert list(recent) == [49606]
    assert len(recent[49606]) == 2


def test_not_live_tearsheet_rows_include_the_reason():
    from indeed_publish.leak import cannot_publish_reason

    assert cannot_publish_reason(_job(publicDescription='', description=''))
    blocked = [{
        'job_id': 23267,
        'title': '2nd shift slicing',
        'department': 'Harrisonburg',
        'recipients': ['wendi@example.com'],
        'detail': 'empty description, no response user',
    }]
    text = render_digest(
        now=datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc),
        mode=MODE_REPORT,
        live_tearsheet=[],
        leaks=[],
        removed=[],
        in_grace=[],
        failed=[],
        cannot_publish=blocked,
        not_live=blocked,
    )
    assert 'On Sponsored - Indeed but not live on Indeed: 1' in text
    assert '23267 2nd shift slicing (Harrisonburg) — empty description, no response user' in text
    recruiter = render_digest(
        now=datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc),
        mode=MODE_REPORT,
        live_tearsheet=[],
        leaks=[],
        removed=[],
        in_grace=[],
        failed=[],
        cannot_publish=[],
        not_live=blocked,
        audience='wendi@example.com',
    )
    assert '23267' in recruiter
    assert 'Live on Indeed but Bullhorn says unpublished' not in recruiter


def test_bcc_defaults_to_ops_and_an_empty_value_disables_it(monkeypatch):
    from indeed_publish.leak import leak_bcc_email, leak_bcc_recipients

    monkeypatch.delenv('INDEED_LEAK_BCC_EMAIL', raising=False)
    assert leak_bcc_email() == OPS
    assert leak_bcc_recipients(['susan@example.com', 'stacy@example.com']) == [OPS]
    assert leak_bcc_recipients([OPS]) == []
    assert leak_bcc_recipients(['Kroots@Myticas.com']) == []
    monkeypatch.setenv('INDEED_LEAK_BCC_EMAIL', '')
    assert leak_bcc_email() == ''
    assert leak_bcc_recipients(['susan@example.com']) == []
    monkeypatch.setenv('INDEED_LEAK_BCC_EMAIL', 'ops@example.com')
    assert leak_bcc_recipients(['susan@example.com']) == ['ops@example.com']


def test_leak_email_sends_one_message_and_records_the_bcc(monkeypatch):
    from indeed_publish import leak_runtime

    calls = []

    class _Service:
        def send_html_email(self, **kwargs):
            calls.append(kwargs)
            return {'success': True}

        def send_notification_email(self, **kwargs):
            raise AssertionError('leak mail must use the BCC-capable path')

    monkeypatch.delenv('INDEED_LEAK_BCC_EMAIL', raising=False)
    monkeypatch.setattr(
        'utils.bullhorn_helpers.get_email_service',
        lambda: _Service(),
    )
    assert leak_runtime._send_email(
        ['susan@example.com', 'stacy@example.com'],
        'Indeed posting outside Sponsored - Indeed: job 34869',
        'body',
    )
    assert leak_runtime._send_email(OPS, 'Indeed leak digest', 'ops body')
    assert len(calls) == 2
    assert calls[0]['to_email'] == ['susan@example.com', 'stacy@example.com']
    assert calls[0]['bcc_emails'] == [OPS]
    assert calls[0]['changes_summary'] == f'bcc:{OPS}'
    assert calls[0]['notification_type'] == 'indeed_leak'
    assert calls[1]['bcc_emails'] is None
    assert calls[1]['changes_summary'] is None


def test_html_email_writes_the_bcc_onto_the_delivery_log():
    from unittest.mock import Mock, patch

    from email_service import EmailService

    recorded = {}

    with patch('email_service.SendGridAPIClient') as mock_client:
        mock_response = Mock()
        mock_response.status_code = 202
        mock_response.headers = {}
        mock_client.return_value.client.mail.send.post.return_value = mock_response
        with patch.dict('os.environ', {'SENDGRID_API_KEY': 'test_key'}):
            service = EmailService()
        service._log_email_delivery = lambda **kwargs: recorded.update(kwargs)
        result = service.send_html_email(
            to_email=['susan@example.com', 'stacy@example.com'],
            subject='Indeed posting',
            html_content='<pre>body</pre>',
            notification_type='indeed_leak',
            bcc_emails=[OPS],
            changes_summary=f'bcc:{OPS}',
        )
    assert result['success'] is True
    assert recorded['changes_summary'] == f'bcc:{OPS}'
    assert recorded['recipient_email'] == 'susan@example.com, stacy@example.com'
    assert recorded['notification_type'] == 'indeed_leak'
    sent = mock_client.return_value.client.mail.send.post.call_args.kwargs['request_body']
    assert OPS in str(sent.get('personalizations'))


def test_fast_path_skips_when_a_sweep_holds_the_lock(monkeypatch, tmp_path):
    import indeed_publish.leak_runtime as runtime

    monkeypatch.setenv('INDEED_LEAK_LOCK_FILE', str(tmp_path / 'leak.lock'))
    monkeypatch.setattr(runtime, '_postgres_engine', lambda: None)
    monkeypatch.setattr(runtime, 'is_qualified_tenant', lambda: True)
    monkeypatch.setattr(runtime, 'leak_mode', lambda: 'report')
    saved = []
    monkeypatch.setattr(runtime, '_save_state', lambda state: saved.append('save'))
    monkeypatch.setattr(runtime, '_load_state', lambda: (_ for _ in ()).throw(AssertionError('load')))
    monkeypatch.setattr(runtime, '_bh', lambda: (_ for _ in ()).throw(AssertionError('bh')))
    held = runtime._acquire_file_lock(blocking=False, timeout=0)
    assert held is not None
    try:
        skipped = runtime.check_jobs([49606])
        recent = runtime.check_recent_indeed_applicants()
    finally:
        runtime._release_leak_lock(held)
    assert skipped == {'skipped': 'lock held'}
    assert recent == {'skipped': 'lock held'}
    assert saved == []


def test_sweep_gives_up_when_the_lock_wait_expires(monkeypatch, tmp_path):
    import indeed_publish.leak_runtime as runtime

    monkeypatch.setenv('INDEED_LEAK_LOCK_FILE', str(tmp_path / 'leak.lock'))
    monkeypatch.setattr(runtime, '_postgres_engine', lambda: None)
    monkeypatch.setattr(runtime, 'is_qualified_tenant', lambda: True)
    monkeypatch.setattr(runtime, 'leak_mode', lambda: 'report')
    monkeypatch.setattr(runtime, 'SWEEP_LOCK_WAIT_SECONDS', 0.15)
    saved = []
    monkeypatch.setattr(runtime, '_save_state', lambda state: saved.append('save'))
    monkeypatch.setattr(runtime, '_bh', lambda: (_ for _ in ()).throw(AssertionError('bh')))
    held = runtime._acquire_file_lock(blocking=False, timeout=0)
    assert held is not None
    try:
        sweep = runtime.run_sweep()
        deep = runtime.run_deep_sweep(
            clock=lambda: datetime(2026, 11, 2, 10, 0, tzinfo=EASTERN),
        )
    finally:
        runtime._release_leak_lock(held)
    assert sweep == {'skipped': 'lock held'}
    assert deep['skipped'] == 'lock held'
    assert deep['attempts'] == 1
    assert deep['emailed'] is False
    assert saved == []


def test_sweep_waits_for_the_lock_then_runs(monkeypatch, tmp_path):
    import threading
    import time
    import indeed_publish.leak_runtime as runtime

    monkeypatch.setenv('INDEED_LEAK_LOCK_FILE', str(tmp_path / 'leak.lock'))
    monkeypatch.setattr(runtime, '_postgres_engine', lambda: None)
    monkeypatch.setattr(runtime, 'is_qualified_tenant', lambda: True)
    monkeypatch.setattr(runtime, 'leak_mode', lambda: 'report')
    monkeypatch.setattr(runtime, 'SWEEP_LOCK_WAIT_SECONDS', 2)
    monkeypatch.setattr(runtime, '_run_sweep_locked', lambda: {'ran': True})
    held = runtime._acquire_file_lock(blocking=False, timeout=0)
    assert held is not None

    def _release():
        time.sleep(0.2)
        runtime._release_leak_lock(held)

    threading.Thread(target=_release, daemon=True).start()
    assert runtime.run_sweep() == {'ran': True}


def test_lock_is_held_across_processes(monkeypatch, tmp_path):
    import subprocess
    import sys
    import time
    import indeed_publish.leak_runtime as runtime

    path = str(tmp_path / 'leak.lock')
    monkeypatch.setenv('INDEED_LEAK_LOCK_FILE', path)
    monkeypatch.setattr(runtime, '_postgres_engine', lambda: None)
    proc = subprocess.Popen(
        [sys.executable, '-c', (
            'import os, fcntl, time\n'
            f'fd = os.open({path!r}, os.O_CREAT | os.O_RDWR, 0o644)\n'
            'fcntl.flock(fd, fcntl.LOCK_EX)\n'
            'time.sleep(30)\n'
        )],
    )
    try:
        blocked = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            held = runtime._acquire_file_lock(blocking=False, timeout=0)
            if held is None:
                blocked = True
                break
            runtime._release_leak_lock(held)
            time.sleep(0.05)
        waited = runtime._acquire_file_lock(blocking=True, timeout=0.1)
        assert blocked
        assert waited is None
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_postgres_lock_uses_an_advisory_lock(monkeypatch):
    import indeed_publish.leak_runtime as runtime

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar(self):
            return self.value

    class _Conn:
        def __init__(self):
            self.calls = []
            self.closed = False

        def execute(self, stmt, params=None):
            self.calls.append((str(stmt), params))
            return _Result(True)

        def commit(self):
            self.calls.append(('commit', None))

        def close(self):
            self.closed = True

    class _Engine:
        def __init__(self):
            self.conn = _Conn()

        def connect(self):
            return self.conn

    engine = _Engine()
    monkeypatch.setattr(runtime, '_postgres_engine', lambda: engine)
    held = runtime._acquire_leak_lock(blocking=False, timeout=0)
    assert held is not None
    assert held.kind == 'postgres'
    assert engine.conn.closed is False
    runtime._release_leak_lock(held)
    sql = ' '.join(call[0] for call in engine.conn.calls)
    assert 'pg_try_advisory_lock' in sql
    assert 'pg_advisory_unlock' in sql
    assert engine.conn.closed is True


def test_fast_path_reenters_a_lock_it_already_holds(monkeypatch, tmp_path):
    import indeed_publish.leak_runtime as runtime

    monkeypatch.setenv('INDEED_LEAK_LOCK_FILE', str(tmp_path / 'leak.lock'))
    monkeypatch.setattr(runtime, '_postgres_engine', lambda: None)
    monkeypatch.setattr(runtime, 'is_qualified_tenant', lambda: True)
    monkeypatch.setattr(runtime, 'leak_mode', lambda: 'report')
    seen = []
    monkeypatch.setattr(
        runtime,
        '_check_jobs_locked',
        lambda ids, apps: seen.append(list(ids)) or {'checked': ids},
    )
    with runtime.leak_state_lock(blocking=False) as acquired:
        assert acquired is True
        result = runtime.check_jobs([49606], {49606: [NOW]})
    assert result['checked'] == [49606]
    assert seen == [[49606]]


def test_submission_search_sends_the_rest_token_as_a_header(monkeypatch):
    import indeed_publish.leak_runtime as runtime

    calls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {'data': []}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, dict(params or {}), dict(headers or {})))
        return _Resp()

    monkeypatch.setattr(runtime.requests, 'get', fake_get)
    bh = type('BH', (), {'base_url': 'https://rest.example/', 'rest_token': 'SECRET'})()
    assert runtime._indeed_board_submissions(bh, 3342757) == []
    url, params, headers = calls[0]
    assert url.endswith('search/JobSubmission')
    assert 'Indeed Job Board' in params['query']
    assert headers.get('BhRestToken') == 'SECRET'
    assert 'BhRestToken' not in params
    assert 'SECRET' not in url


def test_fast_path_lookback_covers_a_deep_sweep_and_a_skipped_cycle():
    import indeed_publish.leak_runtime as runtime

    assert runtime.DEEP_SWEEP_HOLD_MINUTES * 60 >= 322
    assert runtime.FAST_PATH_LOOKBACK_MINUTES >= (
        runtime.DEEP_SWEEP_HOLD_MINUTES + runtime.FAST_PATH_INTERVAL_MINUTES
    )
    assert runtime.FAST_PATH_LOOKBACK_MINUTES == 20


def test_deep_sweep_retries_until_6am_then_emails_once(monkeypatch, tmp_path):
    import indeed_publish.leak_runtime as runtime

    monkeypatch.setenv('INDEED_LEAK_LOCK_FILE', str(tmp_path / 'leak.lock'))
    monkeypatch.setattr(runtime, '_postgres_engine', lambda: None)
    monkeypatch.setattr(runtime, 'is_qualified_tenant', lambda: True)
    monkeypatch.setattr(runtime, 'leak_mode', lambda: 'report')
    monkeypatch.setattr(runtime, 'SWEEP_LOCK_WAIT_SECONDS', 0)
    monkeypatch.setattr(
        runtime,
        '_run_deep_sweep_locked',
        lambda: (_ for _ in ()).throw(AssertionError('sweep ran')),
    )
    emails = []
    monkeypatch.setattr(runtime, '_notify_deep_sweep_missed', lambda moment: emails.append(moment) or True)
    times = [datetime(2026, 11, 2, 3, 0, tzinfo=EASTERN)]

    def sleep(seconds):
        assert seconds == runtime.DEEP_SWEEP_RETRY_SECONDS
        times[0] = times[0] + timedelta(seconds=seconds)

    held = runtime._acquire_file_lock(blocking=False, timeout=0)
    assert held is not None
    try:
        result = runtime.run_deep_sweep(clock=lambda: times[0], sleep=sleep)
    finally:
        runtime._release_leak_lock(held)
    assert result['skipped'] == 'lock held'
    assert result['emailed'] is True
    assert result['attempts'] == 19
    assert times[0] == datetime(2026, 11, 2, 6, 0, tzinfo=EASTERN)
    assert len(emails) == 1


def test_deep_sweep_runs_when_the_lock_frees_before_6am(monkeypatch, tmp_path):
    import indeed_publish.leak_runtime as runtime

    monkeypatch.setenv('INDEED_LEAK_LOCK_FILE', str(tmp_path / 'leak.lock'))
    monkeypatch.setattr(runtime, '_postgres_engine', lambda: None)
    monkeypatch.setattr(runtime, 'is_qualified_tenant', lambda: True)
    monkeypatch.setattr(runtime, 'leak_mode', lambda: 'report')
    monkeypatch.setattr(runtime, 'SWEEP_LOCK_WAIT_SECONDS', 0)
    monkeypatch.setattr(runtime, '_run_deep_sweep_locked', lambda: {'ran': True})
    monkeypatch.setattr(
        runtime,
        '_notify_deep_sweep_missed',
        lambda moment: (_ for _ in ()).throw(AssertionError('emailed')),
    )
    times = [datetime(2026, 11, 2, 3, 0, tzinfo=EASTERN)]
    held = runtime._acquire_file_lock(blocking=False, timeout=0)

    def sleep(seconds):
        times[0] = times[0] + timedelta(seconds=seconds)
        runtime._release_leak_lock(held)

    assert held is not None
    result = runtime.run_deep_sweep(clock=lambda: times[0], sleep=sleep)
    assert result == {'ran': True, 'attempts': 2}
    assert times[0] == datetime(2026, 11, 2, 3, 10, tzinfo=EASTERN)


def test_deep_sweep_miss_email_is_once_per_day(monkeypatch):
    import indeed_publish.leak_runtime as runtime

    store = {}

    class _Settings:
        @staticmethod
        def get_value(key, default=''):
            return store.get(key, default)

        @staticmethod
        def set_value(key, value, **_kwargs):
            store[key] = value

    emails = []
    monkeypatch.setattr('models.scheduling.GlobalSettings', _Settings)
    monkeypatch.setattr(runtime, '_send_email', lambda *args, **_kwargs: emails.append(args) or True)
    morning = datetime(2026, 11, 2, 6, 0, tzinfo=EASTERN)
    assert runtime._notify_deep_sweep_missed(morning) is True
    assert runtime._notify_deep_sweep_missed(morning) is False
    assert runtime._notify_deep_sweep_missed(datetime(2026, 11, 3, 6, 0, tzinfo=EASTERN)) is True
    assert len(emails) == 2
    assert emails[0][1] == 'Indeed leak deep sweep did not run'


def test_failed_advisory_unlock_invalidates_the_connection():
    import indeed_publish.leak_runtime as runtime

    class _Conn:
        def __init__(self):
            self.invalidated = False
            self.closed = False

        def execute(self, _stmt, _params=None):
            raise RuntimeError('unlock failed')

        def invalidate(self):
            self.invalidated = True

        def close(self):
            self.closed = True

    conn = _Conn()
    held = runtime._HeldLock()
    held.kind = 'postgres'
    held.conn = conn
    runtime._release_leak_lock(held)
    assert conn.invalidated is True
    assert conn.closed is False
    assert held.conn is None


def test_digest_load_and_save_stay_inside_the_lock(monkeypatch, tmp_path):
    import indeed_publish.leak_runtime as runtime

    monkeypatch.setenv('INDEED_LEAK_LOCK_FILE', str(tmp_path / 'leak.lock'))
    monkeypatch.setattr(runtime, '_postgres_engine', lambda: None)
    monkeypatch.setattr(runtime, 'is_qualified_tenant', lambda: True)
    monkeypatch.setattr(runtime, 'leak_mode', lambda: 'report')
    monkeypatch.setattr(runtime, 'SWEEP_LOCK_WAIT_SECONDS', 0)
    events = []
    monkeypatch.setattr(runtime, '_load_state', lambda: events.append('load') or {'jobs': {}})
    monkeypatch.setattr(runtime, '_save_state', lambda state: events.append('save'))
    monkeypatch.setattr(runtime, 'digest_is_due', lambda now, state: True)
    monkeypatch.setattr(runtime, '_bh', lambda: object())
    monkeypatch.setattr(runtime, '_ui_client', lambda: object())
    monkeypatch.setattr(
        runtime,
        '_tearsheet_digest_rows',
        lambda _bh, _ui: {'live': [], 'cannot_publish': [], 'not_live': []},
    )
    monkeypatch.setattr(runtime, '_send_email', lambda *_args, **_kwargs: True)
    held = runtime._acquire_file_lock(blocking=False, timeout=0)
    try:
        skipped = runtime.run_weekly_digest()
    finally:
        runtime._release_leak_lock(held)
    assert skipped == {'skipped': 'lock held'}
    assert events == []

    result = runtime.run_weekly_digest()
    assert result['sent']
    assert events == ['load', 'save']


def test_tearsheet_digest_keeps_jobs_whose_status_is_not_indeed_live(monkeypatch):
    import indeed_publish.leak_runtime as runtime

    jobs = {
        1: _job(jid=1, title='Live role'),
        2: _job(jid=2, title='Status failed'),
        3: _job(jid=3, title='2nd shift slicing', publicDescription='', description=''),
    }

    class _UI:
        def get_publication_status(self, job_id):
            if job_id == 1:
                return {'indeed_published': True}
            if job_id == 2:
                raise RuntimeError('HTTP 500')
            return {'indeed_published': False}

    monkeypatch.setattr(runtime, '_tearsheet_ids', lambda _bh: {1, 2, 3})
    monkeypatch.setattr(runtime, '_fetch_job', lambda _bh, job_id: jobs[job_id])
    monkeypatch.setattr(runtime, '_hydrate_emails', lambda _bh, _jobs: None)
    rows = runtime._tearsheet_digest_rows(object(), _UI())
    assert [row['job_id'] for row in rows['live']] == [1]
    by_id = {row['job_id']: row for row in rows['not_live']}
    assert set(by_id) == {2, 3}
    assert 'RuntimeError' in by_id[2]['detail']
    assert 'HTTP 500' in by_id[2]['detail']
    assert by_id[3]['detail'] == 'empty description'
    assert any(row['job_id'] == 3 for row in rows['cannot_publish'])
