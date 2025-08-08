#!/usr/bin/env python3
"""
SIMPLE XML REBUILD - A bulletproof solution to maintain job feeds
No complex monitoring, no safeguards that break things, just simple sync
"""

import os
import json
import hashlib
from datetime import datetime
from lxml import etree
from bullhorn_service import BullhornService
from ftp_service import FTPService
from email_service import EmailService
from app import app, GlobalSettings, db
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generate_reference_number(job_id, job_title, date_added):
    """Generate a consistent reference number for a job"""
    # Use job ID, title, and date to create a unique but stable reference
    unique_string = f"{job_id}-{job_title}-{date_added}"
    hash_object = hashlib.md5(unique_string.encode())
    hash_hex = hash_object.hexdigest()
    
    # Format: First 4 chars of hash + job_id + last 4 chars of hash
    reference = f"{hash_hex[:4].upper()}{job_id}{hash_hex[-4:].upper()}"
    return reference

def build_complete_xml():
    """Build complete XML from Bullhorn with all jobs"""
    logger.info("="*60)
    logger.info("SIMPLE XML REBUILD - Starting fresh build")
    logger.info("="*60)
    
    with app.app_context():
        # Initialize Bullhorn service with credentials
        bullhorn = BullhornService()
        
        # Set credentials from environment
        bullhorn.client_id = os.environ.get('BULLHORN_CLIENT_ID')
        bullhorn.client_secret = os.environ.get('BULLHORN_CLIENT_SECRET')
        bullhorn.username = os.environ.get('BULLHORN_USERNAME')
        bullhorn.password = os.environ.get('BULLHORN_PASSWORD')
        
        if not all([bullhorn.client_id, bullhorn.client_secret, bullhorn.username, bullhorn.password]):
            logger.error("Missing Bullhorn credentials in environment")
            return 0
            
        # Authenticate
        if not bullhorn.authenticate():
            logger.error("Failed to authenticate with Bullhorn")
            return 0
        
        # Get all tearsheets and their jobs
        tearsheets = {
            1256: "Ottawa Sponsored Jobs",
            1257: "Chicago Sponsored Jobs", 
            1258: "Cleveland Sponsored Jobs",
            1264: "VMS Sponsored Jobs",
            1499: "Clover Sponsored Jobs"
        }
        
        all_jobs = []
        job_count_by_tearsheet = {}
        
        for tearsheet_id, name in tearsheets.items():
            logger.info(f"\nFetching {name} (ID: {tearsheet_id})...")
            jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
            job_count = len(jobs) if jobs else 0
            job_count_by_tearsheet[name] = job_count
            
            if jobs:
                all_jobs.extend(jobs)
                logger.info(f"  ✓ Found {job_count} jobs")
            else:
                logger.info(f"  ✓ No jobs found")
        
        logger.info(f"\n📊 TOTAL JOBS FOUND: {len(all_jobs)}")
        for name, count in job_count_by_tearsheet.items():
            logger.info(f"  • {name}: {count}")
        
        # Build XML structure
        root = etree.Element("source")
        
        # Sort jobs by dateAdded (newest first) for proper ordering
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
                
                # Reference number - ALWAYS use generated one
                etree.SubElement(job_elem, "referencenumber").text = etree.CDATA(reference_number)
                
                # URL
                etree.SubElement(job_elem, "url").text = etree.CDATA(f"https://apply.myticas.com/?jobId={job_id}")
                
                # Description with proper HTML formatting
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
                
                # Category (from AI classification)
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
                        'Sofia': '#LI-SO1:',
                        'Alain': '#LI-AD1:',
                        'David': '#LI-DG1:',
                        'Eryn': '#LI-EG1:',
                        'Dechen': '#LI-DW1:',
                        'Navya': '#LI-NA1:',
                        'Maya': '#LI-MR1:',
                        'Anthony': '#LI-AM1:',
                        'Juan': '#LI-JC1:',
                        'Tyler': '#LI-TN1:',
                        'Zachary': '#LI-ZF1:',
                        'Jeremy': '#LI-JD1:',
                        'Kelsey': '#LI-KW1:',
                        'Christy': '#LI-CS1:',
                        'Allyson': '#LI-AS1:',
                        'Rohini': '#LI-RB1:',
                        'Alex': '#LI-AM1:',
                        'Gabriel': '#LI-GG1:',
                        'Michelle': '#LI-MS1:',
                        'Krutika': '#LI-KK1:'
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
        
        # Save to both regular and scheduled files
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
                    logger.info(f"\n🚀 UPLOADED TO SFTP: {jobs_added} jobs now live at https://myticas.com/myticas-job-feed.xml")
                    
                    # Send success email
                    email_service = EmailService()
                    email_body = f"""
                    <h2>XML Feed Successfully Rebuilt</h2>
                    <p>The job feed has been completely rebuilt from Bullhorn data.</p>
                    
                    <h3>Summary:</h3>
                    <ul>
                        <li><strong>Total Jobs:</strong> {jobs_added}</li>
                        <li><strong>Ottawa:</strong> {job_count_by_tearsheet.get('Ottawa Sponsored Jobs', 0)}</li>
                        <li><strong>VMS:</strong> {job_count_by_tearsheet.get('VMS Sponsored Jobs', 0)}</li>
                        <li><strong>Clover:</strong> {job_count_by_tearsheet.get('Clover Sponsored Jobs', 0)}</li>
                        <li><strong>Chicago:</strong> {job_count_by_tearsheet.get('Chicago Sponsored Jobs', 0)}</li>
                        <li><strong>Cleveland:</strong> {job_count_by_tearsheet.get('Cleveland Sponsored Jobs', 0)}</li>
                    </ul>
                    
                    <p><strong>Live URL:</strong> <a href="https://myticas.com/myticas-job-feed.xml">https://myticas.com/myticas-job-feed.xml</a></p>
                    <p><strong>Timestamp:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                    """
                    
                    email_service.queue_email(
                        to_email='luke@myticas.com',
                        subject=f'XML Feed Rebuilt - {jobs_added} Jobs Live',
                        body=email_body,
                        cc_email='rob@myticas.com'
                    )
                    db.session.commit()
                    logger.info("  ✓ Email notification queued")
                    
        except Exception as e:
            logger.error(f"Error uploading to SFTP: {str(e)}")
        
        return jobs_added

if __name__ == "__main__":
    jobs_count = build_complete_xml()
    print(f"\n{'='*60}")
    print(f"REBUILD COMPLETE: {jobs_count} jobs now in XML feed")
    print(f"{'='*60}")