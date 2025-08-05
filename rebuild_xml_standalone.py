"""
Standalone XML Rebuild Script
Rebuilds the job feed XML from scratch using Bullhorn data and existing database credentials
"""
import logging
import os
from datetime import datetime
from lxml import etree
from bullhorn_service import BullhornService
from job_classification_service import JobClassificationService
from xml_integration_service import XMLIntegrationService
from ftp_service import FTPService
import json
from sqlalchemy import create_engine, text
import shutil

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Recruiter LinkedIn tag mapping
RECRUITER_LINKEDIN_TAGS = {
    "Michael Theodossiou": "#LI-MIT",
    "Dom Scaletta": "#LI-DSC", 
    "Dominic Scaletta": "#LI-DSC",
    "Adam Gebara": "#LI-AG",
    "Mike Gebara": "#LI-MG",
    "Amanda Messina": "#LI-AM",
    "Myticas Recruiter": "#LI-RS",
    "Reena Setya": "#LI-RS",
    "Steve Theodossiou": "#LI-ST",
    "Nick Theodossiou": "#LI-NT",
    "Runa Parmar": "#LI-RP",
    "Matheo Theodossiou": "#LI-MAT",
    "Danny Francis": "#LI-DF",
    "Cody Watson": "#LI-CW",
    "Eric Enwright": "#LI-EE"
}

def get_bullhorn_credentials_from_db():
    """Get Bullhorn credentials from the database"""
    try:
        # Connect to database
        engine = create_engine(os.environ.get("DATABASE_URL"))
        
        with engine.connect() as conn:
            # Query for credentials
            result = conn.execute(text("""
                SELECT setting_key, setting_value 
                FROM global_settings 
                WHERE setting_key IN ('bullhorn_client_id', 'bullhorn_client_secret', 
                                     'bullhorn_username', 'bullhorn_password')
            """))
            
            credentials = {}
            for row in result:
                credentials[row[0]] = row[1].strip() if row[1] else None
                
            return credentials
            
    except Exception as e:
        logger.error(f"Error getting credentials from database: {str(e)}")
        return {}

def get_all_tearsheet_jobs():
    """Get all jobs from all monitored tearsheets"""
    tearsheets = [
        (1256, "Ottawa Sponsored Jobs"),
        (1264, "VMS Sponsored Jobs"),
        (1499, "Clover Sponsored Jobs"),
        (1258, "Cleveland Sponsored Jobs"),
        (1257, "Chicago Sponsored Jobs")
    ]
    
    # Get credentials and create Bullhorn service
    credentials = get_bullhorn_credentials_from_db()
    if not all(credentials.get(k) for k in ['bullhorn_client_id', 'bullhorn_client_secret', 
                                              'bullhorn_username', 'bullhorn_password']):
        logger.error("Missing Bullhorn credentials in database")
        return []
    
    bullhorn = BullhornService(
        client_id=credentials['bullhorn_client_id'],
        client_secret=credentials['bullhorn_client_secret'],
        username=credentials['bullhorn_username'],
        password=credentials['bullhorn_password']
    )
    
    if not bullhorn.authenticate():
        logger.error("Failed to authenticate with Bullhorn")
        return []
    
    all_jobs = {}
    
    for tearsheet_id, tearsheet_name in tearsheets:
        logger.info(f"Fetching jobs from {tearsheet_name} (ID: {tearsheet_id})")
        jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
        
        for job in jobs:
            job_id = job.get('id')
            if job_id and job_id not in all_jobs:
                job['tearsheet_name'] = tearsheet_name
                all_jobs[job_id] = job
                
        logger.info(f"Found {len(jobs)} jobs in {tearsheet_name}")
    
    logger.info(f"Total unique jobs across all tearsheets: {len(all_jobs)}")
    return list(all_jobs.values())

