"""
Regression tests for /scout-screening/search — server-side candidate search.

Covers:
- Auth gate (login required)
- 3-character minimum
- Empty job scope returns empty results
- Name search (legacy behaviour preserved)
- Bullhorn ID exact match (numeric query)
- Email substring search
- Phone digit-normalised search via ParsedEmail
- Status filter (qualified / not_recommended / location_barrier)
- Min-score filter
- this_week filter
- Pagination (page 1, page 2, clamping past last page)
- Invalid query params don't crash
- Response shape includes pagination + group_counts
- Group counts reflect FULL filtered set, not just current page
"""
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

ADMIN_USER = 'ts_search_admin'
ADMIN_PASS = 'testpass'

# Job IDs we'll associate with test data
JOB_A = 90001
JOB_B = 90002
JOB_OUTSIDE = 90099  # candidate match exists but outside any user's scope


def _login(app, username=ADMIN_USER, password=ADMIN_PASS):
    c = app.test_client()
    c.post('/login', data={'username': username, 'password': password},
           follow_redirects=False)
    return c


def _ensure_admin(app):
    from extensions import db
    from models import User
    with app.app_context():
        u = User.query.filter_by(username=ADMIN_USER).first()
        if not u:
            u = User(username=ADMIN_USER, email=f'{ADMIN_USER}@test.com', is_admin=True)
            u.password_hash = generate_password_hash(ADMIN_PASS, method='pbkdf2:sha256')
            db.session.add(u)
            db.session.commit()


