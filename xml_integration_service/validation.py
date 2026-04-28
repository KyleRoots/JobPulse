"""ValidationMixin — XMLIntegrationService methods for this domain."""
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


class ValidationMixin:
    """Mixin providing validation-related XMLIntegrationService methods."""

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
