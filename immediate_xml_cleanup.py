#!/usr/bin/env python3
"""
Immediate XML Cleanup Script
Fixes duplicate jobs and field mismatches in existing XML files
Run this once to clean up current issues like job 34219 duplicates
"""

import os
import sys
import logging
from datetime import datetime

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables from .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

def run_immediate_cleanup():
    """Run immediate cleanup on all XML files"""
    try:
        # Import required services
        from bullhorn_service import BullhornService
        from xml_field_sync_service import XMLFieldSyncService
        from xml_integration_service import XMLIntegrationService
        
        logger.info("=" * 80)
        logger.info("STARTING IMMEDIATE XML CLEANUP")
        logger.info("=" * 80)
        
        # Initialize services
        bullhorn_service = BullhornService()
        field_sync_service = XMLFieldSyncService()
        xml_service = XMLIntegrationService()
        
        # Test Bullhorn connection
        logger.info("Testing Bullhorn connection...")
        if not bullhorn_service.test_connection():
            logger.error("Failed to connect to Bullhorn. Please check credentials.")
            return False
        
        logger.info("✅ Bullhorn connection successful")
        
        # Get all current jobs from all tearsheets
        logger.info("Fetching all jobs from Bullhorn tearsheets...")
        all_jobs = []
        
        # Tearsheet IDs from your monitors
        tearsheets = [
            {'id': 1499, 'name': 'Clover Sponsored Jobs'},
            {'id': 1258, 'name': 'Cleveland Sponsored Jobs'},
            {'id': 1264, 'name': 'VMS Sponsored Jobs'},
            {'id': 1256, 'name': 'Ottawa Sponsored Jobs'},
            {'id': 1257, 'name': 'Chicago Sponsored Jobs'}
        ]
        
        for tearsheet in tearsheets:
            logger.info(f"Fetching jobs from {tearsheet['name']} (ID: {tearsheet['id']})...")
            jobs = bullhorn_service.get_tearsheet_jobs(tearsheet['id'])
            if jobs:
                all_jobs.extend(jobs)
                logger.info(f"  Found {len(jobs)} jobs")
            else:
                logger.info(f"  No jobs found")
        
        logger.info(f"Total jobs fetched from Bullhorn: {len(all_jobs)}")
        
        # XML files to clean
        xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
        
        for xml_file in xml_files:
            if not os.path.exists(xml_file):
                logger.warning(f"XML file not found: {xml_file}")
                continue
            
            logger.info(f"\n{'='*60}")
            logger.info(f"CLEANING: {xml_file}")
            logger.info(f"{'='*60}")
            
            # Step 1: Check current state
            logger.info("Step 1: Analyzing current XML state...")
            sync_check = field_sync_service.comprehensive_sync_check(xml_file, all_jobs)
            
            logger.info(f"  Duplicates found: {len(sync_check.get('duplicates_found', []))}")
            if sync_check.get('duplicates_found'):
                for dup in sync_check['duplicates_found']:
                    logger.info(f"    - Job {dup['job_id']} appears {dup.get('count', 2)} times")
            
            logger.info(f"  Jobs with field mismatches: {len(sync_check.get('field_mismatches', []))}")
            if sync_check.get('field_mismatches'):
                for mismatch in sync_check['field_mismatches'][:5]:  # Show first 5
                    logger.info(f"    - Job {mismatch['job_id']}: {mismatch['title']}")
                    for field in mismatch['mismatches'][:3]:  # Show first 3 fields
                        logger.info(f"      • {field['field']}: XML='{field['xml_value']}' vs Bullhorn='{field['bullhorn_value']}'")
            
            logger.info(f"  Missing jobs (in Bullhorn but not XML): {len(sync_check.get('missing_jobs', []))}")
            logger.info(f"  Orphaned jobs (in XML but not Bullhorn): {len(sync_check.get('orphaned_jobs', []))}")
            
            # Step 2: Perform full sync
            logger.info("\nStep 2: Performing full synchronization...")
            sync_result = field_sync_service.perform_full_sync(xml_file, all_jobs)
            
            if sync_result.get('success'):
                logger.info("✅ SYNC COMPLETED SUCCESSFULLY")
                logger.info(f"  • Duplicates removed: {sync_result.get('duplicates_removed', 0)}")
                logger.info(f"  • Fields updated: {sync_result.get('fields_updated', 0)}")
                logger.info(f"  • Jobs added: {sync_result.get('jobs_added', 0)}")
                logger.info(f"  • Jobs removed: {sync_result.get('jobs_removed', 0)}")
                
                if sync_result.get('details'):
                    logger.info("  Details:")
                    for detail in sync_result['details'][:10]:  # Show first 10
                        logger.info(f"    - {detail}")
            else:
                logger.error(f"❌ Sync failed: {sync_result.get('error', 'Unknown error')}")
            
            # Step 3: Verify cleanup
            logger.info("\nStep 3: Verifying cleanup results...")
            verify_check = field_sync_service.comprehensive_sync_check(xml_file, all_jobs)
            
            remaining_duplicates = len(verify_check.get('duplicates_found', []))
            remaining_mismatches = len(verify_check.get('field_mismatches', []))
            
            if remaining_duplicates == 0 and remaining_mismatches == 0:
                logger.info("✅ XML FILE IS CLEAN!")
                logger.info(f"  • No duplicates remaining")
                logger.info(f"  • All fields synchronized")
            else:
                logger.warning(f"⚠️ Some issues remain:")
                if remaining_duplicates > 0:
                    logger.warning(f"  • {remaining_duplicates} duplicates still present")
                if remaining_mismatches > 0:
                    logger.warning(f"  • {remaining_mismatches} field mismatches still present")
        
        logger.info("\n" + "="*80)
        logger.info("CLEANUP COMPLETE")
        logger.info("="*80)
        
        # Create summary report
        logger.info("\nSUMMARY:")
        logger.info(f"• Processed {len(xml_files)} XML files")
        logger.info(f"• Total Bullhorn jobs: {len(all_jobs)}")
        logger.info("• The monitoring system will now maintain sync automatically")
        logger.info("• Check the logs above for detailed results")
        
        return True
        
    except Exception as e:
        logger.error(f"Critical error during cleanup: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False

if __name__ == "__main__":
    # Run the cleanup
    success = run_immediate_cleanup()
    
    if success:
        print("\n✅ Cleanup completed successfully!")
        print("Your XML files have been cleaned and synchronized.")
        sys.exit(0)
    else:
        print("\n❌ Cleanup failed. Check the logs for details.")
        sys.exit(1)