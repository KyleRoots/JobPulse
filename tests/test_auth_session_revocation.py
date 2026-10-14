"""Password save and reset must invalidate session and remember cookies."""

ORIGINAL = 'original-password-1'
CHANGED = 'changed-password-22'
UNCHANGED = 'unchanged-password-1'
WEAK = 'short'


def _clear_login_cache():
    """The session-scoped app context keeps ``g._login_user`` across requests.

    Production pushes a new app context per request, so Flask-Login reloads
    the cookie each time. Tests have to drop the cached user or a later
    client looks signed in without its own cookies.
    """
    from flask import g
    g.pop('_login_user', None)


def _signed_in(client):
    _clear_login_cache()
    response = client.get('/login', follow_redirects=False)
    return response.status_code == 302


def _cleanup(db, User, user_ids):
    from models import PasswordResetToken, UserActivityLog
    if not user_ids:
        return
    PasswordResetToken.query.filter(
        PasswordResetToken.user_id.in_(user_ids)
    ).delete(synchronize_session=False)
    UserActivityLog.query.filter(
        UserActivityLog.user_id.in_(user_ids)
    ).delete(synchronize_session=False)
    User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
    db.session.commit()


def _add_user(db, User, username, password, is_admin=False):
    user = User(
        username=username,
        email=f'{username}@example.com',
        is_admin=is_admin,
        company='Preserve Co',
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, username, password):
    _clear_login_cache()
    response = client.post('/login', data={
        'username': username,
        'password': password,
    }, follow_redirects=False)
    assert response.status_code == 302, response.status_code
    names = [h.split('=', 1)[0] for h in response.headers.getlist('Set-Cookie')]
    assert 'remember_token' in names, names
    assert _signed_in(client)
    return response


def _remember_only(app, source):
    remember = source.get_cookie('remember_token')
    assert remember is not None
    assert remember.value
    other = app.test_client()
    other.set_cookie('remember_token', remember.value)
    assert _signed_in(other)
    return other


def _save_password(client, user_id, password, is_admin=False):
    data = {
        'password': password,
        'display_name': '',
        'bullhorn_user_id': '',
        'company': 'Preserve Co',
    }
    if is_admin:
        data['is_admin'] = 'on'
    _clear_login_cache()
    return client.post(
        f'/settings/users/{user_id}/update',
        data=data,
        follow_redirects=False,
    )


def test_password_save_revokes_session_and_remember_cookies(app):
    from app import db
    from models import User

    with app.app_context():
        owner = _add_user(db, User, 'revoke-owner', ORIGINAL, is_admin=True)
        bystander = _add_user(db, User, 'revoke-bystander', UNCHANGED, is_admin=True)
        owner_id = owner.id
        bystander_id = bystander.id
        try:
            owner_session = app.test_client()
            _login(owner_session, 'revoke-owner', ORIGINAL)
            other_session = app.test_client()
            _login(other_session, 'revoke-owner', ORIGINAL)
            remembered = _remember_only(app, other_session)
            bystander_session = app.test_client()
            _login(bystander_session, 'revoke-bystander', UNCHANGED)

            with owner_session.session_transaction() as sess:
                assert sess.get('_user_id') == str(owner_id)

            response = _save_password(owner_session, owner_id, CHANGED, is_admin=True)
            assert response.status_code == 302
            assert '/login' in (response.headers.get('Location') or '')

            owner_row = db.session.get(User, owner_id)
            assert int(owner_row.auth_revision or 0) == 1
            assert owner_row.check_password(CHANGED)
            assert owner_row.check_password(ORIGINAL) is False
            assert owner_row.get_id() == f'{owner_id}:1'

            assert _signed_in(owner_session) is False
            assert _signed_in(other_session) is False
            assert _signed_in(remembered) is False
            assert _signed_in(bystander_session) is True
            bystander_row = db.session.get(User, bystander_id)
            assert int(bystander_row.auth_revision or 0) == 0
            assert bystander_row.check_password(UNCHANGED)

            fresh = app.test_client()
            _login(fresh, 'revoke-owner', CHANGED)
            anonymous = app.test_client()
            _clear_login_cache()
            rejected = anonymous.post('/login', data={
                'username': 'revoke-owner',
                'password': ORIGINAL,
            }, follow_redirects=False)
            assert rejected.status_code == 200
            assert _signed_in(anonymous) is False
        finally:
            _cleanup(db, User, [owner_id, bystander_id])


