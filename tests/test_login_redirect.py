"""
Tests for P2 login open-redirect fix.

Verifies that the login `next` parameter only allows safe, relative URLs.
External and protocol-relative URLs must fall back to the dashboard.
"""
import pytest


class TestLoginRedirectSafety:
    """Verify login redirect is safe against open-redirect attacks"""

    def _login(self, client, next_url=None):
        """Helper to POST login credentials with an optional next param"""
        login_url = '/login'
        if next_url is not None:
            login_url = f'/login?next={next_url}'
        return client.post(login_url, data={
            'username': 'testadmin',
            'password': 'testpassword123'
        }, follow_redirects=False)

    def test_safe_relative_next_redirects(self, client, app):
        """A safe relative next like /email-logs should redirect there"""
        with app.app_context():
            response = self._login(client, next_url='/email-logs')
            assert response.status_code == 302
            assert '/email-logs' in response.headers['Location']

    def test_no_next_redirects_to_dashboard(self, client, app):
        """No next parameter should redirect to the dashboard"""
        with app.app_context():
            response = self._login(client)
            assert response.status_code == 302
            location = response.headers['Location']
            # Should go to dashboard, not an external site
            assert 'dashboard' in location or '/' == location.rstrip('#top')

    def test_external_https_url_blocked(self, client, app):
        """External URL https://evil.com should NOT be followed"""
        with app.app_context():
            response = self._login(client, next_url='https://evil.com')
            assert response.status_code == 302
            location = response.headers['Location']
            assert 'evil.com' not in location

    def test_external_http_url_blocked(self, client, app):
        """External URL http://evil.com should NOT be followed"""
        with app.app_context():
            response = self._login(client, next_url='http://evil.com')
            assert response.status_code == 302
            location = response.headers['Location']
            assert 'evil.com' not in location

    def test_protocol_relative_url_blocked(self, client, app):
        """Protocol-relative URL //evil.com should NOT be followed"""
        with app.app_context():
            response = self._login(client, next_url='//evil.com')
            assert response.status_code == 302
            location = response.headers['Location']
            assert 'evil.com' not in location

    def test_javascript_scheme_blocked(self, client, app):
        """javascript: URLs should NOT be followed"""
        with app.app_context():
            response = self._login(client, next_url='javascript:alert(1)')
            assert response.status_code == 302
            location = response.headers['Location']
            assert 'javascript' not in location


class TestIsSafeRedirectUrl:
    """Unit tests for the _is_safe_redirect_url helper function"""

    def test_relative_path_is_safe(self, app):
        """A relative path like /dashboard should be safe"""
        from routes.auth import _is_safe_redirect_url
        assert _is_safe_redirect_url('/dashboard') is True

    def test_relative_path_with_query_is_safe(self, app):
        """A relative path with query string should be safe"""
        from routes.auth import _is_safe_redirect_url
        assert _is_safe_redirect_url('/settings?tab=sftp') is True

    def test_external_url_is_unsafe(self, app):
        """An external URL should be unsafe"""
        from routes.auth import _is_safe_redirect_url
        assert _is_safe_redirect_url('https://evil.com') is False

    def test_protocol_relative_is_unsafe(self, app):
        """A protocol-relative URL should be unsafe"""
        from routes.auth import _is_safe_redirect_url
        assert _is_safe_redirect_url('//evil.com') is False

    def test_empty_string_is_unsafe(self, app):
        """An empty string should be unsafe"""
        from routes.auth import _is_safe_redirect_url
        assert _is_safe_redirect_url('') is False

    def test_none_is_unsafe(self, app):
        """None should be unsafe"""
        from routes.auth import _is_safe_redirect_url
        assert _is_safe_redirect_url(None) is False