def rebuild_xml_from_tearsheets(preserve_references: bool = False):
    """Rebuild XML from all tearsheet jobs"""
    operation_type = "ad-hoc fix (preserving references)" if preserve_references else "scheduled automation (new references)"
    logger.info(f"Starting complete XML rebuild from tearsheets ({operation_type})...")
    
    # Get all jobs
    jobs = get_all_tearsheet_jobs()
    
    if not jobs:
        logger.error("No jobs found in tearsheets")
        return False
    
    # Extract existing reference numbers if preserve_references is True
    existing_references = {}
    if preserve_references:
        logger.info("Extracting existing reference numbers for preservation...")
        existing_references = extract_existing_reference_numbers('myticas-job-feed.xml')
        logger.info(f"Found {len(existing_references)} existing reference numbers to preserve")
    
    # Create XML integration service
    xml_service = XMLIntegrationService()
    
    # Create new XML structure
    root = etree.Element("source")
    etree.SubElement(root, "publisherurl").text = "https://myticas.com"
    
    # Process each job
    successful_jobs = 0
    
    for job_data in jobs:
        try:
            # Use preserved reference number if available
            job_id = str(job_data.get('id'))
            existing_ref = existing_references.get(job_id) if preserve_references else None
            
            # Map Bullhorn job data to XML format with preserved reference if available
            xml_job_data = xml_service.map_bullhorn_job_to_xml(job_data, existing_ref)
            
            if xml_job_data:
                # Create job element
                job_elem = etree.Element("job")
                
                # Add all fields with CDATA wrapping
                for field_name, field_value in xml_job_data.items():
                    field_elem = etree.SubElement(job_elem, field_name)
                    field_elem.text = etree.CDATA(f" {field_value} " if field_value else " ")
                
                root.append(job_elem)
                successful_jobs += 1
            else:
                logger.warning(f"Failed to convert job {job_data.get('id')} to XML")
        except Exception as e:
            logger.error(f"Error processing job {job_data.get('id')}: {str(e)}")
    
    # Create the tree
    tree = etree.ElementTree(root)
    
    # Pretty print with proper formatting
    etree.indent(tree, space="  ")
    
    # Backup current XML if it exists
    if os.path.exists('myticas-job-feed.xml'):
        backup_name = f'myticas-job-feed-backup-{datetime.now().strftime("%Y%m%d-%H%M%S")}.xml'
        shutil.copy2('myticas-job-feed.xml', f'xml_backups/{backup_name}')
        logger.info(f"Backed up current XML to {backup_name}")
    
    # Save the new file
    with open('myticas-job-feed.xml', 'wb') as f:
        tree.write(f, pretty_print=True, xml_declaration=True, encoding='UTF-8')
    
    # Also create scheduled version
    shutil.copy2('myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml')
    
    logger.info(f"✓ XML rebuild complete: {successful_jobs} jobs processed successfully")
    
    # Count CDATA sections to verify
    with open('myticas-job-feed.xml', 'r') as f:
        content = f.read()
        cdata_count = content.count('<![CDATA[')
        file_size = os.path.getsize('myticas-job-feed.xml')
        
    logger.info(f"XML file stats: {successful_jobs} jobs, {cdata_count} CDATA sections, {file_size:,} bytes")
    
    # Log this rebuild operation to ProcessingLog for scheduler page consistency
    try:
        engine = create_engine(os.environ.get("DATABASE_URL"))
        
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO processing_log (file_path, processing_type, jobs_processed, success, processed_at)
                VALUES ('myticas-job-feed.xml', 'manual', :jobs, true, NOW())
            """), {'jobs': successful_jobs})
            conn.commit()
        
        logger.info("✓ Processing logged to database for scheduler consistency")
        
    except Exception as e:
        logger.warning(f"Could not log to ProcessingLog: {str(e)}")
        # Continue anyway - this is just for UI consistency
    
    # Upload to FTP
    try:
        # Get FTP credentials from database
        engine = create_engine(os.environ.get("DATABASE_URL"))
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT setting_key, setting_value 
                FROM global_settings 
                WHERE setting_key IN ('ftp_hostname', 'ftp_username', 'ftp_password')
            """))
            
            ftp_creds = {}
            for row in result:
                ftp_creds[row[0]] = row[1].strip() if row[1] else None
        
        if all(ftp_creds.get(k) for k in ['ftp_hostname', 'ftp_username', 'ftp_password']):
            ftp_service = FTPService(
                hostname=ftp_creds['ftp_hostname'],
                username=ftp_creds['ftp_username'],
                password=ftp_creds['ftp_password'],
                target_directory="/",
                port=2222,
                use_sftp=True
            )
            # Upload both files
            ftp_service.upload_file('myticas-job-feed.xml', 'myticas-job-feed.xml')
            ftp_service.upload_file('myticas-job-feed-scheduled.xml', 'myticas-job-feed-scheduled.xml')
            logger.info("✓ XML files uploaded to FTP server")
        else:
            logger.warning("FTP credentials not found in database - skipping upload")
    except Exception as e:
        logger.error(f"Failed to upload to FTP: {str(e)}")
    
    return True

def extract_existing_reference_numbers(xml_file_path: str) -> dict:
    """Extract existing reference numbers from XML file"""
    existing_refs = {}
    try:
        if os.path.exists(xml_file_path):
            with open(xml_file_path, 'rb') as f:
                tree = etree.parse(f)
            root = tree.getroot()
            
            for job in root.xpath('.//job'):
                bhatsid_elem = job.find('.//bhatsid')
                ref_elem = job.find('.//referencenumber')
                
                if bhatsid_elem is not None and ref_elem is not None:
                    job_id = bhatsid_elem.text.strip() if bhatsid_elem.text else None
                    ref_number = ref_elem.text.strip() if ref_elem.text else None
                    
                    if job_id and ref_number:
                        existing_refs[job_id] = ref_number
                        
    except Exception as e:
        logger.warning(f"Could not extract existing reference numbers: {e}")
    
    return existing_refs

def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Rebuild XML files from Bullhorn tearsheets')
    parser.add_argument('--preserve-references', action='store_true', 
                       help='Preserve existing reference numbers (use for ad-hoc fixes)')
    
    args = parser.parse_args()
    
    # Create backup directory if it doesn't exist
    os.makedirs('xml_backups', exist_ok=True)
    
    success = rebuild_xml_from_tearsheets(preserve_references=args.preserve_references)
    
    if success:
        logger.info("✅ XML rebuild completed successfully!")
    else:
        logger.error("❌ XML rebuild failed")
        
if __name__ == "__main__":
    main()