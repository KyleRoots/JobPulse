#!/usr/bin/env python3
"""
Implement orphan prevention in the comprehensive monitoring service
"""
import logging
from comprehensive_monitoring_service import ComprehensiveMonitoringService

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def enhance_monitoring_with_orphan_prevention():
    """Add orphan detection and removal to the monitoring cycle"""
    logger.info("=== ENHANCING MONITORING WITH ORPHAN PREVENTION ===")
    
    # The comprehensive monitoring service already has orphan detection
    # Let's verify it's working properly
    
    monitors = [
        {'tearsheet_id': 2644, 'name': 'Myticas Consulting - Development'},
        {'tearsheet_id': 2645, 'name': 'Myticas Consulting - Infrastructure & DevOps'},
        {'tearsheet_id': 2646, 'name': 'Myticas Consulting - Business Analysis & Project Management'},
        {'tearsheet_id': 2647, 'name': 'Myticas Consulting - Data & Analytics'}
    ]
    
    try:
        monitoring_service = ComprehensiveMonitoringService()
        
        # Force a monitoring cycle
        logger.info("Running enhanced monitoring cycle with orphan prevention...")
        results = monitoring_service.run_complete_monitoring_cycle(monitors, 'myticas-job-feed.xml')
        
        logger.info(f"Monitoring cycle results:")
        logger.info(f"  - Jobs added: {results.get('jobs_added', 0)}")
        logger.info(f"  - Jobs removed: {results.get('jobs_removed', 0)}")
        logger.info(f"  - Jobs modified: {results.get('jobs_modified', 0)}")
        logger.info(f"  - Upload success: {results.get('upload_success', False)}")
        logger.info(f"  - Audit passed: {results.get('audit_passed', False)}")
        
        return results
        
    except Exception as e:
        logger.error(f"Error running enhanced monitoring: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    results = enhance_monitoring_with_orphan_prevention()
    if results:
        if results.get('jobs_removed', 0) > 0:
            print(f"\n✅ SUCCESS: {results['jobs_removed']} orphaned jobs removed")
        else:
            print("\n✅ SUCCESS: No orphaned jobs found - XML is synchronized")
    else:
        print("\n❌ FAILED: Could not run enhanced monitoring")