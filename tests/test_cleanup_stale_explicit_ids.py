"""Legacy stale-'processing' cleanup: recovery only for explicit ids, through
the shared explicit-recovery primitive; dry-run and CLI never write; route
authorization unchanged. Synthetic rows only."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

import vetting_audit_service.run_claim as run_claim
from scripts import cleanup_stale_processing_rows as cleanup

CANDIDATE = 4700101
JOB = 35911
URL = '/api/trigger/cleanup-stale-vetting'


def _now():
    return datetime.utcnow()


@pytest.fixture(autouse=True)
def _clean(app):
    from app import db
    from models import CandidateJobMatch, CandidateVettingLog, EmbeddingFilterLog, EscalationLog, ParsedEmail

    def wipe():
        with app.app_context():
            EmbeddingFilterLog.query.delete()
            EscalationLog.query.delete()
            CandidateJobMatch.query.delete()
            CandidateVettingLog.query.delete()
            ParsedEmail.query.delete()
            db.session.commit()

    wipe()
    yield
    wipe()


def _seed(*, age_hours=30, heartbeat_age_hours=None, matches=0, email=True, candidate=CANDIDATE):
    from app import db
    from models import CandidateJobMatch, CandidateVettingLog, ParsedEmail

    pe_id = None
    if email:
        pe = ParsedEmail(sender_email='synthetic@example.test', recipient_email='inbox@example.test',
                         status='completed', bullhorn_candidate_id=candidate, bullhorn_job_id=JOB,
                         received_at=_now() - timedelta(hours=age_hours),
                         processed_at=_now() - timedelta(hours=age_hours),
                         vetted_at=_now() - timedelta(hours=age_hours))
        db.session.add(pe)
        db.session.flush()
        pe_id = pe.id
    created = _now() - timedelta(hours=age_hours)
    hb = heartbeat_age_hours if heartbeat_age_hours is not None else age_hours
    vlog = CandidateVettingLog(bullhorn_candidate_id=candidate, candidate_name='Synthetic Candidate',
                               status='processing', parsed_email_id=pe_id, applied_job_id=JOB,
                               highest_match_score=0.0, is_qualified=False, is_sandbox=False,
                               created_at=created, updated_at=_now() - timedelta(hours=hb))
    db.session.add(vlog)
    db.session.flush()
    for i in range(matches):
        db.session.add(CandidateJobMatch(vetting_log_id=vlog.id, bullhorn_job_id=JOB + i,
                                         job_title='Synthetic Job', match_score=40.0, is_qualified=False,
                                         is_applied_job=(i == 0), match_summary='synthetic', gaps_identified=''))
    db.session.commit()
    return vlog.id, pe_id


def _snapshot():
    from models import CandidateJobMatch, CandidateVettingLog, ParsedEmail
    return (
        sorted((v.id, v.status, v.parsed_email_id, v.error_message) for v in CandidateVettingLog.query.all()),
        sorted((m.id, m.vetting_log_id, m.match_score) for m in CandidateJobMatch.query.all()),
        sorted((p.id, p.vetted_at) for p in ParsedEmail.query.all()),
    )


def _no_recovery():
    return patch.object(run_claim, 'recover_expired_lease', side_effect=AssertionError('must not recover'))


# -- explicit-id validation ---------------------------------------------------

@pytest.mark.parametrize('bad', [None, [], 'all', 5, {'ids': [1]}, [0], [-3], [True], ['7'], [1.0],
                                 list(range(1, 53))])
def test_apply_without_valid_explicit_ids_fails_closed(app, bad):
    with app.app_context():
        _seed()
        before = _snapshot()
        with _no_recovery():
            result = cleanup.cleanup_stale_processing(24, True, log_ids=bad)
        assert result['success'] is False and result['applied'] is False
        assert 'ids' in result['error'] or 'At most' in result['error']
        assert _snapshot() == before


def test_route_apply_without_ids_is_rejected_and_writes_nothing(app, authenticated_client):
    with app.app_context():
        _seed()
        before = _snapshot()
    with _no_recovery():
        for body in ({'apply': True}, {'apply': True, 'ids': []}, {'apply': True, 'ids': 'all'},
                     {'apply': 'true', 'ids': [1]}):
            resp = authenticated_client.post(URL, json=body)
            assert resp.status_code == 400, body
            assert resp.get_json()['success'] is False
    with app.app_context():
        assert _snapshot() == before


# -- dry run --------------------------------------------------------------------

def test_dry_run_lists_rows_and_changes_nothing(app, authenticated_client):
    with app.app_context():
        log_id, _ = _seed()
        before = _snapshot()
    with _no_recovery():
        resp = authenticated_client.post(URL, json={'hours': 24, 'apply': False, 'ids': [log_id]})
    data = resp.get_json()
    assert resp.status_code == 200 and data['success'] is True
    assert (data['applied'], data['dry_run'], data['count']) == (False, True, 1)
    assert [r['id'] for r in data['rows']] == [log_id]
    assert data['message'].startswith('Dry run, no changes made')
    with app.app_context():
        assert _snapshot() == before


def test_cli_is_dry_run_only(app):
    with app.app_context():
        _seed()
        before = _snapshot()
    with _no_recovery():
        assert cleanup._run_cli(24, True) == 2
        assert cleanup._run_cli(24, False) == 0
    with app.app_context():
        assert _snapshot() == before


# -- shared protocol --------------------------------------------------------------

def test_apply_recovers_only_selected_ids_through_the_shared_primitive(app, authenticated_client):
    from models import CandidateJobMatch, CandidateVettingLog, ParsedEmail

    with app.app_context():
        bare, bare_pe = _seed(candidate=CANDIDATE)
        partial, partial_pe = _seed(candidate=CANDIDATE + 1, matches=2)
        untouched, untouched_pe = _seed(candidate=CANDIDATE + 2)
    calls, locks = [], []
    real_recover, real_lock = run_claim.recover_expired_lease, run_claim.acquire_candidate_lock

    def spy_recover(log_id, expired_before):
        calls.append((log_id, expired_before))
        return real_recover(log_id, expired_before)

    def spy_lock(candidate_id, *a, **kw):
        locks.append(candidate_id)
        return real_lock(candidate_id, *a, **kw)

    with patch.object(run_claim, 'recover_expired_lease', spy_recover), \
            patch.object(run_claim, 'acquire_candidate_lock', spy_lock):
        resp = authenticated_client.post(URL, json={'hours': 24, 'apply': True,
                                                    'ids': [bare, partial, 987654321, bare]})
    data = resp.get_json()
    assert resp.status_code == 200 and data['success'] is True
    assert data['requested_ids'] == [bare, partial, 987654321]
    assert [c[0] for c in calls] == [bare, partial]
    assert all(c[1] <= _now() - timedelta(hours=24) for c in calls)
    assert locks == [CANDIDATE, CANDIDATE + 1]
    assert [r['id'] for r in data['recovered']] == [bare, partial]
    assert [r['id'] for r in data['no_change']] == [987654321] and data['deferred'] == []
    assert (data['applied'], data['dry_run'], data['count']) == (True, False, 2)
    assert 'paid calls it already made are not undone' in data['message']
    with app.app_context():
        assert CandidateVettingLog.query.get(bare) is None
        assert ParsedEmail.query.get(bare_pe).vetted_at is None
        p = CandidateVettingLog.query.get(partial)
        assert p.status == 'failed' and p.parsed_email_id is None and p.highest_match_score == 0.0
        assert CandidateJobMatch.query.filter_by(vetting_log_id=partial).count() == 2
        assert ParsedEmail.query.get(partial_pe).vetted_at is None
        u = CandidateVettingLog.query.get(untouched)
        assert u.status == 'processing' and ParsedEmail.query.get(untouched_pe).vetted_at is not None


def test_recent_heartbeat_is_rechecked_and_left_alone(app):
    with app.app_context():
        live, _ = _seed(age_hours=30, heartbeat_age_hours=0.05)
        slow, _ = _seed(age_hours=30, heartbeat_age_hours=2, candidate=CANDIDATE + 1)
        young, _ = _seed(age_hours=2, candidate=CANDIDATE + 2)
        before = _snapshot()
        result = cleanup.cleanup_stale_processing(24, True, log_ids=[live, slow, young])
        assert result['success'] is True and result['applied'] is False and result['recovered'] == []
        assert [r['id'] for r in result['no_change']] == [live, slow, young]
        assert 'under the candidate run lock' in result['no_change'][0]['reason']
        assert 'older than 24h' in result['no_change'][2]['reason']
        assert result['message'].endswith('No rows were changed.')
        assert _snapshot() == before


def test_lock_unavailable_defers_without_change(app):
    def unavailable(candidate_id, *a, **kw):
        raise run_claim.CandidateLockUnavailable('synthetic')

    with app.app_context():
        log_id, _ = _seed()
        before = _snapshot()
        with patch.object(run_claim, 'acquire_candidate_lock', unavailable):
            result = cleanup.cleanup_stale_processing(24, True, log_ids=[log_id])
        assert result['deferred'] == [{'id': log_id, 'reason': 'candidate run lock unavailable; retry later'}]
        assert result['applied'] is False and result['count'] == 0
        assert _snapshot() == before


# -- authorization unchanged ------------------------------------------------------

def test_authorization_is_unchanged(app, client):
    from app import db
    from models import User

    with app.app_context():
        log_id, _ = _seed()
        before = _snapshot()
        if not User.query.filter_by(username='cleanup_nonadmin').first():
            u = User(username='cleanup_nonadmin', email='cleanup_nonadmin@test.com', is_admin=False)
            u.password_hash = generate_password_hash('testpass', method='pbkdf2:sha256')
            db.session.add(u)
            db.session.commit()
    body = {'apply': True, 'ids': [log_id]}
    with _no_recovery():
        anon = client.post(URL, json=body)
        assert anon.status_code in (302, 401)
        c = app.test_client()
        c.post('/login', data={'username': 'cleanup_nonadmin', 'password': 'testpass'})
        denied = c.post(URL, json=body)
        assert denied.status_code == 403
        assert json.loads(denied.data)['error'] == 'Admin access required'
    with app.app_context():
        assert _snapshot() == before
