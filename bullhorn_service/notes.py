"""NotesMixin — Bullhorn API methods for this domain."""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

import requests  # noqa: F401  (used by methods via self.session)

logger = logging.getLogger(__name__)


class NotesMixin:
    """Mixin providing notes-related Bullhorn API methods."""

    def get_candidate_notes(self, candidate_id: int, action_filter: list = None,
                            since: 'datetime' = None, count: int = 10) -> list:
        """
        Retrieve notes for a candidate, optionally filtered by action and date.
        
        Args:
            candidate_id: Bullhorn candidate ID
            action_filter: List of action strings to filter by (e.g., ["AI Vetting - Qualified"])
            since: Only return notes created after this datetime
            count: Maximum number of notes to retrieve
            
        Returns:
            List of note dicts with id, action, dateAdded fields, or empty list on failure
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                logger.error(f"get_candidate_notes failed: Could not authenticate with Bullhorn")
                return []
        
        try:
            url = f"{self.base_url}entity/Candidate/{candidate_id}/notes"
            params = {
                'fields': 'id,action,dateAdded,comments',
                'count': count,
                'orderBy': '-dateAdded',
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params, timeout=15)
            
            if response.status_code == 401:
                # Token expired, re-authenticate and retry
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    url = f"{self.base_url}entity/Candidate/{candidate_id}/notes"
                    response = self.session.get(url, params=params, timeout=15)
                else:
                    return []
            
            if response.status_code != 200:
                logger.warning(f"get_candidate_notes failed: HTTP {response.status_code}")
                return []
            
            data = self._safe_json_parse(response)
            notes = data.get('data', [])
            
            # Filter by action if specified
            if action_filter:
                notes = [n for n in notes if n.get('action') in action_filter]
            
            # Filter by date if specified
            if since:
                since_ms = int(since.timestamp() * 1000)
                notes = [n for n in notes if n.get('dateAdded', 0) >= since_ms]
            
            return notes
            
        except Exception as e:
            logger.error(f"Error retrieving notes for candidate {candidate_id}: {str(e)}")
            return []
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
                logger.error(f"Note creation failed: Could not authenticate with Bullhorn")
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
        
        logger.info(f"📝 Creating note for candidate {candidate_id}: action='{action}', length={len(note_text)} chars, user_id={self.user_id}")
        
        for approach_name, note_data in approaches:
            try:
                logger.info(f"Trying note creation approach: {approach_name}")
                response = self.session.put(url, params=params, json=note_data, timeout=60)
                
                if response.status_code in [200, 201]:
                    data = self._safe_json_parse(response)
                    note_id = data.get('changedEntityId')
                    logger.info(f"✅ Created note {note_id} on candidate {candidate_id} using '{approach_name}' approach")
                    return note_id
                elif response.status_code == 401:
                    # Token expired, re-authenticate and retry this approach
                    logger.warning(f"Note creation got 401, re-authenticating...")
                    self.rest_token = None
                    if self.authenticate():
                        url = f"{self.base_url}entity/Note"  # Update URL in case base_url changed
                        params = {'BhRestToken': self.rest_token}
                        response = self.session.put(url, params=params, json=note_data, timeout=60)
                        if response.status_code in [200, 201]:
                            data = self._safe_json_parse(response)
                            note_id = data.get('changedEntityId')
                            logger.info(f"✅ Created note {note_id} on candidate {candidate_id} using '{approach_name}' after re-auth")
                            return note_id
                    else:
                        # Re-auth failed - abort all attempts
                        logger.error(f"❌ Re-authentication failed - aborting note creation for candidate {candidate_id}")
                        return None
                
                # Log failure details and try next approach
                error_text = response.text[:500] if response.text else "No error details"
                logger.warning(f"⚠️ Note creation failed with '{approach_name}': HTTP {response.status_code} - {error_text}")
                
            except Exception as e:
                logger.error(f"❌ Exception in note creation approach '{approach_name}': {str(e)}")
        
        logger.error(f"❌ All note creation approaches failed for candidate {candidate_id}")
        return None
