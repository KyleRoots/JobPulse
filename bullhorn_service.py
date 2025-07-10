import os
import logging
import requests
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

class BullhornService:
    """Service for interacting with Bullhorn ATS/CRM API"""
    
    def __init__(self):
        self.base_url = None
        self.rest_token = None
        self.client_id = None
        self.client_secret = None
        self.username = None
        self.password = None
        self.session = requests.Session()
        self._auth_in_progress = False
        self._last_auth_attempt = None
        self._load_credentials()
    
    def _load_credentials(self):
        """Load Bullhorn credentials from Global Settings"""
        try:
            # Import here to avoid circular imports
            from app import GlobalSettings
            
            settings_map = {
                'bullhorn_client_id': 'client_id',
                'bullhorn_client_secret': 'client_secret', 
                'bullhorn_username': 'username',
                'bullhorn_password': 'password'
            }
            
            for setting_key, attr_name in settings_map.items():
                setting = GlobalSettings.query.filter_by(setting_key=setting_key).first()
                if setting and setting.setting_value:
                    setattr(self, attr_name, setting.setting_value.strip())
                    
        except Exception as e:
            logging.warning(f"Could not load Bullhorn credentials: {str(e)}")
        
    def authenticate(self) -> bool:
        """
        Authenticate with Bullhorn using OAuth 2.0 flow
        
        Returns:
            bool: True if authentication successful, False otherwise
        """
        # Check if we already have a valid token
        if self.rest_token and self.base_url:
            # Verify the token is still valid
            try:
                url = f"{self.base_url}/ping"
                params = {'BhRestToken': self.rest_token}
                response = self.session.get(url, params=params)
                if response.status_code == 200:
                    logging.info("Using existing valid Bullhorn token")
                    return True
            except:
                pass
        
        # Prevent concurrent authentication attempts
        if self._auth_in_progress:
            logging.warning("Authentication already in progress, skipping duplicate attempt")
            return False
            
        # Check if we just tried to authenticate (within last 5 seconds)
        if self._last_auth_attempt:
            time_since_last = datetime.now() - self._last_auth_attempt
            if time_since_last.total_seconds() < 5:
                logging.warning("Recent authentication attempt detected, skipping to prevent code reuse")
                return False
        
        if not all([self.client_id, self.client_secret, self.username, self.password]):
            logging.error("Missing Bullhorn credentials")
            return False
            
        try:
            self._auth_in_progress = True
            self._last_auth_attempt = datetime.now()
            # Try direct login first (simpler for API access)
            return self._direct_login()
            
        except Exception as e:
            logging.error(f"Bullhorn authentication failed: {str(e)}")
            return False
        finally:
            self._auth_in_progress = False
    
    def _direct_login(self) -> bool:
        """
        Complete Bullhorn OAuth 2.0 authentication flow
        
        Returns:
            bool: True if login successful, False otherwise
        """
        try:
            # Step 1: Get login info to determine correct data center
            login_info_url = "https://rest.bullhornstaffing.com/rest-services/loginInfo"
            login_info_params = {'username': self.username}
            
            response = self.session.get(login_info_url, params=login_info_params)
            logging.info(f"Login info request to {login_info_url} with username: {self.username}")
            if response.status_code != 200:
                logging.error(f"Failed to get login info: {response.status_code} - {response.text}")
                return False
            
            login_data = response.json()
            logging.info(f"Login info response: {login_data}")
            
            # Extract the authorization URL and REST URL
            if 'oauthUrl' not in login_data or 'restUrl' not in login_data:
                logging.error("Missing oauthUrl or restUrl in login info response")
                return False
            
            oauth_url = login_data['oauthUrl']
            rest_url = login_data['restUrl']
            
            # Step 2: Get authorization code
            auth_endpoint = f"{oauth_url}/authorize"
            
            # Use the whitelisted redirect URI based on environment
            # Bullhorn has whitelisted these specific URLs:
            # - https://workspace.kyleroots00.replit.app/bullhorn/oauth/callback
            # - https://49cc4d39-c8af-4fa8-8edf-ea2819b0c88a-00-1b6n9tc9un0ln.janeway.replit.dev/bullhorn/oauth/callback
            # - https://myticas.com/bullhorn_oauth_callback.php
            
            # Determine the redirect URI based on the environment
            # First check for a hardcoded domain in environment variable
            if os.environ.get('BULLHORN_REDIRECT_DOMAIN'):
                redirect_uri = f"https://{os.environ.get('BULLHORN_REDIRECT_DOMAIN')}/bullhorn/oauth/callback"
            # Otherwise use one of the whitelisted URLs
            elif os.environ.get('REPL_SLUG') == 'workspace':
                redirect_uri = "https://workspace.kyleroots00.replit.app/bullhorn/oauth/callback"
            else:
                # Fallback to the dev URL that was whitelisted
                redirect_uri = "https://49cc4d39-c8af-4fa8-8edf-ea2819b0c88a-00-1b6n9tc9un0ln.janeway.replit.dev/bullhorn/oauth/callback"
            
            auth_params = {
                'client_id': self.client_id,
                'response_type': 'code',
                'redirect_uri': redirect_uri,
                'username': self.username,
                'password': self.password,
                'action': 'Login'
            }
            
            logging.info(f"Using redirect URI: {redirect_uri}")
            logging.info(f"Auth endpoint: {auth_endpoint}")
            
            auth_response = self.session.post(auth_endpoint, data=auth_params, allow_redirects=False)
            logging.info(f"Auth response status: {auth_response.status_code}")
            logging.info(f"Auth response headers: {dict(auth_response.headers)}")
            
            if auth_response.status_code == 302:
                # Check for authorization code in redirect
                location = auth_response.headers.get('Location', '')
                if 'code=' in location:
                    auth_code = location.split('code=')[1].split('&')[0]
                    # URL decode the auth code
                    from urllib.parse import unquote
                    auth_code = unquote(auth_code)
                    logging.info(f"Got authorization code (first 10 chars): {auth_code[:10]}...")
                elif 'error=' in location:
                    error = location.split('error=')[1].split('&')[0]
                    logging.error(f"OAuth authorization failed: {error}")
                    return False
                else:
                    logging.error("No authorization code found in redirect")
                    return False
            else:
                # Try to extract from response content for different OAuth implementations
                response_text = auth_response.text
                logging.info(f"Auth response text (first 500 chars): {response_text[:500]}")
                
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
                    logging.error(f"Unexpected auth response: {auth_response.status_code}")
                    logging.error(f"Full response text: {response_text}")
                    return False
            
            if not auth_code:
                logging.error("Failed to obtain authorization code")
                return False
            
            # Step 3: Exchange authorization code for access token
            token_endpoint = f"{oauth_url}/token"
            token_data = {
                'grant_type': 'authorization_code',
                'code': auth_code,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'redirect_uri': redirect_uri
            }
            
            token_response = self.session.post(token_endpoint, data=token_data)
            if token_response.status_code != 200:
                logging.error(f"Failed to get access token: {token_response.status_code} - {token_response.text}")
                return False
            
            token_info = token_response.json()
            access_token = token_info.get('access_token')
            
            if not access_token:
                logging.error("No access token in response")
                return False
            
            # Step 4: Get REST token for API access
            rest_login_endpoint = f"{rest_url}/login"
            rest_params = {
                'version': '2.0',
                'access_token': access_token
            }
            
            rest_response = self.session.post(rest_login_endpoint, params=rest_params)
            if rest_response.status_code != 200:
                logging.error(f"Failed to get REST token: {rest_response.status_code} - {rest_response.text}")
                return False
            
            rest_data = rest_response.json()
            self.rest_token = rest_data.get('BhRestToken')
            self.base_url = rest_url
            
            if not self.rest_token:
                logging.error("No REST token in response")
                return False
            
            logging.info(f"Bullhorn authentication successful. Base URL: {self.base_url}")
            return True
            
        except Exception as e:
            logging.error(f"Direct login failed: {str(e)}")
            import traceback
            traceback_str = traceback.format_exc()
            logging.error(f"Traceback: {traceback_str}")
            # Log more details about the failure
            logging.error(f"OAuth URL: {oauth_url if 'oauth_url' in locals() else 'Not set'}")
            logging.error(f"Auth endpoint: {auth_endpoint if 'auth_endpoint' in locals() else 'Not set'}")
            logging.error(f"Redirect URI: {redirect_uri if 'redirect_uri' in locals() else 'Not set'}")
            
            # Clear any partial authentication state
            self.rest_token = None
            self.base_url = None
            return False
    
    def get_job_orders(self, last_modified_since: Optional[datetime] = None) -> List[Dict]:
        """
        Get JobOrder entities from Bullhorn
        
        Args:
            last_modified_since: Only return jobs modified since this datetime
            
        Returns:
            List[Dict]: List of job order dictionaries
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        try:
            # Build query
            query_parts = ["isDeleted:0"]
            
            if last_modified_since:
                # Format datetime for Bullhorn (yyyyMMddHHmmss)
                date_str = last_modified_since.strftime('%Y%m%d%H%M%S')
                query_parts.append(f"dateLastModified:[{date_str} TO *]")
            
            query = " AND ".join(query_parts)
            
            # Define fields to retrieve
            fields = [
                "id", "title", "isOpen", "status", "dateAdded", 
                "dateLastModified", "clientCorporation(id,name)",
                "clientContact(firstName,lastName)", "description",
                "publicDescription", "numOpenings", "isPublic"
            ]
            
            # Make API request
            url = f"{self.base_url}/search/JobOrder"
            params = {
                'query': query,
                'fields': ','.join(fields),
                'sort': '-dateLastModified',
                'count': 100,
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('data', [])
            else:
                logging.error(f"Failed to get job orders: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logging.error(f"Error getting job orders: {str(e)}")
            return []
    
    def get_tearsheet_jobs(self, tearsheet_id: int) -> List[Dict]:
        """
        Get all jobs associated with a specific tearsheet
        
        Args:
            tearsheet_id: The ID of the tearsheet to monitor
            
        Returns:
            List[Dict]: List of job dictionaries in the tearsheet
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        try:
            # Get tearsheet with associated jobs
            url = f"{self.base_url}/entity/Tearsheet/{tearsheet_id}"
            params = {
                'fields': 'id,name,jobOrders(id,title,isOpen,status,dateAdded,dateLastModified)',
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('data', {}).get('jobOrders', {}).get('data', [])
            else:
                logging.error(f"Failed to get tearsheet jobs: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logging.error(f"Error getting tearsheet jobs: {str(e)}")
            return []
    
    def get_tearsheets(self) -> List[Dict]:
        """
        Get all tearsheets from Bullhorn
        
        Returns:
            List[Dict]: List of tearsheet dictionaries
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        try:
            url = f"{self.base_url}/search/Tearsheet"
            params = {
                'query': 'isDeleted:0',
                'fields': 'id,name,dateAdded,dateLastModified,jobOrders(total)',
                'sort': '-dateLastModified',
                'count': 50,
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('data', [])
            else:
                logging.error(f"Failed to get tearsheets: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logging.error(f"Error getting tearsheets: {str(e)}")
            return []
    
    def get_tearsheet_by_name(self, name: str) -> Optional[Dict]:
        """
        Get a specific tearsheet by name
        
        Args:
            name: Name of the tearsheet to find
            
        Returns:
            Dict: Tearsheet dictionary if found, None otherwise
        """
        tearsheets = self.get_tearsheets()
        
        for tearsheet in tearsheets:
            if tearsheet.get('name', '').lower() == name.lower():
                return tearsheet
                
        return None
    
    def test_connection(self) -> bool:
        """
        Test connection to Bullhorn API
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            if not self.client_id or not self.username:
                logging.error("Missing client_id or username for Bullhorn connection test")
                return False
            
            # Test authentication
            if self.authenticate():
                # Test a simple API call - use a search query that should always work
                url = f"{self.base_url}/search/Tearsheet"
                params = {
                    'query': 'id:[* TO *]',  # This query matches any tearsheet
                    'fields': 'id',
                    'count': 1,
                    'BhRestToken': self.rest_token
                }
                
                logging.info(f"Test connection URL: {url}")
                logging.info(f"Test connection params: {params}")
                response = self.session.get(url, params=params)
                logging.info(f"Test connection response: {response.status_code}")
                if response.status_code == 200:
                    logging.info("Bullhorn connection test successful")
                    return True
                else:
                    logging.error(f"Test connection failed: {response.status_code} - {response.text}")
                    return False
            
            logging.error("Authentication failed in test_connection")
            return False
            
        except Exception as e:
            logging.error(f"Bullhorn connection test failed: {str(e)}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            return False
    
    def compare_job_lists(self, previous_jobs: List[Dict], current_jobs: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Compare two job lists to find added, removed, and modified jobs
        
        Args:
            previous_jobs: List of jobs from previous check
            current_jobs: List of jobs from current check
            
        Returns:
            Dict with 'added', 'removed', 'modified', and 'summary' lists
        """
        # Create lookup dictionaries for efficient comparison
        previous_lookup = {job['id']: job for job in previous_jobs}
        current_lookup = {job['id']: job for job in current_jobs}
        
        previous_ids = set(previous_lookup.keys())
        current_ids = set(current_lookup.keys())
        
        # Find added and removed jobs
        added_ids = current_ids - previous_ids
        removed_ids = previous_ids - current_ids
        common_ids = previous_ids & current_ids
        
        added_jobs = [current_lookup[job_id] for job_id in added_ids]
        removed_jobs = [previous_lookup[job_id] for job_id in removed_ids]
        
        # Check for modifications in existing jobs
        modified_jobs = []
        for job_id in common_ids:
            prev_job = previous_lookup[job_id]
            curr_job = current_lookup[job_id]
            
            # Check key fields for changes
            changes = []
            fields_to_check = ['title', 'status', 'isOpen', 'numOpenings', 'dateLastModified']
            
            for field in fields_to_check:
                prev_val = prev_job.get(field)
                curr_val = curr_job.get(field)
                if prev_val != curr_val:
                    changes.append({
                        'field': field,
                        'from': prev_val,
                        'to': curr_val
                    })
            
            if changes:
                modified_job = curr_job.copy()
                modified_job['changes'] = changes
                modified_jobs.append(modified_job)
        
        # Create summary statistics
        summary = {
            'total_previous': len(previous_jobs),
            'total_current': len(current_jobs),
            'added_count': len(added_jobs),
            'removed_count': len(removed_jobs),
            'modified_count': len(modified_jobs),
            'net_change': len(current_jobs) - len(previous_jobs)
        }
        
        return {
            'added': added_jobs,
            'removed': removed_jobs,
            'modified': modified_jobs,
            'summary': summary
        }