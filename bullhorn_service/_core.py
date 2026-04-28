"""Core base for BullhornService — class constants and shared helpers."""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

import requests  # noqa: F401  (used by methods via self.session)

logger = logging.getLogger(__name__)


class _BullhornCore:
    """Base class holding constants, __init__, and shared low-level helpers."""

    BULLHORN_ONE_AUTH_URL = "https://auth-east.bullhornstaffing.com/oauth/authorize"
    BULLHORN_ONE_TOKEN_URL = "https://auth-east.bullhornstaffing.com/oauth/token"
    BULLHORN_ONE_REST_LOGIN_URL = "https://rest-east.bullhornstaffing.com/rest-services/login"
    BULLHORN_ONE_REST_URL = "https://rest45.bullhornstaffing.com/rest-services/dcc900/"
    LEGACY_LOGIN_INFO_URL = "https://rest.bullhornstaffing.com/rest-services/loginInfo"
    SUPPORTED_ENTITY_TYPES = {
        'Candidate', 'JobOrder', 'Placement', 'JobSubmission', 'ClientContact',
        'ClientCorporation', 'Lead', 'Opportunity', 'Note', 'Sendout',
        'Appointment', 'Task', 'Tearsheet', 'CorporateUser',
        'CandidateEducation', 'CandidateWorkHistory', 'CandidateReference',
        'Skill', 'Category', 'BusinessSector', 'PlacementChangeRequest',
        'JobSubmissionHistory', 'NoteEntity',
    }
    ENTITY_DEFAULT_FIELDS = {
        'Candidate': 'id,firstName,lastName,email,phone,mobile,status,source,occupation,companyName,skillSet,owner(id,firstName,lastName),address(address1,city,state,zip,countryName)',
        'JobOrder': 'id,title,status,isOpen,isDeleted,isPublic,employmentType,salary,salaryUnit,skillList,clientCorporation(id,name),owner(id,firstName,lastName),address(city,state,countryName)',
        'Placement': 'id,status,dateBegin,dateEnd,salary,payRate,billingFrequency,candidate(id,firstName,lastName),jobOrder(id,title),approvingClientContact(id,firstName,lastName)',
        'JobSubmission': 'id,status,dateWebResponse,source,candidate(id,firstName,lastName),jobOrder(id,title),sendingUser(id,firstName,lastName)',
        'ClientContact': 'id,firstName,lastName,email,phone,status,occupation,clientCorporation(id,name),owner(id,firstName,lastName)',
        'ClientCorporation': 'id,name,status,phone,companyURL,address(city,state,countryName),billingContact(id,firstName,lastName)',
        'Note': 'id,action,comments,dateAdded,personReference(id,firstName,lastName),commentingPerson(id,firstName,lastName)',
        'Tearsheet': 'id,name,description,owner(id,firstName,lastName)',
        'CorporateUser': 'id,firstName,lastName,email,username,isDeleted,enabled,primaryDepartment(id,name),occupation',
        'CandidateEducation': 'id,school,degree,major,graduationDate,candidate(id,firstName,lastName)',
        'CandidateWorkHistory': 'id,companyName,title,startDate,endDate,isLastJob,candidate(id,firstName,lastName)',
        'CandidateReference': 'id,referenceFirstName,referenceLastName,companyName,referencePhone,referenceEmail,candidate(id,firstName,lastName)',
        'Skill': 'id,name',
        'Category': 'id,name,occupation',
        'BusinessSector': 'id,name',
        'PlacementChangeRequest': 'id,status,requestType,placement(id),requestingUser(id,firstName,lastName)',
        'Lead': 'id,firstName,lastName,email,phone,status,company(id,name)',
        'Opportunity': 'id,title,status,clientCorporation(id,name),owner(id,firstName,lastName)',
    }
    def __init__(self, client_id=None, client_secret=None, username=None, password=None):
        self.base_url = None
        self.rest_token = None
        self.access_token = None  # OAuth access token (set during _direct_login)
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
            logger.info("🔄 Bullhorn One API mode ENABLED - using new fixed endpoints")
            logger.info(f"   Auth URL: {self.BULLHORN_ONE_AUTH_URL}")
            logger.info(f"   Token URL: {self.BULLHORN_ONE_TOKEN_URL}")
            logger.info(f"   REST Login URL: {self.BULLHORN_ONE_REST_LOGIN_URL}")
            logger.info(f"   REST URL: {self.BULLHORN_ONE_REST_URL}")
        
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
                logger.warning(f"Received HTML response instead of JSON. Status: {response.status_code}")
                raise Exception("Authentication failed: Received HTML login page instead of JSON data. Please refresh and try again.")
            
            # Attempt to parse JSON
            return response.json()
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {str(e)[:200]}")
            raise Exception(f"Invalid API response format. Please try again.")
        except Exception as e:
            if "Authentication failed" in str(e):
                raise e  # Re-raise authentication errors as-is
            logger.error(f"Error parsing API response: {str(e)}")
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
            logger.warning(f"Could not load Bullhorn credentials from database: {str(e)}")
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
            logger.info(f"🚫 Excluded {self.excluded_count} jobs from processing: {excluded_ids}")
        
        return filtered_jobs
    @staticmethod
    def parse_address_string(address_str: str) -> Dict[str, str]:
        """
        Parse a street address string to extract city, state, zip, and country.
        
        This handles cases where Bullhorn's address object has empty city/state 
        fields but a full address in address1 like:
        "2755 N Michigan Ave, Greensburg, Indiana 47240, United States"
        
        Args:
            address_str: Full address string from address1 field
            
        Returns:
            Dict with city, state, zip, country keys (empty string if not found)
        """
        import re
        
        result = {'city': '', 'state': '', 'zip': '', 'country': ''}
        
        if not address_str or not address_str.strip():
            return result
        
        # Clean the address string
        address_str = address_str.strip()
        
        # Common country names to detect
        countries = ['United States', 'USA', 'US', 'Canada', 'Mexico']
        
        # Check for country at the end
        for country in countries:
            if address_str.lower().endswith(country.lower()):
                result['country'] = 'United States' if country in ['USA', 'US'] else country
                address_str = address_str[:-len(country)].rstrip(', ')
                break
        
        # US state abbreviations
        state_abbrevs = {
            'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas',
            'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware',
            'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho',
            'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas',
            'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
            'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi',
            'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada',
            'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
            'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma',
            'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina',
            'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah',
            'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia',
            'WI': 'Wisconsin', 'WY': 'Wyoming', 'DC': 'District of Columbia'
        }
        
        # Try pattern: "City, State ZIP" or "City, State"
        # Pattern handles: "Greensburg, Indiana 47240" or "Greensburg, IN 47240"
        pattern = r',\s*([^,]+?)[,\s]+([A-Za-z]{2,}(?:\s[A-Za-z]+)?)\s*(\d{5}(?:-\d{4})?)?$'
        match = re.search(pattern, address_str)
        
        if match:
            city_candidate = match.group(1).strip()
            state_candidate = match.group(2).strip()
            zip_code = match.group(3) if match.group(3) else ''
            
            # Check if state_candidate is a valid state name or abbreviation
            state_upper = state_candidate.upper()
            if state_upper in state_abbrevs:
                result['state'] = state_abbrevs[state_upper]
                result['city'] = city_candidate
                result['zip'] = zip_code
            elif state_candidate in state_abbrevs.values():
                result['state'] = state_candidate
                result['city'] = city_candidate
                result['zip'] = zip_code
        
        # FALLBACK: Handle space-separated addresses with NO commas
        # Example: "333 Centennial Pkwy Louisville Colorado 80027 United States"
        if not result['city'] and not result['state']:
            # Look for state name or abbreviation in the address
            words = address_str.split()
            
            # Try to find state name (could be multi-word like "New York")
            for i, word in enumerate(words):
                word_upper = word.upper()
                
                # Check for state abbreviation (2 letters)
                if word_upper in state_abbrevs:
                    result['state'] = state_abbrevs[word_upper]
                    # City is likely the word(s) before the state
                    # Skip street address parts (numbers, common street suffixes)
                    street_suffixes = ['st', 'street', 'ave', 'avenue', 'blvd', 'boulevard', 
                                      'dr', 'drive', 'rd', 'road', 'ln', 'lane', 'way', 
                                      'ct', 'court', 'pkwy', 'parkway', 'pl', 'place', 'cir', 'circle']
                    
                    # Find the city by looking for a word before state that's not a street suffix
                    for j in range(i - 1, -1, -1):
                        candidate = words[j].lower().rstrip(',.')
                        # Skip numbers and street suffixes
                        if not candidate.isdigit() and candidate not in street_suffixes:
                            result['city'] = words[j].rstrip(',.')
                            break
                    break
                
                # Check for full state name (e.g., "Colorado")
                if word in state_abbrevs.values():
                    result['state'] = word
                    # City is likely the word before
                    street_suffixes = ['st', 'street', 'ave', 'avenue', 'blvd', 'boulevard', 
                                      'dr', 'drive', 'rd', 'road', 'ln', 'lane', 'way', 
                                      'ct', 'court', 'pkwy', 'parkway', 'pl', 'place', 'cir', 'circle']
                    for j in range(i - 1, -1, -1):
                        candidate = words[j].lower().rstrip(',.')
                        if not candidate.isdigit() and candidate not in street_suffixes:
                            result['city'] = words[j].rstrip(',.')
                            break
                    break
            
            # Try to find ZIP code (5-digit number optionally followed by -4 digits)
            zip_pattern = r'\b(\d{5}(?:-\d{4})?)\b'
            zip_match = re.search(zip_pattern, address_str)
            if zip_match:
                result['zip'] = zip_match.group(1)
        
        # Set default country if we found a valid US state
        if result['state'] and not result['country']:
            result['country'] = 'United States'
        
        return result
    def normalize_job_address(self, job: Dict) -> Dict:
        """
        Normalize a job's address fields, filling in missing city/state from address1 if needed.
        
        Args:
            job: Job dictionary from Bullhorn API
            
        Returns:
            The same job dictionary with normalized address data
        """
        address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
        
        city = address.get('city', '').strip() if address.get('city') else ''
        state = address.get('state', '').strip() if address.get('state') else ''
        country = (address.get('countryName', '') or address.get('country', '')).strip()
        
        # If city/state are empty but we have address1, try to parse it
        if (not city or not state) and address.get('address1'):
            parsed = self.parse_address_string(address.get('address1', ''))
            if parsed['city'] and not city:
                city = parsed['city']
            if parsed['state'] and not state:
                state = parsed['state']
            if parsed['country'] and not country:
                country = parsed['country']
            
            logger.debug(f"Parsed address for job {job.get('id')}: city={city}, state={state}")
        
        # Update the address dict with normalized values
        if 'address' not in job or not isinstance(job['address'], dict):
            job['address'] = {}
        
        job['address']['city'] = city
        job['address']['state'] = state
        job['address']['countryName'] = country
        
        return job
