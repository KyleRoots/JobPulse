"""Tests for the Customer Onboarding Wizard / tenant provisioning (Task #102).

Covers: happy-path provisioning (env + brand + admin + modules), live-credential
validation gating (invalid creds block with zero writes), required-field
validation, uniqueness guards, isolation invariant (new tenant never default),
and the readiness check.
"""
import pytest

from utils.tenant_provisioning import (
    provision_tenant,
    run_readiness_check,
    validate_bullhorn_credentials,
    ProvisioningError,
)


@pytest.fixture(autouse=True)
def _clean_tenant_tables(db_session):
    """Purge tenant tables before each test (children first) so leaked rows from
    the harness's partial-unique single-default index don't cross-contaminate."""
    from models import BullhornEnvironment, Brand, User
    User.query.delete()
    Brand.query.delete()
    BullhornEnvironment.query.delete()
    db_session.commit()
    yield


def _seed_default_env(db_session):
    """Seed a minimal default environment (stands in for Myticas)."""
    from models import BullhornEnvironment
    env = BullhornEnvironment(
        key='myticas', display_name='Myticas', is_default=True, is_active=True,
    )
    db_session.add(env)
    db_session.commit()
    return env


_GOOD_PAYLOAD = dict(
    display_name='Acme Staffing',
    company_name='Acme Staffing LLC',
    credentials={
        'bullhorn_client_id': 'cid', 'bullhorn_client_secret': 'csec',
        'bullhorn_username': 'user', 'bullhorn_password': 'pass',
    },
    brand={
        'display_name': 'Acme', 'apply_domain': 'apply.acme.com',
        'company_name': 'Acme Staffing LLC', 'from_email': 'careers@acme.com',
        'to_email': 'apply@acme.com',
    },
    screening_profile='standard',
    admin={'username': 'acme_admin', 'email': 'admin@acme.com', 'password': 'pw'},
    modules=['scout_inbound', 'scout_screening'],
)


# ── Credential validation ────────────────────────────────────────────────────

def test_validate_missing_credentials_blocks():
    ok, msg = validate_bullhorn_credentials('', '', '', '')
    assert ok is False
    assert 'Missing' in msg


def test_validate_auth_failure_blocks(monkeypatch):
    class _FakeSvc:
        def __init__(self, **kw):
            pass
        def authenticate(self):
            return False
    monkeypatch.setattr('bullhorn_service.BullhornService', _FakeSvc)
    ok, msg = validate_bullhorn_credentials('a', 'b', 'c', 'd')
    assert ok is False
    assert 'authentication failed' in msg.lower()


def test_validate_test_fetch_failure_blocks(monkeypatch):
    class _FakeSvc:
        def __init__(self, **kw):
            pass
        def authenticate(self):
            return True
        def get_tearsheets(self):
            raise RuntimeError('no api access')
    monkeypatch.setattr('bullhorn_service.BullhornService', _FakeSvc)
    ok, msg = validate_bullhorn_credentials('a', 'b', 'c', 'd')
    assert ok is False
    assert 'test data fetch failed' in msg.lower()


def test_validate_success(monkeypatch):
    class _FakeSvc:
        def __init__(self, **kw):
            pass
        def authenticate(self):
            return True
        def get_tearsheets(self):
            return []
    monkeypatch.setattr('bullhorn_service.BullhornService', _FakeSvc)
    ok, msg = validate_bullhorn_credentials('a', 'b', 'c', 'd')
    assert ok is True


# ── Provisioning happy path ──────────────────────────────────────────────────

def test_provision_happy_path(db_session):
    _seed_default_env(db_session)
    result = provision_tenant(validate=False, **_GOOD_PAYLOAD)

    env = result['environment']
    brand = result['brand']
    user = result['user']

    assert env.id is not None
    assert env.is_default is False                 # invariant: never default
    assert env.is_active is True
    assert env.resolve_credentials() is not None   # inline creds populated
    assert env.get_screening_profile() == 'standard'

    assert brand.environment_id == env.id
    assert brand.is_default is False               # invariant: never default
    assert 'apply.acme.com' in brand.get_domains()
    assert brand.to_email == 'apply@acme.com'

    assert user.environment_id == env.id
    assert user.is_admin is False
    assert user.is_company_admin is True
    assert user.check_password('pw')
    assert set(user.get_modules()) == {'scout_inbound', 'scout_screening'}


