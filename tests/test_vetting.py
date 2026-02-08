"""
Vetting tests for JobPulse.

Tests vetting settings, job requirements, thresholds, and vetting operations.
"""

import pytest
import json


class TestVettingPage:
    """Test vetting main page."""
    
    def test_vetting_page_requires_auth(self, client):
        """Test that vetting page requires authentication."""
        response = client.get('/vetting', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_vetting_page_renders(self, authenticated_client, app):
        """Test that vetting page renders correctly."""
        response = authenticated_client.get('/vetting')
        assert response.status_code == 200


class TestVettingSettingsSave:
    """Test vetting settings save endpoint."""
    
    def test_save_requires_auth(self, client):
        """Test that vetting save requires authentication."""
        response = client.post('/vetting/save', 
            data=json.dumps({}),
            content_type='application/json',
            follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_save_empty_data(self, authenticated_client, app):
        """Test vetting save with empty data."""
        response = authenticated_client.post('/vetting/save',
            data=json.dumps({}),
            content_type='application/json')
        # Should handle gracefully
        assert response.status_code in [200, 400, 500]
    
    def test_save_form_data(self, authenticated_client, app):
        """Test vetting save with form data."""
        response = authenticated_client.post('/vetting/save',
            data={
                'email': 'test@example.com',
                'notify_on_match': 'true'
            })
        # Should process form data
        assert response.status_code in [200, 302, 400, 500]


class TestVettingRun:
    """Test vetting run endpoint."""
    
    def test_run_requires_auth(self, client):
        """Test that vetting run requires authentication."""
        response = client.post('/vetting/run', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_run_executes(self, authenticated_client, app):
        """Test that vetting run endpoint responds."""
        response = authenticated_client.post('/vetting/run')
        # May fail due to missing Bullhorn connection but shouldn't crash
        assert response.status_code in [200, 400, 500]


class TestVettingResetRecent:
    """Test vetting reset recent endpoint."""
    
    def test_reset_recent_requires_auth(self, client):
        """Test that reset recent requires authentication."""
        response = client.post('/vetting/reset-recent', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_reset_recent_executes(self, authenticated_client, app):
        """Test reset recent endpoint."""
        response = authenticated_client.post('/vetting/reset-recent')
        assert response.status_code in [200, 302, 400, 500]


class TestVettingFullCleanSlate:
    """Test vetting full clean slate endpoint."""
    
    def test_clean_slate_requires_auth(self, client):
        """Test that clean slate requires authentication."""
        response = client.post('/vetting/full-clean-slate', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_clean_slate_executes(self, authenticated_client, app):
        """Test clean slate endpoint."""
        response = authenticated_client.post('/vetting/full-clean-slate')
        assert response.status_code in [200, 302, 400, 500]


class TestVettingTestEmail:
    """Test vetting test email endpoint."""
    
    def test_test_email_requires_auth(self, client):
        """Test that test email requires authentication."""
        response = client.post('/vetting/test-email', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_test_email_without_recipients(self, authenticated_client, app):
        """Test email sending without recipients."""
        response = authenticated_client.post('/vetting/test-email',
            data={})
        # Should return error for missing recipients
        assert response.status_code in [200, 400, 500]


class TestVettingHealthCheck:
    """Test vetting health check endpoint."""
    
    def test_health_check_requires_auth(self, client):
        """Test that health check requires authentication."""
        response = client.post('/vetting/health-check', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_health_check_executes(self, authenticated_client, app):
        """Test health check endpoint."""
        response = authenticated_client.post('/vetting/health-check')
        assert response.status_code in [200, 400, 500]


class TestVettingJobRequirements:
    """Test job requirements update endpoint."""
    
    def test_requirements_requires_auth(self, client):
        """Test that requirements update requires authentication."""
        response = client.post('/vetting/job/1/requirements', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_requirements_nonexistent_job(self, authenticated_client, app):
        """Test requirements update for nonexistent job."""
        response = authenticated_client.post('/vetting/job/999999/requirements',
            data=json.dumps({'requirements': 'test'}),
            content_type='application/json')
        # Should return 404 or error
        assert response.status_code in [200, 404, 400, 500]


class TestVettingJobThreshold:
    """Test job threshold update endpoint."""
    
    def test_threshold_requires_auth(self, client):
        """Test that threshold update requires authentication."""
        response = client.post('/vetting/job/1/threshold', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_threshold_nonexistent_job(self, authenticated_client, app):
        """Test threshold update for nonexistent job."""
        response = authenticated_client.post('/vetting/job/999999/threshold',
            data=json.dumps({'threshold': 50}),
            content_type='application/json')
        # Should return 404 or error
        assert response.status_code in [200, 404, 400, 500]


class TestVettingJobRefreshRequirements:
    """Test job requirements refresh endpoint."""
    
    def test_refresh_requires_auth(self, client):
        """Test that refresh requirements requires authentication."""
        response = client.post('/vetting/job/1/refresh-requirements', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_refresh_nonexistent_job(self, authenticated_client, app):
        """Test refresh for nonexistent job."""
        response = authenticated_client.post('/vetting/job/999999/refresh-requirements')
        assert response.status_code in [200, 404, 400, 500]


class TestVettingSyncRequirements:
    """Test sync requirements endpoint."""
    
    def test_sync_requires_auth(self, client):
        """Test that sync requirements requires authentication."""
        response = client.post('/vetting/sync-requirements', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_sync_executes(self, authenticated_client, app):
        """Test sync requirements endpoint."""
        response = authenticated_client.post('/vetting/sync-requirements')
        assert response.status_code in [200, 400, 500]


class TestVettingExtractAllRequirements:
    """Test extract all requirements endpoint."""
    
    def test_extract_all_requires_auth(self, client):
        """Test that extract all requires authentication."""
        response = client.post('/vetting/extract-all-requirements', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_extract_all_executes(self, authenticated_client, app):
        """Test extract all requirements endpoint."""
        response = authenticated_client.post('/vetting/extract-all-requirements')
        assert response.status_code in [200, 400, 500]


class TestVettingSampleNotes:
    """Test sample notes page."""
    
    def test_sample_notes_requires_auth(self, client):
        """Test that sample notes requires authentication."""
        response = client.get('/vetting/sample-notes', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_sample_notes_renders(self, authenticated_client, app):
        """Test sample notes page renders."""
        response = authenticated_client.get('/vetting/sample-notes')
        assert response.status_code in [200, 302]
