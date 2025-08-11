#!/usr/bin/env python3
"""
Clean up orphaned jobs in XML that are not in any Bullhorn tearsheet
"""
import logging
import os
import paramiko
from bullhorn_service import BullhornService
from lxml import etree

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_tearsheet_job_ids():
    """Get all valid job IDs from monitored tearsheets"""
    logger.info("=== GETTING CURRENT TEARSHEET JOBS ===")
    
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
        all_tearsheet_jobs.update(job_ids)
    
    logger.info(f"Total unique jobs across all tearsheets: {len(all_tearsheet_jobs)}")
    return all_tearsheet_jobs

def clean_xml_orphaned_jobs(xml_file: str, valid_job_ids: set):
    """Remove orphaned jobs from XML file"""
    logger.info(f"=== CLEANING ORPHANED JOBS FROM {xml_file} ===")
    
    try:
        # Load XML
        with open(xml_file, 'rb') as f:
            parser = etree.XMLParser(strip_cdata=False)
            tree = etree.parse(f, parser)
        
        root = tree.getroot()
        jobs = root.xpath('//job')
        
        jobs_removed = []
        jobs_kept = []
        
        for job in jobs:
            bhatsid_elem = job.find('bhatsid')
            if bhatsid_elem is not None and bhatsid_elem.text:
                job_id = bhatsid_elem.text.strip()
                
                if job_id in valid_job_ids:
                    jobs_kept.append(job_id)
                else:
                    # Get job title for logging
                    title_elem = job.find('title')
                    title = title_elem.text if title_elem is not None else "No title"
                    
                    logger.info(f"Removing orphaned job {job_id}: {title}")
                    jobs_removed.append((job_id, title))
                    
                    # Remove job from XML
                    root.remove(job)
        
        logger.info(f"Jobs kept: {len(jobs_kept)}")
        logger.info(f"Jobs removed: {len(jobs_removed)}")
        
        if jobs_removed:
            # Save cleaned XML
            xml_str = etree.tostring(tree, encoding='unicode', pretty_print=True)
            with open(xml_file, 'w', encoding='utf-8') as f:
                f.write('<?xml version=\'1.0\' encoding=\'UTF-8\'?>\n')
                f.write(xml_str)
            
            logger.info(f"✅ Cleaned XML saved with {len(jobs_kept)} valid jobs")
            return True, jobs_removed
        else:
            logger.info("No orphaned jobs found - XML is clean")
            return False, []
            
    except Exception as e:
        logger.error(f"Error cleaning XML: {e}")
        import traceback
        traceback.print_exc()
        return False, []

def upload_cleaned_xml(xml_file: str):
    """Upload cleaned XML to live server"""
    logger.info("=== UPLOADING CLEANED XML ===")
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=os.environ.get('SFTP_HOST'),
            port=2222,
            username=os.environ.get('SFTP_USERNAME'),
            password=os.environ.get('SFTP_PASSWORD'),
            timeout=30
        )
        
        sftp = ssh.open_sftp()
        local_size = os.path.getsize(xml_file)
        logger.info(f"Uploading cleaned XML ({local_size} bytes)...")
        
        sftp.put(xml_file, xml_file)
        
        # Verify upload
        remote_stat = sftp.stat(xml_file)
        logger.info(f"Upload complete - Remote size: {remote_stat.st_size} bytes")
        
        sftp.close()
        ssh.close()
        
        if remote_stat.st_size == local_size:
            logger.info("✅ Upload verified successfully")
            return True
        else:
            logger.warning(f"Size mismatch - local: {local_size}, remote: {remote_stat.st_size}")
            return False
            
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False

def main():
    """Main cleanup process"""
    logger.info("=== ORPHANED JOB CLEANUP PROCESS ===")
    
    # Get current valid job IDs from tearsheets
    valid_job_ids = get_tearsheet_job_ids()
    if not valid_job_ids:
        logger.error("Could not get valid job IDs from tearsheets")
        return False
    
    logger.info(f"Found {len(valid_job_ids)} valid jobs in tearsheets")
    
    # Clean local XML file
    xml_file = 'myticas-job-feed.xml'
    cleaned, removed_jobs = clean_xml_orphaned_jobs(xml_file, valid_job_ids)
    
    if cleaned:
        logger.info(f"Removed {len(removed_jobs)} orphaned jobs:")
        for job_id, title in removed_jobs:
            logger.info(f"  - {job_id}: {title}")
        
        # Upload cleaned XML
        if upload_cleaned_xml(xml_file):
            logger.info("🎉 SUCCESS: Orphaned jobs cleaned and uploaded")
            return True
        else:
            logger.error("Upload failed")
            return False
    else:
        logger.info("✅ No cleanup needed - XML is already synchronized")
        return True

if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\n✅ CLEANUP COMPLETE: XML synchronized with tearsheets")
        else:
            print("\n❌ CLEANUP FAILED")
    except Exception as e:
        logger.error(f"Cleanup process failed: {e}")
        import traceback
        traceback.print_exc()