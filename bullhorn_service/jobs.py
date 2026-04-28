"""JobsMixin — Bullhorn API methods for this domain."""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

import requests  # noqa: F401  (used by methods via self.session)

logger = logging.getLogger(__name__)


class JobsMixin:
    """Mixin providing jobs-related Bullhorn API methods."""

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
                data = self._safe_json_parse(response)
                return data.get('data', [])
            else:
                logger.error(f"Failed to get job orders: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting job orders: {str(e)}")
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
                'fields': 'id,name,jobOrders(id,title,isOpen,status,dateAdded,dateLastModified,clientCorporation(name),description,publicDescription,address(address1,city,state,countryName),employmentType,onSite,assignedUsers(id,firstName,lastName,email),responseUser(firstName,lastName),owner(firstName,lastName))',
                'BhRestToken': self.rest_token
            }
            
            entity_response = self.session.get(entity_url, params=entity_params, timeout=60)
            entity_total = 0
            entity_job_ids = set()
            
            if entity_response.status_code == 200:
                entity_data = self._safe_json_parse(entity_response)
                job_orders = entity_data.get('data', {}).get('jobOrders', {})
                if isinstance(job_orders, dict):
                    entity_total = job_orders.get('total', 0)
                    logger.info(f"Tearsheet {tearsheet_id}: Entity API reports {entity_total} total jobs")
                    
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
                                paginated_data = self._safe_json_parse(paginated_response)
                                paginated_jobs = paginated_data.get('data', [])
                                if not paginated_jobs:
                                    break
                                entity_job_ids.update({job.get('id') for job in paginated_jobs if job.get('id')})
                                entity_start += len(paginated_jobs)
                                if len(paginated_jobs) < page_size:
                                    break
                            else:
                                logger.warning(f"Failed to fetch Entity API page at start={entity_start}: {paginated_response.status_code}")
                                break
                        
                        # Safeguard: Abort orphan filtering if we didn't collect all Entity IDs
                        if len(entity_job_ids) < entity_total:
                            logger.error(f"Tearsheet {tearsheet_id}: Entity pagination incomplete! Collected {len(entity_job_ids)} IDs but Entity API reports {entity_total}. Aborting orphan filtering to prevent data loss.")
                            entity_job_ids = set()  # Clear IDs to disable orphan filtering
            
            # For larger tearsheets, use search API to get all jobs
            query = f"tearsheets.id:{tearsheet_id}"
            
            # Define fields to retrieve (enhanced for XML mapping)
            fields = [
                "id", "title", "isOpen", "status", "dateAdded", 
                "dateLastModified", "clientCorporation(id,name)",
                "clientContact(firstName,lastName)", "description",
                "publicDescription", "numOpenings", "isPublic",
                "address(address1,city,state,countryName)", "employmentType",
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
                    data = self._safe_json_parse(response)
                    jobs_data = data.get('data', [])
                    total = data.get('total', 0)
                    
                    # Add jobs from this page
                    all_jobs.extend(jobs_data)
                    
                    # Check if we need to fetch more pages
                    if len(jobs_data) < count or len(all_jobs) >= total:
                        break
                    
                    start += count
                else:
                    logger.error(f"Failed to get tearsheet jobs: {response.status_code} - {response.text}")
                    break
            # Apply job exclusion filter
            filtered_jobs = self._filter_excluded_jobs(all_jobs)
            
            # Log discrepancies between Entity API and Search API
            # NOTE: Search API's tearsheets.id index is the authoritative source
            # of tearsheet membership. Entity API frequently under-reports job
            # counts due to Bullhorn consistency lag, causing valid jobs to be
            # dropped if used for filtering. We log discrepancies but always
            # trust the Search API results.
            if entity_total > 0 and len(all_jobs) != entity_total:
                logger.warning(
                    f"Tearsheet {tearsheet_id}: Entity API reports {entity_total} jobs "
                    f"but Search API returned {len(all_jobs)}. "
                    f"Using Search API results (authoritative for tearsheet membership)."
                )
            
            # Normalize addresses - parse city/state from address1 if nested fields are empty
            for job in filtered_jobs:
                self.normalize_job_address(job)
            
            return filtered_jobs
                
        except Exception as e:
            logger.error(f"Error getting tearsheet jobs: {str(e)}")
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
                "address(address1,city,state,countryName)",
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
                    data = self._safe_json_parse(response)
                    jobs_data = data.get('data', [])
                    total = data.get('total', 0)
                    
                    # Add jobs from this page
                    all_jobs.extend(jobs_data)
                    
                    # Log pagination progress
                    logger.info(f"Fetched {len(all_jobs)} of {total} jobs for query: {query}")
                    
                    # Check if we need to fetch more pages
                    if len(jobs_data) < count or len(all_jobs) >= total:
                        break
                    
                    start += count
                else:
                    logger.error(f"Failed to get jobs by query: {response.status_code} - {response.text}")
                    break
            
            filtered_jobs = self._filter_excluded_jobs(all_jobs)
            
            # Normalize addresses - parse city/state from address1 if nested fields are empty
            for job in filtered_jobs:
                self.normalize_job_address(job)
            
            return filtered_jobs
                
        except Exception as e:
            logger.error(f"Error getting jobs by query: {str(e)}")
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
                'fields': 'id,title,description,publicDescription,employmentType,onSite,address(address1,city,state,countryName),assignedUsers(id,firstName,lastName,email),responseUser(firstName,lastName),owner(firstName,lastName),dateLastModified,customText1,customText2,customText3',
                'BhRestToken': self.rest_token
            }
            
            logger.info(f"Getting job {job_id} from {url}")
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = self._safe_json_parse(response)
                if 'data' in data:
                    job = data['data']
                    # Normalize address - parse city/state from address1 if nested fields are empty
                    self.normalize_job_address(job)
                    logger.info(f"Successfully retrieved job {job_id}: {job.get('title', 'No title')}")
                    return job
                else:
                    logger.warning(f"Job {job_id} not found in response")
                    return None
            else:
                logger.error(f"Failed to get job {job_id}: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting job {job_id}: {str(e)}")
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
                    data = self._safe_json_parse(response).get('data', {})
                    if data:
                        user_map[user_id] = {
                            'firstName': data.get('firstName', ''),
                            'lastName': data.get('lastName', ''),
                            'email': data.get('email', '')
                        }
                else:
                    logger.debug(f"Failed to fetch user {user_id}: {response.status_code}")
                    
            except Exception as e:
                logger.debug(f"Error fetching user {user_id}: {str(e)}")
        
        logger.info(f"📧 Fetched emails for {len(user_map)}/{len(unique_ids)} users")
        return user_map
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
                "description", "address(address1,city,state,countryName)", "employmentType",
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
                data = self._safe_json_parse(response)
                jobs_data = data.get('data', [])
                logger.info(f"Retrieved {len(jobs_data)} jobs out of {len(job_ids)} requested")
                return self._filter_excluded_jobs(jobs_data)
            else:
                logger.error(f"Failed to get jobs batch: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting jobs batch: {str(e)}")
            return []
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
                    logger.info(f"Found job order {job_id}: {job_data.get('title', 'Unknown')}")
                    return job_data
            
            logger.warning(f"Job order {job_id} not found in Bullhorn")
            return None
            
        except Exception as e:
            logger.error(f"Error getting job order: {str(e)}")
            return None
    def update_job_order(self, job_id: int, data: Dict) -> bool:
        return self.update_entity('JobOrder', job_id, data)
