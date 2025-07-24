"""
XML Integration Service for Bullhorn Job Data
Handles mapping Bullhorn job data to XML format and managing XML file updates
"""

import os
import logging
import re
from typing import Dict, List, Optional
from datetime import datetime
from lxml import etree
from xml_processor import XMLProcessor


class XMLIntegrationService:
    """Service for integrating Bullhorn job data with XML files"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.xml_processor = XMLProcessor()
        self._parser = etree.XMLParser(strip_cdata=False, recover=True)
    
    def map_bullhorn_job_to_xml(self, bullhorn_job: Dict, existing_reference_number: Optional[str] = None) -> Dict:
        """
        Map Bullhorn job data to XML job structure
        
        Args:
            bullhorn_job: Dictionary containing Bullhorn job data
            existing_reference_number: Optional existing reference number to preserve
            
        Returns:
            Dict: XML job structure with CDATA wrapped values
        """
        try:
            # Extract basic job information
            job_id = bullhorn_job.get('id', '')
            title = bullhorn_job.get('title', 'Untitled Position')
            
            # Extract clean job title - remove any existing job ID or code in parentheses
            # This handles cases like "Local to MN-Hybrid, Attorney (J.D)(W-4499)" -> "Attorney"
            # First remove any (W-####) or similar codes
            clean_title = re.sub(r'\s*\([A-Z]+-?\d+\)\s*', '', title)
            # Then remove any location prefixes like "Local to XX-Hybrid, "
            clean_title = re.sub(r'^Local to [A-Z]{2}[^,]*,\s*', '', clean_title)
            # Remove any other parenthetical content like (J.D)
            clean_title = re.sub(r'\s*\([^)]+\)\s*', ' ', clean_title).strip()
            
            # Add Bullhorn job ID in parentheses after cleaned title
            formatted_title = f"{clean_title} ({job_id})"
            
            # Always use Myticas Consulting as company name
            company_name = 'Myticas Consulting'
            
            # Extract location information ONLY from Bullhorn structured address fields
            # Never fallback to job description parsing for location data
            address = bullhorn_job.get('address', {})
            city = address.get('city', '') if address else ''
            state = address.get('state', '') if address else ''
            country = address.get('countryName', 'United States') if address else 'United States'
            
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
            assigned_users = bullhorn_job.get('assignedUsers', {})
            response_user = bullhorn_job.get('responseUser', {})
            owner = bullhorn_job.get('owner', {})
            
            assigned_recruiter = self._extract_assigned_recruiter(assigned_users, response_user, owner)
            
            # Extract description (prefer publicDescription, fallback to description)
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
            
            # Extract job ID from formatted title for bhatsid
            bhatsid = self.xml_processor.extract_job_id_from_title(formatted_title)
            
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
                'url': clean_field_value('https://myticas.com/'),
                'description': clean_field_value(description),
                'jobtype': clean_field_value(job_type),
                'city': clean_field_value(city),
                'state': clean_field_value(state),
                'country': clean_field_value(country),
                'category': '',  # Empty as per template
                'apply_email': clean_field_value('apply@myticas.com'),
                'remotetype': clean_field_value(remote_type),
                'assignedrecruiter': clean_field_value(assigned_recruiter)
            }
            
            self.logger.info(f"Mapped Bullhorn job {job_id} ({title}) to XML format")
            return xml_job
            
        except Exception as e:
            self.logger.error(f"Error mapping Bullhorn job to XML: {str(e)}")
            return {}
    

    
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
    
    def _map_remote_type(self, onsite_value) -> str:
        """Map Bullhorn onSite value to XML remote type"""
        # Handle both list and string formats from Bullhorn
        if isinstance(onsite_value, list):
            # If it's a list, take the first item
            onsite_value = onsite_value[0] if onsite_value else ''
        
        onsite_value = onsite_value.lower() if onsite_value else ''
        
        if 'remote' in onsite_value:
            return 'Remote'
        elif 'hybrid' in onsite_value:
            return 'Hybrid'
        elif 'onsite' in onsite_value or 'on-site' in onsite_value:
            return 'Onsite'
        elif 'off-site' in onsite_value:
            return 'Off-Site'
        elif 'no preference' in onsite_value:
            return 'No Preference'
        else:
            return 'Onsite'  # Default fallback
    
    def _extract_assigned_recruiter(self, assigned_users, response_user, owner) -> str:
        """Extract recruiter name from multiple possible fields"""
        try:
            # Check assignedUsers first (array of users)
            if assigned_users and isinstance(assigned_users, dict):
                users_data = assigned_users.get('data', [])
                if isinstance(users_data, list) and users_data:
                    first_user = users_data[0]
                    if isinstance(first_user, dict):
                        first_name = first_user.get('firstName', '')
                        last_name = first_user.get('lastName', '')
                        if first_name or last_name:
                            return f"{first_name} {last_name}".strip()
            
            # Check responseUser next (single user)
            if response_user and isinstance(response_user, dict):
                first_name = response_user.get('firstName', '')
                last_name = response_user.get('lastName', '')
                if first_name or last_name:
                    return f"{first_name} {last_name}".strip()
            
            # Finally check owner (single user)
            if owner and isinstance(owner, dict):
                first_name = owner.get('firstName', '')
                last_name = owner.get('lastName', '')
                if first_name or last_name:
                    return f"{first_name} {last_name}".strip()
            
            return ''
            
        except Exception as e:
            self.logger.warning(f"Error extracting assigned recruiter: {str(e)}")
            return ''
    
    def _clean_description(self, description: str) -> str:
        """Clean and format job description for XML"""
        if not description:
            return ''
        
        # Remove excessive whitespace
        description = ' '.join(description.split())
        
        # Ensure HTML content is properly formatted
        # Note: We preserve HTML tags as they exist in the current XML structure
        return description
    
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
    
    def add_job_to_xml(self, xml_file_path: str, bullhorn_job: Dict) -> bool:
        """
        Add a new job to the XML file at the top (first position after </publisherurl>)
        
        Args:
            xml_file_path: Path to the XML file
            bullhorn_job: Bullhorn job data dictionary
            
        Returns:
            bool: True if job was added successfully, False otherwise
        """
        try:
            # First check if job already exists to preserve reference number
            existing_reference_number = None
            job_id = str(bullhorn_job.get('id', ''))
            
            try:
                tree = etree.parse(xml_file_path, self._parser)
                root = tree.getroot()
                
                # Find existing job by bhatsid
                for job in root.xpath('.//job'):
                    bhatsid_elem = job.find('.//bhatsid')
                    if bhatsid_elem is not None and bhatsid_elem.text and bhatsid_elem.text.strip() == job_id:
                        ref_elem = job.find('.//referencenumber')
                        if ref_elem is not None and ref_elem.text:
                            existing_reference_number = ref_elem.text.strip()
                            self.logger.info(f"Found existing reference number {existing_reference_number} for job {job_id}")
                            break
            except Exception as e:
                self.logger.debug(f"Could not check for existing job: {e}")
            
            # Map Bullhorn job to XML format with existing reference number if found
            xml_job = self.map_bullhorn_job_to_xml(bullhorn_job, existing_reference_number)
            if not xml_job:
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
            
            self.logger.info(f"Added job {xml_job['title']} to XML file {xml_file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error adding job to XML: {str(e)}")
            return False
    
    def remove_job_from_xml(self, xml_file_path: str, job_id: str) -> bool:
        """
        Remove a job from the XML file by matching the job ID in the title
        
        Args:
            xml_file_path: Path to the XML file
            job_id: Bullhorn job ID to remove (will match against title containing "(job_id)")
            
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
    
    def update_job_in_xml(self, xml_file_path: str, bullhorn_job: Dict) -> bool:
        """
        Update an existing job in the XML file with enhanced error handling and CDATA preservation
        
        Args:
            xml_file_path: Path to the XML file
            bullhorn_job: Updated Bullhorn job data dictionary
            
        Returns:
            bool: True if job was updated successfully, False otherwise
        """
        import shutil
        import time
        
        job_id = str(bullhorn_job.get('id', ''))
        job_title = bullhorn_job.get('title', 'Unknown')
        
        # Create backup of XML file before modification
        backup_path = f"{xml_file_path}.backup_update_{int(time.time())}"
        max_retries = 3
        retry_delay = 1  # seconds
        
        for attempt in range(max_retries):
            try:
                # Create backup
                shutil.copy2(xml_file_path, backup_path)
                self.logger.info(f"Created backup at {backup_path} for job {job_id} update (attempt {attempt + 1})")
                
                # Verify job exists in XML before update
                job_exists_before = self._verify_job_exists_in_xml(xml_file_path, job_id)
                
                # CRITICAL FIX: Get existing reference number BEFORE removing the job
                existing_reference_number = None
                try:
                    tree = etree.parse(xml_file_path, self._parser)
                    root = tree.getroot()
                    
                    # Find existing job by bhatsid to preserve reference number
                    for job in root.xpath('.//job'):
                        bhatsid_elem = job.find('.//bhatsid')
                        if bhatsid_elem is not None and bhatsid_elem.text and bhatsid_elem.text.strip() == job_id:
                            ref_elem = job.find('.//referencenumber')
                            if ref_elem is not None and ref_elem.text:
                                existing_reference_number = ref_elem.text.strip()
                                self.logger.info(f"Preserving reference number {existing_reference_number} for job {job_id} update")
                                break
                except Exception as e:
                    self.logger.warning(f"Could not get existing reference number: {e}")
                
                # Step 1: Remove old version
                removal_success = self.remove_job_from_xml(xml_file_path, job_id)
                if not removal_success and job_exists_before:
                    self.logger.warning(f"Failed to remove job {job_id} from XML on attempt {attempt + 1}")
                    if attempt < max_retries - 1:
                        # Restore backup and retry
                        shutil.copy2(backup_path, xml_file_path)
                        time.sleep(retry_delay)
                        continue
                    else:
                        self.logger.error(f"Failed to remove job {job_id} after {max_retries} attempts")
                        return False
                
                # Step 2: Add updated version with preserved reference number
                # Create a copy of bullhorn_job and add the preserved reference number
                bullhorn_job_with_ref = bullhorn_job.copy()
                if existing_reference_number:
                    bullhorn_job_with_ref['_existing_reference_number'] = existing_reference_number
                
                # Map the job with preserved reference number
                xml_job = self.map_bullhorn_job_to_xml(bullhorn_job, existing_reference_number)
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
                    
                    self.logger.info(f"Successfully updated job {xml_job['title']} in XML file {xml_file_path}")
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