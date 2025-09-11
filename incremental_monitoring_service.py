"""
Simplified Incremental Monitoring Service
Replaces the over-complex 8-step comprehensive monitoring with a straightforward approach:
1. Check tearsheets for job changes every 5 minutes
2. Add new jobs, remove deleted jobs, update modified jobs
3. Upload to server
4. No emails except for reference number refresh
"""

import os
import logging
import time
import fcntl
import json
from datetime import datetime
from typing import Dict, List, Optional, Set
from lxml import etree
import paramiko
import re

from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from tearsheet_config import TearsheetConfig

class IncrementalMonitoringService:
    """Simplified incremental monitoring service"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.bullhorn_service = None
        self.xml_integration = XMLIntegrationService()
        self.parser = etree.XMLParser(strip_cdata=False, recover=True)
        self.lock_file = '/tmp/myticas_feed.lock'
        
    def run_monitoring_cycle(self) -> Dict:
        """
        Run simplified monitoring cycle
        Returns dict with cycle results
        """
        cycle_results = {
            'timestamp': datetime.now().isoformat(),
            'jobs_added': 0,
            'jobs_removed': 0,
            'jobs_updated': 0,
            'total_jobs': 0,
            'upload_success': False,
            'cycle_time': 0,
            'errors': []
        }
        excluded_count = 0  # Initialize before try block
        
        start_time = time.time()
        xml_file = 'myticas-job-feed-v2.xml'
        
        # Acquire lock to prevent concurrent modifications
        if not self._acquire_lock():
            self.logger.warning("Another monitoring process is running - skipping cycle")
            cycle_results['errors'].append("Lock acquisition failed")
            return cycle_results
        
        try:
            self.logger.info("=" * 60)
            self.logger.info("STARTING INCREMENTAL MONITORING CYCLE")
            self.logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.logger.info("=" * 60)
            
            # Initialize Bullhorn connection
            if not self.bullhorn_service:
                self.bullhorn_service = BullhornService(
                    client_id=os.environ.get('BULLHORN_CLIENT_ID'),
                    client_secret=os.environ.get('BULLHORN_CLIENT_SECRET'),
                    username=os.environ.get('BULLHORN_USERNAME'),
                    password=os.environ.get('BULLHORN_PASSWORD')
                )
            
            if not self.bullhorn_service.test_connection():
                self.logger.error("Failed to connect to Bullhorn")
                cycle_results['errors'].append("Bullhorn connection failed")
                return cycle_results
            
            # Step 1: Fetch all jobs from monitored tearsheets (with proper pagination)
            self.logger.info("\n📋 Step 1: Fetching jobs from tearsheets...")
            bullhorn_jobs = self._fetch_all_tearsheet_jobs()
            self.logger.info(f"  ✅ TEARSHEET FETCH COMPLETED: {len(bullhorn_jobs)} jobs")
            
            # Step 2: Load current XML and compare
            self.logger.info("\n📄 Step 2: Loading current XML and comparing...")
            self.logger.info("  🔍 About to call _load_xml_jobs()...")
            current_xml_jobs = self._load_xml_jobs(xml_file)
            self.logger.info(f"  ✅ XML LOAD COMPLETED: {len(current_xml_jobs)} jobs")
            self.logger.info(f"  Current XML has {len(current_xml_jobs)} jobs")
            
            # Determine changes
            bullhorn_ids = set(bullhorn_jobs.keys())
            xml_ids = set(current_xml_jobs.keys())
            
            jobs_to_add = bullhorn_ids - xml_ids
            jobs_to_remove = xml_ids - bullhorn_ids
            jobs_to_update = bullhorn_ids & xml_ids
            
            self.logger.info(f"  Changes detected: +{len(jobs_to_add)} -{len(jobs_to_remove)} ~{len(jobs_to_update)}")
            
            # Step 3: Apply changes incrementally
            self.logger.info("\n🔄 Step 3: Applying incremental changes...")
            
            # Add new jobs (preserve any existing reference numbers if they somehow exist)
            for job_id in jobs_to_add:
                try:
                    job_data = bullhorn_jobs[job_id]
                    # Map to XML format with proper field trimming
                    xml_job = self._map_to_xml_format(job_data)
                    if self._add_job_to_xml(xml_file, xml_job):
                        cycle_results['jobs_added'] += 1
                        self.logger.info(f"  ✅ Added job {job_id}: {job_data.get('title', '')}")
                except Exception as e:
                    self.logger.error(f"  ❌ Failed to add job {job_id}: {str(e)}")
                    cycle_results['errors'].append(f"Add job {job_id}: {str(e)}")
            
            # Remove jobs no longer in Bullhorn
            for job_id in jobs_to_remove:
                try:
                    if self._remove_job_from_xml(xml_file, job_id):
                        cycle_results['jobs_removed'] += 1
                        self.logger.info(f"  ✅ Removed job {job_id}")
                except Exception as e:
                    self.logger.error(f"  ❌ Failed to remove job {job_id}: {str(e)}")
                    cycle_results['errors'].append(f"Remove job {job_id}: {str(e)}")
            
            # Update modified jobs (preserving reference numbers)
            updates_made = 0
            for job_id in jobs_to_update:
                try:
                    job_data = bullhorn_jobs[job_id]
                    xml_job = current_xml_jobs[job_id]
                    
                    # Check if job content has changed (excluding reference number)
                    if self._has_job_changed(job_data, xml_job):
                        # Preserve the existing reference number
                        preserved_ref = xml_job.get('referencenumber', '')
                        xml_updated = self._map_to_xml_format(job_data, preserved_ref)
                        
                        if self._update_job_in_xml(xml_file, job_id, xml_updated):
                            updates_made += 1
                            self.logger.info(f"  ✅ Updated job {job_id}: {job_data.get('title', '')}")
                except Exception as e:
                    self.logger.error(f"  ❌ Failed to update job {job_id}: {str(e)}")
                    cycle_results['errors'].append(f"Update job {job_id}: {str(e)}")
            
            cycle_results['jobs_updated'] = updates_made
            
            # Count total jobs
            cycle_results['total_jobs'] = len(self._load_xml_jobs(xml_file))
            
            # Step 4: Manual workflow - SFTP auto-upload disabled
            self.logger.info("\n📊 Step 4: Monitoring complete - ready for manual download")
            cycle_results['upload_success'] = True  # Manual workflow always 'succeeds'
            self.logger.info("  ✅ XML updated locally - use manual download for publishing")
            
            # Include excluded job count in reporting
            excluded_count = getattr(self.bullhorn_service, 'excluded_count', 0) if self.bullhorn_service else 0
            cycle_results['excluded_jobs'] = excluded_count
            
            if excluded_count > 0:
                self.logger.info("  📋 Job counts: Add +{}, Remove -{}, Update ~{}, Excluded {}".format(
                    cycle_results['jobs_added'], cycle_results['jobs_removed'], 
                    cycle_results['jobs_updated'], excluded_count))
            else:
                self.logger.info("  📋 Job counts: Add +{}, Remove -{}, Update ~{}".format(
                    cycle_results['jobs_added'], cycle_results['jobs_removed'], cycle_results['jobs_updated']))
            
        except Exception as e:
            self.logger.error(f"Monitoring cycle error: {str(e)}")
            cycle_results['errors'].append(f"Cycle error: {str(e)}")
        finally:
            # Release lock first
            self._release_lock()
            cycle_results['cycle_time'] = time.time() - start_time
            
            # Create activity record after releasing lock (with app context)
            self._log_monitoring_activity(cycle_results, excluded_count)
            
            self.logger.info("\n" + "=" * 60)
            self.logger.info(f"CYCLE COMPLETED in {cycle_results['cycle_time']:.1f}s")
            self.logger.info(f"Results: +{cycle_results['jobs_added']} -{cycle_results['jobs_removed']} ~{cycle_results['jobs_updated']}")
            self.logger.info(f"Total jobs: {cycle_results['total_jobs']}")
            if cycle_results['errors']:
                self.logger.warning(f"Errors: {len(cycle_results['errors'])}")
            self.logger.info("=" * 60)
        
        return cycle_results
    
    def _log_monitoring_activity(self, cycle_results: Dict, excluded_count: int):
        """Create BullhornActivity record for monitoring cycle"""
        try:
            # Import here to avoid circular imports
            from app import app, db, BullhornActivity
            
            # Prepare activity details
            activity_details = {
                'jobs_added': cycle_results['jobs_added'],
                'jobs_removed': cycle_results['jobs_removed'],
                'jobs_updated': cycle_results['jobs_updated'],
                'excluded_jobs': excluded_count,
                'total_jobs': cycle_results['total_jobs'],
                'cycle_time': round(cycle_results.get('cycle_time', 0), 1),
                'errors': cycle_results.get('errors', [])
            }
            
            # Create activity entry within Flask app context
            with app.app_context():
                try:
                    activity = BullhornActivity(
                        monitor_id=None,  # System-level monitoring activity
                        activity_type='monitoring_cycle_completed',
                        job_id=None,
                        job_title=None,
                        details=json.dumps(activity_details),
                        notification_sent=False
                    )
                    
                    db.session.add(activity)
                    db.session.commit()
                    
                    self.logger.info("✅ Activity logged: Monitoring cycle completed")
                    
                except Exception as db_error:
                    db.session.rollback()
                    self.logger.error(f"❌ Database error logging activity: {str(db_error)}")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to log monitoring activity: {str(e)}")
    
    def _acquire_lock(self, timeout: int = 30) -> bool:
        """Acquire file lock to prevent concurrent modifications"""
        try:
            self.lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_WRONLY)
            # Non-blocking lock attempt
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (IOError, OSError) as e:
            if hasattr(self, 'lock_fd'):
                os.close(self.lock_fd)
            return False
    
    def _release_lock(self):
        """Release file lock"""
        try:
            if hasattr(self, 'lock_fd'):
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                os.close(self.lock_fd)
                delattr(self, 'lock_fd')
        except:
            pass
    
    def _fetch_all_tearsheet_jobs(self) -> Dict[str, Dict]:
        """Fetch all jobs from monitored tearsheets with proper pagination"""
        all_jobs = {}
        
        # Get active monitors - hardcoded for now since TearsheetConfig method doesn't exist
        class MockMonitor:
            def __init__(self, name, tearsheet_id):
                self.name = name
                self.tearsheet_id = tearsheet_id
                self.is_active = True
        
        monitors = [
            MockMonitor('Sponsored - OTT', 1256),
            MockMonitor('Sponsored - VMS', 1264),
            MockMonitor('Sponsored - GR', 1499),
            MockMonitor('Sponsored - CHI', 1239),
            MockMonitor('Sponsored - STSI', 1556)
        ]
        
        for monitor in monitors:
            if not hasattr(monitor, 'is_active') or not monitor.is_active:
                continue
            
            try:
                self.logger.info(f"  Fetching from {monitor.name}...")
                
                # Use the appropriate method based on monitor type
                if monitor.tearsheet_id == 0:
                    # Use query-based search (now with pagination)
                    jobs = self.bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                else:
                    # Use tearsheet ID (already has pagination)
                    jobs = self.bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                
                # Process and add jobs
                for job in jobs:
                    job_id = str(job.get('id'))
                    # Store monitor info for company mapping
                    job['_monitor_name'] = monitor.name
                    all_jobs[job_id] = job
                
                self.logger.info(f"    Found {len(jobs)} jobs")
                
            except Exception as e:
                self.logger.error(f"    Error fetching from {monitor.name}: {str(e)}")
        
        return all_jobs
    
    def _load_xml_jobs(self, xml_file: str) -> Dict[str, Dict]:
        """Load jobs from XML file"""
        jobs = {}
        
        try:
            if not os.path.exists(xml_file):
                return jobs
            
            tree = etree.parse(xml_file, self.parser)
            root = tree.getroot()
            
            for job_elem in root.findall('.//job'):
                # Extract job ID
                bhatsid_elem = job_elem.find('.//bhatsid')
                if bhatsid_elem is None:
                    continue
                
                job_id = self._extract_text(bhatsid_elem)
                if not job_id:
                    continue
                
                # Extract all job fields
                job_data = {
                    'id': job_id,
                    'title': self._extract_text(job_elem.find('.//title')),
                    'company': self._extract_text(job_elem.find('.//company')),
                    'date': self._extract_text(job_elem.find('.//date')),
                    'referencenumber': self._extract_text(job_elem.find('.//referencenumber')),
                    'url': self._extract_text(job_elem.find('.//url')),
                    'description': self._extract_text(job_elem.find('.//description')),
                    'jobtype': self._extract_text(job_elem.find('.//jobtype')),
                    'city': self._extract_text(job_elem.find('.//city')),
                    'state': self._extract_text(job_elem.find('.//state')),
                    'country': self._extract_text(job_elem.find('.//country')),
                    'remotetype': self._extract_text(job_elem.find('.//remotetype')),
                    'assignedrecruiter': self._extract_text(job_elem.find('.//assignedrecruiter')),
                    'jobfunction': self._extract_text(job_elem.find('.//jobfunction')),
                    'jobindustries': self._extract_text(job_elem.find('.//jobindustries')),
                    'senioritylevel': self._extract_text(job_elem.find('.//senioritylevel'))
                }
                
                jobs[job_id] = job_data
                
        except Exception as e:
            self.logger.error(f"Error loading XML jobs: {str(e)}")
        
        return jobs
    
    def _extract_text(self, elem) -> str:
        """Extract text from XML element, handling CDATA"""
        if elem is None:
            return ''
        
        text = elem.text or ''
        
        # Handle CDATA
        if '<![CDATA[' in text:
            text = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', text, flags=re.DOTALL)
        
        return text.strip()
    
    def _map_to_xml_format(self, bullhorn_job: Dict, preserve_ref: str = '') -> Dict:
        """Map Bullhorn job to XML format with field trimming"""
        
        # Get monitor name for company mapping
        monitor_name = bullhorn_job.get('_monitor_name', '')
        company_name = TearsheetConfig.get_company_name(monitor_name)
        
        # Extract and trim location fields - handle lists
        address = bullhorn_job.get('address', {}) or {}
        city = self._safe_extract_string(address.get('city'))
        state = self._safe_extract_string(address.get('state'))
        country = self._safe_extract_string(address.get('countryName', 'United States'))
        
        # Extract job type
        employment_type = self._safe_extract_string(bullhorn_job.get('employmentType'))
        job_type = self._map_employment_type(employment_type)
        
        # Extract remote type
        on_site = self._safe_extract_string(bullhorn_job.get('onSite'))
        remote_type = self._map_remote_type(on_site)
        
        # Extract recruiter
        recruiter = self._extract_recruiter(bullhorn_job)
        
        # Build clean title
        title = self._safe_extract_string(bullhorn_job.get('title'))
        job_id = str(bullhorn_job.get('id', ''))
        
        # Generate or preserve reference number
        if preserve_ref:
            reference_number = preserve_ref
        else:
            reference_number = self.xml_integration.xml_processor.generate_reference_number()
        
        return {
            'id': job_id,
            'bhatsid': job_id,
            'title': title,
            'company': company_name,
            'date': self._format_date(bullhorn_job.get('dateAdded')),
            'referencenumber': reference_number,
            'url': self._generate_job_url(job_id, title, company_name),
            'description': bullhorn_job.get('publicDescription') or bullhorn_job.get('description') or '',
            'jobtype': job_type,
            'city': city,
            'state': state,
            'country': country,
            'category': '',
            'apply_email': 'apply@myticas.com',
            'remotetype': remote_type,
            'assignedrecruiter': recruiter,
            'jobfunction': '',  # Will be filled by AI if needed
            'jobindustries': '',  # Will be filled by AI if needed
            'senioritylevel': ''  # Will be filled by AI if needed
        }
    
    def _map_employment_type(self, employment_type: str) -> str:
        """Map Bullhorn employment type to XML jobtype"""
        if not employment_type:
            return 'Contract'
        
        employment_type_lower = employment_type.lower()
        if 'direct' in employment_type_lower or 'perm' in employment_type_lower:
            return 'Direct Hire'
        elif 'contract' in employment_type_lower:
            return 'Contract'
        else:
            return employment_type
    
    def _map_remote_type(self, on_site: str) -> str:
        """Map Bullhorn onSite field to remotetype"""
        if not on_site:
            return 'Hybrid'
        
        on_site_lower = on_site.lower()
        if 'remote' in on_site_lower:
            return 'Remote'
        elif 'hybrid' in on_site_lower:
            return 'Hybrid'
        elif 'onsite' in on_site_lower or 'on-site' in on_site_lower:
            return 'On-site'
        else:
            return 'Hybrid'
    
    def _extract_recruiter(self, bullhorn_job: Dict) -> str:
        """Extract recruiter information using restored LinkedIn tag formatting WITH names"""
        # Import here to avoid circular imports
        from xml_integration_service import XMLIntegrationService
        
        # Create an instance to use the restored _extract_assigned_recruiter method
        xml_service = XMLIntegrationService()
        
        # Extract recruiter data from multiple possible fields
        assignments = bullhorn_job.get('assignments', {})
        assigned_users = bullhorn_job.get('assignedUsers', {}) 
        response_user = bullhorn_job.get('responseUser', {})
        owner = bullhorn_job.get('owner', {})
        
        # Use the restored method that returns full LinkedIn tags with names
        linkedin_tag_with_name = xml_service._extract_assigned_recruiter(assignments, assigned_users, response_user, owner)
        
        return linkedin_tag_with_name
    
    def _get_recruiter_tag(self, name: str) -> str:
        """Get LinkedIn recruiter tag for name"""
        # Simplified mapping - could be expanded
        tags = {
            'Rachel Mann': '#LI-RM1',
            'Mike Scalzitti': '#LI-MS2',
            'Christine Carter': '#LI-CC1',
            'Runa Parmar': '#LI-RP1',
            'Dominic Scaletta': '#LI-DS2',
            'Ryan Oliver': '#LI-RO1',
            'Michael Theodossiou': '#LI-MT2'
        }
        return tags.get(name, '')
    
    def _safe_extract_string(self, value) -> str:
        """Safely extract string from value that might be a list, string, or None"""
        if value is None:
            return ''
        elif isinstance(value, list):
            # If it's a list, take the first non-empty item
            for item in value:
                if item and str(item).strip():
                    return str(item).strip()
            return ''
        else:
            return str(value).strip()
    
    def _format_date(self, timestamp) -> str:
        """Format date from timestamp"""
        if not timestamp:
            return datetime.now().strftime('%B %d, %Y')
        
        try:
            if isinstance(timestamp, (int, float)):
                dt = datetime.fromtimestamp(timestamp / 1000)
            else:
                dt = datetime.strptime(str(timestamp), '%Y-%m-%d')
            return dt.strftime('%B %d, %Y')
        except:
            return datetime.now().strftime('%B %d, %Y')
    
    def _generate_job_url(self, job_id: str, title: str, company: str) -> str:
        """Generate job application URL"""
        import urllib.parse
        
        # Determine base URL based on company
        if 'STSI' in company:
            base_url = 'https://apply.stsigroup.com'
        else:
            base_url = 'https://apply.myticas.com'
        
        # Clean and encode title
        safe_title = title.replace('/', ' ').replace('\\', ' ')
        if '(' in safe_title:  # Remove job ID from title if present
            safe_title = safe_title.split('(')[0].strip()
        encoded_title = urllib.parse.quote(safe_title)
        
        return f"{base_url}/{job_id}/{encoded_title}/?source=LinkedIn"
    
    def _has_job_changed(self, bullhorn_job: Dict, xml_job: Dict) -> bool:
        """Check if job content has materially changed (excluding reference number)"""
        
        # Map Bullhorn to XML format for comparison
        mapped = self._map_to_xml_format(bullhorn_job, xml_job.get('referencenumber', ''))
        
        # Compare key fields (excluding reference number and AI fields)
        fields_to_compare = ['title', 'company', 'description', 'city', 'state', 
                            'country', 'jobtype', 'remotetype', 'assignedrecruiter']
        
        for field in fields_to_compare:
            if mapped.get(field, '').strip() != xml_job.get(field, '').strip():
                return True
        
        return False
    
    def _add_job_to_xml(self, xml_file: str, job_data: Dict) -> bool:
        """Add job to XML file with atomic write"""
        try:
            # Read current XML
            tree = etree.parse(xml_file, self.parser)
            root = tree.getroot()
            
            # Create job element
            job_elem = self._create_job_element(job_data)
            
            # Add after publisher info
            publisher_url = root.find('.//publisherurl')
            if publisher_url is not None:
                index = list(root).index(publisher_url) + 1
                root.insert(index, job_elem)
            else:
                root.append(job_elem)
            
            # Write atomically
            return self._write_xml_atomically(xml_file, tree)
            
        except Exception as e:
            self.logger.error(f"Error adding job: {str(e)}")
            return False
    
    def _remove_job_from_xml(self, xml_file: str, job_id: str) -> bool:
        """Remove job from XML file with atomic write"""
        try:
            # Read current XML
            tree = etree.parse(xml_file, self.parser)
            root = tree.getroot()
            
            # Find and remove job
            removed = False
            for job_elem in root.findall('.//job'):
                bhatsid_elem = job_elem.find('.//bhatsid')
                if bhatsid_elem is not None:
                    if self._extract_text(bhatsid_elem) == str(job_id):
                        root.remove(job_elem)
                        removed = True
                        break
            
            if removed:
                return self._write_xml_atomically(xml_file, tree)
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error removing job: {str(e)}")
            return False
    
    def _update_job_in_xml(self, xml_file: str, job_id: str, job_data: Dict) -> bool:
        """Update job in XML file preserving reference number"""
        try:
            # Read current XML
            tree = etree.parse(xml_file, self.parser)
            root = tree.getroot()
            
            # Find and update job
            updated = False
            for job_elem in root.findall('.//job'):
                bhatsid_elem = job_elem.find('.//bhatsid')
                if bhatsid_elem is not None:
                    if self._extract_text(bhatsid_elem) == str(job_id):
                        # Get parent and index
                        parent = job_elem.getparent()
                        index = list(parent).index(job_elem)
                        
                        # Create new element with updated data
                        new_job_elem = self._create_job_element(job_data)
                        
                        # Replace old with new
                        parent.remove(job_elem)
                        parent.insert(index, new_job_elem)
                        updated = True
                        break
            
            if updated:
                return self._write_xml_atomically(xml_file, tree)
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error updating job: {str(e)}")
            return False
    
    def _fix_unclosed_html_tags(self, html_content: str) -> str:
        """Fix unclosed HTML tags using proper HTML parsing"""
        if not html_content:
            return ""
        
        try:
            from lxml import html
            
            # Parse the HTML content as a fragment
            # This automatically fixes unclosed tags like <li>, <p>, etc.
            fragment = html.fragment_fromstring(html_content, create_parent='div')
            
            # Convert back to string with proper HTML serialization
            fixed_content = html.tostring(fragment, encoding='unicode', method='html')
            
            # Remove the wrapper div we added
            if fixed_content.startswith('<div>') and fixed_content.endswith('</div>'):
                fixed_content = fixed_content[5:-6]
            
            # Clean up any excessive whitespace while preserving structure
            fixed_content = fixed_content.strip()
            
            return fixed_content
            
        except Exception as e:
            self.logger.warning(f"Error fixing HTML tags with lxml, returning original: {str(e)}")
            # If lxml fails for any reason, return the original content
            return html_content
    
    def _create_job_element(self, job_data: Dict) -> etree.Element:
        """Create XML job element with CDATA wrapping"""
        job = etree.Element('job')
        
        # Helper to create sub-element with CDATA
        def add_field(name: str, value: str):
            elem = etree.SubElement(job, name)
            # Special handling for description field to fix HTML
            if name == 'description' and value:
                value = self._fix_unclosed_html_tags(value)
            # Always wrap ALL fields in CDATA as per requirement
            # Ensure value is a string and handle None values
            if value is None:
                value = ''
            else:
                value = str(value)
            # Wrap ALL fields in CDATA sections with spaces before and after
            elem.text = etree.CDATA(f' {value} ')
        
        # Add all fields
        title_with_id = job_data.get('title', '')
        job_id = job_data.get('bhatsid', job_data.get('id', ''))
        if job_id and f'({job_id})' not in title_with_id:
            title_with_id = f"{title_with_id} ({job_id})"
        
        add_field('title', title_with_id)
        add_field('company', job_data.get('company', ''))
        add_field('date', job_data.get('date', ''))
        add_field('referencenumber', job_data.get('referencenumber', ''))
        add_field('bhatsid', str(job_data.get('bhatsid', job_data.get('id', ''))))
        add_field('url', job_data.get('url', ''))
        add_field('description', job_data.get('description', ''))
        add_field('jobtype', job_data.get('jobtype', ''))
        add_field('city', job_data.get('city', ''))
        add_field('state', job_data.get('state', ''))
        add_field('country', job_data.get('country', ''))
        add_field('category', job_data.get('category', ''))
        add_field('apply_email', job_data.get('apply_email', 'apply@myticas.com'))
        add_field('remotetype', job_data.get('remotetype', ''))
        add_field('assignedrecruiter', job_data.get('assignedrecruiter', ''))
        add_field('jobfunction', job_data.get('jobfunction', ''))
        add_field('jobindustries', job_data.get('jobindustries', ''))
        add_field('senioritylevel', job_data.get('senioritylevel', ''))
        
        return job
    
    def _write_xml_atomically(self, xml_file: str, tree) -> bool:
        """Write XML tree to file atomically"""
        try:
            # Write to temporary file
            tmp_file = f"{xml_file}.tmp"
            tree.write(tmp_file, pretty_print=True, xml_declaration=True, encoding='UTF-8')
            
            # Atomic rename
            os.rename(tmp_file, xml_file)
            return True
            
        except Exception as e:
            self.logger.error(f"Error writing XML: {str(e)}")
            # Clean up temp file if exists
            if os.path.exists(f"{xml_file}.tmp"):
                os.remove(f"{xml_file}.tmp")
            return False
    
    def _upload_to_sftp(self, xml_file: str) -> bool:
        """SFTP upload disabled - using manual download workflow"""
        self.logger.info("📋 SFTP auto-upload disabled - manual download workflow active")
        self.logger.info("💡 Use the web interface to download and manually upload XML when needed")
        return True  # Always return success for manual workflow
    
    def _verify_live_sync(self, expected_size):
        """Verify live URL has updated content"""
        import time
        import requests
        
        max_attempts = 5
        base_url = "https://myticas.com/myticas-job-feed-v2.xml"
        
        for attempt in range(max_attempts):
            try:
                # Try without cache-busting first
                response = requests.get(base_url, timeout=10)
                live_jobs = response.text.count('<job>')
                
                # Also check with timestamp to bypass cache
                cache_bust_url = f"{base_url}?nocache={int(time.time())}"
                cache_response = requests.get(cache_bust_url, timeout=10)
                cache_bust_jobs = cache_response.text.count('<job>')
                
                self.logger.info(f"Live verification attempt {attempt + 1}: {live_jobs} jobs (normal), {cache_bust_jobs} jobs (cache-bust)")
                
                # Check our local file for comparison
                with open('myticas-job-feed-v2.xml', 'r') as f:
                    local_content = f.read()
                    local_jobs = local_content.count('<job>')
                
                if live_jobs == local_jobs:
                    self.logger.info(f"✅ Live URL synchronized: {live_jobs} jobs match local")
                    return True
                elif attempt < max_attempts - 1:
                    wait_time = 30 * (attempt + 1)  # Progressive backoff
                    self.logger.warning(f"Live URL shows {live_jobs} jobs, expected {local_jobs}. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    
            except Exception as e:
                self.logger.error(f"Live verification error: {str(e)}")
                
        # Send alert if sync fails
        self.logger.error(f"⚠️ CDN SYNC ISSUE: Live site not updating after {max_attempts} attempts")
        self._send_cdn_sync_alert(local_jobs, live_jobs)
        return False
    
    def _send_cdn_sync_alert(self, local_jobs, live_jobs):
        """Send alert about CDN sync issues"""
        try:
            message = f"""CDN Synchronization Issue Detected:
            
            Local XML: {local_jobs} jobs
            Live URL: {live_jobs} jobs  
            
            The SFTP upload succeeded but the live website is not reflecting the changes.
            This may require manual CDN cache purging or waiting for cache expiration.
            
            Alternative solutions:
            1. Contact hosting provider to purge CDN cache for /myticas-job-feed-v2.xml
            2. Use versioned URLs (e.g., /myticas-job-feed-v2.xml?v=timestamp)
            3. Configure shorter cache TTL for XML files
            """
            
            # Log the issue for now - email notification can be added later
            self.logger.error(message)
            
            # Also save to a sync issues log file
            with open('cdn_sync_issues.log', 'a') as f:
                f.write(f"\n[{datetime.now()}] {message}\n")
                
        except Exception as e:
            self.logger.error(f"Failed to log CDN alert: {str(e)}")