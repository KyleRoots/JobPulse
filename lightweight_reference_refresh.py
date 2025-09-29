#!/usr/bin/env python3
"""
Lightweight Reference Number Refresh Service
Only updates reference numbers while preserving all other XML content
"""

import random
import string
import os
import requests
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

def lightweight_refresh_references_from_content(xml_content):
    """
    Ultra-lightweight reference number refresh for XML content string
    - No file I/O
    - No API calls
    - No field remapping
    - Preserves all existing data
    - Only touches reference numbers
    """
    start_time = datetime.now()
    
    try:
        # Parse XML content preserving CDATA
        parser = etree.XMLParser(strip_cdata=False)
        root = etree.fromstring(xml_content.encode('utf-8'), parser)
        
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
        
        # Convert back to string preserving formatting
        refreshed_xml = etree.tostring(root, 
                                     encoding='utf-8', 
                                     xml_declaration=True, 
                                     pretty_print=True).decode('utf-8')
        
        elapsed_time = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"✅ Content reference refresh complete:")
        logger.info(f"   - Jobs updated: {jobs_updated}")
        logger.info(f"   - Time taken: {elapsed_time:.2f} seconds")
        
        return {
            'success': True,
            'jobs_updated': jobs_updated,
            'time_seconds': elapsed_time,
            'xml_content': refreshed_xml,
            'reference_numbers': list(used_references)
        }
        
    except Exception as e:
        logger.error(f"❌ Error refreshing references from content: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

def save_references_to_database(xml_content):
    """
    Save reference numbers from XML content to database for preservation
    Called during manual refresh and uploads to maintain reference state
    """
    try:
        from app import db
        from models import JobReferenceNumber
        
        # Parse XML content to extract job references
        parser = etree.XMLParser(strip_cdata=False)
        root = etree.fromstring(xml_content.encode('utf-8'), parser)
        
        saved_count = 0
        updated_count = 0
        
        for job in root.findall('.//job'):
            job_id_elem = job.find('bhatsid')
            ref_elem = job.find('referencenumber')
            title_elem = job.find('title')
            
            if job_id_elem is not None and ref_elem is not None:
                job_id = job_id_elem.text.strip() if job_id_elem.text else ""
                ref_text = ref_elem.text.strip() if ref_elem.text else ""
                job_title = title_elem.text.strip() if title_elem and title_elem.text else ""
                
                if job_id and ref_text:
                    # Check if this job reference already exists
                    existing_ref = JobReferenceNumber.query.filter_by(bullhorn_job_id=job_id).first()
                    
                    if existing_ref:
                        # Update existing reference if it changed
                        if existing_ref.reference_number != ref_text:
                            existing_ref.reference_number = ref_text
                            existing_ref.job_title = job_title
                            updated_count += 1
                    else:
                        # Create new reference entry
                        new_ref = JobReferenceNumber(
                            bullhorn_job_id=job_id,
                            reference_number=ref_text,
                            job_title=job_title
                        )
                        db.session.add(new_ref)
                        saved_count += 1
        
        db.session.commit()
        logger.info(f"💾 Database saved: {saved_count} new, {updated_count} updated reference numbers")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error saving references to database: {str(e)}")
        return False

def get_existing_references_from_database():
    """
    Load existing reference numbers from database
    This provides reliable persistence across automated uploads
    
    Returns:
        dict: Mapping of job_id to reference_number for all stored references
    """
    existing_references = {}
    
    try:
        from app import db
        from models import JobReferenceNumber
        
        # Query all stored reference numbers
        stored_refs = JobReferenceNumber.query.all()
        
        for ref_entry in stored_refs:
            existing_references[ref_entry.bullhorn_job_id] = ref_entry.reference_number
        
        logger.info(f"💾 Loaded {len(existing_references)} existing reference numbers from DATABASE")
        if len(existing_references) > 0:
            # Log a few sample mappings for verification
            sample_mappings = list(existing_references.items())[:3]
            logger.info(f"📋 Sample mappings: {sample_mappings}")
        
        return existing_references
        
    except Exception as e:
        logger.warning(f"⚠️ Could not load references from database: {str(e)}")
        return existing_references

def get_existing_references_from_published_file(published_xml_path='myticas-job-feed-v2.xml'):
    """
    UPDATED: Now uses database as primary source, with local file fallback
    
    Args:
        published_xml_path: Path/filename for fallback local file
        
    Returns:
        dict: Mapping of job_id to reference_number for all jobs with existing references
    """
    # Primary: Load from database (reliable persistence)
    existing_references = get_existing_references_from_database()
    
    # If database has references, use them
    if existing_references:
        return existing_references
    
    # Fallback: Read from local file if database is empty
    logger.info(f"🔄 Database empty, falling back to local file: {published_xml_path}")
    
    try:
        if not os.path.exists(published_xml_path):
            logger.info(f"📝 No local XML file found at {published_xml_path} - no existing references to preserve")
            return existing_references
        
        # Parse existing XML file preserving CDATA
        parser = etree.XMLParser(strip_cdata=False)
        tree = etree.parse(published_xml_path, parser)
        root = tree.getroot()
        
        # Extract job_id to reference number mapping
        for job in root.findall('.//job'):
            job_id_elem = job.find('bhatsid')  # Use bhatsid (Bullhorn ATS ID) as job identifier
            ref_elem = job.find('referencenumber')
            
            if job_id_elem is not None and ref_elem is not None:
                job_id = job_id_elem.text.strip() if job_id_elem.text else ""
                ref_text = ref_elem.text.strip() if ref_elem.text else ""
                
                if job_id and ref_text:
                    existing_references[job_id] = ref_text
        
        logger.info(f"✅ Loaded {len(existing_references)} existing reference numbers from LOCAL FILE: {published_xml_path}")
        return existing_references
        
    except Exception as e:
        logger.warning(f"⚠️ Could not load existing references from local file {published_xml_path}: {str(e)}")
        return existing_references

def preserve_references_from_published_xml(new_xml_content, published_xml_path='myticas-job-feed-v2.xml'):
    """
    Preserve reference numbers from the published XML file in new XML content
    This ensures automated uploads don't override existing reference numbers
    
    Args:
        new_xml_content: The new XML content to apply reference preservation to
        published_xml_path: Path to the currently published XML file (source of truth)
        
    Returns:
        dict: Result with success status and preserved XML content
    """
    start_time = datetime.now()
    
    try:
        # Get existing reference numbers from published file (source of truth)
        existing_references = get_existing_references_from_published_file(published_xml_path)
        
        # Parse new XML content preserving CDATA
        parser = etree.XMLParser(strip_cdata=False)
        root = etree.fromstring(new_xml_content.encode('utf-8'), parser)
        
        # Track reference numbers to ensure uniqueness
        used_references = set(existing_references.values())
        jobs_preserved = 0
        new_refs_generated = 0
        
        # Apply existing reference numbers and generate new ones for jobs without them
        for job in root.findall('.//job'):
            job_id_elem = job.find('bhatsid')  # Use bhatsid (Bullhorn ATS ID) as job identifier
            ref_elem = job.find('referencenumber')
            
            if job_id_elem is not None and ref_elem is not None:
                job_id = job_id_elem.text.strip() if job_id_elem.text else ""
                
                if job_id in existing_references:
                    # Preserve existing reference number from published XML
                    existing_ref = existing_references[job_id]
                    ref_elem.text = None
                    ref_elem.tail = ref_elem.tail
                    # Clear any existing children
                    for child in ref_elem:
                        ref_elem.remove(child)
                    # Add preserved reference as CDATA
                    ref_elem.text = etree.CDATA(f" {existing_ref} ")
                    jobs_preserved += 1
                else:
                    # Generate new reference for jobs not in published XML
                    new_ref = generate_reference_number()
                    while new_ref in used_references:
                        new_ref = generate_reference_number()
                    
                    used_references.add(new_ref)
                    
                    # Add new reference as CDATA
                    ref_elem.text = None
                    ref_elem.tail = ref_elem.tail
                    for child in ref_elem:
                        ref_elem.remove(child)
                    ref_elem.text = etree.CDATA(f" {new_ref} ")
                    new_refs_generated += 1
        
        # Convert back to string preserving formatting
        preserved_xml = etree.tostring(root, 
                                     encoding='utf-8', 
                                     xml_declaration=True, 
                                     pretty_print=True).decode('utf-8')
        
        elapsed_time = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"✅ Reference preservation from published XML complete:")
        logger.info(f"   - Existing references preserved: {jobs_preserved}")
        logger.info(f"   - New references generated: {new_refs_generated}")
        logger.info(f"   - Source: {published_xml_path}")
        logger.info(f"   - Time taken: {elapsed_time:.2f} seconds")
        
        return {
            'success': True,
            'jobs_preserved': jobs_preserved,
            'new_refs_generated': new_refs_generated,
            'time_seconds': elapsed_time,
            'xml_content': preserved_xml,
            'source_file': published_xml_path
        }
        
    except Exception as e:
        logger.error(f"❌ Error preserving references from published XML: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

def preserve_existing_references_in_content(xml_content, preserve_all=True):
    """
    Preserve existing reference numbers in XML content to prevent overriding manual changes
    - Keeps all existing reference numbers unchanged
    - Only generates new reference numbers for jobs that don't have any
    - Used by automated uploads to preserve manual refresh changes
    
    DEPRECATED: Use preserve_references_from_published_xml for automated uploads
    """
    start_time = datetime.now()
    
    try:
        # Parse XML content preserving CDATA
        parser = etree.XMLParser(strip_cdata=False)
        root = etree.fromstring(xml_content.encode('utf-8'), parser)
        
        # Track all reference numbers to ensure uniqueness
        used_references = set()
        jobs_preserved = 0
        new_refs_generated = 0
        
        # First pass: collect all existing reference numbers
        for job in root.findall('.//job'):
            ref_elem = job.find('referencenumber')
            if ref_elem is not None and ref_elem.text:
                # Extract reference number from CDATA or plain text
                ref_text = ref_elem.text.strip() if ref_elem.text else ""
                if ref_text:
                    used_references.add(ref_text)
        
        # Second pass: preserve existing refs and generate new ones only for jobs without refs
        for job in root.findall('.//job'):
            ref_elem = job.find('referencenumber')
            
            if ref_elem is not None:
                # Check if this job already has a reference number
                existing_ref = ref_elem.text.strip() if ref_elem.text else ""
                
                if existing_ref:
                    # Preserve existing reference number (don't change it)
                    jobs_preserved += 1
                else:
                    # Only generate new reference for jobs without any
                    new_ref = generate_reference_number()
                    while new_ref in used_references:
                        new_ref = generate_reference_number()
                    
                    used_references.add(new_ref)
                    
                    # Add new reference as CDATA
                    ref_elem.text = None
                    ref_elem.tail = ref_elem.tail  # Preserve any trailing whitespace
                    # Clear any existing children
                    for child in ref_elem:
                        ref_elem.remove(child)
                    # Add new reference as CDATA
                    ref_elem.text = etree.CDATA(f" {new_ref} ")
                    
                    new_refs_generated += 1
        
        # Convert back to string preserving formatting
        preserved_xml = etree.tostring(root, 
                                     encoding='utf-8', 
                                     xml_declaration=True, 
                                     pretty_print=True).decode('utf-8')
        
        elapsed_time = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"✅ Reference preservation complete:")
        logger.info(f"   - Existing references preserved: {jobs_preserved}")
        logger.info(f"   - New references generated: {new_refs_generated}")
        logger.info(f"   - Time taken: {elapsed_time:.2f} seconds")
        
        return {
            'success': True,
            'jobs_preserved': jobs_preserved,
            'new_refs_generated': new_refs_generated,
            'time_seconds': elapsed_time,
            'xml_content': preserved_xml,
            'total_references': len(used_references)
        }
        
    except Exception as e:
        logger.error(f"❌ Error preserving references in content: {str(e)}")
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
            
            # Upload with environment-specific filename (v2 for production, v2-dev for development)
            # Import the filename function
            from app import get_xml_filename
            remote_filename = get_xml_filename()
            
            upload_result = ftp_service.upload_file('myticas-job-feed.xml', remote_filename)
            
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