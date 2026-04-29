"""CandidatesMixin — Bullhorn API methods for this domain."""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

import requests  # noqa: F401  (used by methods via self.session)

logger = logging.getLogger(__name__)


class CandidatesMixin:
    """Mixin providing candidates-related Bullhorn API methods."""

    def search_candidates(self, email: str = None, phone: str = None, 
                          first_name: str = None, last_name: str = None) -> List[Dict]:
        """
        Search for candidates by email, phone, or name.
        Email search covers email, email2, and email3 fields with case-insensitive matching.
        Includes one retry on timeout/error.
        
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
        
        query_parts = []
        
        if email:
            normalized_email = email.strip().lower()
            query_parts.append(f'(email:"{normalized_email}" OR email2:"{normalized_email}" OR email3:"{normalized_email}")')
        if phone:
            phone_digits = ''.join(filter(str.isdigit, phone))
            if len(phone_digits) >= 10:
                query_parts.append(f'(phone:"{phone_digits}" OR mobile:"{phone_digits}")')
        if first_name and last_name:
            query_parts.append(f'(firstName:"{first_name}" AND lastName:"{last_name}")')
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
            'fields': 'id,firstName,lastName,email,email2,email3,phone,mobile,address,status,source,occupation',
            'count': 10,
            'BhRestToken': self.rest_token
        }
        
        for attempt in range(2):
            try:
                logger.info(f"🔍 Candidate search (attempt {attempt+1}/2): query={query}")
                response = self.session.get(url, params=params, timeout=30)
                
                if response.status_code == 401:
                    logger.info("Token expired during search_candidates, re-authenticating...")
                    self.rest_token = None
                    if self.authenticate():
                        params['BhRestToken'] = self.rest_token
                        response = self.session.get(url, params=params, timeout=30)
                
                if response.status_code == 200:
                    data = self._safe_json_parse(response)
                    results = data.get('data', [])
                    logger.info(f"🔍 Candidate search returned {len(results)} results")
                    return results
                else:
                    logger.error(f"Candidate search failed (attempt {attempt+1}/2): {response.status_code} - {response.text}")
                    if attempt == 0:
                        import time
                        time.sleep(1)
                        continue
                    return []
                    
            except Exception as e:
                logger.error(f"Error searching candidates (attempt {attempt+1}/2): {str(e)}")
                if attempt == 0:
                    import time
                    time.sleep(1)
                    continue
                return []
        
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
            
            if response.status_code == 401:
                logger.info("Token expired during create_candidate, re-authenticating...")
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.put(url, params=params, json=candidate_data, timeout=30)
            
            if response.status_code in [200, 201]:
                data = self._safe_json_parse(response)
                candidate_id = data.get('changedEntityId')
                logger.info(f"Created candidate {candidate_id}: {candidate_data.get('firstName')} {candidate_data.get('lastName')}")
                return candidate_id
            else:
                logger.error(f"Failed to create candidate: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating candidate: {str(e)}")
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
            
            if response.status_code == 401:
                logger.info("Token expired during update_candidate, re-authenticating...")
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.post(url, params=params, json=candidate_data, timeout=30)
            
            if response.status_code == 200:
                logger.info(f"Updated candidate {candidate_id}")
                return candidate_id
            else:
                logger.error(f"Failed to update candidate: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error updating candidate: {str(e)}")
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
            logger.info(f"Authenticating before file upload for candidate {candidate_id}")
            if not self.authenticate():
                logger.error("Failed to authenticate for file upload")
                return None
        
        try:
            # Bullhorn file upload endpoint - PUT /file/Candidate/{candidateId}
            url = f"{self.base_url}file/Candidate/{candidate_id}"
            
            logger.info(f"Uploading file to URL: {url}")
            logger.info(f"File: {filename}, Size: {len(file_content)} bytes")
            
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
            logger.info(f"Uploading file using base64 JSON method to candidate {candidate_id} ({file_size_kb:.1f} KB)")
            
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
            
            logger.info(f"File upload response status: {response.status_code}")
            logger.info(f"File upload response: {response.text[:500] if response.text else 'No response body'}")
            
            if response.status_code in [200, 201]:
                data = self._safe_json_parse(response)
                file_id = data.get('fileId')
                if file_id:
                    logger.info(f"✅ Successfully uploaded file '{filename}' to candidate {candidate_id}, file ID: {file_id}")
                    return file_id
                else:
                    # Some Bullhorn versions return changedEntityId instead
                    file_id = data.get('changedEntityId')
                    if file_id:
                        logger.info(f"✅ Successfully uploaded file '{filename}' to candidate {candidate_id}, entity ID: {file_id}")
                        return file_id
                    logger.warning(f"Upload returned success but no fileId in response: {data}")
                    return None
            else:
                logger.error(f"❌ Failed to upload file to candidate {candidate_id}: {response.status_code}")
                logger.error(f"Response: {response.text}")
                logger.error(f"File details: name={filename}, size={file_size_kb:.1f}KB, type={content_type}")
                
                # Common error diagnostics
                if response.status_code == 400:
                    logger.error("400 Bad Request - Check required fields: externalID, fileType, name, contentType")
                elif response.status_code == 500:
                    logger.error("500 Internal Server Error - File may be too large or corrupted")
                
                # If authentication expired, try once more
                if response.status_code == 401:
                    logger.info("Token expired, re-authenticating and retrying file upload...")
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
                            data = self._safe_json_parse(retry_response)
                            file_id = data.get('fileId') or data.get('changedEntityId')
                            logger.info(f"✅ Retry successful, file ID: {file_id}")
                            return file_id
                
                return None
                
        except Exception as e:
            logger.error(f"❌ Error uploading candidate file: {str(e)}", exc_info=True)
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
                logger.warning(f"Cannot create submission: job order {job_id} not found in Bullhorn")
                return None
            
            if job.get('isDeleted'):
                logger.warning(f"Cannot create submission: job order {job_id} is deleted")
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
            
            if response.status_code == 401:
                logger.info("Token expired during create_job_submission, re-authenticating...")
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.put(url, params=params, json=submission_data, timeout=30)
            
            if response.status_code in [200, 201]:
                data = self._safe_json_parse(response)
                submission_id = data.get('changedEntityId')
                logger.info(f"Created job submission {submission_id}: candidate {candidate_id} -> job {job_id}")
                return submission_id
            else:
                logger.error(f"Failed to create job submission: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating job submission: {str(e)}")
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
                'fields': 'id,firstName,lastName,email,phone,mobile,address(address1,city,state,countryName),status,source,occupation,companyName,skillSet,description',
                'BhRestToken': self.rest_token
            }
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 401:
                logger.info("Token expired during get_candidate, re-authenticating...")
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('data', {})
            else:
                logger.error(f"Failed to get candidate: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting candidate: {str(e)}")
            return None

    def get_candidate_submissions(self, candidate_id: int, count: int = 10) -> List[Dict]:
        """
        Retrieve job submissions for a candidate, ordered most-recent first.

        Each item in the returned list contains at minimum:
          {
            'id': <submission_id>,
            'jobOrder': {
                'id': <job_id>,
                'title': <job_title>,
                'owner': {'id': <corp_user_id>, 'firstName': ..., 'lastName': ...},
                'assignedUsers': {'data': [{'id': ..., 'firstName': ..., 'lastName': ...}]}
            },
            'dateAdded': <epoch_ms>
          }

        Returns an empty list on failure (caller should handle gracefully).
        """
        if not self.base_url or not self.rest_token:
            if not self.authenticate():
                return []

        try:
            url = f"{self.base_url}search/JobSubmission"
            params = {
                'query': f'candidate.id:{candidate_id}',
                'fields': (
                    'id,dateAdded,'
                    'jobOrder(id,title,owner(id,firstName,lastName),'
                    'assignedUsers(id,firstName,lastName))'
                ),
                'count': count,
                'sort': '-dateAdded',
                'BhRestToken': self.rest_token,
            }

            response = self.session.get(url, params=params, timeout=15)

            if response.status_code == 401:
                logger.info("Token expired during get_candidate_submissions, re-authenticating...")
                self.rest_token = None
                if self.authenticate():
                    params['BhRestToken'] = self.rest_token
                    response = self.session.get(url, params=params, timeout=15)

            if response.status_code == 200:
                data = self._safe_json_parse(response)
                return data.get('data', [])
            else:
                logger.warning(
                    f"get_candidate_submissions: HTTP {response.status_code} for candidate {candidate_id}"
                )
                return []

        except Exception as e:
            logger.error(f"Error fetching submissions for candidate {candidate_id}: {e}")
            return []

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
                    except Exception:
                        pass
                
                # Add end date if available (and not current job)
                if work.get('end_year') and not work.get('current'):
                    try:
                        end_date = datetime(int(work['end_year']), 12, 31)
                        work_data['endDate'] = int(end_date.timestamp() * 1000)
                    except Exception:
                        pass
                
                response = self.session.put(url, params=params, json=work_data, timeout=30)
                
                if response.status_code in [200, 201]:
                    data = self._safe_json_parse(response)
                    work_id = data.get('changedEntityId')
                    if work_id:
                        created_ids.append(work_id)
                        logger.info(f"Created work history {work_id}: {work.get('title')} at {work.get('company')}")
                else:
                    logger.warning(f"Failed to create work history: {response.status_code} - {response.text}")
                    
            except Exception as e:
                logger.error(f"Error creating work history record: {str(e)}")
        
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
                    except Exception:
                        pass
                
                response = self.session.put(url, params=params, json=edu_data, timeout=30)
                
                if response.status_code in [200, 201]:
                    data = self._safe_json_parse(response)
                    edu_id = data.get('changedEntityId')
                    if edu_id:
                        created_ids.append(edu_id)
                        logger.info(f"Created education {edu_id}: {edu.get('degree')} from {edu.get('institution')}")
                else:
                    logger.warning(f"Failed to create education: {response.status_code} - {response.text}")
                    
            except Exception as e:
                logger.error(f"Error creating education record: {str(e)}")
        
        return created_ids
