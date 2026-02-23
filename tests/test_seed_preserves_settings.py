"""
Regression tests: operational settings must survive re-seeding.

Verifies the invariant that deploys never overwrite user-set values for:
  - VettingConfig: vetting_enabled, send_recruiter_emails,
    embedding_similarity_threshold, match_threshold, batch_size
  - GlobalSettings: sftp_enabled, automated_uploads_enabled

Also verifies that seed defaults are environment-agnostic (no dev/prod split
for operational toggles).
"""

import pytest
from unittest.mock import patch
from datetime import datetime


@pytest.fixture
def models(app):
    """Provide VettingConfig and GlobalSettings models."""
    from models import VettingConfig, GlobalSettings
    return VettingConfig, GlobalSettings


@pytest.fixture(autouse=True)
def clean_tables(app):
    """Ensure clean VettingConfig and GlobalSettings tables before each test."""
    from app import db
    from models import VettingConfig, GlobalSettings
    with app.app_context():
        VettingConfig.query.delete()
        GlobalSettings.query.delete()
        db.session.commit()
        yield
        VettingConfig.query.delete()
        GlobalSettings.query.delete()
        db.session.commit()


class TestVettingConfigPreservedOnReseed:
    """Existing VettingConfig values must survive a re-seed."""

    def test_existing_values_preserved_after_reseed(self, app, models):
        """Pre-populated custom values are untouched by seed_vetting_config."""
        from app import db
        from seed_database import seed_vetting_config
        VettingConfig, _ = models

        with app.app_context():
            # Seed once to create all keys
            seed_vetting_config(db, VettingConfig)

            # Simulate user changing operational settings via UI
            user_overrides = {
                'vetting_enabled': 'true',
                'send_recruiter_emails': 'true',
                'match_threshold': '90',
                'batch_size': '50',
                'embedding_similarity_threshold': '0.45',
            }
            for key, val in user_overrides.items():
                setting = VettingConfig.query.filter_by(setting_key=key).first()
                setting.setting_value = val
            db.session.commit()

            # Re-seed (simulates deploy restart)
            seed_vetting_config(db, VettingConfig)

            # Assert every user-set value is preserved
            for key, expected in user_overrides.items():
                actual = VettingConfig.query.filter_by(
                    setting_key=key
                ).first().setting_value
                assert actual == expected, (
                    f"VettingConfig '{key}' was overwritten: "
                    f"expected '{expected}', got '{actual}'"
                )


class TestGlobalSettingsPreservedOnReseed:
    """Existing GlobalSettings SFTP/automation values must survive a re-seed."""

    def test_existing_sftp_settings_preserved_after_reseed(self, app, models):
        """Pre-populated SFTP and automation toggles are untouched by seed."""
        from app import db
        from seed_database import seed_global_settings
        _, GlobalSettings = models

        with app.app_context():
            # Seed once to create all keys
            seed_global_settings(db, GlobalSettings)

            # Simulate user enabling toggles via UI
            user_overrides = {
                'sftp_enabled': 'true',
                'automated_uploads_enabled': 'true',
            }
            for key, val in user_overrides.items():
                setting = GlobalSettings.query.filter_by(setting_key=key).first()
                setting.setting_value = val
            db.session.commit()

            # Re-seed (simulates deploy restart)
            seed_global_settings(db, GlobalSettings)

            # Assert user-set values are preserved
            for key, expected in user_overrides.items():
                actual = GlobalSettings.query.filter_by(
                    setting_key=key
                ).first().setting_value
                assert actual == expected, (
                    f"GlobalSettings '{key}' was overwritten: "
                    f"expected '{expected}', got '{actual}'"
                )


class TestMissingKeysCreatedBySeed:
    """Missing keys are created with defaults on seed."""

    def test_missing_vetting_keys_created(self, app, models):
        """Empty DB: seed creates all expected VettingConfig keys."""
        from app import db
        from seed_database import seed_vetting_config
        VettingConfig, _ = models

        expected_keys = {
            'vetting_enabled', 'match_threshold', 'batch_size',
            'admin_notification_email', 'health_alert_email',
            'send_recruiter_emails', 'embedding_filter_enabled',
            'embedding_similarity_threshold', 'escalation_low',
            'escalation_high', 'layer2_model', 'vetting_cutoff_date',
            'global_custom_requirements',
        }

        with app.app_context():
            seed_vetting_config(db, VettingConfig)
            created_keys = {
                c.setting_key for c in VettingConfig.query.all()
            }
            assert expected_keys == created_keys


