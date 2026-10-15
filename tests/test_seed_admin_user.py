"""Startup admin seed must not rewrite an account that already exists."""

from unittest.mock import patch

ORIGINAL = 'original-password-1'
ENV_PASSWORD = 'env-password-should-not-apply'
CREATED_PASSWORD = 'created-admin-password-1'


def _config(username, email, password):
    return {
        'username': username,
        'email': email,
        'password': password,
        'is_admin': True,
    }


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


def test_existing_admin_seed_leaves_users_and_passwords_unchanged(app):
    from app import db
    from models import User
    from seeding.users import create_admin_user

    with app.app_context():
        admin = User.query.filter_by(is_admin=True).first()
        assert admin is not None
        original_company = admin.company
        original_hash = admin.password_hash
        original_revision = int(admin.auth_revision or 0)
        admin.company = 'Preserve Co'
        admin.auth_revision = 0
        admin.set_password(ORIGINAL)
        other = User(
            username='seed-other-user',
            email='seed-other-user@example.com',
            is_admin=False,
            company='Other Co',
        )
        other.set_password(ORIGINAL)
        db.session.add(other)
        db.session.commit()
        admin_hash = admin.password_hash
        other_hash = other.password_hash
        admin_id = admin.id
        other_id = other.id
        username = admin.username
        email = admin.email
        count = User.query.count()
        try:
            with patch('seed_database.get_admin_config', return_value=_config(
                'different-admin', 'different-admin@example.com', ENV_PASSWORD,
            )):
                found, created = create_admin_user(db, User)
            db.session.refresh(admin)
            db.session.refresh(other)
            assert created is False
            assert found.id == admin_id
            assert admin.password_hash == admin_hash
            assert admin.check_password(ORIGINAL)
            assert admin.check_password(ENV_PASSWORD) is False
            assert admin.username == username
            assert admin.email == email
            assert admin.company == 'Preserve Co'
            assert admin.is_admin is True
            assert int(admin.auth_revision or 0) == 0
            assert other.password_hash == other_hash
            assert other.check_password(ORIGINAL)
            assert User.query.count() == count
        finally:
            admin = db.session.get(User, admin_id)
            if admin is not None:
                admin.password_hash = original_hash
                admin.company = original_company
                admin.auth_revision = original_revision
            _cleanup(db, User, [other_id])


def test_admin_is_created_only_when_absent(app):
    from app import db
    from models import User
    from seeding.users import create_admin_user

    with app.app_context():
        existing = User.query.filter_by(username='testadmin').first()
        assert existing is not None
        existing_hash = existing.password_hash
        saved_admin_ids = [row.id for row in User.query.filter_by(is_admin=True).all()]
        for row in User.query.filter_by(is_admin=True).all():
            row.is_admin = False
        db.session.commit()
        created_id = None
        try:
            with patch('seed_database.get_admin_config', return_value=_config(
                'seed-created-admin', 'seed-created-admin@example.com', CREATED_PASSWORD,
            )):
                created_user, created = create_admin_user(db, User)
            created_id = created_user.id
            assert created is True
            assert created_user.username == 'seed-created-admin'
            assert created_user.is_admin is True
            assert created_user.check_password(CREATED_PASSWORD)
            assert int(created_user.auth_revision or 0) == 0
            db.session.refresh(existing)
            assert existing.password_hash == existing_hash
            assert existing.is_admin is False
            assert existing.check_password('testpassword123')
        finally:
            if created_id is not None:
                _cleanup(db, User, [created_id])
            for user_id in saved_admin_ids:
                row = db.session.get(User, user_id)
                if row is not None:
                    row.is_admin = True
            db.session.commit()


def test_existing_non_admin_is_not_promoted_or_rewritten(app):
    from app import db
    from models import User
    from seeding.users import create_admin_user

    with app.app_context():
        existing = User.query.filter_by(username='testadmin').first()
        existing_hash = existing.password_hash
        saved_admin_ids = [row.id for row in User.query.filter_by(is_admin=True).all()]
        for row in User.query.filter_by(is_admin=True).all():
            row.is_admin = False
        db.session.commit()
        count = User.query.count()
        try:
            with patch('seed_database.get_admin_config', return_value=_config(
                existing.username, existing.email, ENV_PASSWORD,
            )):
                found, created = create_admin_user(db, User)
            db.session.refresh(existing)
            assert created is False
            assert found.id == existing.id
            assert existing.is_admin is False
            assert existing.password_hash == existing_hash
            assert existing.check_password(ENV_PASSWORD) is False
            assert User.query.count() == count
        finally:
            for user_id in saved_admin_ids:
                row = db.session.get(User, user_id)
                if row is not None:
                    row.is_admin = True
            db.session.commit()


def test_second_seed_keeps_legacy_session(client, app):
    from app import db
    from models import User
    from seeding.users import create_admin_user

    with app.app_context():
        admin = User.query.filter_by(username='testadmin').first()
        admin.auth_revision = 0
        db.session.commit()
        admin_id = admin.id
        with patch('seed_database.get_admin_config', return_value=_config(
            'different-admin', 'different-admin@example.com', ENV_PASSWORD,
        )):
            create_admin_user(db, User)
            create_admin_user(db, User)
        db.session.refresh(admin)
        assert int(admin.auth_revision or 0) == 0

    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin_id)
        sess['_fresh'] = True
    response = client.get('/login', follow_redirects=False)
    assert response.status_code == 302
