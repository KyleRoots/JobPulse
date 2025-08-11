#!/usr/bin/env python3
"""
Debug script to check the actual country field values from Bullhorn
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
    """Check country field values from Bullhorn"""
    from app import app
    
    with app.app_context():
        try:
            logger.info("=" * 80)
            logger.info("DEBUGGING COUNTRY FIELD VALUES")
            logger.info("=" * 80)
            
            # Import after app context
            from bullhorn_service import BullhornService
            
            # Initialize service
            bullhorn = BullhornService()
            
            # Get tearsheet jobs to check a sample
            logger.info("\nFetching sample jobs from tearsheets...")
            
            tearsheets = {
                1256: "Ottawa",
                1264: "VMS", 
                1499: "Clover",
                1257: "Chicago"
            }
            
            sample_jobs = []
            for tearsheet_id, name in tearsheets.items():
                jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
                if jobs and len(jobs) > 0:
                    # Take first job from each tearsheet
                    sample_jobs.append((name, jobs[0]))
                    if len(sample_jobs) >= 3:  # Get 3 sample jobs
                        break
            
            if sample_jobs:
                logger.info(f"\n✅ Found {len(sample_jobs)} sample jobs to check")
                
                for tearsheet_name, job in sample_jobs:
                    logger.info(f"\n{'='*50}")
                    logger.info(f"Job ID: {job.get('id')}")
                    logger.info(f"Title: {job.get('title')}")
                    logger.info(f"Tearsheet: {tearsheet_name}")
                    
                    # Check address field structure
                    address = job.get('address', {})
                    logger.info(f"\n📍 Address field contents:")
                    if address:
                        logger.info(f"  city: {address.get('city')}")
                        logger.info(f"  state: {address.get('state')}")
                        logger.info(f"  countryID: {address.get('countryID')}")
                        logger.info(f"  countryName: {address.get('countryName')}")
                        logger.info(f"  countryCode: {address.get('countryCode')}")
                        
                        # Show full address data
                        logger.info(f"\n  Full address data:")
                        logger.info(json.dumps(address, indent=4, default=str))
                    else:
                        logger.info("  No address data")
                    
            else:
                logger.error("❌ No jobs found in tearsheets!")
            
            logger.info("\n" + "=" * 80)
            
        except Exception as e:
            logger.error(f"Error: {str(e)}", exc_info=True)

if __name__ == "__main__":
    main()