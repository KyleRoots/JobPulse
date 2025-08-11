#!/usr/bin/env python3
"""
Quick fix to identify and remove orphaned jobs by comparing with active monitoring system
"""
import logging
import time
import os
from comprehensive_monitoring_service import ComprehensiveMonitoringService

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def find_and_fix_orphaned_jobs():
    """Use the monitoring system to identify orphaned jobs and fix them"""
    logger.info("=== QUICK ORPHANED JOB IDENTIFICATION & FIX ===")
    
    # Monitor configuration (same as in app.py)
    monitors = [
        {'tearsheet_id': 2644, 'name': 'Myticas Consulting - Development'},
        {'tearsheet_id': 2645, 'name': 'Myticas Consulting - Infrastructure & DevOps'},
        {'tearsheet_id': 2646, 'name': 'Myticas Consulting - Business Analysis & Project Management'},
        {'tearsheet_id': 2647, 'name': 'Myticas Consulting - Data & Analytics'}
    ]
    
    try:
        # Initialize monitoring service
        monitoring_service = ComprehensiveMonitoringService()
        
        # Run a single monitoring cycle to get current data
        logger.info("Running monitoring cycle to get fresh tearsheet data...")
        results = monitoring_service.run_complete_monitoring_cycle(monitors, 'myticas-job-feed.xml')
        
        logger.info(f"Monitoring cycle completed:")
        logger.info(f"  - Jobs added: {results.get('jobs_added', 0)}")
        logger.info(f"  - Jobs removed: {results.get('jobs_removed', 0)}")
        logger.info(f"  - Jobs modified: {results.get('jobs_modified', 0)}")
        logger.info(f"  - Upload success: {results.get('upload_success', False)}")
        logger.info(f"  - Audit passed: {results.get('audit_passed', False)}")
        
        if results.get('jobs_removed', 0) > 0:
            logger.info("✅ Orphaned jobs were automatically removed during monitoring cycle")
            return True
        else:
            logger.info("No orphaned jobs found or removed")
            return False
            
    except Exception as e:
        logger.error(f"Error during monitoring cycle: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = find_and_fix_orphaned_jobs()
    if success:
        print("\n✅ SUCCESS: Orphaned jobs identified and removed")
        print("Live XML should now be synchronized with tearsheets")
    else:
        print("\n⚠️ No orphaned jobs removed - manual intervention may be needed")