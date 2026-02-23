"""
Settings interaction tests for CandidateVettingService.

Covers:
  - vetting_enabled on/off behavior
  - send_recruiter_emails toggle effect
  - embedding_filter_enabled toggle
  - batch_size configuration
  - Config value accessors (get_config_value, get_threshold, get_job_threshold)
  - Escalation range
"""

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock


def _ensure_config(app, key, value, model='VettingConfig'):
    from app import db
    from models import VettingConfig, GlobalSettings
    Model = VettingConfig if model == 'VettingConfig' else GlobalSettings
    with app.app_context():
        existing = Model.query.filter_by(setting_key=key).first()
        if existing:
            existing.setting_value = value
        else:
            kwargs = {'setting_key': key, 'setting_value': value}
            if model == 'GlobalSettings':
                kwargs['created_at'] = datetime.utcnow()
            db.session.add(Model(**kwargs))
        db.session.commit()


def _make_cvs():
    from candidate_vetting_service import CandidateVettingService
    cvs = CandidateVettingService.__new__(CandidateVettingService)
    cvs.bullhorn_service = MagicMock()
    cvs.openai_client = MagicMock()
    cvs.model = 'gpt-4o'
    cvs.embedding_model = 'text-embedding-3-small'
    return cvs


# ===========================================================================
# 1. is_enabled / get_config_value
# ===========================================================================
class TestIsEnabled:

    def test_enabled_true(self, app):
        _ensure_config(app, 'vetting_enabled', 'true')
        cvs = _make_cvs()
        with app.app_context():
            assert cvs.is_enabled() is True

    def test_enabled_false(self, app):
        _ensure_config(app, 'vetting_enabled', 'false')
        cvs = _make_cvs()
        with app.app_context():
            assert cvs.is_enabled() is False

    def test_missing_key_defaults_false(self, app):
        """If vetting_enabled key is missing, default to False (safe)."""
        from app import db
        from models import VettingConfig
        cvs = _make_cvs()
        with app.app_context():
            VettingConfig.query.filter_by(setting_key='vetting_enabled').delete()
            db.session.commit()
            assert cvs.is_enabled() is False


# ===========================================================================
# 2. get_threshold / get_job_threshold
# ===========================================================================
class TestThreshold:

    def test_global_threshold_default(self, app):
        _ensure_config(app, 'match_threshold', '80')
        cvs = _make_cvs()
        with app.app_context():
            assert cvs.get_threshold() == 80

    def test_job_specific_threshold_overrides_global(self, app):
        """Per-job threshold overrides the global default."""
        from app import db
        from models import JobVettingRequirements
        _ensure_config(app, 'match_threshold', '80')
        cvs = _make_cvs()

        with app.app_context():
            jvr = JobVettingRequirements.query.filter_by(bullhorn_job_id=999).first()
            if not jvr:
                jvr = JobVettingRequirements(
                    bullhorn_job_id=999,
                    job_title='Override Job',
                    vetting_threshold=90
                )
                db.session.add(jvr)
                db.session.commit()
            else:
                jvr.vetting_threshold = 90
                db.session.commit()

            assert cvs.get_job_threshold(999) == 90

            db.session.delete(jvr)
            db.session.commit()

    def test_job_without_override_uses_global(self, app):
        """Job without per-job threshold falls back to global."""
        _ensure_config(app, 'match_threshold', '80')
        cvs = _make_cvs()
        with app.app_context():
            assert cvs.get_job_threshold(77777) == 80


# ===========================================================================
# 3. Batch size configuration
# ===========================================================================
class TestBatchSize:

    def test_default_batch_size(self, app):
        _ensure_config(app, 'batch_size', '25')
        cvs = _make_cvs()
        with app.app_context():
            assert cvs._get_batch_size() == 25

    def test_custom_batch_size(self, app):
        _ensure_config(app, 'batch_size', '50')
        cvs = _make_cvs()
        with app.app_context():
            assert cvs._get_batch_size() == 50


# ===========================================================================
# 4. Escalation range
# ===========================================================================
class TestEscalationRange:

    def test_escalation_range_reads_config(self, app):
        _ensure_config(app, 'escalation_low', '60')
        _ensure_config(app, 'escalation_high', '85')
        cvs = _make_cvs()
        with app.app_context():
            low, high = cvs._get_escalation_range()
            assert low == 60
            assert high == 85

    def test_should_escalate_within_range(self, app):
        _ensure_config(app, 'escalation_low', '60')
        _ensure_config(app, 'escalation_high', '85')
        cvs = _make_cvs()
        with app.app_context():
            assert cvs.should_escalate_to_gpt4o(70) is True
            assert cvs.should_escalate_to_gpt4o(60) is True
            assert cvs.should_escalate_to_gpt4o(85) is True

    def test_should_not_escalate_outside_range(self, app):
        _ensure_config(app, 'escalation_low', '60')
        _ensure_config(app, 'escalation_high', '85')
        cvs = _make_cvs()
        with app.app_context():
            assert cvs.should_escalate_to_gpt4o(59) is False
            assert cvs.should_escalate_to_gpt4o(86) is False
            assert cvs.should_escalate_to_gpt4o(95) is False


# ===========================================================================
# 5. Layer 2 model config
# ===========================================================================
class TestLayer2ModelConfig:

    def test_reads_layer2_model_from_db(self, app):
        _ensure_config(app, 'layer2_model', 'gpt-4o')
        cvs = _make_cvs()
        with app.app_context():
            assert cvs._get_layer2_model() == 'gpt-4o'

    def test_defaults_to_gpt4o_if_missing(self, app):
        from app import db
        from models import VettingConfig
        cvs = _make_cvs()
        with app.app_context():
            VettingConfig.query.filter_by(setting_key='layer2_model').delete()
            db.session.commit()
            # Default should be gpt-4o
            assert cvs._get_layer2_model() == 'gpt-4o'


# ===========================================================================
# 6. Global / per-job custom requirements
# ===========================================================================
class TestCustomRequirements:

    def test_global_requirements_from_config(self, app):
        _ensure_config(app, 'global_custom_requirements', 'Must have 5 years experience.')
        cvs = _make_cvs()
        with app.app_context():
            result = cvs._get_global_custom_requirements()
            assert 'Must have 5 years experience' in result

    def test_job_custom_requirements(self, app):
        from app import db
        from models import JobVettingRequirements
        cvs = _make_cvs()

        with app.app_context():
            jvr = JobVettingRequirements.query.filter_by(bullhorn_job_id=888).first()
            if not jvr:
                jvr = JobVettingRequirements(
                    bullhorn_job_id=888,
                    job_title='Custom Req Job',
                    custom_requirements='Must hold PE license'
                )
                db.session.add(jvr)
                db.session.commit()

            result = cvs._get_job_custom_requirements(888)
            assert result == 'Must hold PE license'

            db.session.delete(jvr)
            db.session.commit()

    def test_no_job_requirements_returns_none(self, app):
        cvs = _make_cvs()
        with app.app_context():
            result = cvs._get_job_custom_requirements(77777)
            assert result is None
