import logging
import random
import string
import time
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
            with open(output_filepath, 'wb') as f:
                tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
            
            self.logger.info(f"Successfully processed {jobs_processed} jobs")
            self.logger.info(f"Found {len(reference_stats)} unique original references")
            
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
