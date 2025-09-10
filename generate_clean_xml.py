#!/usr/bin/env python3
"""
Generate clean XML with proper format (no bhatsid tags) and MYT- reference numbers
Only includes jobs from the correct tearsheets
"""
import os
import re
import logging
from datetime import datetime
from lxml import etree
from bullhorn_service import BullhornService
from job_classification_service import JobClassificationService

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def generate_reference_number():
    """Generate MYT- format reference number"""
    import random
    import string
    chars = string.ascii_uppercase + string.digits
    return f"MYT-{''.join(random.choices(chars, k=6))}"

def clean_html(text):
    """Clean and normalize HTML content for CDATA sections"""
    if not text:
        return ""
    
    # Convert to string
    text = str(text)
    
    # Fix common HTML issues
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&nbsp;', ' ')
    
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    return text

def create_job_element(job_data, ai_fields=None):
    """Create a job element in the proper format (no bhatsid tag)"""
    job = etree.Element("job")
    
    # Define fields in order (WITHOUT bhatsid)
    fields_order = [
        'title', 'company', 'date', 'referencenumber', 'url',
        'description', 'jobtype', 'city', 'state', 'country',
        'category', 'apply_email', 'remotetype', 'assignedrecruiter',
        'jobfunction', 'jobindustries', 'senioritylevel'
    ]
    
    # Add AI fields if provided
    if ai_fields:
        job_data['jobfunction'] = ai_fields.get('jobfunction', 'Other')
        job_data['jobindustries'] = ai_fields.get('jobindustries', 'Other')
        job_data['senioritylevel'] = ai_fields.get('senioritylevel', 'Not Applicable')
    else:
        job_data['jobfunction'] = job_data.get('jobfunction', 'Other')
        job_data['jobindustries'] = job_data.get('jobindustries', 'Other')
        job_data['senioritylevel'] = job_data.get('senioritylevel', 'Not Applicable')
    
    # Create elements in order
    for field in fields_order:
        elem = etree.SubElement(job, field)
        value = job_data.get(field, '')
        
        # Clean the value
        if field == 'description':
            value = clean_html(value)
        else:
            value = str(value).strip() if value else ''
        
        # Add as CDATA
        if value:
            elem.text = etree.CDATA(f" {value} ")
        else:
            elem.text = etree.CDATA(" ")
    
    return job

