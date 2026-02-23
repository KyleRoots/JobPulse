"""
Tests for seed_vetting_config() idempotency and production-aware defaults.

Verifies that:
- All expected VettingConfig keys are seeded on first run
- Existing user settings are NEVER overwritten on re-seed
- Missing keys are added without affecting existing settings
- Seed defaults are environment-agnostic (no dev/prod split for toggles)
"""

import os
import pytest
from unittest.mock import patch


@pytest.fixture
def vetting_config_model(app):
    """Provide the VettingConfig model within app context."""
    from models import VettingConfig
    return VettingConfig


@pytest.fixture(autouse=True)
def clean_vetting_config(app):
    """Ensure a clean VettingConfig table before each test."""
    from app import db
    from models import VettingConfig
    with app.app_context():
        VettingConfig.query.delete()
        db.session.commit()
        yield
        # Cleanup after test
        VettingConfig.query.delete()
        db.session.commit()


# All keys that seed_vetting_config should create
EXPECTED_KEYS = {
    'vetting_enabled',
    'match_threshold',
    'batch_size',
    'admin_notification_email',
    'health_alert_email',
    'send_recruiter_emails',
    'embedding_filter_enabled',
    'embedding_similarity_threshold',
    'escalation_low',
    'escalation_high',
    'layer2_model',
    'vetting_cutoff_date',
    'global_custom_requirements',
}


class TestSeedVettingConfigFirstRun:
    """Tests for first-run seeding (empty table)."""

    def test_all_keys_created(self, app, vetting_config_model):
        """All expected VettingConfig keys are created on first run."""
        from app import db
        from seed_database import seed_vetting_config

        with app.app_context():
            seed_vetting_config(db, vetting_config_model)
            
            created_keys = {
                c.setting_key for c in vetting_config_model.query.all()
            }
            assert EXPECTED_KEYS == created_keys

    def test_dev_defaults(self, app, vetting_config_model):
        """Development environment gets conservative defaults."""
        from app import db
        from seed_database import seed_vetting_config

        with app.app_context():
            with patch('seed_database.is_production_environment', return_value=False):
                seed_vetting_config(db, vetting_config_model)

            def _val(key):
                return vetting_config_model.query.filter_by(
                    setting_key=key
                ).first().setting_value

            assert _val('vetting_enabled') == 'false'
            assert _val('send_recruiter_emails') == 'false'
            assert _val('embedding_similarity_threshold') == '0.25'
            assert _val('vetting_cutoff_date') == ''

    def test_prod_defaults_same_as_dev(self, app, vetting_config_model):
        """Production environment gets same conservative defaults (env-agnostic)."""
        from app import db
        from seed_database import seed_vetting_config

        with app.app_context():
            with patch('seed_database.is_production_environment', return_value=True):
                seed_vetting_config(db, vetting_config_model)

            def _val(key):
                return vetting_config_model.query.filter_by(
                    setting_key=key
                ).first().setting_value

            # Toggles are always conservative regardless of environment
            assert _val('vetting_enabled') == 'false'
            assert _val('send_recruiter_emails') == 'false'
            assert _val('embedding_similarity_threshold') == '0.25'
            assert _val('vetting_cutoff_date') == ''


class TestSeedVettingConfigIdempotent:
    """Tests for re-seed behavior (existing data preserved)."""

    def test_existing_values_never_overwritten(self, app, vetting_config_model):
        """User-modified settings survive a re-seed."""
        from app import db
        from seed_database import seed_vetting_config

        with app.app_context():
            # First seed (dev defaults)
            with patch('seed_database.is_production_environment', return_value=False):
                seed_vetting_config(db, vetting_config_model)

            # User changes some settings via UI
            for key, new_val in [
                ('send_recruiter_emails', 'true'),
                ('embedding_similarity_threshold', '0.55'),
                ('vetting_cutoff_date', '2026-03-01 00:00:00'),
                ('global_custom_requirements', 'Custom prompt text here'),
            ]:
                setting = vetting_config_model.query.filter_by(
                    setting_key=key
                ).first()
                setting.setting_value = new_val
            db.session.commit()

            # Re-seed (even with prod flag — should still preserve)
            with patch('seed_database.is_production_environment', return_value=True):
                seed_vetting_config(db, vetting_config_model)

            def _val(key):
                return vetting_config_model.query.filter_by(
                    setting_key=key
                ).first().setting_value

            # All user values must be preserved
            assert _val('send_recruiter_emails') == 'true'
            assert _val('embedding_similarity_threshold') == '0.55'
            assert _val('vetting_cutoff_date') == '2026-03-01 00:00:00'
            assert _val('global_custom_requirements') == 'Custom prompt text here'

    def test_missing_keys_added_without_affecting_others(self, app, vetting_config_model):
        """If a key is missing, re-seed adds it without touching existing keys."""
        from app import db
        from seed_database import seed_vetting_config

        with app.app_context():
            # First seed
            with patch('seed_database.is_production_environment', return_value=False):
                seed_vetting_config(db, vetting_config_model)

            # User modifies a setting
            setting = vetting_config_model.query.filter_by(
                setting_key='match_threshold'
            ).first()
            setting.setting_value = '90'
            db.session.commit()

            # Simulate a key being deleted (e.g., DB partial reset)
            vetting_config_model.query.filter_by(
                setting_key='escalation_low'
            ).delete()
            db.session.commit()

            # Re-seed
            with patch('seed_database.is_production_environment', return_value=False):
                seed_vetting_config(db, vetting_config_model)

            # Missing key should be recreated with default
            restored = vetting_config_model.query.filter_by(
                setting_key='escalation_low'
            ).first()
            assert restored is not None
            assert restored.setting_value == '60'

            # User-modified value must be untouched
            threshold = vetting_config_model.query.filter_by(
                setting_key='match_threshold'
            ).first()
            assert threshold.setting_value == '90'


class TestSeedVettingConfigKeyCompleteness:
    """Verify the seed dict stays in sync with what the app reads."""

    def test_global_custom_requirements_key_exists(self, app, vetting_config_model):
        """global_custom_requirements is seeded from config/global_screening_prompt.txt."""
        from app import db
        from seed_database import seed_vetting_config

        with app.app_context():
            seed_vetting_config(db, vetting_config_model)
            setting = vetting_config_model.query.filter_by(
                setting_key='global_custom_requirements'
            ).first()
            assert setting is not None
            # Prompt is loaded from config file; verify it contains the expected header
            assert 'WORK AUTHORIZATION & SECURITY CLEARANCE INFERENCE RULES' in setting.setting_value

    def test_vetting_cutoff_date_key_exists(self, app, vetting_config_model):
        """vetting_cutoff_date key is seeded so the vetting cycle respects it."""
        from app import db
        from seed_database import seed_vetting_config

        with app.app_context():
            seed_vetting_config(db, vetting_config_model)
            setting = vetting_config_model.query.filter_by(
                setting_key='vetting_cutoff_date'
            ).first()
            assert setting is not None
