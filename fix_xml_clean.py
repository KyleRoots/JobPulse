#!/usr/bin/env python3
"""
Create clean XML with exactly 70 jobs and correct STSI URLs
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
print("CREATING CLEAN XML WITH CORRECT STSI URLs")
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

# Add publisher URL at the top
publisherurl = etree.SubElement(root, 'publisherurl')
publisherurl.text = etree.CDATA('https://myticas.com')

# Define tearsheets with correct company names
tearsheets = [
    ('Sponsored - OTT', 1256, 'Myticas Consulting'),
    ('Sponsored - VMS', 1264, 'Myticas Consulting'),
    ('Sponsored - GR', 1499, 'Myticas Consulting'),
    ('Sponsored - CHI', 1239, 'Myticas Consulting'),
    ('Sponsored - STSI', 1556, 'STSI (Staffing Technical Services Inc.)')
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
        
        # Determine Apply URL based on company - USING STSIGROUP.COM
        if 'STSI' in company_name:
            # STSI jobs use stsigroup.com domain
            apply_url = f"https://apply.stsigroup.com/{job_id}/{job_title.replace(' ', '%20')}/?source=LinkedIn"
        else:
            # Standard Myticas jobs
            apply_url = f"https://apply.myticas.com/{job_id}/{job_title.replace(' ', '%20')}/?source=LinkedIn"
        
        # Override the apply URL in job data
        job['customText20'] = apply_url
        
        # Get AI classification
        try:
            title = job.get('title', '')
            description = job.get('publicDescription', '')
            classification = classifier.classify_job(title, description)
        except Exception as e:
            classification = {
                'function': 'Information Technology',
                'industries': 'Information Technology and Services',
                'seniority': 'Mid-Senior level'
            }
            
        # Map the job to XML format
        xml_job = xml_service.map_bullhorn_job_to_xml(
            job, 
            existing_reference_number=ref_number,
            monitor_name='STSI' if 'STSI' in company_name else name,
            skip_ai_classification=False,
            existing_ai_fields=classification
        )
        
        # Override company name and URL
        xml_job['company'] = company_name
        xml_job['url'] = apply_url
        
        # Create job element
        job_elem = etree.SubElement(root, 'job')
        
        # Add all fields in proper order
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

print("\n" + "=" * 70)
print(f"3. XML Build Summary:")
print("-" * 50)
for tearsheet_name, count in job_stats.items():
    print(f"  {tearsheet_name}: {count} jobs")
print(f"  TOTAL: {total_jobs} jobs")
print("=" * 70)

# Write XML to file
print("\n4. Writing clean XML file...")
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
    stsigroup_urls = content.count('apply.stsigroup.com')
    myticas_urls = content.count('apply.myticas.com')
    myt_refs = content.count('MYT-')
    
print(f"✓ Verification complete:")
print(f"  - Total jobs: {job_count}")
print(f"  - STSI company jobs: {stsi_count}")
print(f"  - STSI URLs (stsigroup.com): {stsigroup_urls}")
print(f"  - Myticas URLs: {myticas_urls}")
print(f"  - MYT reference numbers: {myt_refs}")

print("\n" + "=" * 70)
print("CLEAN XML CREATED SUCCESSFULLY")
print("=" * 70)