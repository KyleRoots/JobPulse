"""Parity + isolation tests for the multi-tenant backbone (Task #100 Steps 4-7).

The hard invariant for the whole multi-tenant increment is that, with the single
existing Bullhorn environment and its two seeded brands (Myticas + STSI), every
current behavior stays byte-for-byte unchanged. These tests pin that invariant
at the seams that were refactored:

  * Brand host resolution (apply-form identity)
  * _resolve_branding() — both the brand-backed path and the hardcoded fallback
  * Inbound recipient -> environment resolution
  * for_each_active_environment() — single-env runs the callback exactly once

and add the isolation assertion that two environments with overlapping Bullhorn
ids resolve to distinct environments.
"""
import json

import pytest

from models import BullhornEnvironment, Brand


# The canonical historical values both brands must reproduce. These mirror the
# pre-refactor hardcoded mapping; if any drift, apply-form parity is broken.
_MYTICAS = {
    'apply_template': 'apply.html',
    'logo_path': 'static/myticas-logo.png',
    'logo_filename': 'myticas-logo.png',
    'logo_cid': 'myticas_logo',
    'company_name': 'Myticas Consulting',
    'logo_alt_text': 'Myticas Consulting',
    'from_email': 'info@myticas.com',
    'to_email': 'apply@myticas.com',
}
_STSI = {
    'apply_template': 'apply_stsi.html',
    'logo_path': 'static/stsi-logo.png',
    'logo_filename': 'stsi-logo.png',
    'logo_cid': 'stsi_logo',
    'company_name': 'STSI (Staffing Technical Services Inc.)',
    'logo_alt_text': 'STSI Group',
    'from_email': 'info@myticas.com',
    'to_email': 'apply@myticas.com',
}


@pytest.fixture(autouse=True)
def _clean_env_tables(db_session):
    """Guarantee an empty environment/brand slate around each test.

    The shared harness rolls back the scoped session, but these multi-tenant
    tables carry partial-unique 'single default' indexes, so any leaked row
    from a sibling test would break inserts here. Purge (children first) before
    and after each test for deterministic isolation.
    """
    from app import db

    def _purge():
        Brand.query.delete()
        BullhornEnvironment.query.delete()
        db.session.commit()

    _purge()
    yield
    db.session.rollback()
    _purge()


def _make_default_env(db_session, key='myticas'):
    env = BullhornEnvironment(
        key=key, display_name='Myticas', is_default=True, is_active=True
    )
    db_session.add(env)
    db_session.flush()
    return env


def _make_brand(db_session, env, spec, key, is_default, domains):
    brand = Brand(
        environment_id=env.id,
        key=key,
        display_name=key.upper(),
        apply_template=spec['apply_template'],
        logo_path=spec['logo_path'],
        logo_filename=spec['logo_filename'],
        logo_cid=spec['logo_cid'],
        company_name=spec['company_name'],
        logo_alt_text=spec['logo_alt_text'],
        from_email=spec['from_email'],
        to_email=spec['to_email'],
        is_default=is_default,
        is_active=True,
    )
    brand.set_domains(domains)
    db_session.add(brand)
    db_session.flush()
    return brand


@pytest.fixture
def seeded_brands(db_session):
    """Seed the default env with Myticas (default) + STSI brands."""
    env = _make_default_env(db_session)
    myticas = _make_brand(db_session, env, _MYTICAS, 'myticas', True, [])
    stsi = _make_brand(db_session, env, _STSI, 'stsi', False, ['stsigroup'])
    return env, myticas, stsi


# ── Brand host resolution parity ─────────────────────────────────────────────

