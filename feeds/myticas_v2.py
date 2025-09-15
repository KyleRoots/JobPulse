"""
Myticas Job Feed v2 Generator
Produces XML feed matching the live template at https://myticas.com/myticas-job-feed-v2.xml
"""

import os
import re
import logging
import urllib.parse
import html
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from lxml import etree
import hashlib

class MyticasFeedV2:
    """Generator for Myticas job feed v2 format"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.parser = etree.XMLParser(strip_cdata=False, recover=True)
        
    def build_myticas_feed(self, jobs_data: List[Dict]) -> str:
        """
        Build XML feed from Bullhorn job data
        
        Args:
            jobs_data: List of job dictionaries with Bullhorn data and AI fields
            
        Returns:
            str: XML string in Myticas v2 format
        """
        try:
            # Create root element
            root = etree.Element('source')
            
            # Add source metadata
            title_elem = etree.SubElement(root, 'title')
            title_elem.text = 'Myticas Consulting'
            
            link_elem = etree.SubElement(root, 'link')
            link_elem.text = 'https://www.myticas.com'
            
            # Process each job
            for job_data in jobs_data:
                job_elem = self._create_job_element(job_data)
                if job_elem is not None:
                    root.append(job_elem)
            
            # Convert to string with proper formatting
            xml_string = etree.tostring(
                root,
                pretty_print=True,
                xml_declaration=True,
                encoding='UTF-8'
            ).decode('utf-8')
            
            self.logger.info(f"Built XML feed with {len(jobs_data)} jobs")
            return xml_string
            
        except Exception as e:
            self.logger.error(f"Error building Myticas feed: {str(e)}")
            raise
    
    def _create_job_element(self, job_data: Dict) -> Optional[etree.Element]:
        """
        Create a single job XML element
        
        Args:
            job_data: Dictionary containing job information
            
        Returns:
            etree.Element: Job element or None if invalid
        """
        try:
            job = etree.Element('job')
            
            # Title with CDATA wrapping
            title_elem = etree.SubElement(job, 'title')
            title = job_data.get('title', '').strip()
            # Format title with job ID in parentheses
            job_id = job_data.get('bhatsid', job_data.get('id', ''))
            if job_id and not re.search(r'\(\d+\)$', title):
                title = f"{title} ({job_id})"
            title_elem.text = self._wrap_cdata(title)
            
            # Date
            date_elem = etree.SubElement(job, 'date')
            date_elem.text = job_data.get('date', datetime.now().strftime('%Y-%m-%d'))
            
            # Reference number (generated if not provided)
            ref_elem = etree.SubElement(job, 'referencenumber')
            ref_num = job_data.get('referencenumber', self._generate_reference_number())
            ref_elem.text = ref_num
            
            # Bullhorn ATS ID
            bhatsid_elem = etree.SubElement(job, 'bhatsid')
            bhatsid_elem.text = str(job_data.get('bhatsid', job_data.get('id', '')))
            
            # Company
            company_elem = etree.SubElement(job, 'company')
            company = job_data.get('company', 'Myticas Consulting').strip()
            company_elem.text = company
            
            # URL - Generate proper application URL
            url_elem = etree.SubElement(job, 'url')
            url = job_data.get('url', '')
            if not url:
                url = self._generate_job_url(
                    job_data.get('bhatsid', job_data.get('id', '')),
                    job_data.get('clean_title', title.split('(')[0].strip()),
                    company
                )
            url_elem.text = url
            
            # Description with CDATA wrapping
            desc_elem = etree.SubElement(job, 'description')
            description = job_data.get('description', '')
            desc_elem.text = self._wrap_cdata(description)
            
            # Job type
            jobtype_elem = etree.SubElement(job, 'jobtype')
            jobtype_elem.text = job_data.get('jobtype', 'Contract').strip()
            
            # Location fields (with whitespace trimming)
            city_elem = etree.SubElement(job, 'city')
            city_elem.text = job_data.get('city', '').strip()
            
            state_elem = etree.SubElement(job, 'state')
            state_elem.text = job_data.get('state', '').strip()
            
            country_elem = etree.SubElement(job, 'country')
            country_elem.text = job_data.get('country', 'United States').strip()
            
            # Category (empty as per template)
            category_elem = etree.SubElement(job, 'category')
            category_elem.text = ''
            
            # Apply email from config
            apply_email_elem = etree.SubElement(job, 'apply_email')
            apply_email = os.environ.get('APPLY_EMAIL', 'apply@myticas.com')
            apply_email_elem.text = apply_email
            
            # Remote type
            remotetype_elem = etree.SubElement(job, 'remotetype')
            remotetype_elem.text = job_data.get('remotetype', '').strip()
            
            # Assigned recruiter - preserve full LinkedIn tag format with names
            recruiter_elem = etree.SubElement(job, 'assignedrecruiter')
            raw_recruiter = job_data.get('assignedrecruiter', '').strip()
            
            # Use the full recruiter value as-is (includes LinkedIn tag with recruiter name)
            recruiter_elem.text = raw_recruiter
            
            # AI-generated fields
            jobfunction_elem = etree.SubElement(job, 'jobfunction')
            jobfunction_elem.text = job_data.get('jobfunction', '').strip()
            
            jobindustries_elem = etree.SubElement(job, 'jobindustries')
            jobindustries_elem.text = job_data.get('jobindustries', '').strip()
            
            seniority_elem = etree.SubElement(job, 'senioritylevel')
            seniority_elem.text = job_data.get('senioritylevel', '').strip()
            
            return job
            
        except Exception as e:
            self.logger.error(f"Error creating job element for job {job_data.get('id', 'unknown')}: {str(e)}")
            return None
    
    def _wrap_cdata(self, text: str) -> str:
        """
        Wrap text in CDATA tags if it contains special characters
        
        Args:
            text: Text to potentially wrap
            
        Returns:
            str: Text wrapped in CDATA or original text
        """
        if not text:
            return ''
        
        # Check if text needs CDATA wrapping
        if any(char in text for char in ['<', '>', '&', '"', "'"]):
            # Clean up any existing CDATA tags
            text = text.replace('<![CDATA[', '').replace(']]>', '')
            return f'<![CDATA[ {text} ]]>'
        
        return text
    
    def _generate_reference_number(self) -> str:
        """
        Generate a unique reference number
        
        Returns:
            str: 10-character reference number
        """
        import random
        import string
        chars = string.ascii_uppercase + string.digits
        return ''.join(random.choice(chars) for _ in range(10))
    
    def _generate_job_url(self, job_id: str, title: str, company: str) -> str:
        """
        Generate job application URL based on company
        
        Args:
            job_id: Bullhorn job ID
            title: Clean job title
            company: Company name
            
        Returns:
            str: Full application URL
        """
        # Determine base URL based on company (environment-independent, case-insensitive)
        if 'stsi' in company.lower():
            base_url = 'https://apply.stsigroup.com'
        else:
            base_url = 'https://apply.myticas.com'
        
        # Clean and encode title
        safe_title = title.replace('/', ' ').replace('\\', ' ')
        encoded_title = urllib.parse.quote(safe_title)
        
        # Generate URL with LinkedIn source parameter
        return f"{base_url}/{job_id}/{encoded_title}/?source=LinkedIn"
    
    def validate_myticas_feed(self, xml_content: str) -> Tuple[bool, List[str]]:
        """
        Validate XML feed against expected structure and rules
        
        Args:
            xml_content: XML string to validate
            
        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors = []
        
        try:
            # Parse XML
            root = etree.fromstring(xml_content.encode('utf-8'), self.parser)
            
            # Check root element
            if root.tag != 'source':
                errors.append("Root element must be 'source'")
            
            # Check source metadata
            title = root.find('title')
            if title is None or not title.text:
                errors.append("Source must have a title element")
            
            link = root.find('link')
            if link is None or not link.text:
                errors.append("Source must have a link element")
            
            # Required job fields
            required_fields = [
                'title', 'date', 'referencenumber', 'bhatsid', 'company',
                'url', 'description', 'jobtype', 'city', 'state', 'country',
                'category', 'apply_email', 'remotetype', 'assignedrecruiter',
                'jobfunction', 'jobindustries', 'senioritylevel'
            ]
            
            # Validate each job
            jobs = root.findall('job')
            if not jobs:
                errors.append("Feed must contain at least one job")
            
            for idx, job in enumerate(jobs):
                # Check all required fields exist
                for field in required_fields:
                    elem = job.find(field)
                    if elem is None:
                        errors.append(f"Job {idx + 1}: Missing required field '{field}'")
                
                # Validate specific field constraints
                bhatsid = job.find('bhatsid')
                if bhatsid is not None and bhatsid.text:
                    if not bhatsid.text.isdigit():
                        errors.append(f"Job {idx + 1}: bhatsid must be numeric")
                
                ref_num = job.find('referencenumber')
                if ref_num is not None and ref_num.text:
                    if len(ref_num.text) != 10:
                        errors.append(f"Job {idx + 1}: Reference number must be 10 characters")
                
                # Check URL format
                url = job.find('url')
                if url is not None and url.text:
                    if not url.text.startswith('https://'):
                        errors.append(f"Job {idx + 1}: URL must start with https://")
                    if '/?source=LinkedIn' not in url.text:
                        errors.append(f"Job {idx + 1}: URL must include '/?source=LinkedIn' parameter")
            
            # Check for deterministic output (consistent ordering)
            if len(jobs) > 1:
                job_ids = [job.find('bhatsid').text for job in jobs if job.find('bhatsid') is not None]
                if job_ids != sorted(job_ids):
                    errors.append("Jobs should be ordered by bhatsid for deterministic output")
            
        except etree.XMLSyntaxError as e:
            errors.append(f"XML syntax error: {str(e)}")
        except Exception as e:
            errors.append(f"Validation error: {str(e)}")
        
        is_valid = len(errors) == 0
        return is_valid, errors
    
    def publish(self, xml_content: str, sftp_config: Dict) -> bool:
        """
        Publish XML feed to SFTP server
        
        Args:
            xml_content: XML content to publish
            sftp_config: SFTP configuration dictionary
            
        Returns:
            bool: True if successful, False otherwise
        """
        # Check if frozen
        if os.environ.get('XML_FEED_FRZ', '').lower() == 'true':
            self.logger.warning("XML feed is frozen - skipping SFTP upload")
            return False
        
        try:
            import tempfile
            import paramiko
            
            # Write XML to temporary file - ensure UTF-8 encoding
            tmp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8')
            try:
                tmp_file.write(xml_content)
                tmp_file.flush()  # Ensure content is written to disk
                tmp_path = tmp_file.name
            finally:
                tmp_file.close()  # Explicitly close file before upload
            
            try:
                # Create SSH client
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                
                # Connect to SFTP server
                ssh.connect(
                    hostname=sftp_config['host'],
                    port=sftp_config.get('port', 22),
                    username=sftp_config['username'],
                    password=sftp_config.get('password'),
                    key_filename=sftp_config.get('key_file')
                )
                
                # Upload file
                sftp = ssh.open_sftp()
                remote_path = '/myticas-job-feed-v2.xml'  # Site root
                sftp.put(tmp_path, remote_path)
                sftp.close()
                ssh.close()
                
                self.logger.info(f"Successfully published XML feed to {sftp_config['host']}{remote_path}")
                return True
                
            finally:
                # Clean up temp file
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                
        except Exception as e:
            self.logger.error(f"Failed to publish XML feed: {str(e)}")
            return False
    
    def generate_checksum(self, xml_content: str) -> str:
        """
        Generate deterministic checksum for XML content
        
        Args:
            xml_content: XML string
            
        Returns:
            str: SHA256 checksum
        """
        return hashlib.sha256(xml_content.encode('utf-8')).hexdigest()