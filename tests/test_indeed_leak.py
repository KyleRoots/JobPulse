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
    assert emails[0]['to'] == 'susan@example.com'
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
    assert sorted(item['to'] for item in emails) == [
        'stacy@example.com',
        'susan@example.com',
    ]


def test_notice_falls_back_to_owner_then_ops():
    owner_job = _job(assignedUsers={'data': [
        _user(1, 'Unassigned', 'User', 'dead@example.com'),
    ]})
    _run, emails, _removed = _drive([owner_job], mode=MODE_ENFORCE)
    assert [item['to'] for item in emails] == ['kim@example.com']

    ops_job = _job(
        id=38327,
        assignedUsers={'data': [_user(1, 'Unassigned', 'User', '')]},
        owner=_user(1, 'Unassigned', 'User', 'nobody@example.com'),
    )
    _run, emails, _removed = _drive([ops_job], mode=MODE_ENFORCE)
    assert [item['to'] for item in emails] == [OPS]
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