def test_weak_password_and_profile_save_do_not_revoke(app):
    from app import db
    from models import User

    with app.app_context():
        owner = _add_user(db, User, 'keep-owner', ORIGINAL, is_admin=True)
        owner_id = owner.id
        try:
            session_client = app.test_client()
            _login(session_client, 'keep-owner', ORIGINAL)
            remembered = _remember_only(app, session_client)

            weak = _save_password(session_client, owner_id, WEAK, is_admin=True)
            assert weak.status_code == 302
            owner_row = db.session.get(User, owner_id)
            db.session.refresh(owner_row)
            assert int(owner_row.auth_revision or 0) == 0
            assert owner_row.check_password(ORIGINAL)
            assert _signed_in(session_client) is True
            assert _signed_in(remembered) is True

            profile = session_client.post(
                f'/settings/users/{owner_id}/update',
                data={
                    'display_name': 'Kept',
                    'bullhorn_user_id': '',
                    'company': 'Preserve Co',
                    'is_admin': 'on',
                },
                follow_redirects=False,
            )
            assert profile.status_code == 302
            assert '/login' not in (profile.headers.get('Location') or '')
            db.session.refresh(owner_row)
            assert int(owner_row.auth_revision or 0) == 0
            assert owner_row.check_password(ORIGINAL)
            assert _signed_in(session_client) is True
            assert _signed_in(remembered) is True
        finally:
            _cleanup(db, User, [owner_id])


def test_reset_revokes_session_and_remember_cookies(app):
    from app import db
    from models import User
    from routes.auth import generate_reset_token

    with app.app_context():
        owner = _add_user(db, User, 'reset-owner', ORIGINAL, is_admin=True)
        bystander = _add_user(db, User, 'reset-bystander', UNCHANGED, is_admin=True)
        owner_id = owner.id
        bystander_id = bystander.id
        try:
            session_client = app.test_client()
            _login(session_client, 'reset-owner', ORIGINAL)
            remembered = _remember_only(app, session_client)
            bystander_session = app.test_client()
            _login(bystander_session, 'reset-bystander', UNCHANGED)

            mismatch = app.test_client()
            _clear_login_cache()
            bad_token = generate_reset_token(owner)
            bad = mismatch.post(f'/reset-password/{bad_token}', data={
                'password': CHANGED,
                'confirm_password': 'does-not-match-99',
            }, follow_redirects=False)
            assert bad.status_code == 200
            owner_row = db.session.get(User, owner_id)
            db.session.refresh(owner_row)
            assert int(owner_row.auth_revision or 0) == 0
            assert owner_row.check_password(ORIGINAL)
            assert _signed_in(session_client) is True
            assert _signed_in(remembered) is True

            token = generate_reset_token(owner)
            anonymous = app.test_client()
            _clear_login_cache()
            ok = anonymous.post(f'/reset-password/{token}', data={
                'password': CHANGED,
                'confirm_password': CHANGED,
            }, follow_redirects=False)
            assert ok.status_code == 302
            db.session.refresh(owner_row)
            assert int(owner_row.auth_revision or 0) == 1
            assert owner_row.check_password(CHANGED)
            assert _signed_in(session_client) is False
            assert _signed_in(remembered) is False
            assert _signed_in(bystander_session) is True

            fresh = app.test_client()
            _login(fresh, 'reset-owner', CHANGED)
        finally:
            _cleanup(db, User, [owner_id, bystander_id])


def test_legacy_plain_id_rejected_after_password_save(app):
    from app import db
    from models import User

    with app.app_context():
        owner = _add_user(db, User, 'legacy-owner', ORIGINAL, is_admin=True)
        owner_id = owner.id
        try:
            legacy = app.test_client()
            _clear_login_cache()
            with legacy.session_transaction() as sess:
                sess['_user_id'] = str(owner_id)
                sess['_fresh'] = True
            assert _signed_in(legacy) is True

            editor = app.test_client()
            _login(editor, 'legacy-owner', ORIGINAL)
            _save_password(editor, owner_id, CHANGED, is_admin=True)

            assert _signed_in(legacy) is False
            with legacy.session_transaction() as sess:
                sess['_user_id'] = f'{owner_id}:1'
                sess['_fresh'] = True
            assert _signed_in(legacy) is True
            with legacy.session_transaction() as sess:
                sess['_user_id'] = str(owner_id)
                sess['_fresh'] = True
            assert _signed_in(legacy) is False
        finally:
            _cleanup(db, User, [owner_id])


def test_admin_save_of_another_users_password_revokes_only_that_user(app):
    from app import db
    from models import User

    with app.app_context():
        admin = _add_user(db, User, 'editor-admin', ORIGINAL, is_admin=True)
        target = _add_user(db, User, 'edited-user', UNCHANGED, is_admin=False)
        admin_id = admin.id
        target_id = target.id
        try:
            admin_session = app.test_client()
            _login(admin_session, 'editor-admin', ORIGINAL)
            target_session = app.test_client()
            _login(target_session, 'edited-user', UNCHANGED)
            remembered = _remember_only(app, target_session)

            response = _save_password(admin_session, target_id, CHANGED, is_admin=False)
            assert response.status_code == 302
            assert '/login' not in (response.headers.get('Location') or '')

            target_row = db.session.get(User, target_id)
            admin_row = db.session.get(User, admin_id)
            db.session.refresh(target_row)
            db.session.refresh(admin_row)
            assert int(target_row.auth_revision or 0) == 1
            assert target_row.check_password(CHANGED)
            assert int(admin_row.auth_revision or 0) == 0
            assert admin_row.check_password(ORIGINAL)
            assert _signed_in(admin_session) is True
            assert _signed_in(target_session) is False
            assert _signed_in(remembered) is False
        finally:
            _cleanup(db, User, [admin_id, target_id])


