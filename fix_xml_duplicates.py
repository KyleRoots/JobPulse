"""Fix duplicate jobs in the XML file"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_xml_duplicates(input_file, output_file):
    """Remove duplicate job entries from XML file"""
    
    logger.info(f"Processing {input_file}")
    
    # Parse the XML
    tree = ET.parse(input_file)
    root = tree.getroot()
    
    # Find all jobs
    all_jobs = root.findall('.//job')
    logger.info(f"Total jobs found: {len(all_jobs)}")
    
    # Track unique jobs by bhatsid
    unique_jobs = {}
    jobs_to_remove = []
    
    for job in all_jobs:
        bhatsid_elem = job.find('bhatsid')
        if bhatsid_elem is not None and bhatsid_elem.text:
            job_id = bhatsid_elem.text.strip()
            
            if job_id not in unique_jobs:
                unique_jobs[job_id] = job
            else:
                # This is a duplicate
                jobs_to_remove.append(job)
                logger.info(f"Found duplicate job: {job_id}")
    
    # Remove duplicates
    for job in jobs_to_remove:
        root.remove(job)
    
    logger.info(f"Removed {len(jobs_to_remove)} duplicate jobs")
    logger.info(f"Unique jobs remaining: {len(unique_jobs)}")
    
    # Create pretty formatted XML string
    xml_str = ET.tostring(root, encoding='unicode')
    
    # Parse with minidom for pretty printing
    dom = minidom.parseString(xml_str)
    pretty_xml = dom.toprettyxml(indent="  ", encoding='UTF-8')
    
    # Remove extra blank lines
    lines = pretty_xml.decode('utf-8').split('\n')
    non_empty_lines = [line for line in lines if line.strip()]
    final_xml = '\n'.join(non_empty_lines)
    
    # Write to file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(final_xml)
    
    logger.info(f"Fixed XML saved to {output_file}")
    
    return len(unique_jobs), len(jobs_to_remove)

if __name__ == "__main__":
    # Fix the main job feed
    unique_count, removed_count = fix_xml_duplicates('myticas-job-feed.xml', 'myticas-job-feed.xml')
    
    # Also check and fix the scheduled version
    try:
        unique_count2, removed_count2 = fix_xml_duplicates('myticas-job-feed-scheduled.xml', 'myticas-job-feed-scheduled.xml')
        logger.info(f"Scheduled XML: {unique_count2} unique jobs, {removed_count2} duplicates removed")
    except Exception as e:
        logger.warning(f"Could not process scheduled XML: {e}")
    
    logger.info("XML duplicate removal complete!")