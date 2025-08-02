"""Restore XML files from database job data"""

import os
import json
from datetime import datetime
from lxml import etree
from app import app, db, BullhornMonitor, BullhornActivity, GlobalSettings
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def restore_xml_files():
    """Restore XML files using existing job data from the database"""
    
    with app.app_context():
        try:
            # Get the most recent monitor activity with job data
            recent_activity = BullhornActivity.query.filter(
                BullhornActivity.jobs_data.isnot(None)
            ).order_by(BullhornActivity.created_at.desc()).first()
            
            if not recent_activity:
                logger.error("No recent activity with job data found")
                return False
            
            # Parse the jobs data
            try:
                if isinstance(recent_activity.jobs_data, str):
                    jobs_data = json.loads(recent_activity.jobs_data)
                else:
                    jobs_data = recent_activity.jobs_data
                    
                logger.info(f"Found {len(jobs_data)} jobs from recent activity")
            except Exception as e:
                logger.error(f"Error parsing jobs data: {e}")
                return False
            
            # If we don't have enough jobs, fetch from Bullhorn
            if len(jobs_data) < 50:
                logger.info("Not enough jobs in database, fetching from Bullhorn...")
                
                # Get credentials
                credentials = {}
                settings = GlobalSettings.query.filter(GlobalSettings.setting_key.in_([
                    'bullhorn_client_id', 'bullhorn_client_secret', 
                    'bullhorn_username', 'bullhorn_password'
                ])).all()
                
                for setting in settings:
                    if setting.setting_value:
                        credentials[setting.setting_key] = setting.setting_value
                
                if all(k in credentials for k in ['bullhorn_client_id', 'bullhorn_client_secret', 
                                                  'bullhorn_username', 'bullhorn_password']):
                    bullhorn = BullhornService(
                        client_id=credentials['bullhorn_client_id'],
                        client_secret=credentials['bullhorn_client_secret'],
                        username=credentials['bullhorn_username'],
                        password=credentials['bullhorn_password']
                    )
                    
                    if bullhorn.authenticate():
                        # Get all tearsheet jobs
                        all_jobs = []
                        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
                        
                        for monitor in monitors:
                            try:
                                jobs = bullhorn.get_tearsheet_jobs(monitor.tearsheet_id)
                                all_jobs.extend(jobs)
                                logger.info(f"Got {len(jobs)} jobs from tearsheet {monitor.tearsheet_id}")
                            except:
                                continue
                        
                        # Get full details for each job
                        jobs_data = []
                        for job in all_jobs:
                            try:
                                full_job = bullhorn.get_job_by_id(int(job['id']))
                                if full_job:
                                    jobs_data.append(full_job)
                            except:
                                continue
                        
                        logger.info(f"Fetched {len(jobs_data)} jobs from Bullhorn")
            
            if not jobs_data:
                logger.error("No job data available")
                return False
            
            # Initialize XML service
            xml_service = XMLIntegrationService()
            
            # Create XML structure
            root = etree.Element('source')
            root.text = '\n  '
            
            publisher_url = etree.SubElement(root, 'publisherurl')
            publisher_url.text = 'https://myticas.com'
            publisher_url.tail = '\n  '
            
            # Add each job
            job_count = 0
            for job_data in jobs_data:
                try:
                    # Map job to XML format
                    xml_job = xml_service.map_bullhorn_job_to_xml(job_data)
                    
                    # Create job element
                    job_elem = etree.SubElement(root, 'job')
                    job_elem.text = '\n    '
                    job_elem.tail = '\n  '
                    
                    # Field order for consistent formatting
                    field_order = ['title', 'company', 'date', 'referencenumber', 'bhatsid', 
                                  'url', 'description', 'jobtype', 'city', 'state', 'country',
                                  'category', 'apply_email', 'remotetype', 'assignedrecruiter',
                                  'jobfunction', 'jobindustries', 'senoritylevel']
                    
                    # Add fields in order
                    for field in field_order:
                        if field in xml_job:
                            field_elem = etree.SubElement(job_elem, field)
                            field_elem.text = etree.CDATA(f" {xml_job[field]} ")
                            field_elem.tail = '\n    ' if field != field_order[-1] else '\n  '
                    
                    job_count += 1
                    
                except Exception as e:
                    logger.error(f"Error processing job {job_data.get('id', 'unknown')}: {e}")
                    continue
            
            # Fix the last job's tail
            if len(root) > 1:
                root[-1].tail = '\n'
            
            logger.info(f"Created XML with {job_count} jobs")
            
            # Write to both XML files
            for filename in ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']:
                try:
                    with open(filename, 'wb') as f:
                        tree = etree.ElementTree(root)
                        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
                    logger.info(f"Successfully wrote {filename}")
                except Exception as e:
                    logger.error(f"Error writing {filename}: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error in restore process: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    success = restore_xml_files()
    if success:
        logger.info("✅ Successfully restored XML files!")
        # Upload to SFTP
        os.system('python3 upload_xml_files.py')
    else:
        logger.error("❌ Failed to restore XML files")