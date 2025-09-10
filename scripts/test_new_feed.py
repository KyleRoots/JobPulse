#!/usr/bin/env python3
"""
Test Script for New XML Feed System
Verifies that the new feed generator is working correctly
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_freeze_manager():
    """Test the freeze manager functionality"""
    print("\n=== Testing Freeze Manager ===")
    from feeds.freeze_manager import FreezeManager
    
    freeze_mgr = FreezeManager()
    status = freeze_mgr.get_status()
    
    print(f"Freeze Status: {status['frozen']}")
    print(f"Freeze Flag: {status['freeze_flag']}")
    print(f"Message: {status['message']}")
    
    # Test operation checking
    operations = ['rebuild', 'upload', 'monitor']
    for op in operations:
        allowed = freeze_mgr.check_operation(op)
        print(f"Operation '{op}': {'ALLOWED' if allowed else 'BLOCKED'}")
    
    return not status['frozen']

def test_feed_generator():
    """Test the feed generator with sample data"""
    print("\n=== Testing Feed Generator ===")
    from feeds.myticas_v2 import MyticasFeedV2
    
    generator = MyticasFeedV2()
    
    # Create sample job data
    sample_jobs = [
        {
            'title': 'Software Engineer',
            'bhatsid': '12345',
            'company': 'Myticas Consulting',
            'date': '2025-01-14',
            'description': 'Test job description for software engineer position.',
            'jobtype': 'Contract',
            'city': 'Chicago',
            'state': 'IL',
            'country': 'United States',
            'remotetype': 'Hybrid',
            'assignedrecruiter': 'Test Recruiter',
            'jobfunction': 'Information Technology',
            'jobindustries': 'Technology',
            'senioritylevel': 'Mid-Senior level'
        },
        {
            'title': 'Data Analyst',
            'bhatsid': '12346',
            'company': 'STSI (Staffing Technical Services Inc.)',
            'date': '2025-01-14',
            'description': 'Test job description for data analyst position.',
            'jobtype': 'Direct Hire',
            'city': 'New York',
            'state': 'NY',
            'country': 'United States',
            'remotetype': 'Remote',
            'assignedrecruiter': 'Another Recruiter',
            'jobfunction': 'Data Science',
            'jobindustries': 'Finance',
            'senioritylevel': 'Entry level'
        }
    ]
    
    try:
        # Build the feed
        xml_content = generator.build_myticas_feed(sample_jobs)
        print(f"✅ Feed generated with {len(sample_jobs)} jobs")
        
        # Validate the feed
        is_valid, errors = generator.validate_myticas_feed(xml_content)
        if is_valid:
            print("✅ Feed validation passed")
            
            # Generate checksum
            checksum = generator.generate_checksum(xml_content)
            print(f"Checksum: {checksum[:16]}...")
            
            # Write to temp file for inspection
            temp_path = '/tmp/test_feed.xml'
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(xml_content)
            print(f"Test feed written to: {temp_path}")
            
            return True
        else:
            print("❌ Feed validation failed:")
            for error in errors:
                print(f"  - {error}")
            return False
            
    except Exception as e:
        print(f"❌ Error generating feed: {str(e)}")
        return False

def test_tearsheet_config():
    """Test tearsheet configuration"""
    print("\n=== Testing Tearsheet Configuration ===")
    from tearsheet_config import TearsheetConfig
    
    tearsheets = [
        'Open Tech Opportunities (OTT)',
        'Sponsored - STSI',
        'Unknown Tearsheet'
    ]
    
    for ts in tearsheets:
        config = TearsheetConfig.get_config_for_tearsheet(ts)
        company = TearsheetConfig.get_company_name(ts)
        print(f"Tearsheet: {ts}")
        print(f"  Company: {company}")
        print(f"  Domain: {config.get('domain', 'N/A')}")
    
    return True

def test_sftp_config():
    """Test SFTP configuration availability"""
    print("\n=== Testing SFTP Configuration ===")
    from feeds.tearsheet_flow import TearsheetFlow
    
    flow = TearsheetFlow()
    sftp_config = flow._get_sftp_config()
    
    if sftp_config:
        print("✅ SFTP configuration available")
        print(f"  Host: {sftp_config.get('host', 'N/A')}")
        print(f"  Port: {sftp_config.get('port', 'N/A')}")
        print(f"  Username: {'***' if sftp_config.get('username') else 'N/A'}")
        print(f"  Password: {'***' if sftp_config.get('password') else 'N/A'}")
        return True
    else:
        print("⚠️ SFTP configuration not available")
        print("  Set SFTP_HOST, SFTP_USER, and SFTP_PASSWORD environment variables")
        return False

def main():
    print("=" * 60)
    print("XML FEED SYSTEM TEST")
    print("=" * 60)
    
    results = []
    
    # Run tests
    results.append(('Freeze Manager', test_freeze_manager()))
    results.append(('Feed Generator', test_feed_generator()))
    results.append(('Tearsheet Config', test_tearsheet_config()))
    results.append(('SFTP Config', test_sftp_config()))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{test_name}: {status}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 60)
    
    return 0 if all_passed else 1

if __name__ == '__main__':
    sys.exit(main())