"""
Pytest fixtures for Scout Genius tests.

Provides Flask app, test client, and database fixtures for integration testing.
"""

import os
import sys
import pytest
from datetime import datetime

# Ensure the project root is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── CRITICAL: module-level test environment setup ────────────────────────────
# conftest.py is loaded by pytest BEFORE any test file is collected or
# imported.  Several screening sub-modules (note_builder, notification, …)
# carry top-level "from app import db" statements that execute the moment
# Python walks the screening package tree — which happens even for tests that
# only do "from screening.location_review import …" because Python always
# initialises the parent package (screening/__init__.py) first.
#
# TWO things must happen here at module scope, before any test file is
# imported:
#
# 1. Delete DATABASE_URL so Flask-SQLAlchemy falls back to
#    sqlite:///fallback.db instead of binding to the live PostgreSQL engine.
#    If DATABASE_URL is present when "from app import db" first executes
#    (during test collection), the PostgreSQL engine is cached for the
#    entire process and conftest's session-scoped app fixture cannot fix it.
#
# 2. Delete any stale fallback.db from a previous run.  Without this,
#    the first test-collection import that triggers "from app import db"
#    opens a handle to the existing fallback.db.  When the session-scoped
#    app fixture later tries to os.remove() that same file (to pick up
#    schema changes), it unlinks the directory entry while the handle is
#    still open.  On Linux, SQLite then fails all subsequent writes with
#    "attempt to write a readonly database".  Deleting the file here
#    (before any import opens it) lets the first import create a fresh
#    fallback.db that remains valid for the entire session.
if "DATABASE_URL" in os.environ:
    os.environ.pop("DATABASE_URL")

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_instance_dir = os.path.join(_project_root, "instance")
os.makedirs(_instance_dir, exist_ok=True)
for _stale_db in ("fallback.db", "test.db"):
    _stale_path = os.path.join(_instance_dir, _stale_db)
    if os.path.exists(_stale_path):
        os.remove(_stale_path)


@pytest.fixture(scope='session')
def app():
    """
    Create and configure a Flask application instance for testing.

    This fixture uses a fresh SQLite file (instance/fallback.db) rebuilt from
    the current models on every test session, so schema additions are picked
    up automatically.

    Why not :memory:? extensions.py applies PostgreSQL-specific pool options
    (keepalives, pool_size, max_overflow) when DATABASE_URL is set, which
    SQLite rejects. Using the unset-DATABASE_URL fallback path avoids those
    options entirely. We just need to make sure no stale DB file is reused
    across schema changes.
    """
    # DATABASE_URL and stale fallback.db are both cleaned up at conftest
    # module scope (see the block above the fixtures) so that they are gone
    # before any test-collection import can trigger "from app import db" and
    # bind the engine to PostgreSQL or open a stale SQLite file.
    # Nothing to redo here — just set the testing env flags.
    os.environ['FLASK_ENV'] = 'testing'
    os.environ['TESTING'] = 'true'

    # Import app — extensions.py + app.py will create a fresh
    # instance/fallback.db (SQLite) with the current schema on first import.
    from app import app as flask_app, db

    # Configure for testing. Note: SQLALCHEMY_DATABASE_URI is intentionally
    # NOT overridden here — the engine was already bound to fallback.db
    # during app import, and Flask-SQLAlchemy caches the engine per app.
    # Overriding the URI after init has no effect (this is what hid the
    # stale-schema bug for so long).
    flask_app.config.update({
        'TESTING': True,
        'SECRET_KEY': 'test-secret-key-for-pytest',  # Required for sessions/flash
        'WTF_CSRF_ENABLED': False,  # Disable CSRF for testing
        'LOGIN_DISABLED': False,
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'SERVER_NAME': 'localhost:5000',
    })

    # Create application context and database tables
    with flask_app.app_context():
        db.create_all()
        
        # Create a test admin user - use pbkdf2 for compatibility (scrypt not available on all systems)
        from werkzeug.security import generate_password_hash
        from models import User
        test_user = User.query.filter_by(username='testadmin').first()
        if not test_user:
            test_user = User(
                username='testadmin',
                email='testadmin@test.com'
            )
            # Use pbkdf2:sha256 method for compatibility (scrypt requires OpenSSL 1.1+)
            test_user.password_hash = generate_password_hash('testpassword123', method='pbkdf2:sha256')
            db.session.add(test_user)
            db.session.commit()

        # Ensure testadmin is an admin so _get_user_landing() returns a real
        # dashboard URL instead of falling back to /login. Without this flag,
        # any test that follows redirects after a successful login hits an
        # infinite loop (login → /login → login → ...).
        if not test_user.is_admin:
            test_user.is_admin = True
            db.session.commit()
        
        yield flask_app
        
        # Cleanup
        db.session.remove()
        db.drop_all()


@pytest.fixture(scope='function')
def client(app):
    """
    Create a test client for the Flask application.
    
    This client can be used to make requests to the app without running a server.
    """
    return app.test_client()


@pytest.fixture(scope='function')
def runner(app):
    """Create a test CLI runner for the Flask application."""
    return app.test_cli_runner()


@pytest.fixture(scope='function')
def authenticated_client(client, app):
    """
    Create an authenticated test client (logged in as testadmin).
    
    Use this fixture when testing routes that require authentication.
    """
    with app.app_context():
        # Login via the login route
        response = client.post('/login', data={
            'username': 'testadmin',
            'password': 'testpassword123'
        }, follow_redirects=True)
        
        # Verify login was successful
        assert response.status_code == 200
    
    return client


@pytest.fixture(scope='function')
def db_session(app):
    """
    Provide a database session for tests.
    
    Changes made in this session are rolled back after each test.
    """
    from app import db
    
    with app.app_context():
        # Start a transaction
        connection = db.engine.connect()
        transaction = connection.begin()
        
        # Bind the session to the connection
        db.session.begin_nested()
        
        yield db.session
        
        # Rollback after test
        db.session.rollback()
        transaction.rollback()
        connection.close()


# ============================================================================
# Mock fixtures for external services
# ============================================================================

@pytest.fixture
def mock_bullhorn_service(mocker):
    """
    Mock the BullhornService to avoid real API calls during tests.
    """
    mock = mocker.patch('bullhorn_service.BullhornService')
    mock.return_value.authenticate.return_value = True
    mock.return_value.get_tearsheets.return_value = [
        {'id': 1, 'name': 'Test Tearsheet'}
    ]
    mock.return_value.get_tearsheet_jobs.return_value = [
        {'id': 1, 'title': 'Test Job', 'status': 'Open'}
    ]
    return mock


@pytest.fixture
def mock_email_service(mocker):
    """
    Mock the EmailService to avoid sending real emails during tests.
    """
    mock = mocker.patch('email_service.EmailService')
    mock.return_value.send_email.return_value = True
    return mock


@pytest.fixture
def mock_openai(mocker):
    """
    Mock OpenAI client to avoid real API calls during tests.
    """
    mock = mocker.patch('openai.OpenAI')
    return mock