def _seed_search_data(app, monkeypatch):
    """Seed CandidateVettingLog + CandidateJobMatch + ParsedEmail rows that the
    search route can return. Patches _get_user_job_ids so the admin user sees
    {JOB_A, JOB_B} without needing a BullhornMonitor snapshot.
    """
    from extensions import db
    from models import CandidateJobMatch, CandidateVettingLog, ParsedEmail

    monkeypatch.setattr(
        'routes.scout_screening._get_user_job_ids',
        lambda: {JOB_A, JOB_B},
    )

    with app.app_context():
        # Wipe any prior seeded rows so reruns are deterministic
        CandidateJobMatch.query.filter(
            CandidateJobMatch.bullhorn_job_id.in_({JOB_A, JOB_B, JOB_OUTSIDE})
        ).delete(synchronize_session=False)
        CandidateVettingLog.query.filter(
            CandidateVettingLog.bullhorn_candidate_id.in_(range(70000, 70100))
        ).delete(synchronize_session=False)
        ParsedEmail.query.filter(
            ParsedEmail.bullhorn_candidate_id.in_(range(70000, 70100))
        ).delete(synchronize_session=False)
        db.session.commit()

        now = datetime.utcnow()

        # Candidate 70001 — Smith Alpha — qualified on JOB_A, this week
        log1 = CandidateVettingLog(
            bullhorn_candidate_id=70001,
            candidate_name='Alice Smith Alpha',
            candidate_email='alice.alpha@example.com',
            applied_job_id=JOB_A,
            status='completed',
            is_qualified=True,
            highest_match_score=92.0,
            created_at=now,
            updated_at=now,
        )
        db.session.add(log1)
        db.session.flush()
        db.session.add(CandidateJobMatch(
            vetting_log_id=log1.id,
            bullhorn_job_id=JOB_A,
            job_title='Senior Engineer',
            match_score=92.0,
            technical_score=92.0,
            is_qualified=True,
            is_applied_job=True,
            match_summary='Strong match',
            gaps_identified='',
            created_at=now,
        ))

        # Candidate 70002 — Bob Smith Beta — not recommended on JOB_B, last month
        old = now - timedelta(days=30)
        log2 = CandidateVettingLog(
            bullhorn_candidate_id=70002,
            candidate_name='Bob Smith Beta',
            candidate_email='bob.beta@example.com',
            applied_job_id=JOB_B,
            status='completed',
            is_qualified=False,
            highest_match_score=42.0,
            created_at=old,
            updated_at=old,
        )
        db.session.add(log2)
        db.session.flush()
        db.session.add(CandidateJobMatch(
            vetting_log_id=log2.id,
            bullhorn_job_id=JOB_B,
            job_title='QA Engineer',
            match_score=42.0,
            technical_score=42.0,
            is_qualified=False,
            is_applied_job=True,
            match_summary='Low fit',
            gaps_identified='Missing automation experience',
            created_at=old,
        ))

        # Candidate 70003 — Carol Smith Gamma — location barrier on JOB_A
        log3 = CandidateVettingLog(
            bullhorn_candidate_id=70003,
            candidate_name='Carol Smith Gamma',
            candidate_email='carol.gamma@example.com',
            applied_job_id=JOB_A,
            status='completed',
            is_qualified=False,
            highest_match_score=78.0,
            created_at=now,
            updated_at=now,
        )
        db.session.add(log3)
        db.session.flush()
        db.session.add(CandidateJobMatch(
            vetting_log_id=log3.id,
            bullhorn_job_id=JOB_A,
            job_title='Senior Engineer',
            match_score=70.0,
            technical_score=78.0,  # tech ≥ threshold-15 = 65 → triggers loc-barrier
            is_qualified=False,
            is_applied_job=True,
            match_summary='Solid skills, wrong region',
            gaps_identified='Location mismatch — candidate is remote-only',
            created_at=now,
        ))
        # Phone for 70003 lives on ParsedEmail
        db.session.add(ParsedEmail(
            message_id='test-msg-70003@example.com',
            sender_email='carol.gamma@example.com',
            recipient_email='applications@scoutgenius.test',
            bullhorn_candidate_id=70003,
            candidate_name='Carol Smith Gamma',
            candidate_email='carol.gamma@example.com',
            candidate_phone='5552349876',
            status='completed',
            received_at=now,
        ))

        # Candidate 70005 — Eve Smith Echo — PENDING (no matches yet, in scope).
        # Used for pending-parity coverage: status='pending' search must return
        # this row, and group_counts.pending must always reflect its presence.
        log5 = CandidateVettingLog(
            bullhorn_candidate_id=70005,
            candidate_name='Eve Smith Echo',
            candidate_email='eve.echo@example.com',
            applied_job_id=JOB_A,
            status='pending',
            is_qualified=False,
            highest_match_score=0.0,
            created_at=now,
            updated_at=now,
        )
        db.session.add(log5)

        # Candidate 70004 — outside scope (job not in {JOB_A, JOB_B})
        log4 = CandidateVettingLog(
            bullhorn_candidate_id=70004,
            candidate_name='Dave Smith Delta',
            candidate_email='dave.delta@example.com',
            applied_job_id=JOB_OUTSIDE,
            status='completed',
            is_qualified=True,
            highest_match_score=95.0,
            created_at=now,
            updated_at=now,
        )
        db.session.add(log4)
        db.session.flush()
        db.session.add(CandidateJobMatch(
            vetting_log_id=log4.id,
            bullhorn_job_id=JOB_OUTSIDE,
            job_title='Other Role',
            match_score=95.0,
            technical_score=95.0,
            is_qualified=True,
            is_applied_job=True,
            match_summary='',
            gaps_identified='',
            created_at=now,
        ))

        db.session.commit()


def _seed_bulk_for_pagination(app, monkeypatch, count=25):
    """Seed `count` qualified candidates so we can exercise pagination at
    page_size=5. All match a unique surname so the search returns exactly
    `count` candidate groups."""
    from extensions import db
    from models import CandidateJobMatch, CandidateVettingLog

    monkeypatch.setattr('routes.scout_screening._get_user_job_ids', lambda: {JOB_A})

    with app.app_context():
        CandidateJobMatch.query.filter(
            CandidateJobMatch.bullhorn_job_id == JOB_A
        ).delete(synchronize_session=False)
        CandidateVettingLog.query.filter(
            CandidateVettingLog.bullhorn_candidate_id.in_(range(80000, 80100))
        ).delete(synchronize_session=False)
        db.session.commit()

        now = datetime.utcnow()
        for i in range(count):
            log = CandidateVettingLog(
                bullhorn_candidate_id=80000 + i,
                candidate_name=f'Pageable Person {i:02d}',
                candidate_email=f'page{i}@example.com',
                applied_job_id=JOB_A,
                status='completed',
                is_qualified=True,
                highest_match_score=85.0,
                # Vary timestamps so order is deterministic (desc)
                created_at=now - timedelta(minutes=i),
                updated_at=now - timedelta(minutes=i),
            )
            db.session.add(log)
            db.session.flush()
            db.session.add(CandidateJobMatch(
                vetting_log_id=log.id,
                bullhorn_job_id=JOB_A,
                job_title='Pageable Role',
                match_score=85.0,
                technical_score=85.0,
                is_qualified=True,
                is_applied_job=True,
                match_summary='ok',
                gaps_identified='',
                created_at=now - timedelta(minutes=i),
            ))
        db.session.commit()


