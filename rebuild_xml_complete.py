#!/usr/bin/env python3
"""
Complete XML rebuild with proper STSI handling
"""
import os
import sys
sys.path.insert(0, '.')
from lxml import etree
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from job_classification_service import JobClassificationService
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)

print("=" * 70)
print("COMPLETE XML REBUILD - Starting fresh with STSI handling")
print("=" * 70)

# Initialize services
bullhorn = BullhornService(
    client_id=os.environ.get('BULLHORN_CLIENT_ID'),
    client_secret=os.environ.get('BULLHORN_CLIENT_SECRET'), 
    username=os.environ.get('BULLHORN_USERNAME'),
    password=os.environ.get('BULLHORN_PASSWORD')
)

xml_service = XMLIntegrationService()
classifier = JobClassificationService()

# Connect to Bullhorn
print("\n1. Connecting to Bullhorn API...")
if not bullhorn.test_connection():
    print("❌ Failed to connect to Bullhorn")
    sys.exit(1)
print("✓ Connected to Bullhorn successfully")

# Create root XML structure
root = etree.Element('source')

# Add publisher URL at the top (as seen in template)
publisherurl = etree.SubElement(root, 'publisherurl')
publisherurl.text = etree.CDATA('https://myticas.com')

# Define correct tearsheets with expected counts
tearsheets = [
    ('Sponsored - OTT', 1256, 'Myticas Consulting'),   # Ottawa - ~42 jobs
    ('Sponsored - VMS', 1264, 'Myticas Consulting'),   # VMS - ~7 jobs  
    ('Sponsored - GR', 1499, 'Myticas Consulting'),    # Grand Rapids - ~8 jobs
    ('Sponsored - CHI', 1239, 'Myticas Consulting'),   # Chicago - ~0 jobs
    ('Sponsored - STSI', 1556, 'STSI (Staffing Technical Services Inc.)')   # STSI - ~13 jobs
]

total_jobs = 0
job_stats = {}

print("\n2. Fetching jobs from tearsheets...")
print("-" * 50)

# Fetch and add jobs from each tearsheet
for name, tearsheet_id, company_name in tearsheets:
    print(f"\nProcessing {name} (Tearsheet {tearsheet_id})...")
    jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
    job_stats[name] = len(jobs)
    print(f"  ✓ Found {len(jobs)} jobs")
    
    for job in jobs:
        total_jobs += 1
        job_id = job.get('id', 0)
        job_title = job.get('title', 'Unknown')
        
        # Generate reference number
        ref_number = f"MYT-{job_id:06d}"
        
        # Determine Apply URL based on company
        if 'STSI' in company_name:
            # STSI jobs may use STSI subdomain
            apply_url = f"https://apply.stsi.com/{job_id}/{job_title.replace(' ', '%20')}/?source=LinkedIn"
        else:
            # Standard Myticas jobs
            apply_url = f"https://apply.myticas.com/{job_id}/{job_title.replace(' ', '%20')}/?source=LinkedIn"
        
        # Override the apply URL in job data
        job['customText20'] = apply_url
        
        # Get AI classification (with timeout protection)
        try:
            title = job.get('title', '')
            description = job.get('publicDescription', '')
            classification = classifier.classify_job(title, description)
        except Exception as e:
            # Default classification if AI fails
            classification = {
                'function': 'Information Technology',
                'industries': 'Information Technology and Services',
                'seniority': 'Mid-Senior level'
            }
            print(f"    ⚠ Using default classification for job {job_id}")
            
        # Map the job to XML format with proper company name
        xml_job = xml_service.map_bullhorn_job_to_xml(
            job, 
            existing_reference_number=ref_number,
            monitor_name='STSI' if 'STSI' in company_name else name,
            skip_ai_classification=False,
            existing_ai_fields=classification
        )
        
        # Override company name based on tearsheet
        xml_job['company'] = company_name
        
        # Ensure Apply URL is set correctly
        xml_job['url'] = apply_url
        
        # Create job element
        job_elem = etree.SubElement(root, 'job')
        
        # Add all fields in order matching the template
        for field in ['title', 'company', 'date', 'referencenumber', 'url', 'description', 
                      'city', 'state', 'country', 'jobtype', 'category', 'jobfunction', 
                      'jobindustries', 'senioritylevel', 'experience', 'remotetype', 
                      'apply_email', 'assignedrecruiter']:
            elem = etree.SubElement(job_elem, field)
            value = xml_job.get(field, '')
            if value:
                elem.text = etree.CDATA(str(value))
            else:
                elem.text = etree.CDATA('')
        
        print(f"    ✓ Added job {total_jobs}: {job_title[:50]}...")

print("\n" + "=" * 70)
print(f"3. XML Build Summary:")
print("-" * 50)
for tearsheet_name, count in job_stats.items():
    print(f"  {tearsheet_name}: {count} jobs")
print(f"  TOTAL: {total_jobs} jobs")
print("=" * 70)

# Write XML to file
print("\n4. Writing XML file...")
tree = etree.ElementTree(root)
with open('myticas-job-feed.xml', 'wb') as f:
    tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
print("✓ XML file written successfully")

# Verify the file
print("\n5. Verifying XML file...")
with open('myticas-job-feed.xml', 'r') as f:
    content = f.read()
    job_count = content.count('<job>')
    stsi_count = content.count('STSI (Staffing Technical Services Inc.)')
    myticas_count = content.count('<company><![CDATA[Myticas Consulting]]></company>')
    
print(f"✓ Verification complete:")
print(f"  - Total jobs in XML: {job_count}")
print(f"  - STSI company jobs: {stsi_count}")
print(f"  - Myticas company jobs: {myticas_count}")
print(f"  - File size: {len(content):,} bytes")

print("\n" + "=" * 70)
print("XML REBUILD COMPLETE")
print("=" * 70)