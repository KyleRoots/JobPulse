"""
Tests for P5 CSRF protection.

These tests explicitly re-enable CSRF to verify that:
1. Protected routes reject POST requests without a valid CSRF token
2. Protected routes accept POST requests with a valid CSRF token
3. Exempt routes (webhooks, public forms) work without a CSRF token
4. CSRF meta tag is present in authenticated pages
"""

import os
import sys
import pytest
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def csrf_app():
    """Create a Flask app with CSRF protection ENABLED.
    
    Important: saves and restores WTF_CSRF_ENABLED to avoid
    bleeding CSRF=True into other tests that share the app singleton.
    """
    os.environ['FLASK_ENV'] = 'testing'
    os.environ['TESTING'] = 'true'
    if 'DATABASE_URL' in os.environ:
        del os.environ['DATABASE_URL']

    from app import app as flask_app, db

    # Save original config values so we can restore after the test
    original_csrf_enabled = flask_app.config.get('WTF_CSRF_ENABLED')

    flask_app.config.update({
        'TESTING': True,
        'SECRET_KEY': 'test-secret-key-for-csrf-tests',
        'WTF_CSRF_ENABLED': True,  # CSRF enabled for these tests
        'WTF_CSRF_TIME_LIMIT': None,
        'LOGIN_DISABLED': False,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'SERVER_NAME': 'localhost:5000',
    })

    with flask_app.app_context():
        db.create_all()

        from werkzeug.security import generate_password_hash
        from models import User
        test_user = User.query.filter_by(username='testadmin').first()
        if not test_user:
            test_user = User(username='testadmin', email='testadmin@test.com')
            test_user.password_hash = generate_password_hash(
                'testpassword123', method='pbkdf2:sha256'
            )
            db.session.add(test_user)
            db.session.commit()

        yield flask_app

        # Restore original CSRF config to prevent bleeding into other tests
        flask_app.config['WTF_CSRF_ENABLED'] = original_csrf_enabled if original_csrf_enabled is not None else False


@pytest.fixture
def csrf_client(csrf_app):
    """Test client with CSRF enabled."""
    return csrf_app.test_client()


def _extract_csrf_token(html):
    """Extract the CSRF token from a rendered HTML page (from meta tag or hidden field)."""
    # Try meta tag first
    match = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', html)
    if match:
        return match.group(1)
    # Try hidden input field
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if match:
        return match.group(1)
    return None


def _login_with_csrf(csrf_client):
    """Login by extracting CSRF token from the login page first."""
    # GET the login page to obtain a CSRF token
    response = csrf_client.get('/login')
    html = response.data.decode()
    token = _extract_csrf_token(html)
    assert token is not None, "CSRF token not found in login page"

    # POST login with the extracted token
    response = csrf_client.post('/login', data={
        'username': 'testadmin',
        'password': 'testpassword123',
        'csrf_token': token,
    }, follow_redirects=True)
    assert response.status_code == 200
    return token


# ============================================================================
# Tests: Protected routes REJECT requests without CSRF token
# ============================================================================

class TestCSRFEnforcement:
    """Verify that protected routes reject POST requests without a CSRF token."""

    def test_login_post_without_csrf_token_rejected(self, csrf_client, csrf_app):
        """Login form POST without CSRF token returns 400."""
        with csrf_app.app_context():
            response = csrf_client.post('/login', data={
                'username': 'testadmin',
                'password': 'testpassword123',
            })
            assert response.status_code == 400

    def test_settings_post_without_csrf_token_rejected(self, csrf_client, csrf_app):
        """Authenticated POST to /settings without CSRF token → 400."""
        with csrf_app.app_context():
            _login_with_csrf(csrf_client)
            # Now POST to settings WITHOUT the CSRF token
            response = csrf_client.post('/settings', data={
                'sftp_host': 'test.example.com',
            })
            assert response.status_code == 400

    def test_api_feedback_without_csrf_token_rejected(self, csrf_client, csrf_app):
        """POST to /api/feedback without CSRF token → 400."""
        with csrf_app.app_context():
            _login_with_csrf(csrf_client)
            response = csrf_client.post('/api/feedback',
                json={'type': 'bug', 'message': 'test', 'page': '/'},
            )
            assert response.status_code == 400


