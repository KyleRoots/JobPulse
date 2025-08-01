#!/usr/bin/env python3
"""
Restore CDATA formatting to XML job feed files
"""
import xml.etree.ElementTree as ET
import re
import sys

def ensure_cdata_format(text):
    """Ensure text is wrapped in CDATA tags"""
    if text is None:
        return None
    
    # Remove existing CDATA tags if present
    text = text.replace('<![CDATA[', '').replace(']]>', '')
    
    # Return wrapped in CDATA
    return f'<![CDATA[{text}]]>'

def fix_xml_cdata(input_file, output_file):
    """Fix CDATA formatting in XML file"""
    print(f"Processing {input_file}...")
    
    # Parse the XML
    tree = ET.parse(input_file)
    root = tree.getroot()
    
    # Fields that need CDATA
    cdata_fields = [
        'title', 'company', 'date', 'referencenumber', 'bhatsid', 'url',
        'description', 'jobtype', 'city', 'state', 'country', 'category',
        'apply_email', 'remotetype', 'assignedrecruiter', 'jobfunction',
        'jobindustries', 'senoritylevel'
    ]
    
    # Process each job
    jobs_processed = 0
    for job in root.findall('.//job'):
        for field in cdata_fields:
            elem = job.find(field)
            if elem is not None and elem.text:
                # Store the original text
                original_text = elem.text.strip()
                # Clear the element
                elem.text = None
                elem.tail = None
                # Create a new element with CDATA
                elem.clear()
                elem.text = ensure_cdata_format(original_text)
        jobs_processed += 1
    
    # Convert to string and manually fix CDATA formatting
    xml_str = ET.tostring(root, encoding='unicode')
    
    # Replace escaped CDATA markers with actual CDATA sections
    xml_str = xml_str.replace('&lt;![CDATA[', '<![CDATA[')
    xml_str = xml_str.replace(']]&gt;', ']]>')
    
    # Add proper formatting
    xml_str = re.sub(r'><', '>\n<', xml_str)
    
    # Write the result
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(xml_str)
    
    # Count CDATA sections in output
    with open(output_file, 'r') as f:
        content = f.read()
        cdata_count = content.count('<![CDATA[')
    
    print(f"✓ Processed {jobs_processed} jobs")
    print(f"✓ Added {cdata_count} CDATA sections")
    print(f"✓ Output saved to {output_file}")
    
    return jobs_processed, cdata_count

if __name__ == "__main__":
    # Process both XML files
    for xml_file in ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']:
        output_file = xml_file.replace('.xml', '-cdata.xml')
        jobs, cdata = fix_xml_cdata(xml_file, output_file)
        
        # Replace original with fixed version
        import shutil
        shutil.move(output_file, xml_file)
        print(f"✓ Replaced {xml_file} with CDATA-formatted version\n")