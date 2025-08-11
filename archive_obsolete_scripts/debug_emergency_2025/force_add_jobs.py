#!/usr/bin/env python
"""
Force add all jobs from Bullhorn to XML files
"""
import os
os.environ['OPENAI_API_KEY'] = os.environ.get('OPENAI_API_KEY', '')

from app import app, db
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def force_add_all_jobs():
    """Force add all jobs from Bullhorn to XML files"""
    with app.app_context():
        try:
            # Set Bullhorn credentials from environment
            os.environ['BULLHORN_CLIENT_ID'] = os.environ.get('BULLHORN_CLIENT_ID', '')
            os.environ['BULLHORN_CLIENT_SECRET'] = os.environ.get('BULLHORN_CLIENT_SECRET', '')
            os.environ['BULLHORN_USERNAME'] = os.environ.get('BULLHORN_USERNAME', '')
            os.environ['BULLHORN_PASSWORD'] = os.environ.get('BULLHORN_PASSWORD', '')
            
            # Initialize services
            bullhorn = BullhornService()
            
            # Authenticate with Bullhorn
            if not bullhorn.authenticate():
                logger.error("Failed to authenticate with Bullhorn")
                return
            
            xml_service = XMLIntegrationService()
            
            # Get all jobs from tearsheets
            tearsheets = [
                (1256, "Ottawa Sponsored Jobs"),
                (1264, "VMS Sponsored Jobs"),
                (1499, "Clover Sponsored Jobs"),
                (1258, "Cleveland Sponsored Jobs"),
                (1257, "Chicago Sponsored Jobs")
            ]
            
            all_jobs = []
            for tearsheet_id, name in tearsheets:
                logger.info(f"Fetching jobs from {name} (ID: {tearsheet_id})")
                jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
                if jobs:
                    logger.info(f"  Found {len(jobs)} jobs")
                    all_jobs.extend(jobs)
                else:
                    logger.info(f"  No jobs found")
            
            logger.info(f"\nTotal jobs found: {len(all_jobs)}")
            
            # Add all jobs to both XML files
            xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
            
            for xml_file in xml_files:
                logger.info(f"\nProcessing {xml_file}...")
                
                # Get current job IDs in XML
                import lxml.etree as etree
                parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
                tree = etree.parse(xml_file, parser)
                root = tree.getroot()
                
                xml_job_ids = set()
                for url_elem in root.xpath('.//url'):
                    if url_elem.text and 'jobId=' in url_elem.text:
                        job_id = url_elem.text.split('jobId=')[-1].strip()
                        if job_id:
                            xml_job_ids.add(job_id)
                
                logger.info(f"  Current jobs in XML: {len(xml_job_ids)}")
                
                # Add missing jobs
                added_count = 0
                for job in all_jobs:
                    job_id = str(job.get('id'))
                    if job_id not in xml_job_ids:
                        logger.info(f"  Adding job {job_id}: {job.get('title')}")
                        try:
                            xml_service.add_job_to_xml(xml_file, job)
                            added_count += 1
                        except Exception as e:
                            logger.error(f"  Failed to add job {job_id}: {e}")
                
                logger.info(f"  Added {added_count} jobs to {xml_file}")
            
            logger.info("\n✅ Job addition complete!")
            
        except Exception as e:
            logger.error(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    force_add_all_jobs()