def test_provision_blocks_on_invalid_credentials_no_writes(db_session, monkeypatch):
    _seed_default_env(db_session)
    from models import BullhornEnvironment, Brand, User

    class _FakeSvc:
        def __init__(self, **kw):
            pass
        def authenticate(self):
            return False
    monkeypatch.setattr('bullhorn_service.BullhornService', _FakeSvc)

    with pytest.raises(ProvisioningError):
        provision_tenant(validate=True, **_GOOD_PAYLOAD)

    # Zero writes: only the seeded default env remains, no new brand/user.
    assert BullhornEnvironment.query.count() == 1
    assert Brand.query.count() == 0
    assert User.query.count() == 0


def test_provision_requires_admin_fields(db_session):
    _seed_default_env(db_session)
    payload = dict(_GOOD_PAYLOAD)
    payload['admin'] = {'username': '', 'email': '', 'password': ''}
    with pytest.raises(ProvisioningError):
        provision_tenant(validate=False, **payload)


def test_provision_rejects_duplicate_username(db_session):
    _seed_default_env(db_session)
    provision_tenant(validate=False, **_GOOD_PAYLOAD)
    dup = dict(_GOOD_PAYLOAD)
    dup['admin'] = {'username': 'acme_admin', 'email': 'other@acme.com', 'password': 'pw'}
    with pytest.raises(ProvisioningError):
        provision_tenant(validate=False, **dup)


def test_provision_rejects_duplicate_to_email(db_session):
    """Inbound routing keys on Brand.to_email; a second active brand reusing the
    same apply inbox would make recipient->environment resolution ambiguous, so
    provisioning must reject it (no partial writes)."""
    from models import BullhornEnvironment, Brand, User
    _seed_default_env(db_session)
    provision_tenant(validate=False, **_GOOD_PAYLOAD)

    env_before = BullhornEnvironment.query.count()
    dup = dict(_GOOD_PAYLOAD)
    dup['display_name'] = 'Acme Two'
    dup['admin'] = {'username': 'acme_admin2', 'email': 'admin2@acme.com', 'password': 'pw'}
    # same to_email (case-insensitive) as the first tenant
    dup['brand'] = dict(_GOOD_PAYLOAD['brand'], to_email='APPLY@acme.com')

    with pytest.raises(ProvisioningError):
        provision_tenant(validate=False, **dup)

    # No partial write: env/brand/user counts unchanged from the first tenant.
    assert BullhornEnvironment.query.count() == env_before
    assert User.query.filter_by(username='acme_admin2').first() is None
    assert Brand.query.filter_by(display_name='Acme Two').first() is None


def test_provision_invalid_modules_filtered(db_session):
    _seed_default_env(db_session)
    payload = dict(_GOOD_PAYLOAD)
    payload['modules'] = ['scout_inbound', 'not_a_module']
    result = provision_tenant(validate=False, **payload)
    assert result['user'].get_modules() == ['scout_inbound']


def test_provision_default_brand_untouched(db_session):
    """Provisioning must never flip an existing default brand/env."""
    _seed_default_env(db_session)
    from models import Brand, BullhornEnvironment
    default_env = BullhornEnvironment.get_default()
    default_brand = Brand(
        environment_id=default_env.id, key='myticas', display_name='Myticas',
        is_default=True, is_active=True,
    )
    db_session.add(default_brand)
    db_session.commit()

    provision_tenant(validate=False, **_GOOD_PAYLOAD)

    assert BullhornEnvironment.get_default().key == 'myticas'
    assert Brand.get_default().key == 'myticas'


# ── Readiness check ──────────────────────────────────────────────────────────

def test_readiness_check_all_green(db_session):
    _seed_default_env(db_session)
    result = provision_tenant(validate=False, **_GOOD_PAYLOAD)
    checks = run_readiness_check(result['environment'].id)
    assert checks  # non-empty
    assert all(c['ok'] for c in checks), checks