def test_authenticated_reset_post_does_not_change_password(app):
    from app import db
    from models import User
    from routes.auth import generate_reset_token

    with app.app_context():
        owner = _add_user(db, User, 'reset-while-in', ORIGINAL, is_admin=True)
        owner_id = owner.id
        try:
            session_client = app.test_client()
            _login(session_client, 'reset-while-in', ORIGINAL)
            token = generate_reset_token(owner)
            _clear_login_cache()
            response = session_client.post(f'/reset-password/{token}', data={
                'password': CHANGED,
                'confirm_password': CHANGED,
            }, follow_redirects=False)
            assert response.status_code == 302
            owner_row = db.session.get(User, owner_id)
            db.session.refresh(owner_row)
            assert int(owner_row.auth_revision or 0) == 0
            assert owner_row.check_password(ORIGINAL)
            assert _signed_in(session_client) is True
        finally:
            _cleanup(db, User, [owner_id])


def test_stop_impersonation_returns_admin_until_password_changes(app):
    from app import db
    from models import User

    with app.app_context():
        admin = _add_user(db, User, 'return-admin', ORIGINAL, is_admin=True)
        target = _add_user(db, User, 'return-target', UNCHANGED, is_admin=False)
        admin_id = admin.id
        target_id = target.id
        try:
            admin_session = app.test_client()
            _login(admin_session, 'return-admin', ORIGINAL)
            other_admin = app.test_client()
            _login(other_admin, 'return-admin', ORIGINAL)
            _clear_login_cache()
            started = admin_session.post(
                f'/settings/users/{target_id}/impersonate',
                follow_redirects=False,
            )
            assert started.status_code == 302
            _clear_login_cache()
            returned = admin_session.get('/stop-impersonation', follow_redirects=False)
            assert returned.status_code == 302
            assert _signed_in(admin_session) is True
            with admin_session.session_transaction() as sess:
                assert sess.get('_user_id') == str(admin_id)
                assert 'impersonating_admin_id' not in sess

            _clear_login_cache()
            admin_session.post(
                f'/settings/users/{target_id}/impersonate',
                follow_redirects=False,
            )
            _save_password(other_admin, admin_id, CHANGED, is_admin=True)
            _clear_login_cache()
            blocked = admin_session.get('/stop-impersonation', follow_redirects=False)
            assert blocked.status_code == 302
            assert '/login' in (blocked.headers.get('Location') or '')
            assert _signed_in(admin_session) is False
            with admin_session.session_transaction() as sess:
                assert 'impersonating_admin_id' not in sess
        finally:
            _cleanup(db, User, [admin_id, target_id])


def test_impersonated_password_save_drops_admin_return_ticket(app):
    from app import db
    from models import User

    with app.app_context():
        admin = _add_user(db, User, 'ticket-admin', ORIGINAL, is_admin=True)
        other_admin = _add_user(db, User, 'ticket-other', UNCHANGED, is_admin=True)
        admin_id = admin.id
        other_id = other_admin.id
        try:
            admin_session = app.test_client()
            _login(admin_session, 'ticket-admin', ORIGINAL)
            _clear_login_cache()
            admin_session.post(
                f'/settings/users/{other_id}/impersonate',
                follow_redirects=False,
            )
            response = _save_password(admin_session, other_id, CHANGED, is_admin=True)
            assert response.status_code == 302
            assert '/login' in (response.headers.get('Location') or '')
            with admin_session.session_transaction() as sess:
                assert 'impersonating_admin_id' not in sess
            assert _signed_in(admin_session) is False
            _login(admin_session, 'ticket-other', CHANGED)
            _clear_login_cache()
            restore = admin_session.get('/stop-impersonation', follow_redirects=False)
            assert restore.status_code == 302
            with admin_session.session_transaction() as sess:
                assert sess.get('_user_id') == f'{other_id}:1'
                assert sess.get('impersonating_admin_id') != admin_id
        finally:
            _cleanup(db, User, [admin_id, other_id])


def test_impersonation_does_not_revoke_remember_cookie(app):
    from app import db
    from models import User

    with app.app_context():
        admin = _add_user(db, User, 'impersonate-admin', ORIGINAL, is_admin=True)
        target = _add_user(db, User, 'impersonate-target', UNCHANGED, is_admin=False)
        admin_id = admin.id
        target_id = target.id
        try:
            admin_session = app.test_client()
            _login(admin_session, 'impersonate-admin', ORIGINAL)
            remembered = _remember_only(app, admin_session)
            _clear_login_cache()
            response = admin_session.post(
                f'/settings/users/{target_id}/impersonate',
                follow_redirects=False,
            )
            assert response.status_code == 302
            admin_row = db.session.get(User, admin_id)
            target_row = db.session.get(User, target_id)
            assert int(admin_row.auth_revision or 0) == 0
            assert int(target_row.auth_revision or 0) == 0
            assert _signed_in(remembered) is True
            _clear_login_cache()
            remembered.get('/login', follow_redirects=False)
            with remembered.session_transaction() as sess:
                assert sess.get('_user_id') == str(admin_id)
        finally:
            _cleanup(db, User, [admin_id, target_id])
