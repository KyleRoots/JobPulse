#!/usr/bin/env python3
"""
Lightweight Reference Number Refresh Service
Only updates reference numbers while preserving all other XML content
"""

import random
import string
from lxml import etree
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generate_reference_number(length=10):
    """Generate a unique 10-character reference number"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def lightweight_refresh_references(xml_path='myticas-job-feed.xml', output_path=None):
    """
    Ultra-lightweight reference number refresh
    - No API calls
    - No field remapping
    - Preserves all existing data
    - Only touches reference numbers
    """
    start_time = datetime.now()
    
    if output_path is None:
        output_path = xml_path
    
    try:
        # Parse XML preserving CDATA
        parser = etree.XMLParser(strip_cdata=False)
        tree = etree.parse(xml_path, parser)
        root = tree.getroot()
        
        # Track reference numbers to ensure uniqueness
        used_references = set()
        jobs_updated = 0
        
        # Find all job elements
        for job in root.findall('.//job'):
            # Find reference number element
            ref_elem = job.find('referencenumber')
            
            if ref_elem is not None:
                # Generate unique reference number
                new_ref = generate_reference_number()
                while new_ref in used_references:
                    new_ref = generate_reference_number()
                
                used_references.add(new_ref)
                
                # Update ONLY the reference number with CDATA wrapping
                # Clear existing content and add CDATA
                ref_elem.text = None
                ref_elem.tail = ref_elem.tail  # Preserve any trailing whitespace
                # Clear any existing children (in case there's old CDATA)
                for child in ref_elem:
                    ref_elem.remove(child)
                # Add new reference as CDATA
                ref_elem.text = etree.CDATA(f" {new_ref} ")
                
                jobs_updated += 1
        
        # Write back to file preserving formatting
        tree.write(output_path, 
                   encoding='utf-8', 
                   xml_declaration=True, 
                   pretty_print=True)
        
        elapsed_time = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"✅ Reference refresh complete:")
        logger.info(f"   - Jobs updated: {jobs_updated}")
        logger.info(f"   - Time taken: {elapsed_time:.2f} seconds")
        logger.info(f"   - File saved: {output_path}")
        
        return {
            'success': True,
            'jobs_updated': jobs_updated,
            'time_seconds': elapsed_time,
            'reference_numbers': list(used_references)
        }
        
    except Exception as e:
        logger.error(f"❌ Error refreshing references: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

def scheduled_reference_refresh():
    """
    Scheduled task for auto-refreshing references
    Can be called by APScheduler or cron
    """
    logger.info("🔄 Starting scheduled reference refresh...")
    
    # Refresh references
    result = lightweight_refresh_references()
    
    if result['success']:
        # Upload to SFTP using same credentials as regular monitoring
        upload_result = False
        try:
            import os
            from ftp_service import FTPService
            
            # Use environment variables for FTP credentials (same as monitoring cycle)
            ftp_service = FTPService(
                hostname=os.environ.get('SFTP_HOSTNAME', ''),
                username=os.environ.get('SFTP_USERNAME', ''),
                password=os.environ.get('SFTP_PASSWORD', ''),
                port=2222,  # WP Engine SFTP port
                use_sftp=True
            )
            
            # Upload with v2 filename to avoid external system conflicts (same as monitoring)
            upload_result = ftp_service.upload_file('myticas-job-feed.xml', 'myticas-job-feed-v2.xml')
            
            if upload_result:
                logger.info("📤 ✅ Reference refresh complete: Local XML updated AND uploaded to server")
            else:
                logger.warning("⚠️ Reference refresh complete: Local XML updated, but upload failed")
                
        except Exception as e:
            logger.error(f"❌ Upload process failed: {str(e)}")
            logger.warning(f"⚠️ Reference refresh complete: Local XML updated, but upload failed: {str(e)}")
        
        # Log activity with upload status
        log_refresh_activity(result['jobs_updated'], upload_result)
    
    return result

def log_refresh_activity(job_count, upload_success=False):
    """Log the refresh activity to database"""
    try:
        from app import db
        from models import BullhornActivity
        
        activity = BullhornActivity(
            activity_type='reference_refresh',
            description=f'Auto-refreshed {job_count} reference numbers',
            jobs_affected=job_count,
            created_at=datetime.utcnow()
        )
        db.session.add(activity)
        db.session.commit()
    except:
        pass  # Don't fail if logging fails

if __name__ == "__main__":
    # Test the lightweight refresh
    result = lightweight_refresh_references()
    print(f"Result: {result}")