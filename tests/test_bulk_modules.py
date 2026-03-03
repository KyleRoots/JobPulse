"""
Unit tests for the bulk module management endpoint.

Tests POST /settings/users/bulk-modules for authentication, authorization,
add/remove operations, edge cases, and idempotency.

Pattern follows test_vetting_sandbox.py: all DB setup, login, and requests
happen inside a single with app.app_context() block.
"""

import json
import pytest
from werkzeug.security import generate_password_hash


def _make_user(db, username, email, is_admin=False, modules=None):
    from models import User
    user = User.query.filter_by(username=username).first()
    if not user:
        user = User(
            username=username,
            email=email,
            is_admin=is_admin,
        )
        user.password_hash = generate_password_hash('testpass', method='pbkdf2:sha256')
        if modules is not None:
            user.set_modules(modules)
        db.session.add(user)
        db.session.commit()
    return user


def _login(app, username, password='testpass'):
    c = app.test_client()
    c.post('/login', data={'username': username, 'password': password},
           follow_redirects=False)
    return c


def _post_bulk(client, user_ids, module, action):
    return client.post(
        '/settings/users/bulk-modules',
        data=json.dumps({'user_ids': user_ids, 'module': module, 'action': action}),
        content_type='application/json'
    )


class TestBulkModulesAuth:

    def test_requires_login(self, app):
        """Unauthenticated POST should redirect to login."""
        with app.app_context():
            c = app.test_client()
            resp = _post_bulk(c, [1], 'scout_inbound', 'add')
            assert resp.status_code in [302, 401]

    def test_requires_admin(self, app):
        """Non-admin user should receive 403 JSON response."""
        from extensions import db
        with app.app_context():
            u = _make_user(db, 'bm_nonadmin', 'bm_nonadmin@test.com', is_admin=False)
            c = _login(app, 'bm_nonadmin')
            resp = _post_bulk(c, [u.id], 'scout_inbound', 'add')
            assert resp.status_code == 403
            data = json.loads(resp.data)
            assert 'error' in data


class TestBulkModulesValidation:

    def test_empty_user_list_returns_400(self, app):
        """Empty user_ids list should return 400."""
        from extensions import db
        with app.app_context():
            _make_user(db, 'bm_admin_val', 'bm_admin_val@test.com', is_admin=True)
            c = _login(app, 'bm_admin_val')
            resp = _post_bulk(c, [], 'scout_inbound', 'add')
            assert resp.status_code == 400
            data = json.loads(resp.data)
            assert 'error' in data

    def test_invalid_module_returns_400(self, app):
        """Unknown module name should return 400."""
        from extensions import db
        with app.app_context():
            _make_user(db, 'bm_admin_mod', 'bm_admin_mod@test.com', is_admin=True)
            c = _login(app, 'bm_admin_mod')
            resp = _post_bulk(c, [1], 'not_a_real_module', 'add')
            assert resp.status_code == 400
            data = json.loads(resp.data)
            assert 'error' in data

    def test_invalid_action_returns_400(self, app):
        """Action other than add/remove should return 400."""
        from extensions import db
        with app.app_context():
            _make_user(db, 'bm_admin_act', 'bm_admin_act@test.com', is_admin=True)
            c = _login(app, 'bm_admin_act')
            resp = _post_bulk(c, [1], 'scout_inbound', 'delete')
            assert resp.status_code == 400
            data = json.loads(resp.data)
            assert 'error' in data


