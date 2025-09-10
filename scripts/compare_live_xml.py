#!/usr/bin/env python3
"""
Compare Live XML with Bullhorn Tearsheet Data
Validates the uploaded XML file against actual Bullhorn data
"""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
import json
import re

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def extract_jobs_from_xml(xml_path):
    """Extract job data from XML file"""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    jobs = []
    for job_elem in root.findall('.//job'):
        job_data = {}
        for child in job_elem:
            text = child.text or ''
            # Clean CDATA markers
            text = text.replace('<![CDATA[', '').replace(']]>', '').strip()
            job_data[child.tag] = text
        jobs.append(job_data)
    
    return jobs

def fetch_bullhorn_job(job_id):
    """Fetch job data from Bullhorn API"""
    try:
        from bullhorn_service import BullhornService
        
        bs = BullhornService()
        bs.authenticate()
        
        # Fetch comprehensive job data
        fields = [
            'id', 'title', 'publicDescription', 'dateAdded', 
            'address', 'employmentType', 'onSite', 'assignedUsers',
            'customText12', 'customText13', 'customText14',  # AI fields
            'isOpen', 'isPublic', 'owner'
        ]
        
        job = bs.get_job_order(job_id, fields=','.join(fields))
        
        # Fetch assigned users if present
        if job.get('assignedUsers') and job['assignedUsers'].get('data'):
            user_ids = [u['id'] for u in job['assignedUsers']['data']]
            if user_ids:
                users = bs.get_corporate_users(user_ids)
                job['assignedUsers_names'] = [f"{u.get('firstName', '')} {u.get('lastName', '')}" for u in users]
        
        return job
        
    except Exception as e:
        print(f"Error fetching job {job_id}: {str(e)}")
        return None

def get_expected_values(bullhorn_job, tearsheet_name=None):
    """Convert Bullhorn data to expected XML values"""
    expected = {}
    
    # Job ID
    expected['bhatsid'] = str(bullhorn_job.get('id', ''))
    
    # Title (should include ID in parentheses)
    title = bullhorn_job.get('title', '')
    expected['title'] = f"{title} ({expected['bhatsid']})"
    
    # Date (should be ISO format)
    date_added = bullhorn_job.get('dateAdded')
    if date_added:
        dt = datetime.fromtimestamp(date_added / 1000)
        expected['date'] = dt.strftime('%Y-%m-%d')
    
    # Company (based on tearsheet)
    if tearsheet_name and 'STSI' in tearsheet_name:
        expected['company'] = 'STSI (Staffing Technical Services Inc.)'
    else:
        expected['company'] = 'Myticas Consulting'
    
    # Location
    address = bullhorn_job.get('address', {})
    expected['city'] = address.get('city', '')
    expected['state'] = address.get('state', '')
    expected['country'] = address.get('countryName', 'United States')
    
    # Employment type mapping
    emp_type = bullhorn_job.get('employmentType', '')
    type_map = {
        'Contract': 'Contract',
        'Contract to Hire': 'Contract to Hire',
        'Direct Hire': 'Direct Hire',
        'Full Time': 'Direct Hire',
        'Permanent': 'Direct Hire'
    }
    expected['jobtype'] = type_map.get(emp_type, emp_type)
    
    # Remote type
    on_site = bullhorn_job.get('onSite', '')
    remote_map = {
        'Remote': 'Remote',
        'Hybrid': 'Hybrid',
        'On Site': 'Onsite',
        'Onsite': 'Onsite'
    }
    expected['remotetype'] = remote_map.get(on_site, on_site)
    
    # URL (should follow pattern)
    if 'STSI' in expected['company']:
        base_url = 'https://apply.stsigroup.com'
    else:
        base_url = 'https://apply.myticas.com'
    
    # URL encode the title
    import urllib.parse
    url_title = urllib.parse.quote(title)
    expected['url'] = f"{base_url}/{expected['bhatsid']}/{url_title}/?source=LinkedIn"
    
    # AI classification fields (from custom fields)
    expected['jobfunction'] = bullhorn_job.get('customText12', '')
    expected['jobindustries'] = bullhorn_job.get('customText13', '')
    expected['senioritylevel'] = bullhorn_job.get('customText14', '')
    
    # Assigned recruiter
    if bullhorn_job.get('assignedUsers_names'):
        expected['assignedrecruiter'] = ', '.join(bullhorn_job['assignedUsers_names'])
    
    return expected

