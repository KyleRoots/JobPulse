#!/usr/bin/env python3
"""
Script to update all jobs in XML files with proper country names instead of IDs
"""

import logging
from datetime import datetime

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Force update all jobs with country names"""
    from app import app
    
    with app.app_context():
        try:
            logger.info("=" * 80)
            logger.info("UPDATING COUNTRY FIELDS FROM IDS TO NAMES")
            logger.info("=" * 80)
            
            # Import services
            from bullhorn_service import BullhornService
            from xml_integration_service import XMLIntegrationService
            
            # Initialize services
            bullhorn = BullhornService()
            xml_service = XMLIntegrationService()
            
            # Get all jobs from tearsheets
            logger.info("\nFetching all jobs from tearsheets...")
            
            tearsheets = {
                1256: "Ottawa Sponsored Jobs",
                1264: "VMS Sponsored Jobs", 
                1499: "Clover Sponsored Jobs",
                1257: "Chicago Sponsored Jobs",
                1258: "Cleveland Sponsored Jobs"
            }
            
            all_jobs = []
            for tearsheet_id, name in tearsheets.items():
                logger.info(f"Fetching jobs from {name} (ID: {tearsheet_id})...")
                jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
                if jobs:
                    logger.info(f"  Found {len(jobs)} jobs")
                    all_jobs.extend(jobs)
                else:
                    logger.info(f"  No jobs found")
            
            logger.info(f"\n✅ Total jobs found: {len(all_jobs)}")
            
            # Process each XML file
            xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
            
            for xml_file in xml_files:
                logger.info(f"\n{'='*50}")
                logger.info(f"Processing {xml_file}...")
                
                # Use comprehensive sync to update all jobs
                added, removed, modified = xml_service.comprehensive_sync(
                    xml_file, 
                    all_jobs, 
                    is_scheduled=False,
                    preserve_references=True
                )
                
                logger.info(f"  Added: {added} jobs")
                logger.info(f"  Removed: {removed} jobs")  
                logger.info(f"  Modified: {modified} jobs")
                
                # Check a sample job to verify country field
                logger.info(f"\nVerifying country field update in {xml_file}...")
                with open(xml_file, 'r') as f:
                    content = f.read()
                    # Look for country fields
                    import re
                    countries = re.findall(r'<country><!\[CDATA\[(.*?)\]\]></country>', content)
                    if countries:
                        # Show first 5 unique country values
                        unique_countries = list(set(countries))[:5]
                        logger.info(f"  Sample country values: {unique_countries}")
                        
                        # Check if any numeric IDs remain
                        numeric_countries = [c for c in countries if c.strip().isdigit()]
                        if numeric_countries:
                            logger.warning(f"  ⚠️ Still have {len(numeric_countries)} numeric country IDs")
                        else:
                            logger.info(f"  ✅ All country fields now use names!")
            
            logger.info("\n" + "=" * 80)
            logger.info("COUNTRY FIELD UPDATE COMPLETE")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"Error: {str(e)}", exc_info=True)

if __name__ == "__main__":
    main()