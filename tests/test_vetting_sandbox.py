"""
Regression tests for Vetting Sandbox feature.

Covers:
- Access control (super-admin only)
- is_sandbox flag isolation from production views
- Cleanup endpoint deletes only sandbox records
- Screen endpoint returns expected JSON structure
"""

import json
import pytest
from werkzeug.security import generate_password_hash


def _make_user(db, username, email, is_admin=False, is_company_admin=False, modules=None, company='Myticas Consulting'):
    from models import User
    user = User.query.filter_by(username=username).first()
    if not user:
        user = User(
            username=username,
            email=email,
            is_admin=is_admin,
            is_company_admin=is_company_admin,
            company=company,
        )
        user.password_hash = generate_password_hash('testpass', method='pbkdf2:sha256')
        if modules is not None:
            user.set_modules(modules)
        db.session.add(user)
        db.session.commit()
    return user


def _login_fresh(app, username, password='testpass'):
    c = app.test_client()
    resp = c.post('/login', data={'username': username, 'password': password},
                  follow_redirects=False)
    return c, resp


class TestSandboxAccess:

    def test_super_admin_can_access(self, app):
        from extensions import db
        with app.app_context():
            _make_user(db, 'sb_admin', 'sb_admin@test.com', is_admin=True, modules=['scout_vetting'])
            c, _ = _login_fresh(app, 'sb_admin')
            resp = c.get('/vetting-sandbox', follow_redirects=True)
            assert resp.status_code == 200
            assert b'Vetting Sandbox' in resp.data

    def test_regular_user_gets_403(self, app):
        from extensions import db
        with app.app_context():
            _make_user(db, 'sb_user', 'sb_user@test.com', is_admin=False, modules=['scout_vetting'])
            c, _ = _login_fresh(app, 'sb_user')
            resp = c.get('/vetting-sandbox')
            assert resp.status_code == 403

    def test_company_admin_gets_403(self, app):
        from extensions import db
        with app.app_context():
            _make_user(db, 'sb_cadmin', 'sb_cadmin@test.com', is_admin=False, is_company_admin=True, modules=['scout_vetting'])
            c, _ = _login_fresh(app, 'sb_cadmin')
            resp = c.get('/vetting-sandbox')
            assert resp.status_code == 403

    def test_unauthenticated_redirects(self, app):
        with app.app_context():
            c = app.test_client()
            resp = c.get('/vetting-sandbox')
            assert resp.status_code in (302, 401)


class TestSandboxIsolation:

    def test_sandbox_records_excluded_from_production_queries(self, app):
        from extensions import db
        from models import CandidateVettingLog
        with app.app_context():
            sandbox_log = CandidateVettingLog(
                bullhorn_candidate_id=99999,
                candidate_name='Sandbox Test',
                candidate_email='sandbox@test.com',
                applied_job_id=1,
                applied_job_title='Test Job',
                status='completed',
                is_qualified=True,
                highest_match_score=90,
                is_sandbox=True,
            )
            db.session.add(sandbox_log)
            db.session.commit()

            prod_results = CandidateVettingLog.query.filter(
                CandidateVettingLog.is_sandbox != True
            ).all()
            sandbox_ids = [r.id for r in prod_results]
            assert sandbox_log.id not in sandbox_ids

            db.session.delete(sandbox_log)
            db.session.commit()

    def test_sandbox_records_excluded_from_pending_query(self, app):
        from extensions import db
        from models import CandidateVettingLog
        with app.app_context():
            sandbox_log = CandidateVettingLog(
                bullhorn_candidate_id=0,
                candidate_name='Sandbox Pending',
                candidate_email='sandbox2@test.com',
                applied_job_id=2,
                applied_job_title='Test Job 2',
                status='pending',
                is_sandbox=True,
            )
            db.session.add(sandbox_log)
            db.session.commit()

            pending = CandidateVettingLog.query.filter(
                CandidateVettingLog.status.in_(['pending', 'processing']),
                CandidateVettingLog.is_sandbox != True
            ).all()
            assert sandbox_log.id not in [r.id for r in pending]

            db.session.delete(sandbox_log)
            db.session.commit()


class TestSandboxCleanup:

    def test_cleanup_deletes_only_sandbox_records(self, app):
        from extensions import db
        from models import CandidateVettingLog
        with app.app_context():
            _make_user(db, 'sb_cleanup_admin', 'sb_cleanup@test.com', is_admin=True, modules=['scout_vetting'])

            prod_log = CandidateVettingLog(
                bullhorn_candidate_id=88888,
                candidate_name='Production Candidate',
                candidate_email='prod@test.com',
                applied_job_id=3,
                applied_job_title='Prod Job',
                status='completed',
                is_qualified=True,
                highest_match_score=85,
                is_sandbox=False,
            )
            sandbox_log = CandidateVettingLog(
                bullhorn_candidate_id=0,
                candidate_name='Sandbox Candidate',
                candidate_email='sandbox@test.com',
                applied_job_id=0,
                applied_job_title='Sandbox Job',
                status='completed',
                is_qualified=True,
                highest_match_score=90,
                is_sandbox=True,
            )
            db.session.add_all([prod_log, sandbox_log])
            db.session.commit()
            prod_id = prod_log.id
            sandbox_id = sandbox_log.id

            c, _ = _login_fresh(app, 'sb_cleanup_admin')
            resp = c.post('/vetting-sandbox/cleanup',
                         data=json.dumps({}),
                         content_type='application/json')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['success'] is True

            assert CandidateVettingLog.query.get(prod_id) is not None
            assert CandidateVettingLog.query.get(sandbox_id) is None

            remaining = CandidateVettingLog.query.get(prod_id)
            if remaining:
                db.session.delete(remaining)
                db.session.commit()


class TestScreenEndpoint:

    def test_screen_requires_resume(self, app):
        from extensions import db
        with app.app_context():
            _make_user(db, 'sb_screen_admin', 'sb_screen@test.com', is_admin=True, modules=['scout_vetting'])
            c, _ = _login_fresh(app, 'sb_screen_admin')
            resp = c.post('/vetting-sandbox/screen',
                         data=json.dumps({
                             'job_source': 'custom',
                             'job_title': 'Test',
                             'job_description': 'Test desc',
                             'resume_text': '',
                         }),
                         content_type='application/json')
            assert resp.status_code == 400
            data = resp.get_json()
            assert 'error' in data

    def test_screen_requires_job_id_for_existing(self, app):
        from extensions import db
        with app.app_context():
            _make_user(db, 'sb_screen2_admin', 'sb_screen2@test.com', is_admin=True, modules=['scout_vetting'])
            c, _ = _login_fresh(app, 'sb_screen2_admin')
            resp = c.post('/vetting-sandbox/screen',
                         data=json.dumps({
                             'job_source': 'existing',
                             'resume_text': 'Some resume text',
                         }),
                         content_type='application/json')
            assert resp.status_code == 400

    def test_jobs_endpoint_returns_json(self, app):
        from extensions import db
        with app.app_context():
            _make_user(db, 'sb_jobs_admin', 'sb_jobs@test.com', is_admin=True, modules=['scout_vetting'])
            c, _ = _login_fresh(app, 'sb_jobs_admin')
            resp = c.get('/vetting-sandbox/jobs')
            assert resp.status_code == 200
            data = resp.get_json()
            assert 'jobs' in data
            assert isinstance(data['jobs'], list)