class TestBulkModulesOperations:

    def test_add_module_to_user(self, app):
        """Adding a module should persist to the user's subscribed_modules."""
        from extensions import db
        from models import User
        with app.app_context():
            admin = _make_user(db, 'bm_adm_add', 'bm_adm_add@test.com', is_admin=True)
            target = _make_user(db, 'bm_tgt_add', 'bm_tgt_add@test.com', modules=[])
            uid = target.id
            c = _login(app, 'bm_adm_add')
            resp = _post_bulk(c, [uid], 'scout_inbound', 'add')
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data['success'] == 1
            assert data['skipped'] == 0
            assert data['failed'] == 0
            db.session.expire_all()
            updated = User.query.get(uid)
            assert 'scout_inbound' in updated.get_modules()

    def test_remove_module_from_user(self, app):
        """Removing an existing module should persist the removal."""
        from extensions import db
        from models import User
        with app.app_context():
            _make_user(db, 'bm_adm_rem', 'bm_adm_rem@test.com', is_admin=True)
            target = _make_user(db, 'bm_tgt_rem', 'bm_tgt_rem@test.com',
                                modules=['scout_inbound', 'scout_screening'])
            uid = target.id
            c = _login(app, 'bm_adm_rem')
            resp = _post_bulk(c, [uid], 'scout_inbound', 'remove')
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data['success'] == 1
            db.session.expire_all()
            updated = User.query.get(uid)
            mods = updated.get_modules()
            assert 'scout_inbound' not in mods
            assert 'scout_screening' in mods

    def test_admin_users_are_skipped(self, app):
        """Admin users in the bulk list should be skipped, not modified."""
        from extensions import db
        with app.app_context():
            admin = _make_user(db, 'bm_adm_skip', 'bm_adm_skip@test.com', is_admin=True)
            uid = admin.id
            c = _login(app, 'bm_adm_skip')
            resp = _post_bulk(c, [uid], 'scout_inbound', 'add')
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data['skipped'] == 1
            assert data['success'] == 0

    def test_nonexistent_user_counted_as_failed(self, app):
        """A user ID that does not exist should increment failed_count."""
        from extensions import db
        with app.app_context():
            _make_user(db, 'bm_adm_ne', 'bm_adm_ne@test.com', is_admin=True)
            c = _login(app, 'bm_adm_ne')
            resp = _post_bulk(c, [999999], 'scout_inbound', 'add')
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data['failed'] == 1
            assert data['success'] == 0

    def test_multiple_users_at_once(self, app):
        """Bulk operation should process all users and return correct counts."""
        from extensions import db
        from models import User
        with app.app_context():
            _make_user(db, 'bm_adm_multi', 'bm_adm_multi@test.com', is_admin=True)
            u1 = _make_user(db, 'bm_multi_1', 'bm_multi_1@test.com', modules=[])
            u2 = _make_user(db, 'bm_multi_2', 'bm_multi_2@test.com', modules=[])
            u3 = _make_user(db, 'bm_multi_3', 'bm_multi_3@test.com', modules=[])
            ids = [u1.id, u2.id, u3.id]
            c = _login(app, 'bm_adm_multi')
            resp = _post_bulk(c, ids, 'scout_screening', 'add')
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data['success'] == 3
            assert data['failed'] == 0
            db.session.expire_all()
            for uid in ids:
                u = User.query.get(uid)
                assert 'scout_screening' in u.get_modules()


class TestBulkModulesIdempotency:

    def test_add_module_already_present_no_duplicate(self, app):
        """Adding a module that already exists should not create duplicates."""
        from extensions import db
        from models import User
        with app.app_context():
            _make_user(db, 'bm_adm_idem1', 'bm_adm_idem1@test.com', is_admin=True)
            target = _make_user(db, 'bm_tgt_idem1', 'bm_tgt_idem1@test.com',
                                modules=['scout_inbound'])
            uid = target.id
            c = _login(app, 'bm_adm_idem1')
            resp = _post_bulk(c, [uid], 'scout_inbound', 'add')
            assert resp.status_code == 200
            db.session.expire_all()
            updated = User.query.get(uid)
            mods = updated.get_modules()
            assert mods.count('scout_inbound') == 1

    def test_remove_module_not_present_no_crash(self, app):
        """Removing a module not in the list should succeed without crashing."""
        from extensions import db
        from models import User
        with app.app_context():
            _make_user(db, 'bm_adm_idem2', 'bm_adm_idem2@test.com', is_admin=True)
            target = _make_user(db, 'bm_tgt_idem2', 'bm_tgt_idem2@test.com', modules=[])
            uid = target.id
            c = _login(app, 'bm_adm_idem2')
            resp = _post_bulk(c, [uid], 'scout_screening', 'remove')
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data['success'] == 1
