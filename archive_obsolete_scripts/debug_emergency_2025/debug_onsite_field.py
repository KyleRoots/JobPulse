#!/usr/bin/env python3
"""
Debug script to check the actual onSite field value from Bullhorn for job 34219
"""

import logging
import json
from datetime import datetime

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Check onSite field value for job 34219"""
    from app import app
    
    with app.app_context():
        try:
            # Get all tearsheet jobs to find 34219
            logger.info("=" * 80)
            logger.info("DEBUGGING JOB 34219 ONSITE FIELD")
            logger.info("=" * 80)
            
            # Import after app context
            from bullhorn_service import BullhornService
            from xml_integration_service import XMLIntegrationService
            
            # Initialize services
            bullhorn = BullhornService()
            xml_service = XMLIntegrationService()
            
            # Get job 34219 directly
            logger.info("\nFetching job 34219 from Bullhorn...")
            job = bullhorn.get_job_by_id(34219)
            
            if job:
                logger.info(f"✅ Job 34219 found!")
                logger.info(f"Title: {job.get('title')}")
                
                # Check onSite field specifically
                onsite_value = job.get('onSite')
                logger.info(f"\n📍 onSite field value: {onsite_value}")
                logger.info(f"   Type: {type(onsite_value)}")
                logger.info(f"   Repr: {repr(onsite_value)}")
                
                # Also check all fields that might contain remote/onsite info
                logger.info(f"\nOther potentially relevant fields:")
                for field in ['employmentType', 'benefits', 'customText1', 'customText2', 'customText3']:
                    value = job.get(field)
                    if value:
                        logger.info(f"  {field}: {value}")
                
                # Test the mapping function
                logger.info(f"\n🔄 Testing _map_remote_type function:")
                mapped_value = xml_service._map_remote_type(onsite_value)
                logger.info(f"   Input: {repr(onsite_value)}")
                logger.info(f"   Output: {mapped_value}")
                
                # Show the full job data for inspection
                logger.info(f"\n📋 Full job data (formatted):")
                logger.info(json.dumps(job, indent=2, default=str))
                
            else:
                logger.error("❌ Job 34219 not found in Bullhorn!")
                
                # Try to find it in tearsheets
                logger.info("\nSearching for job 34219 in tearsheets...")
                tearsheets = {
                    1256: "Ottawa",
                    1264: "VMS", 
                    1499: "Clover",
                    1257: "Chicago",
                    1258: "Cleveland"
                }
                
                for tearsheet_id, name in tearsheets.items():
                    jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
                    if jobs:
                        for j in jobs:
                            if j.get('id') == 34219:
                                logger.info(f"✅ Found job 34219 in {name} tearsheet!")
                                logger.info(f"   onSite: {j.get('onSite')}")
                                logger.info(f"   Full data: {json.dumps(j, indent=2, default=str)}")
                                break
            
            logger.info("\n" + "=" * 80)
            
        except Exception as e:
            logger.error(f"Error: {str(e)}", exc_info=True)

if __name__ == "__main__":
    main()