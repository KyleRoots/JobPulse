"""
Tests for the rescoped Scout Screening Quality Auditor (Task #11).

Covers three changes:
  T001 - Auditor model is loaded from VettingConfig (not hardcoded).
  T002 - PLATFORM_AGE_CEILINGS is loaded from VettingConfig (not hardcoded).
  T003 - Qualified results are sampled and audited for false positives via
         _run_false_positive_checks + _fetch_qualified_audit_sample.

Plus Task #41:
  T004 - Concurrent audit cycles cannot write duplicate VettingAuditLog rows
         for the same candidate (DB-level unique constraint + graceful
         IntegrityError handling).

The tests do NOT call OpenAI; the AI confirmation step is mocked.
"""

import json
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def clean_vetting_config(app):
    """Ensure a clean VettingConfig table before/after each test."""
    from app import db
    from models import VettingConfig
    with app.app_context():
        VettingConfig.query.delete()
        db.session.commit()
        yield
        VettingConfig.query.delete()
        db.session.commit()


def _set_config(VettingConfig, db, key, value):
    existing = VettingConfig.query.filter_by(setting_key=key).first()
    if existing:
        existing.setting_value = value
    else:
        db.session.add(VettingConfig(setting_key=key, setting_value=value))
    db.session.commit()


# ---------------------------------------------------------------------------
# T001 — quality_auditor_model is config-driven
# ---------------------------------------------------------------------------

class TestAuditorModelConfig:
    """get_auditor_model() reads from VettingConfig with safe fallback."""

    def test_default_when_unset(self, app):
        """Returns DEFAULT_AUDITOR_MODEL when no row exists."""
        from vetting_audit_service import get_auditor_model, DEFAULT_AUDITOR_MODEL
        with app.app_context():
            assert get_auditor_model() == DEFAULT_AUDITOR_MODEL

    def test_uses_config_value(self, app):
        """Returns the configured model when the row exists."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_auditor_model
        with app.app_context():
            _set_config(VettingConfig, db, 'quality_auditor_model', 'gpt-5.5-turbo')
            assert get_auditor_model() == 'gpt-5.5-turbo'

    def test_strips_whitespace(self, app):
        """Whitespace-only values fall back to default."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_auditor_model, DEFAULT_AUDITOR_MODEL
        with app.app_context():
            _set_config(VettingConfig, db, 'quality_auditor_model', '   ')
            assert get_auditor_model() == DEFAULT_AUDITOR_MODEL

    def test_empty_string_falls_back(self, app):
        """Empty string falls back to default."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_auditor_model, DEFAULT_AUDITOR_MODEL
        with app.app_context():
            _set_config(VettingConfig, db, 'quality_auditor_model', '')
            assert get_auditor_model() == DEFAULT_AUDITOR_MODEL


# ---------------------------------------------------------------------------
# T002 — platform_age_ceilings is config-driven
# ---------------------------------------------------------------------------

class TestPlatformAgeCeilingsConfig:
    """get_platform_age_ceilings() reads JSON from VettingConfig with fallback."""

    def test_default_when_unset(self, app):
        """Returns DEFAULT_PLATFORM_AGE_CEILINGS when no row exists."""
        from vetting_audit_service import (
            get_platform_age_ceilings, DEFAULT_PLATFORM_AGE_CEILINGS
        )
        with app.app_context():
            assert get_platform_age_ceilings() == DEFAULT_PLATFORM_AGE_CEILINGS

    def test_uses_config_value(self, app):
        """Returns the parsed dict when valid JSON is configured."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_platform_age_ceilings
        with app.app_context():
            payload = json.dumps({'databricks': 6.5, 'snowflake': 9.0})
            _set_config(VettingConfig, db, 'platform_age_ceilings', payload)
            result = get_platform_age_ceilings()
            assert result == {'databricks': 6.5, 'snowflake': 9.0}

    def test_lowercases_keys(self, app):
        """Keys are normalized to lowercase for case-insensitive lookups."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_platform_age_ceilings
        with app.app_context():
            payload = json.dumps({'Databricks': 7.0, 'SNOWFLAKE': 10.0})
            _set_config(VettingConfig, db, 'platform_age_ceilings', payload)
            result = get_platform_age_ceilings()
            assert 'databricks' in result and 'snowflake' in result
            assert result['databricks'] == 7.0
            assert result['snowflake'] == 10.0

    def test_malformed_json_falls_back(self, app):
        """Invalid JSON returns the in-file defaults instead of crashing."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import (
            get_platform_age_ceilings, DEFAULT_PLATFORM_AGE_CEILINGS
        )
        with app.app_context():
            _set_config(VettingConfig, db, 'platform_age_ceilings', '{not valid json')
            assert get_platform_age_ceilings() == DEFAULT_PLATFORM_AGE_CEILINGS

    def test_empty_dict_falls_back(self, app):
        """An empty JSON object falls back to defaults (operator typo guard)."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import (
            get_platform_age_ceilings, DEFAULT_PLATFORM_AGE_CEILINGS
        )
        with app.app_context():
            _set_config(VettingConfig, db, 'platform_age_ceilings', '{}')
            assert get_platform_age_ceilings() == DEFAULT_PLATFORM_AGE_CEILINGS

    def test_non_numeric_values_skipped(self, app):
        """Entries with non-numeric values are dropped, others kept."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_platform_age_ceilings
        with app.app_context():
            payload = json.dumps({
                'good': 5.0,
                'bad': 'not a number',
            })
            _set_config(VettingConfig, db, 'platform_age_ceilings', payload)
            result = get_platform_age_ceilings()
            assert result == {'good': 5.0}

    def test_out_of_range_values_skipped(self, app):
        """Numeric values outside (0, 100] are dropped to prevent DB-drift poisoning."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_platform_age_ceilings
        with app.app_context():
            payload = json.dumps({
                'ok': 7.5,
                'zero': 0,
                'negative': -3,
                'too_big': 999,
            })
            _set_config(VettingConfig, db, 'platform_age_ceilings', payload)
            result = get_platform_age_ceilings()
            assert result == {'ok': 7.5}

    def test_all_values_invalid_falls_back(self, app):
        """If every entry is invalid, fall back to defaults rather than ship empty."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import (
            get_platform_age_ceilings, DEFAULT_PLATFORM_AGE_CEILINGS
        )
        with app.app_context():
            payload = json.dumps({'a': -1, 'b': 0, 'c': 'oops'})
            _set_config(VettingConfig, db, 'platform_age_ceilings', payload)
            assert get_platform_age_ceilings() == DEFAULT_PLATFORM_AGE_CEILINGS