def main():
    logger.info("=" * 70)
    logger.info("GENERATING CLEAN XML FORMAT")
    logger.info("=" * 70)
    
    # Initialize services with credentials from environment
    bullhorn = BullhornService(
        client_id=os.environ.get('BULLHORN_CLIENT_ID'),
        client_secret=os.environ.get('BULLHORN_CLIENT_SECRET'),
        username=os.environ.get('BULLHORN_USERNAME'),
        password=os.environ.get('BULLHORN_PASSWORD')
    )
    ai_service = JobClassificationService()
    
    # Define tearsheets to process
    tearsheets = {
        1256: 'Ottawa',
        1264: 'VMS (Ottawa)',
        1499: 'Grand Rapids',
        1239: 'Chicago',
        1556: 'STSI'
    }
    
    # Create root element with proper structure
    root = etree.Element("source")
    
    # Add publisher info (optional, but keeping for consistency)
    publisherurl = etree.SubElement(root, "publisherurl")
    publisherurl.text = etree.CDATA("https://myticas.com")
    
    total_jobs = 0
    used_references = set()
    
    for tearsheet_id, tearsheet_name in tearsheets.items():
        logger.info(f"\nProcessing {tearsheet_name} (Tearsheet {tearsheet_id})...")
        
        # Get jobs from Bullhorn
        jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
        
        if not jobs:
            logger.info(f"  No jobs found")
            continue
        
        logger.info(f"  Found {len(jobs)} jobs")
        
        for job in jobs:
            try:
                # Get job details
                job_id = job.get('id')
                if not job_id:
                    continue
                
                # The job data from tearsheet might already have all we need
                job_details = job
                
                # If we need more details, fetch them
                if 'publicDescription' not in job_details:
                    job_details = bullhorn.get_job_by_id(job_id)
                    if not job_details:
                        continue
                
                # Extract job data
                title = job_details.get('title', '').strip()
                
                # Clean title - remove ID from title if present
                clean_title = re.sub(r'\s*\(\d+\)\s*$', '', title)
                
                # Determine company name
                if tearsheet_id == 1556:
                    company_name = "STSI (Staffing Technical Services Inc.)"
                else:
                    company_name = "Myticas Consulting"
                
                # Generate unique reference number
                ref_number = generate_reference_number()
                while ref_number in used_references:
                    ref_number = generate_reference_number()
                used_references.add(ref_number)
                
                # Generate URL based on company
                if tearsheet_id == 1556:
                    base_url = "https://apply.stsigroup.com"
                else:
                    base_url = "https://apply.myticas.com"
                
                # Encode title for URL
                import urllib.parse
                encoded_title = urllib.parse.quote(clean_title.replace(' ', '%20'))
                job_url = f"{base_url}/{job_id}/{encoded_title}/?source=LinkedIn"
                
                # Get location data
                location = job_details.get('address', {})
                city = location.get('city', '') if location else ''
                state = location.get('state', '') if location else ''
                country = location.get('countryName', 'United States') if location else 'United States'
                
                # Map job type
                employment_type = job_details.get('employmentType', '')
                if 'W2' in employment_type or 'Contract' in employment_type:
                    job_type = 'Contract'
                elif 'Contract to Hire' in employment_type or 'Contract-to-Hire' in employment_type:
                    job_type = 'Contract to Hire'
                elif 'Direct' in employment_type or 'Permanent' in employment_type or 'Full Time' in employment_type:
                    job_type = 'Direct Hire'
                else:
                    job_type = 'Contract'
                
                # Get other fields
                description = job_details.get('publicDescription', '')
                date_added = job_details.get('dateAdded')
                
                # Format date
                if date_added:
                    try:
                        date_obj = datetime.fromtimestamp(date_added / 1000)
                        formatted_date = date_obj.strftime('%B %d, %Y')
                    except:
                        formatted_date = datetime.now().strftime('%B %d, %Y')
                else:
                    formatted_date = datetime.now().strftime('%B %d, %Y')
                
                # Get recruiter info
                owner = job_details.get('owner', {})
                if owner:
                    first_name = owner.get('firstName', '')
                    last_name = owner.get('lastName', '')
                    custom_text = owner.get('customText1', '')
                    if custom_text:
                        recruiter = f"#LI-{custom_text}: {first_name} {last_name}"
                    else:
                        recruiter = f"{first_name} {last_name}"
                else:
                    recruiter = "#LI-RS1: Myticas Recruiter"
                
                # Determine remote type
                is_remote = job_details.get('isOpen', True)
                on_site = job_details.get('onSite', 'No Information')
                
                if on_site == 'On Site':
                    remote_type = 'Onsite'
                elif on_site == 'Remote':
                    remote_type = 'Remote'
                elif 'Hybrid' in str(on_site):
                    remote_type = 'Hybrid'
                else:
                    remote_type = 'Hybrid'
                
                # Create job data
                job_data = {
                    'title': f"{clean_title} ({job_id})",
                    'company': company_name,
                    'date': formatted_date,
                    'referencenumber': ref_number,
                    'url': job_url,
                    'description': description,
                    'jobtype': job_type,
                    'city': city,
                    'state': state,
                    'country': country,
                    'category': '',
                    'apply_email': 'apply@myticas.com',
                    'remotetype': remote_type,
                    'assignedrecruiter': recruiter
                }
                
                # Get AI classification
                ai_fields = None
                try:
                    ai_fields = ai_service.classify_job(clean_title, description)
                except:
                    pass
                
                # Create job element
                job_elem = create_job_element(job_data, ai_fields)
                root.append(job_elem)
                
                total_jobs += 1
                
            except Exception as e:
                logger.error(f"  Error processing job {job_id}: {str(e)}")
                continue
    
    # Create tree and write to file
    tree = etree.ElementTree(root)
    etree.indent(tree, space="  ")
    
    output_file = 'myticas-job-feed-clean.xml'
    
    with open(output_file, 'wb') as f:
        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    logger.info("\n" + "=" * 70)
    logger.info(f"CLEAN XML GENERATION COMPLETE")
    logger.info(f"Total jobs: {total_jobs}")
    logger.info(f"Output file: {output_file}")
    logger.info("=" * 70)
    
    return output_file

if __name__ == "__main__":
    main()