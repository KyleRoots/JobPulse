"""EntitiesMixin — Bullhorn API methods for this domain."""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

import requests  # noqa: F401  (used by methods via self.session)

logger = logging.getLogger(__name__)


class EntitiesMixin:
    """Mixin providing entities-related Bullhorn API methods."""

    def get_entity(self, entity_type: str, entity_id: int, fields: str = None) -> Optional[Dict]:
        if entity_type not in self.SUPPORTED_ENTITY_TYPES:
            logger.error(f"Unsupported entity type: {entity_type}")
            return None

        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None

        if not fields:
            fields = self.ENTITY_DEFAULT_FIELDS.get(entity_type, 'id')

        try:
            url = f"{self.base_url}entity/{entity_type}/{entity_id}"
            params = {'fields': fields, 'BhRestToken': self.rest_token}
            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
                else:
                    return None

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('data', {})
            else:
                logger.error(f"Failed to get {entity_type} {entity_id}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error getting {entity_type} {entity_id}: {e}")
            return None
    def update_entity(self, entity_type: str, entity_id: int, data: Dict) -> bool:
        if entity_type not in self.SUPPORTED_ENTITY_TYPES:
            logger.error(f"Unsupported entity type for update: {entity_type}")
            return False

        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return False

        try:
            url = f"{self.base_url}entity/{entity_type}/{entity_id}"
            params = {'BhRestToken': self.rest_token}
            response = self.session.post(url, params=params, json=data, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.post(url, params=params, json=data, timeout=30)
                else:
                    return False

            if response.status_code == 200:
                logger.info(f"Updated {entity_type} {entity_id}: {list(data.keys())}")
                return True
            else:
                logger.error(f"Failed to update {entity_type} {entity_id}: {response.status_code} - {response.text[:300]}")
                return False
        except Exception as e:
            logger.error(f"Error updating {entity_type} {entity_id}: {e}")
            return False
    def search_entity(self, entity_type: str, query: str, fields: str = None, count: int = 10) -> List[Dict]:
        if entity_type not in self.SUPPORTED_ENTITY_TYPES:
            logger.error(f"Unsupported entity type for search: {entity_type}")
            return []

        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []

        if not fields:
            fields = self.ENTITY_DEFAULT_FIELDS.get(entity_type, 'id')

        try:
            url = f"{self.base_url}search/{entity_type}"
            params = {
                'query': query,
                'fields': fields,
                'count': count,
                'BhRestToken': self.rest_token,
            }
            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
                else:
                    return []

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('data', [])
            else:
                logger.error(f"Search {entity_type} failed: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error searching {entity_type}: {e}")
            return []
    def query_entity(self, entity_type: str, where: str, fields: str = 'id', count: int = 50) -> List[Dict]:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []

        try:
            url = f"{self.base_url}query/{entity_type}"
            params = {
                'where': where,
                'fields': fields,
                'count': count,
                'BhRestToken': self.rest_token,
            }
            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
                else:
                    return []

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('data', [])
            else:
                logger.error(f"Query {entity_type} failed: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error querying {entity_type}: {e}")
            return []
    def delete_entity(self, entity_type: str, entity_id: int, soft_delete: bool = True) -> bool:
        if entity_type not in self.SUPPORTED_ENTITY_TYPES:
            logger.error(f"Unsupported entity type for delete: {entity_type}")
            return False

        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return False

        try:
            if soft_delete:
                return self.update_entity(entity_type, entity_id, {'isDeleted': True})
            else:
                url = f"{self.base_url}entity/{entity_type}/{entity_id}"
                params = {'BhRestToken': self.rest_token}
                response = self.session.delete(url, params=params, timeout=30)

                if response.status_code == 401:
                    self.rest_token = None
                    if self.authenticate():
                        params['BhRestToken'] = self.rest_token
                        response = self.session.delete(url, params=params, timeout=30)
                    else:
                        return False

                if response.status_code in (200, 204):
                    logger.info(f"Hard-deleted {entity_type} {entity_id}")
                    return True
                else:
                    logger.error(f"Failed to delete {entity_type} {entity_id}: {response.status_code} - {response.text[:300]}")
                    return False
        except Exception as e:
            logger.error(f"Error deleting {entity_type} {entity_id}: {e}")
            return False
    def bulk_update_entities(self, entity_type: str, entity_ids: List[int], data: Dict) -> Dict[int, bool]:
        results = {}
        for eid in entity_ids:
            results[eid] = self.update_entity(entity_type, eid, data)
        succeeded = sum(1 for v in results.values() if v)
        logger.info(f"Bulk update {entity_type}: {succeeded}/{len(entity_ids)} succeeded")
        return results
    def bulk_delete_entities(self, entity_type: str, entity_ids: List[int], soft_delete: bool = True) -> Dict[int, bool]:
        results = {}
        for eid in entity_ids:
            results[eid] = self.delete_entity(entity_type, eid, soft_delete=soft_delete)
        succeeded = sum(1 for v in results.values() if v)
        logger.info(f"Bulk delete {entity_type} (soft={soft_delete}): {succeeded}/{len(entity_ids)} succeeded")
        return results
    def create_entity(self, entity_type: str, data: Dict) -> Optional[int]:
        if entity_type not in self.SUPPORTED_ENTITY_TYPES:
            logger.error(f"Unsupported entity type for create: {entity_type}")
            return None

        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None

        try:
            url = f"{self.base_url}entity/{entity_type}"
            params = {'BhRestToken': self.rest_token}
            response = self.session.put(url, params=params, json=data, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.put(url, params=params, json=data, timeout=30)
                else:
                    return None

            if response.status_code in (200, 201):
                result = self._safe_json_parse(response)
                entity_id = result.get('changedEntityId')
                logger.info(f"Created {entity_type} #{entity_id}")
                return entity_id
            else:
                logger.error(f"Failed to create {entity_type}: {response.status_code} - {response.text[:300]}")
                return None
        except Exception as e:
            logger.error(f"Error creating {entity_type}: {e}")
            return None
    def add_entity_to_association(self, entity_type: str, entity_id: int,
                                  association_field: str, associated_ids: List[int]) -> bool:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return False

        try:
            ids_str = ','.join(str(i) for i in associated_ids)
            url = f"{self.base_url}entity/{entity_type}/{entity_id}/{association_field}/{ids_str}"
            params = {'BhRestToken': self.rest_token}
            response = self.session.put(url, params=params, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.put(url, params=params, timeout=30)
                else:
                    return False

            if response.status_code == 200:
                logger.info(f"Added {association_field} association on {entity_type} #{entity_id}: {ids_str}")
                return True
            else:
                logger.error(f"Failed to add association: {response.status_code} - {response.text[:300]}")
                return False
        except Exception as e:
            logger.error(f"Error adding association on {entity_type} #{entity_id}: {e}")
            return False
    def remove_entity_from_association(self, entity_type: str, entity_id: int,
                                       association_field: str, associated_ids: List[int]) -> bool:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return False

        try:
            ids_str = ','.join(str(i) for i in associated_ids)
            url = f"{self.base_url}entity/{entity_type}/{entity_id}/{association_field}/{ids_str}"
            params = {'BhRestToken': self.rest_token}
            response = self.session.delete(url, params=params, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.delete(url, params=params, timeout=30)
                else:
                    return False

            if response.status_code == 200:
                logger.info(f"Removed {association_field} association on {entity_type} #{entity_id}: {ids_str}")
                return True
            else:
                logger.error(f"Failed to remove association: {response.status_code} - {response.text[:300]}")
                return False
        except Exception as e:
            logger.error(f"Error removing association on {entity_type} #{entity_id}: {e}")
            return False
    def get_entity_associations(self, entity_type: str, entity_id: int,
                                 association_field: str, fields: str = 'id',
                                 count: int = 100) -> List[Dict]:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []

        try:
            url = f"{self.base_url}entity/{entity_type}/{entity_id}/{association_field}"
            params = {'fields': fields, 'count': count, 'BhRestToken': self.rest_token}
            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
                else:
                    return []

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('data', [])
            else:
                logger.error(f"Failed to get {association_field} on {entity_type} #{entity_id}: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error getting associations {entity_type} #{entity_id}/{association_field}: {e}")
            return []
    def query_entities(self, entity_type: str, where: str, fields: str = None,
                       count: int = 50, order_by: str = None) -> List[Dict]:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []

        if not fields:
            fields = self.ENTITY_DEFAULT_FIELDS.get(entity_type, 'id')

        try:
            url = f"{self.base_url}query/{entity_type}"
            params = {
                'where': where,
                'fields': fields,
                'count': count,
                'BhRestToken': self.rest_token,
            }
            if order_by:
                params['orderBy'] = order_by

            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
                else:
                    return []

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('data', [])
            else:
                logger.error(f"Query {entity_type} failed: {response.status_code} - {response.text[:300]}")
                return []
        except Exception as e:
            logger.error(f"Error querying {entity_type}: {e}")
            return []
    def get_entity_files(self, entity_type: str, entity_id: int) -> List[Dict]:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []

        try:
            url = f"{self.base_url}entityFiles/{entity_type}/{entity_id}"
            params = {'BhRestToken': self.rest_token}
            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
                else:
                    return []

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('EntityFiles', data.get('entityFiles', []))
            else:
                logger.error(f"Failed to get files for {entity_type} #{entity_id}: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error getting files for {entity_type} #{entity_id}: {e}")
            return []
    def delete_entity_file(self, entity_type: str, entity_id: int, file_id: int) -> bool:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return False

        try:
            url = f"{self.base_url}file/{entity_type}/{entity_id}/{file_id}"
            params = {'BhRestToken': self.rest_token}
            response = self.session.delete(url, params=params, timeout=30)

            if response.status_code == 401:
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.delete(url, params=params, timeout=30)
                else:
                    return False

            if response.status_code in (200, 204):
                logger.info(f"Deleted file #{file_id} from {entity_type} #{entity_id}")
                return True
            else:
                logger.error(f"Failed to delete file #{file_id}: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Error deleting file #{file_id} from {entity_type} #{entity_id}: {e}")
            return False
    def update_placement(self, placement_id: int, data: Dict) -> bool:
        return self.update_entity('Placement', placement_id, data)
    def update_client_contact(self, contact_id: int, data: Dict) -> bool:
        return self.update_entity('ClientContact', contact_id, data)
    def update_client_corporation(self, company_id: int, data: Dict) -> bool:
        return self.update_entity('ClientCorporation', company_id, data)
    def update_corporate_user(self, user_id: int, data: Dict) -> bool:
        return self.update_entity('CorporateUser', user_id, data)
    def get_entity_meta(self, entity_type: str, fields: str = None) -> Optional[Dict]:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None

        try:
            url = f"{self.base_url}meta/{entity_type}"
            params = {'BhRestToken': self.rest_token, 'fields': fields or '*'}

            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 401:
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
                else:
                    return None

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data
            else:
                logger.warning(f"get_entity_meta({entity_type}) failed: HTTP {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"get_entity_meta({entity_type}) error: {e}")
            return None
    def get_options(self, option_type: str) -> Optional[List[Dict]]:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None

        try:
            url = f"{self.base_url}options/{option_type}"
            params = {'BhRestToken': self.rest_token}

            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 401:
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
                else:
                    return None

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('data', data.get('optionList', []))

            if response.status_code == 400:
                logger.info(f"get_options({option_type}) returned 400 — trying meta field lookup instead")
                meta_options = self._get_options_from_meta(option_type)
                if meta_options is not None:
                    return meta_options
                logger.warning(f"get_options({option_type}) failed: not available via /options/ or meta")
                return None
            else:
                logger.warning(f"get_options({option_type}) failed: HTTP {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"get_options({option_type}) error: {e}")
            return None
    def _get_options_from_meta(self, option_type: str) -> Optional[List[Dict]]:
        OPTION_TYPE_TO_META = {
            'NoteAction': ('Note', 'action'),
            'CandidateStatus': ('Candidate', 'status'),
            'JobOrderStatus': ('JobOrder', 'status'),
            'PlacementStatus': ('Placement', 'status'),
            'SubmissionStatus': ('JobSubmission', 'status'),
            'LeadStatus': ('Lead', 'status'),
            'OpportunityStatus': ('Opportunity', 'status'),
        }

        mapping = OPTION_TYPE_TO_META.get(option_type)
        if not mapping:
            return None

        entity_type, field_name = mapping
        meta = self.get_entity_meta(entity_type, fields=field_name)
        if meta and isinstance(meta, dict):
            fields = meta.get('fields', [])
            if isinstance(fields, list):
                for f in fields:
                    if isinstance(f, dict) and f.get('name') == field_name:
                        options = f.get('options', [])
                        if isinstance(options, list):
                            return options
        return None
    def get_settings(self, setting_key: str) -> Optional[Any]:
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return None

        try:
            url = f"{self.base_url}settings/{setting_key}"
            params = {'BhRestToken': self.rest_token}

            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 401:
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
                else:
                    return None

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data
            else:
                logger.warning(f"get_settings({setting_key}) failed: HTTP {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"get_settings({setting_key}) error: {e}")
            return None