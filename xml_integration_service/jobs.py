"""JobsMixin — XMLIntegrationService methods for this domain."""
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


class JobsMixin:
    """Mixin providing jobs-related XMLIntegrationService methods."""

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
                except Exception:
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
                    except Exception:
                        pass
                else:
                    # Final attempt failed - restore backup
                    try:
                        shutil.copy2(backup_path, xml_file_path)
                        self.logger.error(f"Restored backup after failed update for job {job_id}")
                    except Exception:
                        pass
                    return False
        
        return False
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
            from defusedxml.minidom import parseString as _safe_parseString
            dom = _safe_parseString(xml_str)
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