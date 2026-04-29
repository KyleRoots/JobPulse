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

Also covers (Task #42):
  TestNotQualifiedHeuristicParity         - each heuristic check_type still fires.
  TestNotQualifiedAuditParity             - VettingAuditLog row fields + summary
                                            counters for the Not-Qualified path
                                            after the _process_candidate_audit
                                            refactor (Task #11).

Also covers (Task #44):
  TestQualifiedAuditParity                - VettingAuditLog row fields + summary
                                            counters for the Qualified
                                            false-positive path
                                            (mode='qualified_false_positive'),
                                            mirroring TestNotQualifiedAuditParity.

Also covers (Task #43):
  TestRevetNewScoreBackfill                - backfill_revet_new_score populates
                                             VettingAuditLog.revet_new_score
                                             from the post-revet
                                             CandidateJobMatch.
  TestAuditSummaryEmailUsesBackfilledScore - the audit summary email re-reads
                                             the persisted revet_new_score so
                                             the back-filled value lands in
                                             the email when available.

The tests do NOT call OpenAI; the AI confirmation step is mocked.
"""

import json
from datetime import datetime
import pytest
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


# ---------------------------------------------------------------------------
# Task #42 — Parity tests for the Not-Qualified audit path after the
# _process_candidate_audit refactor in Task #11.
# ---------------------------------------------------------------------------


def _build_not_qualified_fixtures(
    app,
    *,
    bullhorn_candidate_id=900001,
    job_id=700001,
    candidate_name='Parity Candidate',
    job_title='Senior Data Engineer',
    match_score=42.0,
    technical_score=None,
    is_qualified=False,
    gaps='No experience with required Databricks platform.',
    match_summary='Solid generalist background.',
    experience_match='',
    years_analysis_json=None,
    resume_text='Senior Data Engineer\n10 years across Snowflake, dbt, Airflow.\n',
):
    """Persist a CandidateVettingLog + applied CandidateJobMatch for parity tests.

    Returns (vetting_log_id, job_match_id) so each test can re-fetch fresh
    instances inside its own app_context (avoids stale-session issues).
    """
    from app import db
    from models import CandidateVettingLog, CandidateJobMatch

    with app.app_context():
        vlog = CandidateVettingLog(
            bullhorn_candidate_id=bullhorn_candidate_id,
            candidate_name=candidate_name,
            applied_job_id=job_id,
            applied_job_title=job_title,
            resume_text=resume_text,
            status='completed',
            is_qualified=is_qualified,
            highest_match_score=match_score,
            is_sandbox=False,
            analyzed_at=datetime.utcnow(),
        )
        db.session.add(vlog)
        db.session.flush()

        match = CandidateJobMatch(
            vetting_log_id=vlog.id,
            bullhorn_job_id=job_id,
            job_title=job_title,
            match_score=match_score,
            technical_score=technical_score,
            is_qualified=is_qualified,
            is_applied_job=True,
            match_summary=match_summary,
            gaps_identified=gaps,
            experience_match=experience_match,
            years_analysis_json=years_analysis_json,
        )
        db.session.add(match)
        db.session.commit()
        return vlog.id, match.id


def _delete_audit_rows(app, vetting_log_id):
    from app import db
    from models import VettingAuditLog, CandidateJobMatch, CandidateVettingLog
    with app.app_context():
        VettingAuditLog.query.filter_by(
            candidate_vetting_log_id=vetting_log_id
        ).delete()
        CandidateJobMatch.query.filter_by(vetting_log_id=vetting_log_id).delete()
        CandidateVettingLog.query.filter_by(id=vetting_log_id).delete()
        db.session.commit()


class TestNotQualifiedHeuristicParity:
    """_run_heuristic_checks still produces each documented check_type.

    These guard against silent drift if anyone touches the heuristic
    block inside _process_candidate_audit. Each test exercises the
    exact branch that emits a given check_type.
    """

    def _svc(self):
        from vetting_audit_service import VettingAuditService
        return VettingAuditService()

    def _make_vetting_log(self, resume_text=''):
        m = MagicMock()
        m.resume_text = resume_text
        return m

    def _make_match(self, **kwargs):
        m = MagicMock()
        m.gaps_identified = kwargs.get('gaps_identified', '')
        m.match_summary = kwargs.get('match_summary', '')
        m.match_score = kwargs.get('match_score', 35)
        m.technical_score = kwargs.get('technical_score', None)
        m.is_qualified = kwargs.get('is_qualified', False)
        m.experience_match = kwargs.get('experience_match', '')
        m.years_analysis_json = kwargs.get('years_analysis_json', None)
        m.job_title = kwargs.get('job_title', 'Senior Data Engineer')
        m.bullhorn_job_id = kwargs.get('bullhorn_job_id', 12345)
        return m

    def test_recency_misfire_fires(self):
        svc = self._svc()
        vlog = self._make_vetting_log(
            resume_text='Senior Data Engineer\nLead Databricks platform team.\n'
        )
        match = self._make_match(
            gaps_identified=(
                "Career trajectory has shifted away from data engineering."
            ),
        )
        issues = svc._run_heuristic_checks(vlog, match)
        types = {i['check_type'] for i in issues}
        assert 'recency_misfire' in types

    def test_platform_age_violation_fires(self):
        svc = self._svc()
        vlog = self._make_vetting_log()
        match = self._make_match(
            years_analysis_json=json.dumps({
                'Databricks': {
                    'required_years': 15,
                    'estimated_years': 4,
                    'meets_requirement': False,
                }
            }),
        )
        issues = svc._run_heuristic_checks(vlog, match)
        types = {i['check_type'] for i in issues}
        assert 'platform_age_violation' in types

    def test_score_inconsistency_fires(self):
        svc = self._svc()
        vlog = self._make_vetting_log()
        match = self._make_match(
            match_score=30,
            match_summary='Strong technical skills and well-aligned background.',
        )
        issues = svc._run_heuristic_checks(vlog, match)
        types = {i['check_type'] for i in issues}
        assert 'score_inconsistency' in types

    def test_experience_undercounting_fires(self):
        svc = self._svc()
        vlog = self._make_vetting_log()
        match = self._make_match(
            years_analysis_json=json.dumps({
                'Snowflake': {
                    'required_years': 5,
                    'estimated_years': 7,
                    'meets_requirement': False,
                }
            }),
        )
        issues = svc._run_heuristic_checks(vlog, match)
        types = {i['check_type'] for i in issues}
        assert 'experience_undercounting' in types

    def test_employment_gap_misfire_fires(self):
        svc = self._svc()
        vlog = self._make_vetting_log(
            resume_text=(
                'Senior Data Engineer\n'
                'Acme Corp — 2020 to Present\n'
                'Currently leading the data platform team.\n'
            )
        )
        match = self._make_match(
            gaps_identified='Has a noticeable employment gap between roles.',
        )
        issues = svc._run_heuristic_checks(vlog, match)
        types = {i['check_type'] for i in issues}
        assert 'employment_gap_misfire' in types

    def test_authorization_misfire_fires(self):
        svc = self._svc()
        vlog = self._make_vetting_log()
        match = self._make_match(
            gaps_identified='Work authorization cannot be inferred from resume.',
            match_summary=(
                'Scout Screening infers strong likelihood of US authorization '
                'based on continuous US employment history.'
            ),
        )
        issues = svc._run_heuristic_checks(vlog, match)
        types = {i['check_type'] for i in issues}
        assert 'authorization_misfire' in types

    def test_clean_not_qualified_returns_no_issues(self):
        svc = self._svc()
        vlog = self._make_vetting_log(
            resume_text='Marketing Manager\n10 years of B2B SaaS marketing.\n'
        )
        match = self._make_match(
            gaps_identified='Lacks any data engineering experience.',
            match_summary='Background is in marketing, not data engineering.',
            match_score=22,
        )
        issues = svc._run_heuristic_checks(vlog, match)
        assert issues == []


class TestNotQualifiedAuditParity:
    """_process_candidate_audit(mode='not_qualified') writes the expected
    VettingAuditLog row and updates summary counters consistently with the
    pre-refactor behavior.

    AI confirmation is mocked via patch.object so no OpenAI calls are made.
    Re-vet side-effects are mocked via patch.object so no DB cascades run.
    """

    def _empty_summary(self):
        return {
            'total_audited': 0,
            'issues_found': 0,
            'revets_triggered': 0,
            'qualified_audited': 0,
            'qualified_issues_found': 0,
            'details': [],
        }

    def _run(self, app, vetting_log_id, ai_finding,
             heuristic_issues=None, revet_score=58.0):
        """Drive _process_candidate_audit end-to-end with mocked AI + re-vet.

        Returns (summary, audit_log_dict). The audit_log_dict is read out
        of the DB (not the live ORM instance) so the caller can inspect it
        outside the patched context if needed.
        """
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog, VettingAuditLog

        if heuristic_issues is None:
            heuristic_issues = [{
                'check_type': 'recency_misfire',
                'description': 'Synthetic heuristic issue (test only).'
            }]

        svc = VettingAuditService()
        summary = self._empty_summary()

        with app.app_context():
            vlog = CandidateVettingLog.query.get(vetting_log_id)
            assert vlog is not None, "fixture vetting log was not persisted"

            with patch.object(
                svc, '_run_heuristic_checks', return_value=heuristic_issues
            ), patch.object(
                svc, '_run_ai_audit', return_value=ai_finding
            ), patch.object(
                svc, '_trigger_revet', return_value=revet_score
            ) as mock_revet:
                svc._process_candidate_audit(
                    vlog, mode='not_qualified', summary=summary
                )

            audit_row = VettingAuditLog.query.filter_by(
                candidate_vetting_log_id=vetting_log_id
            ).order_by(VettingAuditLog.id.desc()).first()
            assert audit_row is not None, "audit log row was not written"

            audit_dict = {
                'finding_type': audit_row.finding_type,
                'confidence': audit_row.confidence,
                'action_taken': audit_row.action_taken,
                'revet_new_score': audit_row.revet_new_score,
                'audit_finding': audit_row.audit_finding,
                'job_id': audit_row.job_id,
                'job_title': audit_row.job_title,
                'original_score': audit_row.original_score,
                'bullhorn_candidate_id': audit_row.bullhorn_candidate_id,
                'candidate_name': audit_row.candidate_name,
            }
            return summary, audit_dict, mock_revet

    # -------------------------------------------------------------------
    # AI confirmation outcomes — finding_type x confidence matrix
    # -------------------------------------------------------------------

    @pytest.mark.parametrize('finding_type', [
        'recency_misfire',
        'platform_age_violation',
        'false_gap_claim',
        'score_inconsistency',
        'experience_undercounting',
        'employment_gap_misfire',
        'authorization_misfire',
    ])
    def test_high_confidence_triggers_revet(self, app, finding_type):
        """high + non-no_issue ⇒ action_taken='revet_triggered',
        revet_new_score persisted, summary['revets_triggered'] += 1."""
        vlog_id, _ = _build_not_qualified_fixtures(
            app, bullhorn_candidate_id=910000 + hash(finding_type) % 1000
        )
        try:
            ai = {
                'finding_type': finding_type,
                'confidence': 'high',
                'reasoning': f'High-confidence {finding_type} confirmed.',
            }
            summary, audit, mock_revet = self._run(
                app, vlog_id, ai, revet_score=63.5
            )

            assert audit['finding_type'] == finding_type
            assert audit['confidence'] == 'high'
            assert audit['action_taken'] == 'revet_triggered'
            assert audit['revet_new_score'] == 63.5
            assert audit['audit_finding'] == ai['reasoning']
            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 1
            assert summary['revets_triggered'] == 1
            assert summary['qualified_audited'] == 0
            assert summary['qualified_issues_found'] == 0
            assert mock_revet.call_count == 1
            assert len(summary['details']) == 1
            assert summary['details'][0]['mode'] == 'not_qualified'
            assert summary['details'][0]['finding_type'] == finding_type
        finally:
            _delete_audit_rows(app, vlog_id)

    @pytest.mark.parametrize('finding_type', [
        'recency_misfire',
        'platform_age_violation',
        'false_gap_claim',
        'employment_gap_misfire',
    ])
    def test_medium_confidence_flags_for_review(self, app, finding_type):
        """medium + non-no_issue ⇒ action_taken='flagged_for_review',
        no re-vet triggered, but issues_found still increments."""
        vlog_id, _ = _build_not_qualified_fixtures(
            app, bullhorn_candidate_id=920000 + hash(finding_type) % 1000
        )
        try:
            ai = {
                'finding_type': finding_type,
                'confidence': 'medium',
                'reasoning': f'Medium-confidence {finding_type} suspected.',
            }
            summary, audit, mock_revet = self._run(app, vlog_id, ai)

            assert audit['finding_type'] == finding_type
            assert audit['confidence'] == 'medium'
            assert audit['action_taken'] == 'flagged_for_review'
            assert audit['revet_new_score'] is None
            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 1
            assert summary['revets_triggered'] == 0
            assert mock_revet.call_count == 0
            assert summary['details'][0]['action_taken'] == 'flagged_for_review'
        finally:
            _delete_audit_rows(app, vlog_id)

    @pytest.mark.parametrize('finding_type', [
        'score_inconsistency',
        'experience_undercounting',
        'authorization_misfire',
    ])
    def test_low_confidence_takes_no_action(self, app, finding_type):
        """low + non-no_issue ⇒ action_taken='no_action', no re-vet,
        but issues_found still increments because the AI did identify
        an issue (parity with pre-refactor behavior)."""
        vlog_id, _ = _build_not_qualified_fixtures(
            app, bullhorn_candidate_id=930000 + hash(finding_type) % 1000
        )
        try:
            ai = {
                'finding_type': finding_type,
                'confidence': 'low',
                'reasoning': f'Low-confidence {finding_type}; not actionable.',
            }
            summary, audit, mock_revet = self._run(app, vlog_id, ai)

            assert audit['finding_type'] == finding_type
            assert audit['confidence'] == 'low'
            assert audit['action_taken'] == 'no_action'
            assert audit['revet_new_score'] is None
            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 1
            assert summary['revets_triggered'] == 0
            assert mock_revet.call_count == 0
        finally:
            _delete_audit_rows(app, vlog_id)

    def test_ai_returns_no_issue_after_heuristic_flag(self, app):
        """Heuristic flagged but AI confirmed no_issue ⇒ no_action,
        issues_found NOT incremented, no re-vet, audit log finding_type
        is 'no_issue' with whatever confidence the AI returned."""
        vlog_id, _ = _build_not_qualified_fixtures(
            app, bullhorn_candidate_id=940001
        )
        try:
            ai = {
                'finding_type': 'no_issue',
                'confidence': 'high',
                'reasoning': 'False alarm — original assessment was correct.',
            }
            summary, audit, mock_revet = self._run(app, vlog_id, ai)

            assert audit['finding_type'] == 'no_issue'
            assert audit['action_taken'] == 'no_action'
            assert audit['revet_new_score'] is None
            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 0
            assert summary['revets_triggered'] == 0
            assert mock_revet.call_count == 0
            assert summary['details'] == []
        finally:
            _delete_audit_rows(app, vlog_id)

    def test_clean_heuristic_writes_no_issue_log(self, app):
        """When _run_heuristic_checks returns [], the AI step is skipped
        and a 'clean' audit log row is written with finding_type='no_issue',
        confidence='high', action='no_action'. Summary['total_audited']
        increments by 1; nothing else changes."""
        from app import db
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog, VettingAuditLog

        vlog_id, _ = _build_not_qualified_fixtures(
            app, bullhorn_candidate_id=950001
        )
        try:
            svc = VettingAuditService()
            summary = self._empty_summary()
            with app.app_context():
                vlog = CandidateVettingLog.query.get(vlog_id)
                with patch.object(
                    svc, '_run_heuristic_checks', return_value=[]
                ), patch.object(
                    svc, '_run_ai_audit'
                ) as mock_ai, patch.object(
                    svc, '_trigger_revet'
                ) as mock_revet:
                    svc._process_candidate_audit(
                        vlog, mode='not_qualified', summary=summary
                    )
                    assert mock_ai.call_count == 0, (
                        'AI must not be called when heuristics are clean'
                    )
                    assert mock_revet.call_count == 0

                audit = VettingAuditLog.query.filter_by(
                    candidate_vetting_log_id=vlog_id
                ).order_by(VettingAuditLog.id.desc()).first()
                assert audit is not None
                assert audit.finding_type == 'no_issue'
                assert audit.confidence == 'high'
                assert audit.action_taken == 'no_action'
                assert audit.revet_new_score is None
                assert 'no issues detected' in (audit.audit_finding or '').lower()

            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 0
            assert summary['revets_triggered'] == 0
            assert summary['qualified_audited'] == 0
        finally:
            _delete_audit_rows(app, vlog_id)

    def test_no_applied_match_writes_no_issue_log(self, app):
        """When the candidate has no CandidateJobMatch rows at all,
        _process_candidate_audit writes a 'no_issue' audit log row, marks
        action_taken='no_action', and increments only total_audited."""
        from app import db
        from vetting_audit_service import VettingAuditService
        from models import (
            CandidateVettingLog, CandidateJobMatch, VettingAuditLog
        )

        with app.app_context():
            vlog = CandidateVettingLog(
                bullhorn_candidate_id=960001,
                candidate_name='Orphan Candidate',
                applied_job_id=700009,
                applied_job_title='Data Engineer',
                resume_text='Some resume text',
                status='completed',
                is_qualified=False,
                highest_match_score=20.0,
                is_sandbox=False,
                analyzed_at=datetime.utcnow(),
            )
            db.session.add(vlog)
            db.session.commit()
            vlog_id = vlog.id

        try:
            svc = VettingAuditService()
            summary = self._empty_summary()
            with app.app_context():
                vlog = CandidateVettingLog.query.get(vlog_id)
                assert CandidateJobMatch.query.filter_by(
                    vetting_log_id=vlog_id
                ).count() == 0
                with patch.object(svc, '_run_ai_audit') as mock_ai:
                    svc._process_candidate_audit(
                        vlog, mode='not_qualified', summary=summary
                    )
                    assert mock_ai.call_count == 0

                audit = VettingAuditLog.query.filter_by(
                    candidate_vetting_log_id=vlog_id
                ).order_by(VettingAuditLog.id.desc()).first()
                assert audit is not None
                assert audit.finding_type == 'no_issue'
                assert audit.action_taken == 'no_action'
                assert audit.revet_new_score is None
                assert (
                    'no job match records found'
                    in (audit.audit_finding or '').lower()
                )

            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 0
            assert summary['revets_triggered'] == 0
            assert summary['qualified_audited'] == 0
        finally:
            _delete_audit_rows(app, vlog_id)

    def test_summary_accumulates_across_multiple_candidates(self, app):
        """Three Not-Qualified candidates processed back-to-back: counters
        accumulate (1 high → re-vet, 1 medium → flag, 1 no_issue → clean).
        qualified_audited stays 0 because mode is 'not_qualified'."""
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog

        ids = []
        for i in range(3):
            vlog_id, _ = _build_not_qualified_fixtures(
                app, bullhorn_candidate_id=970000 + i
            )
            ids.append(vlog_id)

        try:
            svc = VettingAuditService()
            summary = self._empty_summary()
            ai_responses = [
                {'finding_type': 'recency_misfire', 'confidence': 'high',
                 'reasoning': 'High'},
                {'finding_type': 'platform_age_violation', 'confidence': 'medium',
                 'reasoning': 'Medium'},
                {'finding_type': 'no_issue', 'confidence': 'high',
                 'reasoning': 'Clean'},
            ]
            with app.app_context():
                for vlog_id, ai in zip(ids, ai_responses):
                    vlog = CandidateVettingLog.query.get(vlog_id)
                    with patch.object(
                        svc, '_run_heuristic_checks',
                        return_value=[{
                            'check_type': 'recency_misfire',
                            'description': 'Synthetic.'
                        }]
                    ), patch.object(
                        svc, '_run_ai_audit', return_value=ai
                    ), patch.object(
                        svc, '_trigger_revet', return_value=70.0
                    ):
                        svc._process_candidate_audit(
                            vlog, mode='not_qualified', summary=summary
                        )

            assert summary['total_audited'] == 3
            assert summary['issues_found'] == 2  # high + medium (not no_issue)
            assert summary['revets_triggered'] == 1  # only the high
            assert summary['qualified_audited'] == 0
            assert summary['qualified_issues_found'] == 0
            assert len(summary['details']) == 2
            assert all(d['mode'] == 'not_qualified' for d in summary['details'])
        finally:
            for vlog_id in ids:
                _delete_audit_rows(app, vlog_id)

    def test_audit_log_carries_match_metadata(self, app):
        """The VettingAuditLog row should mirror the applied job's metadata
        (job_id, job_title, original_score) and the candidate's identity
        fields. This catches drift if anyone reorders the kwargs in
        _process_candidate_audit."""
        vlog_id, _ = _build_not_qualified_fixtures(
            app,
            bullhorn_candidate_id=980001,
            job_id=778899,
            candidate_name='Metadata Candidate',
            job_title='Lead Data Engineer',
            match_score=46.5,
        )
        try:
            ai = {
                'finding_type': 'recency_misfire',
                'confidence': 'medium',
                'reasoning': 'Recency check confirmed at medium confidence.',
            }
            summary, audit, _ = self._run(app, vlog_id, ai)

            assert audit['bullhorn_candidate_id'] == 980001
            assert audit['candidate_name'] == 'Metadata Candidate'
            assert audit['job_id'] == 778899
            assert audit['job_title'] == 'Lead Data Engineer'
            assert audit['original_score'] == 46.5
            assert audit['audit_finding'] == ai['reasoning']
        finally:
            _delete_audit_rows(app, vlog_id)


def _build_qualified_fixtures(
    app,
    *,
    bullhorn_candidate_id=800001,
    job_id=600001,
    candidate_name='Qualified Parity Candidate',
    job_title='Senior Data Engineer',
    match_score=82.0,
    technical_score=None,
    gaps='Strong technical alignment with the role.',
    match_summary='Excellent fit with deep expertise in the required platforms.',
    experience_match='',
    years_analysis_json=None,
    resume_text='Senior Data Engineer\n10 years across Snowflake, dbt, Airflow.\n',
):
    """Persist a Qualified CandidateVettingLog + applied CandidateJobMatch
    for the qualified false-positive parity tests. Thin wrapper around
    _build_not_qualified_fixtures that flips is_qualified=True and uses a
    Qualified-range default score (>=50, the threshold inside
    _run_false_positive_checks)."""
    return _build_not_qualified_fixtures(
        app,
        bullhorn_candidate_id=bullhorn_candidate_id,
        job_id=job_id,
        candidate_name=candidate_name,
        job_title=job_title,
        match_score=match_score,
        technical_score=technical_score,
        is_qualified=True,
        gaps=gaps,
        match_summary=match_summary,
        experience_match=experience_match,
        years_analysis_json=years_analysis_json,
        resume_text=resume_text,
    )


class TestQualifiedAuditParity:
    """_process_candidate_audit(mode='qualified_false_positive') writes the
    expected VettingAuditLog row and updates summary counters consistently.

    Mirrors TestNotQualifiedAuditParity but for the Qualified false-positive
    branch. In this mode BOTH the shared counters (`total_audited`,
    `issues_found`) AND the qualified-specific counters (`qualified_audited`,
    `qualified_issues_found`) increment — see _process_candidate_audit's
    `is_qualified_audit` flag.

    AI confirmation is mocked via patch.object so no OpenAI calls are made.
    Re-vet side-effects are mocked via patch.object so no DB cascades run.
    The qualified heuristic (`_run_false_positive_checks`) is also patched
    so each test deterministically reaches the AI confirmation step.
    """

    def _empty_summary(self):
        return {
            'total_audited': 0,
            'issues_found': 0,
            'revets_triggered': 0,
            'qualified_audited': 0,
            'qualified_issues_found': 0,
            'details': [],
        }

    def _run(self, app, vetting_log_id, ai_finding,
             heuristic_issues=None, revet_score=78.0):
        """Drive _process_candidate_audit end-to-end with mocked false-positive
        heuristic + AI + re-vet. Returns (summary, audit_log_dict, mock_revet).
        """
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog, VettingAuditLog

        if heuristic_issues is None:
            heuristic_issues = [{
                'check_type': 'false_positive_skill_gap',
                'description': 'Synthetic false-positive heuristic issue (test only).'
            }]

        svc = VettingAuditService()
        summary = self._empty_summary()

        with app.app_context():
            vlog = CandidateVettingLog.query.get(vetting_log_id)
            assert vlog is not None, "fixture vetting log was not persisted"

            with patch.object(
                svc, '_run_false_positive_checks', return_value=heuristic_issues
            ), patch.object(
                svc, '_run_ai_audit', return_value=ai_finding
            ), patch.object(
                svc, '_trigger_revet', return_value=revet_score
            ) as mock_revet:
                svc._process_candidate_audit(
                    vlog, mode='qualified_false_positive', summary=summary
                )

            audit_row = VettingAuditLog.query.filter_by(
                candidate_vetting_log_id=vetting_log_id
            ).order_by(VettingAuditLog.id.desc()).first()
            assert audit_row is not None, "audit log row was not written"

            audit_dict = {
                'finding_type': audit_row.finding_type,
                'confidence': audit_row.confidence,
                'action_taken': audit_row.action_taken,
                'revet_new_score': audit_row.revet_new_score,
                'audit_finding': audit_row.audit_finding,
                'job_id': audit_row.job_id,
                'job_title': audit_row.job_title,
                'original_score': audit_row.original_score,
                'bullhorn_candidate_id': audit_row.bullhorn_candidate_id,
                'candidate_name': audit_row.candidate_name,
            }
            return summary, audit_dict, mock_revet

    # -------------------------------------------------------------------
    # AI confirmation outcomes — finding_type x confidence matrix
    # -------------------------------------------------------------------

    @pytest.mark.parametrize('finding_type', [
        'false_positive_skill_gap',
        'false_positive_experience_short',
        'false_positive_negative_summary',
        'false_positive_compliance',
    ])
    def test_high_confidence_triggers_revet(self, app, finding_type):
        """high + non-no_issue ⇒ action_taken='revet_triggered',
        revet_new_score persisted, summary['revets_triggered'] += 1.
        Both shared and qualified counters increment."""
        vlog_id, _ = _build_qualified_fixtures(
            app, bullhorn_candidate_id=810000 + hash(finding_type) % 1000
        )
        try:
            ai = {
                'finding_type': finding_type,
                'confidence': 'high',
                'reasoning': f'High-confidence {finding_type} confirmed.',
            }
            summary, audit, mock_revet = self._run(
                app, vlog_id, ai, revet_score=47.5
            )

            assert audit['finding_type'] == finding_type
            assert audit['confidence'] == 'high'
            assert audit['action_taken'] == 'revet_triggered'
            assert audit['revet_new_score'] == 47.5
            assert audit['audit_finding'] == ai['reasoning']
            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 1
            assert summary['revets_triggered'] == 1
            assert summary['qualified_audited'] == 1
            assert summary['qualified_issues_found'] == 1
            assert mock_revet.call_count == 1
            assert len(summary['details']) == 1
            assert summary['details'][0]['mode'] == 'qualified_false_positive'
            assert summary['details'][0]['finding_type'] == finding_type
        finally:
            _delete_audit_rows(app, vlog_id)

    @pytest.mark.parametrize('finding_type', [
        'false_positive_skill_gap',
        'false_positive_experience_short',
        'false_positive_negative_summary',
        'false_positive_compliance',
    ])
    def test_medium_confidence_flags_for_review(self, app, finding_type):
        """medium + non-no_issue ⇒ action_taken='flagged_for_review',
        no re-vet triggered, but issues_found and qualified_issues_found
        still increment."""
        vlog_id, _ = _build_qualified_fixtures(
            app, bullhorn_candidate_id=820000 + hash(finding_type) % 1000
        )
        try:
            ai = {
                'finding_type': finding_type,
                'confidence': 'medium',
                'reasoning': f'Medium-confidence {finding_type} suspected.',
            }
            summary, audit, mock_revet = self._run(app, vlog_id, ai)

            assert audit['finding_type'] == finding_type
            assert audit['confidence'] == 'medium'
            assert audit['action_taken'] == 'flagged_for_review'
            assert audit['revet_new_score'] is None
            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 1
            assert summary['revets_triggered'] == 0
            assert summary['qualified_audited'] == 1
            assert summary['qualified_issues_found'] == 1
            assert mock_revet.call_count == 0
            assert summary['details'][0]['action_taken'] == 'flagged_for_review'
            assert summary['details'][0]['mode'] == 'qualified_false_positive'
        finally:
            _delete_audit_rows(app, vlog_id)

    @pytest.mark.parametrize('finding_type', [
        'false_positive_skill_gap',
        'false_positive_experience_short',
        'false_positive_negative_summary',
        'false_positive_compliance',
    ])
    def test_low_confidence_takes_no_action(self, app, finding_type):
        """low + non-no_issue ⇒ action_taken='no_action', no re-vet,
        but issues_found and qualified_issues_found still increment because
        the AI did identify an issue (parity with the Not-Qualified path)."""
        vlog_id, _ = _build_qualified_fixtures(
            app, bullhorn_candidate_id=830000 + hash(finding_type) % 1000
        )
        try:
            ai = {
                'finding_type': finding_type,
                'confidence': 'low',
                'reasoning': f'Low-confidence {finding_type}; not actionable.',
            }
            summary, audit, mock_revet = self._run(app, vlog_id, ai)

            assert audit['finding_type'] == finding_type
            assert audit['confidence'] == 'low'
            assert audit['action_taken'] == 'no_action'
            assert audit['revet_new_score'] is None
            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 1
            assert summary['revets_triggered'] == 0
            assert summary['qualified_audited'] == 1
            assert summary['qualified_issues_found'] == 1
            assert mock_revet.call_count == 0
        finally:
            _delete_audit_rows(app, vlog_id)

    @pytest.mark.parametrize('confidence', ['high', 'medium', 'low'])
    def test_ai_returns_no_issue_after_heuristic_flag(self, app, confidence):
        """Heuristic flagged but AI confirmed no_issue ⇒ no_action,
        issues_found / qualified_issues_found NOT incremented, no re-vet,
        audit log finding_type is 'no_issue'. total_audited and
        qualified_audited still increment (the candidate WAS audited)."""
        vlog_id, _ = _build_qualified_fixtures(
            app, bullhorn_candidate_id=840000 + {'high': 1, 'medium': 2, 'low': 3}[confidence]
        )
        try:
            ai = {
                'finding_type': 'no_issue',
                'confidence': confidence,
                'reasoning': 'False alarm — Qualified score was correct.',
            }
            summary, audit, mock_revet = self._run(app, vlog_id, ai)

            assert audit['finding_type'] == 'no_issue'
            assert audit['confidence'] == confidence
            assert audit['action_taken'] == 'no_action'
            assert audit['revet_new_score'] is None
            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 0
            assert summary['revets_triggered'] == 0
            assert summary['qualified_audited'] == 1
            assert summary['qualified_issues_found'] == 0
            assert mock_revet.call_count == 0
            assert summary['details'] == []
        finally:
            _delete_audit_rows(app, vlog_id)

    def test_clean_heuristic_writes_no_issue_log(self, app):
        """When _run_false_positive_checks returns [], the AI step is skipped
        and a 'clean' audit log row is written with finding_type='no_issue',
        confidence='high', action='no_action'. The clean-finding text is
        the qualified-specific message. total_audited and qualified_audited
        increment by 1; nothing else changes."""
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog, VettingAuditLog

        vlog_id, _ = _build_qualified_fixtures(
            app, bullhorn_candidate_id=850001
        )
        try:
            svc = VettingAuditService()
            summary = self._empty_summary()
            with app.app_context():
                vlog = CandidateVettingLog.query.get(vlog_id)
                with patch.object(
                    svc, '_run_false_positive_checks', return_value=[]
                ), patch.object(
                    svc, '_run_ai_audit'
                ) as mock_ai, patch.object(
                    svc, '_trigger_revet'
                ) as mock_revet:
                    svc._process_candidate_audit(
                        vlog, mode='qualified_false_positive', summary=summary
                    )
                    assert mock_ai.call_count == 0, (
                        'AI must not be called when heuristics are clean'
                    )
                    assert mock_revet.call_count == 0

                audit = VettingAuditLog.query.filter_by(
                    candidate_vetting_log_id=vlog_id
                ).order_by(VettingAuditLog.id.desc()).first()
                assert audit is not None
                assert audit.finding_type == 'no_issue'
                assert audit.confidence == 'high'
                assert audit.action_taken == 'no_action'
                assert audit.revet_new_score is None
                assert 'qualified false-positive' in (audit.audit_finding or '').lower()
                assert 'no issues detected' in (audit.audit_finding or '').lower()

            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 0
            assert summary['revets_triggered'] == 0
            assert summary['qualified_audited'] == 1
            assert summary['qualified_issues_found'] == 0
        finally:
            _delete_audit_rows(app, vlog_id)

    def test_no_applied_match_writes_no_issue_log(self, app):
        """When the candidate has no CandidateJobMatch rows at all,
        _process_candidate_audit writes a 'no_issue' audit log row, marks
        action_taken='no_action', and increments total_audited AND
        qualified_audited (because mode='qualified_false_positive')."""
        from app import db
        from vetting_audit_service import VettingAuditService
        from models import (
            CandidateVettingLog, CandidateJobMatch, VettingAuditLog
        )

        with app.app_context():
            vlog = CandidateVettingLog(
                bullhorn_candidate_id=860001,
                candidate_name='Orphan Qualified Candidate',
                applied_job_id=600009,
                applied_job_title='Data Engineer',
                resume_text='Some resume text',
                status='completed',
                is_qualified=True,
                highest_match_score=88.0,
                is_sandbox=False,
                analyzed_at=datetime.utcnow(),
            )
            db.session.add(vlog)
            db.session.commit()
            vlog_id = vlog.id

        try:
            svc = VettingAuditService()
            summary = self._empty_summary()
            with app.app_context():
                vlog = CandidateVettingLog.query.get(vlog_id)
                assert CandidateJobMatch.query.filter_by(
                    vetting_log_id=vlog_id
                ).count() == 0
                with patch.object(svc, '_run_ai_audit') as mock_ai:
                    svc._process_candidate_audit(
                        vlog, mode='qualified_false_positive', summary=summary
                    )
                    assert mock_ai.call_count == 0

                audit = VettingAuditLog.query.filter_by(
                    candidate_vetting_log_id=vlog_id
                ).order_by(VettingAuditLog.id.desc()).first()
                assert audit is not None
                assert audit.finding_type == 'no_issue'
                assert audit.action_taken == 'no_action'
                assert audit.revet_new_score is None
                assert (
                    'no job match records found'
                    in (audit.audit_finding or '').lower()
                )

            assert summary['total_audited'] == 1
            assert summary['issues_found'] == 0
            assert summary['revets_triggered'] == 0
            assert summary['qualified_audited'] == 1
            assert summary['qualified_issues_found'] == 0
        finally:
            _delete_audit_rows(app, vlog_id)

    def test_summary_accumulates_across_multiple_candidates(self, app):
        """Three Qualified candidates processed back-to-back: counters
        accumulate (1 high → re-vet, 1 medium → flag, 1 no_issue → clean).
        qualified_audited == total_audited == 3 because every audit is
        a qualified one in this mode."""
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog

        ids = []
        for i in range(3):
            vlog_id, _ = _build_qualified_fixtures(
                app, bullhorn_candidate_id=870000 + i
            )
            ids.append(vlog_id)

        try:
            svc = VettingAuditService()
            summary = self._empty_summary()
            ai_responses = [
                {'finding_type': 'false_positive_skill_gap', 'confidence': 'high',
                 'reasoning': 'High'},
                {'finding_type': 'false_positive_negative_summary', 'confidence': 'medium',
                 'reasoning': 'Medium'},
                {'finding_type': 'no_issue', 'confidence': 'high',
                 'reasoning': 'Clean'},
            ]
            with app.app_context():
                for vlog_id, ai in zip(ids, ai_responses):
                    vlog = CandidateVettingLog.query.get(vlog_id)
                    with patch.object(
                        svc, '_run_false_positive_checks',
                        return_value=[{
                            'check_type': 'false_positive_skill_gap',
                            'description': 'Synthetic.'
                        }]
                    ), patch.object(
                        svc, '_run_ai_audit', return_value=ai
                    ), patch.object(
                        svc, '_trigger_revet', return_value=45.0
                    ):
                        svc._process_candidate_audit(
                            vlog, mode='qualified_false_positive', summary=summary
                        )

            assert summary['total_audited'] == 3
            assert summary['issues_found'] == 2  # high + medium (not no_issue)
            assert summary['revets_triggered'] == 1  # only the high
            assert summary['qualified_audited'] == 3
            assert summary['qualified_issues_found'] == 2
            assert len(summary['details']) == 2
            assert all(
                d['mode'] == 'qualified_false_positive'
                for d in summary['details']
            )
        finally:
            for vlog_id in ids:
                _delete_audit_rows(app, vlog_id)

    def test_audit_log_carries_match_metadata(self, app):
        """The VettingAuditLog row should mirror the applied job's metadata
        (job_id, job_title, original_score) and the candidate's identity
        fields. Mirrors the Not-Qualified parity test but for a Qualified
        candidate above the false-positive threshold."""
        vlog_id, _ = _build_qualified_fixtures(
            app,
            bullhorn_candidate_id=880001,
            job_id=668899,
            candidate_name='Qualified Metadata Candidate',
            job_title='Lead Data Engineer',
            match_score=86.5,
        )
        try:
            ai = {
                'finding_type': 'false_positive_skill_gap',
                'confidence': 'medium',
                'reasoning': 'Skill-gap concern confirmed at medium confidence.',
            }
            summary, audit, _ = self._run(app, vlog_id, ai)

            assert audit['bullhorn_candidate_id'] == 880001
            assert audit['candidate_name'] == 'Qualified Metadata Candidate'
            assert audit['job_id'] == 668899
            assert audit['job_title'] == 'Lead Data Engineer'
            assert audit['original_score'] == 86.5
            assert audit['audit_finding'] == ai['reasoning']
        finally:
            _delete_audit_rows(app, vlog_id)


# ---------------------------------------------------------------------------
# Task #43 — backfill_revet_new_score: re-vetted score lands on audit row
# ---------------------------------------------------------------------------


class TestRevetNewScoreBackfill:
    """`backfill_revet_new_score` populates `VettingAuditLog.revet_new_score`
    for every audit row that triggered a re-vet, using the freshly-scored
    `CandidateJobMatch.match_score` for the same applied job.

    The Quality Auditor writes the audit row synchronously but `_trigger_revet`
    cannot return the new score (it just clears the candidate's vetting state
    so the next vetting cycle re-scores them). This back-fill helper closes
    the loop so recruiters viewing audit history see the actual new score
    instead of the perpetual "Pending" placeholder.
    """

    def _seed_audit_row(
        self,
        app,
        *,
        candidate_id,
        job_id,
        original_score=42.0,
        revet_new_score=None,
        action_taken='revet_triggered',
        candidate_vetting_log_id=None,
    ):
        from app import db
        from models import VettingAuditLog
        with app.app_context():
            row = VettingAuditLog(
                candidate_vetting_log_id=(
                    candidate_vetting_log_id
                    if candidate_vetting_log_id is not None
                    else candidate_id * 7 + job_id
                ),
                bullhorn_candidate_id=candidate_id,
                candidate_name='Backfill Candidate',
                job_id=job_id,
                job_title='Senior Data Engineer',
                original_score=original_score,
                finding_type='recency_misfire',
                confidence='high',
                action_taken=action_taken,
                revet_new_score=revet_new_score,
                audit_finding='AI confirmed recency misfire.',
            )
            db.session.add(row)
            db.session.commit()
            return row.id

    def _seed_new_vetting_run(
        self,
        app,
        *,
        candidate_id,
        scores_by_job_id,
    ):
        """Persist a fresh CandidateVettingLog + CandidateJobMatch rows
        representing the post-revet scoring cycle. Returns the new
        vetting_log_id so callers can pass it to the helper if desired.
        """
        from app import db
        from models import CandidateVettingLog, CandidateJobMatch
        with app.app_context():
            vlog = CandidateVettingLog(
                bullhorn_candidate_id=candidate_id,
                candidate_name='Backfill Candidate',
                applied_job_id=next(iter(scores_by_job_id.keys())),
                applied_job_title='Senior Data Engineer',
                status='completed',
                is_qualified=any(s >= 75 for s in scores_by_job_id.values()),
                highest_match_score=max(scores_by_job_id.values()),
                is_sandbox=False,
                analyzed_at=datetime.utcnow(),
            )
            db.session.add(vlog)
            db.session.flush()
            for job_id, score in scores_by_job_id.items():
                db.session.add(CandidateJobMatch(
                    vetting_log_id=vlog.id,
                    bullhorn_job_id=job_id,
                    job_title='Senior Data Engineer',
                    match_score=score,
                    is_qualified=score >= 75,
                    is_applied_job=(job_id == vlog.applied_job_id),
                    match_summary='post-revet summary',
                    gaps_identified='',
                ))
            db.session.commit()
            return vlog.id

    def _cleanup(self, app, candidate_ids):
        from app import db
        from models import (
            CandidateVettingLog, CandidateJobMatch, VettingAuditLog,
        )
        with app.app_context():
            for cid in candidate_ids:
                vlog_ids = [
                    v.id for v in CandidateVettingLog.query.filter_by(
                        bullhorn_candidate_id=cid
                    ).all()
                ]
                if vlog_ids:
                    CandidateJobMatch.query.filter(
                        CandidateJobMatch.vetting_log_id.in_(vlog_ids)
                    ).delete(synchronize_session=False)
                    CandidateVettingLog.query.filter(
                        CandidateVettingLog.id.in_(vlog_ids)
                    ).delete(synchronize_session=False)
                VettingAuditLog.query.filter_by(
                    bullhorn_candidate_id=cid
                ).delete()
            db.session.commit()

    # -------------------------------------------------------------------
    # Core back-fill behavior
    # -------------------------------------------------------------------

    def test_backfill_updates_pending_audit_row_for_same_job(self, app):
        """An audit row with action_taken='revet_triggered' and
        revet_new_score=NULL gets back-filled with the new
        CandidateJobMatch.match_score for the same job_id."""
        from app import db
        from models import VettingAuditLog
        from vetting_audit_service import backfill_revet_new_score

        candidate_id = 880001
        job_id = 770001
        try:
            audit_id = self._seed_audit_row(
                app, candidate_id=candidate_id, job_id=job_id,
                original_score=42.0,
            )
            self._seed_new_vetting_run(
                app, candidate_id=candidate_id,
                scores_by_job_id={job_id: 71.5},
            )

            with app.app_context():
                count = backfill_revet_new_score(candidate_id)
                assert count == 1
                audit = VettingAuditLog.query.get(audit_id)
                assert audit.revet_new_score == 71.5
                assert audit.action_taken == 'revet_triggered'
        finally:
            self._cleanup(app, [candidate_id])

    def test_backfill_uses_provided_vetting_log(self, app):
        """When called with an explicit vetting_log argument the helper
        scores from that log's matches, even if a newer log exists."""
        from app import db
        from models import (
            VettingAuditLog, CandidateVettingLog, CandidateJobMatch,
        )
        from vetting_audit_service import backfill_revet_new_score

        candidate_id = 880002
        job_id = 770002
        try:
            audit_id = self._seed_audit_row(
                app, candidate_id=candidate_id, job_id=job_id,
            )
            target_vlog_id = self._seed_new_vetting_run(
                app, candidate_id=candidate_id,
                scores_by_job_id={job_id: 64.0},
            )

            with app.app_context():
                target_vlog = CandidateVettingLog.query.get(target_vlog_id)
                count = backfill_revet_new_score(
                    candidate_id, vetting_log=target_vlog
                )
                assert count == 1
                audit = VettingAuditLog.query.get(audit_id)
                assert audit.revet_new_score == 64.0
        finally:
            self._cleanup(app, [candidate_id])

    def test_backfill_skips_rows_for_other_jobs(self, app):
        """Back-fill only touches audit rows whose job_id has a matching
        new CandidateJobMatch — unrelated audit rows stay NULL."""
        from app import db
        from models import VettingAuditLog
        from vetting_audit_service import backfill_revet_new_score

        candidate_id = 880003
        applied_job = 770003
        unrelated_job = 770099
        try:
            applied_audit_id = self._seed_audit_row(
                app, candidate_id=candidate_id, job_id=applied_job,
                candidate_vetting_log_id=600100,
            )
            unrelated_audit_id = self._seed_audit_row(
                app, candidate_id=candidate_id, job_id=unrelated_job,
                candidate_vetting_log_id=600101,
            )
            self._seed_new_vetting_run(
                app, candidate_id=candidate_id,
                scores_by_job_id={applied_job: 80.0},
            )

            with app.app_context():
                count = backfill_revet_new_score(candidate_id)
                assert count == 1
                applied_audit = VettingAuditLog.query.get(applied_audit_id)
                unrelated_audit = VettingAuditLog.query.get(unrelated_audit_id)
                assert applied_audit.revet_new_score == 80.0
                assert unrelated_audit.revet_new_score is None
        finally:
            self._cleanup(app, [candidate_id])

    def test_backfill_ignores_already_filled_rows(self, app):
        """Rows that already carry a revet_new_score are not touched —
        the helper is idempotent and never overwrites a known score."""
        from app import db
        from models import VettingAuditLog
        from vetting_audit_service import backfill_revet_new_score

        candidate_id = 880004
        job_id = 770004
        try:
            audit_id = self._seed_audit_row(
                app, candidate_id=candidate_id, job_id=job_id,
                revet_new_score=55.0,
            )
            self._seed_new_vetting_run(
                app, candidate_id=candidate_id,
                scores_by_job_id={job_id: 90.0},
            )

            with app.app_context():
                count = backfill_revet_new_score(candidate_id)
                assert count == 0
                audit = VettingAuditLog.query.get(audit_id)
                assert audit.revet_new_score == 55.0
        finally:
            self._cleanup(app, [candidate_id])

    def test_backfill_ignores_non_revet_actions(self, app):
        """flagged_for_review / no_action audit rows are never
        back-filled, even if a same-job CandidateJobMatch exists."""
        from app import db
        from models import VettingAuditLog
        from vetting_audit_service import backfill_revet_new_score

        candidate_id = 880005
        job_id = 770005
        try:
            flagged_id = self._seed_audit_row(
                app, candidate_id=candidate_id, job_id=job_id,
                action_taken='flagged_for_review',
            )
            self._seed_new_vetting_run(
                app, candidate_id=candidate_id,
                scores_by_job_id={job_id: 88.0},
            )

            with app.app_context():
                count = backfill_revet_new_score(candidate_id)
                assert count == 0
                audit = VettingAuditLog.query.get(flagged_id)
                assert audit.revet_new_score is None
                assert audit.action_taken == 'flagged_for_review'
        finally:
            self._cleanup(app, [candidate_id])

    def test_backfill_returns_zero_when_no_pending_rows(self, app):
        """No audit rows ⇒ helper exits cleanly with 0 back-fills."""
        from vetting_audit_service import backfill_revet_new_score

        candidate_id = 880006
        try:
            self._seed_new_vetting_run(
                app, candidate_id=candidate_id,
                scores_by_job_id={770006: 73.0},
            )
            with app.app_context():
                count = backfill_revet_new_score(candidate_id)
                assert count == 0
        finally:
            self._cleanup(app, [candidate_id])

    def test_backfill_returns_zero_when_no_matches_yet(self, app):
        """An audit row exists but the candidate has no completed vetting
        log yet ⇒ helper bails out gracefully without touching the row."""
        from app import db
        from models import VettingAuditLog
        from vetting_audit_service import backfill_revet_new_score

        candidate_id = 880007
        job_id = 770007
        try:
            audit_id = self._seed_audit_row(
                app, candidate_id=candidate_id, job_id=job_id,
            )
            with app.app_context():
                count = backfill_revet_new_score(candidate_id)
                assert count == 0
                audit = VettingAuditLog.query.get(audit_id)
                assert audit.revet_new_score is None
        finally:
            self._cleanup(app, [candidate_id])

    # -------------------------------------------------------------------
    # End-to-end: the vetting service post-commit hook calls the helper.
    # -------------------------------------------------------------------

    def test_vetting_service_invokes_backfill_after_commit(self):
        """`CandidateVettingService` calls `backfill_revet_new_score` from
        its post-commit hook so the integration is wired up — not just the
        helper in isolation. Guarded as a source-text check because the
        full vetting pipeline is too heavy to drive in this test module."""
        import candidate_vetting_service as cvs_module
        import os
        pkg_dir = os.path.dirname(cvs_module.__file__)
        parts = []
        for fn in sorted(os.listdir(pkg_dir)):
            if fn.endswith('.py'):
                with open(os.path.join(pkg_dir, fn), 'r', encoding='utf-8') as f:
                    parts.append(f.read())
        src = '\n'.join(parts)
        assert 'from vetting_audit_service import backfill_revet_new_score' in src, (
            'CandidateVettingService must import backfill_revet_new_score'
        )
        assert 'backfill_revet_new_score(candidate_id' in src, (
            'CandidateVettingService must call backfill_revet_new_score '
            'after committing the new vetting log + matches'
        )


class TestAuditSummaryEmailUsesBackfilledScore:
    """`_send_audit_summary_email` re-reads `VettingAuditLog.revet_new_score`
    from the database when the in-memory summary entry has new_score=None.
    This guarantees that if the back-fill landed between the audit cycle
    and email send time, recruiters see the actual score instead of
    'Pending'."""

    def test_email_renders_backfilled_score(self, app, monkeypatch):
        from app import db
        from vetting_audit_service import VettingAuditService
        from models import VettingAuditLog, VettingConfig

        with app.app_context():
            VettingConfig.query.delete()
            db.session.add(VettingConfig(
                setting_key='admin_notification_email',
                setting_value='qa-test@example.com',
            ))
            audit = VettingAuditLog(
                candidate_vetting_log_id=850100,
                bullhorn_candidate_id=850100,
                candidate_name='Backfilled Email Candidate',
                job_id=860100,
                job_title='Senior Data Engineer',
                original_score=42.0,
                finding_type='recency_misfire',
                confidence='high',
                action_taken='revet_triggered',
                revet_new_score=78.0,
                audit_finding='AI confirmed recency misfire.',
            )
            db.session.add(audit)
            db.session.commit()
            audit_id = audit.id

            captured = {}

            class StubEmailService:
                def __init__(self, *a, **kw):
                    pass

                def send_email(self, **kwargs):
                    captured.update(kwargs)
                    return True

            monkeypatch.setattr(
                'email_service.EmailService', StubEmailService
            )

            svc = VettingAuditService()
            summary = {
                'total_audited': 1,
                'issues_found': 1,
                'revets_triggered': 1,
                'qualified_audited': 0,
                'qualified_issues_found': 0,
                'details': [{
                    'candidate_id': 850100,
                    'candidate_name': 'Backfilled Email Candidate',
                    'job_id': 860100,
                    'job_title': 'Senior Data Engineer',
                    'original_score': 42.0,
                    'finding_type': 'recency_misfire',
                    'confidence': 'high',
                    'action_taken': 'revet_triggered',
                    'new_score': None,
                    'audit_log_id': audit_id,
                    'mode': 'not_qualified',
                }],
            }

            svc._send_audit_summary_email(summary)

            assert captured, 'email was not sent'
            html = captured.get('html_content', '')
            assert '78%' in html, (
                f'Expected back-filled score 78% in email body, got: {html}'
            )
            assert 'Pending' not in html or html.count('Pending') == 0

            VettingAuditLog.query.filter_by(id=audit_id).delete()
            VettingConfig.query.delete()
            db.session.commit()

    def test_email_renders_pending_when_no_backfill(self, app, monkeypatch):
        """If neither the in-memory new_score nor the persisted column has
        a value, the email still falls back to 'Pending' so recruiters
        know the re-vet hasn't landed yet."""
        from app import db
        from vetting_audit_service import VettingAuditService
        from models import VettingAuditLog, VettingConfig

        with app.app_context():
            VettingConfig.query.delete()
            db.session.add(VettingConfig(
                setting_key='admin_notification_email',
                setting_value='qa-test@example.com',
            ))
            audit = VettingAuditLog(
                candidate_vetting_log_id=850200,
                bullhorn_candidate_id=850200,
                candidate_name='Pending Email Candidate',
                job_id=860200,
                job_title='Senior Data Engineer',
                original_score=42.0,
                finding_type='recency_misfire',
                confidence='high',
                action_taken='revet_triggered',
                revet_new_score=None,
                audit_finding='AI confirmed recency misfire.',
            )
            db.session.add(audit)
            db.session.commit()
            audit_id = audit.id

            captured = {}

            class StubEmailService:
                def __init__(self, *a, **kw):
                    pass

                def send_email(self, **kwargs):
                    captured.update(kwargs)
                    return True

            monkeypatch.setattr(
                'email_service.EmailService', StubEmailService
            )

            svc = VettingAuditService()
            summary = {
                'total_audited': 1,
                'issues_found': 1,
                'revets_triggered': 1,
                'qualified_audited': 0,
                'qualified_issues_found': 0,
                'details': [{
                    'candidate_id': 850200,
                    'candidate_name': 'Pending Email Candidate',
                    'job_id': 860200,
                    'job_title': 'Senior Data Engineer',
                    'original_score': 42.0,
                    'finding_type': 'recency_misfire',
                    'confidence': 'high',
                    'action_taken': 'revet_triggered',
                    'new_score': None,
                    'audit_log_id': audit_id,
                    'mode': 'not_qualified',
                }],
            }

            svc._send_audit_summary_email(summary)

            assert captured, 'email was not sent'
            html = captured.get('html_content', '')
            assert 'Pending' in html

            VettingAuditLog.query.filter_by(id=audit_id).delete()
            VettingConfig.query.delete()
            db.session.commit()


# ---------------------------------------------------------------------------
# Task #56 — Re-vet cap + score-stability suppression.
#
# These tests cover the new `_check_revet_caps_and_stability` helper and the
# integration in `_process_candidate_audit` that prevents the auditor from
# repeatedly re-vetting the same (candidate, job) pair (which was burning
# OpenAI tokens and polluting Bullhorn notes).
# ---------------------------------------------------------------------------


class TestRevetCapAndStabilityConfig:
    """get_revet_cap_per_24h() and get_revet_score_tolerance() are
    config-driven with safe fallbacks."""

    def test_cap_default_when_unset(self, app):
        from vetting_audit_service import (
            get_revet_cap_per_24h, DEFAULT_REVET_CAP_PER_24H
        )
        with app.app_context():
            assert get_revet_cap_per_24h() == DEFAULT_REVET_CAP_PER_24H

    def test_cap_reads_config(self, app):
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_revet_cap_per_24h
        with app.app_context():
            _set_config(VettingConfig, db, 'auditor_revet_cap_per_24h', '4')
            assert get_revet_cap_per_24h() == 4

    def test_cap_clamps_below_one_to_one(self, app):
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_revet_cap_per_24h
        with app.app_context():
            _set_config(VettingConfig, db, 'auditor_revet_cap_per_24h', '0')
            assert get_revet_cap_per_24h() == 1

    def test_cap_falls_back_on_garbage(self, app):
        from app import db
        from models import VettingConfig
        from vetting_audit_service import (
            get_revet_cap_per_24h, DEFAULT_REVET_CAP_PER_24H
        )
        with app.app_context():
            _set_config(VettingConfig, db, 'auditor_revet_cap_per_24h', 'abc')
            assert get_revet_cap_per_24h() == DEFAULT_REVET_CAP_PER_24H

    def test_tolerance_default_when_unset(self, app):
        from vetting_audit_service import (
            get_revet_score_tolerance, DEFAULT_REVET_SCORE_TOLERANCE
        )
        with app.app_context():
            assert get_revet_score_tolerance() == DEFAULT_REVET_SCORE_TOLERANCE

    def test_tolerance_reads_config(self, app):
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_revet_score_tolerance
        with app.app_context():
            _set_config(VettingConfig, db, 'auditor_revet_score_tolerance', '8.5')
            assert get_revet_score_tolerance() == 8.5

    def test_tolerance_clamps_negative_to_zero(self, app):
        from app import db
        from models import VettingConfig
        from vetting_audit_service import get_revet_score_tolerance
        with app.app_context():
            _set_config(VettingConfig, db, 'auditor_revet_score_tolerance', '-3')
            assert get_revet_score_tolerance() == 0.0


class TestRevetCapAndStabilitySuppression:
    """`_check_revet_caps_and_stability` returns the right outcome for the
    cap-exceeded, score-stable, and pass-through cases — and is wired into
    `_process_candidate_audit` so suppressed re-vets never call
    `_trigger_revet`, never bump `revets_triggered`, and DO bump the new
    `revets_skipped_capped` / `revets_skipped_stable` counters."""

    def _empty_summary(self):
        return {
            'total_audited': 0,
            'issues_found': 0,
            'revets_triggered': 0,
            'revets_skipped_capped': 0,
            'revets_skipped_stable': 0,
            'qualified_audited': 0,
            'qualified_issues_found': 0,
            'details': [],
        }

    def _seed_revet_audit(
        self,
        app,
        *,
        candidate_id,
        job_id,
        original_score,
        revet_new_score,
        candidate_vetting_log_id,
        created_at=None,
    ):
        from app import db
        from models import VettingAuditLog
        with app.app_context():
            row = VettingAuditLog(
                candidate_vetting_log_id=candidate_vetting_log_id,
                bullhorn_candidate_id=candidate_id,
                candidate_name='Prior Re-vet',
                job_id=job_id,
                job_title='Senior Data Engineer',
                original_score=original_score,
                finding_type='recency_misfire',
                confidence='high',
                action_taken='revet_triggered',
                revet_new_score=revet_new_score,
                audit_finding='Prior re-vet seeded for cap test.',
            )
            if created_at is not None:
                row.created_at = created_at
            db.session.add(row)
            db.session.commit()
            return row.id

    def test_helper_returns_none_when_no_prior_revets(self, app, clean_audit_tables):
        from vetting_audit_service import VettingAuditService
        with app.app_context():
            svc = VettingAuditService()
            assert svc._check_revet_caps_and_stability(
                candidate_id=400001, job_id=500001
            ) is None

    def test_helper_caps_after_two_recent_revets(self, app, clean_audit_tables):
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog
        from app import db
        with app.app_context():
            # Need real CandidateVettingLog rows because audit log has FK-like
            # uniqueness on candidate_vetting_log_id.
            vl_ids = []
            for i in range(2):
                vl = CandidateVettingLog(
                    bullhorn_candidate_id=400002,
                    candidate_name='Cap Test',
                    applied_job_id=500002,
                    applied_job_title='Senior Data Engineer',
                    status='completed',
                    is_qualified=False,
                    highest_match_score=14.0 - i,
                    is_sandbox=False,
                    analyzed_at=datetime.utcnow(),
                )
                db.session.add(vl)
                db.session.commit()
                vl_ids.append(vl.id)

        for vl_id in vl_ids:
            self._seed_revet_audit(
                app,
                candidate_id=400002,
                job_id=500002,
                original_score=14.0,
                revet_new_score=6.0,
                candidate_vetting_log_id=vl_id,
            )

        with app.app_context():
            svc = VettingAuditService()
            outcome = svc._check_revet_caps_and_stability(
                candidate_id=400002, job_id=500002
            )
            assert outcome is not None
            action, reason = outcome
            assert action == 'revet_skipped_capped'
            assert '24h' in reason

    def test_helper_does_not_count_other_jobs(self, app, clean_audit_tables):
        """A candidate re-vetted twice on job A is still eligible for a
        first re-vet on job B (cap is per (candidate, job) pair)."""
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog
        from app import db
        with app.app_context():
            for i in range(2):
                vl = CandidateVettingLog(
                    bullhorn_candidate_id=400003,
                    candidate_name='Cross-Job Test',
                    applied_job_id=500003,
                    applied_job_title='Data Engineer',
                    status='completed',
                    is_qualified=False,
                    highest_match_score=14.0,
                    is_sandbox=False,
                    analyzed_at=datetime.utcnow(),
                )
                db.session.add(vl)
                db.session.commit()
                self._seed_revet_audit(
                    app,
                    candidate_id=400003,
                    job_id=500003,  # same job A
                    original_score=14.0,
                    revet_new_score=6.0,
                    candidate_vetting_log_id=vl.id,
                )

        with app.app_context():
            svc = VettingAuditService()
            # Different job (B) — should pass through.
            assert svc._check_revet_caps_and_stability(
                candidate_id=400003, job_id=999999
            ) is None
            # Same job A — should be capped.
            outcome = svc._check_revet_caps_and_stability(
                candidate_id=400003, job_id=500003
            )
            assert outcome is not None and outcome[0] == 'revet_skipped_capped'

    def test_helper_ignores_stale_revets_outside_24h(
        self, app, clean_audit_tables
    ):
        from datetime import timedelta
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog
        from app import db
        with app.app_context():
            for i in range(3):
                vl = CandidateVettingLog(
                    bullhorn_candidate_id=400004,
                    candidate_name='Stale Re-vet Test',
                    applied_job_id=500004,
                    applied_job_title='Data Engineer',
                    status='completed',
                    is_qualified=False,
                    highest_match_score=14.0,
                    is_sandbox=False,
                    analyzed_at=datetime.utcnow(),
                )
                db.session.add(vl)
                db.session.commit()
                self._seed_revet_audit(
                    app,
                    candidate_id=400004,
                    job_id=500004,
                    original_score=14.0,
                    revet_new_score=14.0 - i,  # large delta to avoid stable
                    candidate_vetting_log_id=vl.id,
                    created_at=datetime.utcnow() - timedelta(hours=30),
                )

        with app.app_context():
            svc = VettingAuditService()
            # All prior re-vets are >24h old, so the cap should not trip and
            # the score-stability check should also be skipped (no recent
            # prior to compare against).
            assert svc._check_revet_caps_and_stability(
                candidate_id=400004, job_id=500004
            ) is None

    def test_helper_skips_when_score_stable(self, app, clean_audit_tables):
        """A single prior re-vet that landed within ±tolerance of its own
        original_score proves the score is stable — suppress further
        re-vets even if the cap hasn't been hit yet."""
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog
        from app import db
        with app.app_context():
            vl = CandidateVettingLog(
                bullhorn_candidate_id=400005,
                candidate_name='Stable Score',
                applied_job_id=500005,
                applied_job_title='Data Engineer',
                status='completed',
                is_qualified=False,
                highest_match_score=42.0,
                is_sandbox=False,
                analyzed_at=datetime.utcnow(),
            )
            db.session.add(vl)
            db.session.commit()
            vl_id = vl.id

        # Prior re-vet moved score 42 -> 44 (delta=2, within default ±5).
        self._seed_revet_audit(
            app,
            candidate_id=400005,
            job_id=500005,
            original_score=42.0,
            revet_new_score=44.0,
            candidate_vetting_log_id=vl_id,
        )

        with app.app_context():
            svc = VettingAuditService()
            outcome = svc._check_revet_caps_and_stability(
                candidate_id=400005, job_id=500005
            )
            assert outcome is not None
            action, reason = outcome
            assert action == 'revet_skipped_stable'
            assert 'stable' in reason.lower()

    def test_helper_does_not_skip_when_score_jumped(self, app, clean_audit_tables):
        """The Beatriz Vieitos case: score swung 14% -> 6% (delta=8, outside
        ±5 tolerance). Stability check must NOT suppress; only the cap
        rule should eventually catch this."""
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog
        from app import db
        with app.app_context():
            vl = CandidateVettingLog(
                bullhorn_candidate_id=400006,
                candidate_name='Beatriz-style swing',
                applied_job_id=500006,
                applied_job_title='Data Engineer',
                status='completed',
                is_qualified=False,
                highest_match_score=6.0,
                is_sandbox=False,
                analyzed_at=datetime.utcnow(),
            )
            db.session.add(vl)
            db.session.commit()
            vl_id = vl.id

        self._seed_revet_audit(
            app,
            candidate_id=400006,
            job_id=500006,
            original_score=14.0,
            revet_new_score=6.0,  # delta=8, outside ±5 tolerance
            candidate_vetting_log_id=vl_id,
        )

        with app.app_context():
            svc = VettingAuditService()
            # Only ONE prior re-vet (cap=2 default), score is NOT stable —
            # the helper should let this re-vet proceed.
            assert svc._check_revet_caps_and_stability(
                candidate_id=400006, job_id=500006
            ) is None

    def test_process_candidate_audit_capped_does_not_call_trigger_revet(
        self, app, clean_audit_tables
    ):
        """End-to-end: with 2 prior re-vets in the last 24h, the auditor's
        decision to re-vet a 3rd time is downgraded — `_trigger_revet`
        is never called and the summary records the skip."""
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog, VettingAuditLog

        # Seed 2 prior 'revet_triggered' rows for (cand=400007, job=500007).
        with app.app_context():
            from app import db
            for i in range(2):
                vl = CandidateVettingLog(
                    bullhorn_candidate_id=400007,
                    candidate_name='Cap E2E',
                    applied_job_id=500007,
                    applied_job_title='Data Engineer',
                    status='completed',
                    is_qualified=False,
                    highest_match_score=14.0,
                    is_sandbox=False,
                    analyzed_at=datetime.utcnow(),
                )
                db.session.add(vl)
                db.session.commit()
                self._seed_revet_audit(
                    app,
                    candidate_id=400007,
                    job_id=500007,
                    original_score=14.0,
                    revet_new_score=6.0,
                    candidate_vetting_log_id=vl.id,
                )

        # Now create a brand-new vetting log + applied match and run audit.
        vlog_id, _ = _build_not_qualified_fixtures(
            app,
            bullhorn_candidate_id=400007,
            job_id=500007,
            candidate_name='Cap E2E',
            match_score=8.0,
        )

        try:
            svc = VettingAuditService()
            summary = self._empty_summary()
            with app.app_context():
                vlog = CandidateVettingLog.query.get(vlog_id)
                with patch.object(
                    svc, '_run_heuristic_checks',
                    return_value=[{
                        'check_type': 'recency_misfire',
                        'description': 'Synthetic.',
                    }]
                ), patch.object(
                    svc, '_run_ai_audit',
                    return_value={
                        'finding_type': 'recency_misfire',
                        'confidence': 'high',
                        'reasoning': 'AI confirms misfire.',
                    }
                ), patch.object(
                    svc, '_trigger_revet'
                ) as mock_revet:
                    svc._process_candidate_audit(
                        vlog, mode='not_qualified', summary=summary
                    )
                    assert mock_revet.call_count == 0, (
                        'Re-vet must be suppressed when 24h cap is hit'
                    )

                audit = VettingAuditLog.query.filter_by(
                    candidate_vetting_log_id=vlog_id
                ).order_by(VettingAuditLog.id.desc()).first()
                assert audit is not None
                assert audit.action_taken == 'revet_skipped_capped'
                assert audit.finding_type == 'recency_misfire'
                assert audit.confidence == 'high'
                assert '24h' in (audit.audit_finding or '')

            assert summary['revets_triggered'] == 0
            assert summary['revets_skipped_capped'] == 1
            assert summary['revets_skipped_stable'] == 0
            assert summary['issues_found'] == 1  # we still recorded the issue
            assert summary['details'][0]['action_taken'] == 'revet_skipped_capped'
        finally:
            _delete_audit_rows(app, vlog_id)
            with app.app_context():
                from app import db
                VettingAuditLog.query.filter_by(
                    bullhorn_candidate_id=400007
                ).delete()
                CandidateVettingLog.query.filter_by(
                    bullhorn_candidate_id=400007
                ).delete()
                db.session.commit()

    def test_process_candidate_audit_stable_does_not_call_trigger_revet(
        self, app, clean_audit_tables
    ):
        """End-to-end: with one prior re-vet that proved score-stable
        (delta within ±5), the auditor's next re-vet is suppressed."""
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog, VettingAuditLog
        from app import db

        with app.app_context():
            vl_prior = CandidateVettingLog(
                bullhorn_candidate_id=400008,
                candidate_name='Stable E2E',
                applied_job_id=500008,
                applied_job_title='Data Engineer',
                status='completed',
                is_qualified=False,
                highest_match_score=44.0,
                is_sandbox=False,
                analyzed_at=datetime.utcnow(),
            )
            db.session.add(vl_prior)
            db.session.commit()
            self._seed_revet_audit(
                app,
                candidate_id=400008,
                job_id=500008,
                original_score=42.0,
                revet_new_score=44.0,  # delta=2, stable
                candidate_vetting_log_id=vl_prior.id,
            )

        vlog_id, _ = _build_not_qualified_fixtures(
            app,
            bullhorn_candidate_id=400008,
            job_id=500008,
            candidate_name='Stable E2E',
            match_score=44.0,
        )

        try:
            svc = VettingAuditService()
            summary = self._empty_summary()
            with app.app_context():
                vlog = CandidateVettingLog.query.get(vlog_id)
                with patch.object(
                    svc, '_run_heuristic_checks',
                    return_value=[{
                        'check_type': 'recency_misfire',
                        'description': 'Synthetic.',
                    }]
                ), patch.object(
                    svc, '_run_ai_audit',
                    return_value={
                        'finding_type': 'recency_misfire',
                        'confidence': 'high',
                        'reasoning': 'AI confirms misfire.',
                    }
                ), patch.object(
                    svc, '_trigger_revet'
                ) as mock_revet:
                    svc._process_candidate_audit(
                        vlog, mode='not_qualified', summary=summary
                    )
                    assert mock_revet.call_count == 0, (
                        'Re-vet must be suppressed when prior re-vet was stable'
                    )

                audit = VettingAuditLog.query.filter_by(
                    candidate_vetting_log_id=vlog_id
                ).order_by(VettingAuditLog.id.desc()).first()
                assert audit is not None
                assert audit.action_taken == 'revet_skipped_stable'
                assert 'stable' in (audit.audit_finding or '').lower()

            assert summary['revets_triggered'] == 0
            assert summary['revets_skipped_stable'] == 1
            assert summary['revets_skipped_capped'] == 0
            assert summary['details'][0]['action_taken'] == 'revet_skipped_stable'
        finally:
            _delete_audit_rows(app, vlog_id)
            with app.app_context():
                VettingAuditLog.query.filter_by(
                    bullhorn_candidate_id=400008
                ).delete()
                CandidateVettingLog.query.filter_by(
                    bullhorn_candidate_id=400008
                ).delete()
                db.session.commit()

    def test_process_candidate_audit_proceeds_when_no_prior(
        self, app, clean_audit_tables
    ):
        """Sanity: with no prior re-vets in the last 24h, the auditor
        still triggers re-vet normally — caps must not fire spuriously."""
        from vetting_audit_service import VettingAuditService
        from models import CandidateVettingLog, VettingAuditLog

        vlog_id, _ = _build_not_qualified_fixtures(
            app,
            bullhorn_candidate_id=400009,
            job_id=500009,
            candidate_name='No Prior',
            match_score=14.0,
        )
        try:
            svc = VettingAuditService()
            summary = self._empty_summary()
            with app.app_context():
                vlog = CandidateVettingLog.query.get(vlog_id)
                with patch.object(
                    svc, '_run_heuristic_checks',
                    return_value=[{
                        'check_type': 'recency_misfire',
                        'description': 'Synthetic.',
                    }]
                ), patch.object(
                    svc, '_run_ai_audit',
                    return_value={
                        'finding_type': 'recency_misfire',
                        'confidence': 'high',
                        'reasoning': 'AI confirms misfire.',
                    }
                ), patch.object(
                    svc, '_trigger_revet', return_value=22.0
                ) as mock_revet:
                    svc._process_candidate_audit(
                        vlog, mode='not_qualified', summary=summary
                    )
                    assert mock_revet.call_count == 1

                audit = VettingAuditLog.query.filter_by(
                    candidate_vetting_log_id=vlog_id
                ).order_by(VettingAuditLog.id.desc()).first()
                assert audit is not None
                assert audit.action_taken == 'revet_triggered'

            assert summary['revets_triggered'] == 1
            assert summary['revets_skipped_capped'] == 0
            assert summary['revets_skipped_stable'] == 0
        finally:
            _delete_audit_rows(app, vlog_id)


class TestAuditCycleSummaryDiagnostics:
    """`run_audit_cycle` initializes the new skip counters and emits a
    diagnostic log line that reports them."""

    def test_summary_initializes_skip_counters(self, app, clean_audit_tables):
        """run_audit_cycle returns a summary that includes the new
        revets_skipped_capped / revets_skipped_stable keys, even when
        no candidates need auditing this cycle."""
        from vetting_audit_service import VettingAuditService

        with app.app_context():
            svc = VettingAuditService()
            # No CandidateVettingLog rows at all → cycle is a no-op but
            # must still return a properly-shaped summary.
            summary = svc.run_audit_cycle(batch_size=5)

        assert 'revets_skipped_capped' in summary
        assert 'revets_skipped_stable' in summary
        assert summary['revets_skipped_capped'] == 0
        assert summary['revets_skipped_stable'] == 0

    def test_cycle_logs_skip_counters(self, app, clean_audit_tables, caplog):
        """The diagnostic log line at the end of run_audit_cycle reports
        how many re-vets were suppressed by each rule."""
        import logging as _logging
        from vetting_audit_service import VettingAuditService

        with app.app_context():
            svc = VettingAuditService()
            with caplog.at_level(_logging.INFO):
                svc.run_audit_cycle(batch_size=5)

        joined = '\n'.join(r.getMessage() for r in caplog.records)
        assert 'cycle complete' in joined.lower()
        assert 'skipped (24h cap)' in joined
        assert 'skipped (score stable)' in joined


# ---------------------------------------------------------------------------
# #54 — per-check heuristic unit tests for _run_false_positive_checks
# ---------------------------------------------------------------------------

class TestFalsePositiveHeuristicsExtended:
    """Unit tests for _run_false_positive_checks — each check's trigger
    conditions are verified directly.

    NOTE: Renamed from TestFalsePositiveHeuristics (the original class lives
    earlier in this file, around line 258, with 5 different tests). Python
    silently overwrites duplicate class names within the same module, so the
    earlier class would be shadowed if both used the same name.

    Existing TestQualifiedAuditParity tests always patch
    _run_false_positive_checks away, so no test previously called the real
    function.  These tests call it directly with a SimpleNamespace stand-in
    for job_match so each condition boundary is pinned.  vetting_log is not
    accessed by _run_false_positive_checks and is passed as None throughout.
    """

    @staticmethod
    def _make_match(
        *,
        match_score=82.0,
        gaps='',
        match_summary='',
        years_analysis_json=None,
    ):
        from types import SimpleNamespace
        return SimpleNamespace(
            match_score=match_score,
            gaps_identified=gaps,
            match_summary=match_summary,
            years_analysis_json=years_analysis_json,
        )

    # -------------------------------------------------------------------
    # Score guard — early exit when score < 50
    # -------------------------------------------------------------------

    def test_score_below_50_returns_empty_regardless_of_gaps(self, app):
        """_run_false_positive_checks exits early for score < 50, even when
        mandatory-skill phrases appear in gaps."""
        from vetting_audit_service import VettingAuditService

        match = self._make_match(
            match_score=49.0,
            gaps='mandatory skill missing | lacks experience in core requirement',
            match_summary='limited experience with the required stack',
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        assert issues == [], (
            'Score < 50 must produce no false-positive flags regardless of gap text'
        )

    # -------------------------------------------------------------------
    # false_positive_skill_gap
    # -------------------------------------------------------------------

    def test_skill_gap_fires_with_two_mandatory_phrases(self, app):
        """Two mandatory-skill indicator phrases in gaps_identified on a
        Qualified score (≥50) triggers false_positive_skill_gap."""
        from vetting_audit_service import VettingAuditService

        match = self._make_match(
            match_score=82.0,
            gaps='mandatory skill not found | lacks experience in required area',
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        check_types = [i['check_type'] for i in issues]
        assert 'false_positive_skill_gap' in check_types, (
            'Expected false_positive_skill_gap with 2+ mandatory phrases in gaps'
        )

    def test_skill_gap_silent_when_only_one_mandatory_phrase(self, app):
        """A single mandatory-skill phrase does not reach the threshold of 2
        — false_positive_skill_gap must NOT fire."""
        from vetting_audit_service import VettingAuditService

        match = self._make_match(
            match_score=82.0,
            gaps='mandatory skill not found — otherwise a strong fit',
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        check_types = [i['check_type'] for i in issues]
        assert 'false_positive_skill_gap' not in check_types

    def test_skill_gap_silent_when_score_below_50(self, app):
        """Two mandatory phrases in gaps but score < 50 → early return;
        false_positive_skill_gap must not appear."""
        from vetting_audit_service import VettingAuditService

        match = self._make_match(
            match_score=45.0,
            gaps='mandatory skill | lacks experience with must have technology',
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        assert issues == []

    # -------------------------------------------------------------------
    # false_positive_negative_summary
    # -------------------------------------------------------------------

    def test_negative_summary_fires_at_score_70_boundary(self, app):
        """A negative qualifier in match_summary at exactly score=70 (the
        boundary) triggers false_positive_negative_summary."""
        from vetting_audit_service import VettingAuditService

        match = self._make_match(
            match_score=70.0,
            match_summary='The candidate has limited experience with the primary stack.',
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        check_types = [i['check_type'] for i in issues]
        assert 'false_positive_negative_summary' in check_types, (
            'Expected false_positive_negative_summary at score=70 with negative qualifier'
        )

    def test_negative_summary_silent_when_score_below_70(self, app):
        """Negative qualifier present but score=65 (below 70) — the check
        must NOT fire; the threshold is score ≥ 70."""
        from vetting_audit_service import VettingAuditService

        match = self._make_match(
            match_score=65.0,
            match_summary='The candidate has limited experience with the primary stack.',
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        check_types = [i['check_type'] for i in issues]
        assert 'false_positive_negative_summary' not in check_types

    def test_negative_summary_silent_when_summary_is_positive(self, app):
        """Strong positive summary at score=80 must NOT trigger
        false_positive_negative_summary."""
        from vetting_audit_service import VettingAuditService

        match = self._make_match(
            match_score=80.0,
            match_summary='Excellent technical background with deep platform expertise.',
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        check_types = [i['check_type'] for i in issues]
        assert 'false_positive_negative_summary' not in check_types

    # -------------------------------------------------------------------
    # false_positive_experience_short
    # -------------------------------------------------------------------

    def test_experience_short_fires_when_estimated_below_half_required(self, app):
        """required=5yr, estimated=1yr (< 2.5yr = half of 5), meets=True →
        false_positive_experience_short fires (AI over-credited experience)."""
        import json
        from vetting_audit_service import VettingAuditService

        years = {
            'Python': {
                'required_years': 5,
                'estimated_years': 1.0,
                'meets_requirement': True,
            }
        }
        match = self._make_match(
            match_score=78.0,
            years_analysis_json=json.dumps(years),
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        check_types = [i['check_type'] for i in issues]
        assert 'false_positive_experience_short' in check_types, (
            '1yr estimated < 2.5yr (half of 5yr required) with meets=True must fire'
        )

    def test_experience_short_silent_when_meets_is_false(self, app):
        """If meets_requirement=False the AI already penalised the gap —
        false_positive_experience_short must NOT double-flag it."""
        import json
        from vetting_audit_service import VettingAuditService

        years = {
            'Python': {
                'required_years': 5,
                'estimated_years': 1.0,
                'meets_requirement': False,
            }
        }
        match = self._make_match(
            match_score=78.0,
            years_analysis_json=json.dumps(years),
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        check_types = [i['check_type'] for i in issues]
        assert 'false_positive_experience_short' not in check_types

    def test_experience_short_silent_when_estimated_above_half(self, app):
        """required=5yr, estimated=3yr (≥ 2.5yr = half of 5) — above the 50 %
        floor, so false_positive_experience_short must NOT fire."""
        import json
        from vetting_audit_service import VettingAuditService

        years = {
            'Python': {
                'required_years': 5,
                'estimated_years': 3.0,
                'meets_requirement': True,
            }
        }
        match = self._make_match(
            match_score=78.0,
            years_analysis_json=json.dumps(years),
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        check_types = [i['check_type'] for i in issues]
        assert 'false_positive_experience_short' not in check_types

    def test_experience_short_silent_when_required_below_3(self, app):
        """required < 3yr is excluded from the check even when estimated is
        well below half — avoids spurious flags on junior/commodity skills."""
        import json
        from vetting_audit_service import VettingAuditService

        years = {
            'Git': {
                'required_years': 2,
                'estimated_years': 0.5,
                'meets_requirement': True,
            }
        }
        match = self._make_match(
            match_score=78.0,
            years_analysis_json=json.dumps(years),
        )
        issues = VettingAuditService()._run_false_positive_checks(None, match)
        check_types = [i['check_type'] for i in issues]
        assert 'false_positive_experience_short' not in check_types