# ---------------------------------------------------------------------------
# Auth + minimum-input gates
# ---------------------------------------------------------------------------

class TestSearchAccessGates:
    def test_anonymous_user_redirected(self, app):
        # The session-scoped `app` fixture holds an outer app_context for the
        # whole test session, which lets `g._login_user` from earlier
        # authenticated tests bleed into a freshly created `test_client()` —
        # the request reuses the outer `g` instead of pushing a clean one,
        # so a "fresh" client appears authenticated even with empty cookies.
        # `tests/test_admin_health.py` documents the same leak and uses the
        # same workaround: patch `flask_login.utils._get_user` to force an
        # anonymous resolution for the duration of the request.
        from unittest.mock import patch, MagicMock

        anon = MagicMock()
        anon.is_authenticated = False
        anon.is_anonymous = True
        anon.is_active = False
        anon.get_id = lambda: None

        c = app.test_client()
        with patch('flask_login.utils._get_user', return_value=anon):
            resp = c.get('/scout-screening/search?q=alice',
                         follow_redirects=False)
        assert resp.status_code in (302, 401), (
            f"Unauthenticated request should be blocked, got {resp.status_code}"
        )
        assert '/login' in (resp.headers.get('Location') or '')

    def test_query_under_three_chars_returns_empty(self, app):
        _ensure_admin(app)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=ab', follow_redirects=False)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['results'] == []
        assert data['total_groups'] == 0
        assert data['total_pages'] == 0

    def test_no_job_scope_returns_empty(self, app, monkeypatch):
        _ensure_admin(app)
        monkeypatch.setattr(
            'routes.scout_screening._get_user_job_ids',
            lambda: set(),
        )
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['results'] == []
        assert data['total_groups'] == 0


# ---------------------------------------------------------------------------
# Search predicates
# ---------------------------------------------------------------------------

