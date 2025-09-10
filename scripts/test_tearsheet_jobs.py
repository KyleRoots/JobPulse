#!/usr/bin/env python3
"""
Test script to check tearsheet job counts directly from Bullhorn
"""

import sys
import os
sys.path.append('..')

from bullhorn_service import BullhornService
import json
from datetime import datetime

def test_tearsheets():
    """Test all tearsheets and report job counts"""
    
    print("=" * 60)
    print("TEARSHEET JOB COUNT TEST")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Initialize Bullhorn service
    bs = BullhornService(
        client_id=os.environ.get('BULLHORN_CLIENT_ID'),
        client_secret=os.environ.get('BULLHORN_CLIENT_SECRET'),
        username=os.environ.get('BULLHORN_USERNAME'),
        password=os.environ.get('BULLHORN_PASSWORD')
    )
    
    if not bs.test_connection():
        print("ERROR: Failed to connect to Bullhorn")
        return
    
    print("\n✅ Connected to Bullhorn successfully\n")
    
    # Test each tearsheet
    tearsheets = [
        ('Sponsored - OTT', 1256),
        ('Sponsored - VMS', 1264),
        ('Sponsored - GR', 1499),
        ('Sponsored - CHI', 1239),
        ('Sponsored - STSI', 1556)
    ]
    
    total_jobs = 0
    tearsheet_results = []
    
    for name, tearsheet_id in tearsheets:
        print(f"Checking {name} (ID: {tearsheet_id})...")
        
        try:
            jobs = bs.get_tearsheet_jobs(tearsheet_id)
            
            if jobs:
                job_count = len(jobs)
                total_jobs += job_count
                
                # Show first 3 jobs as sample
                print(f"  ✅ Found {job_count} jobs")
                for job in jobs[:3]:
                    job_id = job.get('id', 'Unknown')
                    title = job.get('title', 'No title')
                    address = job.get('address', {})
                    city = address.get('city', 'Unknown')
                    state = address.get('state', '')
                    print(f"     - Job {job_id}: {title} ({city}, {state})")
                
                if job_count > 3:
                    print(f"     ... and {job_count - 3} more jobs")
                    
                tearsheet_results.append({
                    'name': name,
                    'id': tearsheet_id,
                    'count': job_count,
                    'status': 'active'
                })
            else:
                print(f"  ⚠️ No jobs found (tearsheet may be empty or inactive)")
                tearsheet_results.append({
                    'name': name,
                    'id': tearsheet_id,
                    'count': 0,
                    'status': 'empty'
                })
                
        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            tearsheet_results.append({
                'name': name,
                'id': tearsheet_id,
                'count': 0,
                'status': 'error',
                'error': str(e)
            })
        
        print()
    
    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total jobs across all tearsheets: {total_jobs}")
    print("\nTearsheet breakdown:")
    for result in tearsheet_results:
        status_icon = "✅" if result['count'] > 0 else "⚠️"
        print(f"  {status_icon} {result['name']}: {result['count']} jobs")
    
    # Check for discrepancies with expected counts
    print("\n" + "=" * 60)
    print("DISCREPANCY CHECK")
    print("=" * 60)
    
    expected = {
        'Sponsored - OTT': 41,  # From user's screenshot
        'Sponsored - VMS': 7,
        'Sponsored - GR': 8,
        'Sponsored - CHI': 0,   # Shows 0 in screenshot
        'Sponsored - STSI': 12
    }
    
    for result in tearsheet_results:
        name = result['name']
        actual = result['count']
        expected_count = expected.get(name, 0)
        
        if actual != expected_count:
            diff = actual - expected_count
            print(f"  ⚠️ {name}: Expected {expected_count}, Got {actual} (Diff: {diff:+d})")
        else:
            print(f"  ✅ {name}: Matches expected count ({actual})")

if __name__ == "__main__":
    test_tearsheets()