#!/usr/bin/env python3
"""
Management CLI for XML Feed System
Provides commands for managing the feed generation, freeze state, and monitoring
"""

import os
import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def freeze_feed():
    """Freeze the XML feed system"""
    os.environ['XML_FEED_FRZ'] = 'true'
    print("🔒 XML Feed System FROZEN")
    print("   - All scheduled rebuilds disabled")
    print("   - SFTP uploads blocked")
    print("   - Manual refreshes disabled")
    
    from feeds.freeze_manager import FreezeManager
    freeze_mgr = FreezeManager()
    freeze_mgr.send_alert("XML Feed System has been frozen", "warning")
    
    return 0

def unfreeze_feed():
    """Unfreeze the XML feed system"""
    os.environ['XML_FEED_FRZ'] = 'false'
    print("🔓 XML Feed System UNFROZEN")
    print("   - Scheduled rebuilds enabled")
    print("   - SFTP uploads allowed")
    print("   - Manual refreshes enabled")
    
    from feeds.freeze_manager import FreezeManager
    freeze_mgr = FreezeManager()
    freeze_mgr.send_alert("XML Feed System has been unfrozen", "info")
    
    return 0

def status():
    """Show current system status"""
    from feeds.freeze_manager import FreezeManager
    
    print("=" * 60)
    print("XML FEED SYSTEM STATUS")
    print("=" * 60)
    
    # Freeze status
    freeze_mgr = FreezeManager()
    status = freeze_mgr.get_status()
    
    print(f"\n🔒 Freeze Status: {'FROZEN' if status['frozen'] else 'ACTIVE'}")
    print(f"   Flag Value: {status['freeze_flag']}")
    
    # Configuration status
    print("\n⚙️ Configuration:")
    configs = {
        'APPLY_EMAIL': os.environ.get('APPLY_EMAIL', 'not set'),
        'PUBLIC_JOB_URL_BASE': os.environ.get('PUBLIC_JOB_URL_BASE', 'not set'),
        'XML_ALERTS_EMAIL': os.environ.get('XML_ALERTS_EMAIL', 'not set'),
        'SFTP_HOST': '***' if os.environ.get('SFTP_HOST') else 'not set',
        'BULLHORN_CLIENT_ID': '***' if os.environ.get('BULLHORN_CLIENT_ID') else 'not set'
    }
    
    for key, value in configs.items():
        print(f"   {key}: {value}")
    
    # Check feed files
    print("\n📄 Feed Files:")
    feed_files = [
        'myticas-job-feed.xml',
        'myticas-job-feed-v2.xml',
        'myticas-job-feed-classified.xml'
    ]
    
    for file in feed_files:
        if os.path.exists(file):
            stat = os.stat(file)
            mod_time = datetime.fromtimestamp(stat.st_mtime)
            size_kb = stat.st_size / 1024
            print(f"   {file}: {size_kb:.1f} KB, modified {mod_time.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print(f"   {file}: not found")
    
    return 0

def rebuild_feed(limit=None, skip_upload=False):
    """Manually rebuild the feed"""
    from feeds.tearsheet_flow import TearsheetFlow
    
    print("🔄 Rebuilding XML feed...")
    
    # Check if frozen
    if os.environ.get('XML_FEED_FRZ', '').lower() == 'true':
        print("❌ Cannot rebuild - system is frozen")
        print("   Run 'manage_feed.py unfreeze' first")
        return 1
    
    # Define tearsheet configurations
    tearsheet_configs = [
        {'tearsheet_id': 1234, 'name': 'Open Tech Opportunities (OTT)', 'company': 'Myticas Consulting'},
        {'tearsheet_id': 1267, 'name': 'VMS Active Jobs', 'company': 'Myticas Consulting'},
        {'tearsheet_id': 1556, 'name': 'Sponsored - STSI', 'company': 'STSI (Staffing Technical Services Inc.)'},
        {'tearsheet_id': 1300, 'name': 'Grow (GR)', 'company': 'Myticas Consulting'},
        {'tearsheet_id': 1523, 'name': 'Chicago (CHI)', 'company': 'Myticas Consulting'}
    ]
    
    # Limit tearsheets if requested
    if limit:
        tearsheet_configs = tearsheet_configs[:1]
        print(f"   Limited to first tearsheet for testing")
    
    # Temporarily disable upload if requested
    if skip_upload:
        os.environ['XML_FEED_FRZ'] = 'upload_only'
        print("   SFTP upload disabled for this rebuild")
    
    try:
        flow = TearsheetFlow()
        flow._rebuild_debounce_seconds = 0  # Disable debounce for manual rebuild
        
        results = flow.rebuild_from_tearsheets(tearsheet_configs)
        
        if results['success']:
            print(f"✅ Feed rebuilt successfully")
            print(f"   Jobs: {results['jobs_processed']}")
            print(f"   Tearsheets: {results['tearsheets_processed']}")
            print(f"   File: {results['xml_path']}")
            print(f"   Uploaded: {'Yes' if results['uploaded'] else 'No'}")
            return 0
        else:
            print(f"❌ Feed rebuild failed")
            print(f"   Reason: {results.get('reason', 'Unknown')}")
            if results.get('errors'):
                print("   Errors:")
                for error in results['errors']:
                    print(f"     - {error}")
            return 1
            
    finally:
        # Restore upload setting
        if skip_upload:
            del os.environ['XML_FEED_FRZ']

def validate_feed(file_path=None):
    """Validate an XML feed file"""
    from feeds.myticas_v2 import MyticasFeedV2
    
    if not file_path:
        file_path = 'myticas-job-feed-v2.xml'
    
    print(f"📋 Validating {file_path}...")
    
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return 1
    
    with open(file_path, 'r', encoding='utf-8') as f:
        xml_content = f.read()
    
    generator = MyticasFeedV2()
    is_valid, errors = generator.validate_myticas_feed(xml_content)
    
    if is_valid:
        print("✅ Validation passed!")
        
        # Count jobs
        import re
        jobs = re.findall(r'<job>', xml_content)
        print(f"   Jobs: {len(jobs)}")
        
        # Generate checksum
        checksum = generator.generate_checksum(xml_content)
        print(f"   Checksum: {checksum[:32]}...")
        
        return 0
    else:
        print("❌ Validation failed!")
        print("   Errors:")
        for error in errors:
            print(f"     - {error}")
        return 1

def main():
    parser = argparse.ArgumentParser(description='Manage XML Feed System')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Freeze command
    subparsers.add_parser('freeze', help='Freeze the XML feed system')
    
    # Unfreeze command
    subparsers.add_parser('unfreeze', help='Unfreeze the XML feed system')
    
    # Status command
    subparsers.add_parser('status', help='Show system status')
    
    # Rebuild command
    rebuild_parser = subparsers.add_parser('rebuild', help='Rebuild the feed')
    rebuild_parser.add_argument('--limit', type=int, help='Limit number of jobs')
    rebuild_parser.add_argument('--skip-upload', action='store_true', help='Skip SFTP upload')
    
    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validate a feed file')
    validate_parser.add_argument('file', nargs='?', help='File to validate')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Execute command
    if args.command == 'freeze':
        return freeze_feed()
    elif args.command == 'unfreeze':
        return unfreeze_feed()
    elif args.command == 'status':
        return status()
    elif args.command == 'rebuild':
        return rebuild_feed(args.limit, args.skip_upload)
    elif args.command == 'validate':
        return validate_feed(args.file)
    else:
        print(f"Unknown command: {args.command}")
        return 1

if __name__ == '__main__':
    sys.exit(main())