class TestSearchPredicates:
    def test_name_search_returns_in_scope_groups(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith')
        assert resp.status_code == 200
        data = resp.get_json()
        # 70001/70002/70003 — Dave (70004) is outside scope
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert 70001 in cand_ids
        assert 70002 in cand_ids
        assert 70003 in cand_ids
        assert 70004 not in cand_ids
        assert data['total_groups'] == 3

    def test_bullhorn_id_exact_match(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=70002')
        data = resp.get_json()
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert cand_ids == {70002}, f"Expected only 70002, got {cand_ids}"

    def test_email_substring_match(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        # Match on the local-part of carol's email
        resp = c.get('/scout-screening/search?q=carol.gamma')
        data = resp.get_json()
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert 70003 in cand_ids
        assert 70001 not in cand_ids

    def test_phone_search_via_parsed_email(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        # Phone seeded as digits-only ('5552349876') so both the Postgres
        # branch (regexp_replace) and the SQLite branch (raw ilike) match.
        # Query has separators which the route normalises to digits.
        resp = c.get('/scout-screening/search?q=555-234-9876')
        data = resp.get_json()
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert 70003 in cand_ids, f"Phone search should return 70003, got {cand_ids}"

    def test_short_digit_query_does_not_trigger_phone_branch(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        # 70001 is also a 5-digit number; should be matched as Bullhorn ID, not
        # as a phone (phone branch needs ≥7 digits)
        resp = c.get('/scout-screening/search?q=70001')
        data = resp.get_json()
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert cand_ids == {70001}

    def test_digits_only_phone_search_works(self, app, monkeypatch):
        # Phone search must work for digits-only input.
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        # Carol's seeded phone is "5552349876" (no separators).
        resp = c.get('/scout-screening/search?q=5552349876').get_json()
        cand_ids = {r['candidate_id'] for r in resp['results']}
        assert 70003 in cand_ids, (
            f"Digits-only phone query 5552349876 must find Carol "
            f"via the phone branch. Got {cand_ids}"
        )

    def test_all_digit_id_query_returns_id_match_plus_phone_match(
        self, app, monkeypatch
    ):
        # All-digit query 7+ chars fires both ID and phone predicates;
        # exact ID hit must always be in the result set.
        from extensions import db
        from models import CandidateJobMatch, CandidateVettingLog, ParsedEmail

        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)

        with app.app_context():
            now = datetime.utcnow()
            # Candidate whose ID happens to be 7 digits.
            log = CandidateVettingLog(
                bullhorn_candidate_id=1234567,
                candidate_name='Frank Foxtrot',
                candidate_email='frank.foxtrot@example.com',
                applied_job_id=JOB_A,
                status='completed',
                is_qualified=True,
                highest_match_score=88.0,
                created_at=now,
                updated_at=now,
            )
            db.session.add(log)
            db.session.flush()
            db.session.add(CandidateJobMatch(
                vetting_log_id=log.id,
                bullhorn_job_id=JOB_A,
                job_title='Senior Engineer',
                match_score=88.0,
                technical_score=88.0,
                is_qualified=True,
                is_applied_job=True,
                match_summary='Good fit',
                gaps_identified='',
                created_at=now,
            ))
            # Different candidate whose phone happens to contain "1234567".
            log2 = CandidateVettingLog(
                bullhorn_candidate_id=70011,
                candidate_name='Grace Golf',
                candidate_email='grace.golf@example.com',
                applied_job_id=JOB_A,
                status='completed',
                is_qualified=False,
                highest_match_score=55.0,
                created_at=now,
                updated_at=now,
            )
            db.session.add(log2)
            db.session.flush()
            db.session.add(CandidateJobMatch(
                vetting_log_id=log2.id,
                bullhorn_job_id=JOB_A,
                job_title='QA Engineer',
                match_score=55.0,
                technical_score=55.0,
                is_qualified=False,
                is_applied_job=True,
                match_summary='Mid fit',
                gaps_identified='',
                created_at=now,
            ))
            db.session.add(ParsedEmail(
                message_id='test-msg-70011@example.com',
                sender_email='grace.golf@example.com',
                recipient_email='applications@scoutgenius.test',
                bullhorn_candidate_id=70011,
                candidate_name='Grace Golf',
                candidate_email='grace.golf@example.com',
                candidate_phone='5551234567',  # contains "1234567"
                status='completed',
                received_at=now,
            ))
            db.session.commit()

        resp = c.get('/scout-screening/search?q=1234567').get_json()
        cand_ids = {r['candidate_id'] for r in resp['results']}
        # Exact-ID hit MUST be present — that's the non-negotiable
        # invariant the previous architect rightly flagged.
        assert 1234567 in cand_ids, (
            f"Exact ID 1234567 must always appear in the result set "
            f"for the all-digit query — got {cand_ids}"
        )
        # And the phone-spillover candidate is also included, by design.
        assert 70011 in cand_ids, (
            f"All-digit query 1234567 should also surface candidates "
            f"with that digit substring in their phone — got {cand_ids}"
        )

        # Formatted phone query continues to work for the spillover case.
        resp = c.get('/scout-screening/search?q=555-123-4567').get_json()
        cand_ids = {r['candidate_id'] for r in resp['results']}
        assert 70011 in cand_ids


# ---------------------------------------------------------------------------
# Filter chips
# ---------------------------------------------------------------------------

class TestFilterChips:
    def test_status_qualified_excludes_others(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith&status=qualified')
        data = resp.get_json()
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert cand_ids == {70001}, f"Only Alice should be qualified, got {cand_ids}"
        assert data['group_counts']['qualified'] == 1
        assert data['group_counts']['not_recommended'] == 0
        assert data['group_counts']['location_barrier'] == 0

    def test_status_not_recommended(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith&status=not_recommended')
        data = resp.get_json()
        cand_ids = {r['candidate_id'] for r in data['results']}
        # Only Bob — Carol is loc_barrier (excluded), Alice is qualified
        assert cand_ids == {70002}

    def test_status_location_barrier(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith&status=location_barrier')
        data = resp.get_json()
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert cand_ids == {70003}

    def test_min_score_filter(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        # Alice=92, Carol=70, Bob=42 → min_score=80 keeps only Alice
        resp = c.get('/scout-screening/search?q=Smith&min_score=80')
        data = resp.get_json()
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert cand_ids == {70001}

    def test_this_week_filter(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        # Bob is from 30 days ago → excluded
        resp = c.get('/scout-screening/search?q=Smith&this_week=1')
        data = resp.get_json()
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert 70002 not in cand_ids
        assert 70001 in cand_ids
        assert 70003 in cand_ids

    def test_pending_status_returns_pending_logs(self, app, monkeypatch):
        # status='pending' must surface in-scope CandidateVettingLog rows
        # whose status is 'pending' (no matches yet). Architect-flagged
        # parity requirement — was previously silently ignored.
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith&status=pending')
        data = resp.get_json()
        # Eve (70005) is the only pending Smith in scope.
        cand_ids = {r['candidate_id'] for r in data['results']}
        assert cand_ids == {70005}, (
            f"status=pending should return only the pending candidate, "
            f"got {cand_ids}"
        )
        assert data['total_groups'] == 1
        # Pending pseudo-rows expose is_pending=True so the frontend can
        # render them with the Pending badge instead of a score badge.
        assert all(r.get('is_pending') is True for r in data['results']), (
            "Every result row in pending mode must carry is_pending=True"
        )

    def test_pending_count_in_group_counts_for_unscoped_search(self, app, monkeypatch):
        # group_counts.pending reflects pending logs matching q when
        # status is unset.
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith').get_json()
        assert resp['group_counts'].get('pending') == 1, (
            "group_counts.pending must reflect pending logs matching q "
            "(Eve 70005) when status is unset, got "
            f"{resp['group_counts'].get('pending')}"
        )

    def test_pending_count_zero_when_status_chip_scopes_to_match_bucket(
        self, app, monkeypatch
    ):
        # When status scopes to a match bucket, pending tile must be 0
        # so tiles agree with the displayed rows.
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)

        for chip in ('qualified', 'not_recommended', 'location_barrier'):
            resp = c.get(
                f'/scout-screening/search?q=Smith&status={chip}'
            ).get_json()
            assert resp['group_counts'].get('pending') == 0, (
                f"group_counts.pending must be 0 when status={chip} "
                f"is active (Eve is pending, not in {chip} bucket), "
                f"got {resp['group_counts'].get('pending')}"
            )

    def test_this_week_filter_applied_in_sql_for_match_query(
        self, app, monkeypatch
    ):
        # this_week is pushed into SQL so the SAFETY_CAP honours it.
        # A match older than 7 days must not appear when this_week=1.
        from extensions import db
        from models import CandidateJobMatch, CandidateVettingLog

        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)

        with app.app_context():
            old_ts = datetime.utcnow() - timedelta(days=30)
            log_old = CandidateVettingLog(
                bullhorn_candidate_id=70020,
                candidate_name='Henry Hotel',
                candidate_email='henry.hotel@example.com',
                applied_job_id=JOB_A,
                status='completed',
                is_qualified=True,
                highest_match_score=92.0,
                created_at=old_ts,
                updated_at=old_ts,
            )
            db.session.add(log_old)
            db.session.flush()
            db.session.add(CandidateJobMatch(
                vetting_log_id=log_old.id,
                bullhorn_job_id=JOB_A,
                job_title='Tenured Role',
                match_score=92.0,
                technical_score=92.0,
                is_qualified=True,
                is_applied_job=True,
                match_summary='Old hire',
                gaps_identified='',
                created_at=old_ts,
            ))
            db.session.commit()

        # No this_week filter → Henry should appear
        resp = c.get('/scout-screening/search?q=Henry').get_json()
        cand_ids = {r['candidate_id'] for r in resp['results']}
        assert 70020 in cand_ids, (
            f"Without this_week filter Henry should appear, got {cand_ids}"
        )

        # this_week=1 → Henry MUST be filtered out at the SQL layer
        resp = c.get('/scout-screening/search?q=Henry&this_week=1').get_json()
        cand_ids = {r['candidate_id'] for r in resp['results']}
        assert 70020 not in cand_ids, (
            f"With this_week=1 Henry (30 days old) must be filtered out "
            f"in SQL, got {cand_ids}"
        )

    def test_phone_search_documented_as_parsed_email_only(self, app, monkeypatch):
        # The phone-search join is intentionally limited to ParsedEmail.
        # There is no `Candidate` table in the schema (Bullhorn is the
        # source of truth and we don't replicate it locally), so a
        # candidate whose phone exists only in Bullhorn cannot be reached
        # via this search. This test locks the documented constraint:
        # candidate 70001 has NO ParsedEmail row, so phone-search can't
        # find them by phone even though they're in scope.
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        # 70001 (Alice) has no ParsedEmail row; search by an arbitrary
        # phone that doesn't match Carol's 5552349876 must return empty.
        resp = c.get('/scout-screening/search?q=555-111-2222').get_json()
        cand_ids = {r['candidate_id'] for r in resp['results']}
        assert 70001 not in cand_ids, (
            "Candidates without a ParsedEmail row are unreachable via "
            "phone search by design — see route docstring for rationale"
        )


class TestTemplateJsIntegrity:
    """Static lint of the scout_screening.html JS to catch undefined-helper
    regressions in the search-mode render path. The previous bug — a call
    to non-existent `_renderSearchPager(...)` in the empty-results branch
    of `_renderSearchResults` — threw a ReferenceError that dropped the
    user out of search mode silently. This test locks the invariant."""

    def test_render_search_results_calls_only_defined_functions(self):
        import re
        from pathlib import Path
        tpl = Path('templates/scout_screening.html').read_text()

        m = re.search(r'function\s+_renderSearchResults\s*\([^)]*\)\s*\{',
                      tpl)
        assert m, '_renderSearchResults function not found in template'
        depth = 0
        i = m.end() - 1
        body_start = i + 1
        while i < len(tpl):
            ch = tpl[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    body_end = i
                    break
            i += 1
        else:
            assert False, 'Could not find end of _renderSearchResults body'
        body = tpl[body_start:body_end]

        called = set(re.findall(r'\b(_[A-Za-z][A-Za-z0-9_]*)\s*\(', body))
        defined = set(re.findall(
            r'function\s+(_[A-Za-z][A-Za-z0-9_]*)\s*\(', tpl))
        defined |= set(re.findall(
            r'\b(_[A-Za-z][A-Za-z0-9_]*)\s*=\s*function\b', tpl))

        missing = called - defined
        assert not missing, (
            f'_renderSearchResults calls undefined helpers: {missing}. '
            f'Define them or remove the calls — this caused a prior '
            f'silent search-mode dropout regression.'
        )


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class TestPagination:
    def test_page_1_returns_first_chunk(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_bulk_for_pagination(app, monkeypatch, count=25)
        c = _login(app)
        # page_size has a server-side floor of 10
        resp = c.get('/scout-screening/search?q=Pageable&page=1&page_size=10')
        data = resp.get_json()
        assert data['page'] == 1
        assert data['page_size'] == 10
        assert data['total_groups'] == 25
        assert data['total_pages'] == 3
        assert len(data['results']) == 10

    def test_page_2_returns_next_chunk(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_bulk_for_pagination(app, monkeypatch, count=25)
        c = _login(app)
        resp_p1 = c.get('/scout-screening/search?q=Pageable&page=1&page_size=10').get_json()
        resp_p2 = c.get('/scout-screening/search?q=Pageable&page=2&page_size=10').get_json()
        ids_p1 = {r['candidate_id'] for r in resp_p1['results']}
        ids_p2 = {r['candidate_id'] for r in resp_p2['results']}
        assert resp_p2['page'] == 2
        assert len(resp_p2['results']) == 10
        # No overlap between pages
        assert ids_p1.isdisjoint(ids_p2), (
            f"Pages should not overlap; page1={ids_p1}, page2={ids_p2}"
        )

    def test_page_past_end_clamps_to_last_page(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_bulk_for_pagination(app, monkeypatch, count=25)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Pageable&page=99&page_size=10').get_json()
        # Server clamps page to total_pages (3)
        assert resp['page'] == 3
        # Last page has remaining 5 candidates (25 - 20)
        assert len(resp['results']) == 5

    def test_default_page_size_when_unspecified(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_bulk_for_pagination(app, monkeypatch, count=25)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Pageable').get_json()
        assert resp['page_size'] == 100
        assert resp['total_pages'] == 1
        assert len(resp['results']) == 25

    def test_group_counts_reflect_full_set_not_just_page(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_bulk_for_pagination(app, monkeypatch, count=25)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Pageable&page=1&page_size=10').get_json()
        # All 25 are qualified; group_counts must show 25 not 10
        assert resp['group_counts']['qualified'] == 25


# ---------------------------------------------------------------------------
# Robustness — invalid params don't crash
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_invalid_page_param_does_not_crash(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith&page=abc')
        assert resp.status_code == 200
        assert resp.get_json()['page'] == 1

    def test_invalid_min_score_does_not_crash(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith&min_score=lots')
        assert resp.status_code == 200
        # Falls through as 0 (no floor)
        data = resp.get_json()
        assert data['total_groups'] == 3

    def test_min_score_clamped_to_100(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Smith&min_score=999')
        data = resp.get_json()
        # 999 clamped to 100 → no candidate's best_score >= 100 in seed
        assert data['total_groups'] == 0

    def test_oversized_page_size_clamped(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_bulk_for_pagination(app, monkeypatch, count=12)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Pageable&page_size=9999').get_json()
        # 9999 clamped to 200
        assert resp['page_size'] == 200

    def test_undersized_page_size_floored_to_10(self, app, monkeypatch):
        # The server-side floor stops a malicious / accidental page_size=1
        # from forcing a page-per-row crawl that would defeat pagination.
        # Numeric values below 10 must clamp UP to 10. Non-numeric input
        # is a separate concern — the route falls back to the default
        # (100) on parse failure, which is also fine.
        _ensure_admin(app)
        _seed_bulk_for_pagination(app, monkeypatch, count=12)
        c = _login(app)
        for size in ('1', '5', '0', '-3', '9'):
            resp = c.get(
                f'/scout-screening/search?q=Pageable&page_size={size}'
            ).get_json()
            assert resp['page_size'] == 10, (
                f"page_size={size!r} should clamp to floor of 10, "
                f"got {resp['page_size']}"
            )
        # Non-numeric falls back to the route's default of 100 (clamped
        # within [10, 200]) — verify that fallback is applied, not the
        # floor.
        resp = c.get(
            '/scout-screening/search?q=Pageable&page_size=abc'
        ).get_json()
        assert resp['page_size'] == 100, (
            "Non-numeric page_size should fall back to default 100, "
            f"got {resp['page_size']}"
        )

    def test_truncated_flag_contract(self, app, monkeypatch):
        # `truncated` is the recruiter-facing signal that the underlying
        # scan hit SAFETY_CAP=5000 and rows past the cap are invisible.
        # This locks the contract: the field is always a boolean, and
        # stays False for result sets that comfortably fit under the cap.
        # A targeted "tripped the cap" assertion would require seeding
        # 5000+ rows, which is too heavy for the unit suite — that path
        # is exercised in the dashboard's integration smoke test.
        _ensure_admin(app)
        _seed_bulk_for_pagination(app, monkeypatch, count=15)
        c = _login(app)
        resp = c.get('/scout-screening/search?q=Pageable').get_json()
        assert isinstance(resp['truncated'], bool), (
            "`truncated` must be a boolean in the response contract"
        )
        assert resp['truncated'] is False, (
            "15 rows should never trip the real SAFETY_CAP=5000"
        )

    def test_response_contract_keys_present(self, app, monkeypatch):
        _ensure_admin(app)
        _seed_search_data(app, monkeypatch)
        c = _login(app)
        data = c.get('/scout-screening/search?q=Smith').get_json()
        for key in ('results', 'total_groups', 'page', 'page_size',
                    'total_pages', 'group_counts', 'truncated'):
            assert key in data, f"Missing key '{key}' in search response"
        for key in ('qualified', 'not_recommended', 'location_barrier',
                    'week', 'pending'):
            assert key in data['group_counts'], (
                f"Missing key '{key}' in group_counts"
            )
