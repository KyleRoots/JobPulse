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
    
    def map_bullhorn_job_to_xml(self, bullhorn_job: Dict) -> Dict:
        """
        Map Bullhorn job data to XML job structure
        
        Args:
            bullhorn_job: Dictionary containing Bullhorn job data
            
        Returns:
            Dict: XML job structure with CDATA wrapped values
        """
        try:
            # Extract basic job information
            job_id = bullhorn_job.get('id', '')
            title = bullhorn_job.get('title', 'Untitled Position')
            
            # Add Bullhorn job ID in parentheses after title
            formatted_title = f"{title} ({job_id})"
            
            # Always use Myticas Consulting as company name
            company_name = 'Myticas Consulting'
            
            # Extract location information
            address = bullhorn_job.get('address', {})
            city = address.get('city', '') if address else ''
            state = address.get('state', '') if address else ''
            country = address.get('countryName', 'United States') if address else 'United States'
            
            # Handle employment type mapping
            employment_type = bullhorn_job.get('employmentType', '')
            job_type = self._map_employment_type(employment_type)
            
            # Extract description (prefer publicDescription, fallback to description)
            description = bullhorn_job.get('publicDescription', '') or bullhorn_job.get('description', '')
            
            # Clean up description - remove excessive whitespace and format for XML
            description = self._clean_description(description)
            
            # Format date
            date_added = bullhorn_job.get('dateAdded', '')
            formatted_date = self._format_date(date_added)
            
            # Generate unique reference number
            reference_number = self.xml_processor.generate_reference_number()
            
            # Create XML job structure
            xml_job = {
                'title': formatted_title,
                'company': company_name,
                'date': formatted_date,
                'referencenumber': reference_number,
                'url': 'https://myticas.com/',
                'description': description,
                'jobtype': job_type,
                'city': city,
                'state': state,
                'country': country,
                'category': '',  # Empty as per template
                'apply_email': 'apply@myticas.com',
                'remotetype': ''  # Empty as per template
            }
            
            self.logger.info(f"Mapped Bullhorn job {job_id} ({title}) to XML format")
            return xml_job
            
        except Exception as e:
            self.logger.error(f"Error mapping Bullhorn job to XML: {str(e)}")
            return None
    
    def _map_employment_type(self, employment_type: str) -> str:
        """Map Bullhorn employment type to XML job type"""
        employment_type = employment_type.lower() if employment_type else ''
        
        if 'contract' in employment_type:
            return 'Contract'
        elif 'permanent' in employment_type or 'full-time' in employment_type:
            return 'Full-time'
        elif 'part-time' in employment_type:
            return 'Part-time'
        elif 'temporary' in employment_type:
            return 'Temporary'
        else:
            return 'Contract'  # Default fallback
    
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
            # Map Bullhorn job to XML format
            xml_job = self.map_bullhorn_job_to_xml(bullhorn_job)
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
                field_element.text = etree.CDATA(f" {value} ")
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
            
            for job in jobs:
                title_element = job.find('title')
                if title_element is not None and title_element.text:
                    title_text = title_element.text
                    # Check if this job contains the target job ID in parentheses
                    if f"({job_id})" in title_text:
                        root.remove(job)
                        removed = True
                        self.logger.info(f"Removed job with ID {job_id} from XML")
                        break
            
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
    
    def update_job_in_xml(self, xml_file_path: str, bullhorn_job: Dict) -> bool:
        """
        Update an existing job in the XML file
        
        Args:
            xml_file_path: Path to the XML file
            bullhorn_job: Updated Bullhorn job data dictionary
            
        Returns:
            bool: True if job was updated successfully, False otherwise
        """
        try:
            job_id = str(bullhorn_job.get('id', ''))
            
            # First remove the old version
            if self.remove_job_from_xml(xml_file_path, job_id):
                # Then add the updated version
                return self.add_job_to_xml(xml_file_path, bullhorn_job)
            else:
                # If job wasn't found to remove, just add it as new
                return self.add_job_to_xml(xml_file_path, bullhorn_job)
            
        except Exception as e:
            self.logger.error(f"Error updating job in XML: {str(e)}")
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
                        # Simple comparison - check if dateLastModified is different
                        current_modified = current_job.get('dateLastModified', '')
                        previous_modified = previous_job.get('dateLastModified', '')
                        
                        if current_modified != previous_modified:
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