class TestBrandHostResolution:
    def test_myticas_host_resolves_myticas(self, seeded_brands):
        env, myticas, stsi = seeded_brands
        brand = Brand.resolve_for_host('apply.myticas.com')
        assert brand.key == 'myticas'
        assert brand.apply_template == _MYTICAS['apply_template']
        assert brand.logo_cid == _MYTICAS['logo_cid']
        assert brand.company_name == _MYTICAS['company_name']

    def test_stsi_host_resolves_stsi(self, seeded_brands):
        brand = Brand.resolve_for_host('apply.stsigroup.com')
        assert brand.key == 'stsi'
        assert brand.apply_template == _STSI['apply_template']
        assert brand.logo_cid == _STSI['logo_cid']
        assert brand.company_name == _STSI['company_name']

    def test_unknown_host_falls_back_to_default(self, seeded_brands):
        brand = Brand.resolve_for_host('careers.somethingelse.com')
        assert brand.key == 'myticas'
        assert brand.is_default is True

    def test_empty_host_falls_back_to_default(self, seeded_brands):
        brand = Brand.resolve_for_host('')
        assert brand.key == 'myticas'


# ── _resolve_branding parity (service-level, both paths) ──────────────────────

class TestResolveBranding:
    def _svc(self):
        from job_application_service import JobApplicationService
        return JobApplicationService()

    def test_brand_backed_myticas(self, seeded_brands):
        b = self._svc()._resolve_branding('apply.myticas.com')
        assert b['template'] == _MYTICAS['apply_template']
        assert b['logo_cid'] == _MYTICAS['logo_cid']
        assert b['company_name'] == _MYTICAS['company_name']
        assert b['from_email'] == _MYTICAS['from_email']
        assert b['to_email'] == _MYTICAS['to_email']

    def test_brand_backed_stsi(self, seeded_brands):
        b = self._svc()._resolve_branding('apply.stsigroup.com')
        assert b['template'] == _STSI['apply_template']
        assert b['logo_cid'] == _STSI['logo_cid']
        assert b['company_name'] == _STSI['company_name']

    def test_hardcoded_fallback_when_no_brands(self, db_session):
        """No brands seeded -> historical hardcoded mapping, exact parity."""
        svc = self._svc()
        myticas = svc._resolve_branding('apply.myticas.com')
        assert myticas['template'] == _MYTICAS['apply_template']
        assert myticas['logo_path'] == _MYTICAS['logo_path']
        assert myticas['logo_filename'] == _MYTICAS['logo_filename']
        assert myticas['logo_cid'] == _MYTICAS['logo_cid']
        assert myticas['company_name'] == _MYTICAS['company_name']
        assert myticas['logo_alt_text'] == _MYTICAS['logo_alt_text']

        stsi = svc._resolve_branding('apply.stsigroup.com')
        assert stsi['template'] == _STSI['apply_template']
        assert stsi['logo_path'] == _STSI['logo_path']
        assert stsi['logo_filename'] == _STSI['logo_filename']
        assert stsi['logo_cid'] == _STSI['logo_cid']
        assert stsi['company_name'] == _STSI['company_name']
        assert stsi['logo_alt_text'] == _STSI['logo_alt_text']

    def test_email_html_uses_branding(self, seeded_brands):
        svc = self._svc()
        stsi_brand = svc._resolve_branding('apply.stsigroup.com')
        data = {'jobTitle': 'Software+Engineer', 'jobId': '123', 'firstName': 'A',
                'lastName': 'B', 'email': 'a@b.com', 'phone': '123'}
        html = svc._build_application_email_html(data, stsi_brand)
        assert 'cid:stsi_logo' in html
        assert 'STSI (Staffing Technical Services Inc.)' in html


# ── Inbound recipient -> environment resolution ──────────────────────────────

class TestInboundEnvironmentResolution:
    def test_known_recipient_resolves_env(self, seeded_brands):
        env, myticas, stsi = seeded_brands
        resolved = Brand.resolve_environment_id_for_recipient('apply@myticas.com')
        assert resolved == env.id

    def test_display_name_wrapped_recipient(self, seeded_brands):
        env, _, _ = seeded_brands
        resolved = Brand.resolve_environment_id_for_recipient(
            'Apply Inbox <apply@myticas.com>'
        )
        assert resolved == env.id

    def test_unknown_recipient_falls_back_to_default_env(self, seeded_brands):
        env, _, _ = seeded_brands
        resolved = Brand.resolve_environment_id_for_recipient('nobody@elsewhere.com')
        assert resolved == env.id

    def test_no_env_returns_none(self, db_session):
        # No environment seeded at all -> None (caller leaves NULL, backfill later)
        assert Brand.resolve_environment_id_for_recipient('apply@myticas.com') is None


