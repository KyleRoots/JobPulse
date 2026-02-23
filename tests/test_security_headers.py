"""
Tests for security headers on all response types.

Verifies that the @app.after_request handler sets the correct security
headers on authenticated routes, public routes, and health check endpoints.
"""
import pytest


class TestSecurityHeaders:
    """Verify security headers are present on all response types"""

    EXPECTED_HEADERS = {
        'X-Frame-Options': 'DENY',
        'X-Content-Type-Options': 'nosniff',
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'X-XSS-Protection': '0',
    }

    def _assert_security_headers(self, response):
        """Helper to check all standard security headers on a response"""
        for header, expected_value in self.EXPECTED_HEADERS.items():
            assert header in response.headers, f"Missing header: {header}"
            assert response.headers[header] == expected_value, (
                f"{header}: expected '{expected_value}', got '{response.headers[header]}'"
            )
        # CSP should be present and contain key directives
        assert 'Content-Security-Policy' in response.headers
        csp = response.headers['Content-Security-Policy']
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_headers_on_login_page(self, client):
        """Security headers should be set on the public login page"""
        response = client.get('/login')
        self._assert_security_headers(response)

    def test_headers_on_health_endpoint(self, client):
        """Security headers should be set on health check responses"""
        response = client.get('/health')
        self._assert_security_headers(response)

    def test_headers_on_authenticated_page(self, authenticated_client, app):
        """Security headers should be set on authenticated dashboard page"""
        with app.app_context():
            response = authenticated_client.get('/')
            self._assert_security_headers(response)

    def test_headers_on_json_api(self, authenticated_client, app):
        """Security headers should be set on JSON API responses"""
        with app.app_context():
            response = authenticated_client.get('/api/monitors')
            self._assert_security_headers(response)

    def test_csp_allows_cdn_scripts(self, client):
        """CSP should allowlist CDN sources used by templates"""
        response = client.get('/login')
        csp = response.headers['Content-Security-Policy']
        assert 'https://cdn.jsdelivr.net' in csp
        assert 'https://cdnjs.cloudflare.com' in csp

    def test_csp_allows_google_fonts(self, client):
        """CSP should allowlist Google Fonts used by templates"""
        response = client.get('/login')
        csp = response.headers['Content-Security-Policy']
        assert 'https://fonts.googleapis.com' in csp
        assert 'https://fonts.gstatic.com' in csp

    def test_csp_allows_replit_bootstrap_theme(self, client):
        """CSP style-src should allow cdn.replit.com for Bootstrap dark theme CSS"""
        response = client.get('/login')
        csp = response.headers['Content-Security-Policy']
        assert 'https://cdn.replit.com' in csp
        # Verify it's specifically in style-src (not script-src)
        style_src = [d for d in csp.split(';') if 'style-src' in d]
        assert len(style_src) == 1
        assert 'https://cdn.replit.com' in style_src[0]
