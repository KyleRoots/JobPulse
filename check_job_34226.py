#!/usr/bin/env python3
"""
Check the specific mapping for job 34226 between Bullhorn and XML
"""
import requests
from lxml import etree
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def check_job_34226_in_xml():
    """Check job 34226 in the live XML file"""
    try:
        logger.info("Checking job 34226 in live XML...")
        response = requests.get("https://myticas.com/myticas-job-feed.xml", timeout=30)
        
        if response.status_code == 200:
            # Parse XML
            parser = etree.XMLParser(strip_cdata=False)
            tree = etree.fromstring(response.content, parser)
            
            # Find job 34226
            jobs = tree.xpath('//job')
            
            for job in jobs:
                bhatsid_elem = job.find('bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text:
                    bhatsid_text = bhatsid_elem.text.strip()
                    # Remove CDATA wrapper if present
                    if '<![CDATA[' in bhatsid_text:
                        bhatsid_text = bhatsid_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                    
                    if bhatsid_text == '34226':
                        logger.info(f"Found job 34226 in XML!")
                        
                        # Get title
                        title_elem = job.find('title')
                        title = title_elem.text if title_elem is not None else 'Unknown'
                        if '<![CDATA[' in title:
                            title = title.replace('<![CDATA[', '').replace(']]>', '').strip()
                        
                        # Get remotetype
                        remotetype_elem = job.find('remotetype')
                        remotetype = remotetype_elem.text if remotetype_elem is not None else 'Not found'
                        if '<![CDATA[' in remotetype:
                            remotetype = remotetype.replace('<![CDATA[', '').replace(']]>', '').strip()
                        
                        logger.info(f"Job 34226 Details:")
                        logger.info(f"  Title: {title}")
                        logger.info(f"  XML remotetype: '{remotetype}'")
                        logger.info(f"  Expected from Bullhorn onSite: 'Remote'")
                        
                        if remotetype.lower() != 'remote':
                            logger.warning(f"❌ DISCREPANCY FOUND!")
                            logger.warning(f"   XML shows: '{remotetype}'")
                            logger.warning(f"   Bullhorn shows: 'Remote'")
                            return False
                        else:
                            logger.info(f"✅ MATCH: Both show Remote")
                            return True
            
            logger.error("Job 34226 not found in XML!")
            return False
            
        else:
            logger.error(f"Failed to fetch XML: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Error checking job 34226: {e}")
        return False

def main():
    """Check the discrepancy for job 34226"""
    logger.info("=== CHECKING JOB 34226 MAPPING DISCREPANCY ===")
    
    success = check_job_34226_in_xml()
    
    if not success:
        logger.info("⚠️ There appears to be a mapping discrepancy for job 34226")
        logger.info("Bullhorn shows: Work Location Requirements = Remote")
        logger.info("This should map to: remotetype = 'Remote' in XML")

if __name__ == "__main__":
    main()