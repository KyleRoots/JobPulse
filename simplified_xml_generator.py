"""
Simplified XML Generator for Bullhorn Job Feed
Directly pulls from all tearsheets and generates clean XML with proper formatting
"""

import os
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime

try:
    from lxml import etree
except ImportError:
    etree = None
    logging.warning("lxml not available, XML generation features disabled")

from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from xml_processor import XMLProcessor


class SimplifiedXMLGenerator:
    """
    Simplified service for generating clean XML directly from Bullhorn tearsheets
    Bypasses complex monitoring for reliable manual workflow
    """
    
    def __init__(self, db=None):
        self.logger = logging.getLogger(__name__)
        self.db = db
        self.xml_integration = XMLIntegrationService()
        self.xml_processor = XMLProcessor()
        
        # Tearsheet IDs to pull from
        self.tearsheet_ids = [1256, 1264, 1499, 1556]
        
        # File to persist reference number mappings
        self.snapshot_file = 'xml_snapshot.json'
        
        # Thread lock for preventing concurrent generation
        self._generation_lock = False
    
    def generate_fresh_xml(self) -> tuple[str, dict]:
        """
        Generate fresh XML by pulling directly from all Bullhorn tearsheets
        
        Returns:
            tuple: (xml_content_string, stats_dict)
        """
        if self._generation_lock:
            raise Exception("XML generation already in progress")
        
        if not etree:
            raise Exception("lxml not available, cannot generate XML")
        
        try:
            self._generation_lock = True
            self.logger.info("🚀 Starting fresh XML generation from Bullhorn tearsheets")
            
            # Load existing reference number mappings
            existing_references = self._load_reference_snapshot()
            
            # Get BullhornService with credentials from database
            bullhorn_service = self._get_bullhorn_service()
            
            # Authenticate with Bullhorn
            if not bullhorn_service.authenticate():
                raise Exception("Failed to authenticate with Bullhorn")
            
            # Pull jobs from all tearsheets
            all_jobs = self._get_jobs_from_tearsheets(bullhorn_service, self.tearsheet_ids)
            
            if not all_jobs:
                raise Exception("No jobs found in any tearsheets")
            
            # Build XML from job data
            xml_content, updated_references = self._build_clean_xml(all_jobs, existing_references)
            
            # Save updated reference mappings
            self._save_reference_snapshot(updated_references)
            
            # Generate stats
            stats = {
                'job_count': len(all_jobs),
                'tearsheets_processed': len(self.tearsheet_ids),
                'xml_size_bytes': len(xml_content.encode('utf-8')),
                'generated_at': datetime.now().isoformat()
            }
            
            self.logger.info(f"✅ Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
            
            return xml_content, stats
            
        finally:
            self._generation_lock = False
    
    def _get_bullhorn_service(self) -> BullhornService:
        """Get BullhornService with credentials from database"""
        if not self.db:
            raise Exception("Database connection required for credentials")
        
        # Import from app to get the initialized models
        from app import GlobalSettings
        
        credentials = {}
        for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
            try:
                setting = GlobalSettings.query.filter_by(setting_key=key).first()
                if setting and setting.setting_value:
                    credentials[key] = setting.setting_value.strip()
            except Exception as e:
                self.logger.error(f"Error loading credential {key}: {str(e)}")
        
        return BullhornService(
            client_id=credentials.get('bullhorn_client_id'),
            client_secret=credentials.get('bullhorn_client_secret'),
            username=credentials.get('bullhorn_username'),
            password=credentials.get('bullhorn_password')
        )
    
    def _get_jobs_from_tearsheets(self, bullhorn_service: BullhornService, tearsheet_ids: List[int]) -> List[Dict]:
        """
        Pull jobs from all tearsheets with deduplication
        
        Args:
            bullhorn_service: Authenticated Bullhorn service
            tearsheet_ids: List of tearsheet IDs to process
            
        Returns:
            List of unique job dictionaries
        """
        all_jobs = []
        seen_job_ids = set()
        
        for tearsheet_id in tearsheet_ids:
            try:
                self.logger.info(f"Processing tearsheet {tearsheet_id}")
                
                # Get tearsheet members (job IDs)
                members = bullhorn_service.get_tearsheet_members(tearsheet_id)
                if not members:
                    self.logger.warning(f"No members found in tearsheet {tearsheet_id}")
                    continue
                
                job_ids = [member['id'] for member in members if member.get('id')]
                self.logger.info(f"Found {len(job_ids)} job IDs in tearsheet {tearsheet_id}")
                
                # Get full job details in batches
                batch_size = 50
                for i in range(0, len(job_ids), batch_size):
                    batch_ids = job_ids[i:i + batch_size]
                    
                    # Fetch full job details
                    jobs = bullhorn_service.get_jobs_batch(batch_ids)
                    
                    for job in jobs:
                        job_id = job.get('id')
                        if job_id and job_id not in seen_job_ids:
                            # Only include active jobs
                            if job.get('status', '').upper() in ['ACTIVE', 'OPEN']:
                                all_jobs.append(job)
                                seen_job_ids.add(job_id)
                                self.logger.debug(f"Added job {job_id}: {job.get('title', 'Unknown')}")
                
            except Exception as e:
                self.logger.error(f"Error processing tearsheet {tearsheet_id}: {str(e)}")
                continue
        
        self.logger.info(f"Total unique active jobs found: {len(all_jobs)}")
        return all_jobs
    
    def _build_clean_xml(self, jobs: List[Dict], existing_references: Dict) -> tuple[str, Dict]:
        """
        Build clean XML from job data with proper CDATA wrapping
        
        Args:
            jobs: List of Bullhorn job dictionaries
            existing_references: Existing bhatsid -> reference_number mappings
            
        Returns:
            tuple: (xml_string, updated_references_dict)
        """
        if not etree:
            raise Exception("lxml not available, cannot generate XML")
        
        # Create root element
        root = etree.Element("source")
        
        # Add header elements
        title_elem = etree.SubElement(root, "title")
        title_elem.text = "Myticas Consulting"
        
        link_elem = etree.SubElement(root, "link")
        link_elem.text = "https://www.myticas.com"
        
        updated_references = existing_references.copy()
        
        for job_data in jobs:
            try:
                job_id = str(job_data.get('id', ''))
                
                # Get existing reference number or None for new generation
                existing_ref = existing_references.get(job_id)
                
                # Map job to XML format using existing service
                xml_job = self.xml_integration.map_bullhorn_job_to_xml(
                    job_data, 
                    existing_reference_number=existing_ref,
                    skip_ai_classification=True  # Skip AI for faster generation
                )
                
                if not xml_job:
                    self.logger.warning(f"Failed to map job {job_id}, skipping")
                    continue
                
                # Store reference number mapping
                ref_number = xml_job.get('referencenumber')
                if ref_number:
                    updated_references[job_id] = ref_number
                
                # Create job element with CDATA wrapping
                job_elem = etree.SubElement(root, "job")
                
                # Add all fields with CDATA wrapping
                for field_name, field_value in xml_job.items():
                    field_elem = etree.SubElement(job_elem, field_name)
                    # Wrap all fields in CDATA for proper XML handling
                    field_elem.text = etree.CDATA(f" {field_value} ")
                
            except Exception as e:
                self.logger.error(f"Error processing job {job_data.get('id', 'unknown')}: {str(e)}")
                continue
        
        # Convert to clean XML string
        xml_string = etree.tostring(
            root, 
            encoding='UTF-8', 
            xml_declaration=True, 
            pretty_print=True
        ).decode('utf-8')
        
        return xml_string, updated_references
    
    def _load_reference_snapshot(self) -> Dict:
        """Load existing reference number mappings from snapshot file"""
        try:
            if os.path.exists(self.snapshot_file):
                with open(self.snapshot_file, 'r') as f:
                    data = json.load(f)
                    # Check if it's the new format with jobs nested structure
                    if 'jobs' in data:
                        # Extract reference numbers from jobs structure
                        reference_mapping = {}
                        for job_id, job_data in data['jobs'].items():
                            if isinstance(job_data, dict) and 'referencenumber' in job_data:
                                reference_mapping[job_id] = job_data['referencenumber']
                        return reference_mapping
                    else:
                        # Legacy format with direct reference_mapping
                        return data.get('reference_mapping', {})
        except Exception as e:
            self.logger.warning(f"Could not load reference snapshot: {str(e)}")
        
        return {}
    
    def _save_reference_snapshot(self, references: Dict):
        """Save reference number mappings to snapshot file"""
        try:
            # Use simple format for reference mappings only
            snapshot_data = {
                'reference_mapping': references,
                'updated_at': datetime.now().isoformat(),
                'total_mappings': len(references)
            }
            
            with open(self.snapshot_file, 'w') as f:
                json.dump(snapshot_data, f, indent=2)
                
            self.logger.debug(f"Saved {len(references)} reference mappings to snapshot")
            
        except Exception as e:
            self.logger.error(f"Error saving reference snapshot: {str(e)}")