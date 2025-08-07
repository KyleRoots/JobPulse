"""
XML Integration Service for Bullhorn Job Data
Handles mapping Bullhorn job data to XML format and managing XML file updates
"""

import os
import logging
import re
import shutil
import time
import urllib.parse
from typing import Dict, List, Optional
from datetime import datetime
try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree
from xml_processor import XMLProcessor
from job_classification_service import JobClassificationService
from xml_safeguards import XMLSafeguards


class XMLIntegrationService:
    """Service for integrating Bullhorn job data with XML files"""
    
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
    
    def map_bullhorn_job_to_xml(self, bullhorn_job: Dict, existing_reference_number: Optional[str] = None, monitor_name: Optional[str] = None, skip_ai_classification: bool = False) -> Dict:
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
            
            # Determine company name based on tearsheet/monitor
            if monitor_name and 'Sponsored - STSI' in monitor_name:
                company_name = 'STSI Group'
                self.logger.info(f"Job {job_id} from STSI tearsheet - using company: STSI Group")
            else:
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
            
            # Generate unique job application URL (clean_title is already defined above)
            job_url = self._generate_job_application_url(bhatsid, clean_title)
            
            # Use AI to classify the job based on title and description (skip during real-time monitoring for performance)
            if skip_ai_classification:
                # For real-time monitoring, use empty values to prevent timeouts
                job_function = ''
                job_industry = ''
                seniority_level = ''
                self.logger.debug(f"Skipped AI classification for job {job_id} during real-time sync")
            else:
                # Full AI classification for scheduled processing or manual operations
                classification = self.job_classifier.classify_job(clean_title, description)
                job_function = classification.get('job_function', '')
                job_industry = classification.get('industries', '')  # Fixed: using 'industries' not 'job_industry'
                seniority_level = classification.get('seniority_level', '')
            
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
                'senoritylevel': clean_field_value(seniority_level)
            }
            
            self.logger.info(f"Mapped Bullhorn job {job_id} ({title}) to XML format")
            return xml_job
            
        except Exception as e:
            self.logger.error(f"Error mapping Bullhorn job to XML: {str(e)}")
            return {}
    
    def _generate_job_application_url(self, bhatsid: str, clean_title: str) -> str:
        """
        Generate unique job application URL for each job
        
        Args:
            bhatsid: Job ID from Bullhorn
            clean_title: Clean job title without ID
            
        Returns:
            str: Unique job application URL in format:
                 https://apply.myticas.com/[bhatsid]/[title]/?source=LinkedIn
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
            encoded_title = urllib.parse.quote(str(clean_title).strip(), safe='')
            
            # CONFIGURABLE: Use environment variable to determine base URL
            # This allows switching between apply.myticas.com (production) and current domain (development)
            import os
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
            
            # Look for job by bhatsid (most reliable)
            for job in root.xpath('.//job'):
                bhatsid_elem = job.find('.//bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text and bhatsid_elem.text.strip() == job_id:
                    # Additional verification - check title contains job ID
                    title_elem = job.find('.//title')
                    if title_elem is not None and title_elem.text and f"({job_id})" in title_elem.text:
                        self.logger.debug(f"Verified job {job_id} exists in XML with correct title format")
                        return True
            
            # Alternative verification - look for job ID in title
            for job in root.xpath('.//job'):
                title_elem = job.find('.//title')
                if title_elem is not None and title_elem.text and f"({job_id})" in title_elem.text:
                    self.logger.debug(f"Verified job {job_id} exists in XML by title")
                    return True
            
            self.logger.error(f"Job {job_id} not found in XML file during verification")
            return False
            
        except Exception as e:
            self.logger.error(f"Error verifying job {job_id} in XML: {str(e)}")
            return False
    
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
        """Extract recruiter name from multiple possible fields and map to LinkedIn-style tag"""
        try:
            recruiter_name = ''
            
            # Check assignedUsers first (array of users)
            if assigned_users and isinstance(assigned_users, dict):
                users_data = assigned_users.get('data', [])
                if isinstance(users_data, list) and users_data:
                    first_user = users_data[0]
                    if isinstance(first_user, dict):
                        first_name = first_user.get('firstName', '')
                        last_name = first_user.get('lastName', '')
                        if first_name or last_name:
                            recruiter_name = f"{first_name} {last_name}".strip()
            
            # Check responseUser next (single user)
            if not recruiter_name and response_user and isinstance(response_user, dict):
                first_name = response_user.get('firstName', '')
                last_name = response_user.get('lastName', '')
                if first_name or last_name:
                    recruiter_name = f"{first_name} {last_name}".strip()
            
            # Finally check owner (single user)
            if not recruiter_name and owner and isinstance(owner, dict):
                first_name = owner.get('firstName', '')
                last_name = owner.get('lastName', '')
                if first_name or last_name:
                    recruiter_name = f"{first_name} {last_name}".strip()
            
            # Apply recruiter name to LinkedIn-style tag mapping
            if recruiter_name:
                return self._map_recruiter_to_linkedin_tag(recruiter_name)
            
            return ''
            
        except Exception as e:
            self.logger.warning(f"Error extracting assigned recruiter: {str(e)}")
            return ''
    
    def _map_recruiter_to_linkedin_tag(self, recruiter_name: str) -> str:
        """Map recruiter names to LinkedIn-style tags for XML"""
        # Define recruiter name to LinkedIn tag mapping - REVISED LIST with '1' added
        recruiter_mapping = {
            'Adam Gebara': '#LI-AG1',
            'Amanda Messina': '#LI-AM1',
            'Bryan Chinzorig': '#LI-BC1',
            'Christine Carter': '#LI-CC1',
            'Dan Sifer': '#LI-DS1',
            'Dominic Scaletta': '#LI-DSC1',
            'Matheo Theodossiou': '#LI-MAT1',
            'Michael Theodossiou': '#LI-MIT1',
            'Michelle Corino': '#LI-MC1',
            'Mike Gebara': '#LI-MG1',
            'Myticas Recruiter': '#LI-RS1',    # Now using #LI-RS1
            'Nick Theodossiou': '#LI-NT1',
            'Reena Setya': '#LI-RS1',          # Also using #LI-RS1
            'Runa Parmar': '#LI-RP1'
        }
        
        # Check for exact match first
        if recruiter_name in recruiter_mapping:
            # Return format with both tag and name
            return f"{recruiter_mapping[recruiter_name]}: {recruiter_name}"
        
        # Check for case-insensitive match
        for name, tag in recruiter_mapping.items():
            if recruiter_name.lower() == name.lower():
                # Return format with both tag and name
                return f"{tag}: {name}"
        
        # If no mapping found, return the original name
        self.logger.info(f"No LinkedIn tag mapping found for recruiter: {recruiter_name}")
        return recruiter_name
    
    def _clean_description(self, description: str) -> str:
        """Clean and format job description for XML"""
        if not description:
            return ''
        
        import html
        
        # Convert HTML entities back to proper HTML tags for consistent formatting
        # This ensures &lt;strong&gt; becomes <strong>, &lt;p&gt; becomes <p>, etc.
        description = html.unescape(description)
        
        # Remove excessive whitespace
        description = ' '.join(description.split())
        
        # Ensure HTML content is properly formatted within CDATA sections
        # All HTML tags will now be consistent raw HTML format
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
    
    def add_job_to_xml(self, xml_file_path: str, bullhorn_job: Dict, monitor_name: Optional[str] = None) -> bool:
        """
        Add a new job to the XML file at the top (first position after </publisherurl>)
        
        Args:
            xml_file_path: Path to the XML file
            bullhorn_job: Bullhorn job data dictionary
            monitor_name: Optional monitor name for company assignment
            
        Returns:
            bool: True if job was added successfully, False otherwise
        """
        max_retries = 3
        retry_delay = 1  # seconds
        
        for attempt in range(max_retries):
            try:
                job_id = str(bullhorn_job.get('id', ''))
                job_title = bullhorn_job.get('title', 'Unknown Job')
                
                # Clean up old backup files first, then create new backup
                self._cleanup_old_backups(xml_file_path, keep_count=2)
                backup_path = f"{xml_file_path}.backup_add_{job_id}"
                shutil.copy2(xml_file_path, backup_path)
                
                self.logger.info(f"Attempt {attempt + 1}/{max_retries}: Adding job {job_id} ({job_title}) to XML")
                
                # Verify job data is complete before proceeding
                if not self._validate_job_data(bullhorn_job):
                    self.logger.error(f"Job {job_id} failed validation - incomplete data")
                    return False
                
                # First check if job already exists to preserve reference number or skip duplicate
                existing_reference_number = None
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
                                    existing_reference_number = ref_elem.text.strip()
                                    if 'CDATA' in existing_reference_number:
                                        existing_reference_number = existing_reference_number[9:-3].strip()
                                    self.logger.info(f"Job {job_id} already exists in XML with reference {existing_reference_number}")
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
                
                # Only force new reference if the monitor explicitly flagged this as a modified job
                # This prevents bulk reference regeneration for all jobs
                if hasattr(bullhorn_job, '_monitor_flagged_as_modified') and bullhorn_job.get('_monitor_flagged_as_modified'):
                    self.logger.info(f"🔄 Job {job_id} was ACTIVELY modified in this cycle - generating NEW reference number")
                    force_new_reference = True
                    existing_reference_number = None  # Force new reference generation
                elif existing_reference_number:
                    # Job exists but was NOT modified in this cycle - keep existing reference
                    self.logger.debug(f"Job {job_id} exists with reference {existing_reference_number} - preserving it")
                
                # Map Bullhorn job to XML format 
                # Pass None for reference if we want to force a new one for modified jobs
                xml_job = self.map_bullhorn_job_to_xml(bullhorn_job, existing_reference_number, monitor_name, skip_ai_classification=False)
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
                job_id = job_id if 'job_id' in locals() else 'unknown'
                backup_path = backup_path if 'backup_path' in locals() else None
                
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
                    
                    # Compare ONLY CRITICAL fields to avoid AI classification noise
                    # AI classification fields (jobfunction, jobindustries, senoritylevel) are excluded
                    # to prevent repeated notifications for minor AI variations
                    comparison_fields = ['title', 'city', 'state', 'country', 'jobtype', 'remotetype', 'assignedrecruiter', 'description']
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
    
    def update_job_in_xml(self, xml_file_path: str, bullhorn_job: Dict, monitor_name: Optional[str] = None) -> bool:
        """
        Update an existing job in the XML file with enhanced error handling and CDATA preservation
        
        Args:
            xml_file_path: Path to the XML file
            bullhorn_job: Updated Bullhorn job data dictionary
            
        Returns:
            bool: True if job was updated successfully, False if no update was needed or failed
        """
        import shutil
        import time
        
        job_id = str(bullhorn_job.get('id', ''))
        job_title = bullhorn_job.get('title', 'Unknown')
        
        # CRITICAL: Check if update is actually needed by comparing current XML data with Bullhorn data
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
                        if bhatsid_elem is not None and bhatsid_elem.text and bhatsid_elem.text.strip() == job_id:
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
                
                # Map the job with existing reference (unless actively modified in this cycle)
                reference_to_use = None if generate_new_reference else existing_reference_for_preservation
                xml_job = self.map_bullhorn_job_to_xml(bullhorn_job, reference_to_use, monitor_name, skip_ai_classification=True)
                
                # CRITICAL: Restore existing AI classification values that were preserved
                if existing_ai_classifications:
                    for ai_field, ai_value in existing_ai_classifications.items():
                        xml_job[ai_field] = ai_value
                        self.logger.info(f"Restored preserved {ai_field}: {ai_value}")
                
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