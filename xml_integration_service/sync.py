"""SyncMixin — XMLIntegrationService methods for this domain."""
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


class SyncMixin:
    """Mixin providing sync-related XMLIntegrationService methods."""

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
