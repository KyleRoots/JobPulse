"""Per-user Location Review email preference seeding."""

from seeding.users import (
    LOCATION_REVIEW_EMAILS_DEFAULT_OFF,
    seed_location_review_email_defaults,
)


def test_adam_email_in_default_off_list():
    assert 'agebara@myticas.com' in LOCATION_REVIEW_EMAILS_DEFAULT_OFF


def test_seed_sets_adam_off(app, db_session):
    from models import User
    from extensions import db

    with app.app_context():
        adam = User.query.filter(User.email.ilike('agebara@myticas.com')).first()
        if adam is None:
            adam = User(
                username='agebara.test',
                email='agebara@myticas.com',
                company='Myticas Consulting',
            )
            adam.set_password('x')
            db.session.add(adam)
        adam.location_review_emails_enabled = True

        other = User.query.filter(User.email.ilike('other-lr-pref@myticas.com')).first()
        if other is None:
            other = User(
                username='other.lrpref',
                email='other-lr-pref@myticas.com',
                company='Myticas Consulting',
            )
            other.set_password('x')
            db.session.add(other)
        other.location_review_emails_enabled = True
        db.session.commit()

        seed_location_review_email_defaults(db, User)

        adam = User.query.filter(User.email.ilike('agebara@myticas.com')).first()
        other = User.query.filter(User.email.ilike('other-lr-pref@myticas.com')).first()
        assert adam.location_review_emails_enabled is False
        assert other.location_review_emails_enabled is True
