#!/usr/bin/env python3
"""
Direct Bullhorn API Pull Script
Fetches all jobs from configured tearsheets and generates accurate XML
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Tearsheet configurations
TEARSHEETS = [
    {'id': 1234, 'name': 'Open Tech Opportunities (OTT)', 'company': 'Myticas Consulting'},
    {'id': 1267, 'name': 'VMS Active Jobs', 'company': 'Myticas Consulting'},
    {'id': 1556, 'name': 'Sponsored - STSI', 'company': 'STSI (Staffing Technical Services Inc.)'},
    {'id': 1300, 'name': 'Grow (GR)', 'company': 'Myticas Consulting'},
    {'id': 1523, 'name': 'Chicago (CHI)', 'company': 'Myticas Consulting'}
]

def fetch_tearsheet_jobs(tearsheet_id, tearsheet_name):
    """Fetch all jobs from a specific tearsheet"""
    try:
        from bullhorn_service import BullhornService
        
        bs = BullhornService()
        bs.authenticate()
        
        print(f"  Fetching tearsheet {tearsheet_id} ({tearsheet_name})...")
        
        # Get tearsheet with jobs
        tearsheet = bs.get_tearsheet(tearsheet_id, associations='jobOrders')
        
        if not tearsheet:
            print(f"    ERROR: Could not fetch tearsheet {tearsheet_id}")
            return []
        
        # Extract job IDs
        job_ids = []
        if tearsheet.get('jobOrders') and tearsheet['jobOrders'].get('data'):
            job_ids = [job['id'] for job in tearsheet['jobOrders']['data']]
        
        print(f"    Found {len(job_ids)} jobs in tearsheet")
        
        if not job_ids:
            return []
        
        # Fetch full job details
        fields = [
            'id', 'title', 'publicDescription', 'dateAdded', 'dateLastModified',
            'address', 'employmentType', 'onSite', 'assignedUsers', 'owner',
            'customText12', 'customText13', 'customText14',  # AI classification fields
            'isOpen', 'isPublic', 'status'
        ]
        
        jobs = []
        batch_size = 20
        
        for i in range(0, len(job_ids), batch_size):
            batch = job_ids[i:i+batch_size]
            batch_jobs = bs.get_multiple_job_orders(batch, fields=','.join(fields))
            if batch_jobs:
                jobs.extend(batch_jobs)
        
        # Fetch assigned user names
        for job in jobs:
            if job.get('assignedUsers') and job['assignedUsers'].get('data'):
                user_ids = [u['id'] for u in job['assignedUsers']['data']]
                if user_ids:
                    users = bs.get_corporate_users(user_ids[:5])  # Limit to 5 users
                    names = []
                    for user in users:
                        name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
                        if name:
                            names.append(name)
                    job['assignedUsers_names'] = names
        
        return jobs
        
    except Exception as e:
        print(f"    ERROR fetching tearsheet {tearsheet_id}: {str(e)}")
        return []

def generate_reference_number(job_id):
    """Generate a consistent reference number for a job"""
    import hashlib
    import string
    import random
    
    # Use job ID to seed for consistency
    seed = int(hashlib.md5(str(job_id).encode()).hexdigest()[:8], 16)
    random.seed(seed)
    
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(10))

def format_job_for_xml(job, company):
    """Format job data for XML output"""
    formatted = {}
    
    # Job ID
    job_id = str(job.get('id', ''))
    formatted['bhatsid'] = job_id
    
    # Title with ID
    title = job.get('title', '').strip()
    formatted['title'] = f"{title} ({job_id})"
    
    # Company
    formatted['company'] = company
    
    # Date (ISO format)
    date_added = job.get('dateAdded')
    if date_added:
        dt = datetime.fromtimestamp(date_added / 1000)
        formatted['date'] = dt.strftime('%Y-%m-%d')
    else:
        formatted['date'] = datetime.now().strftime('%Y-%m-%d')
    
    # Reference number
    formatted['referencenumber'] = generate_reference_number(job_id)
    
    # URL
    if 'STSI' in company:
        base_url = 'https://apply.stsigroup.com'
    else:
        base_url = 'https://apply.myticas.com'
    
    import urllib.parse
    url_title = urllib.parse.quote(title)
    formatted['url'] = f"{base_url}/{job_id}/{url_title}/?source=LinkedIn"
    
    # Description
    formatted['description'] = job.get('publicDescription', '')
    
    # Employment type
    emp_type = job.get('employmentType', '')
    type_map = {
        'Contract': 'Contract',
        'Contract to Hire': 'Contract to Hire',
        'Direct Hire': 'Direct Hire',
        'Full Time': 'Direct Hire',
        'Permanent': 'Direct Hire'
    }
    formatted['jobtype'] = type_map.get(emp_type, emp_type)
    
    # Location
    address = job.get('address', {})
    formatted['city'] = address.get('city', '')
    formatted['state'] = address.get('state', '')
    formatted['country'] = address.get('countryName', 'United States')
    
    # Fix Canada/US inconsistencies
    canada_cities = ['Toronto', 'Ottawa', 'Vancouver', 'Montreal', 'Calgary', 'Edmonton']
    if any(city in formatted['city'] for city in canada_cities):
        formatted['country'] = 'Canada'
    
    # Category (empty in current system)
    formatted['category'] = ''
    
    # Apply email
    formatted['apply_email'] = os.environ.get('APPLY_EMAIL', 'apply@myticas.com')
    
    # Remote type
    on_site = job.get('onSite', '')
    remote_map = {
        'Remote': 'Remote',
        'Hybrid': 'Hybrid',
        'On Site': 'Onsite',
        'Onsite': 'Onsite'
    }
    formatted['remotetype'] = remote_map.get(on_site, 'Onsite')
    
    # Assigned recruiter
    if job.get('assignedUsers_names'):
        formatted['assignedrecruiter'] = ', '.join(job['assignedUsers_names'])
    else:
        formatted['assignedrecruiter'] = ''
    
    # AI classification fields
    formatted['jobfunction'] = job.get('customText12', '')
    formatted['jobindustries'] = job.get('customText13', '')
    formatted['senioritylevel'] = job.get('customText14', '')
    
    return formatted

def build_xml(jobs):
    """Build XML structure from jobs"""
    root = ET.Element('source')
    
    # Add publisher info
    pub = ET.SubElement(root, 'publisher')
    pub.text = 'Myticas Consulting'
    
    pub_url = ET.SubElement(root, 'publisherurl')
    pub_url.text = 'https://www.myticas.com'
    
    # Add jobs (sorted by ID for consistency)
    for job in sorted(jobs, key=lambda x: int(x.get('bhatsid', 0))):
        job_elem = ET.SubElement(root, 'job')
        
        # Define field order
        field_order = [
            'title', 'company', 'date', 'referencenumber', 'bhatsid',
            'url', 'description', 'jobtype', 'city', 'state', 'country',
            'category', 'apply_email', 'remotetype', 'assignedrecruiter',
            'jobfunction', 'jobindustries', 'senioritylevel'
        ]
        
        for field in field_order:
            elem = ET.SubElement(job_elem, field)
            value = job.get(field, '')
            
            # Wrap in CDATA for certain fields
            if field in ['title', 'company', 'description', 'category', 'apply_email',
                        'assignedrecruiter', 'jobfunction', 'jobindustries', 'senioritylevel']:
                elem.text = f" {value} " if value else " "
            else:
                elem.text = value
    
    # Convert to string with proper formatting
    xml_str = ET.tostring(root, encoding='unicode')
    
    # Pretty print
    dom = minidom.parseString(xml_str)
    pretty_xml = dom.toprettyxml(indent='  ', encoding='UTF-8')
    
    # Fix CDATA sections
    pretty_xml = pretty_xml.decode('utf-8')
    
    # Add CDATA wrappers
    for field in ['title', 'company', 'date', 'referencenumber', 'bhatsid', 'url',
                  'description', 'jobtype', 'city', 'state', 'country', 'category',
                  'apply_email', 'remotetype', 'assignedrecruiter', 'jobfunction',
                  'jobindustries', 'senioritylevel']:
        pretty_xml = pretty_xml.replace(f'<{field}> ', f'<{field}><![CDATA[')
        pretty_xml = pretty_xml.replace(f' </{field}>', f']]></{field}>')
        pretty_xml = pretty_xml.replace(f'<{field}><![CDATA[]]></{field}>', f'<{field}><![CDATA[ ]]></{field}>')
    
    return pretty_xml

def main():
    print("=" * 80)
    print("BULLHORN DIRECT API PULL")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Track all jobs and duplicates
    all_jobs = {}
    tearsheet_jobs = defaultdict(list)
    duplicate_count = 0
    
    # Fetch jobs from each tearsheet
    print("FETCHING TEARSHEET DATA")
    print("-" * 40)
    
    for tearsheet in TEARSHEETS:
        jobs = fetch_tearsheet_jobs(tearsheet['id'], tearsheet['name'])
        
        for job in jobs:
            job_id = job.get('id')
            if job_id:
                if job_id in all_jobs:
                    duplicate_count += 1
                    print(f"    Duplicate job {job_id} found in multiple tearsheets")
                else:
                    formatted_job = format_job_for_xml(job, tearsheet['company'])
                    all_jobs[job_id] = formatted_job
                    tearsheet_jobs[tearsheet['name']].append(job_id)
    
    print(f"\nTotal unique jobs: {len(all_jobs)}")
    print(f"Duplicate jobs removed: {duplicate_count}")
    
    # Show distribution
    print("\nJOB DISTRIBUTION BY TEARSHEET")
    print("-" * 40)
    for tearsheet_name, job_ids in tearsheet_jobs.items():
        print(f"{tearsheet_name}: {len(job_ids)} jobs")
    
    # Generate XML
    print("\nGENERATING XML")
    print("-" * 40)
    
    if all_jobs:
        xml_content = build_xml(list(all_jobs.values()))
        
        # Save to file
        output_path = '/tmp/bullhorn_direct_pull.xml'
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_content)
        
        print(f"XML generated with {len(all_jobs)} jobs")
        print(f"Saved to: {output_path}")
        
        # Compare with current feed
        current_feed = 'myticas-job-feed.xml'
        if os.path.exists(current_feed):
            print("\nCOMPARING WITH CURRENT FEED")
            print("-" * 40)
            
            # Parse current feed
            tree = ET.parse(current_feed)
            root = tree.getroot()
            current_jobs = set()
            for job_elem in root.findall('.//job'):
                bhatsid = job_elem.find('bhatsid')
                if bhatsid is not None and bhatsid.text:
                    job_id = bhatsid.text.strip().replace('<![CDATA[', '').replace(']]>', '').strip()
                    current_jobs.add(job_id)
            
            print(f"Current feed has {len(current_jobs)} jobs")
            print(f"Direct API pull has {len(all_jobs)} jobs")
            
            # Find differences
            api_jobs = set(str(j) for j in all_jobs.keys())
            missing_in_current = api_jobs - current_jobs
            extra_in_current = current_jobs - api_jobs
            
            if missing_in_current:
                print(f"\nJobs missing from current feed: {len(missing_in_current)}")
                for job_id in list(missing_in_current)[:10]:  # Show first 10
                    job = all_jobs.get(int(job_id))
                    if job:
                        print(f"  - {job_id}: {job.get('title', 'Unknown')}")
            
            if extra_in_current:
                print(f"\nJobs in current feed but not in API: {len(extra_in_current)}")
                for job_id in list(extra_in_current)[:10]:  # Show first 10
                    print(f"  - {job_id}")
        
        # Summary report
        print("\n" + "=" * 80)
        print("SUMMARY REPORT")
        print("-" * 40)
        print(f"Total tearsheets processed: {len(TEARSHEETS)}")
        print(f"Total unique jobs found: {len(all_jobs)}")
        print(f"Duplicates removed: {duplicate_count}")
        print(f"XML file generated: {output_path}")
        
        # Save summary
        summary = {
            'timestamp': datetime.now().isoformat(),
            'tearsheets_processed': len(TEARSHEETS),
            'total_jobs': len(all_jobs),
            'duplicates_removed': duplicate_count,
            'job_distribution': {name: len(ids) for name, ids in tearsheet_jobs.items()},
            'output_file': output_path
        }
        
        summary_path = '/tmp/bullhorn_pull_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"Summary saved to: {summary_path}")
        
    else:
        print("ERROR: No jobs found from any tearsheet")
        return 1
    
    print("\n" + "=" * 80)
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    return 0

if __name__ == '__main__':
    sys.exit(main())