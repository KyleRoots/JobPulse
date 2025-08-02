"""Restore XML files with real job data from Bullhorn"""

import os
import json
import logging
from datetime import datetime
from app import app, db, GlobalSettings, BullhornMonitor
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from job_classification_service import JobClassificationService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_bullhorn_credentials():
    """Get Bullhorn credentials from GlobalSettings"""
    with app.app_context():
        credentials = {}
        settings = GlobalSettings.query.filter(GlobalSettings.setting_key.in_([
            'bullhorn_client_id', 'bullhorn_client_secret', 
            'bullhorn_username', 'bullhorn_password'
        ])).all()
        
        for setting in settings:
            if setting.setting_value:
                credentials[setting.setting_key] = setting.setting_value
        
        return credentials

def restore_jobs_with_real_data():
    """Fetch real job data from Bullhorn and restore XML files"""
    
    credentials = get_bullhorn_credentials()
    
    if not all(k in credentials for k in ['bullhorn_client_id', 'bullhorn_client_secret', 
                                          'bullhorn_username', 'bullhorn_password']):
        logger.error("Missing Bullhorn credentials in GlobalSettings")
        return False
    
    try:
        # Initialize services with credentials
        bullhorn_service = BullhornService(
            client_id=credentials['bullhorn_client_id'],
            client_secret=credentials['bullhorn_client_secret'],
            username=credentials['bullhorn_username'],
            password=credentials['bullhorn_password']
        )
        xml_service = XMLIntegrationService()
        classification_service = JobClassificationService()
        
        # Authenticate with Bullhorn
        logger.info("Authenticating with Bullhorn...")
        auth_result = bullhorn_service.authenticate()
        if not auth_result:
            logger.error("Failed to authenticate with Bullhorn")
            return False
        
        # Get all tearsheet IDs from monitors
        with app.app_context():
            monitors = BullhornMonitor.query.filter_by(is_active=True).all()
            tearsheet_ids = [monitor.tearsheet_id for monitor in monitors]
            logger.info(f"Found {len(tearsheet_ids)} active monitors with tearsheets: {tearsheet_ids}")
        
        # Collect all jobs from tearsheets
        all_jobs = {}
        
        for tearsheet_id in tearsheet_ids:
            try:
                logger.info(f"Fetching jobs from tearsheet {tearsheet_id}...")
                jobs = bullhorn_service.get_tearsheet_jobs(tearsheet_id)
                logger.info(f"  Found {len(jobs)} jobs in tearsheet {tearsheet_id}")
                
                # Add jobs to collection (avoid duplicates)
                for job in jobs:
                    job_id = str(job.get('id'))
                    if job_id and job_id not in all_jobs:
                        all_jobs[job_id] = job
                        
            except Exception as e:
                logger.error(f"Error fetching tearsheet {tearsheet_id}: {e}")
                continue
        
        logger.info(f"Total unique jobs collected: {len(all_jobs)}")
        
        if not all_jobs:
            logger.error("No jobs found in Bullhorn tearsheets")
            return False
        
        # Get full job details for each job
        jobs_with_details = []
        
        for job_id, basic_job in all_jobs.items():
            try:
                # Get full job details
                full_job = bullhorn_service.get_job_by_id(int(job_id))
                if full_job:
                    # Ensure we have AI classifications
                    if not full_job.get('customText17'):  # jobfunction
                        try:
                            classification = classification_service.classify_job(
                                full_job.get('title', ''),
                                full_job.get('publicDescription', '')
                            )
                            full_job['customText17'] = classification.get('function', '')
                            full_job['customText18'] = classification.get('industry', '')
                            full_job['customText19'] = classification.get('seniority', '')
                        except Exception as e:
                            logger.warning(f"Classification failed for job {job_id}: {e}")
                            # Set default values if classification fails
                            full_job['customText17'] = ''
                            full_job['customText18'] = ''
                            full_job['customText19'] = ''
                    
                    jobs_with_details.append(full_job)
                    logger.info(f"  Processed job {job_id}: {full_job.get('title', 'Unknown')}")
                else:
                    logger.warning(f"  Could not get details for job {job_id}")
                    
            except Exception as e:
                logger.error(f"Error processing job {job_id}: {e}")
                continue
        
        logger.info(f"Successfully processed {len(jobs_with_details)} jobs with full details")
        
        if jobs_with_details:
            # Regenerate XML files with real job data
            logger.info("Regenerating XML files with real job data...")
            
            # Sort jobs by ID for consistency
            jobs_with_details.sort(key=lambda x: x.get('id', 0))
            
            # Generate both XML files
            xml_service.regenerate_xml_from_jobs(jobs_with_details, 'myticas-job-feed.xml')
            xml_service.regenerate_xml_from_jobs(jobs_with_details, 'myticas-job-feed-scheduled.xml')
            
            logger.info("XML files regenerated successfully!")
            
            # Upload to SFTP
            logger.info("Uploading XML files to SFTP server...")
            os.system('python3 upload_xml_files.py')
            
            return True
        else:
            logger.error("No jobs with details to write to XML")
            return False
            
    except Exception as e:
        logger.error(f"Error in restore process: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = restore_jobs_with_real_data()
    if success:
        logger.info("✅ Successfully restored XML files with real job data!")
    else:
        logger.error("❌ Failed to restore XML files")