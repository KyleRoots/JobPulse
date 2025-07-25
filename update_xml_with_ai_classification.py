#!/usr/bin/env python3
"""
Script to update existing XML file with AI-powered job classification
Adds jobfunction, jobindustries, and senoritylevel nodes to all existing jobs
"""

import os
import sys
import logging
from lxml import etree
from job_classification_service import JobClassificationService
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def clean_description_for_ai(description):
    """Clean description for AI analysis by removing HTML tags"""
    if not description:
        return ""
    # Remove HTML tags
    clean_desc = re.sub('<.*?>', '', description)
    # Remove extra whitespace
    clean_desc = ' '.join(clean_desc.split())
    return clean_desc[:1500]  # Limit length for AI processing

def extract_clean_title(title):
    """Extract clean title without job ID for AI analysis"""
    if not title:
        return ""
    
    # Remove job ID in parentheses at the end
    clean_title = re.sub(r'\s*\(\d+\)\s*$', '', title)
    return clean_title.strip()

def add_ai_classification_to_xml(xml_file_path, output_file_path):
    """Add AI classification fields to all jobs in XML file"""
    
    try:
        # Initialize the AI classifier
        classifier = JobClassificationService()
        logger.info("AI classification service initialized")
        
        # Parse XML file
        parser = etree.XMLParser(strip_cdata=False, recover=True)
        with open(xml_file_path, 'rb') as f:
            tree = etree.parse(f, parser)
        
        root = tree.getroot()
        jobs = root.findall('.//job')
        
        logger.info(f"Found {len(jobs)} jobs to classify")
        
        processed_count = 0
        
        for job in jobs:
            try:
                # Extract title and description
                title_elem = job.find('title')
                desc_elem = job.find('description')
                
                title = title_elem.text if title_elem is not None and title_elem.text else ""
                description = desc_elem.text if desc_elem is not None and desc_elem.text else ""
                
                # Clean title and description for AI analysis
                clean_title = extract_clean_title(title)
                clean_description = clean_description_for_ai(description)
                
                # Skip if no meaningful data
                if not clean_title:
                    logger.warning(f"Skipping job with empty title")
                    continue
                
                # Get AI classification
                classification = classifier.classify_job(clean_title, clean_description)
                
                # Find assignedrecruiter element to insert new fields after it
                assigned_recruiter_elem = job.find('assignedrecruiter')
                if assigned_recruiter_elem is None:
                    logger.warning(f"Job '{clean_title}' missing assignedrecruiter element")
                    continue
                
                # Get position to insert new elements
                assigned_recruiter_index = list(job).index(assigned_recruiter_elem)
                
                # Create and insert jobfunction element
                jobfunction_elem = etree.Element('jobfunction')
                jobfunction_elem.text = etree.CDATA(f" {classification.get('job_function', '')} ")
                jobfunction_elem.tail = "\n    "
                job.insert(assigned_recruiter_index + 1, jobfunction_elem)
                
                # Create and insert jobindustries element
                jobindustries_elem = etree.Element('jobindustries')
                jobindustries_elem.text = etree.CDATA(f" {classification.get('job_industry', '')} ")
                jobindustries_elem.tail = "\n    "
                job.insert(assigned_recruiter_index + 2, jobindustries_elem)
                
                # Create and insert senoritylevel element
                senoritylevel_elem = etree.Element('senoritylevel')
                senoritylevel_elem.text = etree.CDATA(f" {classification.get('seniority_level', '')} ")
                senoritylevel_elem.tail = "\n  "  # Close job element indentation
                job.insert(assigned_recruiter_index + 3, senoritylevel_elem)
                
                processed_count += 1
                
                logger.info(f"Classified job '{clean_title}': Function={classification.get('job_function', '')}, "
                           f"Industry={classification.get('job_industry', '')}, "
                           f"Seniority={classification.get('seniority_level', '')}")
                
                # Log progress for large files
                if processed_count % 5 == 0:
                    logger.info(f"Processed {processed_count}/{len(jobs)} jobs...")
                    
            except Exception as e:
                logger.error(f"Error processing job: {e}")
                continue
        
        # Write updated XML file
        logger.info(f"Writing updated XML with AI classifications to: {output_file_path}")
        with open(output_file_path, 'wb') as f:
            tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
            f.flush()
            os.fsync(f.fileno())
        
        # Verify file was written
        if os.path.exists(output_file_path):
            file_size = os.path.getsize(output_file_path)
            logger.info(f"Output file written successfully, size: {file_size} bytes")
        else:
            logger.error("Output file was not created!")
            return False
        
        logger.info(f"Successfully added AI classifications to {processed_count} jobs")
        return True
        
    except Exception as e:
        logger.error(f"Error updating XML with AI classifications: {e}")
        return False

def main():
    """Main execution function"""
    xml_file = "myticas-job-feed.xml"
    output_file = "myticas-job-feed-with-ai.xml"
    
    if not os.path.exists(xml_file):
        logger.error(f"XML file not found: {xml_file}")
        sys.exit(1)
    
    logger.info(f"Starting AI classification update for {xml_file}")
    
    success = add_ai_classification_to_xml(xml_file, output_file)
    
    if success:
        logger.info("AI classification update completed successfully!")
        # Replace original file with updated version
        os.replace(output_file, xml_file)
        logger.info(f"Updated file saved as {xml_file}")
    else:
        logger.error("AI classification update failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()