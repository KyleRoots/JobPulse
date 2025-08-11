#!/usr/bin/env python3
"""
Debug the specific mapping issue with job 34226
"""
import logging
import sys
import os
import json

# Add the current directory to the path so we can import our services
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def debug_job_34226_mapping():
    """Debug the exact mapping process for job 34226"""
    try:
        logger.info("=== DEBUGGING JOB 34226 MAPPING PROCESS ===")
        
        # Initialize services
        bullhorn_service = BullhornService()
        xml_service = XMLIntegrationService()
        
        # Authenticate with Bullhorn
        logger.info("Authenticating with Bullhorn...")
        if not bullhorn_service.authenticate():
            logger.error("Failed to authenticate with Bullhorn")
            return False
        
        # Get job 34226 from Bullhorn
        logger.info("Fetching job 34226 from Bullhorn...")
        job = bullhorn_service.get_job_by_id(34226)
        
        if not job:
            logger.error("Job 34226 not found in Bullhorn")
            return False
        
        # Log the complete job data structure
        logger.info("=== BULLHORN JOB 34226 COMPLETE DATA ===")
        logger.info(f"Job title: {job.get('title', 'Not found')}")
        logger.info(f"Job ID: {job.get('id', 'Not found')}")
        
        # Focus on the onSite field specifically
        onsite_raw = job.get('onSite', 'NOT_FOUND')
        logger.info(f"onSite field (raw): {onsite_raw}")
        logger.info(f"onSite field type: {type(onsite_raw)}")
        
        if isinstance(onsite_raw, list):
            logger.info(f"onSite is a list with {len(onsite_raw)} items:")
            for i, item in enumerate(onsite_raw):
                logger.info(f"  [{i}]: {item} (type: {type(item)})")
        
        # Test the mapping function directly
        logger.info("=== TESTING MAPPING FUNCTION ===")
        mapped_result = xml_service._map_remote_type(onsite_raw)
        logger.info(f"Mapped result: '{mapped_result}'")
        
        # Check if the mapped result is correct
        if mapped_result.lower() == 'remote':
            logger.info("✅ Mapping function produces correct result: 'Remote'")
        else:
            logger.error(f"❌ Mapping function produces INCORRECT result: '{mapped_result}'")
            logger.error(f"Expected: 'Remote', Got: '{mapped_result}'")
        
        # Test the full job mapping process
        logger.info("=== TESTING FULL JOB MAPPING ===")
        try:
            xml_job = xml_service.map_bullhorn_job_to_xml(job, skip_ai_classification=True)
            remotetype_in_mapping = xml_job.get('remotetype', 'NOT_FOUND')
            logger.info(f"Full mapping remotetype result: '{remotetype_in_mapping}'")
            
            if remotetype_in_mapping.lower() == 'remote':
                logger.info("✅ Full mapping produces correct remotetype: 'Remote'")
            else:
                logger.error(f"❌ Full mapping produces INCORRECT remotetype: '{remotetype_in_mapping}'")
                
        except Exception as e:
            logger.error(f"Error in full mapping: {e}")
        
        # Check what the XML currently contains for job 34226
        logger.info("=== CHECKING CURRENT XML CONTENT ===")
        try:
            with open('myticas-job-feed.xml', 'r', encoding='utf-8') as f:
                xml_content = f.read()
                
            # Find job 34226's remotetype in current XML
            import re
            pattern = r'<bhatsid><!\[CDATA\[ 34226 \]\]></bhatsid>.*?<remotetype><!\[CDATA\[ ([^]]+) \]\]></remotetype>'
            match = re.search(pattern, xml_content, re.DOTALL)
            
            if match:
                current_remotetype = match.group(1)
                logger.info(f"Current XML remotetype for job 34226: '{current_remotetype}'")
                
                if current_remotetype.lower() == 'remote':
                    logger.info("✅ Current XML is correct!")
                else:
                    logger.error(f"❌ Current XML is INCORRECT: '{current_remotetype}' (should be 'Remote')")
            else:
                logger.warning("Could not find job 34226 in current XML")
                
        except Exception as e:
            logger.error(f"Error reading XML: {e}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error debugging job 34226: {e}")
        return False

def main():
    """Main debugging function"""
    logger.info("🔍 Starting detailed diagnosis of job 34226 mapping issue...")
    
    success = debug_job_34226_mapping()
    
    if success:
        logger.info("🎯 Debugging completed - check logs for detailed analysis")
    else:
        logger.error("❌ Debugging failed")
    
    return success

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)