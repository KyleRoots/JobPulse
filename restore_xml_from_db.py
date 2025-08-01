"""Restore XML files from database job information"""

import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
from app import app, db, BullhornMonitor, GlobalSettings
from xml_integration_service import XMLIntegrationService
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def restore_xml_files():
    """Restore XML files from database monitor snapshots"""
    
    with app.app_context():
        # Get all monitors
        monitors = BullhornMonitor.query.all()
        all_jobs = {}
        
        logger.info("Collecting jobs from monitor snapshots...")
        
        for monitor in monitors:
            if monitor.last_job_snapshot:
                try:
                    # Handle different snapshot formats
                    snapshot_data = monitor.last_job_snapshot
                    if isinstance(snapshot_data, str):
                        snapshot_data = json.loads(snapshot_data)
                    
                    # Extract jobs based on data structure
                    if isinstance(snapshot_data, list):
                        # List of job IDs
                        logger.info(f"{monitor.name}: Found {len(snapshot_data)} job IDs")
                        # We'll need to get full job data from somewhere
                    elif isinstance(snapshot_data, dict) and 'job_ids' in snapshot_data:
                        # Dictionary with job_ids
                        job_ids = snapshot_data['job_ids']
                        logger.info(f"{monitor.name}: Found {len(job_ids)} job IDs")
                    elif isinstance(snapshot_data, dict) and 'jobs' in snapshot_data:
                        # Dictionary with full job data
                        jobs = snapshot_data['jobs']
                        logger.info(f"{monitor.name}: Found {len(jobs)} jobs with data")
                        for job in jobs:
                            if 'id' in job:
                                all_jobs[str(job['id'])] = job
                                
                except Exception as e:
                    logger.error(f"Error processing {monitor.name}: {e}")
        
        logger.info(f"Total unique jobs collected: {len(all_jobs)}")
        
        if not all_jobs:
            # Try to restore from the last known good state
            logger.info("No jobs found in snapshots. Creating minimal XML files...")
            
            # Create minimal valid XML structure
            root = ET.Element('source')
            ET.SubElement(root, 'publisherurl').text = 'https://myticas.com'
            
            # Save both XML files
            for filename in ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']:
                tree = ET.ElementTree(root)
                tree.write(filename, encoding='UTF-8', xml_declaration=True)
                logger.info(f"Created minimal {filename}")
            
            return
        
        # Create XML structure with collected jobs
        xml_service = XMLIntegrationService()
        
        # For now, create a simple XML with job IDs
        root = ET.Element('source')
        ET.SubElement(root, 'publisherurl').text = 'https://myticas.com'
        
        for job_id, job_data in all_jobs.items():
            job_elem = ET.SubElement(root, 'job')
            
            # Add basic job information
            ET.SubElement(job_elem, 'bhatsid').text = str(job_id)
            ET.SubElement(job_elem, 'title').text = job_data.get('title', f'Job {job_id}')
            ET.SubElement(job_elem, 'company').text = 'Myticas Consulting'
            ET.SubElement(job_elem, 'date').text = datetime.now().strftime('%B %d, %Y')
            ET.SubElement(job_elem, 'referencenumber').text = f'REF-{job_id}'
            ET.SubElement(job_elem, 'url').text = 'https://myticas.com/'
            ET.SubElement(job_elem, 'description').text = job_data.get('description', 'Job description')
            ET.SubElement(job_elem, 'jobtype').text = job_data.get('employmentType', 'Contract')
            ET.SubElement(job_elem, 'city').text = job_data.get('city', '')
            ET.SubElement(job_elem, 'state').text = job_data.get('state', '')
            ET.SubElement(job_elem, 'country').text = job_data.get('country', 'United States')
            
        # Pretty print and save
        xml_str = ET.tostring(root, encoding='unicode')
        dom = minidom.parseString(xml_str)
        pretty_xml = dom.toprettyxml(indent="  ", encoding='UTF-8')
        
        # Remove extra blank lines
        lines = pretty_xml.decode('utf-8').split('\n')
        non_empty_lines = [line for line in lines if line.strip()]
        final_xml = '\n'.join(non_empty_lines)
        
        # Save both files
        for filename in ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(final_xml)
            logger.info(f"Restored {filename} with {len(all_jobs)} jobs")

if __name__ == "__main__":
    restore_xml_files()