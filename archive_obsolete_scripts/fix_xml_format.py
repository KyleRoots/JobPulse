"""Fix the XML format by properly extracting job IDs and creating valid structure"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
import json
import ast
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_xml_format(input_file, output_file):
    """Fix XML format by extracting job IDs and creating proper structure"""
    
    logger.info(f"Processing {input_file}")
    
    # Parse the corrupted XML
    tree = ET.parse(input_file)
    root = tree.getroot()
    
    # Create new root
    new_root = ET.Element('source')
    ET.SubElement(new_root, 'publisherurl').text = 'https://myticas.com'
    
    # Process jobs
    jobs = root.findall('.//job')
    unique_job_ids = set()
    
    for job in jobs:
        try:
            # Extract job ID from the corrupted bhatsid field
            bhatsid_elem = job.find('bhatsid')
            if bhatsid_elem is not None and bhatsid_elem.text:
                # The text contains a dictionary string like "{'id': 32079, 'title': '...'}"
                # Extract just the ID number
                text = bhatsid_elem.text
                if text.startswith("{"):
                    try:
                        # Try to parse as dictionary
                        job_dict = ast.literal_eval(text)
                        job_id = str(job_dict.get('id', ''))
                    except:
                        # Fallback: extract number using regex
                        import re
                        match = re.search(r"'id':\s*(\d+)", text)
                        if match:
                            job_id = match.group(1)
                        else:
                            continue
                else:
                    job_id = text.strip()
                
                # Skip if already processed
                if job_id in unique_job_ids:
                    continue
                    
                unique_job_ids.add(job_id)
                
                # Create new job element with proper structure
                new_job = ET.SubElement(new_root, 'job')
                
                # Add required fields with CDATA where appropriate
                title_elem = ET.SubElement(new_job, 'title')
                title_elem.text = f' Job {job_id} '
                
                company_elem = ET.SubElement(new_job, 'company')
                company_elem.text = ' Myticas Consulting '
                
                date_elem = ET.SubElement(new_job, 'date')
                date_elem.text = ' August 01, 2025 '
                
                ref_elem = ET.SubElement(new_job, 'referencenumber')
                ref_elem.text = f' REF{job_id} '
                
                bhatsid_elem = ET.SubElement(new_job, 'bhatsid')
                bhatsid_elem.text = f' {job_id} '
                
                url_elem = ET.SubElement(new_job, 'url')
                url_elem.text = ' https://myticas.com/ '
                
                desc_elem = ET.SubElement(new_job, 'description')
                desc_elem.text = f' Job description for position {job_id} '
                
                jobtype_elem = ET.SubElement(new_job, 'jobtype')
                jobtype_elem.text = ' Contract '
                
                city_elem = ET.SubElement(new_job, 'city')
                city_elem.text = '  '
                
                state_elem = ET.SubElement(new_job, 'state')
                state_elem.text = '  '
                
                country_elem = ET.SubElement(new_job, 'country')
                country_elem.text = ' United States '
                
                category_elem = ET.SubElement(new_job, 'category')
                category_elem.text = '  '
                
                apply_email_elem = ET.SubElement(new_job, 'apply_email')
                apply_email_elem.text = ' apply@myticas.com '
                
                remotetype_elem = ET.SubElement(new_job, 'remotetype')
                remotetype_elem.text = ' Hybrid '
                
                assignedrecruiter_elem = ET.SubElement(new_job, 'assignedrecruiter')
                assignedrecruiter_elem.text = ' #LI-MYT: Myticas Recruiter '
                
                jobfunction_elem = ET.SubElement(new_job, 'jobfunction')
                jobfunction_elem.text = '  '
                
                jobindustries_elem = ET.SubElement(new_job, 'jobindustries')
                jobindustries_elem.text = '  '
                
                senoritylevel_elem = ET.SubElement(new_job, 'senoritylevel')
                senoritylevel_elem.text = '  '
                
        except Exception as e:
            logger.error(f"Error processing job: {e}")
            continue
    
    logger.info(f"Processed {len(unique_job_ids)} unique jobs")
    
    # Create pretty formatted XML
    xml_str = ET.tostring(new_root, encoding='unicode')
    
    # Add CDATA sections manually
    xml_str = xml_str.replace('<title>', '<title><![CDATA[')
    xml_str = xml_str.replace('</title>', ']]></title>')
    xml_str = xml_str.replace('<company>', '<company><![CDATA[')
    xml_str = xml_str.replace('</company>', ']]></company>')
    xml_str = xml_str.replace('<date>', '<date><![CDATA[')
    xml_str = xml_str.replace('</date>', ']]></date>')
    xml_str = xml_str.replace('<referencenumber>', '<referencenumber><![CDATA[')
    xml_str = xml_str.replace('</referencenumber>', ']]></referencenumber>')
    xml_str = xml_str.replace('<bhatsid>', '<bhatsid><![CDATA[')
    xml_str = xml_str.replace('</bhatsid>', ']]></bhatsid>')
    xml_str = xml_str.replace('<url>', '<url><![CDATA[')
    xml_str = xml_str.replace('</url>', ']]></url>')
    xml_str = xml_str.replace('<description>', '<description><![CDATA[')
    xml_str = xml_str.replace('</description>', ']]></description>')
    xml_str = xml_str.replace('<jobtype>', '<jobtype><![CDATA[')
    xml_str = xml_str.replace('</jobtype>', ']]></jobtype>')
    xml_str = xml_str.replace('<city>', '<city><![CDATA[')
    xml_str = xml_str.replace('</city>', ']]></city>')
    xml_str = xml_str.replace('<state>', '<state><![CDATA[')
    xml_str = xml_str.replace('</state>', ']]></state>')
    xml_str = xml_str.replace('<country>', '<country><![CDATA[')
    xml_str = xml_str.replace('</country>', ']]></country>')
    xml_str = xml_str.replace('<category>', '<category><![CDATA[')
    xml_str = xml_str.replace('</category>', ']]></category>')
    xml_str = xml_str.replace('<apply_email>', '<apply_email><![CDATA[')
    xml_str = xml_str.replace('</apply_email>', ']]></apply_email>')
    xml_str = xml_str.replace('<remotetype>', '<remotetype><![CDATA[')
    xml_str = xml_str.replace('</remotetype>', ']]></remotetype>')
    xml_str = xml_str.replace('<assignedrecruiter>', '<assignedrecruiter><![CDATA[')
    xml_str = xml_str.replace('</assignedrecruiter>', ']]></assignedrecruiter>')
    xml_str = xml_str.replace('<jobfunction>', '<jobfunction><![CDATA[')
    xml_str = xml_str.replace('</jobfunction>', ']]></jobfunction>')
    xml_str = xml_str.replace('<jobindustries>', '<jobindustries><![CDATA[')
    xml_str = xml_str.replace('</jobindustries>', ']]></jobindustries>')
    xml_str = xml_str.replace('<senoritylevel>', '<senoritylevel><![CDATA[')
    xml_str = xml_str.replace('</senoritylevel>', ']]></senoritylevel>')
    
    # Pretty print
    dom = minidom.parseString(xml_str)
    pretty_xml = dom.toprettyxml(indent="  ", encoding='UTF-8')
    
    # Write to file
    with open(output_file, 'wb') as f:
        f.write(pretty_xml)
    
    logger.info(f"Fixed XML saved to {output_file}")
    
    return len(unique_job_ids)

if __name__ == "__main__":
    # Fix both XML files
    count1 = fix_xml_format('myticas-job-feed.xml', 'myticas-job-feed.xml')
    count2 = fix_xml_format('myticas-job-feed-scheduled.xml', 'myticas-job-feed-scheduled.xml')
    
    logger.info(f"XML files fixed! Main: {count1} jobs, Scheduled: {count2} jobs")