# ============================================================================
# Tests: Protected routes ACCEPT requests with valid CSRF token
# ============================================================================

class TestCSRFAcceptance:
    """Verify that protected routes accept POST requests with a valid CSRF token."""

    def test_login_post_with_valid_csrf_token_succeeds(self, csrf_client, csrf_app):
        """Login form POST with valid CSRF token succeeds (redirects to dashboard)."""
        with csrf_app.app_context():
            # GET login page to get CSRF token
            response = csrf_client.get('/login')
            token = _extract_csrf_token(response.data.decode())
            assert token is not None

            response = csrf_client.post('/login', data={
                'username': 'testadmin',
                'password': 'testpassword123',
                'csrf_token': token,
            }, follow_redirects=False)
            # Successful login redirects (302), not 400
            assert response.status_code in (200, 302)

    def test_api_feedback_with_csrf_header_succeeds(self, csrf_client, csrf_app):
        """POST to /api/feedback with X-CSRFToken header succeeds."""
        with csrf_app.app_context():
            _login_with_csrf(csrf_client)
            # GET an authenticated page to get a fresh token
            response = csrf_client.get('/settings')
            token = _extract_csrf_token(response.data.decode())
            assert token is not None

            response = csrf_client.post('/api/feedback',
                json={'type': 'suggestion', 'message': 'Great app!', 'page': '/test',
                      'user': 'testadmin'},
                headers={'X-CSRFToken': token},
            )
            # Should not be 400 (CSRF ok) — any status is fine as long as not 400
            assert response.status_code != 400


# ============================================================================
# Tests: Exempt routes work WITHOUT a CSRF token
# ============================================================================

class TestCSRFExemptions:
    """Verify that explicitly exempted routes accept POST requests without CSRF."""

    def test_inbound_email_webhook_exempt(self, csrf_client, csrf_app):
        """POST to /api/email/inbound (SendGrid webhook) works without CSRF token."""
        with csrf_app.app_context():
            response = csrf_client.post('/api/email/inbound',
                data={'from': 'test@example.com', 'subject': 'Test'},
                content_type='multipart/form-data',
            )
            # Should NOT be 400 (CSRF exempt)
            assert response.status_code != 400

    def test_parse_resume_exempt(self, csrf_client, csrf_app):
        """POST to /parse-resume (public form) works without CSRF token."""
        with csrf_app.app_context():
            from io import BytesIO
            data = {
                'resume': (BytesIO(b'fake resume content'), 'resume.pdf'),
            }
            response = csrf_client.post('/parse-resume',
                data=data,
                content_type='multipart/form-data',
            )
            # Should NOT be 400 (CSRF exempt)
            assert response.status_code != 400

    def test_submit_application_exempt(self, csrf_client, csrf_app):
        """POST to /submit-application (public form) works without CSRF token."""
        with csrf_app.app_context():
            response = csrf_client.post('/submit-application',
                data={'first_name': 'Test', 'last_name': 'User'},
                content_type='multipart/form-data',
            )
            # Should NOT be 400 (CSRF exempt)
            assert response.status_code != 400


# ============================================================================
# Test: CSRF token appears in HTML meta tag
# ============================================================================

class TestCSRFMetaTag:
    """Verify that the CSRF token is exposed via a meta tag in base_layout."""

    def test_csrf_meta_tag_present_on_authenticated_page(self, csrf_client, csrf_app):
        """Authenticated page contains <meta name='csrf-token'> tag."""
        with csrf_app.app_context():
            _login_with_csrf(csrf_client)
            response = csrf_client.get('/settings')
            html = response.data.decode()
            assert 'name="csrf-token"' in html
            token = _extract_csrf_token(html)
            assert token is not None
            assert len(token) > 10  # Token should be a substantial string
