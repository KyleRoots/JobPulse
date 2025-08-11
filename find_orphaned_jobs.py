#!/usr/bin/env python3
"""
Find orphaned jobs in XML that are not in any Bullhorn tearsheet
"""
import logging
from bullhorn_service import BullhornService
from lxml import etree

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_jobs_from_tearsheets():
    """Get all job IDs from monitored tearsheets"""
    logger.info("=== GETTING JOBS FROM BULLHORN TEARSHEETS ===")
    
    # Monitor configuration from app.py
    monitors = [
        {'tearsheet_id': 2644, 'name': 'Myticas Consulting - Development'},
        {'tearsheet_id': 2645, 'name': 'Myticas Consulting - Infrastructure & DevOps'},
        {'tearsheet_id': 2646, 'name': 'Myticas Consulting - Business Analysis & Project Management'},
        {'tearsheet_id': 2647, 'name': 'Myticas Consulting - Data & Analytics'}
    ]
    
    all_tearsheet_jobs = set()
    bullhorn = BullhornService()
    
    if not bullhorn.authenticate():
        logger.error("Failed to authenticate with Bullhorn")
        return set()
    
    for monitor in monitors:
        tearsheet_id = monitor['tearsheet_id']
        name = monitor['name']
        logger.info(f"Getting jobs from tearsheet {tearsheet_id}: {name}")
        
        jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
        job_ids = {str(job['id']) for job in jobs if job.get('id')}
        
        logger.info(f"  Found {len(job_ids)} jobs in {name}")
        logger.info(f"  Job IDs: {sorted(job_ids)}")
        all_tearsheet_jobs.update(job_ids)
    
    logger.info(f"Total unique jobs across all tearsheets: {len(all_tearsheet_jobs)}")
    logger.info(f"All tearsheet job IDs: {sorted(all_tearsheet_jobs)}")
    return all_tearsheet_jobs

def get_jobs_from_xml():
    """Get all job IDs from current XML file"""
    logger.info("=== GETTING JOBS FROM XML ===")
    
    xml_job_ids = set()
    
    try:
        with open('live_xml_download.xml', 'rb') as f:
            parser = etree.XMLParser(strip_cdata=False)
            tree = etree.parse(f, parser)
        
        # Find all job elements and extract bhatsid
        jobs = tree.xpath('//job')
        for job in jobs:
            bhatsid_elem = job.find('bhatsid')
            if bhatsid_elem is not None and bhatsid_elem.text:
                # Extract job ID from CDATA
                job_id = bhatsid_elem.text.strip()
                xml_job_ids.add(job_id)
        
        logger.info(f"Found {len(xml_job_ids)} jobs in XML")
        logger.info(f"XML job IDs: {sorted(xml_job_ids)}")
        return xml_job_ids
        
    except Exception as e:
        logger.error(f"Error reading XML: {e}")
        return set()

def find_orphaned_jobs():
    """Find jobs that are in XML but not in any tearsheet"""
    logger.info("=== FINDING ORPHANED JOBS ===")
    
    tearsheet_jobs = get_jobs_from_tearsheets()
    xml_jobs = get_jobs_from_xml()
    
    # Find orphaned jobs (in XML but not in tearsheets)
    orphaned_jobs = xml_jobs - tearsheet_jobs
    
    # Find missing jobs (in tearsheets but not in XML)
    missing_jobs = tearsheet_jobs - xml_jobs
    
    logger.info(f"\n=== RESULTS ===")
    logger.info(f"Jobs in tearsheets: {len(tearsheet_jobs)}")
    logger.info(f"Jobs in XML: {len(xml_jobs)}")
    logger.info(f"Orphaned jobs (in XML, not in tearsheets): {len(orphaned_jobs)}")
    logger.info(f"Missing jobs (in tearsheets, not in XML): {len(missing_jobs)}")
    
    if orphaned_jobs:
        logger.info(f"🚨 ORPHANED JOB IDs: {sorted(orphaned_jobs)}")
        
        # Get details about orphaned jobs from XML
        try:
            with open('live_xml_download.xml', 'rb') as f:
                parser = etree.XMLParser(strip_cdata=False)
                tree = etree.parse(f, parser)
            
            for job_id in sorted(orphaned_jobs):
                jobs = tree.xpath(f'//job[bhatsid[contains(text(), "{job_id}")]]')
                if jobs:
                    job = jobs[0]
                    title_elem = job.find('title')
                    title = title_elem.text if title_elem is not None else "No title"
                    logger.info(f"  Job {job_id}: {title}")
        except Exception as e:
            logger.error(f"Error getting orphaned job details: {e}")
    
    if missing_jobs:
        logger.info(f"⚠️ MISSING JOB IDs (should be added): {sorted(missing_jobs)}")
    
    return orphaned_jobs, missing_jobs

if __name__ == "__main__":
    try:
        orphaned, missing = find_orphaned_jobs()
        
        if orphaned:
            print(f"\n🔥 ACTION REQUIRED: Remove {len(orphaned)} orphaned job(s) from XML")
            print(f"Orphaned job IDs: {sorted(orphaned)}")
        
        if missing:
            print(f"\n➕ ACTION REQUIRED: Add {len(missing)} missing job(s) to XML") 
            print(f"Missing job IDs: {sorted(missing)}")
            
        if not orphaned and not missing:
            print("\n✅ XML is perfectly synchronized with tearsheets")
            
    except Exception as e:
        logger.error(f"Script failed: {e}")
        import traceback
        traceback.print_exc()