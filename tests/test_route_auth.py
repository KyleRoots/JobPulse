"""
Tests for P1 authorization fixes.

Verifies that previously unprotected routes now require authentication:
- /automation-status
- /manual-upload-progress/<upload_id>
- /test_download/<download_key>
"""
import pytest


class TestAutomationStatusAuth:
    """Verify /automation-status requires authentication"""

    def test_unauthenticated_redirects_to_login(self, client):
        """Unauthenticated user should be redirected to login"""
        response = client.get('/automation-status')
        assert response.status_code == 302
        assert '/login' in response.headers['Location']

    def test_authenticated_can_access(self, authenticated_client, app):
        """Authenticated user should get a successful response"""
        with app.app_context():
            response = authenticated_client.get('/automation-status')
            assert response.status_code == 200


class TestManualUploadProgressAuth:
    """Verify /manual-upload-progress/<upload_id> requires authentication"""

    def test_unauthenticated_redirects_to_login(self, client):
        """Unauthenticated user should be redirected to login"""
        response = client.get('/manual-upload-progress/test-upload-123')
        assert response.status_code == 302
        assert '/login' in response.headers['Location']

    def test_authenticated_can_access(self, authenticated_client, app):
        """Authenticated user gets a response (404 for nonexistent upload is fine)"""
        with app.app_context():
            response = authenticated_client.get('/manual-upload-progress/nonexistent-id')
            # 404 is expected since no real upload exists; the point is it's not a 302
            assert response.status_code in (200, 404)


class TestTestDownloadAuth:
    """Verify /test_download/<download_key> requires authentication"""

    def test_unauthenticated_redirects_to_login(self, client):
        """Unauthenticated user should be redirected to login"""
        response = client.get('/test_download/fake-key-123')
        assert response.status_code == 302
        assert '/login' in response.headers['Location']

    def test_authenticated_can_access(self, authenticated_client, app):
        """Authenticated user gets a response (redirect for invalid key is fine)"""
        with app.app_context():
            response = authenticated_client.get('/test_download/nonexistent-key')
            # Route may redirect to automation_test or return error — not a 302 to login
            assert response.status_code != 302 or '/login' not in response.headers.get('Location', '')
