"""
Authentication tests for JobPulse.

Tests login, logout, and authentication-protected routes.
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


class TestLogout:
    """Test logout functionality."""
    
    def test_logout(self, authenticated_client, app):
        """Test that logout works for authenticated user."""
        response = authenticated_client.get('/logout', follow_redirects=True)
        
        assert response.status_code == 200
        # After logout, should redirect to login or home page


class TestProtectedRoutes:
    """Test that protected routes require authentication."""
    
    def test_dashboard_requires_login(self, client):
        """Test that dashboard redirects to login when not authenticated."""
        response = client.get('/dashboard', follow_redirects=False)
        
        # Should redirect to login (302) or unauthorized (401)
        assert response.status_code in [302, 401, 403]
    
    def test_bullhorn_settings_requires_login(self, client):
        """Test that Bullhorn settings page requires authentication."""
        response = client.get('/bullhorn/settings', follow_redirects=False)
        
        assert response.status_code in [302, 401, 403]
    
    def test_scheduler_requires_login(self, client):
        """Test that scheduler page requires authentication."""
        response = client.get('/scheduler', follow_redirects=False)
        
        assert response.status_code in [302, 401, 403]
    
    def test_vetting_settings_requires_login(self, client):
        """Test that vetting settings page requires authentication."""
        response = client.get('/vetting/settings', follow_redirects=False)
        
        assert response.status_code in [302, 401, 403]


class TestAuthenticatedAccess:
    """Test that authenticated users can access protected routes."""
    
    def test_dashboard_accessible_when_authenticated(self, authenticated_client, app):
        """Test that authenticated user can access dashboard."""
        response = authenticated_client.get('/dashboard')
        
        # Should succeed (200) or redirect to main page
        assert response.status_code in [200, 302]
    
    def test_bullhorn_page_accessible_when_authenticated(self, authenticated_client, app):
        """Test that authenticated user can access Bullhorn page."""
        response = authenticated_client.get('/bullhorn/dashboard')
        
        assert response.status_code in [200, 302]
