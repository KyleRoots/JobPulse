#!/usr/bin/env python3
"""
Comprehensive XML fix to restore CDATA formatting and AI classifications
"""
import xml.etree.ElementTree as ET
from lxml import etree
from app import app, get_bullhorn_service
from job_classification_service import JobClassificationService
import time

def fix_xml_file():
    print("Starting comprehensive XML fix...")
    
    with app.app_context():
        # Load current XML
        tree = ET.parse('myticas-job-feed.xml')
        root = tree.getroot()
        
        # Initialize services
        classification_service = JobClassificationService()
        bullhorn = get_bullhorn_service()
        
        # Create new XML with LXML for CDATA support
        new_root = etree.Element('source')
        publisherurl = etree.SubElement(new_root, 'publisherurl')
        publisherurl.text = etree.CDATA('https://myticas.com')
        
        # Process each job
        jobs = root.findall('.//job')
        print(f"Processing {len(jobs)} jobs...")
        
        for i, job_elem in enumerate(jobs):
            # Create new job element
            new_job = etree.SubElement(new_root, 'job')
            
            # Get job ID for AI classification
            bhatsid_elem = job_elem.find('bhatsid')
            job_id = bhatsid_elem.text.strip() if bhatsid_elem is not None and bhatsid_elem.text else None
            
            # Get AI classification if we have a job ID
            ai_classification = None
            if job_id:
                try:
                    bh_job = bullhorn.get_job_by_id(job_id)
                    if bh_job:
                        ai_classification = classification_service.classify_job(bh_job)
                        print(f"Job {i+1}/{len(jobs)}: {job_id} - {ai_classification.get('jobfunction', 'Unknown')}")
                except Exception as e:
                    print(f"Job {i+1}/{len(jobs)}: {job_id} - Error: {str(e)[:50]}")
            
            # Process each field
            for child in job_elem:
                new_elem = etree.SubElement(new_job, child.tag)
                
                # Handle AI classification fields
                if child.tag == 'jobfunction' and ai_classification:
                    new_elem.text = etree.CDATA(ai_classification.get('jobfunction', ''))
                elif child.tag == 'jobindustries' and ai_classification:
                    new_elem.text = etree.CDATA(ai_classification.get('jobindustries', ''))
                elif child.tag == 'senoritylevel' and ai_classification:
                    new_elem.text = etree.CDATA(ai_classification.get('senoritylevel', ''))
                elif child.text and child.text.strip():
                    # Regular fields with text
                    new_elem.text = etree.CDATA(child.text.strip())
                else:
                    # Empty fields
                    new_elem.text = etree.CDATA('')
            
            # Rate limiting
            if i % 5 == 0 and i > 0:
                time.sleep(0.5)
        
        # Save the fixed XML
        new_tree = etree.ElementTree(new_root)
        with open('myticas-job-feed.xml', 'wb') as f:
            new_tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
        
        # Also update the scheduled XML
        with open('myticas-job-feed-scheduled.xml', 'wb') as f:
            new_tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
        
        print(f"\n✅ Successfully processed {len(jobs)} jobs with CDATA formatting and AI classifications")
        
        # Verify the fix
        with open('myticas-job-feed.xml', 'r') as f:
            content = f.read()
            cdata_count = content.count('<![CDATA[')
            print(f"Total CDATA tags: {cdata_count}")

if __name__ == "__main__":
    fix_xml_file()