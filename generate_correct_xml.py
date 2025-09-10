#!/usr/bin/env python3
import os
import sys
from datetime import datetime
from lxml import etree
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService

def generate_clean_xml():
    """Generate XML with exactly 70 jobs from the correct tearsheets"""
    
    print("=" * 60)
    print("GENERATING CLEAN XML WITH 70 JOBS")
    print("=" * 60)
    
    # Connect to Bullhorn with credentials
    bullhorn = BullhornService(
        client_id='cc648565-fdc2-4239-ac59-c545de9519dd',
        client_secret='SECRET',
        username='qts.api',
        password=os.environ.get('BULLHORN_PASSWORD')
    )
    if not bullhorn.authenticate():
        print("ERROR: Failed to authenticate with Bullhorn")
        return False
    
    # Correct tearsheets ONLY (exclude 1257 and others)
    tearsheets = [1256, 1264, 1499, 1239, 1556]
    print(f"Using tearsheets: {tearsheets}")
    
    # Get jobs from each tearsheet
    all_jobs = []
    for tearsheet_id in tearsheets:
        print(f"\nFetching jobs from tearsheet {tearsheet_id}...")
        
        # For STSI tearsheet 1556, use special branding
        is_stsi = (tearsheet_id == 1556)
        
        query = f"isPublic:1 AND tearsheets.id:{tearsheet_id}"
        fields = 'id,title,dateAdded,address,employmentType,description,publicDescription'
        
        jobs = bullhorn.search_job_orders(
            query=query,
            fields=fields,
            count=200
        )
        
        if jobs:
            print(f"  Found {len(jobs)} jobs from tearsheet {tearsheet_id}")
            for job in jobs:
                job['tearsheet_id'] = tearsheet_id
                job['is_stsi'] = is_stsi
            all_jobs.extend(jobs)
        else:
            print(f"  No jobs found for tearsheet {tearsheet_id}")
    
    print(f"\nTotal jobs collected: {len(all_jobs)}")
    
    # Limit to exactly 70 jobs
    if len(all_jobs) > 70:
        print(f"Limiting to 70 jobs (had {len(all_jobs)})")
        all_jobs = all_jobs[:70]
    
    # Create XML structure
    root = etree.Element("source")
    etree.SubElement(root, "publisher").text = "Myticas Consulting"
    etree.SubElement(root, "publisherurl").text = etree.CDATA("https://myticas.com")
    
    # Reference number generator
    def generate_reference(job_id):
        import hashlib
        hash_obj = hashlib.md5(str(job_id).encode())
        hash_hex = hash_obj.hexdigest()[:6].upper()
        return f"MYT-{hash_hex}"
    
    # Add each job
    for job_data in all_jobs:
        job = etree.SubElement(root, "job")
        
        # Clean title - NO COLON PREFIX
        title = job_data.get('title', '').strip()
        if title.startswith(':'):
            title = title[1:].strip()
        
        # Add job ID to title
        job_id = job_data.get('id', '')
        if job_id and f"({job_id})" not in title:
            title = f"{title} ({job_id})"
        
        # Company name based on tearsheet
        if job_data.get('is_stsi'):
            company = "STSI (Staffing Technical Services Inc.)"
            url_base = "https://apply.stsigroup.com"
        else:
            company = "Myticas Consulting"
            url_base = "https://apply.myticas.com"
        
        # Format date
        date_added = job_data.get('dateAdded', '')
        if date_added:
            try:
                dt = datetime.fromtimestamp(date_added / 1000)
                formatted_date = dt.strftime("%B %d, %Y")
            except:
                formatted_date = "January 1, 2025"
        else:
            formatted_date = "January 1, 2025"
        
        # Core fields
        etree.SubElement(job, "title").text = etree.CDATA(f" {title} ")
        etree.SubElement(job, "company").text = etree.CDATA(f" {company} ")
        etree.SubElement(job, "date").text = etree.CDATA(f" {formatted_date} ")
        etree.SubElement(job, "referencenumber").text = etree.CDATA(f" {generate_reference(job_id)} ")
        etree.SubElement(job, "bhatsid").text = etree.CDATA(f" {job_id} ")
        
        # URL with source parameter
        import urllib.parse
        job_title_encoded = urllib.parse.quote(job_data.get('title', '').replace(':', ''))
        url = f"{url_base}/{job_id}/{job_title_encoded}/?source=LinkedIn"
        etree.SubElement(job, "url").text = etree.CDATA(f" {url} ")
        
        # Description
        description = job_data.get('publicDescription') or job_data.get('description', '')
        if not description:
            description = f"Join our team as a {job_data.get('title', 'professional')}"
        etree.SubElement(job, "description").text = etree.CDATA(f" {description} ")
        
        # Employment type
        emp_type = job_data.get('employmentType', 'Full-time')
        etree.SubElement(job, "jobtype").text = etree.CDATA(f" {emp_type} ")
        
        # Location
        address = job_data.get('address', {}) or {}
        city = address.get('city', '')
        state = address.get('state', '')
        country = address.get('countryName', 'United States')
        
        etree.SubElement(job, "city").text = etree.CDATA(f" {city} ")
        etree.SubElement(job, "state").text = etree.CDATA(f" {state} ")
        etree.SubElement(job, "country").text = etree.CDATA(f" {country} ")
        
        # Additional fields
        etree.SubElement(job, "category").text = etree.CDATA(" ")
        etree.SubElement(job, "apply_email").text = etree.CDATA(" apply@myticas.com ")
        etree.SubElement(job, "remotetype").text = etree.CDATA(" Remote ")
        etree.SubElement(job, "assignedrecruiter").text = etree.CDATA(" ")
        etree.SubElement(job, "jobfunction").text = etree.CDATA(" Other ")
        etree.SubElement(job, "jobindustries").text = etree.CDATA(" Other ")
        etree.SubElement(job, "senioritylevel").text = etree.CDATA(" Not Applicable ")
    
    # Write XML
    tree = etree.ElementTree(root)
    with open('myticas-job-feed.xml', 'wb') as f:
        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    # Verify the output
    with open('myticas-job-feed.xml', 'r') as f:
        content = f.read()
        job_count = content.count('<job>')
        has_bhatsid = '<bhatsid>' in content
        has_myt = 'MYT-' in content
        has_colons = ':Full Stack' in content or ':<!' in content
        
        print("\n" + "=" * 60)
        print("VERIFICATION RESULTS:")
        print(f"✅ Jobs in XML: {job_count}")
        print(f"✅ Has bhatsid tags: {has_bhatsid}")
        print(f"✅ Has MYT- references: {has_myt}")
        print(f"✅ Has colon prefixes: {has_colons}")
        print(f"✅ File size: {len(content) / 1024:.1f} KB")
        print("=" * 60)
        
        if job_count == 70 and has_bhatsid and has_myt and not has_colons:
            print("\n🎉 SUCCESS: XML generated with correct format!")
            return True
        else:
            print("\n⚠️ WARNING: XML may not be in correct format")
            return False

if __name__ == "__main__":
    success = generate_clean_xml()
    sys.exit(0 if success else 1)