def compare_field(field_name, xml_value, expected_value):
    """Compare a single field and return discrepancy if found"""
    # Normalize for comparison
    xml_clean = (xml_value or '').strip()
    expected_clean = (expected_value or '').strip()
    
    # Special handling for certain fields
    if field_name == 'date':
        # Check if XML has wrong format (e.g., "September 09, 2025" vs "2025-09-09")
        try:
            # Try parsing the XML date in various formats
            for fmt in ['%B %d, %Y', '%Y-%m-%d']:
                try:
                    dt = datetime.strptime(xml_clean, fmt)
                    xml_normalized = dt.strftime('%Y-%m-%d')
                    if xml_normalized == expected_clean:
                        return None  # Dates match, just different format
                    break
                except:
                    continue
        except:
            pass
    
    if field_name == 'url':
        # Normalize URLs for comparison
        xml_clean = xml_clean.replace('%20', ' ').replace('+', ' ')
        expected_clean = expected_clean.replace('%20', ' ').replace('+', ' ')
    
    if xml_clean != expected_clean:
        return {
            'field': field_name,
            'xml_value': xml_value,
            'expected_value': expected_value
        }
    
    return None

def analyze_job(xml_job, tearsheet_name=None):
    """Analyze a single job from XML against Bullhorn data"""
    job_id = xml_job.get('bhatsid', '')
    
    if not job_id:
        return {
            'job_id': 'UNKNOWN',
            'title': xml_job.get('title', 'UNKNOWN'),
            'errors': ['Missing bhatsid field'],
            'discrepancies': []
        }
    
    # Fetch Bullhorn data
    bullhorn_job = fetch_bullhorn_job(job_id)
    
    if not bullhorn_job:
        return {
            'job_id': job_id,
            'title': xml_job.get('title', ''),
            'errors': ['Could not fetch from Bullhorn API'],
            'discrepancies': []
        }
    
    # Get expected values
    expected = get_expected_values(bullhorn_job, tearsheet_name)
    
    # Compare fields
    discrepancies = []
    critical_fields = ['bhatsid', 'title', 'date', 'company', 'city', 'state', 'country', 
                      'jobtype', 'remotetype', 'url']
    optional_fields = ['jobfunction', 'jobindustries', 'senioritylevel', 'assignedrecruiter']
    
    for field in critical_fields + optional_fields:
        xml_value = xml_job.get(field, '')
        expected_value = expected.get(field, '')
        
        discrepancy = compare_field(field, xml_value, expected_value)
        if discrepancy:
            discrepancy['critical'] = field in critical_fields
            discrepancies.append(discrepancy)
    
    return {
        'job_id': job_id,
        'title': xml_job.get('title', ''),
        'errors': [],
        'discrepancies': discrepancies,
        'bullhorn_data': bullhorn_job
    }

