#!/usr/bin/env python3
"""
Permanent solution to prevent XML duplicates.
This module ensures that duplicates are never added to XML files.
"""

import logging
from lxml import etree
from typing import Set, Dict, Any
import os
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)


class XMLDuplicatePrevention:
    """Prevents duplicate jobs from being added to XML files."""
    
    def __init__(self):
        self.parser = etree.XMLParser(strip_cdata=False)
        
    def get_job_ids_from_xml(self, xml_file: str) -> Set[str]:
        """Extract all job IDs from an XML file."""
        try:
            if not os.path.exists(xml_file):
                logger.warning(f"XML file {xml_file} does not exist")
                return set()
                
            tree = etree.parse(xml_file, self.parser)
            root = tree.getroot()
            
            job_ids = set()
            for job in root.findall('job'):
                bhatsid_elem = job.find('bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text:
                    # Extract ID from CDATA if present
                    bhatsid_text = str(bhatsid_elem.text).strip()
                    if 'CDATA' in bhatsid_text:
                        bhatsid = bhatsid_text[9:-3].strip()
                    else:
                        bhatsid = bhatsid_text.strip()
                    job_ids.add(bhatsid)
                    
            return job_ids
        except Exception as e:
            logger.error(f"Error reading job IDs from {xml_file}: {e}")
            return set()
    
    def remove_duplicates_from_xml(self, xml_file: str) -> int:
        """Remove any duplicate jobs from an XML file."""
        try:
            if not os.path.exists(xml_file):
                logger.warning(f"XML file {xml_file} does not exist")
                return 0
            
            # Create backup
            backup_file = f"{xml_file}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(xml_file, backup_file)
            logger.info(f"Created backup: {backup_file}")
            
            tree = etree.parse(xml_file, self.parser)
            root = tree.getroot()
            
            seen_jobs = set()
            jobs_to_remove = []
            
            for job in root.findall('job'):
                bhatsid_elem = job.find('bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text:
                    # Extract ID from CDATA if present
                    bhatsid_text = str(bhatsid_elem.text).strip()
                    if 'CDATA' in bhatsid_text:
                        bhatsid = bhatsid_text[9:-3].strip()
                    else:
                        bhatsid = bhatsid_text.strip()
                    
                    if bhatsid in seen_jobs:
                        # This is a duplicate
                        jobs_to_remove.append(job)
                        logger.info(f"Found duplicate job {bhatsid} in {xml_file}")
                    else:
                        seen_jobs.add(bhatsid)
            
            # Remove duplicates
            for job in jobs_to_remove:
                root.remove(job)
            
            if jobs_to_remove:
                # Save the cleaned file
                tree.write(xml_file, encoding='utf-8', xml_declaration=True, pretty_print=True)
                logger.info(f"Removed {len(jobs_to_remove)} duplicates from {xml_file}")
                
            return len(jobs_to_remove)
            
        except Exception as e:
            logger.error(f"Error removing duplicates from {xml_file}: {e}")
            return 0
    
    def ensure_no_duplicates_before_upload(self, xml_files: list) -> Dict[str, int]:
        """Clean all XML files before uploading to prevent duplicates on server."""
        results = {}
        
        for xml_file in xml_files:
            duplicates_removed = self.remove_duplicates_from_xml(xml_file)
            results[xml_file] = duplicates_removed
            
            if duplicates_removed > 0:
                logger.warning(f"⚠️ Removed {duplicates_removed} duplicates from {xml_file} before upload")
            else:
                logger.info(f"✓ No duplicates found in {xml_file}")
        
        return results
    
    def validate_job_addition(self, xml_file: str, job_id: str) -> bool:
        """Check if a job can be added without creating a duplicate."""
        existing_ids = self.get_job_ids_from_xml(xml_file)
        
        if job_id in existing_ids:
            logger.warning(f"Job {job_id} already exists in {xml_file} - preventing duplicate addition")
            return False
        
        return True
    
    def monitor_and_fix_xml_files(self):
        """Monitor and fix XML files to ensure no duplicates."""
        xml_files = ['myticas-job-feed-CORRECT-1755627190.xml', 'myticas-job-feed-scheduled.xml']
        
        for xml_file in xml_files:
            if os.path.exists(xml_file):
                job_ids = self.get_job_ids_from_xml(xml_file)
                total_jobs = 0
                
                # Count total jobs
                try:
                    tree = etree.parse(xml_file, self.parser)
                    root = tree.getroot()
                    total_jobs = len(root.findall('job'))
                except:
                    pass
                
                if total_jobs > len(job_ids):
                    logger.warning(f"⚠️ {xml_file}: {total_jobs} jobs but only {len(job_ids)} unique IDs - duplicates detected!")
                    self.remove_duplicates_from_xml(xml_file)
                else:
                    logger.info(f"✓ {xml_file}: {total_jobs} jobs, all unique")


def main():
    """Main function for testing duplicate prevention."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    preventer = XMLDuplicatePrevention()
    
    # Monitor and fix current XML files
    print("\n=== Checking XML Files for Duplicates ===")
    preventer.monitor_and_fix_xml_files()
    
    # Clean files before upload
    print("\n=== Ensuring Clean Files for Upload ===")
    xml_files = ['myticas-job-feed-CORRECT-1755627190.xml', 'myticas-job-feed-scheduled.xml']
    results = preventer.ensure_no_duplicates_before_upload(xml_files)
    
    for file, count in results.items():
        if count > 0:
            print(f"  {file}: Removed {count} duplicates")
        else:
            print(f"  {file}: Clean (no duplicates)")


if __name__ == "__main__":
    main()