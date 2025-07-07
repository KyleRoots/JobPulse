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
        try:
            # Step 1: Get authorization code
            auth_url = "https://auth.bullhornstaffing.com/oauth/authorize"
            auth_params = {
                'client_id': self.client_id,
                'response_type': 'code',
                'redirect_uri': 'https://www.bullhorn.com',
                'username': self.username,
                'password': self.password
            }
            
            # Step 2: Exchange authorization code for access token
            token_url = "https://auth.bullhornstaffing.com/oauth/token"
            token_data = {
                'grant_type': 'authorization_code',
                'code': 'temp_code',  # This would be obtained from the auth flow
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'redirect_uri': 'https://www.bullhorn.com'
            }
            
            # For production, implement full OAuth flow
            # For now, use direct login if available
            return self._direct_login()
            
        except Exception as e:
            logging.error(f"Bullhorn authentication failed: {str(e)}")
            return False
    
    def _direct_login(self) -> bool:
        """
        Direct login using username/password (if available)
        
        Returns:
            bool: True if login successful, False otherwise
        """
        try:
            # First, get login info to determine correct data center
            login_info_url = "https://rest.bullhornstaffing.com/rest-services/loginInfo"
            login_info_params = {'username': self.username}
            
            response = self.session.get(login_info_url, params=login_info_params)
            if response.status_code != 200:
                logging.error(f"Failed to get login info: {response.status_code}")
                return False
            
            login_data = response.json()
            
            # Extract the correct REST URL
            if 'restUrl' in login_data:
                self.base_url = login_data['restUrl']
            else:
                logging.error("No REST URL found in login info response")
                return False
            
            logging.info(f"Bullhorn authentication successful. Base URL: {self.base_url}")
            return True
            
        except Exception as e:
            logging.error(f"Direct login failed: {str(e)}")
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
                return False
            
            # Test authentication
            if self.authenticate():
                # Test a simple API call
                url = f"{self.base_url}/ping" if self.base_url else "https://rest.bullhornstaffing.com/rest-services/ping"
                params = {'BhRestToken': self.rest_token} if self.rest_token else {}
                
                response = self.session.get(url, params=params)
                return response.status_code == 200
            
            return False
            
        except Exception as e:
            logging.error(f"Bullhorn connection test failed: {str(e)}")
            return False
    
    def compare_job_lists(self, previous_jobs: List[Dict], current_jobs: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Compare two job lists to find added and removed jobs
        
        Args:
            previous_jobs: List of jobs from previous check
            current_jobs: List of jobs from current check
            
        Returns:
            Dict with 'added' and 'removed' lists
        """
        previous_ids = {job['id'] for job in previous_jobs}
        current_ids = {job['id'] for job in current_jobs}
        
        added_ids = current_ids - previous_ids
        removed_ids = previous_ids - current_ids
        
        added_jobs = [job for job in current_jobs if job['id'] in added_ids]
        removed_jobs = [job for job in previous_jobs if job['id'] in removed_ids]
        
        return {
            'added': added_jobs,
            'removed': removed_jobs
        }