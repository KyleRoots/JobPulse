"""Tests for the multi-tenant Bullhorn backbone (Task #100, foundation pass).

Covers the registry model, the environment_id discriminator on ATS-scoped
tables, the idempotent default-environment seed + backfill, and the env-aware
get_bullhorn_service factory (default falls back to GlobalSettings byte-for-byte;
inline-credentialed environments use their own creds).

These run on the conftest SQLite app fixture (create_all, no seed_database), so
each test creates the rows it needs and cleans up the registry / settings it
touches to avoid leaking into other tests.
"""
import pytest


_ENV_SCOPED_MODELS = [
    ("CandidateVettingLog", "candidate_vetting_log"),
    ("CandidateJobMatch", "candidate_job_match"),
    ("JobVettingRequirements", "job_vetting_requirements"),
    ("ParsedEmail", "parsed_email"),
    ("BullhornMonitor", "bullhorn_monitor"),
    ("CandidateFraudAssessment", "candidate_fraud_assessment"),
    ("JobEmbedding", "job_embedding"),
    ("CandidateProfileEmbedding", "candidate_profile_embedding"),
    ("RecruiterNotificationLedger", "recruiter_notification_ledger"),
]


@pytest.fixture
def clean_registry(app):
    """Remove any BullhornEnvironment rows + cred GlobalSettings before/after."""
    def _wipe():
        from app import db
        import models
        try:
            models.BullhornEnvironment.query.delete()
            db.session.commit()
        except Exception:
            db.session.rollback()
        for key in ('bullhorn_client_id', 'bullhorn_client_secret',
                    'bullhorn_username', 'bullhorn_password'):
            try:
                models.GlobalSettings.query.filter_by(setting_key=key).delete()
                db.session.commit()
            except Exception:
                db.session.rollback()

    with app.app_context():
        _wipe()
        yield
        _wipe()


def test_discriminator_columns_present(app):
    """Every ATS-scoped model carries an environment_id discriminator column."""
    import models
    with app.app_context():
        for class_name, table_name in _ENV_SCOPED_MODELS:
            model = getattr(models, class_name)
            assert hasattr(model, 'environment_id'), (
                f"{class_name} is missing environment_id"
            )
            assert 'environment_id' in model.__table__.columns, (
                f"{table_name} table is missing environment_id column"
            )


def test_seed_default_environment_is_idempotent(app, clean_registry):
    """Seeding creates exactly one default 'myticas' env, repeatable safely."""
    from app import db
    from models import BullhornEnvironment
    from seeding.settings import seed_bullhorn_environment

    with app.app_context():
        seed_bullhorn_environment(db)
        seed_bullhorn_environment(db)  # second call must not duplicate

        defaults = BullhornEnvironment.query.filter_by(is_default=True).all()
        assert len(defaults) == 1
        env = defaults[0]
        assert env.key == 'myticas'
        assert env.is_active is True
        # Default env intentionally carries NO inline credentials.
        assert env.resolve_credentials() is None


def test_backfill_sets_environment_on_existing_rows(app, clean_registry):
    """Pre-existing NULL rows get backfilled to the default environment id."""
    from app import db
    from models import CandidateVettingLog, JobEmbedding, BullhornEnvironment
    from seeding.settings import seed_bullhorn_environment

    with app.app_context():
        log = CandidateVettingLog(
            bullhorn_candidate_id=999001, candidate_name='Backfill Test',
            status='pending',
        )
        emb = JobEmbedding(
            bullhorn_job_id=999001, description_hash='x' * 64,
            embedding_vector='[]',
        )
        db.session.add_all([log, emb])
        db.session.commit()
        assert log.environment_id is None
        assert emb.environment_id is None

        seed_bullhorn_environment(db)

        env = BullhornEnvironment.get_default()
        db.session.refresh(log)
        db.session.refresh(emb)
        assert log.environment_id == env.id
        assert emb.environment_id == env.id

        # Cleanup the rows we created.
        db.session.delete(log)
        db.session.delete(emb)
        db.session.commit()


def test_resolve_credentials_requires_full_set(app, clean_registry):
    """A partially-credentialed environment resolves to None (no half-clients)."""
    from models import BullhornEnvironment

    with app.app_context():
        partial = BullhornEnvironment(
            key='partial', display_name='Partial',
            bullhorn_client_id='cid', bullhorn_client_secret='sec',
            # username + password missing
        )
        assert partial.resolve_credentials() is None

        full = BullhornEnvironment(
            key='full', display_name='Full',
            bullhorn_client_id='cid', bullhorn_client_secret='sec',
            bullhorn_username='user', bullhorn_password='pass',
        )
        creds = full.resolve_credentials()
        assert creds == {
            'bullhorn_client_id': 'cid',
            'bullhorn_client_secret': 'sec',
            'bullhorn_username': 'user',
            'bullhorn_password': 'pass',
        }


def test_get_bullhorn_service_defaults_to_global_settings(app, clean_registry):
    """No env / default env (no inline creds) → credentials from GlobalSettings.

    This is the byte-for-byte legacy path: a no-argument call must build the
    client from GlobalSettings exactly as before multi-tenancy existed.
    """
    from app import db
    from models import GlobalSettings
    from seeding.settings import seed_bullhorn_environment
    from utils.bullhorn_helpers import get_bullhorn_service

    with app.app_context():
        for key, val in [
            ('bullhorn_client_id', 'gs_cid'),
            ('bullhorn_client_secret', 'gs_sec'),
            ('bullhorn_username', 'gs_user'),
            ('bullhorn_password', 'gs_pass'),
        ]:
            db.session.add(GlobalSettings(setting_key=key, setting_value=val))
        db.session.commit()

        # (a) No environment row at all → GlobalSettings path.
        svc = get_bullhorn_service()
        assert svc.client_id == 'gs_cid'
        assert svc.client_secret == 'gs_sec'
        assert svc.username == 'gs_user'
        assert svc.password == 'gs_pass'

        # (b) Default env present but with no inline creds → still GlobalSettings.
        seed_bullhorn_environment(db)
        svc2 = get_bullhorn_service()
        assert svc2.client_id == 'gs_cid'
        assert svc2.password == 'gs_pass'


def test_get_bullhorn_service_uses_inline_env_credentials(app, clean_registry):
    """An environment with inline creds uses them, not GlobalSettings."""
    from app import db
    from models import GlobalSettings, BullhornEnvironment
    from utils.bullhorn_helpers import get_bullhorn_service

    with app.app_context():
        # GlobalSettings present to prove inline creds take precedence.
        for key, val in [
            ('bullhorn_client_id', 'gs_cid'),
            ('bullhorn_client_secret', 'gs_sec'),
            ('bullhorn_username', 'gs_user'),
            ('bullhorn_password', 'gs_pass'),
        ]:
            db.session.add(GlobalSettings(setting_key=key, setting_value=val))
        env = BullhornEnvironment(
            key='qs_test', display_name='Qualified Staffing',
            bullhorn_client_id='qs_cid', bullhorn_client_secret='qs_sec',
            bullhorn_username='qs_user', bullhorn_password='qs_pass',
        )
        db.session.add(env)
        db.session.commit()

        svc = get_bullhorn_service(env)
        assert svc.client_id == 'qs_cid'
        assert svc.client_secret == 'qs_sec'
        assert svc.username == 'qs_user'
        assert svc.password == 'qs_pass'
