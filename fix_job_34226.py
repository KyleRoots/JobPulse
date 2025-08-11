#!/usr/bin/env python3
"""
Fix the remotetype mapping for job 34226 - force an update to correct the discrepancy
"""
import logging
import sys
import os

# Add the current directory to the path so we can import our services
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fix_job_34226():
    """Fix the remotetype discrepancy for job 34226"""
    try:
        logger.info("=== FIXING JOB 34226 REMOTETYPE DISCREPANCY ===")
        
        # Initialize services
        bullhorn_service = BullhornService()
        xml_service = XMLIntegrationService()
        
        # Authenticate with Bullhorn
        logger.info("Authenticating with Bullhorn...")
        if not bullhorn_service.authenticate():
            logger.error("Failed to authenticate with Bullhorn")
            return False
        
        # Get current job 34226 from Bullhorn
        logger.info("Fetching job 34226 from Bullhorn...")
        job = bullhorn_service.get_job_by_id(34226)
        
        if not job:
            logger.error("Job 34226 not found in Bullhorn")
            return False
        
        # Log the current onSite value
        onsite_value = job.get('onSite', 'Not found')
        logger.info(f"Bullhorn job 34226 onSite value: {onsite_value}")
        
        # Force flag as modified to ensure it gets updated
        job['_monitor_flagged_as_modified'] = True
        
        # Update the job in XML with fresh Bullhorn data
        logger.info("Updating job 34226 in XML with correct remotetype...")
        xml_file = "myticas-job-feed.xml"
        
        success = xml_service.update_job_in_xml(xml_file, job)
        
        if success:
            logger.info("✅ Job 34226 updated successfully")
            
            # Verify the fix
            logger.info("Verifying the remotetype fix...")
            with open(xml_file, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Find job 34226 in XML and check remotetype
            if '34226' in content:
                # Extract the remotetype for job 34226
                import re
                pattern = r'<bhatsid><!\[CDATA\[ 34226 \]\]></bhatsid>.*?<remotetype><!\[CDATA\[ ([^]]+) \]\]></remotetype>'
                match = re.search(pattern, content, re.DOTALL)
                
                if match:
                    remotetype = match.group(1)
                    logger.info(f"Updated XML remotetype for job 34226: '{remotetype}'")
                    
                    if remotetype.lower() == 'remote':
                        logger.info("🎉 SUCCESS: Discrepancy fixed!")
                        return True
                    else:
                        logger.warning(f"⚠️ Still showing incorrect value: '{remotetype}'")
                        return False
                else:
                    logger.warning("Could not find remotetype in updated XML")
                    return False
            else:
                logger.error("Job 34226 not found in updated XML")
                return False
        else:
            logger.error("❌ Failed to update job 34226")
            return False
            
    except Exception as e:
        logger.error(f"Error fixing job 34226: {e}")
        return False

def main():
    """Main function"""
    success = fix_job_34226()
    
    if success:
        logger.info("🎯 Job 34226 remotetype discrepancy has been resolved")
    else:
        logger.error("❌ Failed to resolve job 34226 remotetype discrepancy")
    
    return success

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)