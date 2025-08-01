"""
Complete XML Rebuild from Bullhorn API
Rebuilds the job feed XML from scratch using Bullhorn data
"""
import logging
import os
from datetime import datetime
from lxml import etree
from bullhorn_service import BullhornService
from job_classification_service import JobClassificationService
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

class XMLRebuilder:
    def __init__(self):
        self.bullhorn = BullhornService()
        self.classifier = JobClassificationService()
        self.safeguards = XMLSafeguards()
        
    def generate_reference_number(self):
        """Generate a unique 10-character alphanumeric reference number"""
        import random
        import string
        chars = string.ascii_uppercase + string.digits
        # Remove ambiguous characters
        chars = chars.replace('0', '').replace('O', '').replace('I', '').replace('1', '')
        return ''.join(random.choice(chars) for _ in range(10))
    
    def format_recruiter_name(self, recruiter_name):
        """Format recruiter name with LinkedIn tag"""
        if not recruiter_name:
            return "#LI-MYT"
            
        # Check for exact match first
        if recruiter_name in RECRUITER_LINKEDIN_TAGS:
            tag = RECRUITER_LINKEDIN_TAGS[recruiter_name]
            return f"{tag}: {recruiter_name}"
            
        # Check for partial matches
        for name, tag in RECRUITER_LINKEDIN_TAGS.items():
            if name.lower() in recruiter_name.lower() or recruiter_name.lower() in name.lower():
                return f"{tag}: {recruiter_name}"
                
        # Default fallback
        return "#LI-MYT"
    
    def extract_job_ids_from_xml(self, xml_path):
        """Extract all job IDs from the current XML file"""
        job_ids = []
        try:
            tree = etree.parse(xml_path)
            root = tree.getroot()
            
            for job in root.findall('.//job'):
                bhatsid_elem = job.find('bhatsid')
                if bhatsid_elem is not None and bhatsid_elem.text:
                    # Extract ID from CDATA
                    job_id = bhatsid_elem.text.strip()
                    if job_id.startswith('<![CDATA['):
                        job_id = job_id[9:-3].strip()
                    try:
                        job_ids.append(int(job_id))
                    except ValueError:
                        logger.warning(f"Invalid job ID format: {job_id}")
                        
            logger.info(f"Extracted {len(job_ids)} job IDs from XML")
            return job_ids
            
        except Exception as e:
            logger.error(f"Error extracting job IDs from XML: {str(e)}")
            return []
    
    def fetch_job_details(self, job_id):
        """Fetch complete job details from Bullhorn"""
        try:
            if not self.bullhorn.authenticate():
                logger.error("Failed to authenticate with Bullhorn")
                return None
                
            # Enhanced fields for complete job data
            fields = [
                "id", "title", "status", "isOpen", "isPublic", "isDeleted",
                "dateAdded", "dateLastModified", "publicDescription",
                "clientCorporation(id,name)", "address(city,state,countryName)",
                "employmentType", "owner(firstName,lastName)",
                "assignedUsers(firstName,lastName)", "responseUser(firstName,lastName)",
                "categories(id,name)", "onSite", "benefits", "bonusPackage",
                "degreeList", "skillList", "certificationList"
            ]
            
            url = f"{self.bullhorn.base_url}entity/JobOrder/{job_id}"
            params = {
                'fields': ','.join(fields),
                'BhRestToken': self.bullhorn.rest_token
            }
            
            response = self.bullhorn.session.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('data')
            else:
                logger.error(f"Failed to fetch job {job_id}: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching job {job_id}: {str(e)}")
            return None
    
    def convert_job_to_xml_element(self, job_data, reference_number=None):
        """Convert Bullhorn job data to XML job element"""
        job_elem = etree.Element("job")
        
        # Generate reference number if not provided
        if not reference_number:
            reference_number = self.generate_reference_number()
        
        # Extract basic fields
        job_id = str(job_data.get('id', ''))
        title = job_data.get('title', '')
        company = job_data.get('clientCorporation', {}).get('name', 'Myticas Consulting')
        
        # Format date
        date_added = job_data.get('dateAdded', '')
        if date_added:
            try:
                dt = datetime.fromtimestamp(date_added / 1000)
                formatted_date = dt.strftime('%B %d, %Y')
            except:
                formatted_date = datetime.now().strftime('%B %d, %Y')
        else:
            formatted_date = datetime.now().strftime('%B %d, %Y')
        
        # Extract description
        description = job_data.get('publicDescription', '')
        if not description:
            description = f"<p>No description available for {title}</p>"
        
        # Extract location
        address = job_data.get('address', {})
        city = address.get('city', '')
        state = address.get('state', '')
        country = address.get('countryName', 'United States')
        
        # Determine job type
        employment_type = job_data.get('employmentType', '')
        if 'Contract' in employment_type:
            jobtype = 'Contract'
        elif 'Direct' in employment_type or 'Permanent' in employment_type:
            jobtype = 'Direct Hire'
        else:
            jobtype = 'Contract'
        
        # Determine remote type
        on_site = job_data.get('onSite', '')
        if on_site == 'Remote':
            remotetype = 'Remote'
        elif on_site == 'Hybrid':
            remotetype = 'Hybrid'
        else:
            remotetype = 'On-site'
        
        # Extract recruiter
        recruiter_name = ''
        if job_data.get('owner'):
            first = job_data['owner'].get('firstName', '')
            last = job_data['owner'].get('lastName', '')
            recruiter_name = f"{first} {last}".strip()
        
        formatted_recruiter = self.format_recruiter_name(recruiter_name)
        
        # Get AI classifications
        classifications = self.classifier.classify_job(title, description)
        
        # Build XML structure with CDATA
        etree.SubElement(job_elem, "title").text = etree.CDATA(f" {title} ({job_id}) ")
        etree.SubElement(job_elem, "company").text = etree.CDATA(f" {company} ")
        etree.SubElement(job_elem, "date").text = etree.CDATA(f" {formatted_date} ")
        etree.SubElement(job_elem, "referencenumber").text = etree.CDATA(f" {reference_number} ")
        etree.SubElement(job_elem, "bhatsid").text = etree.CDATA(f" {job_id} ")
        etree.SubElement(job_elem, "url").text = etree.CDATA(" https://myticas.com/ ")
        etree.SubElement(job_elem, "description").text = etree.CDATA(f" {description} ")
        etree.SubElement(job_elem, "jobtype").text = etree.CDATA(f" {jobtype} ")
        etree.SubElement(job_elem, "city").text = etree.CDATA(f" {city} ")
        etree.SubElement(job_elem, "state").text = etree.CDATA(f" {state} ")
        etree.SubElement(job_elem, "country").text = etree.CDATA(f" {country} ")
        etree.SubElement(job_elem, "category").text = etree.CDATA(" ")
        etree.SubElement(job_elem, "apply_email").text = etree.CDATA(" apply@myticas.com ")
        etree.SubElement(job_elem, "remotetype").text = etree.CDATA(f" {remotetype} ")
        etree.SubElement(job_elem, "assignedrecruiter").text = etree.CDATA(f" {formatted_recruiter} ")
        etree.SubElement(job_elem, "jobfunction").text = etree.CDATA(f" {classifications.get('function', 'Other')} ")
        etree.SubElement(job_elem, "jobindustries").text = etree.CDATA(f" {classifications.get('industry', 'Other')} ")
        etree.SubElement(job_elem, "senoritylevel").text = etree.CDATA(f" {classifications.get('seniority', 'Not Applicable')} ")
        
        return job_elem
    
    def rebuild_xml_from_current_file(self, input_xml_path, output_xml_path):
        """Rebuild XML by fetching fresh data from Bullhorn for each job in current XML"""
        logger.info(f"Starting XML rebuild from {input_xml_path}")
        
        # Extract job IDs from current XML
        job_ids = self.extract_job_ids_from_xml(input_xml_path)
        
        if not job_ids:
            logger.error("No job IDs found in current XML")
            return False
        
        logger.info(f"Found {len(job_ids)} jobs to rebuild")
        
        # Create new XML structure
        root = etree.Element("source")
        etree.SubElement(root, "publisherurl").text = "https://myticas.com"
        
        # Process each job
        successful_jobs = 0
        failed_jobs = []
        
        for job_id in job_ids:
            logger.info(f"Fetching job {job_id} from Bullhorn...")
            job_data = self.fetch_job_details(job_id)
            
            if job_data:
                # Check if job is deleted or closed
                if job_data.get('isDeleted') or not job_data.get('isOpen'):
                    logger.warning(f"Job {job_id} is deleted or closed, skipping")
                    continue
                    
                job_elem = self.convert_job_to_xml_element(job_data)
                root.append(job_elem)
                successful_jobs += 1
            else:
                logger.error(f"Failed to fetch job {job_id}")
                failed_jobs.append(job_id)
        
        # Create the tree and save
        tree = etree.ElementTree(root)
        
        # Pretty print with proper formatting
        etree.indent(tree, space="  ")
        
        # Save the file
        with open(output_xml_path, 'wb') as f:
            tree.write(f, pretty_print=True, xml_declaration=True, encoding='UTF-8')
        
        # Validate the new XML
        is_valid, validation_msg = self.safeguards.validate_xml_file(output_xml_path)
        
        if is_valid:
            logger.info(f"✓ XML rebuild complete: {successful_jobs} jobs processed successfully")
            if failed_jobs:
                logger.warning(f"Failed to fetch {len(failed_jobs)} jobs: {failed_jobs}")
            return True
        else:
            logger.error(f"XML validation failed: {validation_msg}")
            return False


def main():
    """Main function to rebuild XML"""
    rebuilder = XMLRebuilder()
    
    # Rebuild from current XML
    success = rebuilder.rebuild_xml_from_current_file(
        'myticas-job-feed.xml',
        'myticas-job-feed-rebuilt.xml'
    )
    
    if success:
        logger.info("✓ XML rebuild completed successfully!")
        logger.info("New file saved as: myticas-job-feed-rebuilt.xml")
    else:
        logger.error("✗ XML rebuild failed")


if __name__ == "__main__":
    main()