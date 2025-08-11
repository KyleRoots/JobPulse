#!/usr/bin/env python3
"""
Script to force a comprehensive re-sync of all jobs with corrected field mappings.
This will fix issues like job 34219 having incorrect remotetype values.
"""

import logging
import sys
from datetime import datetime
from xml.etree import ElementTree as ET
import requests

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Force a comprehensive re-sync of all jobs"""
    from app import app
    from bullhorn_service import BullhornService
    from xml_integration_service import XMLIntegrationService
    
    with app.app_context():
        try:
            logger.info("=" * 80)
            logger.info("FIELD MAPPING FIX - Starting comprehensive re-sync")
            logger.info("This will update all jobs with correct field mappings:")
            logger.info("  - publicDescription → <description>")
            logger.info("  - onSite → <remotetype>") 
            logger.info("  - countryID → <country>")
            logger.info("  - assignments → <assignedrecruiter>")
            logger.info("=" * 80)
            
            # Initialize services
            bullhorn = BullhornService()
            xml_service = XMLIntegrationService()
            
            # Get all tearsheet jobs
            all_jobs = []
            tearsheets = {
                1256: "Ottawa Sponsored Jobs",
                1264: "VMS Sponsored Jobs", 
                1499: "Clover Sponsored Jobs",
                1257: "Chicago Sponsored Jobs",
                1258: "Cleveland Sponsored Jobs"
            }
            
            for tearsheet_id, name in tearsheets.items():
                logger.info(f"\nFetching jobs from {name} (ID: {tearsheet_id})")
                jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
                if jobs:
                    logger.info(f"  Found {len(jobs)} jobs")
                    all_jobs.extend(jobs)
                else:
                    logger.info(f"  No jobs found")
            
            logger.info(f"\nTotal jobs to update: {len(all_jobs)}")
            
            # Load current XML
            xml_file = 'myticas-job-feed.xml'
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            # Remove all existing jobs
            for job in root.findall('.//job'):
                root.remove(job)
            
            logger.info(f"Cleared all existing jobs from {xml_file}")
            
            # Re-add all jobs with correct field mappings
            updated_count = 0
            field_fixes = []
            
            for job in all_jobs:
                job_id = job.get('id')
                title = job.get('title', 'Unknown')
                
                # Get full job details with corrected fields
                full_job = bullhorn.get_job_by_id(job_id)
                if not full_job:
                    logger.warning(f"Could not fetch full details for job {job_id}")
                    continue
                
                # Track field corrections
                onsite = full_job.get('onSite', '')
                address = full_job.get('address', {})
                country = address.get('countryID', '') if address else ''
                
                logger.info(f"Job {job_id} ({title}):")
                logger.info(f"  - onSite: {onsite}")
                logger.info(f"  - countryID: {country}")
                
                # Add job with correct mappings
                success = xml_service.add_job_to_xml(full_job, xml_file)
                if success:
                    updated_count += 1
                    
                    # Track specific fixes
                    if job_id == 34219:
                        logger.info(f"  ✓ Fixed job 34219 - remotetype should now be 'Remote' based on onSite='{onsite}'")
                        field_fixes.append(f"Job 34219: remotetype fixed to match onSite field")
            
            logger.info(f"\n✅ Successfully updated {updated_count}/{len(all_jobs)} jobs with correct field mappings")
            
            # Also update scheduled XML
            logger.info("\nUpdating scheduled XML file...")
            import shutil
            shutil.copy2(xml_file, 'myticas-job-feed-scheduled.xml')
            logger.info("✅ Scheduled XML updated")
            
            # Upload to SFTP
            logger.info("\nUploading corrected XML files to SFTP...")
            from ftp_service import FTPService
            ftp_service = FTPService()
            
            if ftp_service.upload_xml_file(xml_file):
                logger.info(f"✅ Uploaded {xml_file} to SFTP")
            
            if ftp_service.upload_xml_file('myticas-job-feed-scheduled.xml'):
                logger.info(f"✅ Uploaded myticas-job-feed-scheduled.xml to SFTP")
            
            logger.info("\n" + "=" * 80)
            logger.info("FIELD MAPPING FIX COMPLETE!")
            logger.info(f"Updated {updated_count} jobs with correct field mappings")
            if field_fixes:
                logger.info("\nSpecific fixes applied:")
                for fix in field_fixes:
                    logger.info(f"  - {fix}")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"Error during field mapping fix: {str(e)}", exc_info=True)
            sys.exit(1)

if __name__ == "__main__":
    main()