# ---------------------------------------------------------------------------
# T003 — Qualified false-positive sampling and heuristics
# ---------------------------------------------------------------------------

class TestQualifiedSampleRate:
    """get_qualified_sample_rate() reads percent from VettingConfig."""

    def test_default_when_unset(self, app):
        from vetting_audit_service import (
            get_qualified_sample_rate, DEFAULT_QUALIFIED_SAMPLE_RATE
        )
        with app.app_context():
            assert get_qualified_sample_rate() == DEFAULT_QUALIFIED_SAMPLE_RATE

    def test_zero_disables(self, app):
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_qualified_sample_rate
        with app.app_context():
            _set_config(VettingConfig, db, 'qualified_audit_sample_rate', '0')
            assert get_qualified_sample_rate() == 0

    def test_clamped_invalid_falls_back(self, app):
        from app import db
        from models import VettingConfig
        from vetting_audit_service import (
            get_qualified_sample_rate, DEFAULT_QUALIFIED_SAMPLE_RATE
        )
        with app.app_context():
            _set_config(VettingConfig, db, 'qualified_audit_sample_rate', 'abc')
            assert get_qualified_sample_rate() == DEFAULT_QUALIFIED_SAMPLE_RATE

    def test_out_of_range_falls_back(self, app):
        from app import db
        from models import VettingConfig
        from vetting_audit_service import (
            get_qualified_sample_rate, DEFAULT_QUALIFIED_SAMPLE_RATE
        )
        with app.app_context():
            _set_config(VettingConfig, db, 'qualified_audit_sample_rate', '150')
            assert get_qualified_sample_rate() == DEFAULT_QUALIFIED_SAMPLE_RATE


