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
        
        # Tearsheet IDs to pull from with monitor name mapping (Bullhorn One - January 2026)
        self.tearsheet_ids = [1231, 1232, 1233, 1239, 1474, 1531]
        
        # Tearsheet ID to monitor name mapping (Bullhorn One IDs)
        self.tearsheet_monitor_mapping = {
            1231: 'Sponsored - OTT',
            1232: 'Sponsored - CHI',
            1233: 'Sponsored - CLE',
            1239: 'Sponsored - VMS', 
            1474: 'Sponsored - GR',
            1531: 'Sponsored - STSI'
        }
        
        # File to persist reference number mappings
        self.snapshot_file = 'xml_snapshot.json'
        
        # Thread lock for preventing concurrent generation
        self._generation_lock = False
    
    def generate_fresh_xml(self, tearsheet_caps=None) -> tuple[str, dict]:
        """
        Generate fresh XML by pulling directly from all Bullhorn tearsheets
        
        Args:
            tearsheet_caps: Optional dict of {tearsheet_id: max_jobs} to limit jobs
                            per tearsheet. e.g. {1531: 10} caps STSI to 10 jobs.
                            Jobs are selected by most-recently-added-to-tearsheet.
        
        Returns:
            tuple: (xml_content_string, stats_dict)
        """
        if self._generation_lock:
            raise Exception("XML generation already in progress")
        
        if not etree:
            raise Exception("lxml not available, cannot generate XML")
        
        try:
            self._generation_lock = True
            cap_label = f" (caps: {tearsheet_caps})" if tearsheet_caps else ""
            self.logger.info(f"🚀 Starting fresh XML generation from Bullhorn tearsheets{cap_label}")
            
            existing_references = self._load_references_from_database()
            
            bullhorn_service = self._get_bullhorn_service()
            
            if not bullhorn_service.authenticate():
                raise Exception("Failed to authenticate with Bullhorn")
            
            all_jobs_with_context = self._get_jobs_from_tearsheets(
                bullhorn_service, self.tearsheet_ids, tearsheet_caps=tearsheet_caps
            )
            
            if not all_jobs_with_context:
                raise Exception("No jobs found in any tearsheets")
            
            xml_content, updated_references = self._build_clean_xml(all_jobs_with_context, existing_references)
            
            self._save_references_to_database(xml_content)
            
            stats = {
                'job_count': len(all_jobs_with_context),
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
    
    # Statuses that indicate a job should NOT be in the sponsored XML feed
    # Must match the INELIGIBLE_STATUSES in IncrementalMonitoringService
    INELIGIBLE_STATUSES = {
        'qualifying', 'hold - covered', 'hold - client hold', 'offer out',
        'filled', 'lost - competition', 'lost - filled internally',
        'lost - funding', 'canceled', 'placeholder/ mpc', 'archive'
    }

    def _get_recent_tearsheet_job_ids(self, tearsheet_id: int, limit: int) -> Optional[set]:
        """
        Get the N most recently added job IDs for a tearsheet,
        based on when each job was first observed in TearsheetJobHistory.
        
        Returns None if no history data exists — caller should apply job-ID fallback cap.
        """
        try:
            from models import TearsheetJobHistory
            from sqlalchemy import func
            results = self.db.session.query(
                TearsheetJobHistory.job_id,
                func.min(TearsheetJobHistory.timestamp).label('first_seen')
            ).filter_by(
                tearsheet_id=tearsheet_id
            ).group_by(
                TearsheetJobHistory.job_id
            ).order_by(
                func.min(TearsheetJobHistory.timestamp).desc()
            ).limit(limit).all()
            if not results:
                self.logger.warning(f"⚠️ No TearsheetJobHistory for tearsheet {tearsheet_id} — will fall back to job ID ordering")
                return None
            recent_ids = {str(r.job_id) for r in results}
            self.logger.info(f"📋 Tearsheet {tearsheet_id} cap: keeping {len(recent_ids)} most recent (by timestamp) of {limit} requested")
            return recent_ids
        except Exception as e:
            self.logger.error(f"Failed to query TearsheetJobHistory for cap on {tearsheet_id}: {e} — will fall back to job ID ordering")
            return None

    def _get_jobs_from_tearsheets(self, bullhorn_service: BullhornService, tearsheet_ids: List[int],
                                  tearsheet_caps: Optional[Dict[int, int]] = None) -> List[Dict]:
        """
        Pull jobs from all tearsheets using the same proven method as main monitoring system
        
        Args:
            bullhorn_service: Authenticated Bullhorn service
            tearsheet_ids: List of tearsheet IDs to process
            tearsheet_caps: Optional dict of {tearsheet_id: max_jobs} to cap specific tearsheets
            
        Returns:
            List of job dictionaries with tearsheet_context added (ineligible jobs filtered out)
        """
        all_jobs = []
        seen_job_ids = set()
        total_filtered = 0
        
        cap_filters = {}
        if tearsheet_caps:
            for ts_id, max_jobs in tearsheet_caps.items():
                cap_filters[ts_id] = self._get_recent_tearsheet_job_ids(ts_id, max_jobs)
        
        for tearsheet_id in tearsheet_ids:
            try:
                self.logger.info(f"Processing tearsheet {tearsheet_id}")
                
                jobs = bullhorn_service.get_tearsheet_jobs(tearsheet_id)
                
                if not jobs:
                    self.logger.warning(f"No jobs found in tearsheet {tearsheet_id}")
                    continue
                
                self.logger.info(f"Found {len(jobs)} jobs in tearsheet {tearsheet_id}")
                
                allowed_ids = cap_filters.get(tearsheet_id)
                cap_limit = tearsheet_caps.get(tearsheet_id) if tearsheet_caps else None
                use_id_fallback = (cap_limit is not None) and (allowed_ids is None)

                if use_id_fallback:
                    self.logger.info(f"📋 Tearsheet {tearsheet_id} cap: using job ID fallback (no TearsheetJobHistory data) — keeping top {cap_limit} by ID")

                filtered_count = 0
                eligible_jobs_this_tearsheet = []

                for job in jobs:
                    job_id = job.get('id')
                    if job_id and job_id not in seen_job_ids:
                        status = job.get('status', '').strip()
                        is_open = job.get('isOpen')
                        
                        if is_open == False or str(is_open).lower() in ('closed', 'false'):
                            self.logger.info(f"  Filtered job {job_id}: {job.get('title', '?')[:50]} (isOpen={is_open})")
                            filtered_count += 1
                            continue
                        
                        if status.lower() in self.INELIGIBLE_STATUSES:
                            self.logger.info(f"  Filtered job {job_id}: {job.get('title', '?')[:50]} (status={status})")
                            filtered_count += 1
                            continue

                        if allowed_ids is not None and str(job_id) not in allowed_ids:
                            continue

                        eligible_jobs_this_tearsheet.append(job)

                if use_id_fallback and cap_limit:
                    eligible_jobs_this_tearsheet.sort(key=lambda j: j.get('id', 0), reverse=True)
                    eligible_jobs_this_tearsheet = eligible_jobs_this_tearsheet[:cap_limit]

                capped_count = 0
                monitor_name = self.tearsheet_monitor_mapping.get(tearsheet_id, 'default')
                for job in eligible_jobs_this_tearsheet:
                    job_id = job.get('id')
                    if job_id not in seen_job_ids:
                        job['tearsheet_context'] = {
                            'tearsheet_id': tearsheet_id,
                            'monitor_name': monitor_name
                        }
                        all_jobs.append(job)
                        seen_job_ids.add(job_id)
                        self.logger.debug(f"Added job {job_id}: {job.get('title', 'Unknown')} from {monitor_name}")
                    else:
                        capped_count += 1

                if filtered_count > 0:
                    self.logger.info(f"  ⚠️ Filtered out {filtered_count} ineligible jobs from tearsheet {tearsheet_id}")
                    total_filtered += filtered_count
                if capped_count > 0:
                    self.logger.info(f"  🔒 Capped: excluded {capped_count} older jobs from tearsheet {tearsheet_id} (cap={cap_limit})")
                    
            except Exception as e:
                self.logger.error(f"Error processing tearsheet {tearsheet_id}: {str(e)}")
                continue
        
        if total_filtered > 0:
            self.logger.info(f"📊 Total: {len(all_jobs)} eligible jobs (filtered {total_filtered} ineligible)")
        else:
            self.logger.info(f"Total unique jobs found: {len(all_jobs)}")
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
        
        # Prepare data for batch processing with enhanced keyword classifier
        existing_ref_map = {}
        monitor_name_map = {}
        
        for job_data in jobs:
            job_id = str(job_data.get('id', ''))
            existing_ref_map[job_id] = existing_references.get(job_id, '')
            tearsheet_context = job_data.get('tearsheet_context', {})
            monitor_name_map[job_id] = tearsheet_context.get('monitor_name', 'default')
        
        # Use batch processing with keyword classification (fast, no timeouts)
        self.logger.info(f"🔧 Processing {len(jobs)} jobs with enhanced keyword classifier...")
        
        try:
            # Process all jobs with keyword classification in batches
            xml_jobs = self.xml_integration.map_bullhorn_jobs_to_xml_batch(
                jobs_data=jobs,
                existing_references=existing_ref_map,
                enable_ai_classification=False,  # Keyword-only classification
                monitor_names=monitor_name_map
            )
            
            # Add XML jobs to the root element
            for xml_job in xml_jobs:
                job_id = xml_job.get('bhatsid', '')
                
                # Store reference number mapping
                ref_number = xml_job.get('referencenumber')
                if ref_number and job_id:
                    updated_references[job_id] = ref_number
                
                # Create job element with CDATA wrapping
                job_elem = etree.SubElement(root, "job")
                
                # Add all fields with CDATA wrapping
                for field_name, field_value in xml_job.items():
                    field_elem = etree.SubElement(job_elem, field_name)
                    # Wrap all fields in CDATA for proper XML handling
                    field_elem.text = etree.CDATA(f" {field_value} ")
            
            self.logger.info(f"✅ Successfully processed {len(xml_jobs)} jobs with keyword classification")
            
        except Exception as batch_error:
            self.logger.error(f"Batch processing failed: {str(batch_error)}")
            self.logger.warning("⚠️ Falling back to individual processing")
            
            # Fallback to individual processing without AI as safety measure
            for job_data in jobs:
                try:
                    job_id = str(job_data.get('id', ''))
                    existing_ref = existing_ref_map.get(job_id, '')
                    monitor_name = monitor_name_map.get(job_id, 'default')
                    
                    xml_job = self.xml_integration.map_bullhorn_job_to_xml(
                        job_data, 
                        existing_reference_number=existing_ref,
                        skip_ai_classification=True,  # Fallback without AI
                        monitor_name=monitor_name
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
                    tearsheet_context = job_data.get('tearsheet_context', {})
                    monitor_name = tearsheet_context.get('monitor_name', 'unknown')
                    self.logger.error(f"Error processing job {job_data.get('id', 'unknown')} from {monitor_name}: {str(e)}")
                    continue
        
        # Convert to clean XML string
        xml_string = etree.tostring(
            root, 
            encoding='UTF-8', 
            xml_declaration=True, 
            pretty_print=True
        ).decode('utf-8')
        
        return xml_string, updated_references
    
    def _load_references_from_database(self) -> Dict:
        """
        Load existing reference number mappings from DATABASE (database-first approach)
        This ensures reference numbers persist across all automated uploads and refreshes
        Database is the ONLY source of truth - no fallbacks
        """
        try:
            from lightweight_reference_refresh import get_existing_references_from_database
            
            # Load from database - this is the ONLY source of truth
            existing_references = get_existing_references_from_database()
            
            self.logger.info(f"💾 DATABASE-FIRST: Loaded {len(existing_references)} reference numbers from DATABASE")
            return existing_references
            
        except Exception as e:
            self.logger.critical(f"❌ CRITICAL: Failed to load references from database: {str(e)}")
            # Database is required - raise exception instead of silent fallback
            raise Exception(f"Database-first architecture requires DB access: {str(e)}")
    
    def _load_reference_snapshot(self) -> Dict:
        """
        DEPRECATED: Load existing reference number mappings from snapshot file
        This method is kept for backward compatibility but is no longer used
        """
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
    
    def _save_references_to_database(self, xml_content: str):
        """
        Save reference numbers to DATABASE (database-first approach)
        This ensures reference numbers persist across all automated uploads and refreshes
        Database is the ONLY source of truth - failures are critical
        """
        try:
            from lightweight_reference_refresh import save_references_to_database
            
            # Save to database - this is REQUIRED for database-first architecture
            success = save_references_to_database(xml_content)
            
            if success:
                self.logger.info("💾 DATABASE-FIRST: Reference numbers saved to DATABASE successfully")
            else:
                # Database save failure is CRITICAL in database-first architecture
                self.logger.critical("❌ CRITICAL: Failed to save reference numbers to DATABASE")
                raise Exception("Database-first architecture requires successful DB save")
                
        except Exception as e:
            self.logger.critical(f"❌ CRITICAL: Error saving references to database: {str(e)}")
            # Re-raise to ensure failures are not silent
            raise
    
    def _save_reference_snapshot(self, references: Dict):
        """
        DEPRECATED: Save reference number mappings to snapshot file
        This method is kept for backward compatibility but database is now the primary storage
        """
        try:
            # Use simple format for reference mappings only
            snapshot_data = {
                'reference_mapping': references,
                'updated_at': datetime.now().isoformat(),
                'total_mappings': len(references)
            }
            
            with open(self.snapshot_file, 'w') as f:
                json.dump(snapshot_data, f, indent=2)
                
            self.logger.debug(f"Saved {len(references)} reference mappings to snapshot (legacy)")
            
        except Exception as e:
            self.logger.error(f"Error saving reference snapshot: {str(e)}")