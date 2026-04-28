"""AuthMixin — Bullhorn API methods for this domain."""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

import requests  # noqa: F401  (used by methods via self.session)

logger = logging.getLogger(__name__)


class AuthMixin:
    """Mixin providing auth-related Bullhorn API methods."""

    def authenticate(self) -> bool:
        """
        Authenticate with Bullhorn using OAuth 2.0 flow
        
        Returns:
            bool: True if authentication successful, False otherwise
        """
        # Check if we already have a valid session - skip re-authentication
        if self.rest_token and self.base_url:
            logger.debug("Already authenticated, reusing existing session")
            return True
        
        # Prevent concurrent authentication attempts
        if self._auth_in_progress:
            logger.warning("Authentication already in progress, skipping duplicate attempt")
            return False
            
        # Check if we just tried to authenticate (within last 5 seconds - reduced from 30 to allow faster recovery)
        if self._last_auth_attempt:
            time_since_last = datetime.now() - self._last_auth_attempt
            if time_since_last.total_seconds() < 5:
                logger.warning(f"Recent authentication attempt detected ({time_since_last.total_seconds():.1f}s ago), skipping to prevent overload")
                return False
        
        if not all([self.client_id, self.client_secret, self.username, self.password]):
            missing = []
            if not self.client_id: missing.append('client_id')
            if not self.client_secret: missing.append('client_secret')
            if not self.username: missing.append('username')
            if not self.password: missing.append('password')
            api_mode = 'Bullhorn One' if self.use_bullhorn_one else 'Legacy Bullhorn'
            logger.error(f"Missing {api_mode} credentials: {', '.join(missing)}")
            return False
            
        try:
            self._auth_in_progress = True
            self._last_auth_attempt = datetime.now()
            # Try direct login first (simpler for API access)
            return self._direct_login()
            
        except Exception as e:
            logger.error(f"Bullhorn authentication failed: {str(e)}")
            return False
        finally:
            self._auth_in_progress = False
    def _get_current_user_id(self) -> Optional[int]:
        """
        Query Bullhorn API for the current user's ID (CorporateUser)
        This is used as a fallback when userId is not returned in REST login response
        
        Returns:
            Optional[int]: The current user's ID, or None if not found
        """
        if not self.base_url or not self.rest_token:
            return None
        
        try:
            # Query the settings endpoint which returns current user info
            url = f"{self.base_url}settings/userId"
            params = {'BhRestToken': self.rest_token}
            
            response = self.session.get(url, params=params, timeout=15)
            if response.status_code == 200:
                data = self._safe_json_parse(response)
                user_id = data.get('userId')
                if user_id:
                    logger.info(f"Got user ID from settings endpoint: {user_id}")
                    return int(user_id)
            
            # Alternative: Try to get from userInfo
            url = f"{self.base_url}userInfo"
            response = self.session.get(url, params=params, timeout=15)
            if response.status_code == 200:
                data = self._safe_json_parse(response)
                user_id = data.get('id') or data.get('userId')
                if user_id:
                    logger.info(f"Got user ID from userInfo endpoint: {user_id}")
                    return int(user_id)
                    
        except Exception as e:
            logger.warning(f"Could not query current user ID: {e}")
        
        return None
    def _direct_login(self) -> bool:
        """
        Complete Bullhorn OAuth 2.0 authentication flow
        
        Supports both legacy Bullhorn and Bullhorn One APIs:
        - Legacy: Uses loginInfo endpoint to discover OAuth/REST URLs dynamically
        - Bullhorn One: Uses fixed endpoints provided by Bullhorn support
        
        Returns:
            bool: True if login successful, False otherwise
        """
        # Initialize variables for error logging (before try block)
        oauth_url = ""
        auth_endpoint = ""
        token_endpoint = ""
        rest_login_url = ""
        rest_url = ""
        redirect_uri = ""
        
        try:
            if self.use_bullhorn_one:
                # Bullhorn One: Use fixed endpoints (no loginInfo discovery needed)
                logger.info("🔄 Using Bullhorn One fixed endpoints for authentication")
                auth_endpoint = self.BULLHORN_ONE_AUTH_URL
                token_endpoint = self.BULLHORN_ONE_TOKEN_URL
                rest_login_url = self.BULLHORN_ONE_REST_LOGIN_URL
                rest_url = self.BULLHORN_ONE_REST_URL
                oauth_url = "https://auth-east.bullhornstaffing.com/oauth"  # Base for logging
            else:
                # Legacy: Get login info to determine correct data center
                login_info_url = self.LEGACY_LOGIN_INFO_URL
                login_info_params = {'username': self.username}
                
                response = self.session.get(login_info_url, params=login_info_params, timeout=30)
                logger.info(f"Login info request to {login_info_url} with username: {self.username}")
                if response.status_code != 200:
                    logger.error(f"Failed to get login info: {response.status_code} - {response.text}")
                    return False
                
                login_data = self._safe_json_parse(response)
                logger.info(f"Login info response: {login_data}")
                
                # Extract the authorization URL and REST URL
                if 'oauthUrl' not in login_data or 'restUrl' not in login_data:
                    logger.error("Missing oauthUrl or restUrl in login info response")
                    return False
                
                oauth_url = login_data['oauthUrl']
                rest_url = login_data['restUrl']
                auth_endpoint = f"{oauth_url}/authorize"
                token_endpoint = f"{oauth_url}/token"
                rest_login_url = f"{rest_url}/login"
            
            # Step 2: Get authorization code
            
            # Get the current domain for redirect URI - this must match what's whitelisted with Bullhorn
            # Note: Bullhorn Support must whitelist the exact redirect URI for your domain
            from urllib.parse import urljoin
            import os
            
            # Use environment variable or auto-detect current domain
            base_url = os.environ.get('OAUTH_REDIRECT_BASE_URL')
            if not base_url:
                # Auto-detect from current environment (fallback to production URL)
                base_url = "https://jobpulse.lyntrix.ai"  # Production deployment URL
            else:
                base_url = base_url.strip()  # Remove any whitespace from env var
            
            redirect_uri = f"{base_url}/bullhorn/oauth/callback"
            
            auth_params = {
                'client_id': self.client_id,
                'response_type': 'code',
                'redirect_uri': redirect_uri,
                'username': self.username,
                'password': self.password,
                'action': 'Login'
            }
            
            logger.info(f"Using redirect URI: {redirect_uri}")
            logger.info(f"Auth endpoint: {auth_endpoint}")
            
            auth_response = self.session.get(auth_endpoint, params=auth_params, allow_redirects=False, timeout=30)
            logger.info(f"Auth response status: {auth_response.status_code}")
            logger.info(f"Auth response headers: [redacted for security]")
            
            if auth_response.status_code == 302:
                # Check for authorization code in redirect
                location = auth_response.headers.get('Location', '')
                if 'code=' in location:
                    auth_code = location.split('code=')[1].split('&')[0]
                    # URL decode the auth code
                    from urllib.parse import unquote
                    auth_code = unquote(auth_code)
                    logger.info(f"Got authorization code (first 10 chars): {auth_code[:10]}...")
                elif 'error=' in location:
                    error = location.split('error=')[1].split('&')[0]
                    logger.error(f"OAuth authorization failed: {error}")
                    return False
                else:
                    logger.error("No authorization code found in redirect")
                    return False
            else:
                # Try to extract from response content for different OAuth implementations
                response_text = auth_response.text
                logger.info(f"Auth response text (first 500 chars): {response_text[:500]}")
                
                if '"code":"' in response_text:
                    import re
                    code_match = re.search(r'"code":"([^"]+)"', response_text)
                    auth_code = code_match.group(1) if code_match else None
                elif 'code=' in response_text:
                    # Try URL parameter style
                    import re
                    code_match = re.search(r'code=([^&\s]+)', response_text)
                    auth_code = code_match.group(1) if code_match else None
                else:
                    logger.error(f"Unexpected auth response: {auth_response.status_code}")
                    logger.error(f"Full response text: {response_text}")
                    return False
            
            if not auth_code:
                logger.error("Failed to obtain authorization code")
                return False
            
            # Step 3: Exchange authorization code for access token
            # Include redirect_uri to match authorization request (required for this setup)
            # token_endpoint was set above based on Bullhorn One vs Legacy mode
            token_data = {
                'grant_type': 'authorization_code',
                'code': auth_code,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'redirect_uri': redirect_uri  # Must match the authorization request
            }
            
            # Set explicit headers for token exchange
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            
            logger.info(f"Exchanging auth code at token endpoint: {token_endpoint}")
            token_response = self.session.post(token_endpoint, data=token_data, headers=headers, timeout=30)
            if token_response.status_code != 200:
                logger.error(f"Failed to get access token: HTTP {token_response.status_code}")
                return False
            
            token_info = self._safe_json_parse(token_response)
            access_token = token_info.get('access_token')
            self.access_token = access_token  # Persist as instance attribute for health checks
            
            if not access_token:
                logger.error("No access token in response")
                return False
            
            # Step 4: Get REST token for API access
            # rest_login_url was set above based on Bullhorn One vs Legacy mode
            rest_params = {
                'version': '2.0',
                'access_token': access_token
            }
            
            logger.info(f"Getting REST token from: {rest_login_url}")
            rest_response = self.session.post(rest_login_url, params=rest_params, timeout=30)
            if rest_response.status_code != 200:
                logger.error(f"Failed to get REST token: HTTP {rest_response.status_code}")
                return False
            
            rest_data = self._safe_json_parse(rest_response)
            self.rest_token = rest_data.get('BhRestToken')
            # Extract the actual REST URL from the response, or use the fixed URL for Bullhorn One
            self.base_url = rest_data.get('restUrl', rest_url)
            # Store user ID for note creation (commentingPerson field)
            # Bullhorn may return it as 'userId' or 'corporateUserId'
            self.user_id = rest_data.get('userId') or rest_data.get('corporateUserId')
            
            # Log all keys in REST response for debugging
            logger.info(f"REST login response keys: {list(rest_data.keys())}")
            
            if not self.rest_token:
                logger.error("No REST token in response")
                return False
            
            logger.info(f"Bullhorn authentication successful. Base URL: {self.base_url}")
            logger.info(f"REST Token: ***{self.rest_token[-4:]}")
            
            # If user ID not in login response, try to fetch it
            if not self.user_id:
                logger.warning(f"⚠️ No userId in REST response, querying current user...")
                self.user_id = self._get_current_user_id()
            
            if self.user_id:
                logger.info(f"User ID for note creation: {self.user_id}")
            else:
                logger.warning(f"⚠️ Could not obtain userId - note creation will use minimal approach")
            return True
            
        except Exception as e:
            logger.error(f"Direct login failed: {str(e)}")
            import traceback
            traceback_str = traceback.format_exc()
            logger.error(f"Traceback: {traceback_str}")
            # Log more details about the failure
            logger.error(f"OAuth URL: {oauth_url if oauth_url else 'Not set'}")
            logger.error(f"Auth endpoint: {auth_endpoint if auth_endpoint else 'Not set'}")
            logger.error(f"Redirect URI: {redirect_uri if redirect_uri else 'Not set'}")
            
            # Clear any partial authentication state
            self.rest_token = None
            self.base_url = None
            return False
    def test_connection(self) -> bool:
        """
        Test connection to Bullhorn API
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        if not all([self.client_id, self.username]):
            logger.error("Missing client_id or username for connection test")
            return False
            
        try:
            # Test authentication
            if not self.authenticate():
                logger.error("Authentication failed during connection test")
                return False
            
            # If we have valid authentication tokens, consider connection successful
            # This prevents monitoring failures due to temporary API issues
            if self.rest_token and self.base_url:
                logger.info("Connection test passed - valid authentication credentials available")
                
                # Optional: Try a simple API call, but don't fail if it has issues
                try:
                    url = f"{self.base_url}search/JobOrder"
                    params = {
                        'query': 'id:[1 TO 999999]',
                        'fields': 'id',
                        'count': 1,
                        'BhRestToken': self.rest_token
                    }
                    
                    response = self.session.get(url, params=params, timeout=15)
                    if response.status_code == 200:
                        logger.info("Full API connection test successful")
                    else:
                        logger.warning(f"API test returned {response.status_code}, but authentication valid - continuing")
                        
                except Exception as api_e:
                    logger.warning(f"API test call failed but authentication succeeded: {str(api_e)} - continuing")
                
                return True  # Return success if authentication worked
            else:
                logger.error("Missing authentication tokens after successful authenticate() call")
                return False
            
        except Exception as e:
            logger.error(f"Bullhorn connection test failed: {str(e)}")
            return False
