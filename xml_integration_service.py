"""
XML Integration Service for Bullhorn Job Data
Handles mapping Bullhorn job data to XML format and managing XML file updates
"""

import os
import logging
import re
import shutil
import time
import threading
import urllib.parse
import html
from typing import Dict, List, Optional
from datetime import datetime
try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree
from xml_processor import XMLProcessor
from job_classification_service import JobClassificationService
from xml_safeguards import XMLSafeguards
from tearsheet_config import TearsheetConfig


class XMLIntegrationService:
    """Service for integrating Bullhorn job data with XML files"""
    
    @staticmethod
    def format_linkedin_recruiter_tag(assigned_users: List[Dict]) -> str:
        """
        Centralized LinkedIn recruiter tag formatter - ensures consistent format across all code paths
        Returns exactly "#LI-XXn:" without trailing names
        
        Args:
            assigned_users: List of assigned user dictionaries from Bullhorn
            
        Returns:
            str: LinkedIn tag in format "#LI-XXn:" or empty string
        """
        if not assigned_users or not isinstance(assigned_users, list):
            return ""
        
        for user in assigned_users:
            if isinstance(user, dict) and user.get('linkedInCompanyID'):
                company_id = str(user.get('linkedInCompanyID', '')).strip()
                if company_id:
                    # Return strictly the LinkedIn tag without any names
                    return f"#LI-{company_id}:"
        
        return ""
    
    @staticmethod
    def sanitize_linkedin_recruiter_tag(assignedrecruiter_value: str) -> str:
        """
        Defensive sanitizer to strip trailing names from existing assignedrecruiter values
        Ensures format is exactly "#LI-XXn:" with no trailing content
        
        Args:
            assignedrecruiter_value: Raw assignedrecruiter field value
            
        Returns:
            str: Sanitized LinkedIn tag or empty string if not a valid LinkedIn tag
        """
        if not assignedrecruiter_value:
            return ""
        
        # If it's a LinkedIn tag with or without trailing content, extract just the tag part
        # Relaxed pattern to handle future tag formats: #LI-[anything]:
        linkedin_match = re.match(r'^(#LI-[^:]+:).*$', assignedrecruiter_value.strip())
        if linkedin_match:
            return linkedin_match.group(1)  # Return just the tag part
        
        # If it's not a LinkedIn tag, return empty string to prevent names from leaking
        return ""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.xml_processor = XMLProcessor()
        self.job_classifier = JobClassificationService()
        self.safeguards = XMLSafeguards()
        self._parser = etree.XMLParser(strip_cdata=False, recover=True)
        # Store field changes for notifications
        self._last_field_changes = {}
        # Cache for recruiter mappings
        self._recruiter_cache = {}
        # Thread lock for preventing concurrent XML modifications
        self._xml_lock = threading.Lock()
    
    def map_bullhorn_jobs_to_xml_batch(self, jobs_data: List[Dict], existing_references: Dict = None, enable_ai_classification: bool = True, monitor_names: Dict = None) -> List[Dict]:
        """
        Map multiple Bullhorn jobs to XML format with batch AI classification to prevent timeouts
        
        Args:
            jobs_data: List of Bullhorn job dictionaries
            existing_references: Dict mapping job_id to existing reference numbers
            enable_ai_classification: Whether to perform AI classification in batch mode
            monitor_names: Dict mapping job_id to monitor names for proper branding
            
        Returns:
            List of XML job dictionaries
        """
        if not jobs_data:
            return []
        
        existing_references = existing_references or {}
        monitor_names = monitor_names or {}
        xml_jobs = []
        
        # Perform batch AI classification if enabled with timeout protection
        ai_results = {}
        if enable_ai_classification:
            import time
            start_time = time.time()
            MAX_AI_PROCESSING_TIME = 25  # Max 25 seconds to leave buffer for XML generation
            
            self.logger.info(f"Starting batch AI classification for {len(jobs_data)} jobs (max {MAX_AI_PROCESSING_TIME}s)...")
            
            # Prepare jobs for batch AI classification
            jobs_for_ai = []
            for job_data in jobs_data:
                job_id = str(job_data.get('id', ''))
                title = job_data.get('title', 'Untitled Position')
                
                # Clean title same way as individual mapping
                title = html.unescape(title).replace('/', ' ')
                clean_title = re.sub(r'\s*\([A-Z]+-?\d+\)\s*', '', title)
                clean_title = re.sub(r'^Local to [A-Z]{2}[^,]*,\s*', '', clean_title)
                clean_title = re.sub(r'\s*\([^)]+\)\s*', ' ', clean_title).strip()
                
                # Extract description
                description = job_data.get('publicDescription', '') or job_data.get('description', '')
                
                jobs_for_ai.append({
                    'id': job_id,
                    'title': clean_title,
                    'description': description
                })
            
            try:
                # 🛡️ HARD TIMEOUT WRAPPER - Guarantees completion within worker timeout limit
                import concurrent.futures
                import threading
                
                def run_ai_classification():
                    return self.job_classifier.classify_jobs_batch(
                        jobs_for_ai, 
                        batch_size=4,
                        max_processing_time=MAX_AI_PROCESSING_TIME
                    )
                
                # Execute with hard timeout enforcement
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(run_ai_classification)
                    try:
                        ai_classifications = future.result(timeout=MAX_AI_PROCESSING_TIME)
                        
                        # Map results by job ID for easy lookup
                        for i, job_data in enumerate(jobs_data):
                            job_id = str(job_data.get('id', ''))
                            if i < len(ai_classifications):
                                ai_results[job_id] = ai_classifications[i]
                        
                        elapsed_time = time.time() - start_time
                        self.logger.info(f"🎉 Batch AI classification completed for {len(ai_results)} jobs in {elapsed_time:.1f}s")
                        
                    except concurrent.futures.TimeoutError:
                        elapsed_time = time.time() - start_time
                        self.logger.warning(f"⏰ AI classification hard timeout reached after {elapsed_time:.1f}s - ensuring XML generation completes")
                        future.cancel()  # Attempt to cancel the running task
                        ai_results = {}  # Empty results = no AI classification
                
            except Exception as ai_error:
                elapsed_time = time.time() - start_time
                self.logger.warning(f"AI classification failed after {elapsed_time:.1f}s: {str(ai_error)}")
                self.logger.info("Proceeding without AI classification to ensure XML generation completes")
                ai_results = {}  # Empty results = no AI classification
        
        # Process each job with its AI classification result
        for job_data in jobs_data:
            job_id = str(job_data.get('id', ''))
            monitor_name = monitor_names.get(job_id, '')
            existing_ref = existing_references.get(job_id, '')
            ai_result = ai_results.get(job_id, None)
            
            # Map individual job with pre-computed AI classification
            xml_job = self._map_single_job_with_ai_result(
                job_data,
                existing_reference_number=existing_ref,
                monitor_name=monitor_name,
                ai_classification_result=ai_result
            )
            
            if xml_job:
                xml_jobs.append(xml_job)
        
        self.logger.info(f"Mapped {len(xml_jobs)} jobs to XML format with batch AI classification")
        return xml_jobs
    
    def _map_single_job_with_ai_result(self, bullhorn_job: Dict, existing_reference_number: str = '', monitor_name: str = '', ai_classification_result: Dict = None) -> Dict:
        """
        Map a single job with a pre-computed AI classification result
        This is used by the batch processing method to avoid individual AI calls
        """
        # Use existing AI fields logic but with pre-computed result
        if ai_classification_result and ai_classification_result.get('success'):
            existing_ai_fields = {
                'jobfunction': ai_classification_result.get('job_function', ''),
                'jobindustries': ai_classification_result.get('industries', ''),
                'senioritylevel': ai_classification_result.get('seniority_level', '')
            }
            skip_ai = False  # We have AI data
        else:
            existing_ai_fields = None
            skip_ai = True  # CRITICAL: Force skip AI to prevent fallback individual calls
        
        # Call the existing method with AI results provided as existing fields
        return self.map_bullhorn_job_to_xml(
            bullhorn_job,
            existing_reference_number=existing_reference_number,
            monitor_name=monitor_name,
            skip_ai_classification=skip_ai,  # CRITICAL: Skip AI when batch failed
            existing_ai_fields=existing_ai_fields
        )
    
    def map_bullhorn_job_to_xml(self, bullhorn_job: Dict, existing_reference_number: Optional[str] = None, monitor_name: Optional[str] = None, skip_ai_classification: bool = False, existing_ai_fields: Optional[Dict] = None) -> Dict:
        """
        Map Bullhorn job data to XML job structure
        
        Args:
            bullhorn_job: Dictionary containing Bullhorn job data
            existing_reference_number: Optional existing reference number to preserve
            monitor_name: Optional monitor name to determine company assignment
            
        Returns:
            Dict: XML job structure with CDATA wrapped values
        """
        try:
            # Extract basic job information
            job_id = str(bullhorn_job.get('id', ''))
            title = bullhorn_job.get('title', 'Untitled Position')
            
            # HTML ENTITY FIX: Decode HTML entities in job titles (e.g., &amp; → &)
            title = html.unescape(title)
            
            # CRITICAL FIX: Remove forward slashes from job titles
            # Replace forward slash with space to prevent URL and XML issues
            title = title.replace('/', ' ')
            
            # Ensure job_id is not empty for validation
            if not job_id or job_id == 'None':
                self.logger.error(f"Invalid job ID: {job_id} for job: {title}")
                raise ValueError(f"Job ID cannot be None or empty")
            
            # Extract clean job title - remove any existing job ID or code in parentheses
            # This handles cases like "Local to MN-Hybrid, Attorney (J.D)(W-4499)" -> "Attorney"
            # First remove any (W-####) or similar codes
            clean_title = re.sub(r'\s*\([A-Z]+-?\d+\)\s*', '', title)
            # Then remove any location prefixes like "Local to XX-Hybrid, "
            clean_title = re.sub(r'^Local to [A-Z]{2}[^,]*,\s*', '', clean_title)
            # Remove any other parenthetical content like (J.D)
            clean_title = re.sub(r'\s*\([^)]+\)\s*', ' ', clean_title).strip()
            
            # Add job ID in parentheses to the cleaned title
            formatted_title = f"{clean_title} ({job_id})"
            
            # Log the field mapping for debugging
            self.logger.debug(f"Mapping job {job_id}: title='{formatted_title}'")
            
            # Determine company name based on tearsheet/monitor using configuration
            company_name = TearsheetConfig.get_company_name(monitor_name)
            if monitor_name and 'STSI' in monitor_name:
                self.logger.info(f"Job {job_id} from STSI tearsheet - using company: {company_name}")
            
            # Extract location information ONLY from Bullhorn structured address fields
            # Never fallback to job description parsing for location data
            address = bullhorn_job.get('address', {})
            city = address.get('city', '') if address else ''
            state = address.get('state', '') if address else ''
            # Use countryName field for country mapping, handle country ID conversion
            country_raw = address.get('countryName', 'United States') if address else 'United States'
            country = self._map_country_id_to_name(country_raw)
            
            # Ensure None values are converted to empty strings
            city = city if city is not None else ''
            state = state if state is not None else ''
            country = country if country is not None else 'United States'
            
            # Handle employment type mapping
            employment_type = bullhorn_job.get('employmentType', '')
            job_type = self._map_employment_type(employment_type)
            
            # Handle remote type mapping  
            onsite_value = bullhorn_job.get('onSite', '')
            remote_type = self._map_remote_type(onsite_value)
            
            # Extract assigned recruiter from multiple possible fields
            # First try to get recruiter from assignments field (per user mapping requirements)
            assignments = bullhorn_job.get('assignments', {})
            assigned_users = bullhorn_job.get('assignedUsers', {})
            response_user = bullhorn_job.get('responseUser', {})
            owner = bullhorn_job.get('owner', {})
            
            assigned_recruiter = self._extract_assigned_recruiter(assignments, assigned_users, response_user, owner)
            
            # Extract description - use publicDescription field specifically as requested
            # The publicDescription field contains the public-facing job description from Bullhorn
            description = bullhorn_job.get('publicDescription', '') or bullhorn_job.get('description', '')
            
            # Clean up description - remove excessive whitespace and format for XML
            description = self._clean_description(description)
            
            # Format date
            date_added = bullhorn_job.get('dateAdded', '')
            formatted_date = self._format_date(date_added)
            
            # Use existing reference number if provided, otherwise generate new one
            if existing_reference_number:
                reference_number = existing_reference_number
            else:
                reference_number = self.xml_processor.generate_reference_number()
            
            # Use the actual Bullhorn job ID for bhatsid (critical for matching/updating)
            bhatsid = str(job_id)
            
            # Generate unique job application URL with proper domain based on company
            job_url = self._generate_job_application_url(bhatsid, clean_title, company_name)
            
            # Handle AI classification - preserve existing values if provided
            if existing_ai_fields and existing_ai_fields.get('jobfunction') and existing_ai_fields.get('jobindustries') and existing_ai_fields.get('senioritylevel'):
                # Use existing AI-classified values to maintain consistency (like reference numbers)
                job_function = existing_ai_fields.get('jobfunction', '')
                job_industry = existing_ai_fields.get('jobindustries', '')
                seniority_level = existing_ai_fields.get('senioritylevel', '')
                self.logger.info(f"✅ PRESERVING existing AI classification for job {job_id}: Function={job_function}, Industry={job_industry}, Seniority={seniority_level}")
            elif skip_ai_classification:
                # For real-time monitoring, use empty values to prevent timeouts
                job_function = ''
                job_industry = ''
                seniority_level = ''
                self.logger.debug(f"Skipped AI classification for job {job_id} during real-time sync")
            else:
                # Full AI classification for new jobs or jobs missing these fields
                classification = self.job_classifier.classify_job(clean_title, description)
                job_function = classification.get('job_function', '')
                job_industry = classification.get('industries', '')  # Fixed: using 'industries' not 'job_industry'
                seniority_level = classification.get('seniority_level', '')
                self.logger.info(f"Generated new AI classification for job {job_id}: Function={job_function}, Industry={job_industry}, Seniority={seniority_level}")
            
            # Helper function to clean field values
            def clean_field_value(value):
                """Convert None to empty string, ensure string type"""
                if value is None:
                    return ''
                return str(value)
            
            # Create XML job structure
            xml_job = {
                'title': clean_field_value(formatted_title),
                'company': clean_field_value(company_name),
                'date': clean_field_value(formatted_date),
                'referencenumber': clean_field_value(reference_number),
                'bhatsid': clean_field_value(bhatsid),
                'url': clean_field_value(job_url),
                'description': clean_field_value(description),
                'jobtype': clean_field_value(job_type),
                'city': clean_field_value(city),
                'state': clean_field_value(state),
                'country': clean_field_value(country),
                'category': '',  # Empty as per template
                'apply_email': clean_field_value('apply@myticas.com'),
                'remotetype': clean_field_value(remote_type),
                'assignedrecruiter': clean_field_value(assigned_recruiter),
                'jobfunction': clean_field_value(job_function),
                'jobindustries': clean_field_value(job_industry),
                'senioritylevel': clean_field_value(seniority_level)
            }
            
            self.logger.info(f"Mapped Bullhorn job {job_id} ({title}) to XML format")
            return xml_job
            
        except Exception as e:
            self.logger.error(f"Error mapping Bullhorn job to XML: {str(e)}")
            return {}
    
    def _generate_job_application_url(self, bhatsid: str, clean_title: str, company_name: str = None) -> str:
        """
        Generate unique job application URL for each job
        
        Args:
            bhatsid: Job ID from Bullhorn
            clean_title: Clean job title without ID
            company_name: Company name to determine domain (STSI vs Myticas)
            
        Returns:
            str: Unique job application URL in format:
                 https://apply.myticas.com/[bhatsid]/[title]/?source=LinkedIn
                 https://apply.stsi.com/[bhatsid]/[title]/?source=LinkedIn (for STSI jobs)
        """
        try:
            # Validate inputs to prevent fallback to generic URL
            if not bhatsid or not str(bhatsid).strip():
                self.logger.warning(f"Missing bhatsid for URL generation, using 'unknown': {bhatsid}")
                bhatsid = "unknown"
            
            if not clean_title or not str(clean_title).strip():
                self.logger.warning(f"Missing clean_title for URL generation, using 'position': {clean_title}")
                clean_title = "position"
            
            # URL encode the title to handle special characters and spaces
            # Replace forward slashes with hyphens to prevent URL parsing issues
            # This prevents 404 errors when job titles contain "/" characters
            safe_title = str(clean_title).strip().replace('/', '-')
            encoded_title = urllib.parse.quote(safe_title, safe='')
            
            # Determine base URL based on company
            import os
            if company_name and 'STSI' in company_name:
                base_url = 'https://apply.stsigroup.com'
            else:
                base_url = os.environ.get('JOB_APPLICATION_BASE_URL', 'https://apply.myticas.com')
            
            # Generate the unique URL  
            job_url = f"{base_url}/{str(bhatsid).strip()}/{encoded_title}/?source=LinkedIn"
            
            self.logger.debug(f"Generated unique URL for job {bhatsid}: {job_url}")
            return job_url
            
        except Exception as e:
            self.logger.error(f"Error generating job application URL for {bhatsid} ({clean_title}): {str(e)}")
            # More specific fallback with job ID if available
            try:
                if bhatsid and str(bhatsid).strip():
                    import os
                    # Determine fallback base URL
                    if company_name and 'STSI' in company_name:
                        base_url = 'https://apply.stsigroup.com'
                    else:
                        base_url = os.environ.get('JOB_APPLICATION_BASE_URL', 'https://apply.myticas.com')
                    fallback_url = f"{base_url}/{str(bhatsid).strip()}/position/?source=LinkedIn"
                    self.logger.warning(f"Using fallback URL with job ID: {fallback_url}")
                    return fallback_url
            except:
                pass
            # Final fallback to generic URL
            return "https://myticas.com/"

    def _map_employment_type(self, employment_type: str) -> str:
        """Map Bullhorn employment type to XML job type"""
        employment_type = employment_type.lower() if employment_type else ''
        
        if 'contract to hire' in employment_type or 'contract-to-hire' in employment_type:
            return 'Contract to Hire'
        elif 'direct hire' in employment_type or 'permanent' in employment_type or 'full-time' in employment_type:
            return 'Direct Hire'
        elif 'contract' in employment_type:
            return 'Contract'
        else:
            return 'Contract'  # Default fallback
    
    def _validate_job_data(self, bullhorn_job: Dict) -> bool:
        """Validate that essential job data is present"""
        try:
            job_id = bullhorn_job.get('id')
            title = bullhorn_job.get('title')
            
            if not job_id or not title:
                self.logger.error(f"Missing essential job data - ID: {job_id}, Title: {title}")
                return False
            
            return True
        except Exception as e:
            self.logger.error(f"Error validating job data: {str(e)}")
            return False
    
    def _verify_job_added_to_xml(self, xml_file_path: str, job_id: str, expected_title: str) -> bool:
        """Verify that a job was actually added to the XML file"""
        try:
            with open(xml_file_path, 'rb') as f:
                tree = etree.parse(f, self._parser)
            
            root = tree.getroot()
            
            # Look for job by bhatsid (most reliable) - handle CDATA properly
            for job in root.xpath('.//job'):
                bhatsid_elem = job.find('.//bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text:
                    bhatsid_text = bhatsid_elem.text.strip()
                    # Remove CDATA wrapper if present
                    if '<![CDATA[' in bhatsid_text:
                        bhatsid_text = bhatsid_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                    
                    if bhatsid_text == str(job_id):
                        self.logger.debug(f"Verified job {job_id} exists in XML by bhatsid")
                        return True
            
            # Alternative verification - look for job ID in title
            for job in root.xpath('.//job'):
                title_elem = job.find('.//title')
                if title_elem is not None and title_elem.text:
                    title_text = title_elem.text
                    # Remove CDATA wrapper if present
                    if '<![CDATA[' in title_text:
                        title_text = title_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                    
                    if f"({job_id})" in title_text or str(job_id) in title_text:
                        self.logger.debug(f"Verified job {job_id} exists in XML by title")
                        return True
            
            self.logger.error(f"Job {job_id} not found in XML file during verification")
            return False
            
        except Exception as e:
            self.logger.error(f"Error verifying job {job_id} in XML: {str(e)}")
            return False
    
    def _map_remote_type(self, onsite_value) -> str:
        """Map Bullhorn onSite value to XML remote type"""
        # Enhanced logging for debugging - ALWAYS log at INFO level for job 34219
        self.logger.info(f"Mapping onSite value: {onsite_value} (type: {type(onsite_value)})")
        
        # Handle both list and string formats from Bullhorn
        if isinstance(onsite_value, list):
            # If it's a list, take the first item
            onsite_value = onsite_value[0] if onsite_value else ''
        
        # Convert to string and lowercase for comparison
        onsite_value = str(onsite_value).lower() if onsite_value else ''
        
        # Log the processed value
        self.logger.info(f"Processed onSite value for comparison: '{onsite_value}'")
        
        # Enhanced mapping with more variations
        # IMPORTANT: The value "Remote" from Bullhorn should map to Remote in XML
        if 'remote' in onsite_value or onsite_value == 'offsite':
            result = 'Remote'
        elif 'hybrid' in onsite_value:
            result = 'Hybrid'  
        elif 'onsite' in onsite_value or 'on-site' in onsite_value or 'on site' in onsite_value:
            result = 'Onsite'
        elif 'off-site' in onsite_value or 'off site' in onsite_value:
            result = 'Off-Site'
        elif 'no preference' in onsite_value:
            result = 'No Preference'
        elif onsite_value == '':
            # Default for empty values - but log a warning
            self.logger.warning(f"Empty onSite value detected - defaulting to Onsite")
            result = 'Onsite'  
        else:
            self.logger.warning(f"Unknown onSite value '{onsite_value}' - defaulting to Onsite")
            result = 'Onsite'  # Default fallback
        
        self.logger.info(f"Mapped onSite '{onsite_value}' to remotetype '{result}'")
        return result
    
    def _map_country_id_to_name(self, country_value) -> str:
        """Map Bullhorn country ID or name to proper country name for XML"""
        # Handle None or empty values
        if not country_value:
            return 'United States'
            
        # Convert to string for processing
        country_str = str(country_value).strip()
        
        # Country ID to name mapping (common Bullhorn country IDs)
        country_mapping = {
            '1': 'United States',
            '2': 'Canada', 
            '3': 'Mexico',
            '4': 'United Kingdom',
            '5': 'Germany',
            '6': 'France',
            '7': 'Australia',
            '8': 'Japan',
            '9': 'India',
            '10': 'China'
        }
        
        # If it's a numeric ID, map it to country name
        if country_str.isdigit():
            mapped_country = country_mapping.get(country_str, 'United States')
            self.logger.info(f"Mapped country ID '{country_str}' to '{mapped_country}'")
            return mapped_country
        
        # If it's already a country name, clean it up and return
        clean_country = country_str.strip()
        
        # Handle common variations and clean up formatting
        if 'united states' in clean_country.lower() or clean_country.lower() == 'usa' or clean_country.lower() == 'us':
            return 'United States'
        elif 'canada' in clean_country.lower():
            return 'Canada'
        elif 'united kingdom' in clean_country.lower() or clean_country.lower() == 'uk':
            return 'United Kingdom'
        
        # Return cleaned country name if it looks valid
        if len(clean_country) > 1 and clean_country.replace(' ', '').isalpha():
            return clean_country
        
        # Default fallback
        self.logger.warning(f"Unknown country value '{country_value}' - defaulting to United States")
        return 'United States'
    
    def _extract_assigned_recruiter(self, assignments, assigned_users, response_user, owner) -> str:
        """Extract recruiter name from multiple possible fields and map to LinkedIn-style tag WITH name"""
        try:
            recruiter_name = ''
            
            # Debug logging to see what data we're actually getting
            self.logger.debug(f"RECRUITER DEBUG - assignments: {assignments}")
            self.logger.debug(f"RECRUITER DEBUG - assigned_users: {assigned_users}")
            self.logger.debug(f"RECRUITER DEBUG - response_user: {response_user}")
            self.logger.debug(f"RECRUITER DEBUG - owner: {owner}")
            
            # Check assignments first (per user mapping requirements)
            if assignments and isinstance(assignments, dict):
                assignments_data = assignments.get('data', [])
                if isinstance(assignments_data, list) and assignments_data:
                    first_assignment = assignments_data[0]
                    if isinstance(first_assignment, dict):
                        assigned_to = first_assignment.get('assignedTo', {})
                        if assigned_to and isinstance(assigned_to, dict):
                            first_name = assigned_to.get('firstName', '')
                            last_name = assigned_to.get('lastName', '')
                            if first_name or last_name:
                                recruiter_name = f"{first_name} {last_name}".strip()
                                self.logger.debug(f"RECRUITER DEBUG - Found from assignments: {recruiter_name}")
            
            # Check assignedUsers if no assignments found (array of users)
            if not recruiter_name and assigned_users and isinstance(assigned_users, dict):
                users_data = assigned_users.get('data', [])
                if isinstance(users_data, list) and users_data:
                    first_user = users_data[0]
                    if isinstance(first_user, dict):
                        first_name = first_user.get('firstName', '')
                        last_name = first_user.get('lastName', '')
                        if first_name or last_name:
                            recruiter_name = f"{first_name} {last_name}".strip()
                            self.logger.debug(f"RECRUITER DEBUG - Found from assignedUsers: {recruiter_name}")
            
            # Check responseUser next (single user)
            if not recruiter_name and response_user and isinstance(response_user, dict):
                first_name = response_user.get('firstName', '')
                last_name = response_user.get('lastName', '')
                if first_name or last_name:
                    recruiter_name = f"{first_name} {last_name}".strip()
                    self.logger.debug(f"RECRUITER DEBUG - Found from responseUser: {recruiter_name}")
            
            # Finally check owner (single user)
            if not recruiter_name and owner and isinstance(owner, dict):
                first_name = owner.get('firstName', '')
                last_name = owner.get('lastName', '')
                if first_name or last_name:
                    recruiter_name = f"{first_name} {last_name}".strip()
                    self.logger.debug(f"RECRUITER DEBUG - Found from owner: {recruiter_name}")
            
            # Apply recruiter name to LinkedIn-style tag mapping with full name
            if recruiter_name:
                linkedin_tag = self._map_recruiter_to_linkedin_tag(recruiter_name)
                self.logger.debug(f"RECRUITER DEBUG - Final LinkedIn tag: {linkedin_tag}")
                return linkedin_tag
            else:
                self.logger.debug(f"RECRUITER DEBUG - No recruiter name found, returning empty")
            
            return ''
            
        except Exception as e:
            self.logger.warning(f"Error extracting assigned recruiter: {str(e)}")
            return ''
    
    def _map_recruiter_to_linkedin_tag(self, recruiter_name: str) -> str:
        """Map recruiter names to LinkedIn-style tags for XML using database mappings"""
        try:
            # Import RecruiterMapping here to avoid circular imports
            from app import RecruiterMapping, db, app
            
            # Use application context to query database
            with app.app_context():
                # First try exact match
                mapping = RecruiterMapping.query.filter_by(recruiter_name=recruiter_name).first()
                
                # If no exact match, try case-insensitive match
                if not mapping:
                    all_mappings = RecruiterMapping.query.all()
                    for m in all_mappings:
                        if m.recruiter_name.lower() == recruiter_name.lower():
                            mapping = m
                            break
                
                # If we found a mapping, return only the tag portion (no name)
                if mapping:
                    return mapping.linkedin_tag
                
                # If no mapping found, log and return the original name
                self.logger.info(f"No LinkedIn tag mapping found for recruiter: {recruiter_name}")
                return recruiter_name
                
        except Exception as e:
            self.logger.warning(f"Error querying recruiter mapping database: {str(e)}")
            # Fallback to just returning the name if database query fails
            return recruiter_name
    
    def _clean_description(self, description: str) -> str:
        """Clean and format job description for XML with proper HTML formatting"""
        if not description:
            return ''
        
        import html as html_module
        
        # Convert HTML entities back to proper HTML tags for consistent formatting
        # This ensures &lt;strong&gt; becomes <strong>, &lt;p&gt; becomes <p>, etc.
        description = html_module.unescape(description)
        
        # CRITICAL FIX: Clean up orphaned closing/opening tags BEFORE parsing
        # These often appear at the beginning of lines from improperly extracted content
        # Remove orphaned closing tags at the beginning of lines
        description = re.sub(r'^[\s]*</li>', '', description, flags=re.MULTILINE)
        # Remove orphaned </li><li> patterns that create invalid HTML
        description = re.sub(r'</li>[\s]*<li>', '</li>\n<li>', description)
        
        # If we have list items without a container, wrap them
        if '<li>' in description and not ('<ul>' in description or '<ol>' in description):
            # Find all list items and wrap them in a ul container
            # This prevents orphaned list items from causing issues
            li_pattern = r'(<li>.*?</li>)'
            list_blocks = re.findall(li_pattern, description, re.DOTALL)
            if list_blocks:
                # Replace the first occurrence with wrapped version
                first_block = ''.join(list_blocks)
                wrapped_block = f'<ul>{first_block}</ul>'
                description = re.sub(li_pattern, '', description, flags=re.DOTALL)
                description = wrapped_block + description
        
        try:
            from lxml import html
            
            # Parse the description as an HTML fragment
            # This automatically fixes unclosed tags like <li>, <p>, etc.
            fragment = html.fragment_fromstring(description, create_parent='div')
            
            # Convert back to string with proper HTML serialization
            fixed_description = html.tostring(fragment, encoding='unicode', method='html')
            
            # Remove the wrapper div we added
            if fixed_description.startswith('<div>') and fixed_description.endswith('</div>'):
                fixed_description = fixed_description[5:-6]
            
            # Clean up any excessive whitespace while preserving structure
            fixed_description = fixed_description.strip()
            
            # FINAL CLEANUP: Remove any remaining orphaned patterns
            # Sometimes lxml doesn't catch everything, so do a final pass
            fixed_description = re.sub(r'^[\s]*</li>', '', fixed_description, flags=re.MULTILINE)
            fixed_description = re.sub(r'</li>[\s]*<li>', '</li>\n<li>', fixed_description)
            
            # Log if HTML fixes were applied
            if '</li>' in fixed_description and '<li>' in fixed_description:
                li_count = fixed_description.count('<li>')
                li_close_count = fixed_description.count('</li>')
                if li_count != li_close_count:
                    self.logger.error(f"HTML list formatting STILL BROKEN: {li_count} <li> tags, {li_close_count} </li> tags")
                else:
                    self.logger.info(f"✅ HTML list formatting FIXED: {li_count} <li> tags with matching closing tags")
            else:
                self.logger.info(f"📝 HTML description processed (no lists found): {len(fixed_description)} chars")
            
            return fixed_description
            
        except Exception as e:
            self.logger.warning(f"Error fixing HTML with lxml, returning original: {str(e)}")
            # If lxml fails for any reason, return the original description
            # Remove excessive whitespace at least
            return ' '.join(description.split())
    
    def _format_date(self, date_str: str) -> str:
        """Format date for XML (e.g., 'July 12, 2025')"""
        try:
            if not date_str:
                return datetime.now().strftime('%B %d, %Y')
            
            # Bullhorn dates are typically in timestamp format
            if isinstance(date_str, (int, float)):
                date_obj = datetime.fromtimestamp(date_str / 1000)  # Convert from milliseconds
            else:
                # Try to parse various date formats
                try:
                    date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                except:
                    date_obj = datetime.now()
            
            return date_obj.strftime('%B %d, %Y')
            
        except Exception as e:
            self.logger.warning(f"Error formatting date {date_str}: {str(e)}")
            return datetime.now().strftime('%B %d, %Y')
    
    def _clean_extra_whitespace(self, xml_file_path: str):
        """Clean up extra blank lines in XML file"""
        try:
            with open(xml_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Remove multiple consecutive blank lines between publisherurl and first job
            content = re.sub(r'</publisherurl>\n\s*\n+\s*<job>', '</publisherurl>\n  <job>', content)
            
            with open(xml_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
                
        except Exception as e:
            self.logger.error(f"Error cleaning whitespace: {str(e)}")
    
    def sort_xml_jobs_by_date(self, xml_file_path: str, newest_first: bool = True) -> bool:
        """
        Sort all jobs in the XML file by date
        
        Args:
            xml_file_path: Path to the XML file
            newest_first: If True, sort newest jobs first (default), if False, oldest first
            
        Returns:
            bool: True if sorting was successful, False otherwise
        """
        try:
            # Parse existing XML
            with open(xml_file_path, 'rb') as f:
                tree = etree.parse(f, self._parser)
            
            root = tree.getroot()
            
            # Find all job elements
            jobs = root.findall('job')
            
            if not jobs:
                self.logger.info("No jobs found in XML file to sort")
                return True
            
            # Create a list of (date_obj, job_element) tuples for sorting
            job_date_pairs = []
            
            for job in jobs:
                date_element = job.find('date')
                if date_element is not None and date_element.text:
                    # Parse the date string (format: "July 16, 2025")
                    date_text = date_element.text.strip()
                    try:
                        date_obj = datetime.strptime(date_text, '%B %d, %Y')
                        job_date_pairs.append((date_obj, job))
                    except ValueError:
                        # If date parsing fails, use current date as fallback
                        self.logger.warning(f"Failed to parse date: {date_text}, using current date")
                        job_date_pairs.append((datetime.now(), job))
                else:
                    # If no date found, use current date as fallback
                    job_date_pairs.append((datetime.now(), job))
            
            # Sort by date (newest first if newest_first=True, oldest first if False)
            job_date_pairs.sort(key=lambda x: x[0], reverse=newest_first)
            
            # Remove all existing job elements from the XML
            for job in jobs:
                root.remove(job)
            
            # Find the insertion point (after publisherurl)
            publisher_url = root.find('publisherurl')
            if publisher_url is None:
                self.logger.error("No publisherurl element found in XML")
                return False
            
            publisher_url_index = list(root).index(publisher_url)
            
            # Insert sorted jobs back into the XML
            for i, (date_obj, job) in enumerate(job_date_pairs):
                # Ensure proper spacing
                if i == 0:
                    publisher_url.tail = "\n  "
                job.tail = "\n  "
                
                root.insert(publisher_url_index + 1 + i, job)
            
            # Write the sorted XML back to file
            with open(xml_file_path, 'wb') as f:
                tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
            
            # Clean up extra whitespace
            self._clean_extra_whitespace(xml_file_path)
            
            sort_order = "newest first" if newest_first else "oldest first"
            self.logger.info(f"Successfully sorted {len(job_date_pairs)} jobs by date ({sort_order})")
            return True
            
        except Exception as e:
            self.logger.error(f"Error sorting XML jobs by date: {str(e)}")
            return False
    
    def add_job_to_xml(self, xml_file_path: str, bullhorn_job: Dict, monitor_name: Optional[str] = None, existing_reference_number: Optional[str] = None, existing_ai_fields: Optional[Dict] = None) -> bool:
        """
        Add a new job to the XML file at the top (first position after </publisherurl>)
        WITH THREAD-SAFE DUPLICATE PREVENTION
        
        Args:
            xml_file_path: Path to the XML file
            bullhorn_job: Bullhorn job data dictionary
            monitor_name: Optional monitor name for company assignment
            existing_reference_number: Optional existing reference number to preserve
            existing_ai_fields: Optional existing AI classification fields to preserve
            
        Returns:
            bool: True if job was added successfully, False otherwise
        """
        max_retries = 3
        retry_delay = 1  # seconds
        job_id = str(bullhorn_job.get('id', ''))
        job_title = bullhorn_job.get('title', 'Unknown Job')
        
        # CRITICAL: Acquire lock to prevent concurrent duplicate additions
        with self._xml_lock:
            self.logger.debug(f"Acquired XML lock for adding job {job_id}")
            
            for attempt in range(max_retries):
                try:
                    # Clean up old backup files first, then create new backup
                    self._cleanup_old_backups(xml_file_path, keep_count=2)
                    backup_path = f"{xml_file_path}.backup_add_{job_id}"
                    shutil.copy2(xml_file_path, backup_path)
                    
                    self.logger.info(f"Attempt {attempt + 1}/{max_retries}: Adding job {job_id} ({job_title}) to XML")
                    
                    # Verify job data is complete before proceeding
                    if not self._validate_job_data(bullhorn_job):
                        self.logger.error(f"Job {job_id} failed validation - incomplete data")
                        return False
                    
                    # DUPLICATE CHECK: Re-check if job exists right before adding (within lock)
                    # CRITICAL FIX: Don't overwrite the existing_reference_number parameter!
                    found_in_xml_reference = None
                    job_already_exists = False
                    
                    try:
                        tree = etree.parse(xml_file_path, self._parser)
                        root = tree.getroot()
                        
                        # Find existing job by bhatsid - check for CDATA format
                        for job in root.xpath('.//job'):
                            bhatsid_elem = job.find('.//bhatsid')
                            if bhatsid_elem is not None and bhatsid_elem.text:
                                # Extract ID from CDATA if present
                                bhatsid_text = str(bhatsid_elem.text).strip()
                                if 'CDATA' in bhatsid_text:
                                    existing_bhatsid = bhatsid_text[9:-3].strip()  # Remove CDATA wrapper
                                else:
                                    existing_bhatsid = bhatsid_text.strip()
                                
                                if existing_bhatsid == job_id:
                                    job_already_exists = True
                                    ref_elem = job.find('.//referencenumber')
                                    if ref_elem is not None and ref_elem.text:
                                        found_in_xml_reference = ref_elem.text.strip()
                                        if 'CDATA' in found_in_xml_reference:
                                            found_in_xml_reference = found_in_xml_reference[9:-3].strip()
                                        self.logger.info(f"Job {job_id} already exists in XML with reference {found_in_xml_reference}")
                                    else:
                                        self.logger.info(f"Job {job_id} already exists in XML")
                                    break
                    except Exception as e:
                        self.logger.debug(f"Could not check for existing job: {e}")
                    
                    # If job already exists, skip adding it to prevent duplicates
                    if job_already_exists:
                        self.logger.warning(f"⚠️ Job {job_id} already exists in {xml_file_path} - skipping to prevent duplicate")
                        # Clean up backup file
                        if os.path.exists(backup_path):
                            os.remove(backup_path)
                        return True  # Return True as the job is already in the file
                    

                    
                    # CRITICAL FIX: Only generate new reference numbers for jobs that were ACTUALLY modified
                    # in the current monitoring cycle, not all jobs that have ever been modified
                    # Check if this job was flagged as modified BY THE MONITOR (not just by date comparison)
                    force_new_reference = False
                    
                    # Store the original passed reference before any modifications
                    original_passed_reference = existing_reference_number
                    
                    # Only force new reference if the monitor explicitly flagged this as a modified job
                    # This prevents bulk reference regeneration for all jobs
                    if bullhorn_job.get('_monitor_flagged_as_modified'):
                        self.logger.info(f"🔄 Job {job_id} was ACTIVELY modified in this cycle - generating NEW reference number")
                        force_new_reference = True
                        existing_reference_number = None  # Force new reference generation
                    elif original_passed_reference:
                        # CRITICAL: Use the PASSED reference, not what we found in XML (job was already removed)
                        existing_reference_number = original_passed_reference
                        self.logger.info(f"✅ PRESERVING passed reference for job {job_id}: {existing_reference_number} (not actively modified)")
                    else:
                        self.logger.info(f"⚠️ Job {job_id} has no existing reference number to preserve - will generate new one")
                    
                    # Check if reference number was preserved in the job data
                    if '_preserved_reference' in bullhorn_job:
                        existing_reference_number = bullhorn_job['_preserved_reference']
                        self.logger.debug(f"Using preserved reference {existing_reference_number} for job {job_id}")
                    
                    # Use passed existing_ai_fields parameter, or extract from XML if not provided
                    ai_fields_to_use = existing_ai_fields
                    if not ai_fields_to_use:
                        ai_fields_to_use = {}
                        try:
                            with open(xml_file_path, 'rb') as f:
                                content = f.read().decode('utf-8')
                            
                            # Find existing job data to extract AI classification fields only if none were passed
                            job_pattern = rf'<job>.*?<bhatsid>(?:<!\[CDATA\[)?{re.escape(str(job_id))}(?:\]\]>)?</bhatsid>.*?</job>'
                            job_match = re.search(job_pattern, content, re.DOTALL)
                            
                            if job_match:
                                job_content = job_match.group(0)
                                
                                # Extract existing AI classification fields
                                jobfunction_match = re.search(r'<jobfunction>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</jobfunction>', job_content)
                                if jobfunction_match:
                                    ai_fields_to_use['jobfunction'] = jobfunction_match.group(1).strip()
                                
                                jobindustries_match = re.search(r'<jobindustries>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</jobindustries>', job_content)  
                                if jobindustries_match:
                                    ai_fields_to_use['jobindustries'] = jobindustries_match.group(1).strip()
                                
                                senioritylevel_match = re.search(r'<senioritylevel>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</senioritylevel>', job_content)
                                if senioritylevel_match:
                                    ai_fields_to_use['senioritylevel'] = senioritylevel_match.group(1).strip()
                                    
                                self.logger.debug(f"Extracted existing AI fields for job {job_id}: {ai_fields_to_use}")
                                
                        except Exception as e:
                            self.logger.debug(f"Could not extract existing AI fields for job {job_id}: {e}")
                    else:
                        self.logger.info(f"Using passed AI fields for job {job_id}: {ai_fields_to_use}")
                    
                    # Map Bullhorn job to XML format with existing AI fields preserved
                    xml_job = self.map_bullhorn_job_to_xml(bullhorn_job, existing_reference_number, monitor_name, skip_ai_classification=False, existing_ai_fields=ai_fields_to_use)
                    if not xml_job or not xml_job.get('title') or not xml_job.get('referencenumber'):
                        self.logger.error(f"Failed to map job {job_id} to XML format - invalid XML job data")
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)
                            continue
                        return False
                    
                    # Parse existing XML
                    with open(xml_file_path, 'rb') as f:
                        tree = etree.parse(f, self._parser)
                    
                    root = tree.getroot()
                    
                    # Find the publisherurl element
                    publisher_url = root.find('publisherurl')
                    if publisher_url is None:
                        self.logger.error("No publisherurl element found in XML")
                        return False
                    
                    # Create new job element with proper formatting
                    job_element = etree.Element('job')
                    job_element.text = "\n    "  # Add newline and indentation after opening <job> tag
                    
                    # Add all job fields with CDATA wrapping and proper indentation
                    for field, value in xml_job.items():
                        field_element = etree.SubElement(job_element, field)
                        # Convert None to empty string and ensure proper formatting
                        clean_value = value if value is not None else ''
                        field_element.text = etree.CDATA(f" {clean_value} ")
                        field_element.tail = "\n    "  # Add proper indentation for each field
                    
                    # Fix the last element's tail to close the job properly
                    if len(job_element) > 0:
                        job_element[-1].tail = "\n  "  # Close job element indentation
                    
                    # Insert the new job as the first job (right after publisherurl)
                    publisher_url_index = list(root).index(publisher_url)
                    
                    # Ensure proper spacing around the new job
                    if publisher_url.tail is None:
                        publisher_url.tail = "\n  "
                    else:
                        publisher_url.tail = "\n  "
                    
                    job_element.tail = "\n  "  # Add newline and indentation after the new job
                    
                    root.insert(publisher_url_index + 1, job_element)
                    
                    # Write updated XML back to file with proper formatting
                    with open(xml_file_path, 'wb') as f:
                        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
                    
                    # Verify the job was actually added by checking the file
                    if self._verify_job_added_to_xml(xml_file_path, job_id, xml_job['title']):
                        self.logger.info(f"Successfully added and verified job {job_id} ({xml_job['title']}) to XML file")
                        # Clean up backup file on success
                        if os.path.exists(backup_path):
                            os.remove(backup_path)
                        return True
                    else:
                        self.logger.error(f"Verification failed: Job {job_id} was not properly added to XML")
                        # Restore backup
                        shutil.copy2(backup_path, xml_file_path)
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)
                            continue
                        return False
                        
                except Exception as e:
                    self.logger.error(f"Attempt {attempt + 1} failed adding job {job_id}: {str(e)}")
                    # Restore backup on error
                    if backup_path and os.path.exists(backup_path):
                        shutil.copy2(backup_path, xml_file_path)
                    
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    else:
                        self.logger.error(f"Failed to add job {job_id} after {max_retries} attempts")
                        return False
            
            return False  # Should never reach here
    
    def remove_job_from_xml(self, xml_file_path: str, job_id: str) -> bool:
        """
        Remove a job from the XML file by matching the job ID in bhatsid field OR title
        
        Args:
            xml_file_path: Path to the XML file
            job_id: Bullhorn job ID to remove
            
        Returns:
            bool: True if job was removed successfully, False otherwise
        """
        try:
            # Parse existing XML
            with open(xml_file_path, 'rb') as f:
                tree = etree.parse(f, self._parser)
            
            root = tree.getroot()
            
            # Find job elements
            jobs = root.findall('job')
            removed = False
            removed_count = 0
            
            # Remove ALL jobs with the same job ID (in case of duplicates)
            for job in jobs[:]:  # Use slice to avoid iteration issues during removal
                # First check bhatsid field (more reliable)
                bhatsid_element = job.find('bhatsid')
                if bhatsid_element is not None and bhatsid_element.text:
                    bhatsid_text = bhatsid_element.text.strip()
                    # Remove CDATA wrapper if present
                    if '<![CDATA[' in bhatsid_text:
                        bhatsid_text = bhatsid_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                    
                    if bhatsid_text == str(job_id):
                        root.remove(job)
                        removed = True
                        removed_count += 1
                        title_elem = job.find('title')
                        title_text = title_elem.text if title_elem is not None else 'Unknown'
                        self.logger.info(f"Removed job with ID {job_id} from XML (title: {title_text})")
                        continue
                
                # Fallback to checking title for backward compatibility
                title_element = job.find('title')
                if title_element is not None and title_element.text:
                    title_text = title_element.text
                    # Check if this job contains the target job ID in parentheses
                    if f"({job_id})" in title_text:
                        root.remove(job)
                        removed = True
                        removed_count += 1
                        self.logger.info(f"Removed job with ID {job_id} from XML (title: {title_text})")
            
            if removed_count > 1:
                self.logger.warning(f"Found and removed {removed_count} duplicate jobs with ID {job_id}")
            elif removed_count == 1:
                self.logger.info(f"Removed job with ID {job_id} from XML")
            
            if removed:
                # Clean up whitespace after removal
                publisher_url = root.find('publisherurl')
                if publisher_url is not None:
                    publisher_url.tail = "\n  "
                
                # Write updated XML back to file
                with open(xml_file_path, 'wb') as f:
                    tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
                    
                # Post-process to remove extra blank lines
                self._clean_extra_whitespace(xml_file_path)
                return True
            else:
                self.logger.warning(f"Job with ID {job_id} not found in XML")
                return False
            
        except Exception as e:
            self.logger.error(f"Error removing job from XML: {str(e)}")
            return False
    
    def detect_orphaned_jobs(self, xml_file_path: str, all_current_jobs: List[Dict]) -> List[Dict]:
        """
        Detect jobs that exist in the XML file but are not in any current tearsheet
        
        Args:
            xml_file_path: Path to the XML file
            all_current_jobs: List of all current jobs from all monitored tearsheets
            
        Returns:
            List[Dict]: List of orphaned job information (job_id, title, etc.)
        """
        try:
            # Parse existing XML
            with open(xml_file_path, 'rb') as f:
                tree = etree.parse(f, self._parser)
            
            root = tree.getroot()
            
            # Extract all job IDs from current Bullhorn jobs
            current_job_ids = set()
            for job in all_current_jobs:
                if 'id' in job:
                    current_job_ids.add(str(job['id']))
            
            # Find all jobs in XML file
            xml_jobs = root.findall('job')
            orphaned_jobs = []
            
            for job in xml_jobs:
                title_element = job.find('title')
                if title_element is not None and title_element.text:
                    # Extract job ID from title (format: "Title (job_id)")
                    title_text = title_element.text.strip()
                    job_id_match = re.search(r'\((\d+)\)', title_text)
                    
                    if job_id_match:
                        job_id = job_id_match.group(1)
                        
                        # Check if this job ID exists in current Bullhorn jobs
                        if job_id not in current_job_ids:
                            orphaned_jobs.append({
                                'job_id': job_id,
                                'title': title_text,
                                'xml_element': job
                            })
            
            self.logger.info(f"Detected {len(orphaned_jobs)} orphaned jobs in XML file")
            return orphaned_jobs
            
        except Exception as e:
            self.logger.error(f"Error detecting orphaned jobs: {str(e)}")
            return []
    
    def _cleanup_old_backups(self, xml_file_path: str, keep_count: int = 3):
        """
        Clean up old backup files, keeping only the most recent ones
        
        Args:
            xml_file_path: Path to the XML file
            keep_count: Number of recent backup files to keep (default: 3)
        """
        import glob
        import os
        
        try:
            # Find all backup files for this XML file
            backup_pattern = f"{xml_file_path}.backup_update_*"
            backup_files = glob.glob(backup_pattern)
            
            if len(backup_files) <= keep_count:
                return  # No cleanup needed
            
            # Sort backups by creation time (newest first)
            backup_files.sort(key=lambda f: os.path.getctime(f), reverse=True)
            
            # Remove old backups beyond keep_count
            files_to_remove = backup_files[keep_count:]
            removed_count = 0
            
            for backup_file in files_to_remove:
                try:
                    os.remove(backup_file)
                    removed_count += 1
                except Exception as e:
                    self.logger.warning(f"Failed to remove old backup {backup_file}: {str(e)}")
            
            if removed_count > 0:
                self.logger.info(f"Cleaned up {removed_count} old backup files, keeping {keep_count} most recent")
                
        except Exception as e:
            self.logger.error(f"Error during backup cleanup: {str(e)}")
    
    def remove_orphaned_jobs(self, xml_file_path: str, all_current_jobs: List[Dict]) -> Dict:
        """
        Remove jobs from XML file that are not in any current tearsheet
        
        Args:
            xml_file_path: Path to the XML file
            all_current_jobs: List of all current jobs from all monitored tearsheets
            
        Returns:
            Dict: Result with success status and removed job count
        """
        try:
            # Detect orphaned jobs first
            orphaned_jobs = self.detect_orphaned_jobs(xml_file_path, all_current_jobs)
            
            if not orphaned_jobs:
                return {
                    'success': True,
                    'removed_count': 0,
                    'message': 'No orphaned jobs found'
                }
            
            # Remove orphaned jobs from XML
            removed_count = 0
            for orphaned_job in orphaned_jobs:
                if self.remove_job_from_xml(xml_file_path, orphaned_job['job_id']):
                    removed_count += 1
                    self.logger.info(f"Removed orphaned job: {orphaned_job['title']}")
            
            # Clean up extra whitespace
            self._clean_extra_whitespace(xml_file_path)
            
            return {
                'success': True,
                'removed_count': removed_count,
                'message': f'Removed {removed_count} orphaned jobs from XML file'
            }
            
        except Exception as e:
            self.logger.error(f"Error removing orphaned jobs: {str(e)}")
            return {
                'success': False,
                'removed_count': 0,
                'message': f'Error: {str(e)}'
            }
    
    def _verify_job_exists_in_xml(self, xml_file_path: str, job_id: str) -> bool:
        """
        Verify if a job with the given ID exists in the XML file
        
        Args:
            xml_file_path: Path to the XML file
            job_id: Job ID to search for
            
        Returns:
            bool: True if job exists, False otherwise
        """
        try:
            with open(xml_file_path, 'rb') as f:
                tree = etree.parse(f, self._parser)
            
            root = tree.getroot()
            jobs = root.findall('job')
            
            for job in jobs:
                title_element = job.find('title')
                if title_element is not None and title_element.text:
                    if f"({job_id})" in title_element.text:
                        return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error verifying job existence in XML: {str(e)}")
            return False
    
    def _verify_job_update_in_xml(self, xml_file_path: str, job_id: str, expected_title: str) -> bool:
        """
        Verify that a job was updated correctly in the XML file
        
        Args:
            xml_file_path: Path to the XML file
            job_id: Job ID to verify
            expected_title: Expected title after update
            
        Returns:
            bool: True if job exists with correct title, False otherwise
        """
        try:
            with open(xml_file_path, 'rb') as f:
                tree = etree.parse(f, self._parser)
            
            root = tree.getroot()
            jobs = root.findall('job')
            
            for job in jobs:
                title_element = job.find('title')
                if title_element is not None and title_element.text:
                    title_text = title_element.text.strip()
                    # Check if this job contains the target job ID and expected title
                    if f"({job_id})" in title_text:
                        # Verify the title contains the expected content
                        title_without_id = title_text.replace(f"({job_id})", "").strip()
                        expected_without_id = expected_title.replace(f"({job_id})", "").strip()
                        
                        if title_without_id == expected_without_id:
                            self.logger.info(f"Verified job {job_id} update: title matches expected value")
                            return True
                        else:
                            self.logger.warning(f"Job {job_id} title mismatch - Found: '{title_without_id}', Expected: '{expected_without_id}'")
                            return False
            
            self.logger.error(f"Job {job_id} not found in XML after update")
            return False
            
        except Exception as e:
            self.logger.error(f"Error verifying job update in XML: {str(e)}")
            return False
    
    def _check_if_update_needed(self, xml_file_path: str, bullhorn_job: Dict) -> tuple[bool, dict]:
        """
        Check if a job update is needed by comparing current XML data with Bullhorn data
        
        Args:
            xml_file_path: Path to the XML file
            bullhorn_job: Bullhorn job data to compare against
            
        Returns:
            tuple: (bool: True if update is needed, dict: details of field changes)
        """
        try:
            job_id = str(bullhorn_job.get('id', ''))
            field_changes = {}
            
            # Parse existing XML
            with open(xml_file_path, 'rb') as f:
                tree = etree.parse(f, self._parser)
            
            root = tree.getroot()
            
            # Find the job by bhatsid
            for job in root.xpath('.//job'):
                bhatsid_elem = job.find('.//bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text and bhatsid_elem.text.strip() == job_id:
                    # Extract current XML job data for comparison (including AI classification fields)
                    xml_data = {}
                    for field in ['title', 'city', 'state', 'country', 'jobtype', 'remotetype', 'assignedrecruiter', 'description', 'jobfunction', 'jobindustries', 'senoritylevel']:
                        elem = job.find(field)
                        if elem is not None and elem.text:
                            xml_data[field] = elem.text.strip()
                        else:
                            xml_data[field] = ''
                    
                    # Map Bullhorn data to XML format for comparison (skip AI classification for performance)
                    bullhorn_xml_data = self.map_bullhorn_job_to_xml(bullhorn_job, skip_ai_classification=True)
                    if not bullhorn_xml_data:
                        self.logger.warning(f"Could not map Bullhorn job {job_id} for comparison")
                        return True, {}  # Assume update needed if we can't compare
                    
                    # COMPREHENSIVE FIELD COMPARISON - Check ALL fields from Bullhorn
                    # This ensures complete accuracy and catches all changes
                    comparison_fields = [
                        'title', 'city', 'state', 'country', 'jobtype', 
                        'remotetype', 'assignedrecruiter', 'description',
                        'jobfunction', 'jobindustries', 'senoritylevel',
                        'company', 'date', 'url', 'apply_email'
                    ]
                    changes_detected = False
                    
                    # Field display names for user-friendly notifications
                    field_display_names = {
                        'title': 'Job Title',
                        'city': 'City',
                        'state': 'State/Province',
                        'country': 'Country',
                        'jobtype': 'Employment Type',
                        'remotetype': 'Remote Type',
                        'assignedrecruiter': 'Assigned Recruiter',
                        'description': 'Job Description',
                        'jobfunction': 'Job Function',
                        'jobindustries': 'Job Industry',
                        'senoritylevel': 'Seniority Level'
                    }
                    
                    for field in comparison_fields:
                        xml_value = xml_data.get(field, '').strip()
                        bullhorn_value = str(bullhorn_xml_data.get(field, '')).strip()
                        
                        if xml_value != bullhorn_value:
                            field_changes[field] = {
                                'display_name': field_display_names.get(field, field),
                                'old_value': xml_value or '(empty)',
                                'new_value': bullhorn_value or '(empty)'
                            }
                            changes_detected = True
                            self.logger.info(f"Job {job_id} field '{field}' changed: '{xml_value}' → '{bullhorn_value}'")
                    
                    if not changes_detected:
                        self.logger.debug(f"Job {job_id} - no critical business changes detected (AI classification variations ignored)")
                    
                    return changes_detected, field_changes
            
            # Job not found in XML - this shouldn't happen in update context
            self.logger.warning(f"Job {job_id} not found in XML for update comparison")
            return True, {}  # Assume update needed
            
        except Exception as e:
            self.logger.error(f"Error checking if update needed for job {job_id}: {str(e)}")
            return True, {}  # Assume update needed if comparison fails
    
    def _update_fields_in_place(self, xml_file_path: str, job_id: str, bullhorn_job: Dict, existing_reference_number: Optional[str], existing_ai_fields: Optional[Dict], monitor_name: Optional[str]) -> bool:
        """
        Update job fields in-place without remove-and-add to preserve reference numbers
        """
        try:
            # Parse the XML file
            tree = etree.parse(xml_file_path, self._parser)
            root = tree.getroot()
            
            # Find the job to update
            job_element = None
            actual_existing_reference = None
            actual_existing_ai_fields = {}
            
            for job in root.xpath('.//job'):
                bhatsid_elem = job.find('.//bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text:
                    bhatsid_text = bhatsid_elem.text.strip()
                    if '<![CDATA[' in bhatsid_text:
                        bhatsid_text = bhatsid_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                    
                    if bhatsid_text == str(job_id):
                        job_element = job
                        
                        # CRITICAL FIX: Extract the ACTUAL existing reference number from the current XML
                        ref_elem = job.find('.//referencenumber')
                        if ref_elem is not None and ref_elem.text:
                            ref_text = ref_elem.text.strip()
                            # Remove CDATA wrapper if present
                            if '<![CDATA[' in ref_text:
                                actual_existing_reference = ref_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                            else:
                                actual_existing_reference = ref_text
                            self.logger.info(f"✅ EXTRACTED existing reference from XML for job {job_id}: {actual_existing_reference}")
                        
                        # Also extract existing AI fields
                        for ai_field in ['jobfunction', 'jobindustries', 'senioritylevel']:
                            ai_elem = job.find(f'.//{ai_field}')
                            if ai_elem is not None and ai_elem.text:
                                ai_value = ai_elem.text.strip()
                                if '<![CDATA[' in ai_value:
                                    ai_value = ai_value.replace('<![CDATA[', '').replace(']]>', '').strip()
                                if ai_value:
                                    actual_existing_ai_fields[ai_field] = ai_value
                        
                        break
            
            if job_element is None:
                self.logger.error(f"Job {job_id} not found for in-place update")
                return False
            
            # Use the ACTUAL extracted reference number, fallback to passed parameter
            final_reference = actual_existing_reference or existing_reference_number
            final_ai_fields = actual_existing_ai_fields if actual_existing_ai_fields else existing_ai_fields
            
            if not final_reference:
                self.logger.warning(f"⚠️ No reference number found for job {job_id} - will generate new one")
            
            # Map the Bullhorn job data to XML format with preserved reference
            updated_xml_job = self.map_bullhorn_job_to_xml(
                bullhorn_job,
                existing_reference_number=final_reference,
                monitor_name=monitor_name,
                existing_ai_fields=final_ai_fields
            )
            
            # Update each field in place while preserving CDATA structure
            field_mappings = [
                'title', 'company', 'date', 'referencenumber', 'bhatsid', 'url',
                'description', 'jobtype', 'city', 'state', 'country', 'category',
                'apply_email', 'remotetype', 'assignedrecruiter', 'jobfunction',
                'jobindustries', 'senioritylevel'
            ]
            
            fields_updated = 0
            for xml_field in field_mappings:
                if xml_field in updated_xml_job:
                    # Find the existing field element
                    field_elem = job_element.find(f'.//{xml_field}')
                    if field_elem is not None:
                        # Extract current field value, handling CDATA
                        current_value = field_elem.text or ''
                        if '<![CDATA[' in current_value:
                            current_value = current_value.replace('<![CDATA[', '').replace(']]>', '').strip()
                        
                        # Get new field value (already processed with HTML entity decoding)
                        new_value = updated_xml_job[xml_field]
                        
                        # Extract clean new value for comparison
                        clean_new_value = new_value
                        if '<![CDATA[' in clean_new_value:
                            clean_new_value = clean_new_value.replace('<![CDATA[', '').replace(']]>', '').strip()
                        
                        # Debug logging for job 34258 (FPGA job) to trace HTML entity issue
                        if job_id == '34258' and xml_field == 'title':
                            self.logger.info(f"🔍 DEBUG job {job_id}: field_elem.text = '{field_elem.text}'")
                            self.logger.info(f"🔍 DEBUG job {job_id}: current_value = '{current_value}'")
                            self.logger.info(f"🔍 DEBUG job {job_id}: new_value = '{new_value}'")
                            self.logger.info(f"🔍 DEBUG job {job_id}: clean_new_value = '{clean_new_value}'")
                            self.logger.info(f"🔍 DEBUG job {job_id}: comparison result = {current_value.strip() != clean_new_value.strip()}")
                        
                        # Compare clean values and update if different
                        if current_value.strip() != clean_new_value.strip():
                            field_elem.text = new_value
                            fields_updated += 1
                            if xml_field == 'title':
                                self.logger.info(f"🔧 HTML ENTITY FIX APPLIED: Updated {xml_field} for job {job_id}: '{current_value}' → '{clean_new_value}'")
                            else:
                                self.logger.debug(f"Updated field {xml_field} for job {job_id}")
                        elif xml_field == 'title' and '&amp;' in current_value:
                            self.logger.debug(f"Title already correct for job {job_id}: '{current_value}'")
            
            # Save the updated XML
            tree.write(xml_file_path, encoding='utf-8', xml_declaration=True)
            self.logger.info(f"✅ IN-PLACE UPDATE completed for job {job_id} - {fields_updated} fields updated without reference number changes")
            return True
            
        except Exception as e:
            self.logger.error(f"Error during in-place update for job {job_id}: {str(e)}")
            return False
    
    def update_job_in_xml(self, xml_file_path: str, bullhorn_job: Dict, monitor_name: Optional[str] = None, existing_reference_number: Optional[str] = None, existing_ai_fields: Optional[Dict] = None) -> bool:
        """
        Update an existing job in the XML file with enhanced error handling and CDATA preservation
        WITH THREAD-SAFE OPERATION
        
        Args:
            xml_file_path: Path to the XML file
            bullhorn_job: Updated Bullhorn job data dictionary
            monitor_name: Optional monitor name for tracking
            existing_reference_number: Existing reference number to preserve
            existing_ai_fields: Existing AI classification fields to preserve
            
        Returns:
            bool: True if job was updated successfully, False if no update was needed or failed
        """
        import shutil
        import time
        
        job_id = str(bullhorn_job.get('id', ''))
        job_title = bullhorn_job.get('title', 'Unknown')
        
        # CRITICAL: Acquire lock to prevent concurrent modifications
        with self._xml_lock:
            self.logger.debug(f"Acquired XML lock for updating job {job_id}")
            
            # COMPREHENSIVE CHECK: Compare ALL fields from Bullhorn with XML
            update_needed, field_changes = self._check_if_update_needed(xml_file_path, bullhorn_job)
            if not update_needed:
                self.logger.debug(f"No update needed for job {job_id} - data already matches")
                return False
        
        # Log what's being updated
        self.logger.info(f"🔄 UPDATING JOB {job_id}: '{job_title}' - Changes detected in fields: {field_changes}")  # No update was needed
        
        # Store field changes for potential use in notifications
        self._last_field_changes = field_changes
        
        self.logger.info(f"Update needed for job {job_id} - proceeding with XML update")
        
        # Create backup of XML file before modification
        backup_path = f"{xml_file_path}.backup_update_{int(time.time())}"
        max_retries = 3
        retry_delay = 1  # seconds
        
        # Clean up old backup files to keep only the 2 most recent (reduced for optimization)
        self._cleanup_old_backups(xml_file_path, keep_count=2)
        
        for attempt in range(max_retries):
            try:
                # Create backup
                shutil.copy2(xml_file_path, backup_path)
                self.logger.info(f"Created backup at {backup_path} for job {job_id} update (attempt {attempt + 1})")
                
                # Verify job exists in XML before update
                job_exists_before = self._verify_job_exists_in_xml(xml_file_path, job_id)
                
                # Get existing AI classification values AND reference number BEFORE removing the job
                existing_ai_classifications = {}
                existing_reference_for_preservation = None
                
                try:
                    tree = etree.parse(xml_file_path, self._parser)
                    root = tree.getroot()
                    
                    # Find existing job by bhatsid to preserve AI classifications and potentially reference
                    for job in root.xpath('.//job'):
                        bhatsid_elem = job.find('.//bhatsid')
                        if bhatsid_elem is not None and bhatsid_elem.text:
                            bhatsid_text = bhatsid_elem.text.strip()
                            # Remove CDATA wrapper if present (CRITICAL FIX for duplicate bug)
                            if '<![CDATA[' in bhatsid_text:
                                bhatsid_text = bhatsid_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                            
                            if bhatsid_text == str(job_id):
                                # Get existing reference number BEFORE removal
                                ref_elem = job.find('.//referencenumber')
                                if ref_elem is not None and ref_elem.text:
                                    existing_reference_for_preservation = ref_elem.text.strip()
                                    if 'CDATA' in existing_reference_for_preservation:
                                        existing_reference_for_preservation = existing_reference_for_preservation[9:-3].strip()
                                    self.logger.info(f"Found existing reference for job {job_id}: {existing_reference_for_preservation}")
                                
                                # CRITICAL: Preserve existing AI classification values
                                ai_fields = ['jobfunction', 'jobindustries', 'senoritylevel']
                                for ai_field in ai_fields:
                                    ai_elem = job.find(f'.//{ai_field}')
                                    if ai_elem is not None and ai_elem.text:
                                        # Extract text from CDATA if present
                                        ai_value = ai_elem.text.strip()
                                        if ai_value:
                                            existing_ai_classifications[ai_field] = ai_value
                                            self.logger.info(f"Preserving existing {ai_field}: {ai_value}")
                                
                                break
                            
                except Exception as e:
                    self.logger.warning(f"Could not get existing data: {e}")
                
                # CRITICAL FIX: DO NOT remove and re-add jobs during updates
                # This was causing reference number flip-flopping
                # Instead, perform true in-place field updates to preserve reference numbers
                self.logger.info(f"🔒 SKIP REMOVE-AND-ADD for job {job_id} - performing in-place field updates to preserve reference numbers")
                
                # Perform in-place field updates without remove-and-add
                field_update_success = self._update_fields_in_place(xml_file_path, job_id, bullhorn_job, existing_reference_for_preservation, existing_ai_classifications, monitor_name)
                
                if not field_update_success:
                    self.logger.warning(f"Failed in-place field update for job {job_id} on attempt {attempt + 1}")
                    if attempt < max_retries - 1:
                        # Restore backup and retry
                        shutil.copy2(backup_path, xml_file_path)
                        time.sleep(retry_delay)
                        continue
                    else:
                        self.logger.error(f"Failed to remove job {job_id} after {max_retries} attempts")
                        return False
                
                # Step 2: Add updated version - CRITICAL FIX for reference number preservation
                # Only generate new reference number if this job was ACTIVELY modified in current cycle
                generate_new_reference = False
                
                # Check if this job was flagged as actively modified by the monitor
                if bullhorn_job.get('_monitor_flagged_as_modified'):
                    self.logger.info(f"🔄 Job {job_id} ACTIVELY modified in current cycle - will get NEW reference number (was: {existing_reference_for_preservation})")
                    generate_new_reference = True
                else:
                    # Job was not modified in this cycle - preserve existing reference number
                    if existing_reference_for_preservation:
                        self.logger.info(f"✅ Job {job_id} NOT actively modified - PRESERVING reference: {existing_reference_for_preservation}")
                    else:
                        self.logger.warning(f"Job {job_id} has no existing reference to preserve - will generate new one")
                        generate_new_reference = True
                
                # Use passed parameters for preservation, fallback to discovered values
                final_reference = existing_reference_number or existing_reference_for_preservation
                final_ai_fields = existing_ai_fields or existing_ai_classifications
                
                # Map the job with preserved reference and AI fields 
                xml_job = self.map_bullhorn_job_to_xml(
                    bullhorn_job, 
                    monitor_name=monitor_name, 
                    existing_reference_number=final_reference,
                    existing_ai_fields=final_ai_fields
                )
                
                if not xml_job:
                    self.logger.error(f"Failed to map job {job_id} to XML format")
                    shutil.copy2(backup_path, xml_file_path)
                    return False
                
                # Parse XML and add the job manually to ensure reference preservation
                try:
                    tree = etree.parse(xml_file_path, self._parser)
                    root = tree.getroot()
                    
                    # Find the publisherurl element
                    publisher_url = root.find('publisherurl')
                    if publisher_url is None:
                        self.logger.error("No publisherurl element found in XML")
                        shutil.copy2(backup_path, xml_file_path)
                        return False
                    
                    # Create new job element with proper formatting
                    job_element = etree.Element('job')
                    job_element.text = "\n    "  # Add newline and indentation after opening <job> tag
                    
                    # Add all job fields with CDATA wrapping and proper indentation
                    for field, value in xml_job.items():
                        field_element = etree.SubElement(job_element, field)
                        clean_value = value if value is not None else ''
                        field_element.text = etree.CDATA(f" {clean_value} ")
                        field_element.tail = "\n    "  # Add proper indentation for each field
                    
                    # Fix the last element's tail to close the job properly
                    if len(job_element) > 0:
                        job_element[-1].tail = "\n  "  # Close job element indentation
                    
                    # Insert the new job as the first job (right after publisherurl)
                    publisher_url_index = list(root).index(publisher_url)
                    
                    # Ensure proper spacing around the new job
                    publisher_url.tail = "\n  "
                    job_element.tail = "\n  "  # Add newline and indentation after the new job
                    
                    root.insert(publisher_url_index + 1, job_element)
                    
                    # Write updated XML back to file with proper formatting
                    with open(xml_file_path, 'wb') as f:
                        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
                    
                    self.logger.info(f"Successfully updated job {xml_job['title']} with NEW reference number {xml_job.get('referencenumber', 'unknown')} in XML file {xml_file_path}")
                    addition_success = True
                    
                except Exception as e:
                    self.logger.error(f"Failed to add updated job {job_id} to XML: {str(e)}")
                    addition_success = False
                
                if not addition_success:
                    if attempt < max_retries - 1:
                        # Restore backup and retry
                        shutil.copy2(backup_path, xml_file_path)
                        time.sleep(retry_delay)
                        continue
                    else:
                        self.logger.error(f"Failed to add updated job {job_id} after {max_retries} attempts")
                        # Restore backup on final failure
                        shutil.copy2(backup_path, xml_file_path)
                        return False
                
                # Step 3: Verify the update was successful
                verification_success = self._verify_job_update_in_xml(xml_file_path, job_id, job_title)
                if not verification_success:
                    self.logger.error(f"Job {job_id} update verification failed on attempt {attempt + 1}")
                    if attempt < max_retries - 1:
                        # Restore backup and retry
                        shutil.copy2(backup_path, xml_file_path)
                        time.sleep(retry_delay)
                        continue
                    else:
                        self.logger.error(f"Job {job_id} update verification failed after {max_retries} attempts")
                        # Restore backup on final failure
                        shutil.copy2(backup_path, xml_file_path)
                        return False
                
                # Success - cleanup backup
                try:
                    import os
                    os.remove(backup_path)
                    self.logger.info(f"Successfully updated job {job_id} ({job_title}) in XML file")
                except:
                    pass  # Backup cleanup failure is not critical
                
                return True
                
            except Exception as e:
                self.logger.error(f"Error updating job {job_id} in XML (attempt {attempt + 1}): {str(e)}")
                if attempt < max_retries - 1:
                    # Restore backup and retry
                    try:
                        shutil.copy2(backup_path, xml_file_path)
                        time.sleep(retry_delay)
                        continue
                    except:
                        pass
                else:
                    # Final attempt failed - restore backup
                    try:
                        shutil.copy2(backup_path, xml_file_path)
                        self.logger.error(f"Restored backup after failed update for job {job_id}")
                    except:
                        pass
                    return False
        
        return False
    
    def sync_xml_with_bullhorn_jobs(self, xml_file_path: str, current_jobs: List[Dict], 
                                   previous_jobs: List[Dict]) -> Dict:
        """
        Synchronize XML file with current Bullhorn jobs based on changes
        
        Args:
            xml_file_path: Path to the XML file to update
            current_jobs: List of current Bullhorn job dictionaries
            previous_jobs: List of previous Bullhorn job dictionaries
            
        Returns:
            Dict: Summary of sync operation with counts and success status
        """
        try:
            # Create job ID sets for comparison
            current_job_ids = {str(job.get('id', '')) for job in current_jobs}
            previous_job_ids = {str(job.get('id', '')) for job in previous_jobs}
            
            # Find changes
            added_job_ids = current_job_ids - previous_job_ids
            removed_job_ids = previous_job_ids - current_job_ids
            
            # Track results
            added_count = 0
            removed_count = 0
            updated_count = 0
            errors = []
            
            # Add new jobs
            for job in current_jobs:
                job_id = str(job.get('id', ''))
                if job_id in added_job_ids:
                    if self.add_job_to_xml(xml_file_path, job):
                        added_count += 1
                    else:
                        errors.append(f"Failed to add job {job_id}")
            
            # Remove deleted jobs
            for job_id in removed_job_ids:
                if self.remove_job_from_xml(xml_file_path, job_id):
                    removed_count += 1
                else:
                    errors.append(f"Failed to remove job {job_id}")
            
            # Check for modified jobs (same ID but different content)
            for current_job in current_jobs:
                current_id = str(current_job.get('id', ''))
                if current_id not in added_job_ids:  # Skip newly added jobs
                    # Find matching previous job
                    previous_job = next((job for job in previous_jobs 
                                       if str(job.get('id', '')) == current_id), None)
                    
                    if previous_job:
                        # Comprehensive field comparison - check ALL relevant fields
                        changed_fields = self._compare_job_fields(current_job, previous_job)
                        
                        if changed_fields:
                            self.logger.info(f"Job {current_id} has changed fields: {', '.join(changed_fields)}")
                            if self.update_job_in_xml(xml_file_path, current_job):
                                updated_count += 1
                            else:
                                errors.append(f"Failed to update job {current_id}")
            
            self.logger.info(f"XML sync completed: {added_count} added, {removed_count} removed, {updated_count} updated")
            
            return {
                'success': len(errors) == 0,
                'added_count': added_count,
                'removed_count': removed_count,
                'updated_count': updated_count,
                'total_changes': added_count + removed_count + updated_count,
                'errors': errors
            }
            
        except Exception as e:
            self.logger.error(f"Error syncing XML with Bullhorn jobs: {str(e)}")
            return {
                'success': False,
                'added_count': 0,
                'removed_count': 0,
                'updated_count': 0,
                'total_changes': 0,
                'errors': [str(e)]
            }
    
    def _compare_job_fields(self, current_job: Dict, previous_job: Dict) -> List[str]:
        """
        Compare all relevant job fields between current and previous job data
        
        Args:
            current_job: Current Bullhorn job data
            previous_job: Previous Bullhorn job data
            
        Returns:
            List[str]: List of field names that have changed
        """
        changed_fields = []
        
        # Fields to monitor for changes (mapped to XML fields)
        fields_to_monitor = {
            'title': 'title',
            'publicDescription': 'description',
            'description': 'description',
            'employmentType': 'jobtype',
            'onSite': 'remotetype',
            'address': 'location',
            'assignedUsers': 'assignedrecruiter',
            'responseUser': 'assignedrecruiter',
            'owner': 'assignedrecruiter',
            'dateAdded': 'date',
            'dateLastModified': 'dateLastModified'
        }
        
        try:
            for bullhorn_field, xml_field in fields_to_monitor.items():
                current_value = current_job.get(bullhorn_field, '')
                previous_value = previous_job.get(bullhorn_field, '')
                
                # Handle special cases
                if bullhorn_field == 'address':
                    # Compare address object fields
                    current_addr = current_value if isinstance(current_value, dict) else {}
                    previous_addr = previous_value if isinstance(previous_value, dict) else {}
                    
                    if (current_addr.get('city', '') != previous_addr.get('city', '') or
                        current_addr.get('state', '') != previous_addr.get('state', '') or
                        current_addr.get('countryName', '') != previous_addr.get('countryName', '')):
                        changed_fields.append(xml_field)
                
                elif bullhorn_field in ['assignedUsers', 'responseUser', 'owner']:
                    # Compare recruiter assignment fields
                    current_recruiter = self._extract_assigned_recruiter(
                        current_job.get('assignedUsers', {}),
                        current_job.get('responseUser', {}),
                        current_job.get('owner', {})
                    )
                    previous_recruiter = self._extract_assigned_recruiter(
                        previous_job.get('assignedUsers', {}),
                        previous_job.get('responseUser', {}),
                        previous_job.get('owner', {})
                    )
                    
                    if current_recruiter != previous_recruiter and xml_field not in changed_fields:
                        changed_fields.append(xml_field)
                
                elif bullhorn_field == 'publicDescription':
                    # Prefer publicDescription over description
                    current_desc = current_job.get('publicDescription', '') or current_job.get('description', '')
                    previous_desc = previous_job.get('publicDescription', '') or previous_job.get('description', '')
                    
                    if current_desc != previous_desc:
                        changed_fields.append(xml_field)
                
                elif bullhorn_field == 'description':
                    # Skip if already checked via publicDescription
                    if 'description' not in changed_fields:
                        current_desc = current_job.get('publicDescription', '') or current_job.get('description', '')
                        previous_desc = previous_job.get('publicDescription', '') or previous_job.get('description', '')
                        
                        if current_desc != previous_desc:
                            changed_fields.append(xml_field)
                
                elif bullhorn_field == 'employmentType':
                    # Map employment type for comparison
                    current_type = self._map_employment_type(current_value)
                    previous_type = self._map_employment_type(previous_value)
                    
                    if current_type != previous_type:
                        changed_fields.append(xml_field)
                
                elif bullhorn_field == 'onSite':
                    # Map remote type for comparison
                    current_remote = self._map_remote_type(current_value)
                    previous_remote = self._map_remote_type(previous_value)
                    
                    if current_remote != previous_remote:
                        changed_fields.append(xml_field)
                
                else:
                    # Direct field comparison
                    if current_value != previous_value:
                        changed_fields.append(xml_field)
            
            # Remove duplicates while preserving order
            seen = set()
            unique_changed_fields = []
            for field in changed_fields:
                if field not in seen:
                    seen.add(field)
                    unique_changed_fields.append(field)
            
            return unique_changed_fields
            
        except Exception as e:
            self.logger.error(f"Error comparing job fields: {str(e)}")
            # Fallback to dateLastModified comparison
            current_modified = current_job.get('dateLastModified', '')
            previous_modified = previous_job.get('dateLastModified', '')
            
            if current_modified != previous_modified:
                return ['dateLastModified']
            return []
    
    def regenerate_xml_from_jobs(self, jobs: List[Dict], xml_file_path: str) -> bool:
        """
        Regenerate an entire XML file from a list of Bullhorn jobs
        
        Args:
            jobs: List of Bullhorn job dictionaries
            xml_file_path: Path to the XML file to generate
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info(f"Regenerating {xml_file_path} with {len(jobs)} jobs")
            
            # Create a new XML structure
            root = ET.Element('source')
            publisher_url = ET.SubElement(root, 'publisherurl')
            publisher_url.text = 'https://myticas.com'
            
            # Add each job
            for job in jobs:
                # Map the job to XML format
                xml_job_data = self.map_bullhorn_job_to_xml(job)
                
                # Create job element
                job_elem = ET.SubElement(root, 'job')
                
                # Add all job fields
                for field_name, field_value in xml_job_data.items():
                    field_elem = ET.SubElement(job_elem, field_name)
                    # Wrap in CDATA
                    field_elem.text = f"<![CDATA[{field_value}]]>" if field_value else "<![CDATA[]]>"
            
            # Format and write the XML
            tree = ET.ElementTree(root)
            
            # Pretty print the XML
            xml_str = ET.tostring(root, encoding='utf-8', method='xml').decode('utf-8')
            
            # Fix CDATA formatting (convert escaped CDATA tags back to proper format)
            xml_str = xml_str.replace('&lt;![CDATA[', '<![CDATA[')
            xml_str = xml_str.replace(']]&gt;', ']]>')
            
            # Add XML declaration and proper formatting
            from xml.dom import minidom
            dom = minidom.parseString(xml_str)
            pretty_xml = dom.toprettyxml(indent="  ", encoding=None)
            
            # Remove extra blank lines
            lines = pretty_xml.split('\n')
            non_empty_lines = [line for line in lines if line.strip()]
            pretty_xml = '\n'.join(non_empty_lines)
            
            # Write to file
            with open(xml_file_path, 'w', encoding='utf-8') as f:
                f.write(pretty_xml)
            
            self.logger.info(f"Successfully regenerated {xml_file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error regenerating XML file: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _safe_write_xml(self, xml_file_path: str, tree, validation_callback=None):
        """
        Safely write XML file with validation and backup
        
        Args:
            xml_file_path: Path to the XML file
            tree: lxml tree object to write
            validation_callback: Optional function to validate before writing
        
        Returns:
            bool: True if successful, False otherwise
        """
        def write_xml_file(filepath):
            with open(filepath, 'wb') as f:
                tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
            self._clean_extra_whitespace(filepath)
            return True
        
        # Use safeguards for safe update
        result = self.safeguards.safe_update_xml(
            xml_file_path,
            lambda fp: write_xml_file(fp)
        )
        
        if result['success']:
            self.logger.info(f"Safely wrote XML file: {xml_file_path}")
            if result.get('warnings'):
                self.logger.warning(f"Warnings: {result['warnings']}")
        else:
            self.logger.error(f"Failed to safely write XML: {result.get('error')}")
            if result.get('rolled_back'):
                self.logger.info("Changes were rolled back due to validation failure")
        
        return result['success']
    
    def perform_comprehensive_field_sync(self, xml_file_path: str, bullhorn_job: Dict, monitor_name: Optional[str] = None) -> bool:
        """
        Perform a comprehensive field synchronization for a job
        Ensures ALL fields from Bullhorn are accurately reflected in XML
        
        Args:
            xml_file_path: Path to the XML file
            bullhorn_job: Complete Bullhorn job data
            monitor_name: Optional monitor name for company assignment
            
        Returns:
            bool: True if sync was performed, False if no changes needed
        """
        job_id = str(bullhorn_job.get('id', ''))
        
        # CRITICAL: Thread-safe operation
        with self._xml_lock:
            self.logger.info(f"🔍 Performing comprehensive field sync for job {job_id}")
            
            # Check if job exists in XML
            job_exists = self._verify_job_exists_in_xml(xml_file_path, job_id)
            
            if not job_exists:
                # Job doesn't exist - add it
                self.logger.info(f"Job {job_id} not found in XML - adding it")
                # Release lock temporarily for add_job_to_xml (it has its own lock)
                return self.add_job_to_xml(xml_file_path, bullhorn_job, monitor_name)
            
            # Job exists - perform comprehensive field comparison
            try:
                # Parse XML to get current job data
                tree = etree.parse(xml_file_path, self._parser)
                root = tree.getroot()
                
                # Find the job in XML
                current_xml_job = None
                for job in root.findall('.//job'):
                    bhatsid_elem = job.find('.//bhatsid')
                    if bhatsid_elem is not None and bhatsid_elem.text:
                        bhatsid_text = str(bhatsid_elem.text).strip()
                        if 'CDATA' in bhatsid_text:
                            bhatsid_text = bhatsid_text[9:-3].strip()
                        
                        if bhatsid_text == job_id:
                            current_xml_job = job
                            break
                
                if not current_xml_job:
                    self.logger.warning(f"Job {job_id} not found during comprehensive sync")
                    return False
                
                # CRITICAL FIX: Extract existing reference number BEFORE mapping for comparison
                existing_ref_for_comparison = None
                ref_elem = current_xml_job.find('.//referencenumber')
                if ref_elem is not None and ref_elem.text:
                    existing_ref_for_comparison = ref_elem.text.strip()
                    if 'CDATA' in existing_ref_for_comparison:
                        existing_ref_for_comparison = existing_ref_for_comparison[9:-3].strip()
                    self.logger.debug(f"Using existing reference {existing_ref_for_comparison} for comparison mapping")
                
                # Map Bullhorn data to XML format for comprehensive comparison
                # CRITICAL: Pass existing reference to prevent generating new one during comparison
                new_xml_data = self.map_bullhorn_job_to_xml(bullhorn_job, existing_ref_for_comparison, monitor_name, skip_ai_classification=False)
                
                # Compare ALL fields comprehensively
                fields_to_sync = [
                    'title', 'company', 'date', 'url', 'description',
                    'jobtype', 'city', 'state', 'country', 'category',
                    'apply_email', 'remotetype', 'assignedrecruiter',
                    'jobfunction', 'jobindustries', 'senoritylevel'
                ]
                
                fields_updated = []
                needs_update = False
                
                for field in fields_to_sync:
                    current_elem = current_xml_job.find(field)
                    current_value = ''
                    if current_elem is not None and current_elem.text:
                        current_value = current_elem.text.strip()
                        if 'CDATA' in current_value:
                            current_value = current_value[9:-3].strip()
                    
                    new_value = new_xml_data.get(field, '')
                    
                    # Enhanced logging for remotetype debugging
                    if field == 'remotetype':
                        onsite_raw = bullhorn_job.get('onSite', '')
                        self.logger.info(f"🔍 Job {job_id} remotetype check:")
                        self.logger.info(f"  - Bullhorn onSite raw value: '{onsite_raw}'")
                        self.logger.info(f"  - Current XML remotetype: '{current_value}'")
                        self.logger.info(f"  - New mapped remotetype: '{new_value}'")
                    
                    if current_value != new_value:
                        needs_update = True
                        fields_updated.append({
                            'field': field,
                            'old': current_value,
                            'new': new_value
                        })
                        self.logger.info(f"Field '{field}' needs update: '{current_value}' → '{new_value}'")
                
                if needs_update:
                    self.logger.info(f"📝 Job {job_id} requires comprehensive update for {len(fields_updated)} fields:")
                    for update in fields_updated:
                        self.logger.info(f"  - {update['field']}: '{update['old']}' → '{update['new']}'")
                    
                    # CRITICAL FIX: Use in-place field updates instead of remove-and-add to preserve reference numbers
                    self.logger.info(f"🔒 PERFORMING IN-PLACE UPDATE for job {job_id} to preserve reference numbers")
                    
                    # Get existing reference number and AI fields for preservation
                    existing_reference = None
                    existing_ai_fields = {}
                    
                    ref_elem = current_xml_job.find('.//referencenumber')
                    if ref_elem is not None and ref_elem.text:
                        existing_reference = ref_elem.text.strip()
                        if 'CDATA' in existing_reference:
                            existing_reference = existing_reference[9:-3].strip()
                    
                    # Get existing AI fields
                    ai_fields = ['jobfunction', 'jobindustries', 'senioritylevel']
                    for ai_field in ai_fields:
                        ai_elem = current_xml_job.find(f'.//{ai_field}')
                        if ai_elem is not None and ai_elem.text:
                            ai_value = ai_elem.text.strip()
                            if 'CDATA' in ai_value:
                                ai_value = ai_value[9:-3].strip()
                            if ai_value:
                                existing_ai_fields[ai_field] = ai_value
                    
                    # Perform in-place field updates
                    success = self._update_fields_in_place(
                        xml_file_path, 
                        job_id, 
                        bullhorn_job, 
                        existing_reference, 
                        existing_ai_fields if existing_ai_fields else None, 
                        monitor_name
                    )
                    
                    if success:
                        self.logger.info(f"✅ In-place update completed for job {job_id} - reference number preserved: {existing_reference}")
                        return True
                    else:
                        self.logger.error(f"Failed in-place update for job {job_id}")
                        return False
                else:
                    self.logger.info(f"✅ Job {job_id} is already fully synchronized - all fields match")
                    return False
                    
            except Exception as e:
                self.logger.error(f"Error during comprehensive field sync for job {job_id}: {str(e)}")
                import traceback
                traceback.print_exc()
                return False