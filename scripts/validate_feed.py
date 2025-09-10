#!/usr/bin/env python3
"""
Feed Validation Script
Validates the XML feed structure and content against the live template
"""

import os
import sys
import argparse
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from feeds.myticas_v2 import MyticasFeedV2
from bullhorn_service import BullhornService
from feeds.tearsheet_flow import TearsheetFlow

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def main():
    parser = argparse.ArgumentParser(description='Validate XML feed')
    parser.add_argument('--limit', type=int, default=5, help='Limit number of jobs to process')
    parser.add_argument('--out', type=str, default='/tmp/myticas-job-feed-v2.xml', 
                       help='Output file path')
    parser.add_argument('--validate-only', type=str, help='Validate existing XML file')
    parser.add_argument('--test-sftp', action='store_true', help='Test SFTP upload')
    parser.add_argument('--skip-ai', action='store_true', help='Skip AI classification')
    
    args = parser.parse_args()
    
    if args.validate_only:
        # Validate existing file
        print(f"Validating {args.validate_only}...")
        
        if not os.path.exists(args.validate_only):
            print(f"Error: File {args.validate_only} not found")
            sys.exit(1)
        
        with open(args.validate_only, 'r', encoding='utf-8') as f:
            xml_content = f.read()
        
        feed_generator = MyticasFeedV2()
        is_valid, errors = feed_generator.validate_myticas_feed(xml_content)
        
        if is_valid:
            print("✅ Validation passed!")
            checksum = feed_generator.generate_checksum(xml_content)
            print(f"Checksum: {checksum}")
        else:
            print("❌ Validation failed:")
            for error in errors:
                print(f"  - {error}")
            sys.exit(1)
    
    else:
        # Generate and validate new feed
        print(f"Generating feed with limit={args.limit}...")
        
        # Set skip AI flag if requested
        if args.skip_ai:
            os.environ['SKIP_AI_CLASSIFICATION'] = 'true'
        
        # Define test tearsheet configurations
        tearsheet_configs = [
            {
                'tearsheet_id': 1234,
                'name': 'Open Tech Opportunities (OTT)',
                'company': 'Myticas Consulting'
            },
            {
                'tearsheet_id': 1267,
                'name': 'VMS Active Jobs',
                'company': 'Myticas Consulting'
            },
            {
                'tearsheet_id': 1556,
                'name': 'Sponsored - STSI',
                'company': 'STSI (Staffing Technical Services Inc.)'
            },
            {
                'tearsheet_id': 1300,
                'name': 'Grow (GR)',
                'company': 'Myticas Consulting'
            },
            {
                'tearsheet_id': 1523,
                'name': 'Chicago (CHI)',
                'company': 'Myticas Consulting'
            }
        ]
        
        # Use only the first tearsheet if limit is small
        if args.limit <= 10:
            tearsheet_configs = tearsheet_configs[:1]
        
        flow = TearsheetFlow()
        
        # Override debounce for testing
        flow._rebuild_debounce_seconds = 0
        
        # Rebuild feed from tearsheets
        results = flow.rebuild_from_tearsheets(tearsheet_configs)
        
        if results['success']:
            print(f"✅ Feed generated successfully!")
            print(f"  - Jobs processed: {results['jobs_processed']}")
            print(f"  - Tearsheets processed: {results['tearsheets_processed']}")
            print(f"  - Output: {results['xml_path']}")
            
            # Validate the generated feed
            with open(results['xml_path'], 'r', encoding='utf-8') as f:
                xml_content = f.read()
            
            feed_generator = MyticasFeedV2()
            is_valid, errors = feed_generator.validate_myticas_feed(xml_content)
            
            if is_valid:
                print("✅ Validation passed!")
                checksum = feed_generator.generate_checksum(xml_content)
                print(f"Checksum: {checksum}")
                
                # Test SFTP if requested
                if args.test_sftp:
                    print("\nTesting SFTP upload...")
                    sftp_config = flow._get_sftp_config()
                    if sftp_config:
                        if feed_generator.publish(xml_content, sftp_config):
                            print("✅ SFTP upload successful!")
                        else:
                            print("❌ SFTP upload failed")
                    else:
                        print("❌ SFTP configuration not available")
            else:
                print("❌ Validation failed:")
                for error in errors:
                    print(f"  - {error}")
                sys.exit(1)
        else:
            print(f"❌ Feed generation failed!")
            print(f"  Reason: {results.get('reason', 'Unknown')}")
            if results.get('errors'):
                print("  Errors:")
                for error in results['errors']:
                    print(f"    - {error}")
            sys.exit(1)

if __name__ == '__main__':
    main()