#!/usr/bin/env python3
"""
Test Direct Feed Generation with Correct Tearsheet IDs
Uses the Bullhorn API directly to generate and validate XML feed
"""

import os
import sys
from pathlib import Path
from datetime import datetime
import json

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Correct tearsheet configurations (from database)
TEARSHEET_CONFIGS = [
    {'tearsheet_id': 1256, 'name': 'Sponsored - OTT', 'company': 'Myticas Consulting'},
    {'tearsheet_id': 1264, 'name': 'Sponsored - VMS', 'company': 'Myticas Consulting'},
    {'tearsheet_id': 1556, 'name': 'Sponsored - STSI', 'company': 'STSI (Staffing Technical Services Inc.)'},
    {'tearsheet_id': 1499, 'name': 'Sponsored - GR', 'company': 'Myticas Consulting'},
    {'tearsheet_id': 1239, 'name': 'Sponsored - CHI', 'company': 'Myticas Consulting'}
]

def test_feed_generation():
    """Test feed generation using the tearsheet flow"""
    print("=" * 80)
    print("TESTING FEED GENERATION WITH CORRECT TEARSHEET IDS")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    from feeds.tearsheet_flow import TearsheetFlow
    from feeds.freeze_manager import FreezeManager
    
    # Check freeze status
    freeze_mgr = FreezeManager()
    status = freeze_mgr.get_status()
    
    print("FREEZE STATUS")
    print("-" * 40)
    print(f"Frozen: {status['frozen']}")
    print(f"Message: {status['message']}")
    
    if status['frozen']:
        print("\nWARNING: Feed generation is frozen. Use manage_feed.py to unfreeze.")
        return False
    
    # Initialize flow
    flow = TearsheetFlow()
    
    print("\nREBUILDING FEED FROM TEARSHEETS")
    print("-" * 40)
    
    # Rebuild feed
    result = flow.rebuild_from_tearsheets(TEARSHEET_CONFIGS)
    
    print(f"\nRebuild Success: {result['success']}")
    print(f"Jobs Processed: {result['jobs_processed']}")
    print(f"Tearsheets Processed: {result['tearsheets_processed']}")
    
    if result['errors']:
        print("\nErrors:")
        for error in result['errors']:
            print(f"  - {error}")
    
    if result['xml_path']:
        print(f"\nXML Feed Generated: {result['xml_path']}")
        
        # Load and validate
        with open(result['xml_path'], 'r', encoding='utf-8') as f:
            xml_content = f.read()
        
        # Count jobs in XML
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_content)
        job_count = len(root.findall('.//job'))
        
        print(f"Jobs in XML: {job_count}")
        
        # Compare with current feed
        if os.path.exists('myticas-job-feed.xml'):
            print("\nCOMPARING WITH CURRENT FEED")
            print("-" * 40)
            
            # Parse current feed
            tree = ET.parse('myticas-job-feed.xml')
            current_root = tree.getroot()
            current_jobs = set()
            
            for job_elem in current_root.findall('.//job'):
                bhatsid = job_elem.find('bhatsid')
                if bhatsid is not None and bhatsid.text:
                    job_id = bhatsid.text.strip().replace('<![CDATA[', '').replace(']]>', '').strip()
                    current_jobs.add(job_id)
            
            # Parse new feed
            new_jobs = set()
            for job_elem in root.findall('.//job'):
                bhatsid = job_elem.find('bhatsid')
                if bhatsid is not None and bhatsid.text:
                    job_id = bhatsid.text.strip()
                    new_jobs.add(job_id)
            
            print(f"Current feed: {len(current_jobs)} jobs")
            print(f"New feed: {len(new_jobs)} jobs")
            
            # Find differences
            missing_in_new = current_jobs - new_jobs
            added_in_new = new_jobs - current_jobs
            
            if missing_in_new:
                print(f"\nJobs removed: {len(missing_in_new)}")
                for job_id in list(missing_in_new)[:5]:
                    print(f"  - {job_id}")
            
            if added_in_new:
                print(f"\nJobs added: {len(added_in_new)}")
                for job_id in list(added_in_new)[:5]:
                    print(f"  - {job_id}")
            
            if not missing_in_new and not added_in_new:
                print("\n✅ Job IDs match perfectly between feeds")
        
        # Save summary
        summary = {
            'timestamp': datetime.now().isoformat(),
            'success': result['success'],
            'jobs_processed': result['jobs_processed'],
            'tearsheets_processed': result['tearsheets_processed'],
            'tearsheet_configs': TEARSHEET_CONFIGS,
            'xml_path': result['xml_path'],
            'uploaded': result.get('uploaded', False)
        }
        
        summary_path = '/tmp/feed_test_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"\nSummary saved to: {summary_path}")
    
    print("\n" + "=" * 80)
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    return result['success']

if __name__ == '__main__':
    success = test_feed_generation()
    sys.exit(0 if success else 1)