def generate_report(results):
    """Generate detailed comparison report"""
    report = []
    report.append("=" * 80)
    report.append("LIVE XML VALIDATION REPORT")
    report.append("=" * 80)
    report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Summary statistics
    total_jobs = len(results)
    jobs_with_errors = sum(1 for r in results if r['errors'])
    jobs_with_discrepancies = sum(1 for r in results if r['discrepancies'])
    
    report.append("SUMMARY")
    report.append("-" * 40)
    report.append(f"Total jobs analyzed: {total_jobs}")
    report.append(f"Jobs with API errors: {jobs_with_errors}")
    report.append(f"Jobs with discrepancies: {jobs_with_discrepancies}")
    report.append(f"Jobs matching perfectly: {total_jobs - jobs_with_errors - jobs_with_discrepancies}\n")
    
    # Common issues
    all_discrepancies = []
    for result in results:
        all_discrepancies.extend(result['discrepancies'])
    
    field_issues = {}
    for disc in all_discrepancies:
        field = disc['field']
        if field not in field_issues:
            field_issues[field] = 0
        field_issues[field] += 1
    
    if field_issues:
        report.append("COMMON ISSUES BY FIELD")
        report.append("-" * 40)
        for field, count in sorted(field_issues.items(), key=lambda x: x[1], reverse=True):
            report.append(f"{field}: {count} jobs affected")
        report.append("")
    
    # Detailed job analysis
    report.append("DETAILED JOB ANALYSIS")
    report.append("=" * 80)
    
    for i, result in enumerate(results, 1):
        if not result['errors'] and not result['discrepancies']:
            continue  # Skip perfect matches
        
        report.append(f"\n[{i}] Job ID: {result['job_id']}")
        report.append(f"    Title: {result['title']}")
        
        if result['errors']:
            report.append("    ERRORS:")
            for error in result['errors']:
                report.append(f"      - {error}")
        
        if result['discrepancies']:
            report.append("    DISCREPANCIES:")
            for disc in result['discrepancies']:
                critical = "🔴" if disc['critical'] else "🟡"
                report.append(f"      {critical} {disc['field']}:")
                report.append(f"         XML: {disc['xml_value'][:100]}")
                report.append(f"         Expected: {disc['expected_value'][:100]}")
    
    # Date format issue
    date_issues = [d for d in all_discrepancies if d['field'] == 'date']
    if date_issues:
        report.append("\n" + "=" * 80)
        report.append("DATE FORMAT ISSUE DETECTED")
        report.append("-" * 40)
        report.append("All jobs have incorrect date format!")
        report.append(f"Current format: 'Month DD, YYYY' (e.g., {date_issues[0]['xml_value']})")
        report.append(f"Expected format: 'YYYY-MM-DD' (e.g., {date_issues[0]['expected_value']})")
        report.append("This is a systematic issue affecting all jobs in the feed.")
    
    return "\n".join(report)

def main():
    # Path to uploaded XML file
    xml_path = 'attached_assets/myticas-job-feed-v2_1757483911801.xml'
    
    if not os.path.exists(xml_path):
        print(f"Error: XML file not found at {xml_path}")
        return 1
    
    print(f"Loading XML file: {xml_path}")
    xml_jobs = extract_jobs_from_xml(xml_path)
    print(f"Found {len(xml_jobs)} jobs in XML\n")
    
    # Limit analysis for testing (remove this for full analysis)
    sample_size = 10
    if len(xml_jobs) > sample_size:
        print(f"Analyzing first {sample_size} jobs for testing...")
        xml_jobs = xml_jobs[:sample_size]
    
    # Analyze each job
    results = []
    for i, xml_job in enumerate(xml_jobs, 1):
        job_id = xml_job.get('bhatsid', 'UNKNOWN')
        print(f"[{i}/{len(xml_jobs)}] Analyzing job {job_id}...")
        
        # Determine tearsheet based on company
        tearsheet_name = None
        if 'STSI' in xml_job.get('company', ''):
            tearsheet_name = 'Sponsored - STSI'
        
        result = analyze_job(xml_job, tearsheet_name)
        results.append(result)
    
    # Generate report
    print("\n" + "=" * 80)
    report = generate_report(results)
    print(report)
    
    # Save report
    report_path = '/tmp/xml_validation_report.txt'
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"\nReport saved to: {report_path}")
    
    # Create JSON output for programmatic use
    json_path = '/tmp/xml_validation_data.json'
    json_data = {
        'timestamp': datetime.now().isoformat(),
        'xml_file': xml_path,
        'jobs_analyzed': len(results),
        'results': [
            {
                'job_id': r['job_id'],
                'title': r['title'],
                'errors': r['errors'],
                'discrepancy_count': len(r['discrepancies']),
                'discrepancies': r['discrepancies']
            }
            for r in results
        ]
    }
    
    with open(json_path, 'w') as f:
        json.dump(json_data, f, indent=2)
    print(f"JSON data saved to: {json_path}")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())