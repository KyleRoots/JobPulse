"""MappingMixin — XMLIntegrationService methods for this domain."""
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
    from xml_safe_compat import safe_etree as etree
from xml_processor import XMLProcessor
from job_classification_service import JobClassificationService, InternalJobClassifier
from xml_safeguards import XMLSafeguards
from tearsheet_config import TearsheetConfig
from utils.field_mappers import map_employment_type, map_remote_type

logger = logging.getLogger(__name__)


class MappingMixin:
    """Mixin providing mapping-related XMLIntegrationService methods."""

    def map_bullhorn_jobs_to_xml_batch(self, jobs_data: List[Dict], existing_references: Dict = None, enable_ai_classification: bool = False, monitor_names: Dict = None) -> List[Dict]:
        """
        Map multiple Bullhorn jobs to XML format with fast keyword-based classification
        
        Args:
            jobs_data: List of Bullhorn job dictionaries
            existing_references: Dict mapping job_id to existing reference numbers
            enable_ai_classification: Deprecated - kept for backward compatibility (ignored)
            monitor_names: Dict mapping job_id to monitor names for proper branding
            
        Returns:
            List of XML job dictionaries
        """
        if not jobs_data:
            return []
        
        existing_references = existing_references or {}
        monitor_names = monitor_names or {}
        xml_jobs = []
        
        self.logger.info(f"🚀 Classifying {len(jobs_data)} jobs using enhanced keyword classifier...")
        start_time = time.time()
        
        # Perform fast keyword classification for all jobs
        classification_results = {}
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
            
            # Classify using keyword classifier (instant, no timeouts)
            result = self.job_classifier.classify_job(clean_title, description)
            classification_results[job_id] = result
        
        elapsed = time.time() - start_time
        self.logger.info(f"✅ Keyword classification completed for {len(classification_results)} jobs in {elapsed:.2f}s")
        
        # Process each job with its classification result
        for job_data in jobs_data:
            job_id = str(job_data.get('id', ''))
            monitor_name = monitor_names.get(job_id, '')
            existing_ref = existing_references.get(job_id, '')
            classification = classification_results.get(job_id, None)
            
            # Map individual job with pre-computed classification
            xml_job = self._map_single_job_with_classification_result(
                job_data,
                existing_reference_number=existing_ref,
                monitor_name=monitor_name,
                classification_result=classification
            )
            
            if xml_job:
                xml_jobs.append(xml_job)
        
        self.logger.info(f"Mapped {len(xml_jobs)} jobs to XML format with keyword classification")
        return xml_jobs
    def _map_single_job_with_classification_result(self, bullhorn_job: Dict, existing_reference_number: str = '', monitor_name: str = '', classification_result: Dict = None) -> Dict:
        """
        Map a single job with a pre-computed keyword classification result
        This is used by the batch processing method to avoid individual classification calls
        """
        # Use pre-computed classification fields
        if classification_result and classification_result.get('success'):
            existing_classification_fields = {
                'jobfunction': classification_result.get('job_function', ''),
                'jobindustries': classification_result.get('industries', ''),
                'senioritylevel': classification_result.get('seniority_level', '')
            }
        else:
            existing_classification_fields = None
        
        # Call the existing method with classification results provided as existing fields
        return self.map_bullhorn_job_to_xml(
            bullhorn_job,
            existing_reference_number=existing_reference_number,
            monitor_name=monitor_name,
            skip_ai_classification=True,  # Always skip AI (we use keywords only)
            existing_ai_fields=existing_classification_fields
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
            
            # Handle keyword classification - preserve existing values if provided, otherwise classify
            if existing_ai_fields and existing_ai_fields.get('jobfunction') and existing_ai_fields.get('jobindustries') and existing_ai_fields.get('senioritylevel'):
                # Use existing pre-computed classification values to avoid re-classification
                job_function = existing_ai_fields.get('jobfunction', '')
                job_industry = existing_ai_fields.get('jobindustries', '')
                seniority_level = existing_ai_fields.get('senioritylevel', '')
                self.logger.debug(f"Using pre-computed classification for job {job_id}: Function={job_function}, Industry={job_industry}, Seniority={seniority_level}")
            else:
                # ALWAYS classify with keyword classifier (fast, no timeouts, guaranteed success)
                # This ensures taxonomy fields are NEVER blank, even in real-time paths
                classification = self.job_classifier.classify_job(clean_title, description)
                job_function = classification.get('job_function', 'Operations')  # Guaranteed default
                job_industry = classification.get('industries', 'Professional Services')  # Guaranteed default
                seniority_level = classification.get('seniority_level', 'Mid-Senior level')  # Guaranteed default
                self.logger.debug(f"Keyword classification for job {job_id}: Function={job_function}, Industry={job_industry}, Seniority={seniority_level}")
            
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
            
            # Determine base URL based on company (environment-independent, case-insensitive)
            if company_name and 'stsi' in company_name.lower():
                base_url = 'https://apply.stsigroup.com'
            else:
                base_url = 'https://apply.myticas.com'
            
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
                    if company_name and 'stsi' in company_name.lower():
                        base_url = 'https://apply.stsigroup.com'
                    else:
                        base_url = 'https://apply.myticas.com'
                    fallback_url = f"{base_url}/{str(bhatsid).strip()}/position/?source=LinkedIn"
                    self.logger.warning(f"Using fallback URL with job ID: {fallback_url}")
                    return fallback_url
            except Exception:
                pass
            # Final fallback to generic URL
            return "https://myticas.com/"
    def _map_employment_type(self, employment_type: str) -> str:
        """Map Bullhorn employment type to XML job type (delegates to shared module)"""
        return map_employment_type(employment_type)
    def _map_remote_type(self, onsite_value) -> str:
        """Map Bullhorn onSite value to XML remote type (delegates to shared module)"""
        return map_remote_type(onsite_value, log_context='xml_integration')
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
            
            # Apply recruiter name to LinkedIn tag mapping (returns tag only, e.g., #LI-AG1)
            if recruiter_name:
                linkedin_tag = self._map_recruiter_to_linkedin_tag(recruiter_name)
                return linkedin_tag
            
            return ''
            
        except Exception as e:
            self.logger.warning(f"Error extracting assigned recruiter: {str(e)}")
            return ''
    def _map_recruiter_to_linkedin_tag(self, recruiter_name: str) -> str:
        """
        Map recruiter names to LinkedIn tags for XML using database mappings.
        
        Returns only the LinkedIn tag code (e.g., "#LI-AG1") without the recruiter name.
        Falls back to returning the original recruiter name if no mapping is found.
        
        Args:
            recruiter_name: Full name of the recruiter (e.g., "Adam Gebara")
            
        Returns:
            str: LinkedIn tag code (e.g., "#LI-AG1") or original name if no mapping exists
        """
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
                
                # If we found a mapping, return just the LinkedIn tag (without recruiter name)
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
                except Exception:
                    date_obj = datetime.now()
            
            return date_obj.strftime('%B %d, %Y')
            
        except Exception as e:
            self.logger.warning(f"Error formatting date {date_str}: {str(e)}")
            return datetime.now().strftime('%B %d, %Y')
