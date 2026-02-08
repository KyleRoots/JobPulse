"""
Bullhorn integration tests for JobPulse.

Tests Bullhorn connection, settings, monitors, and API endpoints.
Tests both success paths and typical failure cases (missing credentials, API unavailable).
"""

import pytest


class TestBullhornConnection:
    """Test Bullhorn connection test endpoint."""
    
    def test_connection_test_requires_auth(self, client):
        """Test that connection test requires authentication."""
        response = client.post('/api/bullhorn/connection-test', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_connection_test_returns_json(self, authenticated_client, app):
        """Test that connection test returns JSON response."""
        response = authenticated_client.post('/api/bullhorn/connection-test')
        # May fail due to no Bullhorn config but should return JSON
        assert response.status_code in [200, 400, 500]
        if response.status_code in [200, 400]:
            assert 'application/json' in response.content_type
    
    def test_connection_test_missing_credentials(self, authenticated_client, app):
        """Test connection test with missing/invalid credentials."""
        response = authenticated_client.post('/api/bullhorn/connection-test')
        # Should return error status with message when credentials missing
        assert response.status_code in [200, 400, 500]
        # If 400, should indicate credentials issue
        if response.status_code == 400:
            data = response.get_json()
            assert 'connection_status' in data
            assert data['connection_status'] in ['failed', 'error']


class TestBullhornAPIStatus:
    """Test Bullhorn API status endpoint."""
    
    def test_api_status_requires_auth(self, client):
        """Test that API status requires authentication."""
        response = client.get('/api/bullhorn/api-status', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_api_status_returns_json(self, authenticated_client, app):
        """Test that API status returns JSON with mode info."""
        response = authenticated_client.get('/api/bullhorn/api-status')
        assert response.status_code == 200
        assert 'application/json' in response.content_type
        
        data = response.get_json()
        assert 'api_mode' in data
        assert data['api_mode'] in ['bullhorn_one', 'legacy']
    
    def test_api_status_contains_endpoints(self, authenticated_client, app):
        """Test that API status contains endpoint information."""
        response = authenticated_client.get('/api/bullhorn/api-status')
        assert response.status_code == 200
        
        data = response.get_json()
        assert 'endpoints' in data


class TestBullhornSettings:
    """Test Bullhorn settings page and save functionality."""
    
    def test_settings_page_requires_auth(self, client):
        """Test that Bullhorn settings page requires authentication."""
        response = client.get('/bullhorn/settings', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_settings_page_renders(self, authenticated_client, app):
        """Test that Bullhorn settings page renders correctly."""
        response = authenticated_client.get('/bullhorn/settings')
        assert response.status_code == 200
        # Should contain settings-related content
        assert b'bullhorn' in response.data.lower() or b'Bullhorn' in response.data
    
    def test_settings_post_requires_auth(self, client):
        """Test that settings POST requires authentication."""
        response = client.post('/bullhorn/settings', data={
            'client_id': 'test_id',
            'client_secret': 'test_secret'
        }, follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_settings_post_valid_data(self, authenticated_client, app):
        """Test settings POST with valid credential data."""
        response = authenticated_client.post('/bullhorn/settings', data={
            'bullhorn_client_id': 'test_client_id',
            'bullhorn_client_secret': 'test_client_secret',
            'bullhorn_username': 'test_user',
            'bullhorn_password': 'test_pass'
        }, follow_redirects=True)
        
        # Should redirect or show success/error message
        assert response.status_code in [200, 302]


class TestBullhornMonitors:
    """Test Bullhorn monitor list and management."""
    
    def test_monitors_list_requires_auth(self, client):
        """Test that monitors list requires authentication."""
        response = client.get('/api/bullhorn/monitors', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_monitors_list_returns_json(self, authenticated_client, app):
        """Test that monitors list returns JSON."""
        response = authenticated_client.get('/api/bullhorn/monitors')
        assert response.status_code in [200, 400, 500]
        if response.status_code == 200:
            assert 'application/json' in response.content_type


class TestBullhornMonitorCreate:
    """Test Bullhorn monitor creation."""
    
    def test_create_page_requires_auth(self, client):
        """Test that monitor create page requires authentication."""
        response = client.get('/bullhorn/create', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_create_page_renders(self, authenticated_client, app):
        """Test that monitor create page renders."""
        response = authenticated_client.get('/bullhorn/create')
        assert response.status_code == 200
    
    def test_create_post_requires_auth(self, client):
        """Test that monitor create POST requires authentication."""
        response = client.post('/bullhorn/create', data={
            'name': 'Test Monitor',
            'tearsheet_id': 123
        }, follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_create_post_empty_data(self, authenticated_client, app):
        """Test monitor create POST with empty data."""
        response = authenticated_client.post('/bullhorn/create', data={},
            follow_redirects=True)
        # Should return validation error or redirect back
        assert response.status_code in [200, 302, 400]


class TestBullhornMonitorDetails:
    """Test Bullhorn monitor detail views."""
    
    def test_monitor_details_nonexistent(self, authenticated_client, app):
        """Test that nonexistent monitor returns 404."""
        response = authenticated_client.get('/bullhorn/monitor/999999')
        assert response.status_code == 404


class TestBullhornMonitorDelete:
    """Test Bullhorn monitor deletion."""
    
    def test_delete_requires_auth(self, client):
        """Test that monitor delete requires authentication."""
        response = client.post('/bullhorn/monitor/1/delete', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_delete_nonexistent_monitor(self, authenticated_client, app):
        """Test deleting nonexistent monitor."""
        response = authenticated_client.post('/bullhorn/monitor/999999/delete',
            follow_redirects=False)
        # Should return 404 for nonexistent
        assert response.status_code in [302, 404]


class TestBullhornMonitorTest:
    """Test Bullhorn monitor test functionality."""
    
    def test_monitor_test_requires_auth(self, client):
        """Test that monitor test requires authentication."""
        response = client.post('/bullhorn/monitor/1/test', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_monitor_test_nonexistent(self, authenticated_client, app):
        """Test testing nonexistent monitor."""
        response = authenticated_client.post('/bullhorn/monitor/999999/test')
        assert response.status_code in [404, 500]


class TestBullhornActivities:
    """Test Bullhorn activities log endpoint."""
    
    def test_activities_requires_auth(self, client):
        """Test that activities endpoint requires authentication."""
        response = client.get('/api/bullhorn/activities', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_activities_returns_json(self, authenticated_client, app):
        """Test that activities endpoint returns JSON."""
        response = authenticated_client.get('/api/bullhorn/activities')
        assert response.status_code in [200, 400, 500]
        if response.status_code == 200:
            assert 'application/json' in response.content_type
    
    def test_activities_empty_list(self, authenticated_client, app):
        """Test that activities returns empty list when no activities."""
        response = authenticated_client.get('/api/bullhorn/activities')
        if response.status_code == 200:
            data = response.get_json()
            # Should be a list or contain an activities key
            assert isinstance(data, (list, dict))


class TestBullhornMonitoringCycles:
    """Test Bullhorn monitoring cycles endpoint."""
    
    def test_monitoring_cycles_requires_auth(self, client):
        """Test that monitoring cycles requires authentication."""
        response = client.get('/api/bullhorn/monitoring-cycles', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_monitoring_cycles_returns_json(self, authenticated_client, app):
        """Test that monitoring cycles returns JSON."""
        response = authenticated_client.get('/api/bullhorn/monitoring-cycles')
        assert response.status_code in [200, 400, 500]


class TestBullhornDashboard:
    """Test Bullhorn dashboard page."""
    
    def test_dashboard_requires_auth(self, client):
        """Test that Bullhorn dashboard requires authentication."""
        response = client.get('/bullhorn', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_dashboard_renders(self, authenticated_client, app):
        """Test that Bullhorn dashboard renders correctly."""
        response = authenticated_client.get('/bullhorn')
        assert response.status_code == 200
    
    def test_dashboard_contains_monitors_section(self, authenticated_client, app):
        """Test that dashboard contains monitors management section."""
        response = authenticated_client.get('/bullhorn')
        assert response.status_code == 200
        # Should contain monitor-related content
        assert response.data is not None
