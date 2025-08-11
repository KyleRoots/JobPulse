#!/usr/bin/env python3
"""
Monitor the live monitoring cycle results
"""
import time
import requests
from lxml import etree
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def check_live_xml_status():
    """Check the current status of the live XML"""
    try:
        response = requests.get("https://myticas.com/myticas-job-feed.xml", timeout=30)
        if response.status_code == 200:
            # Parse XML
            parser = etree.XMLParser(strip_cdata=False)
            tree = etree.fromstring(response.content, parser)
            
            jobs = tree.xpath('//job')
            job_ids = []
            duplicates = {}
            
            for job in jobs:
                bhatsid_elem = job.find('bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text:
                    job_id = bhatsid_elem.text.strip()
                    job_ids.append(job_id)
                    
                    if job_id in duplicates:
                        duplicates[job_id] += 1
                    else:
                        duplicates[job_id] = 1
            
            duplicate_count = sum(1 for count in duplicates.values() if count > 1)
            unique_jobs = len(duplicates)
            
            logger.info(f"Live XML Status:")
            logger.info(f"  Total jobs: {len(jobs)}")
            logger.info(f"  Unique job IDs: {unique_jobs}")
            logger.info(f"  Duplicate job IDs: {duplicate_count}")
            
            if duplicate_count > 0:
                logger.warning("Duplicates found:")
                for job_id, count in duplicates.items():
                    if count > 1:
                        logger.warning(f"  Job {job_id}: {count} copies")
            
            return {
                'total_jobs': len(jobs),
                'unique_jobs': unique_jobs,
                'duplicates': duplicate_count,
                'status': 'success'
            }
        else:
            logger.error(f"Failed to fetch XML: {response.status_code}")
            return {'status': 'error', 'error': f'HTTP {response.status_code}'}
            
    except Exception as e:
        logger.error(f"Error checking live XML: {e}")
        return {'status': 'error', 'error': str(e)}

def main():
    """Monitor the monitoring cycle results"""
    logger.info("=== MONITORING LIVE CYCLE RESULTS ===")
    
    # Check current status
    result = check_live_xml_status()
    
    if result['status'] == 'success':
        print(f"\n📊 CURRENT STATUS:")
        print(f"Total jobs: {result['total_jobs']}")
        print(f"Unique jobs: {result['unique_jobs']}")
        print(f"Duplicates: {result['duplicates']}")
        
        if result['duplicates'] == 0:
            print("✅ No duplicates - XML is clean!")
        else:
            print(f"⚠️ Found {result['duplicates']} duplicate job IDs")
        
        # Expected count is around 52 based on tearsheets
        expected = 52
        if result['unique_jobs'] == expected:
            print(f"🎯 Perfect match! {result['unique_jobs']} jobs matches expected {expected}")
        elif abs(result['unique_jobs'] - expected) <= 2:
            print(f"✅ Very close! {result['unique_jobs']} jobs vs expected {expected} (within tolerance)")
        else:
            print(f"📋 Job count: {result['unique_jobs']} vs expected ~{expected}")
    else:
        print(f"❌ Error: {result['error']}")

if __name__ == "__main__":
    main()