class TestSeedDefaultsEnvironmentAgnostic:
    """Operational toggle defaults are the same regardless of is_prod."""

    def test_toggle_defaults_same_for_dev_and_prod(self, app, models):
        """vetting_enabled, send_recruiter_emails, sftp_enabled, etc.
        all default to 'false' regardless of environment."""
        from app import db
        from seed_database import seed_vetting_config, seed_global_settings
        VettingConfig, GlobalSettings = models

        toggle_keys_vetting = ['vetting_enabled', 'send_recruiter_emails']
        toggle_keys_global = ['sftp_enabled', 'automated_uploads_enabled']

        for is_prod in [True, False]:
            with app.app_context():
                # Clean slate
                VettingConfig.query.delete()
                GlobalSettings.query.delete()
                db.session.commit()

                with patch('seed_database.is_production_environment', return_value=is_prod):
                    seed_vetting_config(db, VettingConfig)
                    seed_global_settings(db, GlobalSettings)

                for key in toggle_keys_vetting:
                    val = VettingConfig.query.filter_by(
                        setting_key=key
                    ).first().setting_value
                    assert val == 'false', (
                        f"VettingConfig '{key}' should be 'false' for is_prod={is_prod}, "
                        f"got '{val}'"
                    )

                for key in toggle_keys_global:
                    val = GlobalSettings.query.filter_by(
                        setting_key=key
                    ).first().setting_value
                    assert val == 'false', (
                        f"GlobalSettings '{key}' should be 'false' for is_prod={is_prod}, "
                        f"got '{val}'"
                    )

                # Embedding threshold should also be same
                emb = VettingConfig.query.filter_by(
                    setting_key='embedding_similarity_threshold'
                ).first().setting_value
                assert emb == '0.25', (
                    f"embedding_similarity_threshold should be '0.25' for is_prod={is_prod}, "
                    f"got '{emb}'"
                )


class TestFixProductionDefaultsRemoved:
    """fix_production_defaults must no longer exist — it was the root cause
    of deploy-time setting overrides."""

    def test_function_does_not_exist(self):
        """seed_database module should NOT have fix_production_defaults."""
        import seed_database
        assert not hasattr(seed_database, 'fix_production_defaults'), (
            "fix_production_defaults still exists in seed_database — "
            "it must be removed to prevent deploy-time overrides"
        )


class TestFullSeedNeverOverwritesVettingEnabled:
    """Running the full seed_database() path must never flip vetting_enabled
    or send_recruiter_emails when they already exist with user-set values."""

    def test_vetting_enabled_false_survives_full_seed(self, app, models):
        """vetting_enabled=false → full seed_database() → still false."""
        from app import db
        from seed_database import seed_database as run_seed
        from models import User, VettingConfig, GlobalSettings

        with app.app_context():
            # Pre-populate critical settings with "user turned off" values
            for key, val in [
                ('vetting_enabled', 'false'),
                ('send_recruiter_emails', 'false'),
            ]:
                db.session.add(VettingConfig(setting_key=key, setting_value=val))
            db.session.commit()

            # Run full seed (simulates deploy — is_prod=False avoids
            # credential validation that would fail in test env; the preservation
            # invariant is environment-independent)
            with patch('seed_database.is_production_environment', return_value=False):
                run_seed(db, User)

            # Assert values preserved
            for key in ['vetting_enabled', 'send_recruiter_emails']:
                actual = VettingConfig.query.filter_by(
                    setting_key=key
                ).first().setting_value
                assert actual == 'false', (
                    f"'{key}' was overwritten by seed_database(): "
                    f"expected 'false', got '{actual}'"
                )


class TestFullSeedNeverOverwritesEmbeddingSettings:
    """Embedding filter + threshold must survive a full seed_database() re-run."""

    def test_embedding_settings_preserved_after_full_seed(self, app, models):
        from app import db
        from seed_database import seed_database as run_seed
        from models import User, VettingConfig

        with app.app_context():
            # Pre-populate with user-customized values
            for key, val in [
                ('embedding_filter_enabled', 'false'),
                ('embedding_similarity_threshold', '0.60'),
                ('vetting_cutoff_date', '2026-01-15 00:00:00'),
            ]:
                db.session.add(VettingConfig(setting_key=key, setting_value=val))
            db.session.commit()

            with patch('seed_database.is_production_environment', return_value=False):
                run_seed(db, User)

            checks = {
                'embedding_filter_enabled': 'false',
                'embedding_similarity_threshold': '0.60',
                'vetting_cutoff_date': '2026-01-15 00:00:00',
            }
            for key, expected in checks.items():
                actual = VettingConfig.query.filter_by(
                    setting_key=key
                ).first().setting_value
                assert actual == expected, (
                    f"'{key}' was overwritten: expected '{expected}', got '{actual}'"
                )


class TestFullSeedNeverOverwritesSFTPFlags:
    """sftp_enabled and automated_uploads_enabled must survive full seed_database()."""

    def test_sftp_flags_false_survive_full_seed(self, app, models):
        from app import db
        from seed_database import seed_database as run_seed
        from models import User, GlobalSettings

        with app.app_context():
            for key in ['sftp_enabled', 'automated_uploads_enabled']:
                db.session.add(GlobalSettings(
                    setting_key=key, setting_value='false',
                    created_at=datetime(2026, 1, 1)
                ))
            db.session.commit()

            with patch('seed_database.is_production_environment', return_value=False):
                run_seed(db, User)

            for key in ['sftp_enabled', 'automated_uploads_enabled']:
                actual = GlobalSettings.query.filter_by(
                    setting_key=key
                ).first().setting_value
                assert actual == 'false', (
                    f"'{key}' was overwritten by seed_database(): "
                    f"expected 'false', got '{actual}'"
                )

