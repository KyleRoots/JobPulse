#!/usr/bin/env python3
"""
EMERGENCY FIX - Restore all jobs to XML using existing monitoring infrastructure
"""

import os
import sys
import hashlib
from datetime import datetime
from lxml import etree
from app import app, db, GlobalSettings
from models import BullhornMonitor
from ftp_service import FTPService
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generate_reference_number(job_id, job_title, date_added):
    """Generate a consistent reference number for a job"""
    unique_string = f"{job_id}-{job_title}-{date_added}"
    hash_object = hashlib.md5(unique_string.encode())
    hash_hex = hash_object.hexdigest()
    reference = f"{hash_hex[:4].upper()}{job_id}{hash_hex[-4:].upper()}"
    return reference

def emergency_rebuild():
    """Emergency rebuild using existing monitor infrastructure"""
    logger.info("="*60)
    logger.info("EMERGENCY XML REBUILD - Using existing monitors")
    logger.info("="*60)
    
    with app.app_context():
        # Get all monitors to fetch jobs
        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        
        all_jobs = []
        job_count_by_monitor = {}
        
        for monitor in monitors:
            logger.info(f"\nProcessing {monitor.monitor_name}...")
            try:
                # Force the monitor to process and get fresh data
                monitor.process()
                
                # Get the jobs from the monitor's snapshot
                if hasattr(monitor, '_current_jobs'):
                    jobs = monitor._current_jobs
                    job_count = len(jobs) if jobs else 0
                    job_count_by_monitor[monitor.monitor_name] = job_count
                    
                    if jobs:
                        all_jobs.extend(jobs)
                        logger.info(f"  ✓ Found {job_count} jobs")
                else:
                    job_count_by_monitor[monitor.monitor_name] = 0
                    logger.info(f"  ✓ No jobs found")
                    
            except Exception as e:
                logger.error(f"  ✗ Error: {str(e)}")
                job_count_by_monitor[monitor.monitor_name] = 0
        
        logger.info(f"\n📊 TOTAL JOBS COLLECTED: {len(all_jobs)}")
        for name, count in job_count_by_monitor.items():
            logger.info(f"  • {name}: {count}")
        
        # Build XML structure
        root = etree.Element("source")
        
        # Sort jobs by dateAdded (newest first)
        all_jobs.sort(key=lambda x: x.get('dateAdded', 0), reverse=True)
        
        # Add each job to XML
        jobs_added = 0
        for job_data in all_jobs:
            try:
                job_elem = etree.SubElement(root, "job")
                
                # Generate reference number
                job_id = str(job_data.get('id', ''))
                job_title = job_data.get('title', 'Unknown')
                date_added = job_data.get('dateAdded', 0)
                reference_number = generate_reference_number(job_id, job_title, date_added)
                
                # Core required fields
                etree.SubElement(job_elem, "title").text = etree.CDATA(job_title or "No Title")
                etree.SubElement(job_elem, "company").text = etree.CDATA(
                    job_data.get('clientCorporation', {}).get('name') if isinstance(job_data.get('clientCorporation'), dict) 
                    else "Myticas Consulting"
                )
                
                # Date field
                date_value = job_data.get('dateAdded', 0)
                if date_value:
                    try:
                        date_obj = datetime.fromtimestamp(date_value / 1000)
                        etree.SubElement(job_elem, "date").text = etree.CDATA(date_obj.strftime('%Y-%m-%d'))
                    except:
                        etree.SubElement(job_elem, "date").text = etree.CDATA(datetime.now().strftime('%Y-%m-%d'))
                else:
                    etree.SubElement(job_elem, "date").text = etree.CDATA(datetime.now().strftime('%Y-%m-%d'))
                
                # Reference number
                etree.SubElement(job_elem, "referencenumber").text = etree.CDATA(reference_number)
                
                # URL
                etree.SubElement(job_elem, "url").text = etree.CDATA(f"https://apply.myticas.com/?jobId={job_id}")
                
                # Description
                description = job_data.get('publicDescription') or job_data.get('description') or ""
                if description and not description.strip().startswith('<'):
                    description = f"<p>{description}</p>"
                etree.SubElement(job_elem, "description").text = etree.CDATA(description)
                
                # Location fields
                address = job_data.get('address', {}) or {}
                city = address.get('city', 'Remote')
                state = address.get('state', '')
                country = address.get('countryID') or address.get('country') or 'United States'
                if country == '1':
                    country = 'United States'
                
                etree.SubElement(job_elem, "city").text = etree.CDATA(city)
                etree.SubElement(job_elem, "state").text = etree.CDATA(state)
                etree.SubElement(job_elem, "country").text = etree.CDATA(country)
                etree.SubElement(job_elem, "postalcode").text = etree.CDATA(str(address.get('zip', '')))
                
                # Additional fields
                etree.SubElement(job_elem, "jobtype").text = etree.CDATA(job_data.get('employmentType', 'Full-time'))
                etree.SubElement(job_elem, "experience").text = etree.CDATA(job_data.get('yearsRequired', '0'))
                etree.SubElement(job_elem, "salary").text = etree.CDATA(str(job_data.get('salary', '')))
                etree.SubElement(job_elem, "education").text = etree.CDATA(job_data.get('educationDegree', ''))
                
                # Category
                category = job_data.get('customText5', 'Other')
                etree.SubElement(job_elem, "category").text = etree.CDATA(category)
                
                # Remote type
                is_remote = job_data.get('customText3', '').lower() == 'yes'
                remote_type = 'Remote' if is_remote else 'Onsite'
                etree.SubElement(job_elem, "remotetype").text = etree.CDATA(remote_type)
                
                # Recruiter tag with '1' suffix
                assigned_users = job_data.get('assignedUsers', {})
                if assigned_users and hasattr(assigned_users, 'data') and assigned_users.data:
                    first_user = assigned_users.data[0]
                    first_name = first_user.get('firstName', '')
                    last_name = first_user.get('lastName', '')
                    
                    tag_mapping = {
                        'Rob': '#LI-RS1:',
                        'Bob': '#LI-BS1:',
                        'Andrea': '#LI-AG1:',
                        'Doug': '#LI-DSC1:',
                        'Dilan': '#LI-DC1:',
                        'Jason': '#LI-JC1:',
                        'Tarun': '#LI-TS1:',
                        'Cody': '#LI-CG1:',
                        'Luke': '#LI-LB1:',
                        'Olivia': '#LI-OW1:',
                        'Sofia': '#LI-SO1:'
                    }
                    
                    recruiter_tag = tag_mapping.get(first_name, f'#LI-{first_name[:1]}{last_name[:1]}1:')
                    etree.SubElement(job_elem, "recruitertag").text = etree.CDATA(recruiter_tag)
                
                jobs_added += 1
                
            except Exception as e:
                logger.error(f"Error adding job {job_data.get('id')}: {str(e)}")
                continue
        
        logger.info(f"\n✅ Successfully built XML with {jobs_added} jobs")
        
        # Write to file
        xml_string = etree.tostring(root, pretty_print=True, xml_declaration=True, encoding='UTF-8')
        
        # Save to both files
        for filename in ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']:
            with open(filename, 'wb') as f:
                f.write(xml_string)
            logger.info(f"  ✓ Saved to {filename}")
        
        # Upload to SFTP
        try:
            sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
            sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
            sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
            
            if sftp_hostname and sftp_username and sftp_password:
                ftp = FTPService(
                    hostname=sftp_hostname.setting_value,
                    username=sftp_username.setting_value,
                    password=sftp_password.setting_value,
                    port=2222,
                    use_sftp=True
                )
                
                if ftp.upload_file('myticas-job-feed.xml', 'myticas-job-feed.xml'):
                    logger.info(f"\n🚀 UPLOADED TO SFTP: {jobs_added} jobs now live")
                    logger.info(f"📍 URL: https://myticas.com/myticas-job-feed.xml")
                    
        except Exception as e:
            logger.error(f"Error uploading to SFTP: {str(e)}")
        
        return jobs_added

if __name__ == "__main__":
    jobs_count = emergency_rebuild()
    print(f"\n{'='*60}")
    print(f"EMERGENCY REBUILD COMPLETE: {jobs_count} jobs restored")
    print(f"{'='*60}")