# ── for_each_active_environment single-env / isolation ───────────────────────

class TestEnvironmentRunner:
    def test_no_envs_runs_once_with_none(self, db_session):
        from utils.environment_runner import for_each_active_environment
        seen = []
        results = for_each_active_environment('t', lambda env: seen.append(env) or 'ok')
        assert seen == [None]
        assert results == [(None, 'ok')]

    def test_single_env_runs_once(self, seeded_brands):
        from utils.environment_runner import for_each_active_environment
        env, _, _ = seeded_brands
        seen = []
        for_each_active_environment('t', lambda e: seen.append(e.key))
        assert seen == ['myticas']

    def test_per_env_error_isolation(self, db_session):
        from utils.environment_runner import for_each_active_environment
        env_a = BullhornEnvironment(key='a', display_name='A', is_default=True, is_active=True)
        env_b = BullhornEnvironment(key='b', display_name='B', is_default=False, is_active=True)
        db_session.add_all([env_a, env_b])
        db_session.flush()

        def fn(env):
            if env.key == 'a':
                raise RuntimeError('boom')
            return 'ok'

        results = for_each_active_environment('t', fn)
        by_key = {(e.key if e else None): r for e, r in results}
        # 'a' raised -> None result, 'b' still ran -> 'ok' (isolation)
        assert by_key['a'] is None
        assert by_key['b'] == 'ok'

    def test_two_envs_distinct_resolution(self, db_session):
        """Two environments with the SAME Bullhorn-side ids resolve distinctly."""
        env_a = BullhornEnvironment(key='a', display_name='A', is_default=True, is_active=True)
        env_b = BullhornEnvironment(key='b', display_name='B', is_default=False, is_active=True)
        db_session.add_all([env_a, env_b])
        db_session.flush()
        # Distinct to_email per brand -> distinct env resolution
        _make_brand(db_session, env_a, dict(_MYTICAS, to_email='apply@a.com'),
                    'brand_a', True, ['hosta'])
        _make_brand(db_session, env_b, dict(_MYTICAS, to_email='apply@b.com'),
                    'brand_b', False, ['hostb'])
        assert Brand.resolve_environment_id_for_recipient('apply@a.com') == env_a.id
        assert Brand.resolve_environment_id_for_recipient('apply@b.com') == env_b.id
        assert env_a.id != env_b.id


# ── scope_query isolation (T005/T006) ────────────────────────────────────────

