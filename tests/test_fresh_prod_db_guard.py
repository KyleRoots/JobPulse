"""
Regression tests for check_fresh_production_database_guard.

The guard halts production boot when the database is clearly empty (no
users, no vetting config), preventing the seed function from silently
populating an empty database with default values that would erase the
operator's prior configuration.
"""

import os
import pytest
from unittest.mock import patch


@pytest.fixture
def models(app):
    from models import User, VettingConfig
    return User, VettingConfig


@pytest.fixture(autouse=True)
def clean_state(app):
    """Wipe User + VettingConfig before/after each test.

    Safe because conftest.py points the test app at a separate SQLite
    fallback database (instance/fallback.db) — NOT the dev/prod
    PostgreSQL. We confirm the SQLite fallback is in use before
    deleting anything, so this fixture cannot accidentally truncate
    production data.
    """
    from app import db
    from models import User, VettingConfig
    with app.app_context():
        engine_url = str(db.engine.url)
        assert engine_url.startswith('sqlite'), (
            f"Refusing to wipe a non-SQLite database (engine={engine_url}). "
            "This fixture is only safe against the test fallback DB."
        )
        VettingConfig.query.delete()
        User.query.delete()
        db.session.commit()
        yield
        VettingConfig.query.delete()
        User.query.delete()
        db.session.commit()


@pytest.fixture(autouse=True)
def isolate_env():
    """Strip override + production env vars so each test sets its own."""
    keys = ['ALLOW_FRESH_PROD_SEED', 'APP_ENV', 'ENVIRONMENT',
            'REPLIT_DEPLOYMENT']
    saved = {k: os.environ.pop(k, None) for k in keys}
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


def _patch_prod():
    """Force the guard to take the production code path by:
      - reporting we are in a production environment
      - reporting we are NOT in a test environment (overrides the
        PYTEST_CURRENT_TEST signal that pytest auto-sets)
    """
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch('seed_database.is_production_environment',
                              return_value=True))
    stack.enter_context(patch('seed_database._is_test_environment',
                              return_value=False))
    return stack


class TestGuardSkipsInDevelopment:
    def test_dev_empty_db_no_op(self, app):
        """Dev environment + empty DB → no-op, no exception."""
        from app import db
        from seed_database import check_fresh_production_database_guard
        with app.app_context():
            with patch('seed_database.is_production_environment', return_value=False):
                check_fresh_production_database_guard(db)

    def test_test_environment_no_op_even_in_prod(self, app):
        """Production env + pytest signal → guard skips."""
        from app import db
        from seed_database import check_fresh_production_database_guard
        with app.app_context():
            with patch('seed_database.is_production_environment', return_value=True):
                with patch('seed_database._is_test_environment', return_value=True):
                    # Empty DB but should still skip due to test env
                    check_fresh_production_database_guard(db)


class TestGuardAllowsWhenDataPresent:
    def test_prod_with_users_no_op(self, app, models):
        """Prod + non-empty user table → no-op."""
        from app import db
        from seed_database import check_fresh_production_database_guard
        User, _ = models
        with app.app_context():
            db.session.add(User(
                username='admin', email='a@b.com', password_hash='x',
                is_admin=True
            ))
            db.session.commit()
            with _patch_prod():
                check_fresh_production_database_guard(db)

    def test_prod_with_vetting_config_no_op(self, app, models):
        """Prod + non-empty vetting_config → no-op."""
        from app import db
        from seed_database import check_fresh_production_database_guard
        _, VettingConfig = models
        with app.app_context():
            db.session.add(VettingConfig(
                setting_key='vetting_enabled', setting_value='true'
            ))
            db.session.commit()
            with _patch_prod():
                check_fresh_production_database_guard(db)


class TestGuardBlocksFreshProdDatabase:
    def test_prod_empty_db_no_override_raises(self, app):
        """Prod + empty DB + no override → raises FreshProductionDatabaseError."""
        from app import db
        from seed_database import (
            check_fresh_production_database_guard,
            FreshProductionDatabaseError,
        )
        with app.app_context():
            with _patch_prod():
                with pytest.raises(FreshProductionDatabaseError) as exc_info:
                    check_fresh_production_database_guard(db)
        # Error message should explain what to do
        assert 'ALLOW_FRESH_PROD_SEED' in str(exc_info.value)
        assert 'empty' in str(exc_info.value).lower()


class TestGuardHonorsOverride:
    def test_prod_empty_db_with_override_allows(self, app):
        """Prod + empty DB + ALLOW_FRESH_PROD_SEED=true → no-op (warned)."""
        from app import db
        from seed_database import check_fresh_production_database_guard
        with app.app_context():
            with patch.dict('os.environ', {'ALLOW_FRESH_PROD_SEED': 'true'}):
                with _patch_prod():
                    check_fresh_production_database_guard(db)

    def test_override_is_case_insensitive(self, app):
        """ALLOW_FRESH_PROD_SEED=TRUE also works."""
        from app import db
        from seed_database import check_fresh_production_database_guard
        with app.app_context():
            with patch.dict('os.environ', {'ALLOW_FRESH_PROD_SEED': 'TRUE'}):
                with _patch_prod():
                    check_fresh_production_database_guard(db)

    def test_override_with_wrong_value_still_blocks(self, app):
        """ALLOW_FRESH_PROD_SEED=yes (not 'true') does NOT bypass the guard."""
        from app import db
        from seed_database import (
            check_fresh_production_database_guard,
            FreshProductionDatabaseError,
        )
        with app.app_context():
            with patch.dict('os.environ', {'ALLOW_FRESH_PROD_SEED': 'yes'}):
                with _patch_prod():
                    with pytest.raises(FreshProductionDatabaseError):
                        check_fresh_production_database_guard(db)


class TestGuardWiredIntoSeedDatabase:
    def test_seed_database_aborts_on_fresh_prod(self, app):
        """Full seed_database() must abort when prod + empty DB + no override."""
        from app import db
        from models import User
        from seed_database import (
            seed_database as run_seed,
            FreshProductionDatabaseError,
        )
        with app.app_context():
            with _patch_prod():
                with pytest.raises(FreshProductionDatabaseError):
                    run_seed(db, User)

    def test_seed_database_proceeds_with_override(self, app):
        """Full seed_database() proceeds when override is set."""
        from app import db
        from models import User, VettingConfig
        from seed_database import seed_database as run_seed
        with app.app_context():
            # Override bypasses the guard. Then we let downstream seed
            # logic run under the test env (no production validation).
            with patch.dict('os.environ', {'ALLOW_FRESH_PROD_SEED': 'true'}):
                with _patch_prod():
                    # First call — guard sees override, proceeds. The rest
                    # of seed_database runs under is_production=True path.
                    # Stub get_admin_config to provide a password so the
                    # production admin-password requirement passes.
                    with patch('seed_database.get_admin_config', return_value={
                        'username': 'admin',
                        'email': 'a@b.com',
                        'password': 'pw',
                        'is_admin': True,
                    }):
                        run_seed(db, User)
            # Seeding ran → vetting_config has rows now
            assert VettingConfig.query.count() > 0
