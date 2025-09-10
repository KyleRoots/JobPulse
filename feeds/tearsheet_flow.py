"""
Tearsheet Flow Integration
Connects the new feed generator with the existing tearsheet monitoring system
"""

import os
import time
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from feeds.myticas_v2 import MyticasFeedV2
from bullhorn_service import BullhornService
from job_classification_service import JobClassificationService
import threading

class TearsheetFlow:
    """Manages tearsheet-driven XML feed rebuilds"""
    
    def __init__(self, db_session=None):
        self.logger = logging.getLogger(__name__)
        self.feed_generator = MyticasFeedV2()
        self.job_classifier = JobClassificationService()
        self.db_session = db_session
        self._rebuild_lock = threading.Lock()
        self._last_rebuild = None
        self._rebuild_debounce_seconds = 60  # Prevent rebuilds more than once per minute
        
    def should_rebuild(self) -> bool:
        """
        Check if rebuild should proceed based on freeze flag and debouncing
        
        Returns:
            bool: True if rebuild should proceed
        """
        # Check freeze flag
        if os.environ.get('XML_FEED_FRZ', '').lower() == 'true':
            self.logger.info("XML feed is frozen - skipping rebuild")
            return False
        
        # Check debounce timing
        if self._last_rebuild:
            elapsed = (datetime.now() - self._last_rebuild).total_seconds()
            if elapsed < self._rebuild_debounce_seconds:
                self.logger.info(f"Rebuild debounced - only {elapsed:.0f}s since last rebuild")
                return False
        
        return True
    
    def rebuild_from_tearsheets(self, tearsheet_configs: List[Dict]) -> Dict:
        """
        Rebuild XML feed from tearsheet data
        
        Args:
            tearsheet_configs: List of tearsheet configurations
            
        Returns:
            Dict: Results of the rebuild operation
        """
        with self._rebuild_lock:
            if not self.should_rebuild():
                return {'success': False, 'reason': 'Rebuild not allowed'}
            
            start_time = time.time()
            results = {
                'success': False,
                'jobs_processed': 0,
                'tearsheets_processed': 0,
                'errors': [],
                'xml_path': None,
                'uploaded': False
            }
            
            try:
                # Initialize Bullhorn service
                bullhorn = BullhornService(
                    client_id=os.environ.get('BULLHORN_CLIENT_ID'),
                    client_secret=os.environ.get('BULLHORN_CLIENT_SECRET'),
                    username=os.environ.get('BULLHORN_USERNAME'),
                    password=os.environ.get('BULLHORN_PASSWORD')
                )
                
                if not bullhorn.test_connection():
                    results['errors'].append("Failed to connect to Bullhorn")
                    return results
                
                # Collect all jobs from tearsheets
                all_jobs = []
                seen_job_ids = set()
                
                for config in tearsheet_configs:
                    try:
                        tearsheet_id = config.get('tearsheet_id')
                        tearsheet_name = config.get('name', f'Tearsheet {tearsheet_id}')
                        
                        self.logger.info(f"Fetching jobs from {tearsheet_name}")
                        
                        # Fetch jobs from tearsheet
                        if tearsheet_id == 0:
                            jobs = bullhorn.get_jobs_by_query(tearsheet_name)
                        else:
                            jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
                        
                        if jobs:
                            for job in jobs:
                                job_id = str(job.get('id'))
                                if job_id not in seen_job_ids:
                                    # Map Bullhorn data to XML format
                                    mapped_job = self._map_bullhorn_to_xml(job, config)
                                    
                                    # Add AI classification
                                    if not os.environ.get('SKIP_AI_CLASSIFICATION'):
                                        classification = self.job_classifier.classify_job(
                                            mapped_job.get('title', ''),
                                            mapped_job.get('description', '')
                                        )
                                        mapped_job['jobfunction'] = classification.get('job_function', '')
                                        mapped_job['jobindustries'] = classification.get('industries', '')
                                        mapped_job['senioritylevel'] = classification.get('seniority_level', '')
                                    
                                    all_jobs.append(mapped_job)
                                    seen_job_ids.add(job_id)
                            
                            results['tearsheets_processed'] += 1
                            self.logger.info(f"Processed {len(jobs)} jobs from {tearsheet_name}")
                        
                    except Exception as e:
                        error_msg = f"Error processing tearsheet {tearsheet_name}: {str(e)}"
                        self.logger.error(error_msg)
                        results['errors'].append(error_msg)
                
                # Sort jobs by bhatsid for deterministic output
                all_jobs.sort(key=lambda x: x.get('bhatsid', '0'))
                
                # Build XML feed
                xml_content = self.feed_generator.build_myticas_feed(all_jobs)
                results['jobs_processed'] = len(all_jobs)
                
                # Validate feed
                is_valid, validation_errors = self.feed_generator.validate_myticas_feed(xml_content)
                if not is_valid:
                    results['errors'].extend(validation_errors)
                    self.logger.error(f"Feed validation failed: {validation_errors}")
                    return results
                
                # Write to file
                output_path = 'myticas-job-feed-v2.xml'
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(xml_content)
                results['xml_path'] = output_path
                
                # Publish if not frozen
                if os.environ.get('XML_FEED_FRZ', '').lower() != 'true':
                    sftp_config = self._get_sftp_config()
                    if sftp_config:
                        results['uploaded'] = self.feed_generator.publish(xml_content, sftp_config)
                    else:
                        self.logger.warning("SFTP configuration not available")
                
                results['success'] = True
                self._last_rebuild = datetime.now()
                
                elapsed = time.time() - start_time
                self.logger.info(f"Feed rebuild completed in {elapsed:.2f}s - {results['jobs_processed']} jobs")
                
            except Exception as e:
                error_msg = f"Feed rebuild failed: {str(e)}"
                self.logger.error(error_msg)
                results['errors'].append(error_msg)
            
            return results
    
    def _map_bullhorn_to_xml(self, bullhorn_job: Dict, tearsheet_config: Dict) -> Dict:
        """
        Map Bullhorn job data to XML format
        
        Args:
            bullhorn_job: Raw job data from Bullhorn
            tearsheet_config: Tearsheet configuration
            
        Returns:
            Dict: Job data in XML format
        """
        import html
        import re
        import urllib.parse
        
        # Extract basic fields
        job_id = str(bullhorn_job.get('id', ''))
        title = html.unescape(bullhorn_job.get('title', 'Untitled Position'))
        
        # Clean title - remove forward slashes
        title = title.replace('/', ' ')
        
        # Extract clean title without existing job ID
        clean_title = re.sub(r'\s*\([A-Z]+-?\d+\)\s*', '', title)
        clean_title = re.sub(r'^Local to [A-Z]{2}[^,]*,\s*', '', clean_title)
        clean_title = re.sub(r'\s*\([^)]+\)\s*', ' ', clean_title).strip()
        
        # Determine company based on tearsheet
        company_name = tearsheet_config.get('company', 'Myticas Consulting')
        if 'STSI' in tearsheet_config.get('name', ''):
            company_name = 'STSI (Staffing Technical Services Inc.)'
        
        # Extract location
        address = bullhorn_job.get('address', {})
        city = address.get('city', '') if address else ''
        state = address.get('state', '') if address else ''
        country = address.get('countryName', 'United States') if address else 'United States'
        
        # Map employment type
        employment_type = bullhorn_job.get('employmentType', '')
        job_type = self._map_employment_type(employment_type)
        
        # Map remote type
        onsite_value = bullhorn_job.get('onSite', '')
        remote_type = self._map_remote_type(onsite_value)
        
        # Extract recruiter
        assignments = bullhorn_job.get('assignments', {})
        assigned_users = bullhorn_job.get('assignedUsers', {})
        response_user = bullhorn_job.get('responseUser', {})
        owner = bullhorn_job.get('owner', {})
        assigned_recruiter = self._extract_recruiter(assignments, assigned_users, response_user, owner)
        
        # Get description
        description = bullhorn_job.get('publicDescription', '') or bullhorn_job.get('description', '')
        
        # Format date
        date_added = bullhorn_job.get('dateAdded', '')
        if date_added:
            try:
                dt = datetime.fromtimestamp(date_added / 1000)
                formatted_date = dt.strftime('%Y-%m-%d')
            except:
                formatted_date = datetime.now().strftime('%Y-%m-%d')
        else:
            formatted_date = datetime.now().strftime('%Y-%m-%d')
        
        return {
            'title': clean_title,
            'bhatsid': job_id,
            'company': company_name,
            'date': formatted_date,
            'description': description,
            'jobtype': job_type,
            'city': city,
            'state': state,
            'country': country,
            'remotetype': remote_type,
            'assignedrecruiter': assigned_recruiter,
            'clean_title': clean_title
        }
    
    def _map_employment_type(self, employment_type: str) -> str:
        """Map Bullhorn employment type to XML job type"""
        mapping = {
            'Contract': 'Contract',
            'Contract-to-Hire': 'Contract to Hire',
            'Direct Hire': 'Direct Hire',
            'Full Time': 'Full-time',
            'Part Time': 'Part-time',
            'Temporary': 'Temporary'
        }
        return mapping.get(employment_type, 'Contract')
    
    def _map_remote_type(self, onsite_value: str) -> str:
        """Map Bullhorn onSite value to remote type"""
        if onsite_value == 'Remote':
            return 'Remote'
        elif onsite_value == 'On Site':
            return 'On-site'
        elif onsite_value == 'Hybrid':
            return 'Hybrid'
        return ''
    
    def _extract_recruiter(self, assignments, assigned_users, response_user, owner) -> str:
        """Extract recruiter name from various Bullhorn fields"""
        # Try assignments first
        if assignments and assignments.get('data'):
            for assignment in assignments['data']:
                if assignment.get('primaryRecruiter'):
                    return assignment.get('primaryRecruiter', {}).get('name', '')
        
        # Try assigned users
        if assigned_users and assigned_users.get('data'):
            for user in assigned_users['data']:
                return user.get('name', '')
        
        # Try response user
        if response_user and response_user.get('name'):
            return response_user['name']
        
        # Try owner
        if owner and owner.get('name'):
            return owner['name']
        
        return ''
    
    def _get_sftp_config(self) -> Optional[Dict]:
        """Get SFTP configuration from environment or database"""
        try:
            # Try environment variables first
            if os.environ.get('SFTP_HOST'):
                return {
                    'host': os.environ.get('SFTP_HOST'),
                    'username': os.environ.get('SFTP_USER', 'defaultuser'),
                    'password': os.environ.get('SFTP_PASSWORD'),
                    'key_file': os.environ.get('SFTP_KEY'),
                    'port': int(os.environ.get('SFTP_PORT', '22'))
                }
            
            # Try database if available
            if self.db_session:
                from models import GlobalSettings
                settings = {}
                for key in ['sftp_hostname', 'sftp_username', 'sftp_password', 'sftp_port']:
                    setting = self.db_session.query(GlobalSettings).filter_by(setting_key=key).first()
                    if setting:
                        settings[key] = setting.setting_value
                
                if settings.get('sftp_hostname'):
                    return {
                        'host': settings['sftp_hostname'],
                        'username': settings.get('sftp_username', 'defaultuser'),
                        'password': settings.get('sftp_password'),
                        'port': int(settings.get('sftp_port', '22'))
                    }
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting SFTP config: {str(e)}")
            return None