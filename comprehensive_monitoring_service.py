#!/usr/bin/env python3
"""
Comprehensive Monitoring Service
Performs full audit and synchronization between Bullhorn and XML every cycle
Implements 8-step process for complete data integrity
"""

import os
import re
import json
import logging
import time
import paramiko
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from xml_integration_service import XMLIntegrationService
from bullhorn_service import BullhornService
from email_service import EmailService

class ComprehensiveMonitoringService:
    """
    Complete monitoring service that ensures 100% accuracy between Bullhorn and XML
    Performs full audit on every cycle with all 8 required steps
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.xml_integration = XMLIntegrationService()
        self.bullhorn_service = None  # Will be initialized per cycle
        self.email_service = EmailService()
        
        # Track changes for batched email notifications (every 5 minutes)
        self.accumulated_changes = {
            'added': [],
            'removed': [],
            'modified': [],
            'field_corrections': [],
            'format_fixes': []
        }
        self.last_email_sent = datetime.now()
        self.email_interval_minutes = 5  # Send emails every 5 minutes
        
        # Field mapping from Bullhorn to XML (critical for audit)
        self.field_mappings = {
            'title': 'title',  # Clean title without job ID
            'publicDescription': 'description',
            'employmentType': 'jobtype',
            'address.city': 'city',
            'address.state': 'state',
            'address.countryName': 'country',
            'onSite': 'remotetype',
            'assignedUsers': 'assignedrecruiter',
            'id': 'bhatsid',
            'dateAdded': 'date'
        }
        
        # Track processing time
        self.cycle_start_time = None
        self.max_cycle_seconds = 110  # Leave buffer for 2-minute cycle
        
    def run_complete_monitoring_cycle(self, monitors: List, xml_file: str = 'myticas-job-feed.xml'):
        """
        Execute complete 8-step monitoring process
        Returns: Dict with cycle results
        """
        self.cycle_start_time = time.time()
        cycle_results = {
            'monitors_processed': 0,
            'jobs_added': 0,
            'jobs_removed': 0,
            'jobs_modified': 0,
            'fields_corrected': 0,
            'format_fixes': 0,
            'upload_success': False,
            'email_sent': False,
            'cycle_time': 0,
            'audit_passed': False
        }
        
        try:
            self.logger.info("=" * 60)
            self.logger.info("STARTING COMPREHENSIVE MONITORING CYCLE")
            self.logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.logger.info("=" * 60)
            
            # Initialize Bullhorn connection with credentials from environment
            if not self.bullhorn_service:
                self.bullhorn_service = BullhornService(
                    client_id=os.environ.get('BULLHORN_CLIENT_ID'),
                    client_secret=os.environ.get('BULLHORN_CLIENT_SECRET'),
                    username=os.environ.get('BULLHORN_USERNAME'),
                    password=os.environ.get('BULLHORN_PASSWORD')
                )
            
            if not self.bullhorn_service.test_connection():
                self.logger.error("Failed to connect to Bullhorn - aborting cycle")
                return cycle_results
            
            # STEP 1: Get ALL jobs from monitored tearsheets
            self.logger.info("\n📋 STEP 1: Fetching all jobs from monitored tearsheets...")
            bullhorn_jobs = {}  # job_id -> job_data
            tearsheet_jobs = {}  # tearsheet_id -> [job_ids]
            
            for monitor in monitors:
                if not monitor.is_active:
                    continue
                    
                self.logger.info(f"  Processing monitor: {monitor.name}")
                
                try:
                    # Get jobs from tearsheet
                    if monitor.tearsheet_id == 0:
                        jobs = self.bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                    else:
                        jobs = self.bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                    
                    if jobs:
                        tearsheet_jobs[monitor.tearsheet_id] = []
                        for job in jobs:
                            job_id = str(job.get('id'))
                            bullhorn_jobs[job_id] = job
                            tearsheet_jobs[monitor.tearsheet_id].append(job_id)
                            # Store monitor name for company mapping
                            job['_monitor_name'] = monitor.name
                        
                        self.logger.info(f"    Found {len(jobs)} jobs in {monitor.name}")
                        cycle_results['monitors_processed'] += 1
                    else:
                        self.logger.info(f"    No jobs found in {monitor.name}")
                        tearsheet_jobs[monitor.tearsheet_id] = []
                        
                except Exception as e:
                    self.logger.error(f"    Error fetching jobs from {monitor.name}: {str(e)}")
            
            self.logger.info(f"  Total unique jobs from all tearsheets: {len(bullhorn_jobs)}")
            
            # STEP 2 & 3: Load current XML and identify additions/removals
            self.logger.info("\n📄 STEP 2 & 3: Comparing with current XML...")
            current_xml_jobs = self._load_xml_jobs(xml_file)
            # Store initial XML snapshot for AI preservation during complete remapping
            self.initial_xml_snapshot = current_xml_jobs.copy()
            self.logger.info(f"📸 CAPTURED XML SNAPSHOT: {len(self.initial_xml_snapshot)} jobs for AI preservation")
            current_xml_ids = set(current_xml_jobs.keys())
            bullhorn_ids = set(bullhorn_jobs.keys())
            
            jobs_to_add = bullhorn_ids - current_xml_ids
            jobs_to_remove = current_xml_ids - bullhorn_ids
            jobs_to_check = bullhorn_ids & current_xml_ids
            
            self.logger.info(f"  Jobs to add: {len(jobs_to_add)}")
            self.logger.info(f"  Jobs to remove: {len(jobs_to_remove)}")
            self.logger.info(f"  Jobs to check for modifications: {len(jobs_to_check)}")
            
            # Track all changes for this cycle
            cycle_changes = {
                'added': [],
                'removed': [],
                'modified': [],
                'field_corrections': []
            }
            
            # STEP 2: Add new jobs
            if jobs_to_add:
                self.logger.info("\n➕ Adding new jobs to XML...")
                for job_id in jobs_to_add:
                    job_data = bullhorn_jobs[job_id]
                    self._add_job_to_xml(xml_file, job_data)
                    cycle_changes['added'].append({
                        'id': job_id,
                        'title': job_data.get('title', 'Unknown'),
                        'company': job_data.get('_monitor_name', 'Myticas Consulting')
                    })
                    cycle_results['jobs_added'] += 1
                    self.logger.info(f"    Added job {job_id}: {job_data.get('title')}")
            
            # STEP 3: Remove obsolete jobs
            if jobs_to_remove:
                self.logger.info("\n➖ Removing obsolete jobs from XML...")
                for job_id in jobs_to_remove:
                    job_info = current_xml_jobs.get(job_id, {})
                    self._remove_job_from_xml(xml_file, job_id)
                    cycle_changes['removed'].append({
                        'id': job_id,
                        'title': job_info.get('title', f'Job {job_id}')
                    })
                    cycle_results['jobs_removed'] += 1
                    self.logger.info(f"    Removed job {job_id}: {job_info.get('title')}")
            
            # STEP 4: Monitor field modifications
            self.logger.info("\n🔍 STEP 4: Checking for field modifications...")
            for job_id in jobs_to_check:
                bullhorn_job = bullhorn_jobs[job_id]
                xml_job = current_xml_jobs[job_id]
                
                modifications = self._check_field_modifications(bullhorn_job, xml_job)
                if modifications:
                    self._update_job_in_xml(xml_file, job_id, bullhorn_job, modifications)
                    cycle_changes['modified'].append({
                        'id': job_id,
                        'title': bullhorn_job.get('title', 'Unknown'),
                        'changes': modifications
                    })
                    cycle_results['jobs_modified'] += 1
                    self.logger.info(f"    Modified job {job_id}: {len(modifications)} field changes")
            
            # STEP 7: Review and fix CDATA/HTML formatting
            self.logger.info("\n🔧 STEP 7: Reviewing CDATA and HTML formatting...")
            format_fixes = self._fix_xml_formatting(xml_file)
            cycle_results['format_fixes'] = format_fixes
            if format_fixes > 0:
                self.logger.info(f"    Fixed formatting in {format_fixes} locations")
            
            # STEP 8: FULL AUDIT - Verify ALL mapped fields
            self.logger.info("\n✅ STEP 8: Running FULL AUDIT on all mapped fields...")
            audit_results = self._run_full_audit(xml_file, bullhorn_jobs)
            cycle_results['fields_corrected'] = audit_results['corrections_made']
            cycle_results['audit_passed'] = audit_results['passed']
            
            if audit_results['corrections_made'] > 0:
                self.logger.info(f"    Made {audit_results['corrections_made']} field corrections during audit")
                cycle_changes['field_corrections'] = audit_results['corrections']
            
            if audit_results['passed']:
                self.logger.info("    ✅ AUDIT PASSED: All fields are 100% accurate")
            else:
                self.logger.warning("    ⚠️ AUDIT ISSUES: Some discrepancies could not be resolved")
            
            # STEP 5: Upload to web server
            self.logger.info("\n📤 STEP 5: Uploading to web server...")
            upload_success = self._upload_to_sftp(xml_file)
            cycle_results['upload_success'] = upload_success
            
            if upload_success:
                self.logger.info("    ✅ Successfully uploaded XML to production")
                # Also upload scheduled version
                self._upload_to_sftp('myticas-job-feed-scheduled.xml')
            else:
                self.logger.error("    ❌ Failed to upload XML to production")
            
            # Accumulate changes for batched email
            self._accumulate_changes(cycle_changes)
            
            # STEP 6: Send email notifications (every 5 minutes)
            if self._should_send_email():
                self.logger.info("\n📧 STEP 6: Sending email notification...")
                email_sent = self._send_accumulated_email()
                cycle_results['email_sent'] = email_sent
                if email_sent:
                    self.logger.info("    ✅ Email notification sent")
                else:
                    self.logger.warning("    ⚠️ Email notification failed")
            else:
                time_until_email = 5 - ((datetime.now() - self.last_email_sent).total_seconds() / 60)
                self.logger.info(f"\n📧 STEP 6: Email scheduled in {time_until_email:.1f} minutes")
            
            # Calculate cycle time
            cycle_time = time.time() - self.cycle_start_time
            cycle_results['cycle_time'] = cycle_time
            
            self.logger.info("\n" + "=" * 60)
            self.logger.info("MONITORING CYCLE COMPLETE")
            self.logger.info(f"Time taken: {cycle_time:.2f} seconds")
            self.logger.info(f"Results: {cycle_results['jobs_added']} added, {cycle_results['jobs_removed']} removed, {cycle_results['jobs_modified']} modified")
            self.logger.info("=" * 60)
            
            # Check if we're taking too long
            if cycle_time > 100:
                self.logger.warning(f"⚠️ Cycle took {cycle_time:.2f}s - may need to adjust monitoring interval")
            
        except Exception as e:
            self.logger.error(f"Critical error in monitoring cycle: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
        
        return cycle_results
    
    def _load_xml_jobs(self, xml_file: str) -> Dict:
        """Load current jobs from XML file"""
        jobs = {}
        try:
            if not os.path.exists(xml_file):
                return jobs
            
            with open(xml_file, 'r') as f:
                content = f.read()
            
            # Extract jobs using regex
            job_pattern = r'<job>(.*?)</job>'
            job_matches = re.findall(job_pattern, content, re.DOTALL)
            
            for job_content in job_matches:
                # Extract job ID
                bhatsid_match = re.search(r'<bhatsid>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</bhatsid>', job_content)
                if bhatsid_match:
                    job_id = bhatsid_match.group(1).strip()
                    
                    # Extract other fields
                    job_data = {}
                    
                    # Extract title
                    title_match = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', job_content)
                    if title_match:
                        job_data['title'] = title_match.group(1).strip()
                    
                    # Extract reference number
                    ref_match = re.search(r'<referencenumber>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</referencenumber>', job_content)
                    if ref_match:
                        job_data['referencenumber'] = ref_match.group(1).strip()
                    
                    # Extract AI classification fields for preservation
                    jobfunction_match = re.search(r'<jobfunction>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</jobfunction>', job_content)
                    if jobfunction_match:
                        job_data['jobfunction'] = jobfunction_match.group(1).strip()
                    
                    jobindustries_match = re.search(r'<jobindustries>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</jobindustries>', job_content)
                    if jobindustries_match:
                        job_data['jobindustries'] = jobindustries_match.group(1).strip()
                    
                    senioritylevel_match = re.search(r'<senoritylevel>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</senoritylevel>', job_content)
                    if senioritylevel_match:
                        job_data['senioritylevel'] = senioritylevel_match.group(1).strip()
                    
                    # Store the complete job content for comparison
                    job_data['_xml_content'] = job_content
                    
                    jobs[job_id] = job_data
            
            self.logger.info(f"Loaded {len(jobs)} jobs from {xml_file}")
            
        except Exception as e:
            self.logger.error(f"Error loading XML jobs: {str(e)}")
        
        return jobs
    
    def _check_field_modifications(self, bullhorn_job: Dict, xml_job: Dict) -> List[Dict]:
        """Check for field modifications between Bullhorn and XML"""
        modifications = []
        
        # Map and compare each field
        for bullhorn_field, xml_field in self.field_mappings.items():
            bullhorn_value = self._get_nested_value(bullhorn_job, bullhorn_field)
            
            # Special handling for different fields
            if xml_field == 'title':
                # Remove job ID from title
                bullhorn_value = re.sub(r'\s*\(\d{5}\)\s*', '', str(bullhorn_value))
            elif xml_field == 'jobtype':
                bullhorn_value = self._map_employment_type(bullhorn_value)
            elif xml_field == 'remotetype':
                bullhorn_value = self._map_remote_type(bullhorn_value)
            elif xml_field == 'date':
                bullhorn_value = self._format_date(bullhorn_value)
            elif xml_field == 'assignedrecruiter':
                # Extract recruiter from assignedUsers
                assigned_users = bullhorn_job.get('assignedUsers', {})
                if assigned_users and assigned_users.get('data'):
                    user_data = assigned_users['data'][0] if assigned_users['data'] else {}
                    first_name = user_data.get('firstName', '')
                    last_name = user_data.get('lastName', '')
                    if first_name and last_name:
                        initials = f"{first_name[0]}{last_name[0]}"
                        bullhorn_value = f"#LI-{initials}1: {first_name} {last_name}"
                    else:
                        bullhorn_value = ''
                else:
                    bullhorn_value = ''
            
            # Get current XML value from job content
            xml_content = xml_job.get('_xml_content', '')
            xml_pattern = f'<{xml_field}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{xml_field}>'
            xml_match = re.search(xml_pattern, xml_content)
            current_xml_value = xml_match.group(1).strip() if xml_match else ''
            
            # Compare values (normalize for comparison)
            bullhorn_str = str(bullhorn_value).strip() if bullhorn_value else ''
            xml_str = current_xml_value.strip()
            
            if bullhorn_str != xml_str and xml_field != 'referencenumber':  # Don't change reference numbers
                modifications.append({
                    'field': xml_field,
                    'old_value': xml_str,
                    'new_value': bullhorn_str
                })
        
        return modifications
    
    def _run_full_audit(self, xml_file: str, bullhorn_jobs: Dict) -> Dict:
        """
        Run comprehensive audit on ALL mapped fields
        Ensures 100% accuracy between Bullhorn and XML
        """
        audit_results = {
            'passed': True,
            'corrections_made': 0,
            'corrections': [],
            'issues': []
        }
        
        try:
            # Reload XML to get latest state
            current_xml_jobs = self._load_xml_jobs(xml_file)
            
            # Check every job that should be in XML
            for job_id, bullhorn_job in bullhorn_jobs.items():
                if job_id not in current_xml_jobs:
                    audit_results['issues'].append(f"Job {job_id} missing from XML after add operation")
                    audit_results['passed'] = False
                    continue
                
                # Verify every mapped field
                xml_job = current_xml_jobs[job_id]
                discrepancies = self._check_field_modifications(bullhorn_job, xml_job)
                
                if discrepancies:
                    # Attempt to correct the discrepancies
                    self.logger.warning(f"Audit found {len(discrepancies)} discrepancies in job {job_id}")
                    for discrepancy in discrepancies:
                        self.logger.warning(f"  - {discrepancy['field']}: '{discrepancy['old_value']}' → '{discrepancy['new_value']}'")
                    
                    # Fix the discrepancies
                    self._update_job_in_xml(xml_file, job_id, bullhorn_job, discrepancies)
                    audit_results['corrections_made'] += len(discrepancies)
                    audit_results['corrections'].append({
                        'job_id': job_id,
                        'title': bullhorn_job.get('title', 'Unknown'),
                        'discrepancies': discrepancies
                    })
            
            # Verify no extra jobs in XML
            xml_ids = set(current_xml_jobs.keys())
            bullhorn_ids = set(bullhorn_jobs.keys())
            extra_jobs = xml_ids - bullhorn_ids
            
            if extra_jobs:
                self.logger.warning(f"Audit found {len(extra_jobs)} extra jobs in XML")
                for job_id in extra_jobs:
                    self._remove_job_from_xml(xml_file, job_id)
                    audit_results['corrections_made'] += 1
                    audit_results['corrections'].append({
                        'job_id': job_id,
                        'action': 'removed_extra_job'
                    })
            
            # Final verification
            if audit_results['corrections_made'] == 0 and not audit_results['issues']:
                audit_results['passed'] = True
            else:
                audit_results['passed'] = False
                
        except Exception as e:
            self.logger.error(f"Error during audit: {str(e)}")
            audit_results['passed'] = False
            audit_results['issues'].append(str(e))
        
        return audit_results
    
    def _add_job_to_xml(self, xml_file: str, job_data: Dict):
        """Add a new job to XML file with AI field preservation"""
        try:
            # Check if this job previously existed (for static AI preservation during remapping)
            job_id = str(job_data.get('id', ''))
            existing_ai_fields = None
            
            # Try to get existing AI fields from initial XML snapshot (before any removals)
            try:
                # Use the initial snapshot if available, otherwise load current XML
                existing_xml_jobs = getattr(self, 'initial_xml_snapshot', {}) or self._load_xml_jobs(xml_file)
                self.logger.info(f"🔍 AI PRESERVATION CHECK for job {job_id}: snapshot has {len(existing_xml_jobs)} jobs")
                if job_id in existing_xml_jobs:
                    existing_job = existing_xml_jobs[job_id]
                    existing_ai_fields = {
                        'jobfunction': existing_job.get('jobfunction', ''),
                        'jobindustries': existing_job.get('jobindustries', ''),
                        'senioritylevel': existing_job.get('senioritylevel', '')
                    }
                    # Only use if all AI fields are present (complete AI classification)
                    if all(existing_ai_fields.values()):
                        self.logger.info(f"✅ PRESERVING existing AI classification for job {job_id} during re-add: {existing_ai_fields}")
                    else:
                        existing_ai_fields = None
                        self.logger.info(f"⚠️ Missing AI fields for job {job_id}, will generate new: {existing_ai_fields}")
            except Exception as e:
                self.logger.warning(f"Could not retrieve existing AI fields for job {job_id}: {str(e)}")
                existing_ai_fields = None
            
            # Map job data to XML format
            monitor_name = job_data.get('_monitor_name')
            xml_job = self.xml_integration.map_bullhorn_job_to_xml(
                job_data, 
                monitor_name=monitor_name,
                existing_ai_fields=existing_ai_fields
            )
            
            # Add to XML file
            self.xml_integration.add_job_to_xml(xml_file, xml_job)
            
        except Exception as e:
            self.logger.error(f"Error adding job {job_data.get('id')} to XML: {str(e)}")
    
    def _remove_job_from_xml(self, xml_file: str, job_id: str):
        """Remove a job from XML file"""
        try:
            self.xml_integration.remove_job_from_xml(xml_file, job_id)
        except Exception as e:
            self.logger.error(f"Error removing job {job_id} from XML: {str(e)}")
    
    def _update_job_in_xml(self, xml_file: str, job_id: str, job_data: Dict, modifications: List[Dict]):
        """Update job fields in XML file"""
        try:
            # CRITICAL FIX: Use initial XML snapshot for AI preservation (before any removals)
            existing_xml_jobs = getattr(self, 'initial_xml_snapshot', {}) or self._load_xml_jobs(xml_file)
            existing_job = existing_xml_jobs.get(job_id, {})
            self.logger.info(f"🔍 AI PRESERVATION CHECK for job {job_id} during UPDATE: snapshot has {len(existing_xml_jobs)} jobs")
            
            # Extract existing AI classification fields for preservation
            existing_ai_fields = {
                'jobfunction': existing_job.get('jobfunction', ''),
                'jobindustries': existing_job.get('jobindustries', ''),
                'senioritylevel': existing_job.get('senioritylevel', '')
            }
            
            # Only use if all AI fields are present (complete AI classification)
            if job_id in existing_xml_jobs and all(existing_ai_fields.values()):
                self.logger.info(f"✅ PRESERVING existing AI classification for job {job_id} during UPDATE: {existing_ai_fields}")
            else:
                existing_ai_fields = None
                self.logger.info(f"⚠️ Missing/incomplete AI fields for job {job_id}, will generate new: {existing_ai_fields}")
            
            # Map updated job data while preserving AI classification fields
            monitor_name = job_data.get('_monitor_name')
            xml_job = self.xml_integration.map_bullhorn_job_to_xml(
                job_data,
                monitor_name=monitor_name,
                existing_ai_fields=existing_ai_fields
            )
            
            # Update in XML file
            self.xml_integration.update_job_in_xml(xml_file, job_id, xml_job)
            
        except Exception as e:
            self.logger.error(f"Error updating job {job_id} in XML: {str(e)}")
    
    def _fix_xml_formatting(self, xml_file: str) -> int:
        """Fix CDATA and HTML formatting in XML file"""
        fixes_made = 0
        
        try:
            with open(xml_file, 'r') as f:
                content = f.read()
            
            original_content = content
            
            # Ensure all text fields have CDATA
            fields_needing_cdata = [
                'title', 'company', 'date', 'referencenumber', 'bhatsid', 'url',
                'description', 'jobtype', 'city', 'state', 'country', 'category',
                'apply_email', 'remotetype', 'assignedrecruiter', 'jobfunction',
                'jobindustries', 'senoritylevel'
            ]
            
            for field in fields_needing_cdata:
                # Find fields without CDATA
                pattern = f'<{field}>([^<]*)</{field}>'
                
                def add_cdata(match):
                    nonlocal fixes_made
                    content = match.group(1)
                    if not content.startswith('<![CDATA['):
                        fixes_made += 1
                        return f'<{field}><![CDATA[{content}]]></{field}>'
                    return match.group(0)
                
                content = re.sub(pattern, add_cdata, content)
            
            # Fix any double-encoded HTML entities
            content = content.replace('&amp;lt;', '<')
            content = content.replace('&amp;gt;', '>')
            content = content.replace('&amp;amp;', '&')
            
            if content != original_content:
                with open(xml_file, 'w') as f:
                    f.write(content)
                    
        except Exception as e:
            self.logger.error(f"Error fixing XML formatting: {str(e)}")
        
        return fixes_made
    
    def _upload_to_sftp(self, xml_file: str) -> bool:
        """Upload XML file to SFTP server"""
        try:
            hostname = os.environ.get('SFTP_HOST')
            username = os.environ.get('SFTP_USERNAME')
            password = os.environ.get('SFTP_PASSWORD')
            
            if not all([hostname, username, password]):
                self.logger.error("SFTP credentials not configured")
                return False
            
            transport = paramiko.Transport((hostname, 2222))
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            sftp.put(xml_file, xml_file)
            
            sftp.close()
            transport.close()
            
            return True
            
        except Exception as e:
            self.logger.error(f"SFTP upload error: {str(e)}")
            return False
    
    def _accumulate_changes(self, cycle_changes: Dict):
        """Accumulate changes for batched email notification"""
        for change_type in ['added', 'removed', 'modified', 'field_corrections']:
            if cycle_changes.get(change_type):
                self.accumulated_changes[change_type].extend(cycle_changes[change_type])
    
    def _should_send_email(self) -> bool:
        """Check if it's time to send email (every 5 minutes)"""
        time_since_last = (datetime.now() - self.last_email_sent).total_seconds() / 60
        has_changes = any(self.accumulated_changes[k] for k in self.accumulated_changes)
        return time_since_last >= self.email_interval_minutes and has_changes
    
    def _send_accumulated_email(self) -> bool:
        """Send email with accumulated changes"""
        try:
            if not any(self.accumulated_changes[k] for k in self.accumulated_changes):
                return False
            
            # Prepare email content
            subject = f"Job Feed Monitor - {datetime.now().strftime('%H:%M')} Summary"
            
            body = f"""
            <h2>Job Feed Monitoring Summary</h2>
            <p>Period: Last {self.email_interval_minutes} minutes</p>
            """
            
            if self.accumulated_changes['added']:
                body += f"<h3>✅ {len(self.accumulated_changes['added'])} Jobs Added</h3><ul>"
                for job in self.accumulated_changes['added'][:10]:  # Limit to 10
                    body += f"<li>{job['title']} (ID: {job['id']})</li>"
                if len(self.accumulated_changes['added']) > 10:
                    body += f"<li>...and {len(self.accumulated_changes['added']) - 10} more</li>"
                body += "</ul>"
            
            if self.accumulated_changes['removed']:
                body += f"<h3>❌ {len(self.accumulated_changes['removed'])} Jobs Removed</h3><ul>"
                for job in self.accumulated_changes['removed'][:10]:
                    body += f"<li>{job['title']} (ID: {job['id']})</li>"
                if len(self.accumulated_changes['removed']) > 10:
                    body += f"<li>...and {len(self.accumulated_changes['removed']) - 10} more</li>"
                body += "</ul>"
            
            if self.accumulated_changes['modified']:
                body += f"<h3>📝 {len(self.accumulated_changes['modified'])} Jobs Modified</h3><ul>"
                for job in self.accumulated_changes['modified'][:10]:
                    body += f"<li>{job['title']} - {len(job['changes'])} fields changed</li>"
                if len(self.accumulated_changes['modified']) > 10:
                    body += f"<li>...and {len(self.accumulated_changes['modified']) - 10} more</li>"
                body += "</ul>"
            
            if self.accumulated_changes['field_corrections']:
                body += f"<h3>🔧 {len(self.accumulated_changes['field_corrections'])} Audit Corrections</h3>"
                body += "<p>Fields were automatically corrected to ensure accuracy.</p>"
            
            body += f"""
            <hr>
            <p><small>Generated by Comprehensive Monitoring Service at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</small></p>
            """
            
            # Send email
            success = self.email_service.send_notification(
                subject=subject,
                body=body,
                to_email=os.environ.get('NOTIFICATION_EMAIL', 'team@myticas.com')
            )
            
            if success:
                # Clear accumulated changes
                self.accumulated_changes = {
                    'added': [],
                    'removed': [],
                    'modified': [],
                    'field_corrections': [],
                    'format_fixes': []
                }
                self.last_email_sent = datetime.now()
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error sending email: {str(e)}")
            return False
    
    def _get_nested_value(self, data: Dict, path: str):
        """Get nested value from dictionary using dot notation"""
        keys = path.split('.')
        value = data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value
    
    def _map_employment_type(self, employment_type: str) -> str:
        """Map Bullhorn employment type to XML jobtype"""
        mapping = {
            'Full-Time': 'Direct Hire',
            'Contract': 'Contract',
            'Contract-to-Hire': 'Contract to Hire',
            'Part-Time': 'Part-Time',
            'Temporary': 'Contract'
        }
        return mapping.get(employment_type, 'Contract')
    
    def _map_remote_type(self, onsite_value: str) -> str:
        """Map Bullhorn onSite to XML remotetype"""
        mapping = {
            'On-Site': 'Onsite',
            'Remote': 'Remote',
            'Hybrid': 'Hybrid',
            'No Preference': 'No Preference'
        }
        return mapping.get(onsite_value, 'No Preference')
    
    def _format_date(self, timestamp) -> str:
        """Format timestamp to readable date"""
        try:
            if timestamp:
                dt = datetime.fromtimestamp(int(timestamp) / 1000)
                return dt.strftime('%B %d, %Y')
        except:
            pass
        return datetime.now().strftime('%B %d, %Y')


def main():
    """Test the comprehensive monitoring service"""
    logging.basicConfig(level=logging.INFO)
    
    service = ComprehensiveMonitoringService()
    
    # Mock monitors for testing
    class MockMonitor:
        def __init__(self, name, tearsheet_id):
            self.name = name
            self.tearsheet_id = tearsheet_id
            self.is_active = True
            self.tearsheet_name = name
    
    monitors = [
        MockMonitor("Ottawa Sponsored Jobs", 1256),
        MockMonitor("Clover Sponsored Jobs", 1499),
        MockMonitor("VMS Sponsored Jobs", 1264),
        MockMonitor("Cleveland Sponsored Jobs", 1258),
        MockMonitor("Chicago Sponsored Jobs", 1257)
    ]
    
    results = service.run_complete_monitoring_cycle(monitors)
    print(f"\nCycle Results: {results}")


if __name__ == "__main__":
    main()