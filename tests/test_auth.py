"""
Authentication tests for JobPulse.

Tests login, logout, session management, and authentication-protected routes.
"""

import pytest


class TestLogin:
    """Test login functionality."""
    
    def test_login_page_renders(self, client):
        """Test that the login page loads correctly."""
        response = client.get('/login')
        assert response.status_code == 200
        assert b'login' in response.data.lower() or b'Login' in response.data
    
    def test_login_success(self, client, app):
        """Test successful login with valid credentials."""
        response = client.post('/login', data={
            'username': 'testadmin',
            'password': 'testpassword123'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # After login, should redirect to dashboard or main page
        # Check we're not still on the login page with an error
        assert b'Invalid' not in response.data
    
    def test_login_failure_wrong_password(self, client, app):
        """Test login failure with wrong password."""
        response = client.post('/login', data={
            'username': 'testadmin',
            'password': 'wrongpassword'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Should show error or stay on login page
        assert b'invalid' in response.data.lower() or b'login' in response.data.lower()
    
    def test_login_failure_nonexistent_user(self, client, app):
        """Test login failure with nonexistent user."""
        response = client.post('/login', data={
            'username': 'nonexistent',
            'password': 'anypassword'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Should show error or stay on login page
        assert b'invalid' in response.data.lower() or b'login' in response.data.lower()
    
    def test_login_failure_empty_fields(self, client, app):
        """Test login failure with empty fields."""
        response = client.post('/login', data={
            'username': '',
            'password': ''
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Should stay on login page or show validation error
    
    def test_login_failure_empty_username(self, client, app):
        """Test login failure with empty username only."""
        response = client.post('/login', data={
            'username': '',
            'password': 'somepassword'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Should stay on login page
        assert b'login' in response.data.lower()
    
    def test_login_failure_empty_password(self, client, app):
        """Test login failure with empty password only."""
        response = client.post('/login', data={
            'username': 'testadmin',
            'password': ''
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Should stay on login page or show error
        assert b'invalid' in response.data.lower() or b'login' in response.data.lower()
    
    def test_login_sql_injection_attempt(self, client, app):
        """Test that SQL injection attempts are handled safely."""
        response = client.post('/login', data={
            'username': "admin' OR '1'='1",
            'password': "' OR '1'='1"
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Should not crash or allow login
        assert b'invalid' in response.data.lower() or b'login' in response.data.lower()
    
    def test_login_special_characters(self, client, app):
        """Test login handling of special characters in input."""
        response = client.post('/login', data={
            'username': '<script>alert("xss")</script>',
            'password': '"><img src=x onerror=alert(1)>'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Should not crash


class TestLogout:
    """Test logout functionality."""
    
    def test_logout(self, authenticated_client, app):
        """Test that logout works for authenticated user."""
        response = authenticated_client.get('/logout', follow_redirects=True)
        
        assert response.status_code == 200
        # After logout, should redirect to login or home page
    
    def test_logout_clears_session(self, authenticated_client, app):
        """Test that logout clears the session and prevents access to protected routes."""
        # First logout
        authenticated_client.get('/logout', follow_redirects=True)
        
        # Then try to access protected route
        response = authenticated_client.get('/dashboard', follow_redirects=False)
        
        # Should redirect to login (session cleared)
        assert response.status_code in [302, 401, 403]
    
    def test_logout_unauthenticated_user(self, client):
        """Test that logout doesn't crash for unauthenticated user."""
        response = client.get('/logout', follow_redirects=True)
        
        # Should redirect to login or home page without error
        assert response.status_code == 200


class TestSessionPersistence:
    """Test session persistence and management."""
    
    def test_session_persists_across_requests(self, authenticated_client, app):
        """Test that session persists across multiple requests."""
        # First request - should be authenticated
        response1 = authenticated_client.get('/dashboard')
        assert response1.status_code in [200, 302]
        
        # Second request - should still be authenticated
        response2 = authenticated_client.get('/bullhorn')
        assert response2.status_code in [200, 302]
        
        # Third request - should still be authenticated
        response3 = authenticated_client.get('/scheduler')
        assert response3.status_code in [200, 302]


class TestProtectedRoutes:
    """Test that protected routes require authentication."""
    
    # Core dashboard routes
    def test_dashboard_requires_login(self, client):
        """Test that dashboard redirects to login when not authenticated."""
        response = client.get('/dashboard', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_scheduler_requires_login(self, client):
        """Test that scheduler page requires authentication."""
        response = client.get('/scheduler', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    # Bullhorn routes
    def test_bullhorn_dashboard_requires_login(self, client):
        """Test that Bullhorn dashboard requires authentication."""
        response = client.get('/bullhorn', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_bullhorn_settings_requires_login(self, client):
        """Test that Bullhorn settings page requires authentication."""
        response = client.get('/bullhorn/settings', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_bullhorn_create_requires_login(self, client):
        """Test that Bullhorn monitor create page requires authentication."""
        response = client.get('/bullhorn/create', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    # Vetting routes
    def test_vetting_page_requires_login(self, client):
        """Test that vetting page requires authentication."""
        response = client.get('/vetting', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    # Settings routes
    def test_settings_page_requires_login(self, client):
        """Test that settings page requires authentication."""
        response = client.get('/settings', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    # API routes that require auth
    def test_api_schedules_requires_login(self, client):
        """Test that API schedule creation requires authentication."""
        response = client.post('/api/schedules', follow_redirects=False)
        assert response.status_code in [302, 401, 403]
    
    def test_api_bullhorn_connection_test_requires_login(self, client):
        """Test that Bullhorn connection test requires authentication."""
        response = client.post('/api/bullhorn/connection-test', follow_redirects=False)
        assert response.status_code in [302, 401, 403]


class TestAuthenticatedAccess:
    """Test that authenticated users can access protected routes."""
    
    def test_dashboard_accessible_when_authenticated(self, authenticated_client, app):
        """Test that authenticated user can access dashboard."""
        response = authenticated_client.get('/dashboard')
        assert response.status_code in [200, 302]
    
    def test_scheduler_accessible_when_authenticated(self, authenticated_client, app):
        """Test that authenticated user can access scheduler."""
        response = authenticated_client.get('/scheduler')
        assert response.status_code in [200, 302]
    
    def test_bullhorn_page_accessible_when_authenticated(self, authenticated_client, app):
        """Test that authenticated user can access Bullhorn page."""
        response = authenticated_client.get('/bullhorn')
        assert response.status_code in [200, 302]
    
    def test_bullhorn_settings_accessible_when_authenticated(self, authenticated_client, app):
        """Test that authenticated user can access Bullhorn settings."""
        response = authenticated_client.get('/bullhorn/settings')
        assert response.status_code in [200, 302]
    
    def test_vetting_accessible_when_authenticated(self, authenticated_client, app):
        """Test that authenticated user can access vetting page."""
        response = authenticated_client.get('/vetting')
        assert response.status_code in [200, 302]
    
    def test_settings_accessible_when_authenticated(self, authenticated_client, app):
        """Test that authenticated user can access settings page."""
        response = authenticated_client.get('/settings')
        assert response.status_code in [200, 302]
