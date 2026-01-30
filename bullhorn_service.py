import os
import logging
import requests
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

class BullhornService:
    """Service for interacting with Bullhorn ATS/CRM API"""
    
    # Bullhorn One (new) API endpoints - used when BULLHORN_USE_NEW_API=true
    BULLHORN_ONE_AUTH_URL = "https://auth-east.bullhornstaffing.com/oauth/authorize"
    BULLHORN_ONE_TOKEN_URL = "https://auth-east.bullhornstaffing.com/oauth/token"
    BULLHORN_ONE_REST_LOGIN_URL = "https://rest-east.bullhornstaffing.com/rest-services/login"
    BULLHORN_ONE_REST_URL = "https://rest45.bullhornstaffing.com/rest-services/dcc900/"
    
    # Legacy Bullhorn API endpoint (for loginInfo discovery)
    LEGACY_LOGIN_INFO_URL = "https://rest.bullhornstaffing.com/rest-services/loginInfo"
    
    def __init__(self, client_id=None, client_secret=None, username=None, password=None):
        self.base_url = None
        self.rest_token = None
        self.user_id = None  # Corporate user ID for note creation
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'MyticasJobFeedAutomation/1.0',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })
        self._auth_in_progress = False
        self._last_auth_attempt = None
        
        # Check if we should use Bullhorn One (new) API
        self.use_bullhorn_one = os.environ.get('BULLHORN_USE_NEW_API', 'false').lower() == 'true'
        if self.use_bullhorn_one:
            logging.info("🔄 Bullhorn One API mode ENABLED - using new fixed endpoints")
            logging.info(f"   Auth URL: {self.BULLHORN_ONE_AUTH_URL}")
            logging.info(f"   Token URL: {self.BULLHORN_ONE_TOKEN_URL}")
            logging.info(f"   REST Login URL: {self.BULLHORN_ONE_REST_LOGIN_URL}")
            logging.info(f"   REST URL: {self.BULLHORN_ONE_REST_URL}")
        
        # Job exclusion configuration - specific job IDs to filter out
        self.excluded_job_ids = {31939, 34287}  # Set for O(1) lookup
        self.excluded_count = 0  # Track how many jobs were excluded in last fetch
        
        # Only load from DB if no credentials provided
        if not any([client_id, client_secret, username, password]):
            self._load_credentials()
    
    def _safe_json_parse(self, response):
        """
        Safely parse JSON response and detect HTML error pages
        
        Args:
            response: requests.Response object
            
        Returns:
            dict: Parsed JSON data or raises appropriate exception
            
        Raises:
            Exception: If response contains HTML or invalid JSON
        """
        try:
            content_type = response.headers.get('content-type', '').lower()
            text_content = response.text.strip()
            
            # Check if response is HTML (common when authentication fails)
            if ('text/html' in content_type or 
                text_content.startswith('<!DOCTYPE') or 
                text_content.startswith('<html')):
                logging.warning(f"Received HTML response instead of JSON. Status: {response.status_code}")
                raise Exception("Authentication failed: Received HTML login page instead of JSON data. Please refresh and try again.")
            
            # Attempt to parse JSON
            return response.json()
            
        except json.JSONDecodeError as e:
            logging.error(f"Invalid JSON response: {str(e)[:200]}")
            raise Exception(f"Invalid API response format. Please try again.")
        except Exception as e:
            if "Authentication failed" in str(e):
                raise e  # Re-raise authentication errors as-is
            logging.error(f"Error parsing API response: {str(e)}")
            raise Exception("API response parsing error. Please refresh and try again.")
    
    def _load_credentials(self):
        """Load Bullhorn credentials from database GlobalSettings"""
        try:
            from app import app
            from models import GlobalSettings
            
            with app.app_context():
                client_id_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_id').first()
                client_secret_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_client_secret').first()
                username_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_username').first()
                password_setting = GlobalSettings.query.filter_by(setting_key='bullhorn_password').first()
                
                if client_id_setting:
                    self.client_id = client_id_setting.setting_value
                if client_secret_setting:
                    self.client_secret = client_secret_setting.setting_value
                if username_setting:
                    self.username = username_setting.setting_value
                if password_setting:
                    self.password = password_setting.setting_value
        except Exception as e:
            logging.warning(f"Could not load Bullhorn credentials from database: {str(e)}")
    
    def _filter_excluded_jobs(self, jobs: List[Dict]) -> List[Dict]:
        """
        Filter out jobs with IDs in the exclusion list
        
        Args:
            jobs: List of job dictionaries
            
        Returns:
            List[Dict]: Filtered job list with excluded jobs removed
        """
        if not jobs or not self.excluded_job_ids:
            self.excluded_count = 0
            return jobs
        
        original_count = len(jobs)
        filtered_jobs = [job for job in jobs if job.get('id') not in self.excluded_job_ids]
        self.excluded_count = original_count - len(filtered_jobs)
        
        if self.excluded_count > 0:
            excluded_ids = [job.get('id') for job in jobs if job.get('id') in self.excluded_job_ids]
            logging.info(f"🚫 Excluded {self.excluded_count} jobs from processing: {excluded_ids}")
        
        return filtered_jobs
        
    def authenticate(self) -> bool:
        """
        Authenticate with Bullhorn using OAuth 2.0 flow
        
        Returns:
            bool: True if authentication successful, False otherwise
        """
        # Force fresh authentication by clearing any cached tokens
        self.rest_token = None
        self.base_url = None
        
        # Prevent concurrent authentication attempts
        if self._auth_in_progress:
            logging.warning("Authentication already in progress, skipping duplicate attempt")
            return False
            
        # Check if we just tried to authenticate (within last 30 seconds - extended to handle server issues)
        if self._last_auth_attempt:
            time_since_last = datetime.now() - self._last_auth_attempt
            if time_since_last.total_seconds() < 30:
                logging.warning(f"Recent authentication attempt detected ({time_since_last.total_seconds():.1f}s ago), skipping to prevent overload")
                return False
        
        if not all([self.client_id, self.client_secret, self.username, self.password]):
            missing = []
            if not self.client_id: missing.append('client_id')
            if not self.client_secret: missing.append('client_secret')
            if not self.username: missing.append('username')
            if not self.password: missing.append('password')
            api_mode = 'Bullhorn One' if self.use_bullhorn_one else 'Legacy Bullhorn'
            logging.error(f"Missing {api_mode} credentials: {', '.join(missing)}")
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
                    logging.info(f"Got user ID from settings endpoint: {user_id}")
                    return int(user_id)
            
            # Alternative: Try to get from userInfo
            url = f"{self.base_url}userInfo"
            response = self.session.get(url, params=params, timeout=15)
            if response.status_code == 200:
                data = self._safe_json_parse(response)
                user_id = data.get('id') or data.get('userId')
                if user_id:
                    logging.info(f"Got user ID from userInfo endpoint: {user_id}")
                    return int(user_id)
                    
        except Exception as e:
            logging.warning(f"Could not query current user ID: {e}")
        
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
                logging.info("🔄 Using Bullhorn One fixed endpoints for authentication")
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
                logging.info(f"Login info request to {login_info_url} with username: {self.username}")
                if response.status_code != 200:
                    logging.error(f"Failed to get login info: {response.status_code} - {response.text}")
                    return False
                
                login_data = self._safe_json_parse(response)
                logging.info(f"Login info response: {login_data}")
                
                # Extract the authorization URL and REST URL
                if 'oauthUrl' not in login_data or 'restUrl' not in login_data:
                    logging.error("Missing oauthUrl or restUrl in login info response")
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
            
            logging.info(f"Using redirect URI: {redirect_uri}")
            logging.info(f"Auth endpoint: {auth_endpoint}")
            
            auth_response = self.session.get(auth_endpoint, params=auth_params, allow_redirects=False, timeout=30)
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
            
            logging.info(f"Exchanging auth code at token endpoint: {token_endpoint}")
            token_response = self.session.post(token_endpoint, data=token_data, headers=headers, timeout=30)
            if token_response.status_code != 200:
                logging.error(f"Failed to get access token: {token_response.status_code} - {token_response.text}")
                return False
            
            token_info = self._safe_json_parse(token_response)
            access_token = token_info.get('access_token')
            
            if not access_token:
                logging.error("No access token in response")
                return False
            
            # Step 4: Get REST token for API access
            # rest_login_url was set above based on Bullhorn One vs Legacy mode
            rest_params = {
                'version': '2.0',
                'access_token': access_token
            }
            
            logging.info(f"Getting REST token from: {rest_login_url}")
            rest_response = self.session.post(rest_login_url, params=rest_params, timeout=30)
            if rest_response.status_code != 200:
                logging.error(f"Failed to get REST token: {rest_response.status_code} - {rest_response.text}")
                return False
            
            rest_data = self._safe_json_parse(rest_response)
            self.rest_token = rest_data.get('BhRestToken')
            # Extract the actual REST URL from the response, or use the fixed URL for Bullhorn One
            self.base_url = rest_data.get('restUrl', rest_url)
            # Store user ID for note creation (commentingPerson field)
            # Bullhorn may return it as 'userId' or 'corporateUserId'
            self.user_id = rest_data.get('userId') or rest_data.get('corporateUserId')
            
            # Log all keys in REST response for debugging
            logging.info(f"REST login response keys: {list(rest_data.keys())}")
            
            if not self.rest_token:
                logging.error("No REST token in response")
                return False
            
            logging.info(f"Bullhorn authentication successful. Base URL: {self.base_url}")
            logging.info(f"REST Token (first 20 chars): {self.rest_token[:20]}...")
            
            # If user ID not in login response, try to fetch it
            if not self.user_id:
                logging.warning(f"⚠️ No userId in REST response, querying current user...")
                self.user_id = self._get_current_user_id()
            
            if self.user_id:
                logging.info(f"User ID for note creation: {self.user_id}")
            else:
                logging.warning(f"⚠️ Could not obtain userId - note creation will use minimal approach")
            return True
            
        except Exception as e:
            logging.error(f"Direct login failed: {str(e)}")
            import traceback
            traceback_str = traceback.format_exc()
            logging.error(f"Traceback: {traceback_str}")
            # Log more details about the failure
            logging.error(f"OAuth URL: {oauth_url if oauth_url else 'Not set'}")
            logging.error(f"Auth endpoint: {auth_endpoint if auth_endpoint else 'Not set'}")
            logging.error(f"Redirect URI: {redirect_uri if redirect_uri else 'Not set'}")
            
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
            
            response = self.session.get(url, params=params, timeout=30)
            
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
            # First get the entity API count for validation
            entity_url = f"{self.base_url}entity/Tearsheet/{tearsheet_id}"
            entity_params = {
                'fields': 'id,name,jobOrders(id,title,isOpen,status,dateAdded,dateLastModified,clientCorporation(name),publicDescription,address(city,state,countryName),employmentType,onSite,assignedUsers(id,firstName,lastName,email),responseUser(firstName,lastName),owner(firstName,lastName))',
                'BhRestToken': self.rest_token
            }
            
            entity_response = self.session.get(entity_url, params=entity_params, timeout=60)
            entity_total = 0
            entity_job_ids = set()
            
            if entity_response.status_code == 200:
                entity_data = entity_response.json()
                job_orders = entity_data.get('data', {}).get('jobOrders', {})
                if isinstance(job_orders, dict):
                    entity_total = job_orders.get('total', 0)
                    logging.info(f"Tearsheet {tearsheet_id}: Entity API reports {entity_total} total jobs")
                    
                    # Store Entity API job IDs from first page
                    entity_jobs_data = job_orders.get('data', [])
                    entity_job_ids = {job.get('id') for job in entity_jobs_data if job.get('id')}
                    
                    # If there are 5 or fewer jobs, use the entity API data directly (no pagination needed)
                    if entity_total <= 5:
                        return self._filter_excluded_jobs(entity_jobs_data)
                    
                    # For larger tearsheets, fetch ALL Entity job IDs with pagination (for orphan detection)
                    if entity_total > len(entity_job_ids):
                        # Use association endpoint for proper pagination
                        assoc_url = f"{self.base_url}entity/Tearsheet/{tearsheet_id}/jobOrders"
                        entity_start = len(entity_job_ids)
                        page_size = 200
                        
                        while entity_start < entity_total:
                            assoc_params = {
                                'fields': 'id',
                                'start': entity_start,
                                'count': page_size,
                                'BhRestToken': self.rest_token
                            }
                            paginated_response = self.session.get(assoc_url, params=assoc_params, timeout=60)
                            if paginated_response.status_code == 200:
                                paginated_data = paginated_response.json()
                                paginated_jobs = paginated_data.get('data', [])
                                if not paginated_jobs:
                                    break
                                entity_job_ids.update({job.get('id') for job in paginated_jobs if job.get('id')})
                                entity_start += len(paginated_jobs)
                                if len(paginated_jobs) < page_size:
                                    break
                            else:
                                logging.warning(f"Failed to fetch Entity API page at start={entity_start}: {paginated_response.status_code}")
                                break
                        
                        # Safeguard: Abort orphan filtering if we didn't collect all Entity IDs
                        if len(entity_job_ids) < entity_total:
                            logging.error(f"Tearsheet {tearsheet_id}: Entity pagination incomplete! Collected {len(entity_job_ids)} IDs but Entity API reports {entity_total}. Aborting orphan filtering to prevent data loss.")
                            entity_job_ids = set()  # Clear IDs to disable orphan filtering
            
            # For larger tearsheets, use search API to get all jobs
            query = f"tearsheets.id:{tearsheet_id}"
            
            # Define fields to retrieve (enhanced for XML mapping)
            fields = [
                "id", "title", "isOpen", "status", "dateAdded", 
                "dateLastModified", "clientCorporation(id,name)",
                "clientContact(firstName,lastName)", "description",
                "publicDescription", "numOpenings", "isPublic",
                "address(city,state,countryName)", "employmentType",
                "salary", "salaryUnit", "isDeleted",
                "categories(id,name)", "onSite", "benefits", "bonusPackage",
                "degreeList", "skillList", "certificationList",
                "owner(firstName,lastName)",
                "assignedUsers(firstName,lastName)",
                "responseUser(firstName,lastName)"
            ]
            
            all_jobs = []
            start = 0
            count = 200  # Max records per page
            
            while True:
                # Make API request using search endpoint
                url = f"{self.base_url}search/JobOrder"
                params = {
                    'query': query,
                    'fields': ','.join(fields),
                    'sort': '-dateLastModified',
                    'start': start,
                    'count': count,
                    'BhRestToken': self.rest_token
                }
                
                response = self.session.get(url, params=params, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    jobs_data = data.get('data', [])
                    total = data.get('total', 0)
                    
                    # Add jobs from this page
                    all_jobs.extend(jobs_data)
                    
                    # Check if we need to fetch more pages
                    if len(jobs_data) < count or len(all_jobs) >= total:
                        break
                    
                    start += count
                else:
                    logging.error(f"Failed to get tearsheet jobs: {response.status_code} - {response.text}")
                    break
            # Apply job exclusion filter
            filtered_jobs = self._filter_excluded_jobs(all_jobs)
            
            # Handle discrepancies between Entity API and Search API
            if entity_total > 0 and len(all_jobs) != entity_total:
                if entity_total < len(all_jobs):
                    # Entity API shows FEWER jobs → Jobs were removed from tearsheet
                    # Trust Entity API (authoritative source) and filter out orphaned jobs
                    if entity_job_ids:
                        orphaned_jobs = [j for j in filtered_jobs if j.get('id') not in entity_job_ids]
                        filtered_jobs = [j for j in filtered_jobs if j.get('id') in entity_job_ids]
                        logging.info(f"Tearsheet {tearsheet_id}: Entity API shows {entity_total} jobs but Search API returned {len(all_jobs)}. Removed {len(orphaned_jobs)} orphaned jobs. Using Entity API as source of truth.")
                    else:
                        logging.warning(f"Tearsheet {tearsheet_id}: Entity API shows {entity_total} jobs but Search API returned {len(all_jobs)}. No Entity job IDs available for filtering.")
                else:
                    # Entity API shows MORE jobs → Search API pagination issue
                    # Trust Search API results (more complete due to full field retrieval)
                    logging.info(f"Tearsheet {tearsheet_id}: Search API returned {len(all_jobs)} jobs while Entity API indicates {entity_total}. Using Search API results (more complete field data).")
            
            return filtered_jobs
                
        except Exception as e:
            logging.error(f"Error getting tearsheet jobs: {str(e)}")
            return []
    
    def get_jobs_by_query(self, query: str) -> List[Dict]:
        """
        Get jobs using a custom search query with proper pagination
        
        Args:
            query: Bullhorn search query (e.g., "status:Open AND isPublic:1")
            
        Returns:
            List[Dict]: List of job dictionaries matching the query
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        try:
            # Define fields to retrieve
            fields = [
                "id", "title", "isOpen", "status", "dateAdded", 
                "dateLastModified", "clientCorporation(id,name)",
                "clientContact(firstName,lastName)", "description",
                "publicDescription", "numOpenings", "isPublic",
                "address(city,state,countryName)",
                "employmentType", "onSite",
                "assignedUsers(firstName,lastName)",
                "responseUser(firstName,lastName)",
                "owner(firstName,lastName)"
            ]
            
            # Implement pagination to get ALL jobs
            all_jobs = []
            start = 0
            count = 200  # Max records per page
            
            while True:
                # Make API request
                url = f"{self.base_url}search/JobOrder"
                params = {
                    'query': query,
                    'fields': ','.join(fields),
                    'sort': '-dateLastModified',
                    'start': start,
                    'count': count,
                    'BhRestToken': self.rest_token
                }
                
                response = self.session.get(url, params=params, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    jobs_data = data.get('data', [])
                    total = data.get('total', 0)
                    
                    # Add jobs from this page
                    all_jobs.extend(jobs_data)
                    
                    # Log pagination progress
                    logging.info(f"Fetched {len(all_jobs)} of {total} jobs for query: {query}")
                    
                    # Check if we need to fetch more pages
                    if len(jobs_data) < count or len(all_jobs) >= total:
                        break
                    
                    start += count
                else:
                    logging.error(f"Failed to get jobs by query: {response.status_code} - {response.text}")
                    break
            
            return self._filter_excluded_jobs(all_jobs)
                
        except Exception as e:
            logging.error(f"Error getting jobs by query: {str(e)}")
            return []
    
    def get_job_by_id(self, job_id: int) -> Optional[Dict]:
        """
        Get a single job by its ID
        
        Args:
            job_id: The job ID to retrieve
            
        Returns:
            Dict containing job data or None if not found
        """
        # Use existing connection if available, skip test_connection to avoid auth throttling
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None
            
        try:
            # Use the entity endpoint to get a specific job
            url = f"{self.base_url}entity/JobOrder/{job_id}"
            params = {
                'fields': 'id,title,publicDescription,employmentType,onSite,address(city,state,countryName),assignedUsers(id,firstName,lastName,email),responseUser(firstName,lastName),owner(firstName,lastName),dateLastModified,customText1,customText2,customText3',
                'BhRestToken': self.rest_token
            }
            
            logging.info(f"Getting job {job_id} from {url}")
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    logging.info(f"Successfully retrieved job {job_id}: {data['data'].get('title', 'No title')}")
                    return data['data']
                else:
                    logging.warning(f"Job {job_id} not found in response")
                    return None
            else:
                logging.error(f"Failed to get job {job_id}: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logging.error(f"Error getting job {job_id}: {str(e)}")
            return None
    
    def get_user_emails(self, user_ids: List[int]) -> Dict[int, Dict]:
        """
        Get email addresses for a list of CorporateUser IDs.
        
        The Bullhorn API doesn't return email in nested field syntax (e.g., assignedUsers(email)),
        so we need to query CorporateUser entities directly.
        
        Args:
            user_ids: List of CorporateUser IDs to look up
            
        Returns:
            Dict mapping user_id to {firstName, lastName, email}
        """
        if not user_ids:
            return {}
        
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return {}
        
        user_map = {}
        unique_ids = list(set(user_ids))
        
        # Fetch users individually via entity endpoint (most reliable)
        for user_id in unique_ids:
            try:
                url = f"{self.base_url}entity/CorporateUser/{user_id}"
                params = {
                    'fields': 'id,firstName,lastName,email',
                    'BhRestToken': self.rest_token
                }
                
                response = self.session.get(url, params=params, timeout=10)
                
                if response.status_code == 200:
                    data = response.json().get('data', {})
                    if data:
                        user_map[user_id] = {
                            'firstName': data.get('firstName', ''),
                            'lastName': data.get('lastName', ''),
                            'email': data.get('email', '')
                        }
                else:
                    logging.debug(f"Failed to fetch user {user_id}: {response.status_code}")
                    
            except Exception as e:
                logging.debug(f"Error fetching user {user_id}: {str(e)}")
        
        logging.info(f"📧 Fetched emails for {len(user_map)}/{len(unique_ids)} users")
        return user_map
    
    def get_tearsheets(self) -> List[Dict]:
        """
        Get available tearsheets from Bullhorn by testing common IDs
        
        Returns:
            List[Dict]: List of tearsheet dictionaries
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        tearsheets = []
        
        # Test common tearsheet IDs, focusing on most likely ranges first
        # Start with lower IDs first as they're more likely to exist
        id_ranges = [
            range(1, 51),      # 1-50: Most common range
            range(51, 101),    # 51-100: Less common
            range(101, 201),   # 101-200: Rare
        ]
        
        for id_range in id_ranges:
            consecutive_failures = 0
            
            for tearsheet_id in id_range:
                try:
                    url = f"{self.base_url}entity/Tearsheet/{tearsheet_id}"
                    params = {
                        'fields': 'id,name,description,dateAdded',
                        'BhRestToken': self.rest_token
                    }
                    
                    response = self.session.get(url, params=params, timeout=5)
                    
                    if response.status_code == 200:
                        data = response.json()
                        tearsheet = data.get('data', {})
                        if tearsheet:
                            tearsheets.append(tearsheet)
                            consecutive_failures = 0
                    elif response.status_code == 404:
                        consecutive_failures += 1
                        # If we hit too many consecutive 404s, skip to next range
                        if consecutive_failures >= 20:
                            break
                    else:
                        # Other error, try a few more then stop
                        consecutive_failures += 1
                        if consecutive_failures >= 5:
                            break
                        
                except Exception as e:
                    logging.error(f"Error getting tearsheet {tearsheet_id}: {str(e)}")
                    consecutive_failures += 1
                    if consecutive_failures >= 5:
                        break
                    continue
            
            # If we found some tearsheets, we can stop here for faster loading
            if tearsheets and len(tearsheets) >= 5:
                break
        
        return tearsheets
    
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
        if not all([self.client_id, self.username]):
            logging.error("Missing client_id or username for connection test")
            return False
            
        try:
            # Test authentication
            if not self.authenticate():
                logging.error("Authentication failed during connection test")
                return False
            
            # If we have valid authentication tokens, consider connection successful
            # This prevents monitoring failures due to temporary API issues
            if self.rest_token and self.base_url:
                logging.info("Connection test passed - valid authentication credentials available")
                
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
                        logging.info("Full API connection test successful")
                    else:
                        logging.warning(f"API test returned {response.status_code}, but authentication valid - continuing")
                        
                except Exception as api_e:
                    logging.warning(f"API test call failed but authentication succeeded: {str(api_e)} - continuing")
                
                return True  # Return success if authentication worked
            else:
                logging.error("Missing authentication tokens after successful authenticate() call")
                return False
            
        except Exception as e:
            logging.error(f"Bullhorn connection test failed: {str(e)}")
            return False
    
    def compare_job_lists(self, previous_jobs: List[Dict], current_jobs: List[Dict]) -> Dict:
        """
        Compare two job lists to find added, removed, and modified jobs
        
        Args:
            previous_jobs: List of jobs from previous check
            current_jobs: List of jobs from current check
            
        Returns:
            Dict with 'added', 'removed', 'modified' (lists), and 'summary' (dict with stats)
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
            fields_to_check = ['title', 'status', 'isOpen', 'numOpenings', 'dateLastModified', 
                               'description', 'publicDescription', 'address', 'employmentType',
                               'assignedUsers', 'owner']
            
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
    
    def get_tearsheet_members(self, tearsheet_id: int) -> List[Dict]:
        """
        Get tearsheet members (job IDs) from a tearsheet
        
        Args:
            tearsheet_id: ID of the tearsheet
            
        Returns:
            List[Dict]: List of member dictionaries with job IDs
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        try:
            url = f"{self.base_url}entity/Tearsheet/{tearsheet_id}/jobOrders"
            params = {
                'fields': 'id',
                'count': 500,  # Large batch size for members
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('data', [])
            else:
                logging.error(f"Failed to get tearsheet members: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logging.error(f"Error getting tearsheet members: {str(e)}")
            return []
    
    def remove_job_from_tearsheet(self, tearsheet_id: int, job_id: int) -> bool:
        """
        Remove a job from a tearsheet using Bullhorn's entity dissociation API
        
        Args:
            tearsheet_id: ID of the tearsheet
            job_id: ID of the job to remove
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return False
        
        try:
            # Bullhorn uses DELETE to dissociate entities from a to-many relationship
            url = f"{self.base_url}entity/Tearsheet/{tearsheet_id}/jobOrders/{job_id}"
            params = {
                'BhRestToken': self.rest_token
            }
            
            response = self.session.delete(url, params=params, timeout=30)
            
            if response.status_code in [200, 204]:
                logging.info(f"Successfully removed job {job_id} from tearsheet {tearsheet_id}")
                return True
            else:
                logging.error(f"Failed to remove job from tearsheet: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logging.error(f"Error removing job from tearsheet: {str(e)}")
            return False
    
    def get_jobs_batch(self, job_ids: List[int]) -> List[Dict]:
        """
        Get multiple jobs by their IDs using search query
        
        Args:
            job_ids: List of job IDs to fetch
            
        Returns:
            List[Dict]: List of job dictionaries
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        if not job_ids:
            return []
        
        try:
            # Build OR query for multiple IDs
            id_queries = [f"id:{job_id}" for job_id in job_ids]
            query = " OR ".join(id_queries)
            
            # Define comprehensive fields for XML mapping (removed invalid assignments field)
            fields = [
                "id", "title", "status", "isOpen", "dateAdded", 
                "dateLastModified", "clientCorporation(name)", "publicDescription", 
                "description", "address(city,state,countryName)", "employmentType",
                "onSite", "assignedUsers(firstName,lastName)", 
                "responseUser(firstName,lastName)", "owner(firstName,lastName)",
                "salary", "salaryUnit", "categories(name)", "skillList", 
                "degreeList", "certificationList", "benefits", "bonusPackage", 
                "numOpenings", "isPublic"
            ]
            
            url = f"{self.base_url}search/JobOrder"
            params = {
                'query': query,
                'fields': ','.join(fields),
                'count': len(job_ids),  # Expect exactly this many results
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                jobs_data = data.get('data', [])
                logging.info(f"Retrieved {len(jobs_data)} jobs out of {len(job_ids)} requested")
                return self._filter_excluded_jobs(jobs_data)
            else:
                logging.error(f"Failed to get jobs batch: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logging.error(f"Error getting jobs batch: {str(e)}")
            return []
    
    # ==================== Candidate Methods ====================
    
    def search_candidates(self, email: str = None, phone: str = None, 
                          first_name: str = None, last_name: str = None) -> List[Dict]:
        """
        Search for candidates by email, phone, or name
        
        Args:
            email: Candidate email address
            phone: Candidate phone number (digits only)
            first_name: First name
            last_name: Last name
            
        Returns:
            List of matching candidate records
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        try:
            # Build search query
            query_parts = []
            
            if email:
                query_parts.append(f'email:"{email}"')
            if phone:
                # Search with normalized phone
                phone_digits = ''.join(filter(str.isdigit, phone))
                if len(phone_digits) >= 10:
                    query_parts.append(f'phone:"{phone_digits}"')
            if first_name and last_name:
                query_parts.append(f'firstName:"{first_name}" AND lastName:"{last_name}"')
            elif first_name:
                query_parts.append(f'firstName:"{first_name}"')
            elif last_name:
                query_parts.append(f'lastName:"{last_name}"')
            
            if not query_parts:
                return []
            
            query = " OR ".join(query_parts)
            
            url = f"{self.base_url}search/Candidate"
            params = {
                'query': query,
                'fields': 'id,firstName,lastName,email,phone,mobile,address,status,source,occupation',
                'count': 10,
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = self._safe_json_parse(response)
                results = data.get('data', [])
                logging.info(f"Candidate search returned {len(results)} results")
                return results
            else:
                logging.error(f"Candidate search failed: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logging.error(f"Error searching candidates: {str(e)}")
            return []
    
    def create_candidate(self, candidate_data: Dict) -> Optional[int]:
        """
        Create a new candidate in Bullhorn
        
        Args:
            candidate_data: Dictionary with candidate fields
            
        Returns:
            New candidate ID or None on failure
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None
        
        try:
            url = f"{self.base_url}entity/Candidate"
            params = {'BhRestToken': self.rest_token}
            
            response = self.session.put(url, params=params, json=candidate_data, timeout=30)
            
            if response.status_code in [200, 201]:
                data = self._safe_json_parse(response)
                candidate_id = data.get('changedEntityId')
                logging.info(f"Created candidate {candidate_id}: {candidate_data.get('firstName')} {candidate_data.get('lastName')}")
                return candidate_id
            else:
                logging.error(f"Failed to create candidate: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logging.error(f"Error creating candidate: {str(e)}")
            return None
    
    def update_candidate(self, candidate_id: int, candidate_data: Dict) -> Optional[int]:
        """
        Update an existing candidate in Bullhorn
        
        Args:
            candidate_id: Bullhorn candidate ID
            candidate_data: Dictionary with fields to update
            
        Returns:
            Candidate ID on success or None on failure
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None
        
        try:
            url = f"{self.base_url}entity/Candidate/{candidate_id}"
            params = {'BhRestToken': self.rest_token}
            
            response = self.session.post(url, params=params, json=candidate_data, timeout=30)
            
            if response.status_code == 200:
                logging.info(f"Updated candidate {candidate_id}")
                return candidate_id
            else:
                logging.error(f"Failed to update candidate: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logging.error(f"Error updating candidate: {str(e)}")
            return None
    
    def upload_candidate_file(self, candidate_id: int, file_content: bytes, 
                               filename: str, file_type: str = "Resume") -> Optional[int]:
        """
        Upload a file (resume) to a candidate record using Bullhorn Files API
        Uses base64 JSON format which is more reliable than multipart form-data
        
        Args:
            candidate_id: Bullhorn candidate ID
            file_content: File content as bytes
            filename: Original filename
            file_type: File type classification (default: "Resume")
            
        Returns:
            File ID on success or None on failure
        """
        import base64
        
        if not self.base_url or not self.rest_token:
            logging.info(f"Authenticating before file upload for candidate {candidate_id}")
            if not self.authenticate():
                logging.error("Failed to authenticate for file upload")
                return None
        
        try:
            # Bullhorn file upload endpoint - PUT /file/Candidate/{candidateId}
            url = f"{self.base_url}file/Candidate/{candidate_id}"
            
            logging.info(f"Uploading file to URL: {url}")
            logging.info(f"File: {filename}, Size: {len(file_content)} bytes")
            
            # Determine content type based on file extension
            content_type = 'application/octet-stream'
            if filename.lower().endswith('.pdf'):
                content_type = 'application/pdf'
            elif filename.lower().endswith('.docx'):
                content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            elif filename.lower().endswith('.doc'):
                content_type = 'application/msword'
            elif filename.lower().endswith('.txt'):
                content_type = 'text/plain'
            elif filename.lower().endswith('.rtf'):
                content_type = 'application/rtf'
            
            # Encode file content as base64 (Bullhorn preferred format)
            file_base64 = base64.b64encode(file_content).decode('utf-8')
            
            # Build JSON payload for base64 upload (more reliable than multipart)
            # fileType: 'Resume' maps to Bullhorn's File Type dropdown
            payload = {
                'externalID': 'portfolio',
                'fileContent': file_base64,
                'fileType': 'Resume',
                'name': filename,
                'contentType': content_type,
                'description': f'{file_type} - {filename}',
                'type': 'Resume'
            }
            
            # Log file size for debugging
            file_size_kb = len(file_content) / 1024
            logging.info(f"Uploading file using base64 JSON method to candidate {candidate_id} ({file_size_kb:.1f} KB)")
            
            # Temporarily set JSON content type for this request
            original_content_type = self.session.headers.get('Content-Type')
            self.session.headers['Content-Type'] = 'application/json'
            self.session.headers['Accept'] = 'application/json'
            
            params = {'BhRestToken': self.rest_token}
            
            try:
                # Make PUT request with JSON payload using authenticated session
                response = self.session.put(
                    url, 
                    params=params,
                    json=payload,
                    timeout=120  # Longer timeout for file uploads
                )
            finally:
                # Restore original content type
                if original_content_type:
                    self.session.headers['Content-Type'] = original_content_type
            
            logging.info(f"File upload response status: {response.status_code}")
            logging.info(f"File upload response: {response.text[:500] if response.text else 'No response body'}")
            
            if response.status_code in [200, 201]:
                data = response.json()
                file_id = data.get('fileId')
                if file_id:
                    logging.info(f"✅ Successfully uploaded file '{filename}' to candidate {candidate_id}, file ID: {file_id}")
                    return file_id
                else:
                    # Some Bullhorn versions return changedEntityId instead
                    file_id = data.get('changedEntityId')
                    if file_id:
                        logging.info(f"✅ Successfully uploaded file '{filename}' to candidate {candidate_id}, entity ID: {file_id}")
                        return file_id
                    logging.warning(f"Upload returned success but no fileId in response: {data}")
                    return None
            else:
                logging.error(f"❌ Failed to upload file to candidate {candidate_id}: {response.status_code}")
                logging.error(f"Response: {response.text}")
                logging.error(f"File details: name={filename}, size={file_size_kb:.1f}KB, type={content_type}")
                
                # Common error diagnostics
                if response.status_code == 400:
                    logging.error("400 Bad Request - Check required fields: externalID, fileType, name, contentType")
                elif response.status_code == 500:
                    logging.error("500 Internal Server Error - File may be too large or corrupted")
                
                # If authentication expired, try once more
                if response.status_code == 401:
                    logging.info("Token expired, re-authenticating and retrying file upload...")
                    if self.authenticate():
                        params['BhRestToken'] = self.rest_token
                        self.session.headers['Content-Type'] = 'application/json'
                        self.session.headers['Accept'] = 'application/json'
                        try:
                            retry_response = self.session.put(url, params=params, json=payload, timeout=120)
                        finally:
                            if original_content_type:
                                self.session.headers['Content-Type'] = original_content_type
                        
                        if retry_response.status_code in [200, 201]:
                            data = retry_response.json()
                            file_id = data.get('fileId') or data.get('changedEntityId')
                            logging.info(f"✅ Retry successful, file ID: {file_id}")
                            return file_id
                
                return None
                
        except Exception as e:
            logging.error(f"❌ Error uploading candidate file: {str(e)}", exc_info=True)
            return None
    
    def get_job_order(self, job_id: int) -> Optional[Dict]:
        """
        Get a job order by ID to verify it exists
        
        Args:
            job_id: Bullhorn job order ID
            
        Returns:
            Job order data dictionary or None if not found
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None
        
        try:
            url = f"{self.base_url}entity/JobOrder/{job_id}"
            params = {
                'fields': 'id,title,status,isOpen,isDeleted',
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = self._safe_json_parse(response)
                job_data = data.get('data', {})
                if job_data and job_data.get('id'):
                    logging.info(f"Found job order {job_id}: {job_data.get('title', 'Unknown')}")
                    return job_data
            
            logging.warning(f"Job order {job_id} not found in Bullhorn")
            return None
            
        except Exception as e:
            logging.error(f"Error getting job order: {str(e)}")
            return None
    
    def create_job_submission(self, candidate_id: int, job_id: int, 
                               source: str = None) -> Optional[int]:
        """
        Create a job submission (response) linking a candidate to a job
        
        Args:
            candidate_id: Bullhorn candidate ID
            job_id: Bullhorn job order ID
            source: Application source (for tracking)
            
        Returns:
            Submission ID on success or None on failure
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None
        
        try:
            # Verify job exists before creating submission
            job = self.get_job_order(job_id)
            if not job:
                logging.warning(f"Cannot create submission: job order {job_id} not found in Bullhorn")
                return None
            
            if job.get('isDeleted'):
                logging.warning(f"Cannot create submission: job order {job_id} is deleted")
                return None
            
            url = f"{self.base_url}entity/JobSubmission"
            params = {'BhRestToken': self.rest_token}
            
            submission_data = {
                'candidate': {'id': int(candidate_id)},
                'jobOrder': {'id': int(job_id)},
                'status': 'New Lead',
                'dateWebResponse': int(datetime.now().timestamp() * 1000),
                'source': source or 'Web Response'
            }
            
            response = self.session.put(url, params=params, json=submission_data, timeout=30)
            
            if response.status_code in [200, 201]:
                data = self._safe_json_parse(response)
                submission_id = data.get('changedEntityId')
                logging.info(f"Created job submission {submission_id}: candidate {candidate_id} -> job {job_id}")
                return submission_id
            else:
                logging.error(f"Failed to create job submission: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logging.error(f"Error creating job submission: {str(e)}")
            return None
    
    def get_candidate(self, candidate_id: int) -> Optional[Dict]:
        """
        Get a candidate by ID
        
        Args:
            candidate_id: Bullhorn candidate ID
            
        Returns:
            Candidate data dictionary or None
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None
        
        try:
            url = f"{self.base_url}entity/Candidate/{candidate_id}"
            params = {
                'fields': 'id,firstName,lastName,email,phone,mobile,address,status,source,occupation,companyName,skillSet,description',
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('data', {})
            else:
                logging.error(f"Failed to get candidate: {response.status_code}")
                return None
                
        except Exception as e:
            logging.error(f"Error getting candidate: {str(e)}")
            return None
    
    def create_candidate_work_history(self, candidate_id: int, work_history: List[Dict]) -> List[int]:
        """
        Create work history records for a candidate
        
        Args:
            candidate_id: Bullhorn candidate ID
            work_history: List of work history records from resume parsing
                Each record: {title, company, start_year, end_year, current}
                
        Returns:
            List of created work history IDs
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        created_ids = []
        
        for work in work_history:
            try:
                url = f"{self.base_url}entity/CandidateWorkHistory"
                params = {'BhRestToken': self.rest_token}
                
                # Build work history data
                work_data = {
                    'candidate': {'id': int(candidate_id)},
                    'title': work.get('title', ''),
                    'companyName': work.get('company', ''),
                    'isLastJob': work.get('current', False)
                }
                
                # Add start date if available
                if work.get('start_year'):
                    try:
                        # Bullhorn expects epoch milliseconds
                        from datetime import datetime
                        start_date = datetime(int(work['start_year']), 1, 1)
                        work_data['startDate'] = int(start_date.timestamp() * 1000)
                    except:
                        pass
                
                # Add end date if available (and not current job)
                if work.get('end_year') and not work.get('current'):
                    try:
                        end_date = datetime(int(work['end_year']), 12, 31)
                        work_data['endDate'] = int(end_date.timestamp() * 1000)
                    except:
                        pass
                
                response = self.session.put(url, params=params, json=work_data, timeout=30)
                
                if response.status_code in [200, 201]:
                    data = self._safe_json_parse(response)
                    work_id = data.get('changedEntityId')
                    if work_id:
                        created_ids.append(work_id)
                        logging.info(f"Created work history {work_id}: {work.get('title')} at {work.get('company')}")
                else:
                    logging.warning(f"Failed to create work history: {response.status_code} - {response.text}")
                    
            except Exception as e:
                logging.error(f"Error creating work history record: {str(e)}")
        
        return created_ids
    
    def create_candidate_education(self, candidate_id: int, education: List[Dict]) -> List[int]:
        """
        Create education records for a candidate
        
        Args:
            candidate_id: Bullhorn candidate ID
            education: List of education records from resume parsing
                Each record: {degree, field, institution, year}
                
        Returns:
            List of created education IDs
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []
        
        created_ids = []
        
        for edu in education:
            try:
                url = f"{self.base_url}entity/CandidateEducation"
                params = {'BhRestToken': self.rest_token}
                
                # Build education data
                edu_data = {
                    'candidate': {'id': int(candidate_id)},
                    'degree': edu.get('degree', ''),
                    'major': edu.get('field', ''),
                    'school': edu.get('institution', '')
                }
                
                # Add graduation date if available
                if edu.get('year'):
                    try:
                        from datetime import datetime
                        grad_date = datetime(int(edu['year']), 6, 1)  # Assume June graduation
                        edu_data['graduationDate'] = int(grad_date.timestamp() * 1000)
                    except:
                        pass
                
                response = self.session.put(url, params=params, json=edu_data, timeout=30)
                
                if response.status_code in [200, 201]:
                    data = self._safe_json_parse(response)
                    edu_id = data.get('changedEntityId')
                    if edu_id:
                        created_ids.append(edu_id)
                        logging.info(f"Created education {edu_id}: {edu.get('degree')} from {edu.get('institution')}")
                else:
                    logging.warning(f"Failed to create education: {response.status_code} - {response.text}")
                    
            except Exception as e:
                logging.error(f"Error creating education record: {str(e)}")
        
        return created_ids
    
    def create_candidate_note(self, candidate_id: int, note_text: str, 
                               action: str = "AI Resume Summary") -> Optional[int]:
        """
        Create a note on a candidate record
        
        Args:
            candidate_id: Bullhorn candidate ID
            note_text: The note content
            action: The action/title for the note
            
        Returns:
            Note ID on success or None on failure
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                logging.error(f"Note creation failed: Could not authenticate with Bullhorn")
                return None
        
        url = f"{self.base_url}entity/Note"
        params = {'BhRestToken': self.rest_token}
        
        # Try multiple note creation approaches in order of preference
        approaches = []
        
        # Build base note data
        base_note_data = {
            'personReference': {'id': int(candidate_id)},
            'action': action,
            'comments': note_text,
            'isDeleted': False
        }
        
        if self.user_id:
            # Approach 1: Full with commentingPerson and candidates array
            note_data_full = {**base_note_data, 
                            'commentingPerson': {'id': int(self.user_id)},
                            'candidates': [{'id': int(candidate_id)}]}
            approaches.append(('full_with_user', note_data_full))
            
            # Approach 2: With commentingPerson only (no candidates array)
            note_data_with_user = {**base_note_data,
                                  'commentingPerson': {'id': int(self.user_id)}}
            approaches.append(('with_user_no_candidates', note_data_with_user))
        
        # Approach 3: With candidates array only (no commentingPerson)
        note_data_candidates = {**base_note_data,
                               'candidates': [{'id': int(candidate_id)}]}
        approaches.append(('with_candidates', note_data_candidates))
        
        # Approach 4: Minimal - just personReference
        approaches.append(('minimal', base_note_data.copy()))
        
        logging.info(f"📝 Creating note for candidate {candidate_id}: action='{action}', length={len(note_text)} chars, user_id={self.user_id}")
        
        for approach_name, note_data in approaches:
            try:
                logging.info(f"Trying note creation approach: {approach_name}")
                response = self.session.put(url, params=params, json=note_data, timeout=60)
                
                if response.status_code in [200, 201]:
                    data = self._safe_json_parse(response)
                    note_id = data.get('changedEntityId')
                    logging.info(f"✅ Created note {note_id} on candidate {candidate_id} using '{approach_name}' approach")
                    return note_id
                elif response.status_code == 401:
                    # Token expired, re-authenticate and retry this approach
                    logging.warning(f"Note creation got 401, re-authenticating...")
                    self.rest_token = None
                    if self.authenticate():
                        url = f"{self.base_url}entity/Note"  # Update URL in case base_url changed
                        params = {'BhRestToken': self.rest_token}
                        response = self.session.put(url, params=params, json=note_data, timeout=60)
                        if response.status_code in [200, 201]:
                            data = self._safe_json_parse(response)
                            note_id = data.get('changedEntityId')
                            logging.info(f"✅ Created note {note_id} on candidate {candidate_id} using '{approach_name}' after re-auth")
                            return note_id
                    else:
                        # Re-auth failed - abort all attempts
                        logging.error(f"❌ Re-authentication failed - aborting note creation for candidate {candidate_id}")
                        return None
                
                # Log failure details and try next approach
                error_text = response.text[:500] if response.text else "No error details"
                logging.warning(f"⚠️ Note creation failed with '{approach_name}': HTTP {response.status_code} - {error_text}")
                
            except Exception as e:
                logging.error(f"❌ Exception in note creation approach '{approach_name}': {str(e)}")
        
        logging.error(f"❌ All note creation approaches failed for candidate {candidate_id}")
        return None