class TestFalsePositiveHeuristics:
    """_run_false_positive_checks fires for over-scored Qualified results."""

    def _make_match(self, score=85, gaps='', summary='', years_json=None):
        """Build a stand-in CandidateJobMatch object for heuristic input."""
        m = MagicMock()
        m.match_score = score
        m.gaps_identified = gaps
        m.match_summary = summary
        m.years_analysis_json = years_json
        m.job_title = 'Senior Data Engineer'
        m.bullhorn_job_id = 12345
        return m

    def test_clean_qualified_returns_no_issues(self):
        from vetting_audit_service import VettingAuditService
        svc = VettingAuditService()
        match = self._make_match(
            score=85,
            gaps='No major gaps identified.',
            summary='Strong match across required platforms and proven leadership.'
        )
        issues = svc._run_false_positive_checks(MagicMock(), match)
        assert issues == []

    def test_low_score_skipped(self):
        """Below 50% is treated as Not-Qualified territory; skip false-positive checks."""
        from vetting_audit_service import VettingAuditService
        svc = VettingAuditService()
        match = self._make_match(
            score=40,
            gaps='No experience with Databricks. Missing required Snowflake.',
            summary='Lacks core skills.'
        )
        issues = svc._run_false_positive_checks(MagicMock(), match)
        assert issues == []

    def test_multiple_mandatory_gaps_flagged(self):
        from vetting_audit_service import VettingAuditService
        svc = VettingAuditService()
        match = self._make_match(
            score=82,
            gaps=(
                'No experience with required Databricks platform. '
                'Missing required Snowflake certification. '
                'Lacks experience in dbt orchestration.'
            ),
            summary='Solid generalist background.'
        )
        issues = svc._run_false_positive_checks(MagicMock(), match)
        types = {i['check_type'] for i in issues}
        assert 'false_positive_skill_gap' in types

    def test_negative_summary_flagged(self):
        from vetting_audit_service import VettingAuditService
        svc = VettingAuditService()
        match = self._make_match(
            score=80,
            gaps='None.',
            summary='Limited experience with cloud-native pipelines; lacks demonstrated Databricks fluency.'
        )
        issues = svc._run_false_positive_checks(MagicMock(), match)
        types = {i['check_type'] for i in issues}
        assert 'false_positive_negative_summary' in types

    def test_experience_shortfall_flagged(self):
        from vetting_audit_service import VettingAuditService
        svc = VettingAuditService()
        years_payload = json.dumps({
            'Databricks': {
                'required_years': 8,
                'estimated_years': 1.5,
                'meets_requirement': True,
            }
        })
        match = self._make_match(
            score=80,
            gaps='None.',
            summary='Strong generalist.',
            years_json=years_payload
        )
        issues = svc._run_false_positive_checks(MagicMock(), match)
        types = {i['check_type'] for i in issues}
        assert 'false_positive_experience_short' in types


