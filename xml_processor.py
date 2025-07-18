import logging
import os
import random
import string
import time
import re
from lxml import etree
from collections import defaultdict

class XMLProcessor:
    """Handles XML processing for job feed files"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.generated_references = set()
    
    def validate_xml(self, filepath):
        """Validate XML file structure"""
        try:
            # Parse XML file
            with open(filepath, 'rb') as f:
                tree = etree.parse(f)
            
            # Check for root element
            root = tree.getroot()
            if root.tag != 'source':
                self.logger.error("Invalid root element. Expected 'source'")
                return False
            
            # Check for job elements
            jobs = root.findall('.//job')
            if not jobs:
                self.logger.error("No job elements found")
                return False
            
            # Validate that jobs have required elements
            required_elements = ['title', 'company', 'date', 'referencenumber']
            for i, job in enumerate(jobs[:10]):  # Check first 10 jobs
                for element in required_elements:
                    if job.find(element) is None:
                        self.logger.error(f"Job {i+1} missing required element: {element}")
                        return False
            
            return True
            
        except etree.XMLSyntaxError as e:
            self.logger.error(f"XML syntax error: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"Error validating XML: {str(e)}")
            return False
    
    def count_jobs(self, filepath):
        """Count number of job elements in XML file"""
        try:
            with open(filepath, 'rb') as f:
                tree = etree.parse(f)
            
            jobs = tree.findall('.//job')
            return len(jobs)
            
        except Exception as e:
            self.logger.error(f"Error counting jobs: {str(e)}")
            return 0
    
    def extract_job_id_from_title(self, title):
        """Extract job ID from title brackets (e.g., '32623' from 'Senior Engineer (32623)')"""
        try:
            # Look for numbers in parentheses at the end of the title
            match = re.search(r'\((\d+)\)\s*$', title)
            if match:
                return match.group(1)
            return ""
        except Exception as e:
            self.logger.error(f"Error extracting job ID from title '{title}': {str(e)}")
            return ""
    
    def generate_reference_number(self, length=10):
        """Generate unique alphanumeric reference number"""
        max_attempts = 1000
        attempts = 0
        
        while attempts < max_attempts:
            # Generate random alphanumeric string
            chars = string.ascii_uppercase + string.digits
            reference = ''.join(random.choice(chars) for _ in range(length))
            
            # Ensure uniqueness
            if reference not in self.generated_references:
                self.generated_references.add(reference)
                return reference
            
            attempts += 1
        
        # If we can't generate unique reference, add timestamp
        timestamp = str(int(time.time()))[-4:]
        base_reference = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(length-4))
        reference = base_reference + timestamp
        self.generated_references.add(reference)
        return reference
    
    def process_xml(self, input_filepath, output_filepath):
        """Process XML file and update reference numbers"""
        try:
            # Parse XML file with CDATA preservation
            parser = etree.XMLParser(strip_cdata=False)
            with open(input_filepath, 'rb') as f:
                tree = etree.parse(f, parser)
            
            # Find all referencenumber elements
            reference_elements = tree.findall('.//referencenumber')
            
            if not reference_elements:
                return {
                    'success': False,
                    'error': 'No reference number elements found in XML'
                }
            
            # Update each reference number
            jobs_processed = 0
            reference_stats = defaultdict(int)
            
            for ref_element in reference_elements:
                # Generate new unique reference number
                new_reference = self.generate_reference_number()
                
                # Store old reference for stats
                old_reference = ""
                if ref_element.text:
                    old_reference = ref_element.text.strip()
                    reference_stats[old_reference] += 1
                
                # Store the original tail (whitespace after the element)
                original_tail = ref_element.tail
                
                # Clear existing content
                ref_element.clear()
                ref_element.text = None
                
                # Create new CDATA section with new reference
                ref_element.text = etree.CDATA(new_reference)
                
                # Restore the original tail to maintain formatting
                ref_element.tail = original_tail
                
                jobs_processed += 1
                
                # Log progress for large files
                if jobs_processed % 100 == 0:
                    self.logger.info(f"Processed {jobs_processed} reference numbers...")
            
            # Ensure proper formatting by cleaning up whitespace
            etree.cleanup_namespaces(tree)
            etree.indent(tree, space="  ")
            
            # Write updated XML to output file preserving CDATA with proper formatting
            self.logger.info(f"Writing output file to: {output_filepath}")
            with open(output_filepath, 'wb') as f:
                tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
                f.flush()  # Ensure data is written to disk
                os.fsync(f.fileno())  # Force OS to write to disk
            
            # Verify file was written immediately after closing
            self.logger.info(f"File closed, checking existence...")
            if os.path.exists(output_filepath):
                file_size = os.path.getsize(output_filepath)
                self.logger.info(f"Output file written successfully, size: {file_size} bytes")
                
            else:
                self.logger.error("Output file was not created!")
            
            self.logger.info(f"Successfully processed {jobs_processed} jobs")
            
            return {
                'success': True,
                'jobs_processed': jobs_processed,
                'original_references': len(reference_stats),
                'new_references_generated': len(self.generated_references)
            }
            
        except Exception as e:
            self.logger.error(f"Error processing XML: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_reference_numbers(self, filepath):
        """Get all current reference numbers from XML file"""
        try:
            parser = etree.XMLParser(strip_cdata=False)
            with open(filepath, 'rb') as f:
                tree = etree.parse(f, parser)
            
            reference_elements = tree.findall('.//referencenumber')
            references = []
            
            for ref_element in reference_elements:
                if ref_element.text:
                    references.append(ref_element.text.strip())
            
            return references
            
        except Exception as e:
            self.logger.error(f"Error getting reference numbers: {str(e)}")
            return []
    
    def validate_uniqueness(self, filepath):
        """Validate that all reference numbers in file are unique"""
        try:
            references = self.get_reference_numbers(filepath)
            
            if not references:
                return False, "No reference numbers found"
            
            unique_references = set(references)
            
            if len(references) != len(unique_references):
                duplicates = len(references) - len(unique_references)
                return False, f"Found {duplicates} duplicate reference numbers"
            
            return True, f"All {len(references)} reference numbers are unique"
            
        except Exception as e:
            self.logger.error(f"Error validating uniqueness: {str(e)}")
            return False, str(e)
    
    def add_bhatsid_nodes(self, input_filepath, output_filepath):
        """Add <bhatsid> nodes to all jobs in XML file, extracting job IDs from titles"""
        try:
            # Parse XML file with CDATA preservation
            parser = etree.XMLParser(strip_cdata=False)
            with open(input_filepath, 'rb') as f:
                tree = etree.parse(f, parser)
            
            # Find all job elements
            jobs = tree.findall('.//job')
            
            if not jobs:
                return {
                    'success': False,
                    'error': 'No job elements found in XML'
                }
            
            nodes_added = 0
            
            for job in jobs:
                # Find the title element
                title_element = job.find('title')
                if title_element is None:
                    continue
                    
                # Extract job ID from title
                title_text = title_element.text or ""
                job_id = self.extract_job_id_from_title(title_text)
                
                # Find the referencenumber element
                ref_element = job.find('referencenumber')
                if ref_element is None:
                    continue
                
                # Check if bhatsid already exists
                existing_bhatsid = job.find('bhatsid')
                if existing_bhatsid is not None:
                    # Update existing bhatsid
                    existing_bhatsid.clear()
                    existing_bhatsid.text = etree.CDATA(job_id) if job_id else etree.CDATA("")
                else:
                    # Create new bhatsid element
                    bhatsid_element = etree.SubElement(job, 'bhatsid')
                    bhatsid_element.text = etree.CDATA(job_id) if job_id else etree.CDATA("")
                    
                    # Move bhatsid to be right after referencenumber
                    # Find the index of referencenumber
                    ref_index = list(job).index(ref_element)
                    
                    # Remove bhatsid from its current position
                    job.remove(bhatsid_element)
                    
                    # Insert bhatsid right after referencenumber
                    job.insert(ref_index + 1, bhatsid_element)
                
                nodes_added += 1
                
                # Log progress for large files
                if nodes_added % 10 == 0:
                    self.logger.info(f"Added bhatsid nodes to {nodes_added} jobs...")
            
            # Write updated XML to output file preserving CDATA formatting
            self.logger.info(f"Writing updated XML with bhatsid nodes to: {output_filepath}")
            with open(output_filepath, 'wb') as f:
                tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
                f.flush()
                os.fsync(f.fileno())
            
            # Verify file was written
            if os.path.exists(output_filepath):
                file_size = os.path.getsize(output_filepath)
                self.logger.info(f"Output file with bhatsid nodes written successfully, size: {file_size} bytes")
            else:
                self.logger.error("Output file was not created!")
            
            self.logger.info(f"Successfully added bhatsid nodes to {nodes_added} jobs")
            
            return {
                'success': True,
                'nodes_added': nodes_added,
                'jobs_processed': len(jobs)
            }
            
        except Exception as e:
            self.logger.error(f"Error adding bhatsid nodes: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def validate_xml_detailed(self, filepath):
        """Validate XML file structure and return detailed results"""
        try:
            # Parse XML file
            with open(filepath, 'rb') as f:
                tree = etree.parse(f)
            
            # Check for root element
            root = tree.getroot()
            if root.tag != 'source':
                error_msg = "Invalid root element. Expected 'source'"
                self.logger.error(error_msg)
                return {
                    'valid': False,
                    'error': error_msg
                }
            
            # Check for job elements
            jobs = root.findall('.//job')
            if not jobs:
                error_msg = "No job elements found"
                self.logger.error(error_msg)
                return {
                    'valid': False,
                    'error': error_msg
                }
            
            # Validate that jobs have required elements
            required_elements = ['title', 'company', 'date', 'referencenumber']
            for i, job in enumerate(jobs[:10]):  # Check first 10 jobs
                for element in required_elements:
                    if job.find(element) is None:
                        error_msg = f"Job {i+1} missing required element: {element}"
                        self.logger.error(error_msg)
                        return {
                            'valid': False,
                            'error': error_msg
                        }
            
            return {
                'valid': True,
                'jobs_count': len(jobs),
                'error': None
            }
            
        except etree.XMLSyntaxError as e:
            error_msg = f"XML syntax error: {str(e)}"
            self.logger.error(error_msg)
            return {
                'valid': False,
                'error': error_msg
            }
        except Exception as e:
            error_msg = f"Error validating XML: {str(e)}"
            self.logger.error(error_msg)
            return {
                'valid': False,
                'error': error_msg
            }
