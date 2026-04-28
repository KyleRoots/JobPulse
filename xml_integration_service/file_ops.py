"""FileOpsMixin — XMLIntegrationService methods for this domain."""
import os
import logging
import re
import shutil
import time
import threading
import urllib.parse
import html
from typing import Dict, List, Optional
from datetime import datetime
try:
    from lxml import etree
except ImportError:
    from xml_safe_compat import safe_etree as etree
from xml_processor import XMLProcessor
from job_classification_service import JobClassificationService, InternalJobClassifier
from xml_safeguards import XMLSafeguards
from tearsheet_config import TearsheetConfig
from utils.field_mappers import map_employment_type, map_remote_type

logger = logging.getLogger(__name__)


class FileOpsMixin:
    """Mixin providing file_ops-related XMLIntegrationService methods."""

    def _clean_extra_whitespace(self, xml_file_path: str):
        """Clean up extra blank lines in XML file"""
        try:
            with open(xml_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Remove multiple consecutive blank lines between publisherurl and first job
            content = re.sub(r'</publisherurl>\n\s*\n+\s*<job>', '</publisherurl>\n  <job>', content)
            
            with open(xml_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
                
        except Exception as e:
            self.logger.error(f"Error cleaning whitespace: {str(e)}")
    def sort_xml_jobs_by_date(self, xml_file_path: str, newest_first: bool = True) -> bool:
        """
        Sort all jobs in the XML file by date
        
        Args:
            xml_file_path: Path to the XML file
            newest_first: If True, sort newest jobs first (default), if False, oldest first
            
        Returns:
            bool: True if sorting was successful, False otherwise
        """
        try:
            # Parse existing XML
            with open(xml_file_path, 'rb') as f:
                tree = etree.parse(f, self._parser)
            
            root = tree.getroot()
            
            # Find all job elements
            jobs = root.findall('job')
            
            if not jobs:
                self.logger.info("No jobs found in XML file to sort")
                return True
            
            # Create a list of (date_obj, job_element) tuples for sorting
            job_date_pairs = []
            
            for job in jobs:
                date_element = job.find('date')
                if date_element is not None and date_element.text:
                    # Parse the date string (format: "July 16, 2025")
                    date_text = date_element.text.strip()
                    try:
                        date_obj = datetime.strptime(date_text, '%B %d, %Y')
                        job_date_pairs.append((date_obj, job))
                    except ValueError:
                        # If date parsing fails, use current date as fallback
                        self.logger.warning(f"Failed to parse date: {date_text}, using current date")
                        job_date_pairs.append((datetime.now(), job))
                else:
                    # If no date found, use current date as fallback
                    job_date_pairs.append((datetime.now(), job))
            
            # Sort by date (newest first if newest_first=True, oldest first if False)
            job_date_pairs.sort(key=lambda x: x[0], reverse=newest_first)
            
            # Remove all existing job elements from the XML
            for job in jobs:
                root.remove(job)
            
            # Find the insertion point (after publisherurl)
            publisher_url = root.find('publisherurl')
            if publisher_url is None:
                self.logger.error("No publisherurl element found in XML")
                return False
            
            publisher_url_index = list(root).index(publisher_url)
            
            # Insert sorted jobs back into the XML
            for i, (date_obj, job) in enumerate(job_date_pairs):
                # Ensure proper spacing
                if i == 0:
                    publisher_url.tail = "\n  "
                job.tail = "\n  "
                
                root.insert(publisher_url_index + 1 + i, job)
            
            # Write the sorted XML back to file
            with open(xml_file_path, 'wb') as f:
                tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
            
            # Clean up extra whitespace
            self._clean_extra_whitespace(xml_file_path)
            
            sort_order = "newest first" if newest_first else "oldest first"
            self.logger.info(f"Successfully sorted {len(job_date_pairs)} jobs by date ({sort_order})")
            return True
            
        except Exception as e:
            self.logger.error(f"Error sorting XML jobs by date: {str(e)}")
            return False
    def _cleanup_old_backups(self, xml_file_path: str, keep_count: int = 3):
        """
        Clean up old backup files, keeping only the most recent ones
        
        Args:
            xml_file_path: Path to the XML file
            keep_count: Number of recent backup files to keep (default: 3)
        """
        import glob
        import os
        
        try:
            # Find all backup files for this XML file
            backup_pattern = f"{xml_file_path}.backup_update_*"
            backup_files = glob.glob(backup_pattern)
            
            if len(backup_files) <= keep_count:
                return  # No cleanup needed
            
            # Sort backups by creation time (newest first)
            backup_files.sort(key=lambda f: os.path.getctime(f), reverse=True)
            
            # Remove old backups beyond keep_count
            files_to_remove = backup_files[keep_count:]
            removed_count = 0
            
            for backup_file in files_to_remove:
                try:
                    os.remove(backup_file)
                    removed_count += 1
                except Exception as e:
                    self.logger.warning(f"Failed to remove old backup {backup_file}: {str(e)}")
            
            if removed_count > 0:
                self.logger.info(f"Cleaned up {removed_count} old backup files, keeping {keep_count} most recent")
                
        except Exception as e:
            self.logger.error(f"Error during backup cleanup: {str(e)}")
    def _safe_write_xml(self, xml_file_path: str, tree, validation_callback=None):
        """
        Safely write XML file with validation and backup
        
        Args:
            xml_file_path: Path to the XML file
            tree: lxml tree object to write
            validation_callback: Optional function to validate before writing
        
        Returns:
            bool: True if successful, False otherwise
        """
        def write_xml_file(filepath):
            with open(filepath, 'wb') as f:
                tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
            self._clean_extra_whitespace(filepath)
            return True
        
        # Use safeguards for safe update
        result = self.safeguards.safe_update_xml(
            xml_file_path,
            lambda fp: write_xml_file(fp)
        )
        
        if result['success']:
            self.logger.info(f"Safely wrote XML file: {xml_file_path}")
            if result.get('warnings'):
                self.logger.warning(f"Warnings: {result['warnings']}")
        else:
            self.logger.error(f"Failed to safely write XML: {result.get('error')}")
            if result.get('rolled_back'):
                self.logger.info("Changes were rolled back due to validation failure")
        
        return result['success']
