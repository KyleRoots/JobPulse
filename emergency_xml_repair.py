#!/usr/bin/env python3
"""
Emergency XML Repair Utility - Consolidated repair functions for critical XML issues
Replaces: fix_cdata_complete.py, fix_xml_duplicates.py, restore_cdata_formatting.py
"""

import os
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class XMLRepairUtility:
    """Consolidated XML repair utilities for emergency situations"""
    
    def __init__(self):
        self.cdata_fields = [
            'title', 'company', 'date', 'referencenumber', 'bhatsid', 'url',
            'description', 'jobtype', 'city', 'state', 'country', 'category',
            'apply_email', 'remotetype', 'assignedrecruiter', 'jobfunction',
            'jobindustries', 'senoritylevel', 'publisher', 'publisherurl'
        ]
    
    def backup_file(self, filepath):
        """Create backup before repair"""
        if os.path.exists(filepath):
            backup_path = f"{filepath}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            import shutil
            shutil.copy2(filepath, backup_path)
            logger.info(f"Backup created: {backup_path}")
            return backup_path
        return None
    
    def fix_cdata_formatting(self, input_file, output_file=None):
        """Fix CDATA formatting in XML file"""
        if output_file is None:
            output_file = input_file
            
        self.backup_file(input_file)
        logger.info(f"Fixing CDATA formatting in {input_file}")
        
        with open(input_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        cdata_count = 0
        
        # Process each field
        for field in self.cdata_fields:
            pattern = f'<{field}>(?!<!\[CDATA\[)(.*?)</{field}>'
            
            def add_cdata(match):
                nonlocal cdata_count
                field_content = match.group(1)
                if '<![CDATA[' in field_content:
                    return match.group(0)
                cdata_count += 1
                return f'<{field}><![CDATA[{field_content}]]></{field}>'
            
            content = re.sub(pattern, add_cdata, content, flags=re.DOTALL)
        
        # Fix escaped CDATA markers
        content = content.replace('&lt;![CDATA[', '<![CDATA[')
        content = content.replace(']]&gt;', ']]>')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        total_cdata = content.count('<![CDATA[')
        logger.info(f"✓ Fixed {cdata_count} CDATA sections, total: {total_cdata}")
        return total_cdata
    
    def remove_duplicates(self, input_file, output_file=None):
        """Remove duplicate job entries based on bhatsid"""
        if output_file is None:
            output_file = input_file
            
        self.backup_file(input_file)
        logger.info(f"Removing duplicates from {input_file}")
        
        tree = ET.parse(input_file)
        root = tree.getroot()
        
        all_jobs = root.findall('.//job')
        unique_jobs = {}
        jobs_to_remove = []
        
        for job in all_jobs:
            bhatsid_elem = job.find('bhatsid')
            if bhatsid_elem is not None and bhatsid_elem.text:
                job_id = bhatsid_elem.text.strip()
                
                if job_id not in unique_jobs:
                    unique_jobs[job_id] = job
                else:
                    jobs_to_remove.append(job)
        
        # Remove duplicates
        for job in jobs_to_remove:
            root.remove(job)
        
        # Save with pretty formatting
        xml_str = ET.tostring(root, encoding='unicode')
        dom = minidom.parseString(xml_str)
        pretty_xml = dom.toprettyxml(indent="  ", encoding='UTF-8')
        
        # Remove extra blank lines
        lines = pretty_xml.decode('utf-8').split('\n')
        non_empty_lines = [line for line in lines if line.strip()]
        final_xml = '\n'.join(non_empty_lines)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(final_xml)
        
        logger.info(f"✓ Removed {len(jobs_to_remove)} duplicates, {len(unique_jobs)} unique jobs remain")
        return len(unique_jobs), len(jobs_to_remove)
    
    def validate_xml_structure(self, filepath):
        """Validate XML file structure and report issues"""
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
            
            if root.tag != 'source':
                logger.warning(f"Root element should be 'source', found '{root.tag}'")
            
            jobs = root.findall('.//job')
            logger.info(f"Found {len(jobs)} jobs in {filepath}")
            
            # Check for required fields in first few jobs
            required_fields = ['title', 'company', 'bhatsid', 'referencenumber']
            
            for i, job in enumerate(jobs[:5]):  # Check first 5 jobs
                missing_fields = []
                for field in required_fields:
                    if job.find(field) is None:
                        missing_fields.append(field)
                
                if missing_fields:
                    logger.warning(f"Job {i+1} missing fields: {missing_fields}")
            
            logger.info(f"✓ XML structure validation complete for {filepath}")
            return True
            
        except ET.ParseError as e:
            logger.error(f"XML parsing error in {filepath}: {e}")
            return False
        except Exception as e:
            logger.error(f"Validation error for {filepath}: {e}")
            return False
    
    def emergency_repair(self, xml_files=None):
        """Run complete emergency repair on XML files"""
        if xml_files is None:
            xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
        
        logger.info("=== EMERGENCY XML REPAIR STARTING ===")
        
        for xml_file in xml_files:
            if not os.path.exists(xml_file):
                logger.warning(f"File not found: {xml_file}")
                continue
            
            logger.info(f"\nRepairing {xml_file}...")
            
            # 1. Validate structure
            if not self.validate_xml_structure(xml_file):
                logger.error(f"Critical XML structure issues in {xml_file}")
                continue
            
            # 2. Remove duplicates
            unique_count, removed_count = self.remove_duplicates(xml_file)
            
            # 3. Fix CDATA formatting  
            cdata_count = self.fix_cdata_formatting(xml_file)
            
            # 4. Final validation
            self.validate_xml_structure(xml_file)
            
            logger.info(f"✅ {xml_file} repair complete: {unique_count} jobs, {cdata_count} CDATA sections")
        
        logger.info("=== EMERGENCY XML REPAIR COMPLETE ===")

def main():
    """Main function with command line options"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Emergency XML Repair Utility')
    parser.add_argument('--cdata', action='store_true', help='Fix CDATA formatting only')
    parser.add_argument('--duplicates', action='store_true', help='Remove duplicates only')
    parser.add_argument('--validate', action='store_true', help='Validate XML structure only')
    parser.add_argument('--full', action='store_true', help='Run complete emergency repair')
    parser.add_argument('--files', nargs='+', help='Specific XML files to process')
    
    args = parser.parse_args()
    repair = XMLRepairUtility()
    
    xml_files = args.files or ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    if args.cdata:
        for file in xml_files:
            if os.path.exists(file):
                repair.fix_cdata_formatting(file)
    
    elif args.duplicates:
        for file in xml_files:
            if os.path.exists(file):
                repair.remove_duplicates(file)
    
    elif args.validate:
        for file in xml_files:
            if os.path.exists(file):
                repair.validate_xml_structure(file)
    
    elif args.full or not any([args.cdata, args.duplicates, args.validate]):
        repair.emergency_repair(xml_files)
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()