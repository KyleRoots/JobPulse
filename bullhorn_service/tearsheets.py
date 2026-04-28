"""TearsheetsMixin — Bullhorn API methods for this domain."""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

import requests  # noqa: F401  (used by methods via self.session)

logger = logging.getLogger(__name__)


class TearsheetsMixin:
    """Mixin providing tearsheets-related Bullhorn API methods."""

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
                        data = self._safe_json_parse(response)
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
                    logger.error(f"Error getting tearsheet {tearsheet_id}: {str(e)}")
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
                data = self._safe_json_parse(response)
                return data.get('data', [])
            else:
                logger.error(f"Failed to get tearsheet members: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting tearsheet members: {str(e)}")
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
                logger.info(f"Successfully removed job {job_id} from tearsheet {tearsheet_id}")
                return True
            else:
                logger.error(f"Failed to remove job from tearsheet: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error removing job from tearsheet: {str(e)}")
            return False
    def add_job_to_tearsheet(self, tearsheet_id: int, job_id: int) -> bool:
        return self.add_entity_to_association('Tearsheet', tearsheet_id, 'jobOrders', [job_id])
    def add_candidate_to_tearsheet(self, tearsheet_id: int, candidate_id: int) -> bool:
        return self.add_entity_to_association('Tearsheet', tearsheet_id, 'candidates', [candidate_id])
    def remove_candidate_from_tearsheet(self, tearsheet_id: int, candidate_id: int) -> bool:
        return self.remove_entity_from_association('Tearsheet', tearsheet_id, 'candidates', [candidate_id])
