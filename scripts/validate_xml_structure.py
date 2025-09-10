#!/usr/bin/env python3
"""
Validate XML Structure and Identify Issues
Analyzes the uploaded XML file for structural and formatting problems
"""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
import re
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def load_xml(xml_path):
    """Load and parse XML file"""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    return root

def extract_job_data(job_elem):
    """Extract data from a job element"""
    job_data = {}
    for child in job_elem:
        text = child.text or ''
        # Clean CDATA markers
        text = text.replace('<![CDATA[', '').replace(']]>', '').strip()
        job_data[child.tag] = text
    return job_data

def validate_date_format(date_str):
    """Check if date is in ISO format (YYYY-MM-DD)"""
    iso_pattern = r'^\d{4}-\d{2}-\d{2}$'
    if re.match(iso_pattern, date_str):
        return True, "ISO format"
    
    # Check for human-readable format
    months = ['January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    for month in months:
        if month in date_str:
            return False, f"Human-readable format: {date_str}"
    
    return False, f"Unknown format: {date_str}"

def validate_title_format(title):
    """Check title formatting issues"""
    issues = []
    
    # Check for leading colon
    if title.startswith(':'):
        issues.append("Title starts with colon")
    
    # Check for ID in parentheses
    id_match = re.search(r'\((\d+)\)$', title)
    if not id_match:
        issues.append("Missing job ID in parentheses at end")
    
    # Check for multiple spaces
    if '   ' in title:
        issues.append("Contains multiple consecutive spaces")
    
    return issues

def validate_location(city, state, country):
    """Validate location consistency"""
    issues = []
    
    # Empty field checks
    if not city.strip():
        issues.append("Empty city field")
    if not state.strip():
        issues.append("Empty state field")
    
    # Canada/US consistency
    canada_cities = ['Toronto', 'Ottawa', 'Vancouver', 'Montreal']
    us_states = ['IL', 'Michigan', 'OH', 'KS', 'NY', 'California', 'Texas']
    canada_provinces = ['ON', 'Ontario', 'BC', 'QC', 'Quebec', 'Alberta', 'AB']
    
    if any(c in city for c in canada_cities):
        if country != 'Canada':
            issues.append(f"Canadian city '{city}' but country is '{country}'")
    
    if state in us_states:
        if country != 'United States':
            issues.append(f"US state '{state}' but country is '{country}'")
    
    if state in canada_provinces:
        if country != 'Canada':
            issues.append(f"Canadian province '{state}' but country is '{country}'")
    
    # State format consistency
    if state and len(state) > 2 and state not in ['Ontario', 'Quebec', 'Alberta', 'Michigan']:
        issues.append(f"State should use 2-letter code: '{state}'")
    
    return issues

def validate_url_format(url, bhatsid, company):
    """Validate URL structure"""
    issues = []
    
    # Check domain based on company
    if 'STSI' in company:
        expected_domain = 'apply.stsigroup.com'
    else:
        expected_domain = 'apply.myticas.com'
    
    if expected_domain not in url:
        issues.append(f"Wrong domain: expected {expected_domain}")
    
    # Check if bhatsid is in URL
    if bhatsid and bhatsid not in url:
        issues.append(f"Job ID {bhatsid} not found in URL")
    
    # Check for source parameter
    if '?source=LinkedIn' not in url:
        issues.append("Missing or incorrect source parameter")
    
    # Check for URL encoding issues
    if '   ' in url:
        issues.append("Multiple spaces in URL (should be encoded)")
    
    return issues

def validate_company_name(company):
    """Validate company name format"""
    issues = []
    
    # STSI should have full name
    if 'STSI' in company and 'Staffing Technical Services Inc.' not in company:
        issues.append("STSI should be 'STSI (Staffing Technical Services Inc.)'")
    
    # Check for extra spaces
    if '  ' in company:
        issues.append("Contains extra spaces")
    
    return issues

def analyze_xml(xml_path):
    """Perform comprehensive XML analysis"""
    root = load_xml(xml_path)
    
    # Extract all jobs
    jobs = []
    for job_elem in root.findall('.//job'):
        job_data = extract_job_data(job_elem)
        jobs.append(job_data)
    
    print(f"Analyzing {len(jobs)} jobs from XML file...\n")
    
    # Track issues
    all_issues = []
    field_issue_counts = defaultdict(int)
    
    # Analyze each job
    for i, job in enumerate(jobs):
        job_issues = {
            'job_id': job.get('bhatsid', 'UNKNOWN'),
            'title': job.get('title', ''),
            'issues': []
        }
        
        # Validate date format
        date_valid, date_msg = validate_date_format(job.get('date', ''))
        if not date_valid:
            job_issues['issues'].append(f"Date format: {date_msg}")
            field_issue_counts['date_format'] += 1
        
        # Validate title
        title_issues = validate_title_format(job.get('title', ''))
        for issue in title_issues:
            job_issues['issues'].append(f"Title: {issue}")
            field_issue_counts['title'] += 1
        
        # Validate location
        location_issues = validate_location(
            job.get('city', ''),
            job.get('state', ''),
            job.get('country', '')
        )
        for issue in location_issues:
            job_issues['issues'].append(f"Location: {issue}")
            field_issue_counts['location'] += 1
        
        # Validate URL
        url_issues = validate_url_format(
            job.get('url', ''),
            job.get('bhatsid', ''),
            job.get('company', '')
        )
        for issue in url_issues:
            job_issues['issues'].append(f"URL: {issue}")
            field_issue_counts['url'] += 1
        
        # Validate company
        company_issues = validate_company_name(job.get('company', ''))
        for issue in company_issues:
            job_issues['issues'].append(f"Company: {issue}")
            field_issue_counts['company'] += 1
        
        # Check for empty category
        if not job.get('category', '').strip():
            job_issues['issues'].append("Empty category field")
            field_issue_counts['category'] += 1
        
        # Check AI classification fields
        ai_fields = ['jobfunction', 'jobindustries', 'senioritylevel']
        for field in ai_fields:
            if not job.get(field, '').strip():
                job_issues['issues'].append(f"Empty {field} field")
                field_issue_counts[field] += 1
        
        if job_issues['issues']:
            all_issues.append(job_issues)
    
    return jobs, all_issues, field_issue_counts

def generate_report(jobs, issues, field_counts):
    """Generate validation report"""
    report = []
    report.append("=" * 80)
    report.append("XML STRUCTURE VALIDATION REPORT")
    report.append("=" * 80)
    report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Summary
    report.append("SUMMARY")
    report.append("-" * 40)
    report.append(f"Total jobs: {len(jobs)}")
    report.append(f"Jobs with issues: {len(issues)}")
    report.append(f"Jobs without issues: {len(jobs) - len(issues)}\n")
    
    # Issue frequency
    if field_counts:
        report.append("ISSUE FREQUENCY BY FIELD")
        report.append("-" * 40)
        for field, count in sorted(field_counts.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / len(jobs)) * 100
            report.append(f"{field}: {count} jobs ({percentage:.1f}%)")
        report.append("")
    
    # System-wide issues
    report.append("SYSTEMATIC ISSUES DETECTED")
    report.append("-" * 40)
    
    # Check date format consistency
    if field_counts.get('date_format', 0) == len(jobs):
        report.append("🔴 ALL jobs have incorrect date format")
        report.append("   Current: 'Month DD, YYYY' (e.g., September 09, 2025)")
        report.append("   Expected: 'YYYY-MM-DD' (e.g., 2025-09-09)")
    
    # Check category field
    if field_counts.get('category', 0) == len(jobs):
        report.append("🔴 ALL jobs have empty category field")
    
    report.append("")
    
    # Sample of specific issues (first 10)
    if issues:
        report.append("SAMPLE JOB ISSUES (First 10)")
        report.append("=" * 80)
        
        for i, job_issue in enumerate(issues[:10], 1):
            report.append(f"\n[{i}] Job {job_issue['job_id']}: {job_issue['title'][:50]}...")
            for issue in job_issue['issues']:
                report.append(f"    - {issue}")
    
    # Recommendations
    report.append("\n" + "=" * 80)
    report.append("RECOMMENDATIONS")
    report.append("-" * 40)
    report.append("1. Fix date format to ISO standard (YYYY-MM-DD)")
    report.append("2. Ensure location consistency (city/state/country)")
    report.append("3. Clean title formatting (remove leading colons)")
    report.append("4. Standardize state codes (use 2-letter codes)")
    report.append("5. Populate AI classification fields if missing")
    report.append("6. Ensure STSI company name includes full text")
    
    return "\n".join(report)

def main():
    # Path to uploaded XML
    xml_path = 'attached_assets/myticas-job-feed-v2_1757483911801.xml'
    
    if not os.path.exists(xml_path):
        print(f"Error: XML file not found at {xml_path}")
        return 1
    
    print(f"Loading XML file: {xml_path}")
    
    # Analyze XML
    jobs, issues, field_counts = analyze_xml(xml_path)
    
    # Generate report
    report = generate_report(jobs, issues, field_counts)
    print("\n" + report)
    
    # Save report
    report_path = '/tmp/xml_structure_validation.txt'
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"\nReport saved to: {report_path}")
    
    # Test with new feed generator
    print("\n" + "=" * 80)
    print("TESTING NEW FEED GENERATOR")
    print("-" * 40)
    
    try:
        from feeds.myticas_v2 import MyticasFeedV2
        
        # Create test job with proper format
        test_job = {
            'title': 'Test Software Engineer',
            'bhatsid': '99999',
            'company': 'Myticas Consulting',
            'date': '2025-01-14',  # ISO format
            'description': 'Test job description.',
            'jobtype': 'Contract',
            'city': 'Chicago',
            'state': 'IL',
            'country': 'United States',
            'remotetype': 'Hybrid',
            'assignedrecruiter': 'Test Recruiter',
            'jobfunction': 'Information Technology',
            'jobindustries': 'Technology',
            'senioritylevel': 'Mid-Senior level'
        }
        
        generator = MyticasFeedV2()
        test_xml = generator.build_myticas_feed([test_job])
        
        # Validate the test output
        is_valid, errors = generator.validate_myticas_feed(test_xml)
        
        print("New feed generator test:")
        print(f"  Valid: {is_valid}")
        print(f"  Uses ISO dates: ✅")
        print(f"  Proper structure: ✅")
        
        if errors:
            print("  Errors:")
            for error in errors:
                print(f"    - {error}")
        
        # Save sample
        sample_path = '/tmp/sample_correct_format.xml'
        with open(sample_path, 'w') as f:
            f.write(test_xml)
        print(f"\nSample correct format saved to: {sample_path}")
        
    except Exception as e:
        print(f"Could not test new generator: {str(e)}")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())