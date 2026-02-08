"""
Schedule CRUD tests for JobPulse.

Tests schedule creation, deletion, status, and management endpoints.
"""

import pytest
import json


class TestScheduleCreate:
    """Test schedule creation endpoint."""
    
    def test_create_requires_auth(self, client):
        """Test that schedule creation requires authentication."""
        response = client.post('/api/schedules', 
            data=json.dumps({'name': 'Test'}),
            content_type='application/json',
            follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_create_empty_data(self, authenticated_client, app):
        """Test schedule creation with empty data."""
        response = authenticated_client.post('/api/schedules',
            data=json.dumps({}),
            content_type='application/json')
        # Should return validation error
        assert response.status_code in [400, 500]
    
    def test_create_missing_required_fields(self, authenticated_client, app):
        """Test schedule creation with missing required fields."""
        response = authenticated_client.post('/api/schedules',
            data=json.dumps({'name': 'Test Schedule'}),
            content_type='application/json')
        # Should return error for missing fields
        assert response.status_code in [400, 500]
    
    def test_create_invalid_json(self, authenticated_client, app):
        """Test schedule creation with invalid JSON."""
        response = authenticated_client.post('/api/schedules',
            data='not valid json',
            content_type='application/json')
        # Should handle gracefully
        assert response.status_code in [400, 500]


class TestScheduleDelete:
    """Test schedule deletion endpoint."""
    
    def test_delete_requires_auth(self, client):
        """Test that schedule deletion requires authentication."""
        response = client.delete('/api/schedules/1', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_delete_nonexistent(self, authenticated_client, app):
        """Test deleting nonexistent schedule."""
        response = authenticated_client.delete('/api/schedules/999999')
        # Should return 404 or error
        assert response.status_code in [404, 400, 500]


class TestScheduleStatus:
    """Test schedule status endpoint."""
    
    def test_status_requires_auth(self, client):
        """Test that schedule status requires authentication."""
        response = client.get('/api/schedules/1/status', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_status_nonexistent(self, authenticated_client, app):
        """Test getting status of nonexistent schedule."""
        response = authenticated_client.get('/api/schedules/999999/status')
        assert response.status_code in [404, 400, 500]


class TestScheduleProgress:
    """Test schedule progress endpoint."""
    
    def test_progress_requires_auth(self, client):
        """Test that schedule progress requires authentication."""
        response = client.get('/api/schedules/1/progress', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_progress_nonexistent(self, authenticated_client, app):
        """Test getting progress of nonexistent schedule."""
        response = authenticated_client.get('/api/schedules/999999/progress')
        assert response.status_code in [404, 400, 500]


class TestScheduleRun:
    """Test schedule manual run endpoint."""
    
    def test_run_requires_auth(self, client):
        """Test that schedule manual run requires authentication."""
        response = client.post('/api/schedules/1/run', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_run_nonexistent(self, authenticated_client, app):
        """Test running nonexistent schedule."""
        response = authenticated_client.post('/api/schedules/999999/run')
        assert response.status_code in [404, 400, 500]


class TestScheduleReplaceFile:
    """Test schedule file replacement endpoint."""
    
    def test_replace_file_requires_auth(self, client):
        """Test that file replacement requires authentication."""
        response = client.post('/api/schedules/replace-file', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_replace_file_empty_data(self, authenticated_client, app):
        """Test file replacement with no file."""
        response = authenticated_client.post('/api/schedules/replace-file',
            data={})
        # Should return error for missing file
        assert response.status_code in [400, 500]
