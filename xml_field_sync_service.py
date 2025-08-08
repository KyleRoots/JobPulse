"""
XML Field Synchronization Service
Ensures complete field-level synchronization between Bullhorn and XML files
with enhanced duplicate prevention and comprehensive field checking
"""

import os
import logging
import time
import threading
import hashlib
from typing import Dict, List, Set, Optional, Tuple
from datetime import datetime
import shutil

# Try to import fcntl for Unix file locking, fallback to Windows or no locking
try:
    import fcntl
    FCNTL_AVAILABLE = True
except ImportError:
    FCNTL_AVAILABLE = False

try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    import xml.etree.ElementTree as etree
    LXML_AVAILABLE = False

class XMLFieldSyncService:
    """Service for comprehensive XML field synchronization with duplicate prevention"""
    
    # Define ALL fields that need to be synchronized from Bullhorn
    SYNC_FIELDS = {
        'title': str,
        'company': str,
        'date': str,
        'referencenumber': str,
        'bhatsid': str,
        'url': str,
        'description': str,
        'jobtype': str,
        'city': str,
        'state': str,
        'country': str,
        'category': str,
        'apply_email': str,
        'remotetype': str,
        'assignedrecruiter': str,
        'jobfunction': str,
        'jobindustries': str,
        'senoritylevel': str
    }
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._file_locks = {}  # Track file locks for concurrent access
        self._lock = threading.Lock()  # Thread safety for operations
        
        # Parser setup
        if LXML_AVAILABLE:
            self._parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False, recover=True)
        else:
            self._parser = None
    
    def comprehensive_sync_check(self, xml_file_path: str, bullhorn_jobs: List[Dict]) -> Dict:
        """
        Perform comprehensive synchronization check between XML and Bullhorn data
        
        Returns:
            Dict with sync results including duplicates found, fields needing update, etc.
        """
        results = {
            'duplicates_found': [],
            'missing_jobs': [],
            'orphaned_jobs': [],
            'field_mismatches': [],
            'jobs_needing_update': [],
            'success': False,
            'error': None
        }
        
        try:
            # Create file lock to prevent concurrent modifications
            with self._acquire_file_lock(xml_file_path):
                # Parse XML file
                if LXML_AVAILABLE:
                    tree = etree.parse(xml_file_path, self._parser)
                else:
                    tree = etree.parse(xml_file_path)
                
                root = tree.getroot()
                
                # Build index of XML jobs by ID
                xml_jobs_by_id = {}
                duplicate_ids = set()
                
                for job_elem in root.findall('.//job'):
                    job_id = self._extract_field_value(job_elem, 'bhatsid')
                    if job_id:
                        if job_id in xml_jobs_by_id:
                            # Found duplicate!
                            duplicate_ids.add(job_id)
                            results['duplicates_found'].append({
                                'job_id': job_id,
                                'count': 2 if job_id not in duplicate_ids else xml_jobs_by_id[job_id].get('count', 1) + 1
                            })
                        else:
                            xml_jobs_by_id[job_id] = {
                                'element': job_elem,
                                'fields': self._extract_all_fields(job_elem),
                                'count': 1
                            }
                
                # Build index of Bullhorn jobs by ID
                bullhorn_jobs_by_id = {str(job.get('id')): job for job in bullhorn_jobs if job.get('id')}
                
                # Find missing jobs (in Bullhorn but not in XML)
                missing_ids = set(bullhorn_jobs_by_id.keys()) - set(xml_jobs_by_id.keys())
                results['missing_jobs'] = list(missing_ids)
                
                # Find orphaned jobs (in XML but not in Bullhorn)
                orphaned_ids = set(xml_jobs_by_id.keys()) - set(bullhorn_jobs_by_id.keys())
                results['orphaned_jobs'] = list(orphaned_ids)
                
                # Check field mismatches for existing jobs
                for job_id, xml_job_data in xml_jobs_by_id.items():
                    if job_id in bullhorn_jobs_by_id:
                        bullhorn_job = bullhorn_jobs_by_id[job_id]
                        xml_fields = xml_job_data['fields']
                        
                        # Map Bullhorn fields to XML fields
                        mapped_fields = self._map_bullhorn_to_xml_fields(bullhorn_job)
                        
                        # Compare each field
                        mismatches = []
                        for field_name in self.SYNC_FIELDS:
                            xml_value = xml_fields.get(field_name, '').strip()
                            mapped_value = mapped_fields.get(field_name, '').strip()
                            
                            # Special handling for certain fields
                            if field_name == 'referencenumber':
                                continue  # Don't compare reference numbers
                            
                            if xml_value != mapped_value:
                                mismatches.append({
                                    'field': field_name,
                                    'xml_value': xml_value,
                                    'bullhorn_value': mapped_value
                                })
                        
                        if mismatches:
                            results['field_mismatches'].append({
                                'job_id': job_id,
                                'title': xml_fields.get('title', 'Unknown'),
                                'mismatches': mismatches
                            })
                            results['jobs_needing_update'].append(job_id)
                
                results['success'] = True
                
        except Exception as e:
            self.logger.error(f"Error in comprehensive sync check: {str(e)}")
            results['error'] = str(e)
        
        return results
    
    def fix_duplicates(self, xml_file_path: str) -> Dict:
        """Remove duplicate jobs from XML file, keeping only the most recent"""
        results = {
            'duplicates_removed': 0,
            'jobs_processed': [],
            'success': False,
            'error': None
        }
        
        try:
            # Backup the file first
            backup_path = f"{xml_file_path}.backup_dedup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(xml_file_path, backup_path)
            
            with self._acquire_file_lock(xml_file_path):
                if LXML_AVAILABLE:
                    tree = etree.parse(xml_file_path, self._parser)
                else:
                    tree = etree.parse(xml_file_path)
                
                root = tree.getroot()
                
                # Track jobs by ID and remove duplicates
                seen_job_ids = set()
                jobs_to_remove = []
                
                for job_elem in root.findall('.//job'):
                    job_id = self._extract_field_value(job_elem, 'bhatsid')
                    
                    if job_id:
                        if job_id in seen_job_ids:
                            # This is a duplicate - mark for removal
                            jobs_to_remove.append(job_elem)
                            results['duplicates_removed'] += 1
                            results['jobs_processed'].append(job_id)
                            self.logger.warning(f"Removing duplicate job {job_id}")
                        else:
                            seen_job_ids.add(job_id)
                
                # Remove duplicate jobs
                for job_elem in jobs_to_remove:
                    root.remove(job_elem)
                
                # Write back to file
                if LXML_AVAILABLE:
                    tree.write(xml_file_path, encoding='utf-8', xml_declaration=True, pretty_print=True)
                else:
                    tree.write(xml_file_path, encoding='utf-8', xml_declaration=True)
                
                results['success'] = True
                self.logger.info(f"Successfully removed {results['duplicates_removed']} duplicate jobs")
                
        except Exception as e:
            self.logger.error(f"Error fixing duplicates: {str(e)}")
            results['error'] = str(e)
        
        return results
    
    def update_job_fields(self, xml_file_path: str, job_id: str, field_updates: Dict) -> bool:
        """Update specific fields for a job in the XML file"""
        try:
            with self._acquire_file_lock(xml_file_path):
                if LXML_AVAILABLE:
                    tree = etree.parse(xml_file_path, self._parser)
                else:
                    tree = etree.parse(xml_file_path)
                
                root = tree.getroot()
                job_updated = False
                
                # Find the job and update fields
                for job_elem in root.findall('.//job'):
                    if self._extract_field_value(job_elem, 'bhatsid') == str(job_id):
                        for field_name, new_value in field_updates.items():
                            field_elem = job_elem.find(f'.//{field_name}')
                            if field_elem is not None:
                                # Update with CDATA wrapper
                                if LXML_AVAILABLE:
                                    field_elem.text = None
                                    field_elem.append(etree.CDATA(str(new_value)))
                                else:
                                    field_elem.text = f"<![CDATA[ {new_value} ]]>"
                                self.logger.info(f"Updated {field_name} for job {job_id}")
                            else:
                                # Field doesn't exist, create it
                                new_elem = etree.SubElement(job_elem, field_name)
                                if LXML_AVAILABLE:
                                    new_elem.append(etree.CDATA(str(new_value)))
                                else:
                                    new_elem.text = f"<![CDATA[ {new_value} ]]>"
                                self.logger.info(f"Added {field_name} for job {job_id}")
                        
                        job_updated = True
                        break
                
                if job_updated:
                    # Write back to file
                    if LXML_AVAILABLE:
                        tree.write(xml_file_path, encoding='utf-8', xml_declaration=True, pretty_print=True)
                    else:
                        tree.write(xml_file_path, encoding='utf-8', xml_declaration=True)
                    
                    return True
                else:
                    self.logger.warning(f"Job {job_id} not found in XML for field updates")
                    return False
                    
        except Exception as e:
            self.logger.error(f"Error updating job fields: {str(e)}")
            return False
    
    def _extract_field_value(self, job_elem, field_name: str) -> str:
        """Extract field value from job element, handling CDATA"""
        field_elem = job_elem.find(f'.//{field_name}')
        if field_elem is not None and field_elem.text:
            value = field_elem.text.strip()
            # Remove CDATA wrapper if present
            if value.startswith('<![CDATA[') and value.endswith(']]>'):
                value = value[9:-3].strip()
            return value
        return ''
    
    def _extract_all_fields(self, job_elem) -> Dict:
        """Extract all field values from a job element"""
        fields = {}
        for field_name in self.SYNC_FIELDS:
            fields[field_name] = self._extract_field_value(job_elem, field_name)
        return fields
    
    def _map_bullhorn_to_xml_fields(self, bullhorn_job: Dict) -> Dict:
        """Map Bullhorn job data to XML field format"""
        from xml_integration_service import XMLIntegrationService
        
        # Use existing mapping logic from XMLIntegrationService
        xml_service = XMLIntegrationService()
        
        # Map basic fields
        mapped = {
            'bhatsid': str(bullhorn_job.get('id', '')),
            'title': f"{bullhorn_job.get('title', '')} ({bullhorn_job.get('id', '')})",
            'company': 'Myticas Consulting',
            'date': xml_service._format_date(bullhorn_job.get('dateAdded')),
            'description': xml_service._clean_description(bullhorn_job.get('publicDescription', '')),
            'city': bullhorn_job.get('address', {}).get('city', '') if isinstance(bullhorn_job.get('address'), dict) else '',
            'state': bullhorn_job.get('address', {}).get('state', '') if isinstance(bullhorn_job.get('address'), dict) else '',
            'country': bullhorn_job.get('address', {}).get('countryName', 'United States') if isinstance(bullhorn_job.get('address'), dict) else 'United States',
            'apply_email': 'apply@myticas.com',
            'jobtype': xml_service._map_employment_type(bullhorn_job.get('employmentType', '')),
            'remotetype': xml_service._map_remote_type(bullhorn_job.get('onSite', '')),
            'category': '',
            'url': xml_service._generate_unique_job_url(bullhorn_job.get('id'), bullhorn_job.get('title', '').split('(')[0].strip())
        }
        
        # Map recruiter
        assigned_users = bullhorn_job.get('assignedUsers')
        response_user = bullhorn_job.get('responseUser')
        owner = bullhorn_job.get('owner')
        mapped['assignedrecruiter'] = xml_service._extract_assigned_recruiter(assigned_users, response_user, owner)
        
        # Map AI classification fields
        mapped['jobfunction'] = bullhorn_job.get('customText20', 'Information Technology')
        mapped['jobindustries'] = bullhorn_job.get('customText21', 'Computer Software')
        mapped['senoritylevel'] = bullhorn_job.get('customText22', 'Mid-Senior level')
        
        return mapped
    
    def _acquire_file_lock(self, file_path: str):
        """Context manager for file locking"""
        class FileLock:
            def __init__(self, path, service):
                self.path = path
                self.service = service
                self.lock_file = None
                
            def __enter__(self):
                # Create a lock file
                lock_path = f"{self.path}.lock"
                max_wait = 10  # seconds
                wait_time = 0
                
                while os.path.exists(lock_path) and wait_time < max_wait:
                    time.sleep(0.1)
                    wait_time += 0.1
                
                # Create lock file
                self.lock_file = open(lock_path, 'w')
                if FCNTL_AVAILABLE:
                    try:
                        # Try to get exclusive lock (Unix only)
                        fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except:
                        pass  # Failed to get lock
                
                return self
                
            def __exit__(self, exc_type, exc_val, exc_tb):
                if self.lock_file:
                    if FCNTL_AVAILABLE:
                        try:
                            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                        except:
                            pass
                    self.lock_file.close()
                    
                lock_path = f"{self.path}.lock"
                if os.path.exists(lock_path):
                    try:
                        os.remove(lock_path)
                    except:
                        pass
        
        return FileLock(file_path, self)
    
    def perform_full_sync(self, xml_file_path: str, bullhorn_jobs: List[Dict]) -> Dict:
        """
        Perform full synchronization: remove duplicates, update fields, add missing, remove orphaned
        """
        results = {
            'duplicates_removed': 0,
            'fields_updated': 0,
            'jobs_added': 0,
            'jobs_removed': 0,
            'success': False,
            'details': []
        }
        
        try:
            # Step 1: Check current state
            sync_check = self.comprehensive_sync_check(xml_file_path, bullhorn_jobs)
            
            # Step 2: Fix duplicates
            if sync_check['duplicates_found']:
                dedup_result = self.fix_duplicates(xml_file_path)
                results['duplicates_removed'] = dedup_result['duplicates_removed']
            
            # Step 3: Update field mismatches
            for mismatch_info in sync_check['field_mismatches']:
                job_id = mismatch_info['job_id']
                field_updates = {}
                
                for mismatch in mismatch_info['mismatches']:
                    field_name = mismatch['field']
                    new_value = mismatch['bullhorn_value']
                    field_updates[field_name] = new_value
                
                if self.update_job_fields(xml_file_path, job_id, field_updates):
                    results['fields_updated'] += 1
                    results['details'].append(f"Updated {len(field_updates)} fields for job {job_id}")
            
            # Step 4: Remove orphaned jobs
            if sync_check['orphaned_jobs']:
                from xml_integration_service import XMLIntegrationService
                xml_service = XMLIntegrationService()
                
                for job_id in sync_check['orphaned_jobs']:
                    if xml_service.remove_job_from_xml(xml_file_path, job_id):
                        results['jobs_removed'] += 1
            
            # Step 5: Add missing jobs
            if sync_check['missing_jobs']:
                from xml_integration_service import XMLIntegrationService
                xml_service = XMLIntegrationService()
                
                bullhorn_jobs_by_id = {str(job.get('id')): job for job in bullhorn_jobs}
                for job_id in sync_check['missing_jobs']:
                    if job_id in bullhorn_jobs_by_id:
                        job_data = bullhorn_jobs_by_id[job_id]
                        if xml_service.add_job_to_xml(xml_file_path, job_data, "Field Sync Service"):
                            results['jobs_added'] += 1
            
            results['success'] = True
            
        except Exception as e:
            self.logger.error(f"Error in full sync: {str(e)}")
            results['error'] = str(e)
        
        return results