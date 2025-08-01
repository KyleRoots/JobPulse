#!/usr/bin/env python3
"""
XML Safeguards Module - Prevents common XML corruption issues
"""
import xml.etree.ElementTree as ET
import os
import shutil
from datetime import datetime
import hashlib
import json

class XMLSafeguards:
    """Implements safeguards for XML job feed files"""
    
    def __init__(self):
        self.backup_dir = "xml_backups"
        self.validation_log = "xml_validation.log"
        os.makedirs(self.backup_dir, exist_ok=True)
    
    def create_backup(self, filepath):
        """Create timestamped backup of XML file"""
        if not os.path.exists(filepath):
            return None
            
        filename = os.path.basename(filepath)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{filename}.{timestamp}.backup"
        backup_path = os.path.join(self.backup_dir, backup_name)
        
        shutil.copy2(filepath, backup_path)
        return backup_path
    
    def validate_xml_structure(self, filepath):
        """Validate XML structure and content"""
        errors = []
        warnings = []
        
        try:
            # Parse XML
            tree = ET.parse(filepath)
            root = tree.getroot()
            
            # Check root element
            if root.tag != 'source':
                errors.append("Root element must be 'source'")
            
            # Count jobs
            jobs = root.findall('.//job')
            if len(jobs) == 0:
                errors.append("No jobs found in XML")
            
            # Track job IDs for duplicate detection
            seen_ids = set()
            duplicates = []
            
            # Required fields for each job
            required_fields = ['title', 'company', 'date', 'referencenumber', 
                             'bhatsid', 'url', 'description']
            
            for idx, job in enumerate(jobs):
                # Check for required fields
                for field in required_fields:
                    elem = job.find(field)
                    if elem is None:
                        errors.append(f"Job {idx+1} missing required field: {field}")
                    elif not elem.text or elem.text.strip() == '':
                        warnings.append(f"Job {idx+1} has empty {field}")
                
                # Check for duplicate job IDs
                bhatsid_elem = job.find('bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text:
                    job_id = bhatsid_elem.text.strip()
                    if '<![CDATA[' in job_id:
                        job_id = job_id.replace('<![CDATA[', '').replace(']]>', '').strip()
                    
                    if job_id in seen_ids:
                        duplicates.append(job_id)
                    else:
                        seen_ids.add(job_id)
            
            if duplicates:
                errors.append(f"Duplicate job IDs found: {', '.join(duplicates)}")
            
            # Check CDATA formatting
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                cdata_count = content.count('<![CDATA[')
                
            expected_cdata = len(jobs) * len(required_fields)
            if cdata_count < expected_cdata * 0.8:  # Allow some flexibility
                warnings.append(f"Low CDATA count: {cdata_count} (expected ~{expected_cdata})")
            
            # File size check
            file_size = os.path.getsize(filepath) / 1024  # KB
            if file_size < 100:
                warnings.append(f"File size suspiciously small: {file_size:.1f}KB")
            
        except ET.ParseError as e:
            errors.append(f"XML parsing error: {str(e)}")
        except Exception as e:
            errors.append(f"Validation error: {str(e)}")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'job_count': len(jobs) if 'jobs' in locals() else 0,
            'file_size_kb': file_size if 'file_size' in locals() else 0
        }
    
    def calculate_checksum(self, filepath):
        """Calculate MD5 checksum of file"""
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def log_validation(self, filepath, validation_result, checksum):
        """Log validation results"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'file': filepath,
            'checksum': checksum,
            'valid': validation_result['valid'],
            'job_count': validation_result['job_count'],
            'file_size_kb': validation_result['file_size_kb'],
            'errors': validation_result['errors'],
            'warnings': validation_result['warnings']
        }
        
        with open(self.validation_log, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
    
    def safe_update_xml(self, filepath, update_function, *args, **kwargs):
        """Safely update XML file with validation and rollback"""
        # Create backup
        backup_path = self.create_backup(filepath)
        if not backup_path:
            return {'success': False, 'error': 'Failed to create backup'}
        
        # Validate before update
        pre_validation = self.validate_xml_structure(filepath)
        pre_checksum = self.calculate_checksum(filepath)
        
        try:
            # Apply update
            result = update_function(filepath, *args, **kwargs)
            
            # Validate after update
            post_validation = self.validate_xml_structure(filepath)
            post_checksum = self.calculate_checksum(filepath)
            
            # Check if update was successful
            if not post_validation['valid']:
                # Rollback
                shutil.copy2(backup_path, filepath)
                return {
                    'success': False,
                    'error': 'Validation failed after update',
                    'details': post_validation['errors'],
                    'rolled_back': True
                }
            
            # Log successful update
            self.log_validation(filepath, post_validation, post_checksum)
            
            return {
                'success': True,
                'backup': backup_path,
                'pre_jobs': pre_validation['job_count'],
                'post_jobs': post_validation['job_count'],
                'warnings': post_validation['warnings']
            }
            
        except Exception as e:
            # Rollback on error
            shutil.copy2(backup_path, filepath)
            return {
                'success': False,
                'error': str(e),
                'rolled_back': True
            }
    
    def cleanup_old_backups(self, days_to_keep=7):
        """Remove backups older than specified days"""
        import time
        
        cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)
        cleaned = 0
        
        for filename in os.listdir(self.backup_dir):
            filepath = os.path.join(self.backup_dir, filename)
            if os.path.getmtime(filepath) < cutoff_time:
                os.remove(filepath)
                cleaned += 1
        
        return cleaned

# Example usage functions
def remove_duplicates_safely(filepath):
    """Example: Remove duplicate jobs from XML"""
    import xml.etree.ElementTree as ET
    
    tree = ET.parse(filepath)
    root = tree.getroot()
    
    seen_ids = set()
    jobs_to_remove = []
    
    for job in root.findall('.//job'):
        bhatsid_elem = job.find('bhatsid')
        if bhatsid_elem is not None and bhatsid_elem.text:
            job_id = bhatsid_elem.text.strip().replace('<![CDATA[', '').replace(']]>', '').strip()
            if job_id in seen_ids:
                jobs_to_remove.append(job)
            else:
                seen_ids.add(job_id)
    
    for job in jobs_to_remove:
        root.remove(job)
    
    tree.write(filepath, encoding='utf-8', xml_declaration=True)
    return len(jobs_to_remove)

if __name__ == "__main__":
    # Test safeguards
    safeguards = XMLSafeguards()
    
    for xml_file in ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']:
        if os.path.exists(xml_file):
            print(f"\nValidating {xml_file}...")
            validation = safeguards.validate_xml_structure(xml_file)
            print(f"Valid: {validation['valid']}")
            print(f"Jobs: {validation['job_count']}")
            if validation['errors']:
                print(f"Errors: {validation['errors']}")
            if validation['warnings']:
                print(f"Warnings: {validation['warnings']}")