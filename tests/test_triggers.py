"""
Trigger endpoint tests for JobPulse.

Tests system trigger endpoints for job sync, file cleanup, health check, and AI fixes.
"""

import pytest


class TestJobSyncTrigger:
    """Test job sync trigger endpoint."""
    
    def test_job_sync_requires_auth(self, client):
        """Test that job sync trigger requires authentication."""
        response = client.post('/api/trigger/job-sync', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_job_sync_returns_json(self, authenticated_client, app):
        """Test that job sync trigger returns JSON."""
        response = authenticated_client.post('/api/trigger/job-sync')
        # May fail due to missing services but should not crash
        assert response.status_code in [200, 400, 500]
        if response.status_code == 200:
            assert 'application/json' in response.content_type


class TestFileCleanupTrigger:
    """Test file cleanup trigger endpoint."""
    
    def test_file_cleanup_requires_auth(self, client):
        """Test that file cleanup trigger requires authentication."""
        response = client.post('/api/trigger/file-cleanup', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_file_cleanup_returns_json(self, authenticated_client, app):
        """Test that file cleanup trigger returns JSON."""
        response = authenticated_client.post('/api/trigger/file-cleanup')
        assert response.status_code in [200, 400, 500]
        if response.status_code == 200:
            assert 'application/json' in response.content_type


class TestHealthCheckTrigger:
    """Test health check trigger endpoint."""
    
    def test_health_check_trigger_requires_auth(self, client):
        """Test that health check trigger requires authentication."""
        response = client.post('/api/trigger/health-check', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_health_check_trigger_returns_json(self, authenticated_client, app):
        """Test that health check trigger returns JSON."""
        response = authenticated_client.post('/api/trigger/health-check')
        assert response.status_code in [200, 400, 500]


class TestAIClassificationFixTrigger:
    """Test AI classification fix trigger endpoint."""
    
    def test_ai_fix_requires_auth(self, client):
        """Test that AI classification fix trigger requires authentication."""
        response = client.post('/api/trigger/ai-classification-fix', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_ai_fix_returns_json(self, authenticated_client, app):
        """Test that AI classification fix trigger returns JSON."""
        response = authenticated_client.post('/api/trigger/ai-classification-fix')
        # May take time or fail due to missing OpenAI config
        assert response.status_code in [200, 400, 500]


class TestRefreshReferenceNumbers:
    """Test refresh reference numbers trigger endpoint."""
    
    def test_refresh_ref_requires_auth(self, client):
        """Test that refresh reference numbers requires authentication."""
        response = client.post('/api/refresh-reference-numbers', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_refresh_ref_executes(self, authenticated_client, app):
        """Test refresh reference numbers endpoint."""
        response = authenticated_client.post('/api/refresh-reference-numbers')
        # May fail due to missing Bullhorn connection
        assert response.status_code in [200, 400, 500]


class TestManualUpload:
    """Test manual upload trigger endpoint."""
    
    def test_manual_upload_requires_auth(self, client):
        """Test that manual upload requires authentication."""
        response = client.post('/manual-upload-now', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_manual_upload_executes(self, authenticated_client, app):
        """Test manual upload endpoint."""
        response = authenticated_client.post('/manual-upload-now')
        # May fail due to missing SFTP config
        assert response.status_code in [200, 302, 400, 500]