class TestQualifiedAuditCycleIntegration:
    """run_audit_cycle invokes Phase 2 when sample rate > 0."""

    def test_phase2_skipped_when_rate_zero(self, app):
        """Sample rate of 0 means _fetch_qualified_audit_sample is never called."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import VettingAuditService
        with app.app_context():
            _set_config(VettingConfig, db, 'qualified_audit_sample_rate', '0')
            svc = VettingAuditService()
            with patch.object(
                svc, '_fetch_qualified_audit_sample', return_value=[]
            ) as mock_fetch:
                summary = svc.run_audit_cycle(batch_size=5)
                mock_fetch.assert_not_called()
                assert summary['qualified_audited'] == 0

    def test_phase2_runs_when_rate_positive(self, app):
        """Sample rate > 0 triggers _fetch_qualified_audit_sample at least once."""
        from app import db
        from models import VettingConfig
        from vetting_audit_service import VettingAuditService
        with app.app_context():
            _set_config(VettingConfig, db, 'qualified_audit_sample_rate', '25')
            svc = VettingAuditService()
            with patch.object(
                svc, '_fetch_qualified_audit_sample', return_value=[]
            ) as mock_fetch:
                svc.run_audit_cycle(batch_size=5)
                mock_fetch.assert_called_once()
                args, kwargs = mock_fetch.call_args
                assert args[0] == 5
                assert args[1] == 25


# ---------------------------------------------------------------------------
# T004 — Task #41: concurrent-audit safeguard
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_audit_tables(app):
    """Wipe vetting log + audit log rows before/after each concurrency test."""
    from app import db
    from models import VettingAuditLog, CandidateVettingLog, CandidateJobMatch
    with app.app_context():
        CandidateJobMatch.query.delete()
        VettingAuditLog.query.delete()
        CandidateVettingLog.query.delete()
        db.session.commit()
        yield
        CandidateJobMatch.query.delete()
        VettingAuditLog.query.delete()
        CandidateVettingLog.query.delete()
        db.session.commit()


class TestConcurrentAuditSafeguard:
    """Two overlapping cycles cannot create duplicate VettingAuditLog rows.

    The Quality Auditor pre-filters already-audited candidates at the start of
    each cycle, but if two cycles overlap (manual trigger races a scheduled
    run, or a long cycle is still running when the next 15-minute tick fires)
    both can pick up the same candidate before either has committed. The
    DB-level unique constraint on `candidate_vetting_log_id` makes the
    duplicate insert impossible regardless of timing, and
    `_commit_audit_log` catches the resulting IntegrityError so the cycle
    keeps running.
    """

    def _make_vetting_log(self, db, bullhorn_candidate_id=99001):
        from models import CandidateVettingLog
        vl = CandidateVettingLog(
            bullhorn_candidate_id=bullhorn_candidate_id,
            candidate_name='Race Condition Candidate',
            applied_job_id=8888,
            applied_job_title='Senior Data Engineer',
            status='completed',
            is_qualified=False,
            highest_match_score=42.0,
            is_sandbox=False,
            analyzed_at=datetime.utcnow(),
        )
        db.session.add(vl)
        db.session.commit()
        return vl

    def test_unique_constraint_blocks_direct_duplicate_insert(
        self, app, clean_audit_tables
    ):
        """Inserting two VettingAuditLog rows for the same candidate raises IntegrityError."""
        from app import db
        from models import VettingAuditLog
        from sqlalchemy.exc import IntegrityError

        with app.app_context():
            vl = self._make_vetting_log(db)

            db.session.add(VettingAuditLog(
                candidate_vetting_log_id=vl.id,
                bullhorn_candidate_id=vl.bullhorn_candidate_id,
                candidate_name=vl.candidate_name,
                finding_type='no_issue',
                action_taken='no_action',
                audit_finding='first audit'
            ))
            db.session.commit()

            db.session.add(VettingAuditLog(
                candidate_vetting_log_id=vl.id,
                bullhorn_candidate_id=vl.bullhorn_candidate_id,
                candidate_name=vl.candidate_name,
                finding_type='no_issue',
                action_taken='no_action',
                audit_finding='second audit (race)'
            ))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            rows = VettingAuditLog.query.filter_by(
                candidate_vetting_log_id=vl.id
            ).all()
            assert len(rows) == 1

    def test_commit_audit_log_helper_returns_false_on_duplicate(
        self, app, clean_audit_tables
    ):
        """_commit_audit_log catches IntegrityError, rolls back, returns False."""
        from app import db
        from models import VettingAuditLog
        from vetting_audit_service import VettingAuditService

        with app.app_context():
            vl = self._make_vetting_log(db, bullhorn_candidate_id=99002)

            svc = VettingAuditService()

            first = VettingAuditLog(
                candidate_vetting_log_id=vl.id,
                bullhorn_candidate_id=vl.bullhorn_candidate_id,
                candidate_name=vl.candidate_name,
                finding_type='no_issue',
                action_taken='no_action',
                audit_finding='first cycle'
            )
            assert svc._commit_audit_log(first) is True

            duplicate = VettingAuditLog(
                candidate_vetting_log_id=vl.id,
                bullhorn_candidate_id=vl.bullhorn_candidate_id,
                candidate_name=vl.candidate_name,
                finding_type='no_issue',
                action_taken='no_action',
                audit_finding='second cycle (race)'
            )
            assert svc._commit_audit_log(duplicate) is False

            rows = VettingAuditLog.query.filter_by(
                candidate_vetting_log_id=vl.id
            ).all()
            assert len(rows) == 1
            assert rows[0].audit_finding == 'first cycle'

    def test_unrelated_integrity_error_is_not_swallowed(
        self, app, clean_audit_tables
    ):
        """Non-duplicate IntegrityErrors (e.g. NOT NULL violations) must
        propagate so genuine bugs aren't masked as 'already audited'."""
        from app import db
        from models import VettingAuditLog
        from vetting_audit_service import VettingAuditService
        from sqlalchemy.exc import IntegrityError

        with app.app_context():
            vl = self._make_vetting_log(db, bullhorn_candidate_id=99004)

            svc = VettingAuditService()

            bad = VettingAuditLog(
                candidate_vetting_log_id=vl.id,
                bullhorn_candidate_id=None,
                candidate_name='missing required candidate id',
                finding_type='no_issue',
                action_taken='no_action',
                audit_finding='intentional NOT NULL violation'
            )
            with pytest.raises(IntegrityError):
                svc._commit_audit_log(bad)

    def test_two_overlapping_cycles_produce_one_audit_row(
        self, app, clean_audit_tables
    ):
        """Simulate two overlapping audit cycles auditing the same candidate.

        Both cycles call _process_candidate_audit on the same vetting log
        (this is the exact race the task safeguards against). Only one
        VettingAuditLog row should result, and the second call must not
        raise.
        """
        from app import db
        from models import VettingAuditLog
        from vetting_audit_service import VettingAuditService

        with app.app_context():
            vl = self._make_vetting_log(db, bullhorn_candidate_id=99003)

            svc = VettingAuditService()
            summary_a = {
                'total_audited': 0, 'issues_found': 0, 'revets_triggered': 0,
                'qualified_audited': 0, 'qualified_issues_found': 0,
                'details': []
            }
            summary_b = {
                'total_audited': 0, 'issues_found': 0, 'revets_triggered': 0,
                'qualified_audited': 0, 'qualified_issues_found': 0,
                'details': []
            }

            svc._process_candidate_audit(vl, mode='not_qualified', summary=summary_a)
            svc._process_candidate_audit(vl, mode='not_qualified', summary=summary_b)

            rows = VettingAuditLog.query.filter_by(
                candidate_vetting_log_id=vl.id
            ).all()
            assert len(rows) == 1, (
                f"Expected exactly one audit row after two overlapping cycles, "
                f"got {len(rows)}"
            )

            assert summary_a['total_audited'] == 1
            assert summary_b['total_audited'] == 0, (
                "The losing cycle must not double-count the candidate as audited"
            )