class TestScopeQueryIsolation:
    """scope_query no-ops at <=1 active env (byte-for-byte parity) and filters by
    the current environment once a second environment is active — proving the
    screening/inbound view queries cannot leak across tenants with overlapping
    Bullhorn ids."""

    def _two_envs_with_overlapping_matches(self, db_session):
        from models import CandidateVettingLog, CandidateJobMatch
        env_a = BullhornEnvironment(key='a', display_name='A', is_default=True, is_active=True)
        env_b = BullhornEnvironment(key='b', display_name='B', is_default=False, is_active=True)
        db_session.add_all([env_a, env_b])
        db_session.flush()
        # SAME bullhorn_job_id / candidate id in both tenants (overlap).
        for env in (env_a, env_b):
            log = CandidateVettingLog(
                bullhorn_candidate_id=999, applied_job_id=555,
                status='completed', environment_id=env.id,
            )
            db_session.add(log)
            db_session.flush()
            db_session.add(CandidateJobMatch(
                vetting_log_id=log.id, bullhorn_job_id=555,
                match_score=90, environment_id=env.id,
            ))
        db_session.flush()
        return env_a, env_b

    def test_single_env_noop_returns_all(self, db_session, monkeypatch):
        from models import CandidateJobMatch
        from utils import environment_context as ec
        env_a = BullhornEnvironment(key='a', display_name='A', is_default=True, is_active=True)
        db_session.add(env_a)
        db_session.flush()
        # One active env -> scope_query must NOT filter (parity).
        q = scope_query_call(CandidateJobMatch.query, CandidateJobMatch)
        assert q is CandidateJobMatch.query or str(q) == str(CandidateJobMatch.query)

    def test_two_envs_filter_to_current(self, db_session, monkeypatch):
        from models import CandidateJobMatch
        from utils import environment_context as ec
        env_a, env_b = self._two_envs_with_overlapping_matches(db_session)

        # Force "two active envs", non-super-admin, current env = env_b.
        monkeypatch.setattr(ec, '_active_environment_count', lambda: 2)
        monkeypatch.setattr(ec, '_is_super_admin', lambda: False)
        monkeypatch.setattr(ec, 'current_environment', lambda: env_b)

        scoped = ec.scope_query(CandidateJobMatch.query, CandidateJobMatch).all()
        assert scoped, 'expected env_b match'
        assert all(m.environment_id == env_b.id for m in scoped)

    def test_super_admin_sees_all_envs(self, db_session, monkeypatch):
        from models import CandidateJobMatch
        from utils import environment_context as ec
        env_a, env_b = self._two_envs_with_overlapping_matches(db_session)

        monkeypatch.setattr(ec, '_active_environment_count', lambda: 2)
        monkeypatch.setattr(ec, '_is_super_admin', lambda: True)  # roll-up
        monkeypatch.setattr(ec, 'current_environment', lambda: env_b)

        rolled = ec.scope_query(CandidateJobMatch.query, CandidateJobMatch).all()
        env_ids = {m.environment_id for m in rolled}
        assert env_ids == {env_a.id, env_b.id}


def scope_query_call(query, model):
    from utils.environment_context import scope_query
    return scope_query(query, model)


# ── env-scoped job-threshold lookup (config service) ─────────────────────────

class TestJobThresholdEnvScoping:
    """VettingConfigService resolves JobVettingRequirements by its OWN
    environment once >1 env is active, so overlapping bullhorn_job_id values
    can't read another tenant's threshold. No-ops at <=1 env (parity)."""

    def _two_envs_with_overlapping_reqs(self, db_session):
        from models import JobVettingRequirements
        env_a = BullhornEnvironment(key='a', display_name='A', is_default=True, is_active=True)
        env_b = BullhornEnvironment(key='b', display_name='B', is_default=False, is_active=True)
        db_session.add_all([env_a, env_b])
        db_session.flush()
        # SAME bullhorn_job_id, DIFFERENT per-tenant threshold.
        db_session.add(JobVettingRequirements(
            bullhorn_job_id=777, vetting_threshold=60, environment_id=env_a.id))
        db_session.add(JobVettingRequirements(
            bullhorn_job_id=777, vetting_threshold=95, environment_id=env_b.id))
        db_session.flush()
        return env_a, env_b

    def _svc(self, environment_id=None):
        """Build a config-service instance without the heavy __init__ side
        effects (OpenAI client, email service) — we only exercise the
        JobVettingRequirements lookup path."""
        from candidate_vetting_service.config import VettingConfigMixin
        svc = VettingConfigMixin.__new__(VettingConfigMixin)
        svc.environment_id = environment_id
        return svc

    def test_threshold_scoped_to_service_env(self, db_session):
        env_a, env_b = self._two_envs_with_overlapping_reqs(db_session)

        svc_a = self._svc(env_a.id)
        svc_b = self._svc(env_b.id)

        assert svc_a.get_job_threshold(777) == 60.0
        assert svc_b.get_job_threshold(777) == 95.0

    def test_single_env_threshold_unscoped(self, db_session):
        """One active env -> lookup is NOT env-filtered (legacy NULL rows still
        resolve), preserving single-tenant parity."""
        from models import JobVettingRequirements
        env = BullhornEnvironment(key='only', display_name='Only', is_default=True, is_active=True)
        db_session.add(env)
        db_session.flush()
        # Legacy row with NULL environment_id must still be found.
        db_session.add(JobVettingRequirements(
            bullhorn_job_id=888, vetting_threshold=72, environment_id=None))
        db_session.flush()

        svc = self._svc()
        assert svc.get_job_threshold(888) == 72.0
